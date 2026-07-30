"""Large full-history benchmark.

Future pagination trigger: keep the full-history GET until repeatable
20MB/30MB/50MB measurements show a loading or retained-memory regression.
Then add an additive archived-history cursor API and a formal AgentScope
history-page callback.
"""
from __future__ import annotations

import json
import os
import statistics
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from agentscope.message import Msg, TextBlock
from agentscope.state import AgentState

from qwenpaw.agents.context.scroll.history import HistoryStore
from qwenpaw.agents.context.scroll.serialize import msg_to_entries
from qwenpaw.app.chats.api import get_chat
from qwenpaw.app.chats.models import ChatHistory, ChatSpec
from qwenpaw.app.chats.utils import agentscope_msg_to_message

RUN_LARGE = os.getenv("QWENPAW_RUN_LARGE_HISTORY_BENCHMARKS") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        not RUN_LARGE,
        reason="set QWENPAW_RUN_LARGE_HISTORY_BENCHMARKS=1",
    ),
]


@dataclass
class LargeFixture:
    chat_id: str
    manager: object
    session: object
    workspace: object


def _message(
    *,
    message_id: str,
    name: str,
    role: str,
    text: str,
    created_at: str,
) -> Msg:
    return Msg(
        id=message_id,
        name=name,
        role=role,
        content=[TextBlock(type="text", text=text)],
        created_at=created_at,
    )


def _text_block(message: Msg) -> str:
    return message.content[0].text


def build_large_scroll_fixture(
    tmp_path: Path,
    target_mb: int,
) -> LargeFixture:
    """Build a real Scroll database whose GET JSON is within 1% of target."""
    target_bytes = target_mb * 1024**2
    message_count = 80
    filler_bytes = target_bytes - 128 * 1024
    per_message = filler_bytes // message_count
    archived = [
        _message(
            message_id=f"archived-{index}",
            name="user" if index % 2 == 0 else "assistant",
            role="user" if index % 2 == 0 else "assistant",
            text=f"history-{index:03d}-" + ("x" * per_message),
            created_at="2026-07-30T00:00:00+00:00",
        )
        for index in range(message_count)
    ]
    marker = _message(
        message_id="memory-marker",
        name="memory",
        role="user",
        text="[context compressed]",
        created_at="2026-07-30T00:01:00+00:00",
    )
    tail = _message(
        message_id="live-tail",
        name="assistant",
        role="assistant",
        text="latest-history-message",
        created_at="2026-07-30T00:02:00+00:00",
    )

    def serialized_size() -> int:
        history = ChatHistory(
            messages=agentscope_msg_to_message(
                [*archived, marker, tail],
            ),
            status="idle",
        )
        return len(history.model_dump_json().encode("utf-8"))

    correction = target_bytes - serialized_size()
    if correction >= 0:
        archived[-1].content[0].text = (
            _text_block(archived[-1]) + ("x" * correction)
        )
    else:
        archived[-1].content[0].text = _text_block(
            archived[-1],
        )[:correction]
    assert abs(serialized_size() - target_bytes) <= target_bytes * 0.01

    store = HistoryStore(tmp_path / "history.db")
    first_seq = 0
    last_seq = 0
    for message in archived:
        entries = msg_to_entries(message)
        assert len(entries) == 1
        seq = store.append(
            session_id="large-session",
            agent_id="default",
            entry=entries[0],
            dedup_key=message.id,
        )
        first_seq = first_seq or seq
        last_seq = seq
    store.close()

    state = {
        "agent": {
            "state": AgentState(
                session_id="large-session",
                context=[marker, tail],
            ).model_dump(mode="json"),
            "scroll": {
                "index": {
                    "session_id": "large-session",
                    "agent_id": "default",
                    "tiers": [[{
                        "seq_lo": first_seq,
                        "seq_hi": last_seq,
                        "lines": [],
                    }]],
                },
            },
        },
    }
    chat_id = "00000000-0000-0000-0000-000000000050"
    spec = ChatSpec(
        id=chat_id,
        name="large-history",
        session_id="large-session",
        user_id="large-user",
        channel="console",
    )

    class Manager:
        async def get_chat(self, requested_chat_id: str):
            return spec if requested_chat_id == chat_id else None

    class Session:
        async def get_session_state_dict(self, *args, **kwargs):
            return state

    class Tracker:
        async def get_status(self, requested_chat_id: str):
            return "idle"

    workspace = SimpleNamespace(
        workspace_dir=tmp_path,
        agent_id="default",
        task_tracker=Tracker(),
        config=SimpleNamespace(
            running=SimpleNamespace(
                light_context_config=SimpleNamespace(
                    scroll_config=SimpleNamespace(
                        db_filename="history.db",
                    ),
                ),
            ),
        ),
    )
    return LargeFixture(chat_id, Manager(), Session(), workspace)


@pytest.mark.parametrize("target_mb", [20, 30, 50])
@pytest.mark.asyncio
async def test_full_history_get_large_payload(
    target_mb,
    tmp_path,
):
    """Measure end-to-end GET reconstruction and response serialization."""
    fixture = build_large_scroll_fixture(tmp_path, target_mb)

    tracemalloc.start()
    samples_ms = []
    result = None
    serialized = b""
    for _ in range(3):
        started = time.perf_counter()
        result = await get_chat(
            fixture.chat_id,
            mgr=fixture.manager,
            session=fixture.session,
            workspace=fixture.workspace,
        )
        serialized = result.model_dump_json().encode("utf-8")
        samples_ms.append((time.perf_counter() - started) * 1000)
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert result is not None
    target_bytes = target_mb * 1024**2
    assert abs(len(serialized) - target_bytes) <= target_bytes * 0.01
    texts = [
        content.text
        for message in result.messages
        for content in message.content
        if content.type == "text"
    ]
    assert "[context compressed]" in texts
    assert texts[-1] == "latest-history-message"
    assert max(samples_ms) < 30_000
    print(
        "COMPACT_CHAT_HISTORY_BACKEND_METRIC "
        + json.dumps({
            "target_mb": target_mb,
            "payload_bytes": len(serialized),
            "median_ms": statistics.median(samples_ms),
            "max_ms": max(samples_ms),
            "peak_python_bytes": peak_bytes,
        }),
    )
