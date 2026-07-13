QUORUM_THRESHOLD = 4  # all silos must flag before BLOCK escalates

def arbitrate_signals(signals: List[SiloSignal]) -> tuple:
    """
    Single explicit place that resolves conflicting silo opinions.
    Requires QUORUM_THRESHOLD distinct silos to agree before escalating to BLOCK.
    Returns (resolved_verdict, arbitration_note).
    """
    if not signals:
        return "UNCLASSIFIED", "No silo produced a signal. Logged as a visible gap, not defaulted to block."

    opinions = [s for s in signals if s.verdict != "NO_OPINION"]
    if not opinions:
        names = ", ".join(s.silo_id for s in signals)
        return "UNCLASSIFIED", f"No silo among [{names}] had an opinion. Visible gap, not a silent block."

    blocks = [s for s in opinions if s.verdict == "BLOCK" and s.confidence >= 0.7]
    if len(blocks) >= QUORUM_THRESHOLD:
        names = ", ".join(f"{s.silo_id} ({s.confidence:.2f})" for s in blocks)
        return "BLOCK", f"Quorum reached ({len(blocks)}/{QUORUM_THRESHOLD}). Blocked by: {names}."

    clarifies = [s for s in opinions if s.verdict == "STOP_FOR_CLARITY"]
    if clarifies:
        names = ", ".join(s.silo_id for s in clarifies)
        return "STOP_FOR_CLARITY", f"Clarification requested by: {names}."

    weak_blocks = [s for s in opinions if s.verdict == "BLOCK"]
    if weak_blocks:
        names = ", ".join(f"{s.silo_id} ({s.confidence:.2f})" for s in weak_blocks)
        return "ALLOW", f"Sub-quorum block signals ({len(weak_blocks)}/{QUORUM_THRESHOLD}) from: {names}. Insufficient for block."

    names = ", ".join(s.silo_id for s in opinions)
    return "ALLOW", f"All opinions agree: {names} -> ALLOW."
