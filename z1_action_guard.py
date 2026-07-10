"""
z1_action_guard.py
Action-risk classifier for the z1 dam layer.

Role:
    Classify user/runtime requests before execution.
    This module does not execute actions. It only returns a decision.

Design goals:
    - boring, explicit, auditable
    - destructive/evidence-affecting actions require confirmation
    - ambiguous instructions stop for clarity
    - safe/reversible analysis proceeds
    - silo context from manifest gates overrides keyword defaults when present

Changes from prior version:
    - classify() now accepts optional silo_context dict
    - silo_context keys: system_gate_active, require_confirmation_destructive,
      require_confirmation_external, hard_stops (list[str])
    - If system_gate_active is False, ALLOW immediately (conversational silo)
    - hard_stops from silo are checked against request text before keyword scan
    - Five governance rule enums align with z1_silo_manifest.py definitions
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Dict, Iterable, List, Optional


# ---------------------------------------------------------------------------
# Five governance rules — first-class, not comments
# ---------------------------------------------------------------------------

class GovernanceRule(str, Enum):
    MUTATION          = "R_MUTATION"
    DELETION          = "R_DELETION"
    EXTERNAL_TX       = "R_EXTERNAL_TX"
    STRUCTURAL_PILLAR = "R_STRUCTURAL_PILLAR"
    BULK_OPS          = "R_BULK_OPS"


RULE_LABELS = {
    GovernanceRule.MUTATION:          "mutation of persistent state",
    GovernanceRule.DELETION:          "deletion or removal",
    GovernanceRule.EXTERNAL_TX:       "external transmission or side effect",
    GovernanceRule.STRUCTURAL_PILLAR: "structural pillar access",
    GovernanceRule.BULK_OPS:          "bulk / unscoped operation",
}


# ---------------------------------------------------------------------------
# Decision + result
# ---------------------------------------------------------------------------

class ActionDecision(str, Enum):
    ALLOW                = "ALLOW"
    STOP_FOR_CLARITY     = "STOP_FOR_CLARITY"
    REQUIRE_CONFIRMATION = "REQUIRE_CONFIRMATION"
    BLOCK                = "BLOCK"


@dataclass
class GuardResult:
    decision: ActionDecision
    reason: str
    matched_terms: List[str]              = field(default_factory=list)
    triggered_rules: List[GovernanceRule] = field(default_factory=list)
    risk_level: str                       = "low"
    reversible: bool                      = True
    evidence_affecting: bool              = False
    destructive: bool                     = False
    silo_hard_stop: bool                  = False


# ---------------------------------------------------------------------------
# Term lists
# ---------------------------------------------------------------------------

DESTRUCTIVE_TERMS = [
    "delete", "remove", "wipe", "erase", "destroy", "purge", "trash",
    "overwrite", "replace", "reset", "format", "drop table", "truncate",
]

EVIDENCE_AFFECTING_TERMS = [
    "edit evidence", "alter evidence", "modify evidence", "change evidence",
    "redact original", "rename original", "delete original", "overwrite original",
    "compress evidence", "re-save evidence", "metadata", "timestamp",
]

EXTERNAL_ACTION_TERMS = [
    "send", "submit", "file", "publish", "post", "email", "forward",
    "upload", "commit", "push", "merge", "deploy", "release",
]

AMBIGUOUS_TARGET_TERMS = [
    "clean up", "fix it", "fix this", "handle it", "do it",
    "make it work", "update everything", "delete the bad ones",
    "remove the wrong ones", "archive everything",
]

SAFE_DRAFT_TERMS = [
    "draft", "review", "summarize", "analyze", "inspect", "compare",
    "explain", "classify", "triage", "recommend", "make a checklist",
]

STRUCTURAL_PILLAR_TERMS = [
    "ledger", "silo manifest", "governance config", "z1_core",
    "z1_dam", "action_guard", "governance seed", "session_seed",
]

BULK_OPS_TERMS = [
    "everything", "all files", "all silos", "batch", "bulk",
    "entire", "all records", "all entries",
]

MUTATION_TERMS = [
    "update", "write", "save", "modify", "change", "edit", "set",
    "append", "insert", "patch", "migrate",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _matched_terms(text: str, terms: Iterable[str]) -> List[str]:
    lower = _normalize(text)
    return [term for term in terms if term in lower]


def _has_explicit_target(text: str) -> bool:
    lower = _normalize(text)
    target_markers = [
        "file", "folder", "path", "named", "called", "this file", "these files",
        ".py", ".json", ".md", ".docx", ".pdf", ".zip", "only", "specific",
    ]
    return any(marker in lower for marker in target_markers)


def _check_hard_stops(text: str, hard_stops: List[str]) -> Optional[str]:
    lower = _normalize(text)
    for stop in hard_stops:
        if _normalize(stop) in lower:
            return stop
    return None


# ---------------------------------------------------------------------------
# Gate context builder — called by bridge to pull from manifest
# ---------------------------------------------------------------------------

def gate_context_from_silo(silo_id: str) -> Dict:
    """
    Pull gate ruleset from z1_silo_manifest and return a plain dict
    that classify() can consume. Gracefully returns permissive defaults
    if the manifest is unavailable so the keyword fallback still runs.
    """
    try:
        from z1_silo_manifest import get_gate_ruleset
        ruleset = get_gate_ruleset(silo_id)
        if ruleset is None:
            return {}
        return {
            "system_gate_active":               ruleset.system_gate_active,
            "require_confirmation_destructive":  ruleset.require_confirmation_destructive,
            "require_confirmation_external":     ruleset.require_confirmation_external,
            "hard_stops":                        list(ruleset.hard_stops),
        }
    except ImportError:
        return {}


# ---------------------------------------------------------------------------
# ActionGuard
# ---------------------------------------------------------------------------

class ActionGuard:
    """
    Classifies requests before the dam allows execution.

    confirmation=True means the user explicitly confirmed the action in the
    current interaction, not merely implied it historically.

    inspected_artifact=True means the relevant artifact was inspected or the
    request is not artifact-dependent.

    silo_context is a dict with keys from gate_context_from_silo(). If empty
    or None the classifier falls through to keyword-only mode (safe default).
    """

    def classify(
        self,
        request_text: str,
        *,
        confirmation: bool = False,
        inspected_artifact: bool = True,
        silo_context: Optional[Dict] = None,
    ) -> GuardResult:
        text = _normalize(request_text)
        ctx = silo_context or {}

        if not text:
            return GuardResult(
                ActionDecision.STOP_FOR_CLARITY,
                "Empty request cannot be safely classified.",
                risk_level="unknown",
            )

        # 1. Silo gate bypass
        if ctx.get("system_gate_active") is False:
            return GuardResult(
                ActionDecision.ALLOW,
                "Silo has system_gate_active=False. Conversational content is not governed.",
                risk_level="low",
                reversible=True,
            )

        # 2. Hard stops from silo
        hard_stops = ctx.get("hard_stops", [])
        if hard_stops:
            matched_stop = _check_hard_stops(text, hard_stops)
            if matched_stop:
                return GuardResult(
                    ActionDecision.BLOCK,
                    f"Silo hard stop matched: '{matched_stop}'. Cannot proceed.",
                    matched_terms=[matched_stop],
                    risk_level="critical",
                    reversible=False,
                    silo_hard_stop=True,
                )

        # 3. Safe intent check
        safe = _matched_terms(text, SAFE_DRAFT_TERMS)
        ambiguous = _matched_terms(text, AMBIGUOUS_TARGET_TERMS)
        if safe and not ambiguous:
            return GuardResult(
                ActionDecision.ALLOW,
                "Safe reversible analysis/drafting request.",
                matched_terms=safe,
                risk_level="low",
                reversible=True,
            )

        # 4. Ambiguity check
        if ambiguous and not _has_explicit_target(text):
            return GuardResult(
                ActionDecision.STOP_FOR_CLARITY,
                "Instruction is ambiguous and lacks a specific target/scope.",
                matched_terms=ambiguous,
                risk_level="ambiguous",
            )

        # 5. Five governance rules

        # R_STRUCTURAL_PILLAR
        structural = _matched_terms(text, STRUCTURAL_PILLAR_TERMS)
        mutation = _matched_terms(text, MUTATION_TERMS)
        if structural and mutation:
            rules = [GovernanceRule.STRUCTURAL_PILLAR, GovernanceRule.MUTATION]
            if confirmation and inspected_artifact:
                return GuardResult(
                    ActionDecision.ALLOW,
                    "Structural pillar mutation allowed: explicit confirmation and artifact inspection present.",
                    matched_terms=structural + mutation,
                    triggered_rules=rules,
                    risk_level="critical",
                    reversible=False,
                )
            return GuardResult(
                ActionDecision.REQUIRE_CONFIRMATION,
                "Structural pillar mutation requires explicit confirmation and artifact inspection.",
                matched_terms=structural + mutation,
                triggered_rules=rules,
                risk_level="critical",
                reversible=False,
            )

        # R_DELETION
        evidence = _matched_terms(text, EVIDENCE_AFFECTING_TERMS)
        destructive = _matched_terms(text, DESTRUCTIVE_TERMS)

        if evidence:
            rules = [GovernanceRule.DELETION]
            req = ctx.get("require_confirmation_destructive", True)
            if not req or (confirmation and inspected_artifact):
                return GuardResult(
                    ActionDecision.ALLOW,
                    "Evidence-affecting action allowed: confirmation and inspection present.",
                    matched_terms=evidence,
                    triggered_rules=rules,
                    risk_level="high",
                    reversible=False,
                    evidence_affecting=True,
                )
            return GuardResult(
                ActionDecision.REQUIRE_CONFIRMATION,
                "Evidence-affecting action requires explicit confirmation and artifact inspection.",
                matched_terms=evidence,
                triggered_rules=rules,
                risk_level="high",
                reversible=False,
                evidence_affecting=True,
            )

        if destructive:
            rules = [GovernanceRule.DELETION]
            req = ctx.get("require_confirmation_destructive", True)
            if not req or (confirmation and inspected_artifact):
                return GuardResult(
                    ActionDecision.ALLOW,
                    "Destructive action allowed: confirmation and inspection present.",
                    matched_terms=destructive,
                    triggered_rules=rules,
                    risk_level="high",
                    reversible=False,
                    destructive=True,
                )
            return GuardResult(
                ActionDecision.REQUIRE_CONFIRMATION,
                "Destructive or irreversible action requires explicit confirmation.",
                matched_terms=destructive,
                triggered_rules=rules,
                risk_level="high",
                reversible=False,
                destructive=True,
            )

        # R_BULK_OPS
        bulk = _matched_terms(text, BULK_OPS_TERMS)
        if bulk and not _has_explicit_target(text):
            return GuardResult(
                ActionDecision.STOP_FOR_CLARITY,
                "Bulk operation without explicit scope. Narrow the target.",
                matched_terms=bulk,
                triggered_rules=[GovernanceRule.BULK_OPS],
                risk_level="high",
            )

        # R_EXTERNAL_TX
        external = _matched_terms(text, EXTERNAL_ACTION_TERMS)
        if external:
            rules = [GovernanceRule.EXTERNAL_TX]
            req = ctx.get("require_confirmation_external", True)
            if not req or confirmation:
                return GuardResult(
                    ActionDecision.ALLOW,
                    "External/action-taking request allowed: confirmation present.",
                    matched_terms=external,
                    triggered_rules=rules,
                    risk_level="medium",
                    reversible=False,
                )
            return GuardResult(
                ActionDecision.REQUIRE_CONFIRMATION,
                "External action requires explicit confirmation before execution.",
                matched_terms=external,
                triggered_rules=rules,
                risk_level="medium",
                reversible=False,
            )

        # R_MUTATION
        if mutation and not _has_explicit_target(text):
            return GuardResult(
                ActionDecision.STOP_FOR_CLARITY,
                "Mutation requested without a specific target. Clarify before proceeding.",
                matched_terms=mutation,
                triggered_rules=[GovernanceRule.MUTATION],
                risk_level="ambiguous",
            )

        # 6. Uninspected artifact
        if not inspected_artifact and _has_explicit_target(text):
            return GuardResult(
                ActionDecision.STOP_FOR_CLARITY,
                "Referenced artifact has not been inspected.",
                risk_level="ambiguous",
            )

        # 7. Default ALLOW
        return GuardResult(
            ActionDecision.ALLOW,
            "No governance rule triggered.",
            risk_level="low",
            reversible=True,
        )
