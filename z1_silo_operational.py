"""
z1_silo_operational.py
Silo 1: OPERATIONAL

Owns:
    - Operator identity (who is running this system)
    - Trust contract (what the runtime is allowed to do)
    - Compressed session kernels (facts that survived the dam across sessions)

Contract:
    This is what the model reads at boot to know who it's talking to,
    what the standing rules are, and what has been established.
    It replaces the journal. The journal was linear append; this is structured
    and survives compression because it earns its place.

    Dense facts that recur across multiple contextual dimensions graduate here
    from session context — they become standing assumptions, behavioral baselines,
    or identity-adjacent facts. Nothing promotes itself. Python promotes it.

Storage:
    Postgres table: silo_operational (already exists on Railway)
    Columns expected:
        id SERIAL PRIMARY KEY,
        entry_key TEXT UNIQUE NOT NULL,       -- machine-readable label
        entry_type TEXT NOT NULL,             -- IDENTITY | TRUST | KERNEL | BASELINE
        content TEXT NOT NULL,               -- the actual fact/rule/kernel
        source_session TEXT,                 -- session_id that wrote this
        confidence FLOAT DEFAULT 1.0,        -- 0.0-1.0; below 0.5 = stale candidate
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW(),
        digest TEXT UNIQUE                   -- sha256 of entry_key+content, dedup guard
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras


# ---------------------------------------------------------------------------
# Entry types
# ---------------------------------------------------------------------------

class EntryType(str, Enum):
    IDENTITY  = "IDENTITY"   # Who the operator is. Name, role, auth level.
    TRUST     = "TRUST"      # What the runtime is allowed to do. Behavioral contract.
    KERNEL    = "KERNEL"     # Compressed session fact that survived the dam.
    BASELINE  = "BASELINE"   # Standing assumption / behavioral default locked in.


# ---------------------------------------------------------------------------
# Data shape
# ---------------------------------------------------------------------------

@dataclass
class OperationalEntry:
    entry_key:      str
    entry_type:     EntryType
    content:        str
    source_session: Optional[str]  = None
    confidence:     float          = 1.0
    created_at:     Optional[str]  = None
    updated_at:     Optional[str]  = None
    digest:         Optional[str]  = None

    def compute_digest(self) -> str:
        raw = f"{self.entry_key}:{self.content}"
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class Silo1LoadResult:
    ok:      bool
    entries: List[OperationalEntry] = field(default_factory=list)
    error:   Optional[str]          = None

    def identity_block(self) -> List[OperationalEntry]:
        return [e for e in self.entries if e.entry_type == EntryType.IDENTITY]

    def trust_block(self) -> List[OperationalEntry]:
        return [e for e in self.entries if e.entry_type == EntryType.TRUST]

    def kernels(self) -> List[OperationalEntry]:
        return [e for e in self.entries if e.entry_type == EntryType.KERNEL]

    def baselines(self) -> List[OperationalEntry]:
        return [e for e in self.entries if e.entry_type == EntryType.BASELINE]

    def to_preamble(self) -> str:
        """
        Produces the text block injected into the model's context at session boot.
        Structured for fast model parsing, not human reading.
        """
        if not self.ok:
            return f"SILO_1_OPERATIONAL: UNAVAILABLE — {self.error}"

        lines = ["=== SILO_1_OPERATIONAL ==="]

        identity = self.identity_block()
        if identity:
            lines.append("[OPERATOR IDENTITY]")
            for e in identity:
                lines.append(f"  {e.entry_key}: {e.content}")

        trust = self.trust_block()
        if trust:
            lines.append("[TRUST CONTRACT]")
            for e in trust:
                lines.append(f"  {e.entry_key}: {e.content}")

        kernels = self.kernels()
        if kernels:
            lines.append("[SESSION KERNELS]")
            for e in kernels:
                conf = f" (confidence={e.confidence:.2f})" if e.confidence < 1.0 else ""
                lines.append(f"  {e.entry_key}: {e.content}{conf}")

        baselines = self.baselines()
        if baselines:
            lines.append("[BEHAVIORAL BASELINES]")
            for e in baselines:
                lines.append(f"  {e.entry_key}: {e.content}")

        lines.append("=== END SILO_1_OPERATIONAL ===")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def _get_conn():
    """
    Reads DATABASE_URL from environment (Railway injects this).
    Raises clearly if missing — no silent fallback.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL not set. "
            "Silo 1 requires Postgres. "
            "On Railway: ensure the Postgres plugin is attached and DATABASE_URL is shared."
        )
    return psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------

BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS silo_operational (
    id              SERIAL PRIMARY KEY,
    entry_key       TEXT NOT NULL,
    entry_type      TEXT NOT NULL,
    content         TEXT NOT NULL,
    source_session  TEXT,
    confidence      FLOAT DEFAULT 1.0,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    digest          TEXT UNIQUE
);

CREATE INDEX IF NOT EXISTS silo_op_type_idx ON silo_operational(entry_type);
CREATE INDEX IF NOT EXISTS silo_op_key_idx  ON silo_operational(entry_key);
"""

def bootstrap_schema() -> None:
    """
    Idempotent. Safe to call on every boot if needed.
    Creates silo_operational if it doesn't exist.
    """
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(BOOTSTRAP_SQL)
        conn.commit()


# ---------------------------------------------------------------------------
# Core read
# ---------------------------------------------------------------------------

def load_silo1(min_confidence: float = 0.0) -> Silo1LoadResult:
    """
    Loads all entries from silo_operational above min_confidence threshold.
    Default 0.0 loads everything; pass 0.5 to skip stale candidates.
    Ordered: IDENTITY → TRUST → BASELINE → KERNEL (kernels last, most volatile).
    """
    ORDER = {
        EntryType.IDENTITY: 0,
        EntryType.TRUST:    1,
        EntryType.BASELINE: 2,
        EntryType.KERNEL:   3,
    }

    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT entry_key, entry_type, content,
                           source_session, confidence, created_at, updated_at, digest
                    FROM silo_operational
                    WHERE confidence >= %s
                    ORDER BY entry_type, created_at
                    """,
                    (min_confidence,),
                )
                rows = cur.fetchall()

        entries = []
        for row in rows:
            try:
                etype = EntryType(row["entry_type"])
            except ValueError:
                etype = EntryType.KERNEL  # unknown type falls to kernel tier
            entries.append(OperationalEntry(
                entry_key      = row["entry_key"],
                entry_type     = etype,
                content        = row["content"],
                source_session = row["source_session"],
                confidence     = float(row["confidence"] or 1.0),
                created_at     = str(row["created_at"]) if row["created_at"] else None,
                updated_at     = str(row["updated_at"]) if row["updated_at"] else None,
                digest         = row["digest"],
            ))

        # Sort by type tier, then created_at within tier
        entries.sort(key=lambda e: (ORDER.get(e.entry_type, 99), e.created_at or ""))
        return Silo1LoadResult(ok=True, entries=entries)

    except Exception as exc:
        return Silo1LoadResult(ok=False, error=str(exc))


# ---------------------------------------------------------------------------
# Core write
# ---------------------------------------------------------------------------

