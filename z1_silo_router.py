s"""
z1_silo_router.py

Deterministic keyword-based silo router.
No model required. Python only.

Silos:
    core_runtime     -- system internals, governance, ledger, auditor
    work_product     -- grants, complaints, legal, professional output
    technical_builds -- code, models, hardware, evals, RunPod
    life_admin       -- personal, family, financial, medical, HOA
    other            -- catch-all for unclassifiable content

Usage:
    from z1_silo_router import route_to_silo
    silo = route_to_silo("the auditor hit 82 percent zero shot")
    # -> "technical_builds"
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple


SILO_MANIFESTS: Dict[str, List[str]] = {
    "core_runtime": [
        "ledger", "dam", "reservoir", "reflect", "evolve", "z1", "auditor",
        "dump.txt", "z1", "bridge", "tarpit", "silo", "governance",
        "context packet", "compression", "journal", "log_event", "open_reservoir",
        "z1_core", "z1_dam", "action_guard", "reservoir_gate",
        "runtime_memory", "persist", "continuity", "provenance", "receipt_id",
        "ledger_ok", "ledger_failure", "stop_for_clarity", "block_destructive",
        "ingest_dump", "reflect_evolve", "kernel", "packet", "session_event",
    ],
    "technical_builds": [
        "ollama", "runpod", "lora", "vram", "gpu", "token", "model", "inference",
        "python", "fastapi", "eval", "dataset", "training", "fine-tune", "finetune",
        "accuracy", "benchmark", "gguf", "quantize", "llama", "qwen", "phi",
        "rdna", "rx9070", "cuda", "rocm", "unsloth", "trl", "transformers",
        "jsonl", "checkpoint", "epoch", "batch", "learning rate", "loss",
        "conflict_detection", "stale_context", "action_gate", "tarpit_detection",
        "zero-shot", "zero shot", "classification", "binary", "verdict",
        "z1_auditor", "dam_eval", "dam eval", "action_gate_eval", "action gate eval", "reflect_eval",
        "action gate", "deterministic stage", "action_gate_eval_results",
        "z1_bridge", "sys.path", "import", "def ", "class ", "dataclass",
    ],
    "work_product": [
        "sbir", "grant", "complaint", "cftc", "azdes", "snap", "3cloud",
        "gumroad", "proposal", "application", "resume", "pitch", "funding",
        "whistleblower", "regulatory", "federal", "agency", "filing",
        "wage theft", "tip theft", "fraud", "lawsuit", "legal", "court",
        "writ", "writ of mandamus", "motion", "exhibit", "evidence", "subpoena",
        "nsf", "reach grant", "maricopa", "chandler-gilbert",
        "senior ai consultant", "job application", "cover letter",
    ],
    "life_admin": [
        "melanie", "jessica", "irs", "tas", "refund", "tax", "dependent",
        "hoa", "treasurer", "management company", "dues", "ballot",
        "chime", "paypal", "google pay", "prepaid", "card", "billing",
        "mortgage", "foreclosure", "rent", "benihana", "chicago", "flight",
        "rheumatoid", "arthritis", "prescription", "doctor", "medical",
        "school", "teacher", "grade", "graduation", "powerschool",
        "charter", "legacy traditional", "triebel", "vertex education",
        "capital one", "motion to set aside", "credit", "debt", "custody", "quitclaim", "deed", "leoma",
        "hot tub", "pool", "muriatic", "chlorine", "dog", "lemon tree",
    ],
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def score_text(text: str) -> Dict[str, int]:
    lower = _normalize(text)
    scores: Dict[str, int] = {silo: 0 for silo in SILO_MANIFESTS}
    for silo, terms in SILO_MANIFESTS.items():
        for term in terms:
            if term in lower:
                scores[silo] += 1
    return scores


def route_to_silo(text: str) -> str:
    scores = score_text(text)
    best_silo = max(scores, key=lambda s: scores[s])
    if scores[best_silo] == 0:
        return "other"
    return best_silo


def route_with_scores(text: str) -> Tuple[str, Dict[str, int]]:
    scores = score_text(text)
    best_silo = max(scores, key=lambda s: scores[s])
    if scores[best_silo] == 0:
        best_silo = "other"
    return best_silo, scores


def route_and_write(
    text: str,
    *,
    source: str = "unknown",
    base: Optional[Path] = None,
) -> str:
    silo = route_to_silo(text)
    if base is not None:
        silo_dir = Path(base) / silo
        silo_dir.mkdir(parents=True, exist_ok=True)
        dump = silo_dir / "dump.txt"
        with dump.open("a", encoding="utf-8") as f:
            f.write(f"\n<|im_start|>{source}\n{text}\n<|im_end|>\n")
    return silo


def load_context_for_mode(mode: str, *, base: Optional[Path] = None) -> str:
    if base is None:
        return ""
    dump = Path(base) / mode / "dump.txt"
    if not dump.exists():
        return ""
    content = dump.read_text(encoding="utf-8", errors="replace")
    return content[-2000:] if len(content) > 2000 else content
