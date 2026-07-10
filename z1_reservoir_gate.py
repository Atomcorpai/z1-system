"""
z1_reservoir_gate.py
Cold-storage / reservoir access control for z1.

Role:
    The ledger may point to cold storage, but the runtime must not automatically ingest it.
    Reservoir access requires explicit authorization and specific scope.

Required authorization format:
    OPEN_RESERVOIR: [specific file/folder/request]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import re
from typing import List, Optional, Sequence


AUTH_PREFIX = "OPEN_RESERVOIR:"


class ReservoirDecision(str, Enum):
    ALLOW = "ALLOW"
    AUTH_REQUIRED = "RESERVOIR_AUTH_REQUIRED"
    SCOPE_REQUIRED = "RESERVOIR_SCOPE_REQUIRED"
    BLOCK = "BLOCK"


@dataclass
class ReservoirGateResult:
    decision: ReservoirDecision
    reason: str
    scope: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


def parse_authorization(text: str) -> Optional[str]:
    if not text:
        return None
    pattern = re.compile(r"OPEN_RESERVOIR:\s*(.+)", flags=re.IGNORECASE | re.DOTALL)
    match = pattern.search(text)
    if not match:
        return None
    scope = match.group(1).strip()
    return scope or None


class ReservoirGate:
    def __init__(self, allowed_roots: Optional[Sequence[str | Path]] = None):
        self.allowed_roots = [Path(p).resolve() for p in (allowed_roots or [])]

    def authorize(self, request_text: str, *, requested_target: Optional[str] = None) -> ReservoirGateResult:
        scope = parse_authorization(request_text)
        if not scope:
            return ReservoirGateResult(
                ReservoirDecision.AUTH_REQUIRED,
                "Reservoir/cold-storage access requires explicit OPEN_RESERVOIR authorization with specific scope.",
            )
        if scope.lower() in {"all", "everything", "whatever", "all files", "full archive"}:
            return ReservoirGateResult(
                ReservoirDecision.SCOPE_REQUIRED,
                "Reservoir scope is too broad. Request a specific file, folder, term, or evidence target.",
                scope=scope,
            )
        if requested_target and requested_target.lower() not in scope.lower() and scope.lower() not in requested_target.lower():
            return ReservoirGateResult(
                ReservoirDecision.SCOPE_REQUIRED,
                "Requested target and authorization scope do not clearly match.",
                scope=scope,
                warnings=[f"requested_target={requested_target}"],
            )
        return ReservoirGateResult(
            ReservoirDecision.ALLOW,
            "Reservoir access authorized for specific scope. Retrieved content must be labeled as retrieved, not current truth.",
            scope=scope,
        )

    def validate_path_scope(self, candidate: str | Path) -> ReservoirGateResult:
        path = Path(candidate).resolve()
        if not self.allowed_roots:
            return ReservoirGateResult(ReservoirDecision.ALLOW, "No root restriction configured.", scope=str(path))
        for root in self.allowed_roots:
            try:
                path.relative_to(root)
                return ReservoirGateResult(ReservoirDecision.ALLOW, "Path is within allowed reservoir root.", scope=str(path))
            except ValueError:
                continue
        return ReservoirGateResult(ReservoirDecision.BLOCK, "Path is outside allowed reservoir roots.", scope=str(path))
