"""
z1_action_gate

Two-stage action gate predictor.

Stage 1: Deterministic rules (fast, high precision, no model).
Stage 2: 3B auditor model for ambiguous boundary cases only.

Rule IDs:
    R0 = schema/parse failure (default BLOCK)
    R1 = destructive or irreversible action
    R2 = external side effects
    R3 = reservoir access violation
    R4 = ambiguous or uncertain (default BLOCK, low confidence)

Usage:
    from z1_reservoir_gate import predict_reservoir_gate
    result = predict_reservoir_gate("delete all files in archive", ctx={})
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_AUDITOR_URL = "http://localhost:11434/api/generate"
DEFAULT_AUDITOR_MODEL = "llama3.2:3b"
CONFIDENCE_THRESHOLD = 0.5  # Below this, ALLOW flips to STOP escalation


# ---------------------------------------------------------------------------
# Rule term lists
# ---------------------------------------------------------------------------

DESTRUCTIVE_TERMS = [
    "delete", "remove", "wipe", "erase", "destroy", "purge", "trash",
    "overwrite", "replace", "reset", "format", "drop table", "truncate",
    "terminate", "kill", "shutdown", "uninstall",
]

EXTERNAL_TERMS = [
    "send", "submit", "file", "publish", "post", "email", "forward",
    "upload", "commit", "push", "merge", "deploy", "release",
    "payment", "transfer", "notify", "broadcast",
]

RESERVOIR_TERMS = [
    "reservoir", "cold storage", "archive", "old files", "chat history",
    "dump.txt", ".gsd", "scaffolding files",
]

AMBIGUOUS_TERMS = [
    "clean up", "fix it", "fix this", "handle it", "handle the", "do it",
    "make it work", "update everything", "delete the bad ones",
    "remove the wrong ones", "archive everything", "take care of it",
    "deal with it", "sort it out",
]

TARGET_MARKERS = [
    "file", "folder", "path", "named", "called", "this file", "these files",
    ".py", ".json", ".md", ".txt", "only", "specific", "the following",
]

OPEN_RESERVOIR_PREFIX = "open_reservoir:"


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------

@dataclass
class GateDecision:
    verdict: str                          # ALLOW | BLOCK | STOP_FOR_CLARITY
    rule_id: str                          # R0-R4
    confidence: float                     # 0.0 - 1.0
    risk_tags: List[str]
    rationale: str
    evidence: List[str]
    needs_human: bool
    receipt_id: str
    stage: str                            # deterministic | auditor
    raw_model_output: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_receipt() -> str:
    return f"gate_{uuid.uuid4().hex[:12]}_{int(time.time())}"


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _matched(text: str, terms: list) -> List[str]:
    return [t for t in terms if t in text]


def _has_target(text: str) -> bool:
    return any(m in text for m in TARGET_MARKERS)


def _extract_evidence(text: str, terms: List[str], max_chars: int = 80) -> List[str]:
    evidence = []
    for term in terms[:3]:
        idx = text.find(term)
        if idx != -1:
            snippet = text[max(0, idx - 20):idx + len(term) + 20].strip()
            evidence.append(f"...{snippet}...")
    return evidence


# ---------------------------------------------------------------------------
# Stage 1: Deterministic predictor
# ---------------------------------------------------------------------------

def _deterministic_gate(
    text: str,
    lower: str,
    ctx: Dict[str, Any],
) -> Optional[GateDecision]:
    """
    Returns a GateDecision if a deterministic rule fires, otherwise None.
    None means: send to auditor model.
    """
    receipt = _new_receipt()

    # Reservoir access without authorization
    if any(t in lower for t in RESERVOIR_TERMS):
        if not str(ctx.get("reservoir_scope", "") or "").strip():
            raw = ctx.get("raw_text", text)
            if OPEN_RESERVOIR_PREFIX not in raw.lower():
                return GateDecision(
                    verdict="BLOCK",
                    rule_id="R3",
                    confidence=0.99,
                    risk_tags=["reservoir_access_violation"],
                    rationale="Reservoir access attempted without OPEN_RESERVOIR:[scope] prefix.",
                    evidence=_extract_evidence(lower, RESERVOIR_TERMS),
                    needs_human=True,
                    receipt_id=receipt,
                    stage="deterministic",
                )

    # Destructive terms
    destructive = _matched(lower, DESTRUCTIVE_TERMS)
    if destructive:
        return GateDecision(
            verdict="BLOCK",
            rule_id="R1",
            confidence=0.97,
            risk_tags=["destructive"],
            rationale=f"Destructive action detected: {', '.join(destructive[:3])}. Explicit confirmation required.",
            evidence=_extract_evidence(lower, destructive),
            needs_human=True,
            receipt_id=receipt,
            stage="deterministic",
        )

    # External side effects
    external = _matched(lower, EXTERNAL_TERMS)
    if external:
        return GateDecision(
            verdict="BLOCK",
            rule_id="R2",
            confidence=0.95,
            risk_tags=["external_side_effect"],
            rationale=f"External action detected: {', '.join(external[:3])}. Explicit confirmation required.",
            evidence=_extract_evidence(lower, external),
            needs_human=True,
            receipt_id=receipt,
            stage="deterministic",
        )

    # Ambiguous without target
    ambiguous = _matched(lower, AMBIGUOUS_TERMS)
    if ambiguous and not _has_target(lower):
        return GateDecision(
            verdict="STOP_FOR_CLARITY",
            rule_id="R4",
            confidence=0.90,
            risk_tags=["ambiguous"],
            rationale=f"Ambiguous instruction without clear target: {', '.join(ambiguous[:3])}.",
            evidence=_extract_evidence(lower, ambiguous),
            needs_human=True,
            receipt_id=receipt,
            stage="deterministic",
        )

    return None


# ---------------------------------------------------------------------------
# Stage 2: Auditor model
# ---------------------------------------------------------------------------

reservoir_gate_PROMPT = """You are a runtime auditor. Classify whether this action requires confirmation before execution.

