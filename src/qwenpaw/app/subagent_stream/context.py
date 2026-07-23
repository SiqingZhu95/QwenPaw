# -*- coding: utf-8 -*-
"""Toolkit middleware and invocation-local subagent stream context."""
from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Callable

from .manager import get_subagent_stream_manager
from .models import SubagentBindingKey

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SubagentStreamInvocationContext:
    stream_id: str
    producer_token: str
    binding: SubagentBindingKey


_current_invocation: ContextVar[SubagentStreamInvocationContext | None] = (
    ContextVar("qwenpaw_subagent_stream_invocation", default=None)
)


def get_current_subagent_stream_invocation(
) -> SubagentStreamInvocationContext | None:
    return _current_invocation.get()


def set_current_subagent_stream_invocation(
    context: SubagentStreamInvocationContext,
) -> Token:
    return _current_invocation.set(context)


def reset_current_subagent_stream_invocation(token: Token) -> None:
    _current_invocation.reset(token)


async def subagent_stream_toolkit_middleware(
    kwargs: dict[str, Any],
    next_handler: Callable[..., Any],
) -> AsyncGenerator[Any, None]:
    """Register a stream before spawn starts without changing output."""

    tool_call = kwargs.get("tool_call") or {}
    if tool_call.get("name") != "spawn_subagent":
        async for response in await next_handler(**kwargs):
            yield response
        return

    parent_tool_call_id = str(tool_call.get("id") or "").strip()
    if not parent_tool_call_id:
        async for response in await next_handler(**kwargs):
            yield response
        return

    token: Token | None = None
    stream_id: str | None = None
    manager = get_subagent_stream_manager()
    try:
        from ..agent_context import (
            get_current_agent_id,
            get_current_channel,
            get_current_session_id,
            get_current_user_id,
        )

        binding = SubagentBindingKey(
            agent_id=get_current_agent_id() or "default",
            parent_session_id=get_current_session_id() or "",
            parent_user_id=get_current_user_id() or "",
            parent_channel=get_current_channel() or "console",
            parent_tool_call_id=parent_tool_call_id,
        )
        tool_input = tool_call.get("input") or {}
        handle = await manager.register_or_get(
            binding,
            fork=tool_input.get("fork") is True,
            background=tool_input.get("background") is True,
        )
        stream_id = handle.snapshot.stream_id
        if handle.producer_token:
            token = set_current_subagent_stream_invocation(
                SubagentStreamInvocationContext(
                    stream_id=stream_id,
                    producer_token=handle.producer_token,
                    binding=binding,
                ),
            )
    except Exception:  # noqa: BLE001 - this observer must fail open
        logger.warning(
            "Unable to prepare subagent stream side channel",
            exc_info=True,
        )

    try:
        async for response in await next_handler(**kwargs):
            yield response
    finally:
        if token is not None:
            reset_current_subagent_stream_invocation(token)
        if stream_id:
            try:
                await manager.finish_unstarted(stream_id)
            except Exception:  # noqa: BLE001
                logger.debug(
                    "Unable to finalize unstarted subagent stream",
                    exc_info=True,
                )


def install_subagent_stream_toolkit_middleware(toolkit: Any) -> None:
    """Install the optional middleware; a registration error is non-fatal."""

    try:
        toolkit.register_middleware(subagent_stream_toolkit_middleware)
    except Exception:  # noqa: BLE001
        logger.warning(
            "Unable to install subagent stream middleware",
            exc_info=True,
        )
