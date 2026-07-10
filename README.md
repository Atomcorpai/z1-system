system infrastructure for agentic AI systems.

Z1 is a Python middleware stack that intercepts, classifies, and governs every action and memory operation in an agentic AI pipeline. It is model-agnostic and tool-agnostic. Any model plugged into the stack inherits the same system behavior. Any tool added to the stack is automatically subject to auditor oversight — not just memory, but every tool.
The Problem

Agentic AI systems fail in predictable ways:

    They execute destructive actions from ambiguous instructions ("clean up everything" → deletes production data)
    They accumulate contradictory memory with no mechanism to detect or resolve conflicts
    They hallucinate continuity — inventing prior context that doesn't exist when sessions reset
    There is no standard system layer; every deployment rolls its own ad hoc safeguards

Z1 is a reusable answer to all four.
Architecture

The stack is four layers deep, each with a specific job:

Incoming instruction
        │
        ▼
  [_z1_dam ]           ← Is this action allowed? ALLOW / BLOCK_DESTRUCTIVE / STOP_FOR_CLARITY
        │
        ▼
  [ z1_action_guard ]  ← Deterministic pre-execution check. Confirms scope before anything runs.
        │
        ▼
  [ z1_reservoir_gate ]← Controls what context enters the model's active window
        │
        ▼
  [ z1_silo_router ]    ← Routes memory to the correct domain silo. Deterministic keyword matching.
        │
        ▼
  [ z1_core / ledger ]  ← Persists classified, timestamped, conflict-checked memory
        │
        ▼
  [ z1_reflect_evolve_log_compress ] ← Compresses session state into portable context packets
        │
        ▼
  [ z1_Auditor model ]       ← Binary classifier (3B LLM). Called only for genuine ambiguity.
                            Conflict / No Conflict. Stale / Current. Valid / Invalid.

The deterministic layers handle routing, classification, and gating. The auditor model handles only binary judgment at the boundary. It is not the brains of the operation — it is the last checkpoint before a decision that the Python layer cannot make deterministically.
Validation Results

All components validated locally on consumer AMD hardware (ASUS Prime RX 9070 OC, 16GB VRAM).
Component 	Test Cases 	Accuracy 	Method
Dam layer (z1_dam) 	28 	100% 	Deterministic Python
Action gate (z1_action_guard) 	31 	100% 	Deterministic Python
Silo routing (z1_silo_router) 	45 	100% 	Deterministic Python
Context portability 	10 critical facts 	100% 	End-to-end pipeline
Auditor model (llama3.2:3b, zero-shot) 	200 	82.3% 	Ollama local inference
Auditor model (Qwen LoRA fine-tuned) 	200 	90.5% 	Local GGUF via Ollama

Token budget overhead: 5.3% worst case at 8B context. The system does not materially constrain model capacity.

Tarpit detection was removed from auditor scope by design. Loop detection is handled deterministically in Python; calling a model for it introduced unnecessary latency and underperformed against a simple counter.
Files

Core system (9 files):
File 	Role
z1_core.py 	Ledger management, memory persistence, provenance tracking
z1_dam.py 	First-layer action gate: ALLOW / BLOCK_DESTRUCTIVE / STOP_FOR_CLARITY
z1_action_guard.py 	Pre-execution scope confirmation
z1_reservoir_gate.py 	Context window ingress control
z1_bridge.py 	Model interface layer, system prompt enforcement
z1_reflect_evolve_log_compress.py 	Session compression and portable context packet generation
z1_action_gate.py 	Action classification and routing
z1_silo_router.py 	Deterministic domain-based memory routing
z1_runtime_memory_persistence_ledger.json 	Persistent ledger store

Reference files:

    runtime_beacon.txt — system invariants and auditor contract (loaded at session start)
    session_seed.json — session initialization state

Key Design Decisions

Deterministic over probabilistic where possible. Routing, gating, and loop detection are Python. The model is reserved for genuine boundary cases that require judgment, not pattern matching.

The auditor governs any tool added to the stack. This is the differentiator from memory-only system systems. Adding a new tool to an z1-system pipeline does not require writing new system logic — the auditor already covers it.

No mythology. Prior versions of this codebase accumulated lore-based naming ("sovereign," "vessel," "architect") that created conceptual drift and made the system harder to reason about. The current stack uses plain descriptive names. The architecture is the architecture; it doesn't need a story.

Portable context. Compressed session packets can be ingested by a new session, restoring verified context without requiring the full conversation history. This is the memory continuity layer — designed specifically for use cases where session resets are frequent or unavoidable.
Status

Phase 1 complete. All deterministic layers validated. Auditor model validated at 90.5% zero-shot accuracy on consumer hardware.

Phase 2 in progress: auditor fine-tuning pipeline (RunPod LoRA), z1_audit_coordinator.py bridge integration, Ubuntu 26.04 / ROCm dual-GPU build for expanded local inference capacity.

NSF SBIR submission: confirmation 00114188.
License

Proprietary. All rights reserved. AtomCorp, 2026.# z1-system
