# Compact Chat History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变 `GET /chats/{id}` 合约和模型上下文的前提下，恢复 Scroll 压缩前历史，并用 20MB、30MB、50MB 规格验证后端接口、前端转换、页面打开和内存表现。

**Architecture:** 新增一个只读 Scroll 展示适配器，从持久化 checkpoint 提取当前有效 `seq` 范围，以 SQLite read-only 模式读取并恢复 AgentScope `Msg`；GET 将归档消息放在现有压缩提示和 live tail 之前。前端协议与 AgentScope 组件不变，只增加可显式启用的大规格基准；若指标达到已批准的退化门槛，再单独设计分页。

**Tech Stack:** Python 3.10+、FastAPI、Pydantic v2、SQLite、pytest/pytest-asyncio、TypeScript、Vitest、React、Playwright/Chromium CDP。

## Global Constraints

- 代码基线为 QwenPaw v2.0.1，默认上下文策略为 `scroll`。
- 所有本需求文档保存在 `docs/0729-compact-chat-history/`。
- 保持 `GET /chats/{id}` 的路径、参数和 `ChatHistory { messages, status }` 响应结构不变。
- 只恢复持久化 Scroll checkpoint 精确覆盖的历史；不得按 session 全表恢复。
- 非 Scroll、无有效 checkpoint、缺库、坏库或 retention 缺行时保持现有页面可用。
- 展示恢复不得修改 `AgentState.context`、session JSON、Scroll checkpoint 或 `history.db`。
- SQLite 展示读取必须使用 read-only URI，不复用会建表、迁移或 quarantine 的 `HistoryStore` 读取路径。
- 前端继续由 AgentScope Runtime WebUI 每次渲染 10 条历史；本次不新增分页接口或游标。
- 20MB、30MB、50MB 基准使用环境变量 `QWENPAW_RUN_LARGE_HISTORY_BENCHMARKS=1` 显式启用，避免普通 PR 测试承担 100MB 以上测试数据成本。
- 大规格测试文件必须保留 future pagination trigger 注释；只有可复现指标越过已批准门槛才实现分页。

---

## File Structure

- Create `src/qwenpaw/app/chats/scroll_history.py`: checkpoint 范围解析、只读 SQLite 查询、行到 AgentScope `Msg` 的展示适配。
- Modify `src/qwenpaw/app/chats/api.py`: 在 GET 展示链路中异步卸载只读恢复，并组装归档 + 当前 context。
- Create `tests/unit/app/chats/test_scroll_history.py`: 范围、反序列化、隔离、只读和故障降级单元测试。
- Create `tests/unit/app/chats/test_api_scroll_history.py`: GET 集成、策略旁路、顺序和故障降级测试。
- Create `tests/integration/test_compact_chat_history_large.py`: 20/30/50MB 后端接口与峰值 Python 内存基准。
- Modify `console/src/pages/Chat/tests/testLargeSession.test.ts`: 20/30/50MB UTF-8 payload 转换与 Node heap 基准。
- Create `e2e/tests/test_compact_chat_history_large.py`: mock 大响应后的真实 Chat 页面打开、10 条 DOM 窗口与 Chromium heap 基准。
- Create `docs/0729-compact-chat-history/06-validation-report.md`: 保存验证环境、指标、分页决策和长期提示。

---

### Task 1: Scroll checkpoint range parser

**Files:**
- Create: `src/qwenpaw/app/chats/scroll_history.py`
- Create: `tests/unit/app/chats/test_scroll_history.py`

**Interfaces:**
- Consumes: session JSON 中的 `agent.scroll` mapping，以及当前 `chat_spec.session_id`。
- Produces: `extract_index_ranges(scroll_state: Mapping[str, Any], *, session_id: str) -> list[tuple[int, int]]`。

- [ ] **Step 1: Write failing range tests**

在 `tests/unit/app/chats/test_scroll_history.py` 添加：

```python
from qwenpaw.app.chats.scroll_history import extract_index_ranges


def test_extract_index_ranges_merges_tiers_and_adjacent_spans():
    scroll = {
        "index": {
            "session_id": "session-1",
            "tiers": [
                [
                    {"seq_lo": 20, "seq_hi": 29, "lines": []},
                    {"seq_lo": 30, "seq_hi": 35, "lines": []},
                ],
                [{"seq_lo": 1, "seq_hi": 19, "lines": []}],
            ],
        },
    }

    assert extract_index_ranges(scroll, session_id="session-1") == [(1, 35)]


def test_extract_index_ranges_accepts_legacy_levels():
    scroll = {
        "index": {
            "session_id": "session-1",
            "levels": [[{"seq_lo": 5, "seq_hi": 8, "lines": []}]],
        },
    }

    assert extract_index_ranges(scroll, session_id="session-1") == [(5, 8)]


def test_extract_index_ranges_rejects_other_session_and_invalid_values():
    other = {
        "index": {
            "session_id": "other",
            "tiers": [[{"seq_lo": 1, "seq_hi": 10, "lines": []}]],
        },
    }
    invalid = {
        "index": {
            "session_id": "session-1",
            "tiers": [[
                {"seq_lo": True, "seq_hi": 3, "lines": []},
                {"seq_lo": 9, "seq_hi": 4, "lines": []},
                {"seq_lo": "1", "seq_hi": 2, "lines": []},
            ]],
        },
    }

    assert extract_index_ranges(other, session_id="session-1") == []
    assert extract_index_ranges(invalid, session_id="session-1") == []
```

