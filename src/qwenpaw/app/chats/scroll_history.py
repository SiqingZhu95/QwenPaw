"""Helpers for restoring messages archived by Scroll compression."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from agentscope.message import Msg

logger = logging.getLogger(__name__)

_SELECT_COLUMNS = (
    "seq, kind, role, name, content, tool_call_id, tool_state, "
    "blocks, metadata, created_at, dedup_key"
)


def _is_sequence_number(value: Any) -> bool:
    """Return whether *value* is a valid history sequence number."""
    return isinstance(value, int) and not isinstance(value, bool)


def extract_index_ranges(
    scroll_state: Mapping[str, Any],
    *,
    session_id: str,
    agent_id: str | None = None,
) -> list[tuple[int, int]]:
    """Extract normalized archived sequence ranges from Scroll state."""
    index = scroll_state.get("index")
    if not isinstance(index, Mapping):
        return []

    indexed_session = index.get("session_id")
    if indexed_session != session_id:
        return []
    if index.get("agent_id") != agent_id:
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


def _json_value(raw: Any, fallback: Any) -> Any:
    """Decode a JSON-backed history field without making display brittle."""
    if raw in (None, ""):
        return fallback
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return fallback


def history_rows_to_messages(
    rows: Sequence[Mapping[str, Any]],
) -> list[Msg]:
    """Convert durable history rows back to displayable AgentScope messages."""
    messages: list[Msg] = []
    for row in rows:
        try:
            role = (
                row["role"]
                if row["role"] in {"user", "assistant", "system"}
                else "assistant"
            )
            blocks = _json_value(row["blocks"], None)
            if not isinstance(blocks, list) or not blocks:
                if row["kind"] == "tool_result":
                    blocks = [{
                        "type": "tool_result",
                        "id": (
                            row["tool_call_id"]
                            or row["dedup_key"]
                            or f"scroll-tool-{row['seq']}"
                        ),
                        "name": row["name"] or "tool",
                        "output": row["content"] or "",
                        "state": row["tool_state"] or "success",
                    }]
                else:
                    blocks = [{
                        "type": "text",
                        "text": row["content"] or "",
                    }]

            metadata = _json_value(row["metadata"], {})
            if not isinstance(metadata, dict):
                metadata = {}

            messages.append(
                Msg(
                    id=(
                        row["dedup_key"]
                        or f"scroll-history-{row['seq']}"
                    ),
                    name=row["name"] or role,
                    role=role,
                    content=blocks,
                    metadata=metadata,
                    created_at=(
                        row["created_at"]
                        or "1970-01-01T00:00:00+00:00"
                    ),
                ),
            )
        except Exception:
            logger.warning(
                "Skipping unreadable Scroll history row seq=%r",
                row.get("seq") if isinstance(row, Mapping) else None,
                exc_info=True,
            )
    return messages


def read_archived_messages(
    *,
    workspace_dir: Path,
    db_filename: str,
    session_id: str,
    agent_id: str | None,
    scroll_state: Mapping[str, Any],
) -> list[Msg]:
    """Read the current Scroll checkpoint's archived messages, read-only."""
    ranges = extract_index_ranges(
        scroll_state,
        session_id=session_id,
        agent_id=agent_id,
    )
    if not ranges:
        return []

    db_path = Path(workspace_dir) / db_filename
    if not db_path.is_file():
        return []

    range_sql = " OR ".join(
        "(seq BETWEEN ? AND ?)"
        for _ in ranges
    )
    params: list[Any] = [session_id]
    if agent_id is None:
        agent_sql = "agent_id IS NULL"
    else:
        agent_sql = "agent_id = ?"
        params.append(agent_id)
    params.extend(value for pair in ranges for value in pair)
    sql = (
        f"SELECT {_SELECT_COLUMNS} FROM conversation_history "
        "WHERE session_id = ? "
        f"AND {agent_sql} "
        f"AND ({range_sql}) ORDER BY seq ASC"
    )

    try:
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1.0) as connection:
            connection.row_factory = sqlite3.Row
            rows = [
                dict(row)
                for row in connection.execute(sql, params).fetchall()
            ]
        return history_rows_to_messages(rows)
    except (OSError, sqlite3.Error):
        logger.warning(
            "Unable to read archived Scroll history from %s",
            db_path,
            exc_info=True,
        )
        return []
