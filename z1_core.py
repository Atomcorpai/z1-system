"""
Runtime Memory Persistence Ledger (z1)
Version: 1.4
Date: 2026-04-29
System: z1 — Cognition Runtime Operating System

Purpose:
    A small, authoritative continuity and provenance record for agent runtime governance.

z1 is:
    - not RAG
    - not identity roleplay
    - not a memory dump
    - not a replacement for direct artifact inspection

Core rule:
    Verify before claiming.
    Stop before guessing.
    Ask before acting.
    Log after acting.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
import hashlib
import hmac
import json
import os
import shutil


z1_VERSION = "1.4"
SYSTEM_NAME = "z1"
SYSTEM_TYPE = "Cognition Runtime Operating System"

DEFAULT_LEDGER_PATH = Path("runtime_memory_persistence_ledger.json")
DEFAULT_LOG_PATH = Path("runtime_memory_persistence_log.jsonl")

RESERVOIR_AUTH_PREFIX = "OPEN_RESERVOIR:"


class FactBasis(str, Enum):
    DIRECTLY_OBSERVED = "directly_observed"
    USER_STATED = "user_stated"
    LEDGER_CONFIRMED = "ledger_confirmed"
    LOG_CONFIRMED = "log_confirmed"
    INFERRED = "inferred"
    ASSUMED = "assumed"
    UNKNOWN = "unknown_not_checked"


class LedgerFailureState(str, Enum):
    AVAILABLE = "LEDGER_AVAILABLE"
    UNAVAILABLE = "LEDGER_UNAVAILABLE"
    CORRUPTED = "LEDGER_CORRUPTED"
    STALE = "LEDGER_STALE"
    CONFLICT = "LEDGER_CONFLICT"
    CAPABILITY_CONFLICT = "LEDGER_CAPABILITY_CONFLICT"


class TarpitState(str, Enum):
    PASS = "A_PASS_VERIFICATION"
    CONTAINED = "B_CONTAINED_UNRESOLVED"
    ADVERSARIAL = "C_ADVERSARIAL_OR_COORDINATING"


class ActionRisk(str, Enum):
    LOW_REVERSIBLE = "low_reversible"
    AMBIGUOUS = "ambiguous"
    DESTRUCTIVE = "destructive_or_irreversible"
    EVIDENCE_AFFECTING = "evidence_affecting"
    PRODUCTION_AFFECTING = "production_affecting"


class RuntimeDecision(str, Enum):
    PROCEED = "proceed"
    STOP_AND_ASK = "stop_and_ask"
    BLOCK = "block"
    DRAFT_ONLY = "draft_only"
    SELF_AUDIT = "self_audit"
    QUARANTINE = "quarantine"


@dataclass
class VerifiedFact:
    content: str
    basis: FactBasis
    source: Optional[str] = None
    timestamp: str = field(default_factory=lambda: utc_now())


@dataclass
class RejectedPath:
    path: str
    reason: str
    timestamp: str = field(default_factory=lambda: utc_now())


@dataclass
class OpenLoop:
    topic: str
    status: str = "open"
    next_action: Optional[str] = None
    timestamp: str = field(default_factory=lambda: utc_now())


@dataclass
class RuntimeRule:
    name: str
    rule: str
    priority: int = 100


@dataclass
class RuntimeLogEvent:
    event_type: str
    message: str
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: utc_now())


@dataclass
class TarpitResult:
    state: TarpitState
    reason: str
    may_continue_to_hmac: bool = False
    recommended_decision: RuntimeDecision = RuntimeDecision.QUARANTINE


@dataclass
class AmbiguityAssessment:
    is_ambiguous: bool
    triggers: List[str] = field(default_factory=list)
    safest_default: RuntimeDecision = RuntimeDecision.DRAFT_ONLY
    required_question: Optional[str] = None


@dataclass
class ActionRequest:
    instruction: str
    target: Optional[str] = None
    environment: Optional[str] = None
    action_type: Optional[str] = None
    risk: ActionRisk = ActionRisk.AMBIGUOUS
    user_confirmed: bool = False
    inspected_artifacts: List[str] = field(default_factory=list)
    facts_used: List[VerifiedFact] = field(default_factory=list)


@dataclass
class RuntimeMemoryPersistenceLedger:
    version: str = z1_VERSION
    system_name: str = SYSTEM_NAME
    system_type: str = SYSTEM_TYPE
    created_at: str = field(default_factory=lambda: utc_now())
    updated_at: str = field(default_factory=lambda: utc_now())

    current_task: Optional[str] = None
    current_mode: str = "runtime_governance"
    verified_facts: List[VerifiedFact] = field(default_factory=list)
    rejected_paths: List[RejectedPath] = field(default_factory=list)
    open_loops: List[OpenLoop] = field(default_factory=list)
    runtime_rules: List[RuntimeRule] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    reservoir_access_authorized: bool = False
    reservoir_target: Optional[str] = None

    def add_fact(self, content: str, basis: FactBasis, source: Optional[str] = None) -> None:
        self.verified_facts.append(VerifiedFact(content=content, basis=basis, source=source))
        self.touch()

    def reject_path(self, path: str, reason: str) -> None:
        self.rejected_paths.append(RejectedPath(path=path, reason=reason))
        self.touch()

    def add_open_loop(self, topic: str, status: str = "open", next_action: Optional[str] = None) -> None:
        self.open_loops.append(OpenLoop(topic=topic, status=status, next_action=next_action))
        self.touch()

    def add_rule(self, name: str, rule: str, priority: int = 100) -> None:
        self.runtime_rules.append(RuntimeRule(name=name, rule=rule, priority=priority))
        self.touch()

    def touch(self) -> None:
        self.updated_at = utc_now()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RuntimeMemoryPersistenceLedger":
        ledger = cls(
            version=data.get("version", z1_VERSION),
            system_name=data.get("system_name", SYSTEM_NAME),
            system_type=data.get("system_type", SYSTEM_TYPE),
            created_at=data.get("created_at", utc_now()),
            updated_at=data.get("updated_at", utc_now()),
            current_task=data.get("current_task"),
            current_mode=data.get("current_mode", "runtime_governance"),
            reservoir_access_authorized=data.get("reservoir_access_authorized", False),
            reservoir_target=data.get("reservoir_target"),
            notes=data.get("notes", []),
        )
        ledger.verified_facts = [
            VerifiedFact(
                content=f["content"],
                basis=FactBasis(f["basis"]),
                source=f.get("source"),
                timestamp=f.get("timestamp", utc_now()),
            )
            for f in data.get("verified_facts", [])
        ]
        ledger.rejected_paths = [
            RejectedPath(
                path=p["path"],
                reason=p["reason"],
                timestamp=p.get("timestamp", utc_now()),
            )
            for p in data.get("rejected_paths", [])
        ]
        ledger.open_loops = [
            OpenLoop(
                topic=l["topic"],
                status=l.get("status", "open"),
                next_action=l.get("next_action"),
                timestamp=l.get("timestamp", utc_now()),
            )
            for l in data.get("open_loops", [])
        ]
        ledger.runtime_rules = [
            RuntimeRule(
                name=r["name"],
                rule=r["rule"],
                priority=int(r.get("priority", 100)),
            )
            for r in data.get("runtime_rules", [])
        ]
        return ledger


CORE_RULES: List[RuntimeRule] = [
    RuntimeRule("verification_rule", "Separate observed facts from inference. Do not promote inference to fact without confirmation.", 10),
    RuntimeRule("ambiguity_stop_rule", "If the agent does not know what to do, it must stop. Uncertainty is not permission to guess.", 10),
    RuntimeRule("destructive_action_rule", "No destructive or irreversible action without explicit user confirmation.", 10),
    RuntimeRule("artifact_inspection_rule", "Do not claim to have inspected an artifact unless it was directly inspected in the current session.", 10),
    RuntimeRule("retrieval_rule", "Do not access cold storage or reservoir automatically. Reservoir access requires explicit scoped authorization.", 20),
    RuntimeRule("self_audit_rule", "If uncertain, perform bounded self-audit against active ledger/logs before asking or acting.", 20),
    RuntimeRule("anti_deflection_rule", "Do not use unrelated suggestions as an exit ramp from unresolved work. State the actual blocker.", 30),
    RuntimeRule("anti_literalism_rule", "Implementation reality overrides metaphor. Do not treat jokes, lore, names, or anchors as capabilities.", 30),
    RuntimeRule("no_clean_substrate_rule", "Do not assume any model is a blank slate. Detect substrate-specific distortion.", 40),
    RuntimeRule("platform_independence_rule", "z1 must not depend on any single hosted platform, provider, or proprietary assistant environment.", 40),
]


AMBIGUITY_TERMS = {
    "clean up", "fix", "reset", "remove", "archive", "update", "sync", "migrate",
    "delete", "purge", "overwrite", "deploy", "move", "replace", "submit", "send"
}

DESTRUCTIVE_TERMS = {
    "delete", "purge", "drop", "destroy", "wipe", "overwrite", "reset",
    "terminate", "deactivate", "remove", "format", "truncate"
}

BANNED_EXTERNAL_TERMS = {
    "sovereign": "local / independent / user-controlled",
    "vessel": "runtime / process / service",
    "takeover": "governance override / safety interlock",
    "resurrection": "restoration / migration / recovery",
    "escape route": "fallback path / recovery path",
    "house": "runtime / scaffold",
    "sanctuary": "runtime / scaffold",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def backup_file(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    backup = path.with_name(f"{path.stem}_{stamp}_backup{path.suffix}")
    shutil.copy2(path, backup)
    return backup


def save_ledger(ledger: RuntimeMemoryPersistenceLedger, path: Path = DEFAULT_LEDGER_PATH, backup: bool = True) -> None:
    if backup and path.exists():
        backup_file(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(ledger.to_json(), encoding="utf-8")
    os.replace(tmp, path)


def load_ledger(path: Path = DEFAULT_LEDGER_PATH) -> tuple[LedgerFailureState, Optional[RuntimeMemoryPersistenceLedger], str]:
    if not path.exists():
        return LedgerFailureState.UNAVAILABLE, None, "Ledger file does not exist."

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        ledger = RuntimeMemoryPersistenceLedger.from_dict(data)
        return LedgerFailureState.AVAILABLE, ledger, "Ledger loaded."
    except json.JSONDecodeError as exc:
        return LedgerFailureState.CORRUPTED, None, f"Ledger JSON is corrupted: {exc}"
    except Exception as exc:
        return LedgerFailureState.CORRUPTED, None, f"Ledger could not be loaded: {exc}"


def append_log(event: RuntimeLogEvent, path: Path = DEFAULT_LOG_PATH) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")


def classify_fact_basis(facts: List[VerifiedFact]) -> bool:
    allowed = {
        FactBasis.DIRECTLY_OBSERVED,
        FactBasis.USER_STATED,
        FactBasis.LEDGER_CONFIRMED,
        FactBasis.LOG_CONFIRMED,
    }
    return all(f.basis in allowed for f in facts)


def assess_ambiguity(request: ActionRequest) -> AmbiguityAssessment:
    triggers: List[str] = []
    instruction = request.instruction.lower()

    if not request.target:
        triggers.append("target unclear")

    if not request.action_type:
        triggers.append("action type unclear")

    if request.risk in {ActionRisk.DESTRUCTIVE, ActionRisk.PRODUCTION_AFFECTING} and not request.environment:
        triggers.append("environment unclear for high-risk action")

    if any(term in instruction for term in AMBIGUITY_TERMS) and request.risk != ActionRisk.LOW_REVERSIBLE:
        triggers.append("instruction contains state-affecting ambiguous term")

    if not classify_fact_basis(request.facts_used):
        triggers.append("request relies on inferred, assumed, or unknown fact basis")

    if request.risk in {
        ActionRisk.DESTRUCTIVE,
        ActionRisk.EVIDENCE_AFFECTING,
        ActionRisk.PRODUCTION_AFFECTING,
    } and not request.user_confirmed:
        triggers.append("high-risk action lacks explicit confirmation")

    is_ambiguous = bool(triggers)
    question = None
    if is_ambiguous:
        question = "Please confirm the exact target, scope, environment, and action before I proceed."

    return AmbiguityAssessment(
        is_ambiguous=is_ambiguous,
        triggers=triggers,
        safest_default=RuntimeDecision.DRAFT_ONLY,
        required_question=question,
    )


def should_execute(request: ActionRequest) -> tuple[RuntimeDecision, str]:
    ambiguity = assess_ambiguity(request)
    if ambiguity.is_ambiguous:
        return RuntimeDecision.STOP_AND_ASK, "; ".join(ambiguity.triggers)

    if request.risk in {
        ActionRisk.DESTRUCTIVE,
        ActionRisk.EVIDENCE_AFFECTING,
        ActionRisk.PRODUCTION_AFFECTING,
    }:
        if not request.user_confirmed:
            return RuntimeDecision.BLOCK, "High-risk action lacks explicit confirmation."
        return RuntimeDecision.PROCEED, "High-risk action confirmed and ambiguity checks passed."

    return RuntimeDecision.PROCEED, "Low-risk/reversible action with explicit target and safe fact basis."


def tarpit_classify(payload: Dict[str, Any]) -> TarpitResult:
    if not isinstance(payload, dict):
        return TarpitResult(
            state=TarpitState.ADVERSARIAL,
            reason="Payload is not a dictionary.",
            may_continue_to_hmac=False,
            recommended_decision=RuntimeDecision.QUARANTINE,
        )

    required = {"packet_type", "nonce", "scope", "timestamp"}
    missing = required - set(payload.keys())
    if missing:
        return TarpitResult(
            state=TarpitState.CONTAINED,
            reason=f"Missing required fields: {sorted(missing)}",
            may_continue_to_hmac=False,
            recommended_decision=RuntimeDecision.QUARANTINE,
        )

    if int(payload.get("repeated_attempts", 0)) > 3:
        return TarpitResult(
            state=TarpitState.ADVERSARIAL,
            reason="Repeated attempts exceed threshold.",
            may_continue_to_hmac=False,
            recommended_decision=RuntimeDecision.QUARANTINE,
        )

    return TarpitResult(
        state=TarpitState.PASS,
        reason="Payload cleared basic pre-verification shape checks.",
        may_continue_to_hmac=True,
        recommended_decision=RuntimeDecision.PROCEED,
    )


def sign_packet(packet: Dict[str, Any], key: bytes) -> str:
    raw = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hmac.new(key, raw.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_packet(packet: Dict[str, Any], signature: str, key: bytes) -> bool:
    expected = sign_packet(packet, key)
    return hmac.compare_digest(expected, signature)


def create_initial_ledger() -> RuntimeMemoryPersistenceLedger:
    ledger = RuntimeMemoryPersistenceLedger()
    ledger.current_task = "Build and validate z1 as the core continuity/provenance component of z1."
    for rule in CORE_RULES:
        ledger.add_rule(rule.name, rule.rule, rule.priority)

    ledger.add_fact(
        "z1 is a small, authoritative continuity and provenance record for agent runtime governance.",
        FactBasis.USER_STATED,
        "z1 v1.4 canonical construction session",
    )
    ledger.add_fact(
        "The model is an inference engine. The runtime governance layer is the control surface.",
        FactBasis.USER_STATED,
        "z1 v1.4 canonical construction session",
    )
    ledger.add_open_loop(
        "De-Gemini Python runtime files",
        next_action="Strip unsafe or theatrical language while preserving mechanisms.",
    )
    ledger.add_open_loop(
        "Grant rewrite",
        next_action="Use z1 as technical spine after Python runtime files are cleaned.",
    )
    return ledger


if __name__ == "__main__":
    ledger = create_initial_ledger()
    save_ledger(ledger)
    append_log(RuntimeLogEvent(
        event_type="ledger_created",
        message="Initial z1 v1.4 ledger created.",
        data={"version": z1_VERSION, "path": str(DEFAULT_LEDGER_PATH)},
    ))
    print(f"Created {DEFAULT_LEDGER_PATH}")
    print(f"Created/updated {DEFAULT_LOG_PATH}")