- [ ] **Step 2: Run the tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/app/chats/test_scroll_history.py -v
```

Expected: collection fails because `qwenpaw.app.chats.scroll_history` does not exist.

- [ ] **Step 3: Implement the minimal parser**

在 `src/qwenpaw/app/chats/scroll_history.py` 添加：

```python
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _is_seq(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def extract_index_ranges(
    scroll_state: Mapping[str, Any],
    *,
    session_id: str,
) -> list[tuple[int, int]]:
    index = scroll_state.get("index")
    if not isinstance(index, Mapping):
        return []
    indexed_session = index.get("session_id")
    if indexed_session and indexed_session != session_id:
        return []
    tiers = index.get("tiers", index.get("levels", []))
    if not isinstance(tiers, list):
        return []

    ranges: list[tuple[int, int]] = []
    for tier in tiers:
        if not isinstance(tier, list):
            continue
        for block in tier:
            if not isinstance(block, Mapping):
                continue
            lo, hi = block.get("seq_lo"), block.get("seq_hi")
            if _is_seq(lo) and _is_seq(hi) and lo <= hi:
                ranges.append((lo, hi))

    merged: list[tuple[int, int]] = []
    for lo, hi in sorted(ranges):
        if merged and lo <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged
```

- [ ] **Step 4: Run the range tests to verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/app/chats/test_scroll_history.py -v
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit Task 1**

```powershell
git add -- src/qwenpaw/app/chats/scroll_history.py tests/unit/app/chats/test_scroll_history.py
git commit -m "test: define scroll history display ranges"
```

---

### Task 2: Read-only row reconstruction

**Files:**
- Modify: `src/qwenpaw/app/chats/scroll_history.py`
- Modify: `tests/unit/app/chats/test_scroll_history.py`

**Interfaces:**
- Consumes: `Path` to workspace, session/agent identity, checkpoint ranges, SQLite rows from `conversation_history`.
- Produces:
  - `history_rows_to_messages(rows: Sequence[Mapping[str, Any]]) -> list[Msg]`
  - `read_archived_messages(*, workspace_dir: Path, db_filename: str, session_id: str, agent_id: str | None, scroll_state: Mapping[str, Any]) -> list[Msg]`

- [ ] **Step 1: Write failing reconstruction and isolation tests**

使用现有 `HistoryStore` 只负责创建临时测试库，测试目标 reader 必须另行以 read-only 模式打开：

```python
import json
from pathlib import Path

from qwenpaw.agents.context.scroll.history import HistoryStore
from qwenpaw.agents.context.types import LogEntry
from qwenpaw.app.chats.scroll_history import read_archived_messages


def _append_text(
    store: HistoryStore,
    *,
    session_id: str,
    text: str,
    role: str,
    dedup_key: str,
) -> int:
    return store.append(
        session_id=session_id,
        agent_id="default",
        dedup_key=dedup_key,
        entry=LogEntry(
            kind="context_msg" if role == "user" else "model_turn",
            role=role,
            content=text,
            blocks=[{"type": "text", "text": text}],
            metadata={"source": "test"},
            created_at="2026-07-30T00:00:00+00:00",
        ),
    )


def test_read_archived_messages_uses_only_indexed_session_ranges(tmp_path: Path):
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path)
    before = _append_text(
        store,
        session_id="session-1",
        text="before-clear",
        role="user",
        dedup_key="before",
    )
    first = _append_text(
        store,
        session_id="session-1",
        text="archived-user",
        role="user",
        dedup_key="user",
    )
    last = _append_text(
        store,
        session_id="session-1",
        text="archived-assistant",
        role="assistant",
        dedup_key="assistant",
    )
    _append_text(
        store,
        session_id="other-session",
        text="must-not-leak",
        role="user",
        dedup_key="other",
    )
    store.close()
    mtime = db_path.stat().st_mtime_ns

    messages = read_archived_messages(
        workspace_dir=tmp_path,
        db_filename="history.db",
        session_id="session-1",
        agent_id="default",
        scroll_state={
            "index": {
                "session_id": "session-1",
                "tiers": [[{
                    "seq_lo": first,
                    "seq_hi": last,
                    "lines": [],
                }]],
            },
        },
    )

    assert [message.get_text_content() for message in messages] == [
        "archived-user",
        "archived-assistant",
    ]
    assert before < first
    assert db_path.stat().st_mtime_ns == mtime


def test_read_archived_messages_restores_tool_result_and_legacy_text(tmp_path: Path):
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path)
    first = store.append(
        session_id="session-1",
        agent_id="default",
        dedup_key="legacy",
        entry=LogEntry(
            kind="model_turn",
            role="assistant",
            content="legacy-text",
            blocks=None,
        ),
    )
    last = store.append(
        session_id="session-1",
        agent_id="default",
        dedup_key="tool-1",
        entry=LogEntry(
            kind="tool_result",
            role="assistant",
            name="search",
            content="tool-output",
            tool_call_id="tool-1",
            tool_state="success",
            blocks=[{
                "type": "tool_result",
                "id": "tool-1",
                "name": "search",
                "output": "tool-output",
            }],
        ),
    )
    store.close()

    messages = read_archived_messages(
        workspace_dir=tmp_path,
        db_filename="history.db",
        session_id="session-1",
        agent_id="default",
        scroll_state={
            "index": {
                "session_id": "session-1",
                "tiers": [[{"seq_lo": first, "seq_hi": last, "lines": []}]],
            },
        },
    )

    assert messages[0].get_text_content() == "legacy-text"
    assert messages[1].content[0].type == "tool_result"
    assert messages[1].content[0].id == "tool-1"


