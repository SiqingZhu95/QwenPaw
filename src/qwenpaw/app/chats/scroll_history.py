"""Helpers for restoring messages archived by Scroll compression."""

from collections.abc import Mapping
from typing import Any


def _is_sequence_number(value: Any) -> bool:
    """Return whether *value* is a valid history sequence number."""
    return isinstance(value, int) and not isinstance(value, bool)


def extract_index_ranges(
    scroll_state: Mapping[str, Any],
    *,
    session_id: str,
) -> list[tuple[int, int]]:
    """Extract normalized archived sequence ranges from Scroll state."""
    index = scroll_state.get("index")
    if not isinstance(index, Mapping):
        return []

    indexed_session = index.get("session_id")
    if indexed_session and indexed_session != session_id:
        return []

    tiers = index.get("tiers", index.get("levels", []))
    if not isinstance(tiers, list):
        return []

    ranges: list[tuple[int, int]] = []
    for tier in tiers:
        if not isinstance(tier, list):
            continue
        for span in tier:
            if not isinstance(span, Mapping):
                continue
            start = span.get("seq_lo")
            end = span.get("seq_hi")
            if (
                not _is_sequence_number(start)
                or not _is_sequence_number(end)
                or start < 0
                or end < start
            ):
                continue
            ranges.append((start, end))

    if not ranges:
        return []

    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged
