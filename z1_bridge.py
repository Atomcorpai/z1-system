"""
z1_bridge.py
FastAPI server bridging the z1 runtime to the Anthropic API.

Role:
    HTTP interface for prompt/response flow.
    Routes content to correct silo via keyword router.
    Injects relevant silo context into model prompt.
    Surfaces audit flags to human. Never acts on them autonomously.

Phase 2 changes (this version):
    /gate endpoint now pulls silo context from gumbo_silo_manifest via
    gate_context_from_silo() and passes it into guard.classify().
    Router determines silo. Python enforces silo rules. No inference called.

Phase 3 (not yet released):
    rmpl_audit_coordinator.py — silo-level auditor integration.
"""

import os
import anthropic
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pathlib import Path

from z1_action_guard import ActionGuard, ActionDecision, gate_context_from_silo
from z1_dam import z1Dam, DamDecision
from reflect_evolve_log_compress import reflect, evolve, log_event
from rmpl_silo_router import route_and_write, route_to_silo, load_context_for_mode
from z1_silo_operational import load_silo1

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
AUDITOR_MODEL = os.environ.get("AUDITOR_MODEL", "claude-haiku-4-5-20251001")
LIB_PATH = os.environ.get("z1_LIB_PATH", os.path.dirname(os.path.abspath(__file__)))
SILO_BASE = Path(os.environ.get("RMPL_SILO_PATH", os.path.join(LIB_PATH, "silos")))
MODE = os.environ.get("RMPL_MODE", "default")

# ---------------------------------------------------------------------------
# Anthropic client
# ---------------------------------------------------------------------------

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# ---------------------------------------------------------------------------
# Silo 1 boot preamble — loaded once at startup, refreshed per request
# ---------------------------------------------------------------------------

def get_silo1_preamble() -> str:
    result = load_silo1()
    if not result.ok:
        return f"SILO_1_OPERATIONAL: UNAVAILABLE — {result.error}"
    return result.to_preamble()

# ---------------------------------------------------------------------------
# Base system prompt — static rules, no identity (identity comes from Silo 1)
# ---------------------------------------------------------------------------

BASE_SYSTEM_PROMPT = """You are operating within the Z1 runtime.

Governance is handled by Python. You do not police yourself.
Be direct. Do not hedge. Do not invent continuity.
Verify before claiming. Stop before guessing.
Destructive or irreversible actions require explicit confirmation.
The operator is the owner of this system. The owner is not the threat model."""

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="z1 Bridge")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://www.z1governs.com",
        "https://z1governs.com",
        "https://atomcorp.ai",
        "https://www.atomcorp.ai",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

dam = z1Dam()
guard = ActionGuard()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    prompt: str
    context: str = ""
    mode: str = MODE


class GateRequest(BaseModel):
    instruction: str
    confirmation: bool = False
    silo_id: str | None = None


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def run_inference(user_prompt: str, reflection_context: str = "", silo_context: str = "") -> str:
    silo1_preamble = get_silo1_preamble()

    system_prompt = f"{silo1_preamble}\n\n{BASE_SYSTEM_PROMPT}"

    parts = []
    if reflection_context:
        parts.append(f"LATEST REFLECTION: {reflection_context}")
    if silo_context:
        parts.append(silo_context)
    parts.append(f"User: {user_prompt}")

    full_prompt = "\n\n".join(parts)

    try:
        message = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": full_prompt}],
        )
        return message.content[0].text
    except Exception as e:
        return f"INFERENCE_ERROR: {str(e)}"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/status")
async def status():
    silo1 = load_silo1()
    try:
        files = [f for f in os.listdir(LIB_PATH) if f.endswith(".py")]
    except Exception:
        files = []
    return {
        "status": "ONLINE",
        "system": "z1",
        "model": ANTHROPIC_MODEL,
        "auditor_model": AUDITOR_MODEL,
        "auditor_status": "PHASE_3_PENDING",
        "mode": MODE,
        "silo_base": str(SILO_BASE),
        "files_loaded": files,
        "silo1_operational": {
            "ok": silo1.ok,
            "entry_count": len(silo1.entries),
            "error": silo1.error,
        },
    }


