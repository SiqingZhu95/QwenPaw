from __future__ import annotations

from types import SimpleNamespace

import pytest
from agentscope_runtime.engine.schemas.agent_schemas import AgentRequest

from qwenpaw.app.subagent_stream.manager import SubagentStreamManager
from qwenpaw.app.subagent_stream.models import (
    SubagentBindingKey,
    SubagentStreamStatus,
)
from qwenpaw.app.subagent_stream.observer import (
    SafeSubagentStreamObserver,
    SubagentStreamObserverFactory,
)
from qwenpaw.app.subagent_stream.tool_bridge import (
    PRODUCER_TOKEN_CONTEXT_KEY,
    STREAM_ID_CONTEXT_KEY,
)


def make_request(child_session_id: str, context: dict) -> AgentRequest:
    return AgentRequest(
        session_id=child_session_id,
        input=[
            {
                "role": "user",
                "content": [{"type": "text", "text": "hello"}],
            },
        ],
        request_context=context,
    )


@pytest.mark.asyncio
async def test_factory_claims_and_strips_private_context_before_runner():
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
        fork=True,
        background=False,
    )
    token = handle.producer_token or ""
    await manager.expect_child(
        handle.snapshot.stream_id,
        token,
        agent_id="default",
        child_session_id="child",
    )
    request = make_request(
        "child",
        {
            STREAM_ID_CONTEXT_KEY: handle.snapshot.stream_id,
            PRODUCER_TOKEN_CONTEXT_KEY: token,
            "fork_project_dir": "project-dir",
        },
    )

    result = await SubagentStreamObserverFactory(manager).create(
        request,
        SimpleNamespace(agent_id="default"),
    )
    cleaned = result.request_for_runner.request_context
    assert STREAM_ID_CONTEXT_KEY not in cleaned
    assert PRODUCER_TOKEN_CONTEXT_KEY not in cleaned
    assert cleaned["fork_project_dir"] == "project-dir"
    assert STREAM_ID_CONTEXT_KEY in request.request_context

    await result.observer.observe({"object": "message", "id": "m1"})
    await result.observer.finish()
    snapshot = await manager.resolve(binding)
    assert snapshot is not None
    assert snapshot.status == SubagentStreamStatus.COMPLETED


@pytest.mark.asyncio
async def test_invalid_token_still_gets_stripped_and_request_continues():
    manager = SubagentStreamManager()
    request = make_request(
        "child",
        {
            STREAM_ID_CONTEXT_KEY: "forged-stream",
            PRODUCER_TOKEN_CONTEXT_KEY: "forged-token",
            "source": "test",
        },
    )
    result = await SubagentStreamObserverFactory(manager).create(
        request,
        SimpleNamespace(agent_id="default"),
    )
    assert result.request_for_runner.request_context == {"source": "test"}
    await result.observer.observe({"object": "message", "id": "safe"})
    await result.observer.finish()


@pytest.mark.asyncio
async def test_safe_observer_never_propagates_delegate_errors():
    class BrokenObserver:
        async def observe(self, event):
            del event
            raise RuntimeError("broken")

        async def finish(self):
            raise RuntimeError("broken")

        async def fail(self, error):
            del error
            raise RuntimeError("broken")

    observer = SafeSubagentStreamObserver(BrokenObserver())
    await observer.observe({"id": "m1"})
    await observer.finish()
    await observer.fail(RuntimeError("original"))
