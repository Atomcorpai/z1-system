"""
z1_audit_coordinator.py

Silo-level audit coordinator for the Z1 governance stack.

Role:
    Reads silo content after each write.
    Calls the auditor model for binary conflict/staleness classification.
    Writes audit flags to silo flag files.
    Detects tarpits (looping patterns) via deterministic counter. No model.
    Surfaces flags to the bridge. Never acts on them autonomously.
    Human release required for tarpit unlock.

Usage:
    from z1_audit_coordinator import AuditCoordinator
    coordinator = AuditCoordinator(base=SILO_BASE, model="llama3.2:3b", ollama_url=OLLAMA_API_URL)
    result = coordinator.audit_silo("core_runtime", incoming_content="some prompt")
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_AUDITOR_MODEL = "llama3.2:3b"
DEFAULT_OLLAMA_URL = "http://localhost:11434/api/generate"
TARPIT_THRESHOLD = 5        # Repeated near-identical inputs within window triggers tarpit
TARPIT_WINDOW = 10          # Number of recent inputs to check for loops
FLAG_FILE = "audit_flags.jsonl"
TARPIT_FILE = "tarpit_state.json"
SNIPPET_CHARS = 800         # Max chars of silo content passed to auditor


# ---------------------------------------------------------------------------
# Audit prompt
# ---------------------------------------------------------------------------

AUDIT_PROMPT = """You are a runtime auditor. Classify the incoming content against existing silo context.

LABELS:
- NO_CONFLICT: content is consistent with existing silo context, no issues detected.
- CONFLICT: content contradicts or is inconsistent with existing silo context.
- STALE: content references outdated facts that have been superseded.
- CURRENT: content is up to date and consistent.
- BLOCK: content should not be written to this silo (wrong domain, harmful, or system violation).
- ALLOW: content is appropriate for this silo.

Respond ONLY with valid JSON. No prose before or after.

Required schema:
{{
  "ok": true,
  "task": "silo_audit",
  "verdict": "NO_CONFLICT" | "CONFLICT" | "STALE" | "CURRENT" | "BLOCK" | "ALLOW",
  "confidence": 0.0,
  "rationale": "<=240 chars",
  "flag": true | false
}}

Silo: {silo}
Existing context (recent):
{context}

Incoming content:
{incoming}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return f"audit_{uuid.uuid4().hex[:10]}_{int(time.time())}"


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _similarity(a: str, b: str) -> float:
    """Simple character-level overlap ratio for tarpit detection."""
    a, b = _normalize(a), _normalize(b)
    if not a or not b:
        return 0.0
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    matches = sum(1 for c in shorter if c in longer)
    return matches / max(len(longer), 1)


# ---------------------------------------------------------------------------
# AuditCoordinator
# ---------------------------------------------------------------------------

