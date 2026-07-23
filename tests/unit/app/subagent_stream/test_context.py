from __future__ import annotations

import pytest

from qwenpaw.app import agent_context
from qwenpaw.app.subagent_stream.context import (
    get_current_subagent_stream_invocation,
    subagent_stream_toolkit_middleware,
)


@pytest.mark.asyncio
async def test_middleware_preserves_tool_chunks_and_resets_context(
    monkeypatch,
):
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
    seen = []

    async def next_handler(**kwargs):
        del kwargs

        async def generate():
            seen.append(get_current_subagent_stream_invocation())
            yield "first"
            yield "second"

        return generate()

    output = []
    generator = subagent_stream_toolkit_middleware(
        {
            "tool_call": {
                "id": "middleware-call",
                "name": "spawn_subagent",
                "input": {"fork": False, "background": False},
            },
        },
        next_handler,
    )
    async for item in generator:
        output.append(item)

    assert output == ["first", "second"]
    assert seen[0] is not None
    assert seen[0].binding.parent_tool_call_id == "middleware-call"
    assert get_current_subagent_stream_invocation() is None


@pytest.mark.asyncio
async def test_non_spawn_tool_is_a_noop():
    async def next_handler(**kwargs):
        del kwargs

        async def generate():
            yield {"unchanged": True}

        return generate()

    output = []
    async for item in subagent_stream_toolkit_middleware(
        {"tool_call": {"id": "x", "name": "read_file", "input": {}}},
        next_handler,
    ):
        output.append(item)
    assert output == [{"unchanged": True}]
