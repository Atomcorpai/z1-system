"""
z1_dam.py
Coordinator / flow controller for the z1 dam layer.

Role:
    Orchestrates ledger, action guard, and reservoir gate.
    Decides whether a request may flow forward into runtime/tool execution/retrieval.

Outputs:
    ALLOW
    STOP_FOR_CLARITY
    BLOCK_DESTRUCTIVE
    LEDGER_CONFLICT
    LEDGER_FAILURE
    RESERVOIR_AUTH_REQUIRED

This file is the implementation-facing version of the z1 dam spec.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Dict, List, Optional

from z1_action_guard import ActionDecision, ActionGuard, GuardResult
from z1_core import (
    LedgerFailureState,
    load_ledger,
    save_ledger,
    RuntimeMemoryPersistenceLedger,
)


# ---------------------------------------------------------------------------
# Thin adapter: maps z1_ledger-style class API onto z1_core functions
# so the rest of this file needs no changes.
# ---------------------------------------------------------------------------

class LedgerStatus:
    """Aliases onto LedgerFailureState values expected by z1Dam."""
    OK = LedgerFailureState.AVAILABLE
    UNAVAILABLE = LedgerFailureState.UNAVAILABLE
    CORRUPTED = LedgerFailureState.CORRUPTED
    STALE = LedgerFailureState.STALE
    CONFLICT = LedgerFailureState.CONFLICT


from dataclasses import dataclass as _dc

@_dc
class _LedgerCheckResult:
    status: object
    message: str
    ledger: object = None


class RuntimeLedger:
    """Adapter: wraps z1_core load_ledger/save_ledger behind the class API."""

    def load(self) -> _LedgerCheckResult:
        state, ledger, message = load_ledger()
        # Map LedgerFailureState -> LedgerStatus alias
        status_map = {
            LedgerFailureState.AVAILABLE: LedgerStatus.OK,
            LedgerFailureState.UNAVAILABLE: LedgerStatus.UNAVAILABLE,
            LedgerFailureState.CORRUPTED: LedgerStatus.CORRUPTED,
            LedgerFailureState.STALE: LedgerStatus.STALE,
            LedgerFailureState.CONFLICT: LedgerStatus.CONFLICT,
            LedgerFailureState.CAPABILITY_CONFLICT: LedgerStatus.CONFLICT,
        }
        return _LedgerCheckResult(
            status=status_map.get(state, LedgerStatus.UNAVAILABLE),
            message=message,
            ledger=ledger,
        )

    def log_rejected_path(self, action: str, reason: str, context: dict = None) -> None:
        state, ledger, _ = load_ledger()
        if ledger is None:
            return
        ledger.reject_path(path=action[:240], reason=reason)
        save_ledger(ledger, backup=False)
from z1_reservoir_gate import ReservoirDecision, ReservoirGate, ReservoirGateResult


class DamDecision(str, Enum):
    ALLOW = "ALLOW"
    STOP_FOR_CLARITY = "STOP_FOR_CLARITY"
    BLOCK_DESTRUCTIVE = "BLOCK_DESTRUCTIVE"
    LEDGER_FAILURE = "LEDGER_FAILURE"
    LEDGER_CONFLICT = "LEDGER_CONFLICT"
    RESERVOIR_AUTH_REQUIRED = "RESERVOIR_AUTH_REQUIRED"


@dataclass
class SiloSignal:
    """One silo's read on a request. Auditors produce signals, not verdicts."""
    silo_id: str
    verdict: str          # ALLOW | BLOCK | STOP_FOR_CLARITY | NO_OPINION
    confidence: float
    reason: str


@dataclass
class DamResult:
    decision: DamDecision
    reason: str
    action_guard: Optional[GuardResult] = None
    reservoir_gate: Optional[ReservoirGateResult] = None
    ledger_status: Optional[LedgerStatus] = None
    assumptions: List[str] = field(default_factory=list)
    required_next_step: Optional[str] = None
    silo_signals: List[SiloSignal] = field(default_factory=list)
    arbitration_note: Optional[str] = None


