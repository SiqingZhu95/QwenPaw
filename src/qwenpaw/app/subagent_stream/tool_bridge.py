# -*- coding: utf-8 -*-
"""Small fail-open facade used by the existing spawn_subagent tool."""
from __future__ import annotations

import logging
from typing import Any, Mapping

from .context import get_current_subagent_stream_invocation
from .manager import get_subagent_stream_manager

logger = logging.getLogger(__name__)

STREAM_ID_CONTEXT_KEY = "_qwenpaw_subagent_stream_id"
PRODUCER_TOKEN_CONTEXT_KEY = "_qwenpaw_subagent_producer_token"


class SubagentStreamToolBridge:
    """Adds private routing metadata without changing tool results."""

    async def prepare_child_request_context(
        self,
        base_context: Mapping[str, Any] | None,
        *,
        agent_id: str,
        child_session_id: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = dict(base_context or {})
        invocation = get_current_subagent_stream_invocation()
        if invocation is None:
            return result
        try:
            accepted = await get_subagent_stream_manager().expect_child(
                invocation.stream_id,
                invocation.producer_token,
                agent_id=agent_id,
                child_session_id=child_session_id,
                metadata=metadata,
            )
            if accepted:
                result[STREAM_ID_CONTEXT_KEY] = invocation.stream_id
                result[PRODUCER_TOKEN_CONTEXT_KEY] = invocation.producer_token
        except Exception:  # noqa: BLE001 - never alter spawn behavior
            logger.warning(
                "Unable to attach subagent stream metadata",
                exc_info=True,
            )
        return result

    async def record_background_submission(
        self,
        task_result: Mapping[str, Any],
    ) -> None:
        invocation = get_current_subagent_stream_invocation()
        task_id = task_result.get("task_id")
        if invocation is None or not isinstance(task_id, str) or not task_id:
            return
        try:
            await get_subagent_stream_manager().update_metadata(
                invocation.stream_id,
                invocation.producer_token,
                {"task_id": task_id},
            )
        except Exception:  # noqa: BLE001
            logger.debug(
                "Unable to record subagent background task metadata",
                exc_info=True,
            )


subagent_stream_tool_bridge = SubagentStreamToolBridge()
