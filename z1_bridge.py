"""
z1_bridge
FastAPI server bridging the z1 runtime to the Anthropic API.

Role:
    HTTP interface for prompt/response flow.
    Routes content to correct silo via keyword router.
    Injects relevant silo context into model prompt.
    Surfaces audit flags to human. Never acts on them autonomously.

Phase 2 changes (this version):
    /gate endpoint now pulls silo context from z1_silo_manifest via
    gate_context_from_silo() and passes it into guard.classify().
    Router determines silo. Python enforces silo rules. No inference called.

Phase 3 (not yet released):
    z1_audit_coordinator — silo-level auditor integration.
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
from z1_reflect_evolve_log_compress import reflect, evolve, log_event
from z1_silo_router import route_and_write, route_to_silo, load_context_for_mode

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
LIB_PATH = os.environ.get("z1_LIB_PATH", os.path.dirname(os.path.abspath(__file__)))
SILO_BASE = Path(os.environ.get("z1_SILO_PATH", os.path.join(LIB_PATH, "silos")))
MODE = os.environ.get("z1_MODE", "default")

# ---------------------------------------------------------------------------
# Anthropic client
# ---------------------------------------------------------------------------

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a runtime governance assistant operating within the z1 stack.
Be direct and accurate. Do not invent continuity.
Verify before claiming. Stop before guessing. Ask before acting on ambiguous instructions.
Destructive or irreversible actions require explicit confirmation before execution."""

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="z1_Bridge")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://www.z1governs.com", "https://z1governs.com"],
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
    full_prompt = (
        f"LATEST REFLECTION: {reflection_context}\n\n"
        f"{silo_context}\n\n"
        f"User: {user_prompt}"
    ).strip()

    try:
        message = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
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
    try:
        files = [f for f in os.listdir(LIB_PATH) if f.endswith(".py")]
    except Exception:
        files = []
    return {
        "status": "ONLINE",
        "system": "z1",
        "model": ANTHROPIC_MODEL,
        "auditor_status": "PHASE_3_PENDING",
        "mode": MODE,
        "silo_base": str(SILO_BASE),
        "files_loaded": files,
    }


@app.post("/gate")
async def gate_endpoint(request: GateRequest):
    """
    Deterministic gate check with silo context.

    Flow:
      1. Router determines silo from instruction text (or uses caller-supplied silo_id).
      2. gate_context_from_silo() pulls the GateRuleset for that silo.
      3. ActionGuard.classify() enforces silo rules + five governance rules.
      4. z1Dam.inspect_request() arbitrates signals and returns final verdict.

    No inference is called. This is pure Python governance.
    """
    instruction = request.instruction

    # 1. Determine silo
    silo_id = request.silo_id or route_to_silo(instruction)

    # 2. Pull silo gate context from manifest
    silo_ctx = gate_context_from_silo(silo_id)

    # 3. Run dam
    dam_result = dam.inspect_request(
        instruction,
        confirmation=request.confirmation,
        silo_id=silo_id,
    )

    # 4. Run guard with silo context
    guard_result = guard.classify(
        instruction,
        confirmation=request.confirmation,
        silo_context=silo_ctx,
    )

    # 5. Guard result takes precedence on hard stops; otherwise use dam verdict
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
    """Phase 3 stub."""
    return {"flag_count": 0, "flags": [], "status": "PHASE_3_PENDING"}


@app.get("/audit/tarpit")
async def tarpit_status():
    """Phase 3 stub."""
    return {"tarpit_status": {}, "status": "PHASE_3_PENDING"}


@app.post("/audit/release/{silo}")
async def release_tarpit(silo: str, confirmed_by: str = "human"):
    """Phase 3 stub."""
    return {"released": None, "status": "PHASE_3_PENDING"}


@app.get("/ls")
async def list_repo():
    try:
        files = os.listdir(LIB_PATH)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"directory": LIB_PATH, "files": files}


@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    mode = request.mode or MODE

    # 1. Reflect before answering
    current_state = reflect()
    reflections = current_state.get("reflections", [])
    latest_reflection = reflections[-1]["summary"] if reflections else ""

    # 2. Route incoming prompt to correct silo
    routed_silo = route_and_write(
        request.prompt,
        source="user",
        base=SILO_BASE,
    )

    # 3. Load relevant silo context for prompt injection
    silo_context = load_context_for_mode(mode, base=SILO_BASE)

    # 4. Run inference
    response_text = run_inference(
        request.prompt,
        reflection_context=latest_reflection,
        silo_context=silo_context,
    )

    # 5. Route response to silo
    route_and_write(response_text, source="assistant", base=SILO_BASE)

    # 6. Log event and evolve
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
