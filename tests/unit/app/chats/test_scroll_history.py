# -*- coding: utf-8 -*-
"""Tests for restoring Scroll-archived messages for chat display."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from qwenpaw.agents.context.scroll.history import HistoryStore
from qwenpaw.agents.context.types import LogEntry
from qwenpaw.app.chats.scroll_history import (
    extract_index_ranges,
    history_rows_to_messages,
    read_archived_messages,
)


def _append_text(
    store: HistoryStore,
    *,
    session_id: str,
    text: str,
    role: str,
    dedup_key: str,
    agent_id: str | None = "default",
) -> int:
    return store.append(
        session_id=session_id,
        agent_id=agent_id,
        dedup_key=dedup_key,
        entry=LogEntry(
            kind="context_msg" if role == "user" else "model_turn",
            role=role,
            content=text,
            blocks=[{"type": "text", "text": text}],
            metadata={"source": "test"},
            created_at="2026-07-30T00:00:00+00:00",
        ),
    )


def test_extract_index_ranges_merges_tiers_and_adjacent_spans():
    """A carried index must recover every archived seq exactly once."""
    scroll = {
        "index": {
            "session_id": "session-1",
            "agent_id": None,
            "tiers": [
                [
                    {"seq_lo": 20, "seq_hi": 29, "lines": []},
                    {"seq_lo": 30, "seq_hi": 35, "lines": []},
                ],
                [{"seq_lo": 1, "seq_hi": 19, "lines": []}],
            ],
        },
    }

    assert extract_index_ranges(
        scroll,
        session_id="session-1",
    ) == [(1, 35)]


def test_extract_index_ranges_accepts_legacy_levels():
    """Older checkpoints used ``levels`` and must remain readable."""
    scroll = {
        "index": {
            "session_id": "session-1",
            "agent_id": None,
            "levels": [
                [{"seq_lo": 5, "seq_hi": 8, "lines": []}],
            ],
        },
    }

    assert extract_index_ranges(
        scroll,
        session_id="session-1",
    ) == [(5, 8)]


def test_extract_index_ranges_rejects_other_session():
    """A copied or corrupt checkpoint must not leak another session."""
    scroll = {
        "index": {
            "session_id": "other-session",
            "agent_id": None,
            "tiers": [
                [{"seq_lo": 1, "seq_hi": 10, "lines": []}],
            ],
        },
    }

    assert extract_index_ranges(scroll, session_id="session-1") == []


@pytest.mark.parametrize(
    "index_identity",
    [
        {"agent_id": "default"},
        {"session_id": "session-1", "agent_id": "alternate"},
    ],
)
def test_extract_index_ranges_requires_exact_checkpoint_identity(
    index_identity: dict,
):
    """Missing session and another agent's checkpoint must be rejected."""
    scroll = {
        "index": {
            **index_identity,
            "tiers": [
                [{"seq_lo": 1, "seq_hi": 10, "lines": []}],
            ],
        },
    }

    assert extract_index_ranges(
        scroll,
        session_id="session-1",
        agent_id="default",
    ) == []


def test_extract_index_ranges_ignores_invalid_values():
    """Booleans, strings, and reversed spans are not valid seq ranges."""
    scroll = {
        "index": {
            "session_id": "session-1",
            "agent_id": None,
            "tiers": [
                [
                    {"seq_lo": True, "seq_hi": 3, "lines": []},
                    {"seq_lo": 9, "seq_hi": 4, "lines": []},
                    {"seq_lo": "1", "seq_hi": 2, "lines": []},
                ],
            ],
        },
    }

    assert extract_index_ranges(scroll, session_id="session-1") == []


def test_read_archived_messages_uses_only_indexed_session_ranges(
    tmp_path: Path,
):
    """Rows outside the active checkpoint, session, or agent stay hidden."""
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path)
    before = _append_text(
        store,
        session_id="session-1",
        text="before-clear",
        role="user",
        dedup_key="before",
    )
    first = _append_text(
        store,
        session_id="session-1",
        text="archived-user",
        role="user",
        dedup_key="user",
    )
    _append_text(
        store,
        session_id="other-session",
        text="must-not-leak-session",
        role="user",
        dedup_key="other-session",
    )
    _append_text(
        store,
        session_id="session-1",
        text="must-not-leak-agent",
        role="assistant",
        dedup_key="other-agent",
        agent_id="alternate",
    )
    _append_text(
        store,
        session_id="session-1",
        text="must-not-leak-null-agent",
        role="assistant",
        dedup_key="null-agent",
        agent_id=None,
    )
    last = _append_text(
        store,
        session_id="session-1",
        text="archived-assistant",
        role="assistant",
        dedup_key="assistant",
    )
    store.close()
    mtime = db_path.stat().st_mtime_ns

    messages = read_archived_messages(
        workspace_dir=tmp_path,
        db_filename="history.db",
        session_id="session-1",
        agent_id="default",
        scroll_state={
            "index": {
                "session_id": "session-1",
                "agent_id": "default",
                "tiers": [[{
                    "seq_lo": first,
                    "seq_hi": last,
                    "lines": [],
                }]],
            },
        },
    )

    assert [message.get_text_content() for message in messages] == [
        "archived-user",
        "archived-assistant",
    ]
    assert before < first
    assert db_path.stat().st_mtime_ns == mtime