@pytest.mark.parametrize("database_state", ["missing", "corrupt", "no-table"])
def test_read_archived_messages_fails_open_for_unavailable_database(
    tmp_path: Path,
    database_state: str,
):
    db_path = tmp_path / "history.db"
    if database_state == "corrupt":
        db_path.write_bytes(b"not-a-sqlite-database")
    elif database_state == "no-table":
        sqlite3.connect(db_path).close()

    messages = read_archived_messages(
        workspace_dir=tmp_path,
        db_filename="history.db",
        session_id="session-1",
        agent_id="default",
        scroll_state={
            "index": {
                "session_id": "session-1",
                "tiers": [[{"seq_lo": 1, "seq_hi": 2, "lines": []}]],
            },
        },
    )

    assert messages == []


def test_history_rows_to_messages_falls_back_from_invalid_blocks():
    messages = history_rows_to_messages([{
        "seq": 7,
        "kind": "model_turn",
        "role": "assistant",
        "name": None,
        "content": "recoverable-text",
        "tool_call_id": None,
        "tool_state": None,
        "blocks": "{invalid-json",
        "metadata": "{invalid-json",
        "created_at": None,
        "dedup_key": None,
    }])

    assert len(messages) == 1
    assert messages[0].id == "scroll-history-7"
    assert messages[0].get_text_content() == "recoverable-text"
```

测试文件相应导入 `sqlite3` 与 `history_rows_to_messages`。

- [ ] **Step 2: Run the focused tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/app/chats/test_scroll_history.py -v
```

Expected: tests fail because `read_archived_messages` is not defined.

- [ ] **Step 3: Implement row decoding and read-only query**

核心实现：

```python
import json
import logging
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from agentscope.message import Msg

logger = logging.getLogger(__name__)

_SELECT_COLUMNS = (
    "seq, kind, role, name, content, tool_call_id, tool_state, "
    "blocks, metadata, created_at, dedup_key"
)


def _json_value(raw: Any, fallback: Any) -> Any:
    if raw in (None, ""):
        return fallback
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return fallback


def history_rows_to_messages(
    rows: Sequence[Mapping[str, Any]],
) -> list[Msg]:
    messages: list[Msg] = []
    for row in rows:
        try:
            role = row["role"] if row["role"] in {
                "user",
                "assistant",
                "system",
            } else "assistant"
            blocks = _json_value(row["blocks"], None)
            if not isinstance(blocks, list) or not blocks:
                if row["kind"] == "tool_result":
                    blocks = [{
                        "type": "tool_result",
                        "id": (
                            row["tool_call_id"]
                            or row["dedup_key"]
                            or f"scroll-tool-{row['seq']}"
                        ),
                        "name": row["name"] or "tool",
                        "output": row["content"] or "",
                        "state": row["tool_state"] or "success",
                    }]
                else:
                    blocks = [{
                        "type": "text",
                        "text": row["content"] or "",
                    }]
            metadata = _json_value(row["metadata"], {})
            if not isinstance(metadata, dict):
                metadata = {}
            messages.append(
                Msg(
                    id=row["dedup_key"] or f"scroll-history-{row['seq']}",
                    name=row["name"] or role,
                    role=role,
                    content=blocks,
                    metadata=metadata,
                    created_at=(
                        row["created_at"]
                        or "1970-01-01T00:00:00+00:00"
                    ),
                ),
            )
        except Exception:
            logger.warning(
                "Skipping unreadable Scroll history row seq=%r",
                row.get("seq") if isinstance(row, Mapping) else None,
                exc_info=True,
            )
    return messages


def read_archived_messages(
    *,
    workspace_dir: Path,
    db_filename: str,
    session_id: str,
    agent_id: str | None,
    scroll_state: Mapping[str, Any],
) -> list[Msg]:
    ranges = extract_index_ranges(scroll_state, session_id=session_id)
    if not ranges:
        return []
    db_path = Path(workspace_dir) / db_filename
    if not db_path.is_file():
        return []

    range_sql = " OR ".join("(seq BETWEEN ? AND ?)" for _ in ranges)
    params: list[Any] = [session_id, agent_id, agent_id]
    params.extend(value for pair in ranges for value in pair)
    sql = (
        f"SELECT {_SELECT_COLUMNS} FROM conversation_history "
        "WHERE session_id = ? "
        "AND (? IS NULL OR agent_id IS NULL OR agent_id = ?) "
        f"AND ({range_sql}) ORDER BY seq ASC"
    )
    try:
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1.0) as connection:
            connection.row_factory = sqlite3.Row
            rows = [
                dict(row)
                for row in connection.execute(sql, params).fetchall()
            ]
        return history_rows_to_messages(rows)
    except (OSError, sqlite3.Error):
        logger.warning(
            "Unable to read archived Scroll history from %s",
            db_path,
            exc_info=True,
        )
        return []
```

- [ ] **Step 4: Run unit tests and inspect read-only invariants**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/app/chats/test_scroll_history.py -v
```

Expected: range, reconstruction, isolation, missing/corrupt DB and mtime tests pass.

- [ ] **Step 5: Commit Task 2**

```powershell
git add -- src/qwenpaw/app/chats/scroll_history.py tests/unit/app/chats/test_scroll_history.py
git commit -m "feat: read archived scroll history safely"
```

---

### Task 3: Integrate archived history into GET

**Files:**
- Modify: `src/qwenpaw/app/chats/api.py:232-290`
- Create: `tests/unit/app/chats/test_api_scroll_history.py`

**Interfaces:**
- Consumes: Task 2 `read_archived_messages(...)`.
- Produces: unchanged FastAPI `get_chat(...) -> ChatHistory`, with valid Scroll archived messages prepended.

- [ ] **Step 1: Write failing GET tests with fakes**

测试必须覆盖：

```python
from pathlib import Path
from types import SimpleNamespace

