# -*- coding: utf-8 -*-
"""Process-local replay and subscription manager for subagent streams."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Mapping
from uuid import uuid4

from .models import (
    InternalSubagentStreamHandle,
    SubagentBindingKey,
    SubagentStreamEvent,
    SubagentStreamEventKind,
    SubagentStreamLimits,
    SubagentStreamOwner,
    SubagentStreamRun,
    SubagentStreamSnapshot,
    SubagentStreamStatus,
    SubagentStreamSubscription,
    TERMINAL_STATUSES,
)


class SubagentStreamManager:
    """Owns isolated, bounded event buffers for spawn_subagent invocations."""

    def __init__(self, limits: SubagentStreamLimits | None = None) -> None:
        self._limits = limits or SubagentStreamLimits()
        self._lock = asyncio.Lock()
        self._runs: dict[str, SubagentStreamRun] = {}
        self._bindings: dict[SubagentBindingKey, str] = {}

    @staticmethod
    def _token_digest(token: str) -> bytes:
        return hashlib.sha256(token.encode("utf-8")).digest()

    @classmethod
    def _token_matches(cls, run: SubagentStreamRun, token: str) -> bool:
        if not token:
            return False
        return hmac.compare_digest(
            run.producer_token_digest,
            cls._token_digest(token),
        )

    @staticmethod
    def _owner_matches(
        binding: SubagentBindingKey,
        owner: SubagentStreamOwner,
    ) -> bool:
        return SubagentStreamOwner.from_binding(binding) == owner

    def _remove_run_locked(self, stream_id: str) -> None:
        run = self._runs.pop(stream_id, None)
        if run is None:
            return
        if self._bindings.get(run.binding) == stream_id:
            self._bindings.pop(run.binding, None)
        for subscription in run.subscribers.values():
            subscription.closed = True
            subscription.close_reason = "stream_expired"
        run.subscribers.clear()

    def _cleanup_locked(self, now: float) -> None:
        expired: list[str] = []
        for stream_id, run in self._runs.items():
            ttl = (
                self._limits.terminal_ttl_seconds
                if run.status in TERMINAL_STATUSES
                else self._limits.active_ttl_seconds
            )
            if now - run.updated_at > ttl:
                expired.append(stream_id)
        for stream_id in expired:
            self._remove_run_locked(stream_id)

    def _evict_for_capacity_locked(self) -> None:
        if len(self._runs) < self._limits.max_streams:
            return
        terminal = [
            run
            for run in self._runs.values()
            if run.status in TERMINAL_STATUSES
        ]
        if terminal:
            oldest = min(terminal, key=lambda run: run.updated_at)
            self._remove_run_locked(oldest.stream_id)
            return
        raise RuntimeError("subagent_stream_capacity_exceeded")

    def _publish_locked(
        self,
        run: SubagentStreamRun,
        kind: SubagentStreamEventKind,
        payload: Mapping[str, Any],
        now: float,
    ) -> SubagentStreamEvent:
        run.latest_sequence += 1
        run.updated_at = now
        event = SubagentStreamEvent(
            stream_id=run.stream_id,
            sequence=run.latest_sequence,
            kind=kind,
            payload=dict(payload),
            created_at=now,
        )
        run.events.append(event)
        slow_subscribers: list[str] = []
        for subscription_id, subscription in run.subscribers.items():
            try:
                subscription.queue.put_nowait(event)
            except asyncio.QueueFull:
                subscription.closed = True
                subscription.close_reason = "slow_consumer"
                slow_subscribers.append(subscription_id)
        for subscription_id in slow_subscribers:
            run.subscribers.pop(subscription_id, None)
        return event

    async def register_or_get(
        self,
        binding: SubagentBindingKey,
        *,
        fork: bool,
        background: bool,
    ) -> InternalSubagentStreamHandle:
        now = time.time()
        async with self._lock:
            self._cleanup_locked(now)
            existing_id = self._bindings.get(binding)
            if existing_id:
                existing = self._runs.get(existing_id)
                if existing is not None:
                    return InternalSubagentStreamHandle(
                        snapshot=existing.snapshot(),
                        producer_token=None,
                        created=False,
                    )

            self._evict_for_capacity_locked()
            stream_id = str(uuid4())
            producer_token = secrets.token_urlsafe(32)
            run = SubagentStreamRun.create(
                stream_id=stream_id,
                binding=binding,
                fork=fork,
                background=background,
                producer_token_digest=self._token_digest(producer_token),
                now=now,
                max_events=self._limits.max_events_per_stream,
            )
            self._runs[stream_id] = run
            self._bindings[binding] = stream_id
            self._publish_locked(
                run,
                SubagentStreamEventKind.METADATA,
                {
                    "status": run.status.value,
                    "fork": fork,
                    "background": background,
                },
                now,
            )
            return InternalSubagentStreamHandle(
                snapshot=run.snapshot(),
                producer_token=producer_token,
                created=True,
            )

    async def resolve(
        self,
        binding: SubagentBindingKey,
    ) -> SubagentStreamSnapshot | None:
        async with self._lock:
            self._cleanup_locked(time.time())
            stream_id = self._bindings.get(binding)
            run = self._runs.get(stream_id or "")
            return run.snapshot() if run is not None else None

    async def wait_for_binding(
        self,
        binding: SubagentBindingKey,
        *,
        timeout_seconds: float,
    ) -> SubagentStreamSnapshot | None:
        snapshot = await self.resolve(binding)
        if snapshot is not None or timeout_seconds <= 0:
            return snapshot
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            await asyncio.sleep(
                min(0.05, max(0.0, deadline - time.monotonic())),
            )
            snapshot = await self.resolve(binding)
            if snapshot is not None:
                return snapshot
        return None

    async def expect_child(
        self,
        stream_id: str,
        producer_token: str,
        *,
        agent_id: str,
        child_session_id: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> bool:
        async with self._lock:
            run = self._runs.get(stream_id)
            if (
                run is None
                or run.binding.agent_id != agent_id
                or not child_session_id
                or not self._token_matches(run, producer_token)
                or run.status in TERMINAL_STATUSES
            ):
                return False
            if (
                run.child_session_id
                and run.child_session_id != child_session_id
            ):
                return False
            run.child_session_id = child_session_id
            extra = dict(metadata or {})
            if isinstance(extra.get("child_chat_id"), str):
                run.child_chat_id = extra["child_chat_id"]
            self._publish_locked(
                run,
                SubagentStreamEventKind.METADATA,
                {
                    "child_session_id": child_session_id,
                    **extra,
                },
                time.time(),
            )
            return True

    async def claim_producer(
        self,
        stream_id: str,
        producer_token: str,
        *,
        agent_id: str,
        child_session_id: str,
    ) -> SubagentStreamSnapshot | None:
        async with self._lock:
            run = self._runs.get(stream_id)
            if (
                run is None
                or run.binding.agent_id != agent_id
                or run.child_session_id != child_session_id
                or not self._token_matches(run, producer_token)
                or run.status in TERMINAL_STATUSES
            ):
                return None
            if not run.producer_claimed:
                run.producer_claimed = True
                run.status = SubagentStreamStatus.RUNNING
                self._publish_locked(
                    run,
                    SubagentStreamEventKind.STATUS,
                    {"status": run.status.value},
                    time.time(),
                )
            return run.snapshot()

    async def update_metadata(
        self,
        stream_id: str,
        producer_token: str,
        metadata: Mapping[str, Any],
    ) -> bool:
        async with self._lock:
            run = self._runs.get(stream_id)
            if run is None or not self._token_matches(run, producer_token):
                return False
            task_id = metadata.get("task_id")
            chat_id = metadata.get("child_chat_id")
            if isinstance(task_id, str) and task_id:
                run.task_id = task_id
            if isinstance(chat_id, str) and chat_id:
                run.child_chat_id = chat_id
            self._publish_locked(
                run,
                SubagentStreamEventKind.METADATA,
                metadata,
                time.time(),
            )
            return True

    async def publish_runtime_event(
        self,
        stream_id: str,
        payload: Mapping[str, Any],
    ) -> bool:
        try:
            encoded_size = len(
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            )
        except (TypeError, ValueError):
            await self.fail(stream_id, "stream_serialization_failed")
            return False
        if encoded_size > self._limits.max_event_bytes:
            await self.fail(stream_id, "stream_event_too_large")
            return False
        async with self._lock:
            run = self._runs.get(stream_id)
            if (
                run is None
                or not run.producer_claimed
                or run.status in TERMINAL_STATUSES
            ):
                return False
            self._publish_locked(
                run,
                SubagentStreamEventKind.RUNTIME,
                payload,
                time.time(),
            )
            return True

    async def _set_terminal(
        self,
        stream_id: str,
        status: SubagentStreamStatus,
        *,
        code: str | None = None,
    ) -> None:
        async with self._lock:
            run = self._runs.get(stream_id)
            if run is None or run.status in TERMINAL_STATUSES:
                return
            run.status = status
            payload: dict[str, Any] = {"status": status.value}
            if code:
                payload["code"] = code
            kind = (
                SubagentStreamEventKind.ERROR
                if status == SubagentStreamStatus.FAILED
                else SubagentStreamEventKind.STATUS
            )
            self._publish_locked(run, kind, payload, time.time())
            for subscription in run.subscribers.values():
                subscription.closed = True
                subscription.close_reason = "terminal"
            run.subscribers.clear()

    async def complete(self, stream_id: str) -> None:
        await self._set_terminal(stream_id, SubagentStreamStatus.COMPLETED)

    async def fail(self, stream_id: str, code: str) -> None:
        await self._set_terminal(
            stream_id,
            SubagentStreamStatus.FAILED,
            code=code,
        )

    async def cancel(self, stream_id: str) -> None:
        await self._set_terminal(stream_id, SubagentStreamStatus.CANCELLED)

    async def finish_unstarted(self, stream_id: str) -> None:
        async with self._lock:
            run = self._runs.get(stream_id)
            should_fail = bool(
                run is not None
                and not run.producer_claimed
                and run.status == SubagentStreamStatus.PREPARING
                and (
                    not run.child_session_id
                    or not run.background
                    or not run.task_id
                )
            )
        if should_fail:
            await self.fail(stream_id, "subagent_not_started")

    async def get_snapshot(
        self,
        stream_id: str,
        owner: SubagentStreamOwner,
    ) -> SubagentStreamSnapshot | None:
        async with self._lock:
            self._cleanup_locked(time.time())
            run = self._runs.get(stream_id)
            if run is None or not self._owner_matches(run.binding, owner):
                return None
            return run.snapshot()

    async def subscribe(
        self,
        stream_id: str,
        owner: SubagentStreamOwner,
        *,
        after_sequence: int,
    ) -> SubagentStreamSubscription | None:
        async with self._lock:
            self._cleanup_locked(time.time())
            run = self._runs.get(stream_id)
            if run is None or not self._owner_matches(run.binding, owner):
                return None
            first_sequence = run.events[0].sequence if run.events else 0
            reset_required = bool(
                after_sequence > 0
                and first_sequence > 0
                and after_sequence < first_sequence - 1
            )
            replay = tuple(
                event
                for event in run.events
                if event.sequence > after_sequence
            )
            subscription_id = str(uuid4())
            subscription = SubagentStreamSubscription(
                subscription_id=subscription_id,
                stream_id=stream_id,
                replay=replay,
                queue=asyncio.Queue(
                    maxsize=self._limits.subscriber_queue_size,
                ),
                reset_required=reset_required,
            )
            if run.status not in TERMINAL_STATUSES:
                run.subscribers[subscription_id] = subscription
            else:
                subscription.closed = True
                subscription.close_reason = "terminal"
            return subscription

    async def detach(self, subscription_id: str) -> None:
        async with self._lock:
            for run in self._runs.values():
                subscription = run.subscribers.pop(subscription_id, None)
                if subscription is not None:
                    subscription.closed = True
                    subscription.close_reason = (
                        subscription.close_reason or "detached"
                    )
                    return


_MANAGER = SubagentStreamManager()


def get_subagent_stream_manager() -> SubagentStreamManager:
    """Return the process-local side-channel manager."""

    return _MANAGER
