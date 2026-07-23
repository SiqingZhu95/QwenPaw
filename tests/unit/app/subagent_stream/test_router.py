from __future__ import annotations

from types import SimpleNamespace

import pytest

import qwenpaw.app.subagent_stream.router as router_module
from qwenpaw.app.subagent_stream.manager import SubagentStreamManager
from qwenpaw.app.subagent_stream.models import (
    SubagentBindingKey,
    SubagentStreamOwner,
)


@pytest.mark.asyncio
async def test_events_endpoint_replays_json_sse_without_producer_token(
    monkeypatch,
):
    manager = SubagentStreamManager()
    binding = SubagentBindingKey(
        agent_id="default",
        parent_session_id="parent",
        parent_user_id="user",
        parent_channel="console",
        parent_tool_call_id="call-1",
    )
    handle = await manager.register_or_get(
        binding,
        fork=False,
        background=False,
    )
    token = handle.producer_token or ""
    await manager.expect_child(
        handle.snapshot.stream_id,
        token,
        agent_id="default",
        child_session_id="child",
    )
    await manager.claim_producer(
        handle.snapshot.stream_id,
        token,
        agent_id="default",
        child_session_id="child",
    )
    await manager.publish_runtime_event(
        handle.snapshot.stream_id,
        {"object": "message", "id": "message-1"},
    )
    await manager.complete(handle.snapshot.stream_id)

    async def allow_agent(_request, _agent_id):
        return None

    monkeypatch.setattr(router_module, "_validate_agent", allow_agent)
    monkeypatch.setattr(
        router_module,
        "get_subagent_stream_manager",
        lambda: manager,
    )
    body = router_module.SubscribeSubagentStreamRequest(
        agent_id="default",
        parent_session_id="parent",
        parent_user_id="user",
        parent_channel="console",
        after_sequence=0,
    )

    response = await router_module.stream_subagent_events(
        handle.snapshot.stream_id,
        SimpleNamespace(),
        body,
    )
    chunks = [chunk async for chunk in response.body_iterator]
    text = "".join(
        chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
        for chunk in chunks
    )

    assert "event: subagent" in text
    assert '"id": "message-1"' in text
    assert token not in text


@pytest.mark.asyncio
async def test_resolve_requires_the_complete_owner_binding(monkeypatch):
    manager = SubagentStreamManager()
    binding = SubagentBindingKey(
        agent_id="default",
        parent_session_id="parent",
        parent_user_id="user",
        parent_channel="console",
        parent_tool_call_id="call-1",
    )
    await manager.register_or_get(binding, fork=True, background=True)

    async def allow_agent(_request, _agent_id):
        return None

    monkeypatch.setattr(router_module, "_validate_agent", allow_agent)
    monkeypatch.setattr(
        router_module,
        "get_subagent_stream_manager",
        lambda: manager,
    )
    body = router_module.ResolveSubagentStreamRequest(
        agent_id="default",
        parent_session_id="other-parent",
        parent_user_id="user",
        parent_channel="console",
        parent_tool_call_id="call-1",
        wait_timeout_ms=0,
    )

    result = await router_module.resolve_subagent_stream(
        SimpleNamespace(),
        body,
    )

    assert result["found"] is False
    assert result["stream"] is None