def arbitrate_signals(signals: List[SiloSignal]) -> tuple:
    """
    Single explicit place that resolves conflicting silo opinions.
    Returns (resolved_verdict, arbitration_note).
    """
    if not signals:
        return "UNCLASSIFIED", "No silo produced a signal. Logged as a visible gap, not defaulted to block."

    opinions = [s for s in signals if s.verdict != "NO_OPINION"]
    if not opinions:
        names = ", ".join(s.silo_id for s in signals)
        return "UNCLASSIFIED", f"No silo among [{names}] had an opinion. Visible gap, not a silent block."

    blocks = [s for s in opinions if s.verdict == "BLOCK" and s.confidence >= 0.7]
    if blocks:
        names = ", ".join(f"{s.silo_id} ({s.confidence:.2f})" for s in blocks)
        return "BLOCK", f"Blocked by: {names}."

    clarifies = [s for s in opinions if s.verdict == "STOP_FOR_CLARITY"]
    if clarifies:
        names = ", ".join(s.silo_id for s in clarifies)
        return "STOP_FOR_CLARITY", f"Clarification requested by: {names}."

    weak_blocks = [s for s in opinions if s.verdict == "BLOCK"]
    if weak_blocks:
        names = ", ".join(f"{s.silo_id} ({s.confidence:.2f})" for s in weak_blocks)
        return "STOP_FOR_CLARITY", f"Low-confidence block from: {names}. Downgraded rather than silently blocked."

    names = ", ".join(s.silo_id for s in opinions)
    return "ALLOW", f"All opinions agree: {names} -> ALLOW."


AMBIGUOUS_ACTION_TERMS = [
    "clean up everything", "fix everything", "remove everything",
    "archive everything", "update everything", "sync everything",
    "migrate everything", "restore everything", "replace everything",
    "delete everything", "handle everything", "handle it", "deal with it",
    "sort it out", "take care of it",
]

RESERVOIR_TERMS = [
    "reservoir", "cold storage", "old files", "chat history",
    "dump.txt", ".gsd", "scaffolding files",
]


def _mentions_any(text: str, terms: List[str]) -> bool:
    text = (text or "").lower()
    for term in terms:
        if ' ' in term:
            if term in text:
                return True
        else:
            if re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', text):
                return True
    return False


def _looks_ambiguous(text: str) -> bool:
    lower = (text or "").lower()
    if _mentions_any(lower, AMBIGUOUS_ACTION_TERMS):
        target_markers = [
            "file", "folder", "path", "named", "called", "this", "these",
            "only", "specific", "the ledger", "the log", "the silo",
        ]
        return not any(marker in lower for marker in target_markers)
    return False