def test_read_archived_messages_restores_tool_result_and_legacy_text(
    tmp_path: Path,
):
    """Both structured blocks and pre-block-schema text remain displayable."""
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path)
    first = store.append(
        session_id="session-1",
        agent_id="default",
        dedup_key="legacy",
        entry=LogEntry(
            kind="model_turn",
            role="assistant",
            content="legacy-text",
            blocks=None,
        ),
    )
    last = store.append(
        session_id="session-1",
        agent_id="default",
        dedup_key="tool-1",
        entry=LogEntry(
            kind="tool_result",
            role="assistant",
            name="search",
            content="tool-output",
            tool_call_id="tool-1",
            tool_state="success",
            blocks=[{
                "type": "tool_result",
                "id": "tool-1",
                "name": "search",
                "output": "tool-output",
            }],
        ),
    )
    store.close()

    messages = read_archived_messages(
        workspace_dir=tmp_path,
        db_filename="history.db",
        session_id="session-1",
        agent_id="default",
        scroll_state={
            "index": {
                "session_id": "session-1",
                "agent_id": "default",
                "tiers": [[{
                    "seq_lo": first,
                    "seq_hi": last,
                    "lines": [],
                }]],
            },
        },
    )

    assert messages[0].get_text_content() == "legacy-text"
    assert messages[1].content[0].type == "tool_result"
    assert messages[1].content[0].id == "tool-1"


@pytest.mark.parametrize("database_state", ["missing", "corrupt", "no-table"])
def test_read_archived_messages_fails_open_for_unavailable_database(
    tmp_path: Path,
    database_state: str,
):
    """Unavailable history must not make the existing chat endpoint fail."""
    db_path = tmp_path / "history.db"
    if database_state == "corrupt":
        db_path.write_bytes(b"not-a-sqlite-database")
    elif database_state == "no-table":
        sqlite3.connect(db_path).close()

    messages = read_archived_messages(
        workspace_dir=tmp_path,
        db_filename="history.db",
        session_id="session-1",
        agent_id="default",
        scroll_state={
            "index": {
                "session_id": "session-1",
                "agent_id": "default",
                "tiers": [[{
                    "seq_lo": 1,
                    "seq_hi": 2,
                    "lines": [],
                }]],
            },
        },
    )

    assert messages == []
    if database_state == "missing":
        assert not db_path.exists()


def test_read_archived_messages_allows_null_rows_only_for_null_agent(
    tmp_path: Path,
):
    """Legacy null-agent rows require a matching null-agent checkpoint."""
    store = HistoryStore(tmp_path / "history.db")
    first = _append_text(
        store,
        session_id="session-1",
        text="legacy-null-agent",
        role="user",
        dedup_key="legacy-null",
        agent_id=None,
    )
    last = _append_text(
        store,
        session_id="session-1",
        text="current-agent",
        role="assistant",
        dedup_key="current",
        agent_id="default",
    )
    store.close()

    messages = read_archived_messages(
        workspace_dir=tmp_path,
        db_filename="history.db",
        session_id="session-1",
        agent_id=None,
        scroll_state={
            "index": {
                "session_id": "session-1",
                "agent_id": None,
                "tiers": [[{
                    "seq_lo": first,
                    "seq_hi": last,
                    "lines": [],
                }]],
            },
        },
    )

    assert [message.get_text_content() for message in messages] == [
        "legacy-null-agent",
    ]


def test_history_rows_to_messages_falls_back_from_invalid_blocks():
    """A damaged structured payload falls back to its recoverable text."""
    messages = history_rows_to_messages([{
        "seq": 7,
        "kind": "model_turn",
        "role": "assistant",
        "name": None,
        "content": "recoverable-text",
        "tool_call_id": None,
        "tool_state": None,
        "blocks": "{invalid-json",
        "metadata": "{invalid-json",
        "created_at": None,
        "dedup_key": None,
    }])

    assert len(messages) == 1
    assert messages[0].id == "scroll-history-7"
    assert messages[0].get_text_content() == "recoverable-text"