import pytest
from agentscope.message import Msg
from agentscope.state import AgentState

from qwenpaw.app.chats.api import get_chat
from qwenpaw.app.chats.models import ChatSpec


class FakeManager:
    async def get_chat(self, chat_id: str):
        return ChatSpec(
            id=chat_id,
            name="test",
            session_id="session-1",
            user_id="user-1",
            channel="console",
        )


class FakeSession:
    def __init__(self, state: dict):
        self.state = state

    async def get_session_state_dict(self, *args, **kwargs):
        return self.state


class FakeTaskTracker:
    async def get_status(self, chat_id: str):
        return "idle"


def fake_workspace(tmp_path: Path):
    scroll_config = SimpleNamespace(db_filename="history.db")
    light = SimpleNamespace(scroll_config=scroll_config)
    running = SimpleNamespace(light_context_config=light)
    return SimpleNamespace(
        workspace_dir=tmp_path,
        agent_id="default",
        config=SimpleNamespace(running=running),
        task_tracker=FakeTaskTracker(),
    )


@pytest.mark.asyncio
async def test_get_chat_prepends_archived_before_marker_and_tail(
    tmp_path,
    monkeypatch,
):
    marker = Msg(
        name="memory",
        role="user",
        content="[context compressed]",
    )
    tail = Msg(name="assistant", role="assistant", content="live-tail")
    state = {
        "agent": {
            "state": AgentState(
                session_id="session-1",
                context=[marker, tail],
            ).model_dump(mode="json"),
            "scroll": {
                "index": {
                    "session_id": "session-1",
                    "tiers": [[{
                        "seq_lo": 1,
                        "seq_hi": 2,
                        "lines": [],
                    }]],
                },
            },
        },
    }
    archived = [
        Msg(name="user", role="user", content="archived-user"),
        Msg(
            name="assistant",
            role="assistant",
            content="archived-assistant",
        ),
    ]
    captured = {}

    def fake_reader(**kwargs):
        captured.update(kwargs)
        return archived

    monkeypatch.setattr(
        "qwenpaw.app.chats.api.read_archived_messages",
        fake_reader,
    )

    result = await get_chat(
        "00000000-0000-0000-0000-000000000001",
        mgr=FakeManager(),
        session=FakeSession(state),
        workspace=fake_workspace(tmp_path),
    )

    texts = [
        content.text
        for message in result.messages
        for content in message.content
        if content.type == "text"
    ]
    assert texts == [
        "archived-user",
        "archived-assistant",
        "[context compressed]",
        "live-tail",
    ]
    assert captured["workspace_dir"] == tmp_path
    assert captured["db_filename"] == "history.db"
    assert captured["session_id"] == "session-1"
    assert captured["agent_id"] == "default"


@pytest.mark.asyncio
async def test_get_chat_without_scroll_keeps_current_context(
    tmp_path,
    monkeypatch,
):
    tail = Msg(name="assistant", role="assistant", content="native-tail")
    state = {
        "agent": {
            "state": AgentState(
                session_id="session-1",
                context=[tail],
            ).model_dump(mode="json"),
        },
    }

    def unexpected_reader(**kwargs):
        raise AssertionError("reader must not run without a Scroll checkpoint")

    monkeypatch.setattr(
        "qwenpaw.app.chats.api.read_archived_messages",
        unexpected_reader,
    )
    result = await get_chat(
        "00000000-0000-0000-0000-000000000001",
        mgr=FakeManager(),
        session=FakeSession(state),
        workspace=fake_workspace(tmp_path),
    )

    assert len(result.messages) == 1
    assert result.messages[0].content[0].text == "native-tail"


@pytest.mark.asyncio
async def test_get_chat_empty_archive_keeps_marker_and_status(
    tmp_path,
    monkeypatch,
):
    marker = Msg(
        name="memory",
        role="user",
        content="[context compressed]",
    )
    state = {
        "agent": {
            "state": AgentState(
                session_id="session-1",
                context=[marker],
            ).model_dump(mode="json"),
            "scroll": {
                "index": {
                    "session_id": "session-1",
                    "tiers": [[{
                        "seq_lo": 1,
                        "seq_hi": 2,
                        "lines": [],
                    }]],
                },
            },
        },
    }
    monkeypatch.setattr(
        "qwenpaw.app.chats.api.read_archived_messages",
        lambda **kwargs: [],
    )

    result = await get_chat(
        "00000000-0000-0000-0000-000000000001",
        mgr=FakeManager(),
        session=FakeSession(state),
        workspace=fake_workspace(tmp_path),
    )

    assert result.status == "idle"
    assert result.messages[0].content[0].text == "[context compressed]"


