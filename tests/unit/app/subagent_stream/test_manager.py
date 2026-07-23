from __future__ import annotations

import pytest

from qwenpaw.app.subagent_stream.manager import SubagentStreamManager
from qwenpaw.app.subagent_stream.models import (
    SubagentBindingKey,
    SubagentStreamLimits,
    SubagentStreamOwner,
    SubagentStreamStatus,
)


def binding(tool_call_id: str = "call-1") -> SubagentBindingKey:
    return SubagentBindingKey(
        agent_id="default",
        parent_session_id="parent-session",
        parent_user_id="user-1",
        parent_channel="console",
        parent_tool_call_id=tool_call_id,
    )


@pytest.mark.asyncio
async def test_register_is_idempotent_but_token_is_returned_only_once():
    manager = SubagentStreamManager()

    first = await manager.register_or_get(
        binding(),
        fork=False,
        background=False,
    )
    second = await manager.register_or_get(
        binding(),
        fork=False,
        background=False,
    )

    assert first.created is True
    assert first.producer_token
    assert "producer_token" not in first.snapshot.to_dict()
    assert second.created is False
    assert second.producer_token is None
    assert second.snapshot.stream_id == first.snapshot.stream_id


@pytest.mark.asyncio
async def test_producer_claim_requires_token_agent_and_child_session():
    manager = SubagentStreamManager()
    handle = await manager.register_or_get(
        binding(),
        fork=True,
        background=False,
    )
    token = handle.producer_token or ""

    assert not await manager.expect_child(
        handle.snapshot.stream_id,
        "wrong-token",
        agent_id="default",
        child_session_id="child-1",
    )
    assert await manager.expect_child(
        handle.snapshot.stream_id,
        token,
        agent_id="default",
        child_session_id="child-1",
    )
    assert (
        await manager.claim_producer(
            handle.snapshot.stream_id,
            token,
            agent_id="other-agent",
            child_session_id="child-1",
        )
        is None
    )
    claimed = await manager.claim_producer(
        handle.snapshot.stream_id,
        token,
        agent_id="default",
        child_session_id="child-1",
    )
    assert claimed is not None
    assert claimed.status == SubagentStreamStatus.RUNNING


@pytest.mark.asyncio
async def test_replay_sequence_and_terminal_event_are_isolated_by_owner():
    manager = SubagentStreamManager()
    handle = await manager.register_or_get(
        binding("call-a"),
        fork=False,
        background=False,
    )
    token = handle.producer_token or ""
    await manager.expect_child(
        handle.snapshot.stream_id,
        token,
        agent_id="default",
        child_session_id="child-a",
    )
    await manager.claim_producer(
        handle.snapshot.stream_id,
        token,
        agent_id="default",
        child_session_id="child-a",
    )
    await manager.publish_runtime_event(
        handle.snapshot.stream_id,
        {"object": "message", "id": "m1"},
    )

    denied = await manager.subscribe(
        handle.snapshot.stream_id,
        SubagentStreamOwner(
            agent_id="default",
            parent_session_id="other-parent",
            parent_user_id="user-1",
            parent_channel="console",
        ),
        after_sequence=0,
    )
    assert denied is None

    subscription = await manager.subscribe(
        handle.snapshot.stream_id,
        SubagentStreamOwner.from_binding(binding("call-a")),
        after_sequence=2,
    )
    assert subscription is not None
    assert [event.sequence for event in subscription.replay] == [3, 4]

    await manager.complete(handle.snapshot.stream_id)
    terminal = await subscription.queue.get()
    assert terminal.payload["status"] == "completed"
    assert subscription.closed is True
    assert subscription.close_reason == "terminal"


@pytest.mark.asyncio
async def test_slow_subscriber_does_not_block_producer():
    manager = SubagentStreamManager(
        SubagentStreamLimits(subscriber_queue_size=1),
    )
    handle = await manager.register_or_get(
        binding(),
        fork=False,
        background=False,
    )
    token = handle.producer_token or ""
    await manager.expect_child(
        handle.snapshot.stream_id,
        token,
        agent_id="default",
        child_session_id="child-1",
    )
    await manager.claim_producer(
        handle.snapshot.stream_id,
        token,
        agent_id="default",
        child_session_id="child-1",
    )
    subscription = await manager.subscribe(
        handle.snapshot.stream_id,
        SubagentStreamOwner.from_binding(binding()),
        after_sequence=0,
    )
    assert subscription is not None

    assert await manager.publish_runtime_event(
        handle.snapshot.stream_id,
        {"object": "message", "id": "m1"},
    )
    assert await manager.publish_runtime_event(
        handle.snapshot.stream_id,
        {"object": "message", "id": "m2"},
    )
    assert subscription.closed is True
    assert subscription.close_reason == "slow_consumer"


@pytest.mark.asyncio
async def test_unserializable_event_closes_only_the_side_stream():
    manager = SubagentStreamManager()
    handle = await manager.register_or_get(
        binding(),
        fork=False,
        background=False,
    )
    token = handle.producer_token or ""
    await manager.expect_child(
        handle.snapshot.stream_id,
        token,
        agent_id="default",
        child_session_id="child-1",
    )
    await manager.claim_producer(
        handle.snapshot.stream_id,
        token,
        agent_id="default",
        child_session_id="child-1",
    )

    published = await manager.publish_runtime_event(
        handle.snapshot.stream_id,
        {"not_json": object()},
    )

    assert published is False
    snapshot = await manager.resolve(binding())
    assert snapshot is not None
    assert snapshot.status == SubagentStreamStatus.FAILED


@pytest.mark.asyncio
async def test_different_parent_tool_calls_never_share_a_stream():
    manager = SubagentStreamManager()
    first = await manager.register_or_get(
        binding("call-1"),
        fork=False,
        background=True,
    )
    second = await manager.register_or_get(
        binding("call-2"),
        fork=False,
        background=True,
    )
    assert first.snapshot.stream_id != second.snapshot.stream_id
