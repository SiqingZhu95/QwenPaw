# -*- coding: utf-8 -*-
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from qwenpaw.providers.anthropic_chat_model_compat import (
    AnthropicChatModelCompat,
)
from qwenpaw.providers.model_termination import (
    CONTEXT_LIMIT_NOTICE,
    OUTPUT_LIMIT_NOTICE,
)


class FakeAsyncStream:
    def __init__(self, events: list[Any]) -> None:
        self._events = events
        self._iterator: Any | None = None

    def __aiter__(self) -> "FakeAsyncStream":
        self._iterator = iter(self._events)
        return self

    async def __anext__(self) -> Any:
        assert self._iterator is not None
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


def _message_start() -> Any:
    return SimpleNamespace(
        type="message_start",
        message=SimpleNamespace(
            id="msg-test",
            usage=SimpleNamespace(input_tokens=10, output_tokens=0),
        ),
    )


def _text_delta(text: str) -> Any:
    return SimpleNamespace(
        type="content_block_delta",
        index=0,
        delta=SimpleNamespace(type="text_delta", text=text),
    )


def _message_delta(stop_reason: str) -> Any:
    return SimpleNamespace(
        type="message_delta",
        delta=SimpleNamespace(stop_reason=stop_reason),
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


async def _parse_stream(
    model: AnthropicChatModelCompat,
    events: list[Any],
) -> list[Any]:
    responses = []
    async for response in model._parse_anthropic_stream_completion_response(
        datetime.now(),
        FakeAsyncStream(events),
    ):
        responses.append(response)
    return responses


@pytest.mark.parametrize(
    ("stop_reason", "expected_notice"),
    [
        ("max_tokens", OUTPUT_LIMIT_NOTICE),
        ("model_context_window_exceeded", CONTEXT_LIMIT_NOTICE),
    ],
)
async def test_truncated_text_stream_emits_terminal_notice(
    stop_reason: str,
    expected_notice: str,
) -> None:
    model = AnthropicChatModelCompat(
        "dummy",
        api_key="ant-test",
        stream=True,
        stream_tool_parsing=False,
    )

    responses = await _parse_stream(
        model,
        [
            _message_start(),
            _text_delta("partial"),
            _message_delta(stop_reason),
        ],
    )

    assert len(responses) == 2
    assert responses[-1].content[0]["text"].startswith("partial")
    assert expected_notice in responses[-1].content[0]["text"]


async def test_truncated_stream_drops_repaired_tool_call() -> None:
    model = AnthropicChatModelCompat(
        "dummy",
        api_key="ant-test",
        stream=True,
        stream_tool_parsing=False,
    )
    events = [
        _message_start(),
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=SimpleNamespace(
                type="tool_use",
                id="tool-partial",
                name="write_file",
            ),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(
                type="input_json_delta",
                partial_json='{"path":"unsafe.txt"',
            ),
        ),
        _message_delta("max_tokens"),
    ]

    responses = await _parse_stream(model, events)

    assert responses
    assert not any(
        block["type"] == "tool_use" for block in responses[-1].content
    )
    assert OUTPUT_LIMIT_NOTICE.strip() in responses[-1].content[-1]["text"]


async def test_normal_stream_stop_does_not_add_notice() -> None:
    model = AnthropicChatModelCompat(
        "dummy",
        api_key="ant-test",
        stream=True,
    )

    responses = await _parse_stream(
        model,
        [
            _message_start(),
            _text_delta("complete"),
            _message_delta("end_turn"),
        ],
    )

    assert len(responses) == 1
    assert responses[0].content[0]["text"] == "complete"