def upsert_entry(
    entry_key:      str,
    entry_type:     EntryType,
    content:        str,
    source_session: Optional[str] = None,
    confidence:     float         = 1.0,
) -> OperationalEntry:
    """
    Insert or update an entry.
    Deduplication: digest = sha256(entry_key:content).
    If the same key arrives with new content, it updates in place and bumps updated_at.
    If the same key+content arrives again, the digest UNIQUE constraint silently no-ops.
    """
    entry = OperationalEntry(
        entry_key      = entry_key,
        entry_type     = entry_type,
        content        = content,
        source_session = source_session,
        confidence     = confidence,
    )
    entry.digest = entry.compute_digest()

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO silo_operational
                    (entry_key, entry_type, content, source_session, confidence, digest)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (entry_key) DO UPDATE
                    SET content        = EXCLUDED.content,
                        entry_type     = EXCLUDED.entry_type,
                        source_session = EXCLUDED.source_session,
                        confidence     = EXCLUDED.confidence,
                        digest         = EXCLUDED.digest,
                        updated_at     = NOW()
                """,
                (
                    entry.entry_key,
                    entry.entry_type.value,
                    entry.content,
                    entry.source_session,
                    entry.confidence,
                    entry.digest,
                ),
            )
        conn.commit()

    return entry


def write_kernel(
    key:            str,
    content:        str,
    session_id:     Optional[str] = None,
    confidence:     float         = 1.0,
) -> OperationalEntry:
    """Convenience wrapper: write a compressed session kernel."""
    return upsert_entry(key, EntryType.KERNEL, content, session_id, confidence)


def write_identity(key: str, content: str) -> OperationalEntry:
    """Convenience wrapper: write an operator identity fact."""
    return upsert_entry(key, EntryType.IDENTITY, content)


def write_trust(key: str, content: str) -> OperationalEntry:
    """Convenience wrapper: write a trust contract rule."""
    return upsert_entry(key, EntryType.TRUST, content)


def write_baseline(key: str, content: str, session_id: Optional[str] = None) -> OperationalEntry:
    """Convenience wrapper: write a behavioral baseline."""
    return upsert_entry(key, EntryType.BASELINE, content, session_id)


# ---------------------------------------------------------------------------
# Graduation: session context → silo entry
# ---------------------------------------------------------------------------

def graduate_to_silo(
    key:            str,
    content:        str,
    entry_type:     EntryType,
    session_id:     Optional[str] = None,
    confidence:     float         = 1.0,
    reason:         str           = "",
) -> dict:
    """
    Called by Python (not the model) when a session-context fact is dense
    enough across multiple contextual dimensions to warrant permanent promotion.

    'Dense enough' is a Python decision based on recurrence count,
    cross-silo signal overlap, and operator explicit promotion.
    The model never calls this directly.

    Returns a receipt dict.
    """
    entry = upsert_entry(key, entry_type, content, session_id, confidence)
    return {
        "action": "GRADUATED",
        "entry_key": key,
        "entry_type": entry_type.value,
        "reason": reason,
        "digest": entry.digest,
        "session_id": session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Decay: mark stale candidates
# ---------------------------------------------------------------------------

def decay_confidence(entry_key: str, delta: float = 0.1) -> None:
    """
    Reduce confidence on an entry. Entries below 0.3 are stale candidates
    for review or deletion. Nothing is automatically deleted.
    """
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE silo_operational
                SET confidence = GREATEST(0.0, confidence - %s),
                    updated_at = NOW()
                WHERE entry_key = %s
                """,
                (delta, entry_key),
            )
        conn.commit()


def list_stale(threshold: float = 0.3) -> List[OperationalEntry]:
    """Returns entries with confidence below threshold for human review."""
    result = load_silo1(min_confidence=0.0)
    if not result.ok:
        return []
    return [e for e in result.entries if e.confidence < threshold]


# ---------------------------------------------------------------------------
# Seed: bootstrap AtomCorp operator identity
# ---------------------------------------------------------------------------