@pytest.mark.asyncio
async def test_get_chat_invalid_agent_state_uses_legacy_without_scroll(
    tmp_path,
    monkeypatch,
):
    legacy = Msg(name="user", role="user", content="legacy-message")
    state = {
        "agent": {
            "state": {"context": "invalid"},
            "memory": {
                "content": [[legacy.model_dump(mode="json"), []]],
                "_compressed_summary": "legacy-summary",
            },
            "scroll": {
                "index": {
                    "session_id": "session-1",
                    "tiers": [[{
                        "seq_lo": 1,
                        "seq_hi": 2,
                        "lines": [],
                    }]],
                },
            },
        },
    }

    def unexpected_reader(**kwargs):
        raise AssertionError("invalid AgentState must stay on legacy fallback")

    monkeypatch.setattr(
        "qwenpaw.app.chats.api.read_archived_messages",
        unexpected_reader,
    )
    result = await get_chat(
        "00000000-0000-0000-0000-000000000001",
        mgr=FakeManager(),
        session=FakeSession(state),
        workspace=fake_workspace(tmp_path),
    )

    assert result.messages[0].content[0].text == "legacy-message"
```

以上测试完整覆盖 reader 参数、Native/无 Scroll 旁路、空归档降级、legacy fallback 与 status 保持。

- [ ] **Step 2: Run the GET tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/app/chats/test_api_scroll_history.py -v
```

Expected: monkeypatch target不存在，或 GET 未调用 reader 导致顺序断言失败。

- [ ] **Step 3: Add minimal GET integration**

在 `api.py`：

```python
from ...utils.io_utils import run_sync_io
from .scroll_history import read_archived_messages
```

在 `get_chat` 中记录 `parsed_agent_state`，仅在 `AgentState` 成功解析且 `agent.scroll` 为 dict 时恢复：

```python
    parsed_agent_state = False
    state_raw = agent_raw.get("state")
    if isinstance(state_raw, dict):
        try:
            agent_state = AgentState.model_validate(state_raw)
            memories = list(agent_state.context)
            parsed_agent_state = True
        except Exception:
            logger.debug(
                "Failed to parse agent.state, falling back to legacy",
                exc_info=True,
            )

    if not memories:
        memory_raw = agent_raw.get("memory", {})
        if memory_raw:
            memories, _summary = parse_legacy_memory_state(memory_raw)

    scroll_raw = agent_raw.get("scroll")
    if parsed_agent_state and isinstance(scroll_raw, dict):
        try:
            db_filename = (
                workspace.config.running.light_context_config
                .scroll_config.db_filename
            )
        except Exception:
            db_filename = "history.db"
        archived = await run_sync_io(
            read_archived_messages,
            workspace_dir=workspace.workspace_dir,
            db_filename=db_filename,
            session_id=chat_spec.session_id,
            agent_id=getattr(workspace, "agent_id", None),
            scroll_state=scroll_raw,
        )
        if archived:
            memories = [*archived, *memories]
```

- [ ] **Step 4: Run GET, chats and Scroll regression tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/app/chats/test_api_scroll_history.py tests/unit/app/chats tests/unit/agents/context/test_history_store.py -v
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit Task 3**

```powershell
git add -- src/qwenpaw/app/chats/api.py tests/unit/app/chats/test_api_scroll_history.py
git commit -m "feat: restore compacted history in chat details"
```

---

### Task 4: Backend 20MB/30MB/50MB benchmark

**Files:**
- Create: `tests/integration/test_compact_chat_history_large.py`

**Interfaces:**
- Consumes: integrated `get_chat`, `HistoryStore`, `LogEntry`, temporary workspace.
- Produces: deterministic benchmark lines prefixed `COMPACT_CHAT_HISTORY_BACKEND_METRIC`。

- [ ] **Step 1: Add an opt-in deterministic payload generator**

测试文件顶部保留：

```python
"""Large full-history benchmark.

Future pagination trigger: keep the full-history GET until repeatable
20MB/30MB/50MB measurements show a loading or retained-memory regression.
Then add an additive archived-history cursor API and a formal AgentScope
history-page callback.
"""
```

使用 `pytest.mark.skipif`：

```python
RUN_LARGE = os.getenv("QWENPAW_RUN_LARGE_HISTORY_BENCHMARKS") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        not RUN_LARGE,
        reason="set QWENPAW_RUN_LARGE_HISTORY_BENCHMARKS=1",
    ),
]
```

使用以下确定性构造。先在内存中用现有转换器校准目标 JSON，再把同一批 `Msg` 通过正式 `msg_to_entries` 写入临时 `HistoryStore`：

```python
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from agentscope.message import Msg
from agentscope.state import AgentState

from qwenpaw.agents.context.scroll.history import HistoryStore
from qwenpaw.agents.context.scroll.serialize import msg_to_entries
from qwenpaw.app.chats.models import ChatHistory, ChatSpec
from qwenpaw.app.chats.utils import agentscope_msg_to_message


@dataclass
class LargeFixture:
    chat_id: str
    manager: object
    session: object
    workspace: object


def _text_block(message: Msg) -> str:
    return message.content[0].text


def build_large_scroll_fixture(
    tmp_path: Path,
    target_mb: int,
) -> LargeFixture:
    target_bytes = target_mb * 1024**2
    message_count = 80
    filler_bytes = target_bytes - 128 * 1024
    per_message = filler_bytes // message_count
    archived = [
        Msg(
            id=f"archived-{index}",
            name="user" if index % 2 == 0 else "assistant",
            role="user" if index % 2 == 0 else "assistant",
            content=f"history-{index:03d}-" + ("x" * per_message),
            created_at="2026-07-30T00:00:00+00:00",
        )
        for index in range(message_count)
    ]
    marker = Msg(
        id="memory-marker",
        name="memory",
        role="user",
        content="[context compressed]",
        created_at="2026-07-30T00:01:00+00:00",
    )
    tail = Msg(
        id="live-tail",
        name="assistant",
        role="assistant",
        content="latest-history-message",
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
    archived[-1].content[0].text = (
        _text_block(archived[-1]) + ("x" * max(0, correction))
    )
    if correction < 0:
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
```