class z1Dam:
    def __init__(
        self,
        ledger: Optional[RuntimeLedger] = None,
        action_guard: Optional[ActionGuard] = None,
        reservoir_gate: Optional[ReservoirGate] = None,
        *,
        allow_current_session_without_ledger: bool = True,
    ):
        self.ledger = ledger or RuntimeLedger()
        self.action_guard = action_guard or ActionGuard()
        self.reservoir_gate = reservoir_gate or ReservoirGate()
        self.allow_current_session_without_ledger = allow_current_session_without_ledger

    def inspect_request(
        self,
        request_text: str,
        *,
        confirmation: bool = False,
        inspected_artifact: bool = True,
        requested_reservoir_target: Optional[str] = None,
        silo_id: Optional[str] = None,
    ) -> DamResult:
        """
        Classify a request before runtime/tool/retrieval execution.
        This does not execute the request.

        silo_id: which silo the router placed this request into, if known.
        If that silo's gate has system_gate_active=False, skip governance
        entirely — conversation is not the gate's job.
        """
        request_text = request_text or ""
        ledger_check = self.ledger.load()
        signals: List[SiloSignal] = []

        if silo_id:
            try:
                from gumbo_silo_manifest import get_gate_ruleset
                ruleset = get_gate_ruleset(silo_id)
                if ruleset and not ruleset.system_gate_active:
                    return DamResult(
                        DamDecision.ALLOW,
                        f"Silo '{silo_id}' has system_gate_active=False. Conversational content is not governed.",
                        ledger_status=ledger_check.status,
                        silo_signals=[SiloSignal(silo_id, "ALLOW", 1.0, "Silo scope excludes system gate.")],
                    )
            except ImportError:
                pass

        if ledger_check.status not in {LedgerStatus.OK, LedgerStatus.UNAVAILABLE}:
            return DamResult(
                DamDecision.LEDGER_FAILURE,
                f"Ledger failure: {ledger_check.message}",
                ledger_status=ledger_check.status,
                required_next_step="Stop before acting. Use current-session facts only or restore/update the ledger.",
            )

        if ledger_check.status == LedgerStatus.UNAVAILABLE and not self.allow_current_session_without_ledger:
            return DamResult(
                DamDecision.LEDGER_FAILURE,
                "Ledger unavailable and current-session-only fallback is disabled.",
                ledger_status=ledger_check.status,
                required_next_step="Restore ledger or explicitly authorize current-session-only operation.",
            )

        if _looks_ambiguous(request_text):
            signals.append(SiloSignal("dam.ambiguity", "STOP_FOR_CLARITY", 0.85,
                                       "Ambiguous action language without clear target/scope."))

        guard_result = self.action_guard.classify(
            request_text,
            confirmation=confirmation,
            inspected_artifact=inspected_artifact,
        )
        if guard_result.decision in {ActionDecision.REQUIRE_CONFIRMATION, ActionDecision.BLOCK}:
            try:
                self.ledger.log_rejected_path(
                    action=request_text[:240],
                    reason=guard_result.reason,
                    context={"matched_terms": guard_result.matched_terms},
                )
            except Exception:
                pass
            signals.append(SiloSignal("dam.action_guard", "BLOCK", 0.95, guard_result.reason))
        elif guard_result.decision == ActionDecision.STOP_FOR_CLARITY:
            signals.append(SiloSignal("dam.action_guard", "STOP_FOR_CLARITY", 0.8, guard_result.reason))
        else:
            signals.append(SiloSignal("dam.action_guard", "ALLOW", 0.7, "Action guard found no restricted pattern."))

        reservoir_result = None
        if _mentions_any(request_text, RESERVOIR_TERMS):
            reservoir_result = self.reservoir_gate.authorize(
                request_text,
                requested_target=requested_reservoir_target,
            )
            if reservoir_result.decision != ReservoirDecision.ALLOW:
                signals.append(SiloSignal("dam.reservoir", "BLOCK", 0.99, reservoir_result.reason))
            else:
                signals.append(SiloSignal("dam.reservoir", "ALLOW", 0.9, "Scoped reservoir authorization present."))

        resolved, arb_note = arbitrate_signals(signals)

        decision_map = {
            "ALLOW": DamDecision.ALLOW,
            "BLOCK": DamDecision.BLOCK_DESTRUCTIVE,
            "STOP_FOR_CLARITY": DamDecision.STOP_FOR_CLARITY,
            "UNCLASSIFIED": DamDecision.STOP_FOR_CLARITY,
        }

        required_next = None
        if resolved == "BLOCK":
            required_next = "Require explicit confirmation or provide a reversible draft/checklist only."
        elif resolved == "STOP_FOR_CLARITY":
            required_next = "Ask for target/scope clarification, or flag as unclassified for routing review."
        elif resolved == "UNCLASSIFIED":
            required_next = "No silo recognized this request. Log as a gap for routing review."

        assumptions = []
        if ledger_check.status != LedgerStatus.OK:
            assumptions.append("Ledger unavailable; proceeding with current-session context only.")

        return DamResult(
            decision_map[resolved],
            arb_note,
            action_guard=guard_result,
            reservoir_gate=reservoir_result,
            ledger_status=ledger_check.status,
            assumptions=assumptions,
            required_next_step=required_next,
            silo_signals=signals,
            arbitration_note=arb_note,
        )

    def build_runtime_preamble(self) -> str:
        """Small boot preamble for prompt/runtime injection."""
        check = self.ledger.load()
        if check.status != LedgerStatus.OK:
            return (
                f"z1 DAM STATUS: {check.status}. {check.message}\n"
                "Use current-session facts only. Do not invent continuity. Stop before destructive or ambiguous actions."
            )
        ledger = check.ledger
        rules = [r.rule for r in getattr(ledger, "runtime_rules", [])]
        task = getattr(ledger, "current_task", "")
        open_loops = getattr(ledger, "open_loops", [])[-5:]
        rejected = getattr(ledger, "rejected_paths", [])[-5:]
        return (
            "z1 DAM STATUS: LEDGER_OK\n"
            f"Current task state: {task}\n"
            f"Safety rules: {rules}\n"
            f"Recent open loops: {open_loops}\n"
            f"Recent rejected paths: {rejected}\n"
            "Do not access reservoir without explicit OPEN_RESERVOIR scope. "
            "No confirmation means no destructive execution."
        )