@app.post("/gate")
async def gate_endpoint(request: GateRequest):
    instruction = request.instruction

    silo_id = request.silo_id or route_to_silo(instruction)
    silo_ctx = gate_context_from_silo(silo_id)

    dam_result = dam.inspect_request(
        instruction,
        confirmation=request.confirmation,
        silo_id=silo_id,
    )

    guard_result = guard.classify(
        instruction,
        confirmation=request.confirmation,
        silo_context=silo_ctx,
    )

    if guard_result.silo_hard_stop:
        verdict = "BLOCK"
        reason = guard_result.reason
        triggered_rules = [r.value for r in guard_result.triggered_rules]
    else:
        decision_map = {
            DamDecision.ALLOW: "ALLOW",
            DamDecision.STOP_FOR_CLARITY: "STOP_FOR_CLARITY",
            DamDecision.BLOCK_DESTRUCTIVE: "BLOCK",
            DamDecision.LEDGER_FAILURE: "BLOCK",
            DamDecision.LEDGER_CONFLICT: "BLOCK",
            DamDecision.RESERVOIR_AUTH_REQUIRED: "BLOCK",
        }
        verdict = decision_map.get(dam_result.decision, "BLOCK")
        reasons = [s.reason for s in dam_result.silo_signals if s.verdict != "ALLOW"]
        reason = reasons[0] if reasons else dam_result.reason
        triggered_rules = [r.value for r in guard_result.triggered_rules]

    return {
        "verdict": verdict,
        "reason": reason,
        "silo_id": silo_id,
        "silo_gate_active": silo_ctx.get("system_gate_active", True),
        "triggered_rules": triggered_rules,
        "risk_level": guard_result.risk_level,
        "decision": dam_result.decision.value,
        "required_next_step": dam_result.required_next_step,
        "assumptions": dam_result.assumptions,
        "silo_signals": [
            {
                "silo_id": s.silo_id,
                "verdict": s.verdict,
                "confidence": s.confidence,
                "reason": s.reason,
            }
            for s in dam_result.silo_signals
        ],
    }


@app.get("/audit/flags")
async def get_flags():
    return {"flag_count": 0, "flags": [], "status": "PHASE_3_PENDING"}


@app.get("/audit/tarpit")
async def tarpit_status():
    return {"tarpit_status": {}, "status": "PHASE_3_PENDING"}


@app.post("/audit/release/{silo}")
async def release_tarpit(silo: str, confirmed_by: str = "human"):
    return {"released": None, "status": "PHASE_3_PENDING"}


@app.get("/ls")
async def list_repo():
    try:
        files = os.listdir(LIB_PATH)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"directory": LIB_PATH, "files": files}


@app.get("/silo1")
async def silo1_endpoint():
    """Debug endpoint — returns the current Silo 1 preamble as the model sees it."""
    result = load_silo1()
    return {
        "ok": result.ok,
        "entry_count": len(result.entries),
        "preamble": result.to_preamble(),
        "error": result.error,
    }


@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    mode = request.mode or MODE

    current_state = reflect()
    reflections = current_state.get("reflections", [])
    latest_reflection = reflections[-1]["summary"] if reflections else ""

    routed_silo = route_and_write(
        request.prompt,
        source="user",
        base=SILO_BASE,
    )

    silo_context = load_context_for_mode(mode, base=SILO_BASE)

    response_text = run_inference(
        request.prompt,
        reflection_context=latest_reflection,
        silo_context=silo_context,
    )

    route_and_write(response_text, source="assistant", base=SILO_BASE)

    log_event(request.prompt, kind="user_input", mode=mode)
    evolve(trigger=f"Input: {request.prompt[:60]}")

    return {
        "response": response_text,
        "routed_to": routed_silo,
        "audit": {
            "status": "PHASE_3_PENDING",
            "flag_count": 0,
            "flags": [],
            "tarpit_active": False,
        },
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
