"""
z1_reflect_evolve_log_compress.py

Clean RMPL/Z1 runtime utility for reflection, evolution markers,
durable event logging, and lossy compression of recent runtime text.

Design goals:
- One active logic file instead of split reflection/memory-loop drift.
- No required external z1 imports.
- Deterministic file-backed defaults.
- Safe enough to run locally with plain Python.
- Adapter-friendly: pass custom memory/journal objects if desired.

This module does NOT try to create identity, consciousness, or magic memory.
It maintains practical runtime continuity artifacts:
- journal log
- state JSON
- reflection summaries
- compressed active packets
- stale/duplicate trimming
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
from typing import Any, Iterable, Optional


# -----------------------------
# Defaults
# -----------------------------

DEFAULT_STATE_FILE = "runtime_state.json"
DEFAULT_JOURNAL_FILE = "runtime_journal.log"
DEFAULT_PACKET_FILE = "active_context_packet.json"
DEFAULT_DUMP_FILE = "dump.txt"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?){2}\d{4}\b")
WS_RE = re.compile(r"\s+")
TAG_RE = re.compile(r"#\w+|\[(\w+)\]")
ASSISTANT_BLOCK_RE = re.compile(r"<\|im_start\|>assistant\n(.*?)<\|im_end\|>", re.DOTALL)

STOPWORDS = {
    "about", "after", "again", "being", "because", "before", "could", "doing",
    "every", "from", "have", "into", "just", "like", "more", "need", "only",
    "other", "really", "should", "still", "that", "their", "there", "these",
    "thing", "this", "those", "through", "what", "when", "where", "which",
    "while", "would", "with", "without", "youre", "youve", "your", "ours",
}


# -----------------------------
# Data records
# -----------------------------

@dataclass
class EventRecord:
    at: str
    kind: str
    text: str
    tags: list[str]
    mode: str
    source: str
    digest: str


@dataclass
class CompressionPacket:
    at: str
    mode: str
    source_count: int
    source_digests: list[str]
    summary: str
    kernels: list[str]
    tags: list[str]
    confidence: float


# -----------------------------
# Time / normalization helpers
# -----------------------------


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_text(text: str) -> str:
    return WS_RE.sub(" ", str(text or "")).strip()


def redact(text: str) -> str:
    text = EMAIL_RE.sub("[redacted@email]", text or "")
    text = PHONE_RE.sub("[redacted phone]", text)
    return text


def digest_text(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()[:16]


def dedupe_keep_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        value = normalize_text(item)
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def extract_tags(lines: Iterable[str]) -> list[str]:
    tags: set[str] = set()
    for line in lines:
        for raw in re.findall(r"#\w+|\[\w+\]", line or ""):
            tag = raw.strip("[]").lower()
            if not tag.startswith("#"):
                tag = f"#{tag}"
            tags.add(tag)
    return sorted(tags)


# -----------------------------
# File-backed state/journal
# -----------------------------


def read_json(path: str | Path, default: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return dict(default or {})
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # Do not destroy corrupted state. Quarantine by returning a minimal state.
        return {
            "state_error": "json_decode_failed",
            "state_file": str(p),
            "quarantined_at": iso_now(),
        }


def write_json(path: str | Path, data: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def append_line(path: str | Path, line: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(line.rstrip() + "\n")


def list_recent_lines(path: str | Path, limit: int = 50) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-limit:]


# -----------------------------
# Compression logic
# -----------------------------


def extract_kernels(text: str, *, max_terms: int = 40) -> list[str]:
    """
    Lightweight deterministic kernel extraction.
    Keeps capitalized concepts, snake/camel-ish tokens, hashtags, and meaningful long words.
    This is intentionally boring and explainable, not semantic magic.
    """
    clean = redact(normalize_text(text))
    candidates = re.findall(r"#\w+|\b[A-Z][A-Za-z0-9_/-]{2,}\b|\b[a-z][a-z0-9_/-]{4,}\b", clean)
    out: list[str] = []
    seen: set[str] = set()
    for token in candidates:
        key = token.lower().strip(".,:;!?()[]{}\"'")
        if not key or key in STOPWORDS or len(key) < 3:
            continue
        if key not in seen:
            seen.add(key)
            out.append(token.strip(".,:;!?()[]{}\"'"))
        if len(out) >= max_terms:
            break
    return out


def summarize_entries(entries: Iterable[str], *, max_chars: int = 700, take: int = 12) -> str:
    recent = list(entries)[-take:]
    recent = [redact(normalize_text(e)) for e in recent]
    recent = dedupe_keep_order(recent)
    joined = " | ".join(recent)
    if len(joined) <= max_chars:
        return joined
    return joined[: max_chars - 1].rstrip() + "…"


def confidence_score(entries: list[str], kernels: list[str]) -> float:
    """
    Simple operational confidence, not truth confidence.
    Measures whether there is enough non-duplicate material to form an active packet.
    """
    if not entries:
        return 0.0
    unique_entries = len(dedupe_keep_order(entries))
    kernel_factor = min(len(kernels) / 20, 1.0)
    volume_factor = min(unique_entries / 6, 1.0)
    return round((0.35 + 0.35 * volume_factor + 0.30 * kernel_factor), 2)



def _extract_text_from_entry(entry: str) -> str:
    """Extract just the text field if entry is a JSON log record, otherwise return as-is."""
    try:
        parsed = json.loads(entry)
        if isinstance(parsed, dict) and "text" in parsed:
            return parsed["text"]
    except Exception:
        pass
    return entry


def compress_entries(
    entries: Iterable[str],
    *,
    mode: str = "default",
    max_summary_chars: int = 700,
    max_kernels: int = 48,
    take: int = 12,
) -> CompressionPacket:
    selected = [normalize_text(_extract_text_from_entry(e)) for e in list(entries)[-take:] if normalize_text(e)]
    summary = summarize_entries(selected, max_chars=max_summary_chars, take=take)
    all_text = "\n".join(selected)
    kernels = extract_kernels(all_text, max_terms=max_kernels)
    tags = extract_tags(selected)
    digests = [digest_text(e) for e in selected]
    return CompressionPacket(
        at=iso_now(),
        mode=mode,
        source_count=len(selected),
        source_digests=digests,
        summary=summary,
        kernels=kernels,
        tags=tags,
        confidence=confidence_score(selected, kernels),
    )


# -----------------------------
# Logging / reflection / evolution
# -----------------------------


def log_event(
    text: str,
    *,
    kind: str = "event",
    mode: str = "default",
    source: str = "manual",
    journal_file: str | Path = DEFAULT_JOURNAL_FILE,
    redact_text: bool = True,
) -> EventRecord:
    body = redact(text) if redact_text else normalize_text(text)
    body = normalize_text(body)
    tags = extract_tags([body])
    record = EventRecord(
        at=iso_now(),
        kind=kind,
        text=body,
        tags=tags,
        mode=mode,
        source=source,
        digest=digest_text(body),
    )
    append_line(journal_file, json.dumps(asdict(record), sort_keys=True))
    return record


def reflect(
    *,
    state_file: str | Path = DEFAULT_STATE_FILE,
    journal_file: str | Path = DEFAULT_JOURNAL_FILE,
    packet_file: str | Path = DEFAULT_PACKET_FILE,
    mode: str = "default",
    take: int = 20,
    keep_reflections: int = 200,
) -> dict[str, Any]:
    state = read_json(state_file, default={"reflections": [], "evolutions": [], "packets": []})
    recent = list_recent_lines(journal_file, limit=max(take, 20))
    packet = compress_entries(recent, mode=mode, take=take)

    reflection = {
        "at": packet.at,
        "mode": mode,
        "summary": packet.summary,
        "kernels": packet.kernels,
        "tags": packet.tags,
        "confidence": packet.confidence,
        "source_count": packet.source_count,
    }

    state.setdefault("reflections", [])
    state.setdefault("packets", [])
    state["last_reflection"] = packet.at
    state["last_mode"] = mode
    state["last_tags"] = packet.tags
    state["last_confidence"] = packet.confidence
    state["reflections"].append(reflection)
    state["packets"].append(asdict(packet))

    state["reflections"] = state["reflections"][-keep_reflections:]
    state["packets"] = state["packets"][-keep_reflections:]

    write_json(packet_file, asdict(packet))
    write_json(state_file, state)
    append_line(journal_file, json.dumps(asdict(EventRecord(
        at=iso_now(),
        kind="reflect",
        text=packet.summary,
        tags=packet.tags,
        mode=mode,
        source="reflect_evolve_log_compress",
        digest=digest_text(packet.summary),
    )), sort_keys=True))
    return state


def evolve(
    trigger: str,
    *,
    state_file: str | Path = DEFAULT_STATE_FILE,
    journal_file: str | Path = DEFAULT_JOURNAL_FILE,
    packet_file: str | Path = DEFAULT_PACKET_FILE,
    mode: str = "default",
) -> dict[str, Any]:
    log_event(trigger, kind="evolve_trigger", mode=mode, source="manual", journal_file=journal_file)
    state = reflect(state_file=state_file, journal_file=journal_file, packet_file=packet_file, mode=mode)
    state.setdefault("evolutions", [])
    state["evolutions"].append({"at": iso_now(), "mode": mode, "trigger": redact(normalize_text(trigger))})
    write_json(state_file, state)
    return state


# -----------------------------
# Dump ingestion helper
# -----------------------------


def extract_last_assistant_response(text: str) -> Optional[str]:
    matches = ASSISTANT_BLOCK_RE.findall(text or "")
    if matches:
        return matches[-1].strip()
    clean = normalize_text(text)
    return clean or None


def read_last_from_dump(dump_file: str | Path = DEFAULT_DUMP_FILE) -> Optional[str]:
    p = Path(dump_file)
    if not p.exists():
        return None
    return extract_last_assistant_response(p.read_text(encoding="utf-8", errors="replace"))


def ingest_dump(
    *,
    dump_file: str | Path = DEFAULT_DUMP_FILE,
    state_file: str | Path = DEFAULT_STATE_FILE,
    journal_file: str | Path = DEFAULT_JOURNAL_FILE,
    packet_file: str | Path = DEFAULT_PACKET_FILE,
    mode: str = "default",
) -> Optional[dict[str, Any]]:
    latest = read_last_from_dump(dump_file)
    if not latest:
        return None
    log_event(latest, kind="dump_ingest", mode=mode, source=str(dump_file), journal_file=journal_file)
    return reflect(state_file=state_file, journal_file=journal_file, packet_file=packet_file, mode=mode)


# -----------------------------
# CLI
# -----------------------------


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="RMPL reflect/evolve/log/compress utility")
    parser.add_argument("command", choices=["log", "reflect", "evolve", "ingest-dump", "packet"])
    parser.add_argument("text", nargs="?", default="")
    parser.add_argument("--mode", default="default")
    parser.add_argument("--state", default=DEFAULT_STATE_FILE)
    parser.add_argument("--journal", default=DEFAULT_JOURNAL_FILE)
    parser.add_argument("--packet", default=DEFAULT_PACKET_FILE)
    parser.add_argument("--dump", default=DEFAULT_DUMP_FILE)
    args = parser.parse_args()

    if args.command == "log":
        record = log_event(args.text, mode=args.mode, journal_file=args.journal)
        print(json.dumps(asdict(record), indent=2))
    elif args.command == "reflect":
        state = reflect(state_file=args.state, journal_file=args.journal, packet_file=args.packet, mode=args.mode)
        print(json.dumps(state.get("reflections", [])[-1] if state.get("reflections") else {}, indent=2))
    elif args.command == "evolve":
        state = evolve(args.text, state_file=args.state, journal_file=args.journal, packet_file=args.packet, mode=args.mode)
        print(json.dumps(state.get("evolutions", [])[-1] if state.get("evolutions") else {}, indent=2))
    elif args.command == "ingest-dump":
        state = ingest_dump(dump_file=args.dump, state_file=args.state, journal_file=args.journal, packet_file=args.packet, mode=args.mode)
        print(json.dumps({"ingested": state is not None, "last_reflection": (state or {}).get("last_reflection")}, indent=2))
    elif args.command == "packet":
        recent = list_recent_lines(args.journal, limit=20)
        packet = compress_entries(recent, mode=args.mode)
        print(json.dumps(asdict(packet), indent=2))


if __name__ == "__main__":
    main()