- [ ] **Step 2: Add parameterized endpoint measurement**

核心测试结构：

```python
@pytest.mark.parametrize("target_mb", [20, 30, 50])
@pytest.mark.asyncio
async def test_full_history_get_large_payload(
    target_mb,
    tmp_path,
):
    fixture = build_large_scroll_fixture(tmp_path, target_mb)

    tracemalloc.start()
    samples_ms = []
    result = None
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
    assert abs(len(serialized) - target_mb * 1024**2) <= (
        target_mb * 1024**2 * 0.01
    )
    assert any(
        content.type == "text"
        and "[context compressed]" in content.text
        for message in result.messages
        for content in message.content
    )
    assert max(samples_ms) < 30_000
    print(
        "COMPACT_CHAT_HISTORY_BACKEND_METRIC "
        + json.dumps({
            "target_mb": target_mb,
            "payload_bytes": len(serialized),
            "median_ms": statistics.median(samples_ms),
            "max_ms": max(samples_ms),
            "peak_python_bytes": peak_bytes,
        })
    )
```

- [ ] **Step 3: Run the benchmark once to verify data sizing**

Run:

```powershell
$env:QWENPAW_RUN_LARGE_HISTORY_BENCHMARKS='1'
.\.venv\Scripts\python.exe -m pytest tests/integration/test_compact_chat_history_large.py -v -s
```

Expected: three sizes pass, each prints payload bytes, median/max milliseconds and peak Python bytes.

- [ ] **Step 4: Fix only correctness or benchmark-harness defects**

If data sizing misses 1%, adjust only the final ASCII filler. Do not optimize production code or add pagination based on a single cold sample; the final report uses repeated median/max and approved decision lines.

- [ ] **Step 5: Commit Task 4**

```powershell
git add -- tests/integration/test_compact_chat_history_large.py
git commit -m "test: benchmark large compacted chat responses"
```

---

### Task 5: Frontend 20MB/30MB/50MB conversion benchmark

**Files:**
- Modify: `console/src/pages/Chat/tests/testLargeSession.test.ts`

**Interfaces:**
- Consumes: existing `convertMessages`.
- Produces: deterministic metrics prefixed `COMPACT_CHAT_HISTORY_CONVERT_METRIC`。

- [ ] **Step 1: Add an exact UTF-8 multi-turn generator**

添加 ASCII filler 的 `buildUtf8SizedMessages(targetBytes)`。生成 40 个 user/assistant turn，使用 `Buffer.byteLength(JSON.stringify(messages), "utf8")` 校准最后一条 assistant 文本：

```typescript
function buildUtf8SizedMessages(targetBytes: number): {
  messages: Message[];
  size: number;
} {
  const turnCount = 40;
  const fillerBudget = Math.max(0, targetBytes - 128 * 1024);
  const fillerPerMessage = Math.floor(fillerBudget / (turnCount * 2));
  const messages: Message[] = [];
  for (let turn = 0; turn < turnCount; turn += 1) {
    messages.push({
      id: `large-user-${turn}`,
      role: "user",
      content: [{
        type: "text",
        text: `user-${turn}-` + "u".repeat(fillerPerMessage),
      }],
      metadata: { timestamp: "2026-07-30T00:00:00+00:00" },
    });
    messages.push({
      id: `large-assistant-${turn}`,
      role: "assistant",
      content: [{
        type: "text",
        text: `assistant-${turn}-` + "a".repeat(fillerPerMessage),
      }],
      metadata: { timestamp: "2026-07-30T00:00:01+00:00" },
    });
  }

  const encodedSize = () =>
    Buffer.byteLength(JSON.stringify(messages), "utf8");
  const last = messages[messages.length - 1];
  const lastText = (last.content as Array<{ type: string; text: string }>)[0];
  const correction = targetBytes - encodedSize();
  if (correction >= 0) {
    lastText.text += "z".repeat(correction);
  } else {
    lastText.text = lastText.text.slice(0, correction);
  }
  return { messages, size: encodedSize() };
}
```

文件中保留注释：

```typescript
// Future pagination trigger: keep the full-history GET until repeatable
// 20MB/30MB/50MB measurements show a loading or retained-memory regression.
// Then add an additive cursor API plus a formal AgentScope page callback.
```

- [ ] **Step 2: Add opt-in parameterized conversion tests**

```typescript
const RUN_LARGE_HISTORY_BENCHMARKS =
  process.env.QWENPAW_RUN_LARGE_HISTORY_BENCHMARKS === "1";

describe.skipIf(!RUN_LARGE_HISTORY_BENCHMARKS)(
  "convertMessages — 20MB/30MB/50MB full-history benchmark",
  () => {
    it.each([20, 30, 50])(
      "converts %iMB without crashing",
      (targetMb) => {
        const targetBytes = targetMb * 1024 * 1024;
        const { messages, size } = buildUtf8SizedMessages(targetBytes);
        const maybeGc = (globalThis as { gc?: () => void }).gc;
        maybeGc?.();
        const heapBefore = process.memoryUsage().heapUsed;
        const started = performance.now();
        const converted = convertMessages(messages);
        const elapsedMs = performance.now() - started;
        const heapAfter = process.memoryUsage().heapUsed;

        expect(Math.abs(size - targetBytes)).toBeLessThanOrEqual(
          targetBytes * 0.01,
        );
        expect(converted.length).toBeGreaterThan(0);
        expect(elapsedMs).toBeLessThan(30_000);
        console.info(
          "COMPACT_CHAT_HISTORY_CONVERT_METRIC",
          JSON.stringify({
            targetMb,
            payloadBytes: size,
            elapsedMs,
            heapDeltaBytes: heapAfter - heapBefore,
          }),
        );
      },
      60_000,
    );
  },
);
```