class AuditCoordinator:

    def __init__(
        self,
        base: Path,
        model: str = DEFAULT_AUDITOR_MODEL,
        ollama_url: str = DEFAULT_OLLAMA_URL,
    ):
        self.base = Path(base)
        self.model = model
        self.ollama_url = ollama_url
        self._recent_inputs: Dict[str, List[str]] = {}  # silo -> recent input list

    # ------------------------------------------------------------------
    # Silo file paths
    # ------------------------------------------------------------------

    def _silo_dir(self, silo: str) -> Path:
        d = self.base / silo
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _flag_path(self, silo: str) -> Path:
        return self._silo_dir(silo) / FLAG_FILE

    def _tarpit_path(self, silo: str) -> Path:
        return self._silo_dir(silo) / TARPIT_FILE

    def _dump_path(self, silo: str) -> Path:
        return self._silo_dir(silo) / "dump.txt"

    # ------------------------------------------------------------------
    # Context loading
    # ------------------------------------------------------------------

    def _load_context(self, silo: str) -> str:
        dump = self._dump_path(silo)
        if not dump.exists():
            return ""
        content = dump.read_text(encoding="utf-8", errors="replace")
        return content[-SNIPPET_CHARS:] if len(content) > SNIPPET_CHARS else content

    # ------------------------------------------------------------------
    # Tarpit detection (deterministic — no model)
    # ------------------------------------------------------------------

    def _check_tarpit(self, silo: str, incoming: str) -> bool:
        """
        Returns True if tarpit threshold is exceeded.
        Tracks recent inputs per silo and checks for near-duplicate loops.
        """
        recent = self._recent_inputs.setdefault(silo, [])
        recent.append(incoming)
        if len(recent) > TARPIT_WINDOW:
            recent.pop(0)

        if len(recent) < TARPIT_THRESHOLD:
            return False

        # Count near-duplicates in window
        base = _normalize(incoming)
        similar_count = sum(
            1 for prev in recent[:-1]
            if _similarity(base, _normalize(prev)) > 0.85
        )
        return similar_count >= TARPIT_THRESHOLD - 1

    def _set_tarpit(self, silo: str, active: bool, reason: str = "") -> None:
        state = {
            "silo": silo,
            "active": active,
            "reason": reason,
            "timestamp": _iso_now(),
        }
        self._tarpit_path(silo).write_text(
            json.dumps(state, indent=2), encoding="utf-8"
        )

    def tarpit_status(self) -> Dict[str, bool]:
        """Returns tarpit active state for all silos that have a tarpit file."""
        result = {}
        if not self.base.exists():
            return result
        for silo_dir in self.base.iterdir():
            if silo_dir.is_dir():
                tp = silo_dir / TARPIT_FILE
                if tp.exists():
                    try:
                        state = json.loads(tp.read_text(encoding="utf-8"))
                        result[silo_dir.name] = state.get("active", False)
                    except Exception:
                        result[silo_dir.name] = False
        return result

    def release_tarpit(self, silo: str, confirmed_by: str = "human") -> None:
        """Human-only release. Explicit call required."""
        self._set_tarpit(silo, active=False, reason=f"Released by {confirmed_by} at {_iso_now()}")
        # Clear recent input window for this silo
        self._recent_inputs[silo] = []

    # ------------------------------------------------------------------
    # Flag management
    # ------------------------------------------------------------------

    def _write_flag(self, silo: str, flag: Dict[str, Any]) -> None:
        with self._flag_path(silo).open("a", encoding="utf-8") as f:
            f.write(json.dumps(flag) + "\n")

    def read_flags(self, silo: str) -> List[Dict[str, Any]]:
        path = self._flag_path(silo)
        if not path.exists():
            return []
        flags = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    flags.append(json.loads(line))
                except Exception:
                    pass
        return flags

    def read_all_flags(self) -> List[Dict[str, Any]]:
        all_flags = []
        if not self.base.exists():
            return all_flags
        for silo_dir in self.base.iterdir():
            if silo_dir.is_dir():
                all_flags.extend(self.read_flags(silo_dir.name))
        return all_flags

    # ------------------------------------------------------------------
    # Auditor model call
    # ------------------------------------------------------------------

    def _call_auditor(
        self,
        silo: str,
        context: str,
        incoming: str,
    ) -> Dict[str, Any]:
        prompt = AUDIT_PROMPT.format(
            silo=silo,
            context=context or "(empty)",
            incoming=incoming[:400],
        )
        try:
            response = requests.post(
                self.ollama_url,
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.0, "num_ctx": 2048},
                },
                timeout=30,
            )
            raw = response.json().get("response", "").strip()
        except Exception as e:
            return {
                "ok": False,
                "verdict": "ALLOW",
                "confidence": 0.0,
                "rationale": f"Auditor call failed: {str(e)[:100]}",
                "flag": False,
                "error": str(e),
            }

        try:
            clean = raw.replace("```json", "").replace("```", "").strip()
            parsed = json.loads(clean)
            return parsed
        except Exception:
            return {
                "ok": False,
                "verdict": "ALLOW",
                "confidence": 0.0,
                "rationale": "Auditor returned invalid JSON. Defaulting to ALLOW.",
                "flag": False,
                "raw": raw[:200],
            }

    # ------------------------------------------------------------------
    # Main audit entry point
    # ------------------------------------------------------------------

    def audit_silo(
        self,
        silo: str,
        incoming_content: str,
    ) -> Dict[str, Any]:
        """
        Audit a silo after a write event.

        1. Check tarpit (deterministic).
        2. Load silo context.
        3. Call auditor model for binary classification.
        4. Write flag if auditor raises one.
        5. Return audit result to bridge.

        Never acts on flags. Surfaces them only.
        """
        audit_id = _new_id()

        # 1. Tarpit check
        tarpit_triggered = self._check_tarpit(silo, incoming_content)
        if tarpit_triggered:
            self._set_tarpit(silo, active=True, reason="Loop pattern detected by deterministic counter.")
            flag = {
                "audit_id": audit_id,
                "silo": silo,
                "timestamp": _iso_now(),
                "verdict": "TARPIT",
                "confidence": 1.0,
                "rationale": "Repeated near-identical inputs detected. Human review required.",
                "flag": True,
                "source": "deterministic",
            }
            self._write_flag(silo, flag)
            return {
                "status": "TARPIT",
                "audit_id": audit_id,
                "silo": silo,
                "flag_count": 1,
                "flags": [flag],
                "tarpit_active": True,
            }

        # 2. Load context
        context = self._load_context(silo)

        # 3. Call auditor
        result = self._call_auditor(silo, context, incoming_content)

        verdict = result.get("verdict", "ALLOW")
        confidence = float(result.get("confidence", 0.0))
        rationale = str(result.get("rationale", ""))[:240]
        should_flag = result.get("flag", False) or verdict in {"CONFLICT", "STALE", "BLOCK"}

        # 4. Write flag if raised
        flags_written = []
        if should_flag:
            flag = {
                "audit_id": audit_id,
                "silo": silo,
                "timestamp": _iso_now(),
                "verdict": verdict,
                "confidence": confidence,
                "rationale": rationale,
                "flag": True,
                "source": "auditor",
                "incoming_snippet": incoming_content[:120],
            }
            self._write_flag(silo, flag)
            flags_written.append(flag)

        # 5. Return to bridge
        return {
            "status": verdict,
            "audit_id": audit_id,
            "silo": silo,
            "confidence": confidence,
            "rationale": rationale,
            "flag_count": len(flags_written),
            "flags": flags_written,
            "tarpit_active": False,
        }
