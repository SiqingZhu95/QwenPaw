# -*- coding: utf-8 -*-
"""Anthropic model wrapper that preserves response termination reasons."""

from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncGenerator, Type

from agentscope.model import AnthropicChatModel
from agentscope.model._model_response import ChatResponse
from pydantic import BaseModel

from .model_termination import (
    apply_truncation_notice,
    is_truncation_reason,
)


class _TrackedAnthropicStream:
    """Proxy an Anthropic stream and retain ``message_delta.stop_reason``."""

    def __init__(self, stream: Any) -> None:
        self._iterator = stream.__aiter__()
        self.stop_reason: str | None = None

    def __aiter__(self) -> "_TrackedAnthropicStream":
        return self

    async def __anext__(self) -> Any:
        event = await self._iterator.__anext__()
        if getattr(event, "type", None) == "message_delta":
            delta = getattr(event, "delta", None)
            reason = getattr(delta, "stop_reason", None)
            if isinstance(reason, str) and reason:
                self.stop_reason = reason
        return event


class AnthropicChatModelCompat(AnthropicChatModel):
    """AgentScope Anthropic model with safe truncation handling."""

    async def _parse_anthropic_stream_completion_response(
        self,
        start_datetime: datetime,
        response: Any,
        structured_model: Type[BaseModel] | None = None,
    ) -> AsyncGenerator[ChatResponse, None]:
        tracked = _TrackedAnthropicStream(response)
        last_response: ChatResponse | None = None

        async for parsed in (
            super()._parse_anthropic_stream_completion_response(
                start_datetime=start_datetime,
                response=tracked,
                structured_model=structured_model,
            )
        ):
            last_response = parsed
            yield parsed

        # AgentScope does not yield on Anthropic's terminal message_delta for
        # plain-text streams. Emit one final cumulative snapshot so the ReAct
        # layer and the UI both receive the notice.
        if is_truncation_reason(tracked.stop_reason):
            if last_response is None:
                terminal = ChatResponse(content=[])
            else:
                terminal = ChatResponse(
                    content=list(last_response.content),
                    id=last_response.id,
                    usage=last_response.usage,
                )
            yield apply_truncation_notice(terminal, tracked.stop_reason)
