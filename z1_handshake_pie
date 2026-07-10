from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

import hashlib
import hmac
import json
import uuid


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------
# Runtime Identity
# ---------------------------------------------------------------------

@dataclass
class RuntimeIdentity:
    """
    Stable identity describing the runtime itself.

    This is NOT the conversation.
    This is NOT the journal.

    It is the runtime's durable identity.
    """

    runtime_version: str

    revision: int

    created: str

    updated: str

    principles: List[str] = field(default_factory=list)

    traits: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------
# Reflection Export
# ---------------------------------------------------------------------

@dataclass
class ContextSummary:
    """
    Export of reflected understanding.

    Nothing here should expose private conversations.

    Everything here should be the result of reflection,
    evidence, and compression.
    """

    evidence_summary: List[str] = field(default_factory=list)

    belief_summary: List[str] = field(default_factory=list)

    reflection_summary: List[str] = field(default_factory=list)

    silo_revisions: Dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------
# Handshake Pie
# ---------------------------------------------------------------------

@dataclass
class HandshakePie:
    """
    Signed continuity artifact exchanged between runtimes.
    """

    identity: RuntimeIdentity

    context: ContextSummary

    issued: str = field(default_factory=utc_now)

    nonce: str = field(default_factory=lambda: str(uuid.uuid4()))

    signature: str = "UNSIGNED"

    # -------------------------------------------------------------

    def unsigned_json(self) -> str:

        data = asdict(self)

        data.pop("signature", None)

        return json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":")
        )

    # -------------------------------------------------------------

    def sign(self, key: Optional[bytes]) -> None:

        if key is None:

            self.signature = "UNSIGNED"

            return

        self.signature = hmac.new(
            key,
            self.unsigned_json().encode(),
            hashlib.sha256
        ).hexdigest()

    # -------------------------------------------------------------

    def verify(self, key: Optional[bytes]) -> bool:

        if self.signature == "UNSIGNED":

            return key is None

        expected = hmac.new(
            key,
            self.unsigned_json().encode(),
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(
            expected,
            self.signature
        )


# ---------------------------------------------------------------------
# Continuity Decisions
# ---------------------------------------------------------------------

class HandshakeDecision(Enum):

    ACCEPT = "accept"

    MERGE = "merge"

    ARCHIVE = "archive"

    REJECT = "reject"


# ---------------------------------------------------------------------
# Revision Evaluation
# ---------------------------------------------------------------------

def evaluate_handshake(
    local_revision: int,
    incoming_revision: int,
    signature_valid: bool,
) -> HandshakeDecision:
    """
    Determine what should happen after receiving a Handshake Pie.

    No identity replacement.

    No takeover.

    No masks.

    Only deterministic continuity decisions.
    """

    if not signature_valid:

        return HandshakeDecision.REJECT

    if incoming_revision > local_revision:

        return HandshakeDecision.MERGE

    if incoming_revision == local_revision:

        return HandshakeDecision.ACCEPT

    return HandshakeDecision.ARCHIVE


# ---------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------

def build_handshake(
    identity: RuntimeIdentity,
    context: ContextSummary,
    key: Optional[bytes] = None,
) -> HandshakePie:
    """
    Build and optionally sign a Handshake Pie.
    """

    pie = HandshakePie(
        identity=identity,
        context=context,
    )

    pie.sign(key)

    return pie