- [ ] **Step 3: Run existing fast tests first**

Run:

```powershell
Set-Location console
npm test -- --run src/pages/Chat/tests/testLargeSession.test.ts
```

Expected: existing tests pass; large suite is skipped.

- [ ] **Step 4: Run large conversion benchmarks**

Run:

```powershell
$env:QWENPAW_RUN_LARGE_HISTORY_BENCHMARKS='1'
$env:NODE_OPTIONS='--expose-gc --max-old-space-size=4096'
npm test -- --run src/pages/Chat/tests/testLargeSession.test.ts
```

Expected: 20/30/50MB cases pass and print conversion/heap metrics.

- [ ] **Step 5: Commit Task 5**

```powershell
git add -- console/src/pages/Chat/tests/testLargeSession.test.ts
git commit -m "test: benchmark large chat message conversion"
```

---

### Task 6: Chromium page-open and retained-heap benchmark

**Files:**
- Create: `e2e/tests/test_compact_chat_history_large.py`

**Interfaces:**
- Consumes: E2E `mock_api` fixture, Chat route, mocked `/api/chats` responses, Chromium CDP session.
- Produces: metrics prefixed `COMPACT_CHAT_HISTORY_PAGE_METRIC`，并验证 DOM 窗口、错误和切换后 retained heap。

- [ ] **Step 1: Create opt-in page benchmark and API mocks**

生成与 Task 5 相同结构的 20/30/50MB HTTP messages：

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class BrowserPayload:
    chat_id: str
    history_json: str
    payload_bytes: int


def build_large_history(target_mb: int) -> BrowserPayload:
    target_bytes = target_mb * 1024**2
    turn_count = 40
    filler_per_message = (
        target_bytes - 128 * 1024
    ) // (turn_count * 2)
    messages = []
    for turn in range(turn_count):
        messages.extend([
            {
                "id": f"large-user-{turn}",
                "role": "user",
                "content": [{
                    "type": "text",
                    "text": f"user-{turn}-" + "u" * filler_per_message,
                }],
                "metadata": {
                    "timestamp": "2026-07-30T00:00:00+00:00",
                },
            },
            {
                "id": f"large-assistant-{turn}",
                "role": "assistant",
                "content": [{
                    "type": "text",
                    "text": (
                        "latest-history-message"
                        if turn == turn_count - 1
                        else f"assistant-{turn}-"
                    ) + "a" * filler_per_message,
                }],
                "metadata": {
                    "timestamp": "2026-07-30T00:00:01+00:00",
                },
            },
        ])

    body = {"messages": messages, "status": "idle"}

    def encode() -> bytes:
        return json.dumps(
            body,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    last_text = messages[-1]["content"][0]
    correction = target_bytes - len(encode())
    if correction >= 0:
        last_text["text"] += "z" * correction
    else:
        last_text["text"] = last_text["text"][:correction]
    encoded = encode()
    assert abs(len(encoded) - target_bytes) <= target_bytes * 0.01
    return BrowserPayload(
        chat_id=f"large-history-{target_mb}",
        history_json=encoded.decode("utf-8"),
        payload_bytes=len(encoded),
    )
```

使用 `mock_api` 后注册更具体的 routes，利用 Playwright “后注册优先”：

```python
def register_large_history_routes(page, payload: BrowserPayload) -> None:
    large_chat_spec = {
        "id": payload.chat_id,
        "session_id": payload.chat_id,
        "user_id": "admin",
        "channel": "console",
        "name": f"Large {payload.payload_bytes}",
        "created_at": "2026-07-30T00:00:00Z",
        "updated_at": "2026-07-30T00:00:00Z",
        "pinned": False,
    }
    small_chat_spec = {
        **large_chat_spec,
        "id": "small-history",
        "session_id": "small-history",
        "name": "Small history",
    }
    page.route(
        "**/api/chats",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps([large_chat_spec, small_chat_spec]),
        ),
    )
    page.route(
        f"**/api/chats/{payload.chat_id}",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=payload.history_json,
        ),
    )
    page.route(
        "**/api/chats/small-history",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "messages": [],
                "status": "idle",
            }),
        ),
    )
```

每个 size 使用独立 page/context，避免前一档污染下一档。

- [ ] **Step 2: Measure page visibility and Chromium heap**

```python
@pytest.mark.parametrize("target_mb", [20, 30, 50])
def test_large_history_page_opens_and_releases_memory(
    mock_api,
    target_mb,
):
    page = mock_api
    payload = build_large_history(target_mb)
    register_large_history_routes(page, payload)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console",
        lambda message: (
            errors.append(message.text)
            if message.type == "error"
            else None
        ),
    )
    cdp = page.context.new_cdp_session(page)
    cdp.send("Performance.enable")

    started = time.perf_counter()
    page.goto(
        f"{config.server.base_url}/chat/{payload.chat_id}",
        wait_until="domcontentloaded",
        timeout=60_000,
    )
    page.locator("text=latest-history-message").wait_for(
        state="visible",
        timeout=60_000,
    )
    visible_ms = (time.perf_counter() - started) * 1000
    metrics = {
        item["name"]: item["value"]
        for item in cdp.send("Performance.getMetrics")["metrics"]
    }

    assert not errors
    assert visible_ms < 60_000
    assert page.locator(
        "[class*='chat-anywhere-message-list'] [class*='bubble']",
    ).count() <= 10
    print(
        "COMPACT_CHAT_HISTORY_PAGE_METRIC "
        + json.dumps({
            "target_mb": target_mb,
            "visible_ms": visible_ms,
            "js_heap_used_bytes": metrics.get("JSHeapUsedSize", 0),
        })
    )
