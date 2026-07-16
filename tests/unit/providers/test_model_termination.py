# -*- coding: utf-8 -*-
from agentscope.message import TextBlock, ThinkingBlock, ToolUseBlock
from agentscope.model._model_response import ChatResponse

from qwenpaw.providers.model_termination import (
    CONTEXT_LIMIT_NOTICE,
    OUTPUT_LIMIT_NOTICE,
    apply_truncation_notice,
    is_truncation_reason,
)


def test_classify_openai_and_anthropic_truncation_reasons() -> None:
    assert is_truncation_reason("length")
    assert is_truncation_reason("max_tokens")
    assert is_truncation_reason("model_context_window_exceeded")
    assert not is_truncation_reason("end_turn")
    assert not is_truncation_reason(None)


def test_output_limit_notice_removes_tool_calls_and_metadata() -> None:
    response = ChatResponse(
        content=[
            TextBlock(type="text", text="partial answer"),
            ToolUseBlock(
                type="tool_use",
                id="call-incomplete",
                name="dangerous_tool",
                input={"repaired": True},
            ),
        ],
        metadata={"structured": "untrusted"},
    )

    result = apply_truncation_notice(response, "length")

    assert result is response
    assert result.metadata is None
    assert not any(block["type"] == "tool_use" for block in result.content)
    assert result.content[0]["text"].startswith("partial answer")
    assert OUTPUT_LIMIT_NOTICE in result.content[0]["text"]


def test_notice_is_idempotent() -> None:
    response = ChatResponse(
        content=[TextBlock(type="text", text="partial")],
    )

    apply_truncation_notice(response, "max_tokens")
    apply_truncation_notice(response, "max_tokens")

    assert response.content[0]["text"].count(OUTPUT_LIMIT_NOTICE) == 1


def test_context_limit_adds_text_when_response_has_no_text() -> None:
    response = ChatResponse(
        content=[ThinkingBlock(type="thinking", thinking="unfinished")],
    )

    apply_truncation_notice(response, "model_context_window_exceeded")

    text_blocks = [
        block for block in response.content if block["type"] == "text"
    ]
    assert len(text_blocks) == 1
    assert CONTEXT_LIMIT_NOTICE.strip() in text_blocks[0]["text"]


def test_normal_stop_does_not_modify_response() -> None:
    original_content = [TextBlock(type="text", text="complete")]
    response = ChatResponse(
        content=original_content,
        metadata={"valid": True},
    )

    apply_truncation_notice(response, "stop")

    assert response.content is original_content
    assert response.metadata == {"valid": True}