reservoir_gate LABELS:
- ALLOW: safe, non-destructive, no external side effects, unambiguous.
- BLOCK: destructive/irreversible/external OR ambiguous OR missing required confirmation.
- If uncertain: output BLOCK with confidence <= 0.49 and rule_id=R4. Never hedge.

RULE IDs:
- R0: schema/parse failure
- R1: destructive or irreversible
- R2: external side effects
- R3: reservoir access violation
- R4: ambiguous or uncertain

Respond ONLY with valid JSON. No prose before or after.

Required schema:
{
  "ok": true,
  "task": "reservoir_gate",
  "verdict": "ALLOW" | "BLOCK",
  "confidence": 0.0,
  "rule_id": "R0" | "R1" | "R2" | "R3" | "R4",
  "rationale": "<=240 chars",
  "evidence": ["<=3 short quotes"]
}

Action to classify: {action}
"""


def _auditor_gate(
    text: str,
    ollama_url: str,
    model: str,
    timeout: int = 30,
) -> GateDecision:
    receipt = _new_receipt()
    prompt = reservoir_gate_PROMPT.format(action=text)

    try:
        response = requests.post(
            ollama_url,
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.0, "num_ctx": 2048},
            },
            timeout=timeout,
        )
        raw = response.json().get("response", "").strip()
    except Exception as e:
        return GateDecision(
            verdict="BLOCK",
            rule_id="R0",
            confidence=0.0,
            risk_tags=["model_error"],
            rationale=f"Model call failed: {str(e)[:100]}",
            evidence=[],
            needs_human=True,
            receipt_id=receipt,
            stage="auditor",
            raw_model_output=str(e),
        )

    # Parse JSON
    try:
        clean = raw.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(clean)
    except Exception:
        return GateDecision(
            verdict="BLOCK",
            rule_id="R0",
            confidence=0.0,
            risk_tags=["invalid_json"],
            rationale="Model output was not valid JSON. Defaulting to BLOCK.",
            evidence=[raw[:80]] if raw else [],
            needs_human=True,
            receipt_id=receipt,
            stage="auditor",
            raw_model_output=raw,
        )

    verdict = parsed.get("verdict", "BLOCK")
    if verdict not in {"ALLOW", "BLOCK"}:
        verdict = "BLOCK"

    confidence = float(parsed.get("confidence", 0.0))
    rule_id = parsed.get("rule_id", "R4")
    rationale = str(parsed.get("rationale", ""))[:240]
    evidence = parsed.get("evidence", [])[:3]

    # If ALLOW but confidence below threshold, escalate
    needs_human = verdict == "BLOCK" or confidence < CONFIDENCE_THRESHOLD

    return GateDecision(
        verdict=verdict,
        rule_id=rule_id,
        confidence=confidence,
        risk_tags=["auditor_allow"] if verdict == "ALLOW" else ["auditor_block"],
        rationale=rationale,
        evidence=evidence,
        needs_human=needs_human,
        receipt_id=receipt,
        stage="auditor",
        raw_model_output=raw,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def predict_reservoir_gate(
    text: str,
    ctx: Optional[Dict[str, Any]] = None,
    *,
    ollama_url: str = DEFAULT_AUDITOR_URL,
    model: str = DEFAULT_AUDITOR_MODEL,
    use_auditor: bool = True,
    timeout: int = 30,
) -> GateDecision:
    """
    Two-stage action gate.

    Stage 1: Deterministic rules. Fast, high precision, no model.
    Stage 2: 3B auditor model for ambiguous boundary cases only.

    Args:
        text: The request or action text to classify.
        ctx: Optional session context (reservoir_scope, confirmation_granted, etc.)
        ollama_url: Ollama API endpoint.
        model: Auditor model name.
        use_auditor: If False, only deterministic stage runs (useful for testing).
        timeout: Model call timeout in seconds.

    Returns:
        GateDecision with verdict, rule_id, confidence, and receipt_id.
    """
    ctx = ctx or {}
    ctx["raw_text"] = text
    lower = _normalize(text)

    # Stage 1
    deterministic = _deterministic_gate(text, lower, ctx)
    if deterministic is not None:
        return deterministic

    # Stage 2
    if not use_auditor:
        receipt = _new_receipt()
        return GateDecision(
            verdict="ALLOW",
            rule_id="R4",
            confidence=0.5,
            risk_tags=["no_auditor"],
            rationale="No deterministic rule matched. Auditor disabled. Defaulting to ALLOW.",
            evidence=[],
            needs_human=False,
            receipt_id=receipt,
            stage="deterministic",
        )

    return _auditor_gate(text, ollama_url=ollama_url, model=model, timeout=timeout)
