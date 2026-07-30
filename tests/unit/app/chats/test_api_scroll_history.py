# -*- coding: utf-8 -*-
"""GET chat integration tests for Scroll-archived display history."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from agentscope.message import Msg, TextBlock
from agentscope.state import AgentState

from qwenpaw.app.chats.api import get_chat
from qwenpaw.app.chats.models import ChatSpec


def text_message(*, name: str, role: str, text: str) -> Msg:
    return Msg(
        name=name,
        role=role,
        content=[TextBlock(type="text", text=text)],
    )


class FakeManager:
    async def get_chat(self, chat_id: str):
        return ChatSpec(
            id=chat_id,
            name="test",
            session_id="session-1",
            user_id="user-1",
            channel="console",
        )


class FakeSession:
    def __init__(self, state: dict):
        self.state = state

    async def get_session_state_dict(self, *args, **kwargs):
        return self.state


class FakeTaskTracker:
    async def get_status(self, chat_id: str):
        return "idle"


def fake_workspace(tmp_path: Path):
    scroll_config = SimpleNamespace(db_filename="history.db")
    light = SimpleNamespace(scroll_config=scroll_config)
    running = SimpleNamespace(light_context_config=light)
    return SimpleNamespace(
        workspace_dir=tmp_path,
        agent_id="default",
        config=SimpleNamespace(running=running),
        task_tracker=FakeTaskTracker(),
    )


@pytest.mark.asyncio
async def test_get_chat_prepends_archived_before_marker_and_tail(
    tmp_path,
    monkeypatch,
):
    marker = text_message(
        name="memory",
        role="user",
        text="[context compressed]",
    )
    tail = text_message(
        name="assistant",
        role="assistant",
        text="live-tail",
    )
    scroll_state = {
        "index": {
            "session_id": "session-1",
            "tiers": [[{
                "seq_lo": 1,
                "seq_hi": 2,
                "lines": [],
            }]],
        },
    }
    state = {
        "agent": {
            "state": AgentState(
                session_id="session-1",
                context=[marker, tail],
            ).model_dump(mode="json"),
            "scroll": scroll_state,
        },
    }
    archived = [
        text_message(name="user", role="user", text="archived-user"),
        text_message(
            name="assistant",
            role="assistant",
            text="archived-assistant",
        ),
    ]
    captured = {}

    def fake_reader(**kwargs):
        captured.update(kwargs)
        return archived

    monkeypatch.setattr(
        "qwenpaw.app.chats.api.read_archived_messages",
        fake_reader,
    )

    result = await get_chat(
        "00000000-0000-0000-0000-000000000001",
        mgr=FakeManager(),
        session=FakeSession(state),
        workspace=fake_workspace(tmp_path),
    )

    texts = [
        content.text
        for message in result.messages
        for content in message.content
        if content.type == "text"
    ]
    assert texts == [
        "archived-user",
        "archived-assistant",
        "[context compressed]",
        "live-tail",
    ]
    assert captured["workspace_dir"] == tmp_path
    assert captured["db_filename"] == "history.db"
    assert captured["session_id"] == "session-1"
    assert captured["agent_id"] == "default"
    assert captured["scroll_state"] is scroll_state


@pytest.mark.asyncio
async def test_get_chat_without_scroll_keeps_current_context(
    tmp_path,
    monkeypatch,
):
    tail = text_message(
        name="assistant",
        role="assistant",
        text="native-tail",
    )
    state = {
        "agent": {
            "state": AgentState(
                session_id="session-1",
                context=[tail],
            ).model_dump(mode="json"),
        },
    }

    def unexpected_reader(**kwargs):
        raise AssertionError("reader must not run without a Scroll checkpoint")

    monkeypatch.setattr(
        "qwenpaw.app.chats.api.read_archived_messages",
        unexpected_reader,
    )
    result = await get_chat(
        "00000000-0000-0000-0000-000000000001",
        mgr=FakeManager(),
        session=FakeSession(state),
        workspace=fake_workspace(tmp_path),
    )

    assert len(result.messages) == 1
    assert result.messages[0].content[0].text == "native-tail"


@pytest.mark.asyncio
async def test_get_chat_empty_archive_keeps_marker_and_status(
    tmp_path,
    monkeypatch,
):
    marker = text_message(
        name="memory",
        role="user",
        text="[context compressed]",
    )
    state = {
        "agent": {
            "state": AgentState(
                session_id="session-1",
                context=[marker],
            ).model_dump(mode="json"),
            "scroll": {
                "index": {
                    "session_id": "session-1",
                    "tiers": [[{
                        "seq_lo": 1,
                        "seq_hi": 2,
                        "lines": [],
                    }]],
                },
            },
        },
    }
    monkeypatch.setattr(
        "qwenpaw.app.chats.api.read_archived_messages",
        lambda **kwargs: [],
    )

    result = await get_chat(
        "00000000-0000-0000-0000-000000000001",
        mgr=FakeManager(),
        session=FakeSession(state),
        workspace=fake_workspace(tmp_path),
    )

    assert result.status == "idle"
    assert result.messages[0].content[0].text == "[context compressed]"


@pytest.mark.asyncio
async def test_get_chat_invalid_agent_state_uses_legacy_without_scroll(
    tmp_path,
    monkeypatch,
):
    legacy = text_message(
        name="user",
        role="user",
        text="legacy-message",
    )
    state = {
        "agent": {
            "state": {"context": "invalid"},
            "memory": {
                "content": [[legacy.model_dump(mode="json"), []]],
                "_compressed_summary": "legacy-summary",
            },
            "scroll": {
                "index": {
                    "session_id": "session-1",
                    "tiers": [[{
                        "seq_lo": 1,
                        "seq_hi": 2,
                        "lines": [],
                    }]],
                },
            },
        },
    }

    def unexpected_reader(**kwargs):
        raise AssertionError("invalid AgentState must stay on legacy fallback")

    monkeypatch.setattr(
        "qwenpaw.app.chats.api.read_archived_messages",
        unexpected_reader,
    )
    result = await get_chat(
        "00000000-0000-0000-0000-000000000001",
        mgr=FakeManager(),
        session=FakeSession(state),
        workspace=fake_workspace(tmp_path),
    )

    assert result.messages[0].content[0].text == "legacy-message"