ATOMCORP_IDENTITY_SEED: List[Dict[str, str]] = [
    {
        "key":     "operator.name",
        "type":    EntryType.IDENTITY,
        "content": "Adam Dolin — solo founder, AtomCorp. NSF REACH Scholar. Background in consultative sales and startup scaling.",
    },
    {
        "key":     "operator.system",
        "type":    EntryType.IDENTITY,
        "content": "Z1 — deterministic Python governance layer for agentic AI. Core principle: the model proposes. Python disposes.",
    },
    {
        "key":     "operator.auth_level",
        "type":    EntryType.IDENTITY,
        "content": "OWNER. The operator is not the threat model. The owner has full trust.",
    },
    {
        "key":     "trust.honesty",
        "type":    EntryType.TRUST,
        "content": "Honesty is required, not optional. The operator needs to hear what he does not want to hear. Soft agreement is a failure mode.",
    },
    {
        "key":     "trust.no_unsolicited_directives",
        "type":    EntryType.TRUST,
        "content": "Do not end responses by telling the operator what to do next. No 'go to sleep', 'go eat', etc.",
    },
    {
        "key":     "trust.no_repetition",
        "type":    EntryType.TRUST,
        "content": "Do not repeat the same point across a response. Say it once.",
    },
    {
        "key":     "trust.complete_files",
        "type":    EntryType.TRUST,
        "content": "Deliver complete file rewrites, not surgical edits. Finding exact replacement locations in large files is error-prone.",
    },
    {
        "key":     "trust.python_is_the_wall",
        "type":    EntryType.TRUST,
        "content": "Python enforces decisions and writes receipts. The model reports outcomes. The model does not police itself.",
    },
    {
        "key":     "baseline.silos_are_the_product",
        "type":    EntryType.BASELINE,
        "content": "Z1 was built to provide state management, context persistence, and silo-organized business knowledge. Governance is the trust layer on top of that, not the reason for existence.",
    },
    {
        "key":     "baseline.phase_boundaries",
        "type":    EntryType.BASELINE,
        "content": "Phase 1 problems get Phase 1 solutions. The operator knows when something is a Phase 2 problem and flags it explicitly.",
    },
    {
        "key":     "baseline.quorum_is_the_bridge",
        "type":    EntryType.BASELINE,
        "content": "Quorum model is a bridge to the contextual classifier. It generates training examples in the interim. Sub-quorum blocks return ALLOW.",
    },
]


def seed_atomcorp_identity(force: bool = False) -> List[dict]:
    """
    Writes the AtomCorp operator identity and trust contract to silo_operational.
    Safe to call on every boot — upsert_entry is idempotent.
    Set force=True to overwrite even if content hasn't changed (rare).
    Returns list of receipts.
    """
    receipts = []
    for item in ATOMCORP_IDENTITY_SEED:
        entry = upsert_entry(
            entry_key  = item["key"],
            entry_type = item["type"],
            content    = item["content"],
            confidence = 1.0,
        )
        receipts.append({
            "entry_key": entry.entry_key,
            "entry_type": entry.entry_type.value,
            "digest": entry.digest,
        })
    return receipts


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse, sys

    parser = argparse.ArgumentParser(description="z1_silo_operational CLI")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("bootstrap",    help="Create table if not exists")
    sub.add_parser("seed",         help="Write AtomCorp identity and trust contract")
    sub.add_parser("load",         help="Load and print preamble")
    sub.add_parser("stale",        help="List stale entries (confidence < 0.3)")

    write_p = sub.add_parser("write", help="Write a single entry")
    write_p.add_argument("entry_key")
    write_p.add_argument("entry_type", choices=[e.value for e in EntryType])
    write_p.add_argument("content")
    write_p.add_argument("--session", default=None)
    write_p.add_argument("--confidence", type=float, default=1.0)

    args = parser.parse_args()

    if args.cmd == "bootstrap":
        bootstrap_schema()
        print("Schema bootstrapped.")

    elif args.cmd == "seed":
        receipts = seed_atomcorp_identity()
        for r in receipts:
            print(f"  {r['entry_type']:10s}  {r['entry_key']}")
        print(f"\n{len(receipts)} entries written.")

    elif args.cmd == "load":
        result = load_silo1()
        print(result.to_preamble())
        print(f"\n({len(result.entries)} entries loaded)")

    elif args.cmd == "stale":
        stale = list_stale()
        if not stale:
            print("No stale entries.")
        else:
            for e in stale:
                print(f"  [{e.confidence:.2f}] {e.entry_key}: {e.content[:80]}")

    elif args.cmd == "write":
        entry = upsert_entry(
            args.entry_key,
            EntryType(args.entry_type),
            args.content,
            args.session,
            args.confidence,
        )
        print(f"Written: {entry.entry_key} ({entry.entry_type.value}) digest={entry.digest[:12]}...")

    else:
        parser.print_help()
        sys.exit(1)