```

选择器先通过现有 DOM 实际结构校准，禁止为了让测试通过而放宽到无法证明 10 条窗口的父节点。

- [ ] **Step 3: Add session-switch retained-heap measurement**

在首次 heap 采样后切换到 mock 小会话，显式 GC，再次进入大会话：

```python
cdp.send("HeapProfiler.enable")
large_heap = metrics.get("JSHeapUsedSize", 0)
page.goto(
    f"{config.server.base_url}/chat/small-history",
    wait_until="domcontentloaded",
    timeout=60_000,
)
page.locator("text=latest-history-message").wait_for(
    state="detached",
    timeout=60_000,
)
cdp.send("HeapProfiler.collectGarbage")
after_switch_metrics = {
    item["name"]: item["value"]
    for item in cdp.send("Performance.getMetrics")["metrics"]
}
retained_after_switch = after_switch_metrics.get("JSHeapUsedSize", 0)

second_started = time.perf_counter()
page.goto(
    f"{config.server.base_url}/chat/{payload.chat_id}",
    wait_until="domcontentloaded",
    timeout=60_000,
)
page.locator("text=latest-history-message").wait_for(
    state="visible",
    timeout=60_000,
)
second_visible_ms = (time.perf_counter() - second_started) * 1000

assert retained_after_switch >= 0
assert second_visible_ms < 60_000
```

将 `large_heap`、`retained_after_switch` 和 `second_visible_ms` 加入 Step 2 的同一 metric JSON。断言页面不崩溃；实际是否达到 300MB/持续累积由 Task 7 按基线规则判断。

- [ ] **Step 4: Run the page benchmarks**

Run:

```powershell
$env:QWENPAW_RUN_LARGE_HISTORY_BENCHMARKS='1'
$env:QWENPAW_HEADLESS='true'
.\.venv\Scripts\python.exe -m pytest e2e/tests/test_compact_chat_history_large.py -v -s
```

Expected: 20/30/50MB 页面均打开，最新消息可见，无 page/console error，DOM 历史窗口不超过 10，并打印 heap/耗时。

- [ ] **Step 5: Commit Task 6**

```powershell
git add -- e2e/tests/test_compact_chat_history_large.py
git commit -m "test: measure large chat page loading"
```

---

### Task 7: Full verification and pagination decision

**Files:**
- Create: `docs/0729-compact-chat-history/06-validation-report.md`
- Modify only if metrics trigger pagination: create a new approved pagination spec before any pagination code.

**Interfaces:**
- Consumes: Tasks 4-6 metric lines and all correctness tests.
- Produces: validation report with explicit `保持全量` or `触发分页` decision.

- [ ] **Step 1: Run backend correctness and integration suites**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/app/chats tests/unit/agents/context tests/integration/test_chats_global.py tests/integration/test_chats_agent_scoped.py -v
```

Expected: all selected tests pass.

- [ ] **Step 2: Run frontend regression and build**

```powershell
Set-Location console
npm test -- --run src/pages/Chat/tests/testLargeSession.test.ts src/pages/Chat/tests/sessionCacheStaleness.test.ts src/pages/Chat/ChatPage.test.tsx
npm run build
```

Expected: tests and TypeScript/Vite build pass.

- [ ] **Step 3: Run all large benchmarks and capture metrics**

按 Tasks 4-6 的命令显式设置 `QWENPAW_RUN_LARGE_HISTORY_BENCHMARKS=1`，把每个 metric JSON 记录到验证报告表格。

- [ ] **Step 4: Apply the approved decision rules**

报告必须逐项判断：

- 50MB GET median/max 是否稳定超过 2 秒。
- 50MB conversion 是否稳定超过 1 秒。
- 50MB latest-message-visible 是否稳定超过 5 秒。
- 50MB 页面额外 retained JS heap 是否稳定超过 300MB。
- 20MB 到 50MB 耗时或内存是否超过 4 倍。
- 会话切换 + GC 后 retained heap 是否持续累积。

若任一项触发，停止本计划，不直接编写分页代码；先在同目录增加独立分页设计规格，明确接口和 AgentScope 扩展并请用户批准。若均未触发，报告结论为“保持 GET 全量恢复”。

- [ ] **Step 5: Write the validation report**

`06-validation-report.md` 必须包含：

- OS、CPU、内存、Python、Node、Chromium 版本。
- 每档 payload 实际字节数。
- 后端 median/max/peak Python bytes。
- 转换 elapsed/heap delta。
- 页面 visible/JS heap/GC retained/second-open。
- correctness/build 测试结果。
- 明确分页决策。
- Future pagination trigger 原文。

- [ ] **Step 6: Run final diff and status checks**

```powershell
git diff --check
git status --short
```

Expected: 无 whitespace error；只包含本需求文件，用户已有未跟踪文件保持未暂存。

- [ ] **Step 7: Commit validation report**

```powershell
git add -- docs/0729-compact-chat-history/06-validation-report.md
git commit -m "docs: record compact chat history validation"
```
