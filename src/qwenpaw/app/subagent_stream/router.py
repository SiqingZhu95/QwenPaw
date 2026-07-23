# -*- coding: utf-8 -*-
"""Read-only binding and SSE API for observable spawn_subagent runs."""
from __future__ import annotations

import asyncio
import json
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from .manager import get_subagent_stream_manager
from .models import SubagentBindingKey, SubagentStreamOwner

router = APIRouter(prefix="/subagent-streams", tags=["subagent-streams"])


class SubagentStreamOwnerRequest(BaseModel):
    agent_id: str = Field(min_length=1)
    parent_session_id: str = Field(min_length=1)
    parent_user_id: str = ""
    parent_channel: str = "console"

    def owner(self) -> SubagentStreamOwner:
        return SubagentStreamOwner(
            agent_id=self.agent_id,
            parent_session_id=self.parent_session_id,
            parent_user_id=self.parent_user_id,
            parent_channel=self.parent_channel,
        )


class ResolveSubagentStreamRequest(SubagentStreamOwnerRequest):
    parent_tool_call_id: str = Field(min_length=1)
    wait_timeout_ms: int = Field(default=1000, ge=0, le=1000)

    def binding(self) -> SubagentBindingKey:
        owner = self.owner()
        return SubagentBindingKey(
            agent_id=owner.agent_id,
            parent_session_id=owner.parent_session_id,
            parent_user_id=owner.parent_user_id,
            parent_channel=owner.parent_channel,
            parent_tool_call_id=self.parent_tool_call_id,
        )


class SubscribeSubagentStreamRequest(SubagentStreamOwnerRequest):
    after_sequence: int = Field(default=0, ge=0)


async def _validate_agent(request: Request, agent_id: str) -> None:
    from ..agent_context import get_agent_for_request

    workspace = await get_agent_for_request(request, agent_id=agent_id)
    if workspace.agent_id != agent_id:
        raise HTTPException(status_code=403, detail="stream_forbidden")


@router.post("/resolve")
async def resolve_subagent_stream(
    request: Request,
    body: ResolveSubagentStreamRequest,
) -> dict:
    await _validate_agent(request, body.agent_id)
    snapshot = await get_subagent_stream_manager().wait_for_binding(
        body.binding(),
        timeout_seconds=body.wait_timeout_ms / 1000,
    )
    return {
        "found": snapshot is not None,
        "retry_after_ms": None if snapshot is not None else 500,
        "stream": snapshot.to_dict() if snapshot is not None else None,
    }


@router.post("/{stream_id}/metadata")
async def get_subagent_stream_metadata(
    stream_id: str,
    request: Request,
    body: SubagentStreamOwnerRequest,
) -> dict:
    await _validate_agent(request, body.agent_id)
    snapshot = await get_subagent_stream_manager().get_snapshot(
        stream_id,
        body.owner(),
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="stream_not_found")
    return snapshot.to_dict()


def _format_sse(event: dict) -> str:
    stream_id = event.get("stream_id", "")
    sequence = event.get("sequence", 0)
    return (
        f"id: {stream_id}:{sequence}\n"
        "event: subagent\n"
        f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    )


@router.post("/{stream_id}/events")
async def stream_subagent_events(
    stream_id: str,
    request: Request,
    body: SubscribeSubagentStreamRequest,
) -> StreamingResponse:
    await _validate_agent(request, body.agent_id)
    manager = get_subagent_stream_manager()
    subscription = await manager.subscribe(
        stream_id,
        body.owner(),
        after_sequence=body.after_sequence,
    )
    if subscription is None:
        raise HTTPException(status_code=404, detail="stream_not_found")

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            yield _format_sse(
                {
                    "stream_id": stream_id,
                    "sequence": body.after_sequence,
                    "kind": "metadata",
                    "payload": {
                        "connected": True,
                        "reset_required": subscription.reset_required,
                    },
                },
            )
            for event in subscription.replay:
                yield _format_sse(event.to_dict())
            while not subscription.closed:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(
                        subscription.queue.get(),
                        timeout=15.0,
                    )
                    yield _format_sse(event.to_dict())
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
            if subscription.close_reason == "slow_consumer":
                yield _format_sse(
                    {
                        "stream_id": stream_id,
                        "sequence": body.after_sequence,
                        "kind": "error",
                        "payload": {"code": "slow_consumer"},
                    },
                )
        finally:
            await manager.detach(subscription.subscription_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
