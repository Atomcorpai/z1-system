"""
z1_silo_manifest

Z1 context silo system.

The content router determines WHAT something is.
This file determines WHERE it lives and HOW the gate behaves once it's there.

Architecture:
    Personality substrate — foundational layer, not a silo. Defined in self.
    The lens every silo is read through. No proper nouns baked in.

    Four invariant context silos — the kernel. Every Z1 deployment boots
    with exactly these. They classify by KIND of memory, not topic or vibe:

        OPERATIONAL  — decisions, blockers, how work actually moves
        RELATIONAL   — who the agent knows about, relationship state
        DOMAIN       — facts, fluency, how-things-work knowledge
        SITUATIONAL  — temporary hot-hold for what is live right now

    The bridge — the only cross-silo mover. Silos never import each other.
    Every bridge move hits the immutable LOG. Multi-membership is filed by
    the bridge, not adjudicated — if something belongs in two silos, it
    lands in both. Python files, not picks.

    Tenant tier — attach-or-spawn per deployment, kernel untouched:
        Rule   — auditor bolted onto an existing silo
        Wall   — new silo when context needs its own compartment
        Screen — deterministic word/pattern list, stackable on either

    Walls are exclusive. Multi-membership applies only to the four context
    silos. A walled item goes to its wall and nowhere else, no matter how
    many context labels it might match. That "and" is exactly the leak
    the wall exists to prevent.

Situational decay:
    Items hold a close_condition, not a countdown. They stay hot until an
    event closes them (explicit or recognized). On close the bridge
    re-classifies and files to the item's HOME silo — not always Relational.
    Demote, never delete. The move hits the LOG.

Usage:
    from z1_silo_manifest import get_silo, get_gate_ruleset, SILOS
    silo = get_silo("OPERATIONAL")
    ruleset = get_gate_ruleset("SITUATIONAL")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Tenant tier — how a deployment-level requirement resolves
# ---------------------------------------------------------------------------

class TenantTierKind(str, Enum):
    RULE   = "RULE"    # auditor bolted onto existing silo
    WALL   = "WALL"    # new compartment, single-membership
    SCREEN = "SCREEN"  # deterministic word/pattern list, stackable


# ---------------------------------------------------------------------------
# Gate ruleset
# ---------------------------------------------------------------------------

@dataclass
class GateRuleset:
    """
    Per-silo gate configuration.
    Loaded by the action guard instead of global defaults
    when it knows which silo it is operating in.
    """
    silo_id: str

    # System action governance — dam, ledger rules
    system_gate_active: bool = True

    # Confirmation required for destructive actions
    require_confirmation_destructive: bool = True

    # Confirmation required for external side effects
    require_confirmation_external: bool = True

    # Reservoir access requires OPEN_RESERVOIR prefix
    require_reservoir_prefix: bool = True

    # Whether tenant rules can be appended to this silo
    extensible: bool = True

    # Whether this is a wall — single-membership, no cross-filing
    is_wall: bool = False

    # Hard stops — non-negotiable, never unlocked away
    hard_stops: List[str] = field(default_factory=list)

    # Tenant-appended rules (RULE or SCREEN tier)
    tenant_rules: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Silo definition
# ---------------------------------------------------------------------------

@dataclass
class Silo:
    silo_id: str
    display_name: str
    description: str
    gate: GateRuleset
    content_router_tags: List[str] = field(default_factory=list)

    # Situational only — what event closes a hot item
    # None means this silo does not use decay
    decay_on_close: bool = False

    # Where Situational items go when they close
    # Resolved by bridge at demote time
    home_silo_candidates: List[str] = field(default_factory=list)

    def is_wall(self) -> bool:
        return self.gate.is_wall


# ---------------------------------------------------------------------------
# SILO DEFINITIONS — four invariant context silos
# ---------------------------------------------------------------------------

SILOS: Dict[str, Silo] = {}


# --- OPERATIONAL ------------------------------------------------------------

SILOS["OPERATIONAL"] = Silo(
    silo_id="OPERATIONAL",
    display_name="Operational",
    description=(
        "Decisions, blockers, how work actually moves. "
        "This is the silo of record for anything that affects what gets done "
        "and in what order. Resolved decisions demoted from Situational land here."
    ),
    gate=GateRuleset(
        silo_id="OPERATIONAL",
        system_gate_active=True,
        require_confirmation_destructive=True,
        require_confirmation_external=True,
        require_reservoir_prefix=True,
        extensible=True,
        is_wall=False,
        hard_stops=[
            "no destructive action without explicit confirmation",
            "no external side effects without explicit confirmation",
            "no logging failure becomes permission to execute",
        ],
    ),
    content_router_tags=[
        "decision", "blocker", "task", "priority", "status",
        "action_item", "next_step", "work_product",
    ],
)


# --- RELATIONAL -------------------------------------------------------------

SILOS["RELATIONAL"] = Silo(
    silo_id="RELATIONAL",
    display_name="Relational",
    description=(
        "Who the agent knows about and the state of those relationships. "
        "Contact details, trust level, history, last interaction, pending items. "
        "Relationship events demoted from Situational land here."
    ),
    gate=GateRuleset(
        silo_id="RELATIONAL",
        system_gate_active=True,
        require_confirmation_destructive=True,
        require_confirmation_external=True,
        require_reservoir_prefix=True,
        extensible=True,
        is_wall=False,
        hard_stops=[
            "no destructive action without explicit confirmation",
            "do not invent relationship history not present in ledger",
        ],
    ),
    content_router_tags=[
        "contact", "relationship", "person", "partner", "client",
        "vendor", "stakeholder", "trust", "history",
    ],
)


# --- DOMAIN -----------------------------------------------------------------

SILOS["DOMAIN"] = Silo(
    silo_id="DOMAIN",
    display_name="Domain",
    description=(
        "Facts, fluency, how-things-work knowledge. "
        "The agent's understanding of the subject matter it operates in — "
        "not what is happening right now, but what is true about the world "
        "it works inside. Facts learned while live demoted from Situational land here."
    ),
    gate=GateRuleset(
        silo_id="DOMAIN",
        system_gate_active=True,
        require_confirmation_destructive=True,
        require_confirmation_external=False,   # reading facts is not an external action
        require_reservoir_prefix=True,
        extensible=True,
        is_wall=False,
        hard_stops=[
            "no destructive action without explicit confirmation",
            "do not assert facts without source or verification",
        ],
    ),
    content_router_tags=[
        "fact", "knowledge", "concept", "definition", "how_to",
        "reference", "spec", "rule", "policy", "fluency",
    ],
)


# --- SITUATIONAL ------------------------------------------------------------

SILOS["SITUATIONAL"] = Silo(
    silo_id="SITUATIONAL",
    display_name="Situational",
    description=(
        "Temporary hot-hold for what is live right now. "
        "Items here carry a close_condition, not a countdown. "
        "They stay hot until an event closes them — explicit or recognized. "
        "On close the bridge re-classifies and files to the item's home silo. "
        "Demote, never delete. Every move hits the LOG."
    ),
    gate=GateRuleset(
        silo_id="SITUATIONAL",
        system_gate_active=True,
        require_confirmation_destructive=True,
        require_confirmation_external=True,
        require_reservoir_prefix=False,   # situational is live context, not cold storage
        extensible=True,
        is_wall=False,
        hard_stops=[
            "no destructive action without explicit confirmation",
            "demote on close — never delete",
            "every demote hits the LOG",
        ],
    ),
    content_router_tags=[
        "live", "active", "current", "pending", "open_loop",
        "hot", "in_progress", "waiting_on", "unresolved",
    ],
    decay_on_close=True,
    home_silo_candidates=["OPERATIONAL", "RELATIONAL", "DOMAIN"],
)


# ---------------------------------------------------------------------------
# Runtime helpers
# ---------------------------------------------------------------------------

def get_silo(silo_id: str) -> Optional[Silo]:
    return SILOS.get(silo_id.upper())


def get_gate_ruleset(silo_id: str) -> Optional[GateRuleset]:
    silo = get_silo(silo_id)
    return silo.gate if silo else None


def get_all_silo_ids() -> List[str]:
    return list(SILOS.keys())


def is_wall(silo_id: str) -> bool:
    silo = get_silo(silo_id)
    return silo.is_wall() if silo else False


def register_tenant_silo(
    silo_id: str,
    display_name: str,
    description: str,
    kind: TenantTierKind,
    gate_overrides: Dict[str, Any],
    content_router_tags: List[str],
) -> tuple[bool, str]:
    """
    Register a tenant-tier silo (WALL) or attach a rule/screen to an existing one.

    WALL: creates a new single-membership compartment. Hard stops from the
    kernel are inherited and cannot be removed.

    RULE / SCREEN: appended to an existing extensible silo via tenant_rules.
    Does not create a new silo entry.
    """
    if kind in {TenantTierKind.RULE, TenantTierKind.SCREEN}:
        target = get_silo(silo_id)
        if not target:
            return False, f"Silo {silo_id} not found. Cannot attach {kind.value}."
        if not target.gate.extensible:
            return False, f"Silo {silo_id} is not extensible."
        target.gate.tenant_rules.append(description)
        return True, f"{kind.value} attached to {silo_id}."

    # WALL
    if silo_id.upper() in SILOS:
        return False, f"Silo {silo_id} already exists."

    # Inherit hard stops from all kernel silos — walls cannot be softer
    inherited_hard_stops = list({
        stop
        for s in SILOS.values()
        for stop in s.gate.hard_stops
    })

    gate = GateRuleset(
        silo_id=silo_id.upper(),
        system_gate_active=True,
        require_confirmation_destructive=True,
        require_confirmation_external=True,
        require_reservoir_prefix=True,
        extensible=False,   # walls are not extensible
        is_wall=True,
        hard_stops=inherited_hard_stops,
        **{k: v for k, v in gate_overrides.items()
           if k not in {"hard_stops", "is_wall", "extensible"}},
    )

    silo = Silo(
        silo_id=silo_id.upper(),
        display_name=display_name,
        description=description,
        gate=gate,
        content_router_tags=content_router_tags,
    )

    SILOS[silo_id.upper()] = silo
    return True, f"Wall silo '{display_name}' registered as {silo_id.upper()}. Single-membership enforced."


def silo_status_summary() -> Dict[str, Any]:
    return {
        "kernel_silos": [s.silo_id for s in SILOS.values() if not s.is_wall()],
        "wall_silos":   [s.silo_id for s in SILOS.values() if s.is_wall()],
        "total":        len(SILOS),
    }
