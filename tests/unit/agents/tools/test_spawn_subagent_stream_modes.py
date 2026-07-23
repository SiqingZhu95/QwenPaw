from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from qwenpaw.agents.tools import agent_management
from qwenpaw.app import agent_context
from qwenpaw.app.subagent_stream.tool_bridge import subagent_stream_tool_bridge


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fork", "background", "expected_session"),
    [
        (False, False, "sub-fixed"),
        (True, False, "fork-fixed"),
        (False, True, "sub-fixed"),
        (True, True, "fork-fixed"),
    ],
)
async def test_all_spawn_modes_attach_stream_context_without_changing_result(
    monkeypatch,
    fork,
    background,
    expected_session,
):
    captured = {}

    monkeypatch.setattr(
        agent_context,
        "get_current_agent_id",
        lambda: "default",
    )
    monkeypatch.setattr(
        agent_context,
        "get_current_session_id",
        lambda: "parent",
    )
    monkeypatch.setattr(
        agent_context,
        "get_current_user_id",
        lambda: "user",
    )
    monkeypatch.setattr(
        agent_context,
        "get_current_channel",
        lambda: "console",
    )
    monkeypatch.setattr(
        agent_management,
        "_generate_subagent_session_id",
        lambda: "sub-fixed",
    )
    monkeypatch.setattr(
        agent_management,
        "_call_fork_api",
        AsyncMock(
            return_value={
                "fork_session_id": "fork-fixed",
                "worktree_path": "",
                "worktree_branch": "",
            },
        ),
    )

    async def prepare(base_context, **kwargs):
        assert kwargs["agent_id"] == "default"
        assert kwargs["child_session_id"] == expected_session
        return {**dict(base_context), "_private_stream_marker": "attached"}

    record_submission = AsyncMock()
    monkeypatch.setattr(
        subagent_stream_tool_bridge,
        "prepare_child_request_context",
        prepare,
    )
    monkeypatch.setattr(
        subagent_stream_tool_bridge,
        "record_background_submission",
        record_submission,
    )

    def foreground(_base_url, payload, agent_id, timeout):
        captured.update(payload=payload, agent_id=agent_id, timeout=timeout)
        return {
            "output": [
                {"content": [{"type": "text", "text": "unchanged result"}]},
            ],
        }

    def submit(_base_url, payload, agent_id, timeout):
        captured.update(payload=payload, agent_id=agent_id, timeout=timeout)
        return {"task_id": "task-fixed"}

    monkeypatch.setattr(
        agent_management,
        "collect_final_agent_chat_response",
        foreground,
    )
    monkeypatch.setattr(agent_management, "submit_agent_chat_task", submit)

    response = await agent_management.spawn_subagent(
        "inspect repository",
        fork=fork,
        background=background,
    )

    assert captured["payload"]["session_id"] == expected_session
    request_context = captured["payload"]["request_context"]
    assert request_context["_private_stream_marker"] == "attached"
    text = response.content[0]["text"]
    if background:
        assert text.startswith("[TASK_ID: task-fixed]")
        assert f"[SESSION: {expected_session}]" in text
        record_submission.assert_awaited_once_with({"task_id": "task-fixed"})
    else:
        assert text == f"[SESSION: {expected_session}]\n\nunchanged result"
        record_submission.assert_not_awaited()
