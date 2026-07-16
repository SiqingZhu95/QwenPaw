# -*- coding: utf-8 -*-
"""Normalize model termination reasons into user-visible notices."""

from __future__ import annotations

import logging

from agentscope.message import TextBlock
from agentscope.model._model_response import ChatResponse

logger = logging.getLogger(__name__)

OUTPUT_LIMIT_NOTICE = (
    "\n\n---\n"
    "⚠️ 本次回复已达到模型输出长度上限，内容可能不完整。"
    "你可以发送“继续”让我接着回答，或在模型设置中提高最大输出 token 数。\n\n"
    "⚠️ This response reached the model output limit and may be incomplete. "
    "Send “continue” to resume, or increase the model's maximum output tokens."
)

CONTEXT_LIMIT_NOTICE = (
    "\n\n---\n"
    "⚠️ 本次回复因模型上下文窗口已满而被截断，内容可能不完整。"
    "建议先使用 /compact 压缩上下文；如果仍然失败，请使用 /new 或 /clear。\n\n"
    "⚠️ This response was truncated because the model context window is "
    "full. Try /compact first, or use /new or /clear if necessary."
)

CONTEXT_LIMIT_ERROR_NOTICE = (
    "⚠️ 当前会话内容已超过模型可接受的上下文窗口，本次请求未能完成。"
    "请先使用 /compact 压缩上下文；如果仍然失败，请使用 /new 或 /clear 后重试。\n\n"
    "⚠️ This conversation exceeds the model's context window, so the request "
    "could not be completed. Try /compact first, or use /new or /clear and "
    "retry."
)

_TRUNCATION_NOTICES = {
    "length": OUTPUT_LIMIT_NOTICE,
    "max_tokens": OUTPUT_LIMIT_NOTICE,
    "model_context_window_exceeded": CONTEXT_LIMIT_NOTICE,
}


def is_truncation_reason(reason: object) -> bool:
    """Return whether *reason* represents an incomplete response."""
    return isinstance(reason, str) and (
        reason.strip().lower() in _TRUNCATION_NOTICES
    )


def apply_truncation_notice(
    response: ChatResponse,
    reason: object,
) -> ChatResponse:
    """Append a notice and discard unsafe output after truncation."""
    if not isinstance(reason, str):
        return response

    notice = _TRUNCATION_NOTICES.get(reason.strip().lower())
    if notice is None:
        return response

    # AgentScope repairs incomplete tool JSON at stream end.  Never execute
    # or trust structured output from a truncated response.
    original_content_count = len(response.content)
    content = [
        block
        for block in response.content
        if block.get("type") != "tool_use"
    ]
    removed_tool_calls = original_content_count - len(content)

    text_block = next(
        (
            block
            for block in reversed(content)
            if block.get("type") == "text"
        ),
        None,
    )
    if text_block is None:
        content.append(TextBlock(type="text", text=notice.lstrip()))
    else:
        text = str(text_block.get("text") or "")
        if notice not in text:
            text_block["text"] = text.rstrip() + notice

    response.content = content
    # A truncated structured output must not be treated as validated data.
    response.metadata = None

    logger.warning(
        "Model response truncated: reason=%s removed_tool_calls=%d",
        reason,
        removed_tool_calls,
    )
    return response
