# -*- coding: utf-8 -*-
"""Data models for the optional spawn_subagent event side channel."""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Deque, Mapping


class SubagentStreamStatus(str, Enum):
    """Lifecycle of an observable subagent run."""

    PREPARING = "preparing"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


TERMINAL_STATUSES = {
    SubagentStreamStatus.COMPLETED,
    SubagentStreamStatus.FAILED,
    SubagentStreamStatus.CANCELLED,
    SubagentStreamStatus.EXPIRED,
}


class SubagentStreamEventKind(str, Enum):
    """Stable event kinds exposed by the SSE protocol."""

    METADATA = "metadata"
    RUNTIME = "runtime"
    STATUS = "status"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class SubagentBindingKey:
    """Owner-scoped lookup key derived from the parent tool invocation."""

    agent_id: str
    parent_session_id: str
    parent_user_id: str
    parent_channel: str
    parent_tool_call_id: str


@dataclass(frozen=True, slots=True)
class SubagentStreamOwner:
    """Owner fields required to read a stream."""

    agent_id: str
    parent_session_id: str
    parent_user_id: str
    parent_channel: str

    @classmethod
    def from_binding(
        cls,
        binding: SubagentBindingKey,
    ) -> "SubagentStreamOwner":
        return cls(
            agent_id=binding.agent_id,
            parent_session_id=binding.parent_session_id,
            parent_user_id=binding.parent_user_id,
            parent_channel=binding.parent_channel,
        )


@dataclass(frozen=True, slots=True)
class SubagentStreamEvent:
    stream_id: str
    sequence: int
    kind: SubagentStreamEventKind
    payload: Mapping[str, Any]
    created_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "stream_id": self.stream_id,
            "sequence": self.sequence,
            "kind": self.kind.value,
            "payload": dict(self.payload),
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class SubagentStreamSnapshot:
    stream_id: str
    binding: SubagentBindingKey
    status: SubagentStreamStatus
    fork: bool
    background: bool
    child_session_id: str | None
    child_chat_id: str | None
    task_id: str | None
    latest_sequence: int
    first_sequence: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "stream_id": self.stream_id,
            "status": self.status.value,
            "fork": self.fork,
            "background": self.background,
            "child_session_id": self.child_session_id,
            "child_chat_id": self.child_chat_id,
            "task_id": self.task_id,
            "latest_sequence": self.latest_sequence,
            "first_sequence": self.first_sequence,
        }


@dataclass(frozen=True, slots=True)
class InternalSubagentStreamHandle:
    """Internal registration result; never serialize this type in a router."""

    snapshot: SubagentStreamSnapshot
    producer_token: str | None
    created: bool


@dataclass(frozen=True, slots=True)
class SubagentStreamLimits:
    max_streams: int = 512
    max_events_per_stream: int = 2000
    max_event_bytes: int = 512 * 1024
    subscriber_queue_size: int = 256
    terminal_ttl_seconds: float = 30 * 60
    active_ttl_seconds: float = 6 * 60 * 60


@dataclass(slots=True)
class SubagentStreamSubscription:
    subscription_id: str
    stream_id: str
    replay: tuple[SubagentStreamEvent, ...]
    queue: asyncio.Queue[SubagentStreamEvent]
    reset_required: bool = False
    closed: bool = False
    close_reason: str | None = None


@dataclass(slots=True)
class SubagentStreamRun:
    stream_id: str
    binding: SubagentBindingKey
    status: SubagentStreamStatus
    fork: bool
    background: bool
    producer_token_digest: bytes
    created_at: float
    updated_at: float
    events: Deque[SubagentStreamEvent]
    child_session_id: str | None = None
    child_chat_id: str | None = None
    task_id: str | None = None
    producer_claimed: bool = False
    latest_sequence: int = 0
    subscribers: dict[str, SubagentStreamSubscription] = field(
        default_factory=dict,
    )

    @classmethod
    def create(
        cls,
        *,
        stream_id: str,
        binding: SubagentBindingKey,
        fork: bool,
        background: bool,
        producer_token_digest: bytes,
        now: float,
        max_events: int,
    ) -> "SubagentStreamRun":
        return cls(
            stream_id=stream_id,
            binding=binding,
            status=SubagentStreamStatus.PREPARING,
            fork=fork,
            background=background,
            producer_token_digest=producer_token_digest,
            created_at=now,
            updated_at=now,
            events=deque(maxlen=max_events),
        )

    def snapshot(self) -> SubagentStreamSnapshot:
        first_sequence = self.events[0].sequence if self.events else 0
        return SubagentStreamSnapshot(
            stream_id=self.stream_id,
            binding=self.binding,
            status=self.status,
            fork=self.fork,
            background=self.background,
            child_session_id=self.child_session_id,
            child_chat_id=self.child_chat_id,
            task_id=self.task_id,
            latest_sequence=self.latest_sequence,
            first_sequence=first_sequence,
        )
