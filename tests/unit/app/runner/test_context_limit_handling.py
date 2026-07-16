# -*- coding: utf-8 -*-
from types import SimpleNamespace

from agentscope.message import Msg

import qwenpaw.app.runner.runner as runner_module
from qwenpaw.app.runner.runner import AgentRunner
from qwenpaw.app.runner.runner import _build_context_limit_notice
from qwenpaw.providers.model_termination import CONTEXT_LIMIT_ERROR_NOTICE


class FakeMemory:
    def __init__(self) -> None:
        self.messages = []

    async def add(self, message) -> None:
        self.messages.append(message)


async def test_context_limit_notice_is_visible_and_persisted() -> None:
    memory = FakeMemory()
    agent = SimpleNamespace(memory=memory)

    notice = await _build_context_limit_notice(agent, "QwenPaw")

    assert notice.role == "assistant"
    assert notice.name == "QwenPaw"
    assert notice.get_text_content() == CONTEXT_LIMIT_ERROR_NOTICE
    assert memory.messages == [notice]


async def test_query_handler_returns_notice_for_provider_context_error(
    monkeypatch,
) -> None:
    class FakeAPIError(Exception):
        status_code = 400
        body = {
            "error": {
                "code": "context_length_exceeded",
                "message": "maximum context length exceeded",
            },
        }

    def raise_context_error(_agent_id: str) -> None:
        raise FakeAPIError("maximum context length exceeded")

    monkeypatch.setattr(
        runner_module,
        "load_agent_config",
        raise_context_error,
    )
    runner = AgentRunner(agent_id="test-agent")
    request = SimpleNamespace(
        session_id="session-test",
        user_id="user-test",
        channel="console",
        channel_meta={},
    )
    messages = [Msg("user", "hello", "user")]

    responses = [
        item async for item in runner.query_handler(messages, request=request)
    ]

    assert len(responses) == 1
    notice, is_last = responses[0]
    assert is_last is True
    assert notice.get_text_content() == CONTEXT_LIMIT_ERROR_NOTICE
