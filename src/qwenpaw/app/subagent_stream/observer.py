# -*- coding: utf-8 -*-
"""Fail-open observer strategies for DynamicMultiAgentRunner."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from .manager import SubagentStreamManager, get_subagent_stream_manager
from .serialization import RuntimeEventSerializer
from .tool_bridge import PRODUCER_TOKEN_CONTEXT_KEY, STREAM_ID_CONTEXT_KEY

logger = logging.getLogger(__name__)


class SubagentStreamObserver(Protocol):
    async def observe(self, event: Any) -> None:
        ...

    async def finish(self) -> None:
        ...

    async def fail(self, error: BaseException) -> None:
        ...


class NullSubagentStreamObserver:
    async def observe(self, event: Any) -> None:
        del event

    async def finish(self) -> None:
        return None

    async def fail(self, error: BaseException) -> None:
        del error


class ManagerSubagentStreamObserver:
    def __init__(
        self,
        manager: SubagentStreamManager,
        stream_id: str,
        serializer: RuntimeEventSerializer | None = None,
    ) -> None:
        self._manager = manager
        self._stream_id = stream_id
        self._serializer = serializer or RuntimeEventSerializer()

    async def observe(self, event: Any) -> None:
        payload = self._serializer.serialize(event)
        await self._manager.publish_runtime_event(self._stream_id, payload)

    async def finish(self) -> None:
        await self._manager.complete(self._stream_id)

    async def fail(self, error: BaseException) -> None:
        code = (
            "subagent_cancelled"
            if isinstance(error, asyncio.CancelledError)
            else "subagent_runtime_failed"
        )
        if isinstance(error, asyncio.CancelledError):
            await self._manager.cancel(self._stream_id)
        else:
            await self._manager.fail(self._stream_id, code)


class SafeSubagentStreamObserver:
    """Protect the original Runtime generator from every observer failure."""

    def __init__(
        self,
        delegate: SubagentStreamObserver,
        *,
        manager: SubagentStreamManager | None = None,
        stream_id: str | None = None,
    ) -> None:
        self._delegate = delegate
        self._manager = manager
        self._stream_id = stream_id
        self._disabled = False

    async def _disable(self, code: str) -> None:
        self._disabled = True
        if self._manager is not None and self._stream_id:
            try:
                await self._manager.fail(self._stream_id, code)
            except Exception:  # noqa: BLE001
                logger.debug(
                    "Unable to close failed subagent stream",
                    exc_info=True,
                )

    async def observe(self, event: Any) -> None:
        if self._disabled:
            return
        try:
            await self._delegate.observe(event)
        except Exception:  # noqa: BLE001
            logger.warning("Subagent stream observer failed", exc_info=True)
            await self._disable("stream_serialization_failed")

    async def finish(self) -> None:
        if self._disabled:
            return
        try:
            await self._delegate.finish()
        except Exception:  # noqa: BLE001
            logger.warning(
                "Subagent stream finish observer failed",
                exc_info=True,
            )
            await self._disable("stream_observer_failed")

    async def fail(self, error: BaseException) -> None:
        if self._disabled:
            return
        try:
            await self._delegate.fail(error)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Subagent stream failure observer failed",
                exc_info=True,
            )
            await self._disable("stream_observer_failed")


@dataclass(frozen=True, slots=True)
class ObserverCreationResult:
    request_for_runner: Any
    observer: SubagentStreamObserver


def _copy_request_with_clean_context(request: Any) -> tuple[Any, str, str]:
    original_context = getattr(request, "request_context", None)
    context = (
        dict(original_context) if isinstance(original_context, dict) else {}
    )
    stream_id = str(context.pop(STREAM_ID_CONTEXT_KEY, "") or "")
    producer_token = str(context.pop(PRODUCER_TOKEN_CONTEXT_KEY, "") or "")
    if context == (original_context or {}):
        return request, stream_id, producer_token
    if hasattr(request, "model_copy"):
        return (
            request.model_copy(update={"request_context": context}),
            stream_id,
            producer_token,
        )
    if isinstance(request, dict):
        copied = dict(request)
        copied["request_context"] = context
        return copied, stream_id, producer_token
    try:
        import copy

        copied = copy.copy(request)
        setattr(copied, "request_context", context)
        return copied, stream_id, producer_token
    except Exception:  # noqa: BLE001
        # Unknown request types are used only by tests/custom integrations. Do
        # not mutate them; the normal AgentRequest path above is always copied.
        return request, stream_id, producer_token


class SubagentStreamObserverFactory:
    def __init__(self, manager: SubagentStreamManager | None = None) -> None:
        self._manager = manager or get_subagent_stream_manager()

    async def create(
        self,
        request: Any,
        workspace: Any,
    ) -> ObserverCreationResult:
        request_for_runner = request
        try:
            request_for_runner, stream_id, producer_token = (
                _copy_request_with_clean_context(request)
            )
            if not stream_id or not producer_token:
                return ObserverCreationResult(
                    request_for_runner,
                    NullSubagentStreamObserver(),
                )
            agent_id = str(getattr(workspace, "agent_id", "") or "")
            child_session_id = str(
                getattr(request, "session_id", "")
                or (
                    request.get("session_id", "")
                    if isinstance(request, dict)
                    else ""
                ),
            )
            snapshot = await self._manager.claim_producer(
                stream_id,
                producer_token,
                agent_id=agent_id,
                child_session_id=child_session_id,
            )
            if snapshot is None:
                return ObserverCreationResult(
                    request_for_runner,
                    NullSubagentStreamObserver(),
                )
            delegate = ManagerSubagentStreamObserver(self._manager, stream_id)
            return ObserverCreationResult(
                request_for_runner,
                SafeSubagentStreamObserver(
                    delegate,
                    manager=self._manager,
                    stream_id=stream_id,
                ),
            )
        except Exception:  # noqa: BLE001 - factory must never affect Runner
            logger.warning(
                "Unable to create subagent stream observer",
                exc_info=True,
            )
            return ObserverCreationResult(
                request_for_runner,
                NullSubagentStreamObserver(),
            )
