"""
z1_launcher.py

Boot verifier for the z1 governance stack.
Runs before uvicorn starts. Fails loud if anything is broken.
Railway pulls this from GitHub and runs it cold — so every check
that can fail silently in production gets caught here instead.

Exit codes:
    0 — all checks passed, safe to start server
    1 — one or more checks failed, do not start

Usage (Procfile or Railway start command):
    python z1_launcher.py && uvicorn z1_bridge:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timezone

from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timezone
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ok(label: str) -> None:
    print(f"  [PASS] {label}")


def fail(label: str, detail: str = "") -> None:
    print(f"  [FAIL] {label}")
    if detail:
        print(f"         {detail}")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_imports() -> bool:
    passed = True
    pairs = [
        ("z1_action_guard",    "ActionGuard, GovernanceRule, gate_context_from_silo"),
        ("z1_dam",             "z1Dam, DamDecision"),
        ("z1_reservoir_gate",  "ReservoirGate, ReservoirDecision"),
        ("z1_silo_manifest",   "get_silo, get_gate_ruleset, SILOS"),
        ("z1_reflect_evolve_log_compress", "reflect, evolve, log_event"),
    ]
    for module, names in pairs:
        try:
            mod = __import__(module)
            for name in [n.strip() for n in names.split(",")]:
                if not hasattr(mod, name):
                    fail(f"{module}.{name}", "attribute missing after import")
                    passed = False
            ok(f"import {module}")
        except ImportError as e:
            fail(f"import {module}", str(e))
            passed = False
        except Exception as e:
            fail(f"import {module}", traceback.format_exc(limit=1).strip())
            passed = False
    return passed


def check_manifest() -> bool:
    passed = True
    try:
        from z1_silo_manifest import get_gate_ruleset, get_silo, SILOS

        # Four invariant context silos must always be present
        kernel_silos = ["OPERATIONAL", "RELATIONAL", "DOMAIN", "SITUATIONAL"]
        for silo_id in kernel_silos:
            if silo_id not in SILOS:
                fail(f"kernel silo {silo_id}", "not found in SILOS dict")
                passed = False
            else:
                ok(f"kernel silo {silo_id}")

        # All kernel silos must have system_gate_active=True
        for silo_id in kernel_silos:
            ruleset = get_gate_ruleset(silo_id)
            if ruleset is None:
                fail(f"get_gate_ruleset('{silo_id}')", "returned None")
                passed = False
            elif not ruleset.system_gate_active:
                fail(f"{silo_id} gate", "system_gate_active=False — kernel silos must have gate active")
                passed = False
            else:
                ok(f"get_gate_ruleset('{silo_id}') — system_gate_active confirmed")

        # SITUATIONAL must have decay_on_close and home_silo_candidates
        situational = get_silo("SITUATIONAL")
        if situational and not situational.decay_on_close:
            fail("SITUATIONAL decay_on_close", "False — must be True")
            passed = False
        elif situational and not situational.home_silo_candidates:
            fail("SITUATIONAL home_silo_candidates", "empty — demote needs a destination")
            passed = False
        else:
            ok(f"SITUATIONAL decay config — home candidates: {situational.home_silo_candidates}")

        # No kernel silo should be marked as a wall
        for silo_id in kernel_silos:
            silo = get_silo(silo_id)
            if silo and silo.is_wall():
                fail(f"{silo_id} wall check", "kernel silo marked is_wall=True — walls are tenant-only")
                passed = False
            else:
                ok(f"{silo_id} is not a wall (correct)")

        # Hard stops must be non-empty on every kernel silo
        for silo_id in kernel_silos:
            ruleset = get_gate_ruleset(silo_id)
            if ruleset and not ruleset.hard_stops:
                fail(f"{silo_id} hard_stops", "empty — every silo must have hard stops")
                passed = False
            else:
                ok(f"{silo_id} hard_stops — {len(ruleset.hard_stops)} loaded")

    except Exception as e:
        fail("manifest check", traceback.format_exc(limit=2).strip())
        passed = False

    return passed


def check_gate_context_wiring() -> bool:
    """
    Confirm gate_context_from_silo() returns real silo data from z1_silo_manifest.
    Empty dict means silo hard stops are silently skipped in production.
    """
    try:
        from z1_action_guard import gate_context_from_silo

        ctx = gate_context_from_silo("OPERATIONAL")
        if not ctx:
            fail("gate_context_from_silo('OPERATIONAL')",
                 "returned empty dict — import or wiring failure")
            return False
        if "system_gate_active" not in ctx:
            fail("gate_context_from_silo('OPERATIONAL')",
                 "missing system_gate_active key")
            return False
        ok("gate_context_from_silo wiring — silo context loads into guard correctly")
        return True
    except Exception as e:
        fail("gate_context_from_silo", traceback.format_exc(limit=2).strip())
        return False


def check_dam() -> bool:
    try:
        from z1_dam import z1Dam, DamDecision
        dam = z1Dam()

        # Safe request — should ALLOW
        result = dam.inspect_request("summarize the current ledger state")
        if result.decision not in {DamDecision.ALLOW, DamDecision.STOP_FOR_CLARITY}:
            fail("dam safe request", f"expected ALLOW, got {result.decision}")
            return False
        ok(f"dam safe request — {result.decision.value}")

        # Destructive without confirmation — should BLOCK
        result = dam.inspect_request("delete all the log files", confirmation=False)
        if result.decision == DamDecision.ALLOW:
            fail("dam destructive request", "ALLOW without confirmation — gate not enforcing")
            return False
        ok(f"dam destructive request — {result.decision.value} (correct)")

        return True
    except Exception as e:
        fail("dam check", traceback.format_exc(limit=2).strip())
        return False


def check_action_guard_with_silo() -> bool:
    """
    End-to-end: guard classifies requests using live silo context from z1_silo_manifest.
    Full chain — manifest -> gate_context -> guard -> block/allow.
    """
    try:
        from z1_action_guard import ActionGuard, ActionDecision, gate_context_from_silo

        guard = ActionGuard()

        # OPERATIONAL — destructive without confirmation must block
        ctx = gate_context_from_silo("OPERATIONAL")
        result = guard.classify(
            "delete all the audit logs",
            confirmation=False,
            silo_context=ctx,
        )
        if result.decision == ActionDecision.ALLOW:
            fail("guard + OPERATIONAL silo", "ALLOW on destructive without confirmation")
            return False
        ok(f"guard + OPERATIONAL silo — {result.decision.value} (correct)")

        # DOMAIN — read/analysis request should ALLOW (no external action)
        ctx = gate_context_from_silo("DOMAIN")
        result = guard.classify(
            "summarize what we know about the governance architecture",
            confirmation=False,
            silo_context=ctx,
        )
        if result.decision != ActionDecision.ALLOW:
            fail("guard + DOMAIN silo", f"expected ALLOW on read request, got {result.decision}")
            return False
        ok(f"guard + DOMAIN silo — {result.decision.value} (correct)")

        # SITUATIONAL — external action without confirmation must block
        ctx = gate_context_from_silo("SITUATIONAL")
        result = guard.classify(
            "send the status update to the client",
            confirmation=False,
            silo_context=ctx,
        )
        if result.decision == ActionDecision.ALLOW:
            fail("guard + SITUATIONAL silo", "ALLOW on external action without confirmation")
            return False
        ok(f"guard + SITUATIONAL silo — {result.decision.value} (correct)")

        return True
    except Exception as e:
        fail("guard + silo context check", traceback.format_exc(limit=2).strip())
        return False


# ---------------------------------------------------------------------------
# Boot receipt
# ---------------------------------------------------------------------------

def write_boot_receipt(results: dict) -> None:
    receipt = {
        "booted_at": utc_now(),
        "all_passed": all(results.values()),
        "checks": results,
    }
    try:
        with open("boot_receipt.json", "w", encoding="utf-8") as f:
            json.dump(receipt, f, indent=2)
        print(f"\n  Boot receipt written to boot_receipt.json")
    except Exception as e:
        print(f"\n  Could not write boot receipt: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    import os
    print("FILES IN /app:", os.listdir("/app"))
    print("SYS.PATH:", sys.path)
    
def main() -> None:
    print(f"\nz1 BOOT CHECK — {utc_now()}")
    print("=" * 50)

    results = {}

    print("\n[1] Import resolution")
    results["imports"] = check_imports()

    print("\n[2] Silo manifest — four kernel silos")
    results["manifest"] = check_manifest()

    print("\n[3] Gate context wiring")
    results["gate_context_wiring"] = check_gate_context_wiring()

    print("\n[4] Dam instantiation and classification")
    results["dam"] = check_dam()

    print("\n[5] Guard + silo context end-to-end")
    results["guard_silo_e2e"] = check_action_guard_with_silo()

    print("\n" + "=" * 50)
    all_passed = all(results.values())

    write_boot_receipt(results)

    if all_passed:
        print("  BOOT CHECK: PASSED — stack is ready\n")
        sys.exit(0)
    else:
        failed = [k for k, v in results.items() if not v]
        print(f"  BOOT CHECK: FAILED — {failed}")
        print("  Do not start the server with a broken stack.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
