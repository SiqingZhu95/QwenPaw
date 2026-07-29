# 03. 需求方案分析与设计

## 1. 方案比较

### 方案 A：GET 内部全量恢复（采用）

后端在原有 `GET /chats/{id}` 内部读取 Scroll 索引范围，把归档消息与当前上下文组装后返回。

优点：

- API 协议和前端零变更。
- 复用现有消息转换和 10 条 DOM 窗口。
- 修改范围最小。
- 满足“前端展示完整、模型上下文不变”。

风险：

- 极大会话仍会产生全量网络、JSON 解析和客户端内存成本。

控制方式：

- 增加 20MB、30MB、50MB 基准。
- 记录可复现指标。
- 只有达到已定义的退化信号才升级到分页。

### 方案 B：新增归档历史分页接口（暂缓）

保留 GET 当前行为，增加归档历史游标接口，前端滚动到压缩边界时加载。

优点：

- 网络和客户端内存可做严格上限。
- 首屏与历史总量解耦。

缺点：

- 当前 AgentScope 的 10 条分页只是本地切片，没有公开的后端分页回调。
- 需要增加接口、游标、前端状态机、滚动锚点、取消请求和第三方组件扩展。
- 页边界必须处理 tool call/result 分组与压缩期间游标失效。

在现有数百 KB 实际会话、前端已有 2MB 回归测试的条件下，收益不足以覆盖复杂度。

### 方案 C：把完整历史重复保存到 session JSON（拒绝）

优点是 GET 无需读取 SQLite；缺点是重复存储、保存变慢、快照膨胀并增加一致性问题，不符合 Scroll 以 `history.db` 为真源的设计。

## 2. 总体架构

```text
GET /chats/{id}
    |
    +-- ChatManager：解析 chat/session 身份
    |
    +-- SafeJSONSession：读取当前 session snapshot
    |
    +-- ScrollDisplayHistoryReader
    |      1. 解析 agent.scroll.index
    |      2. 生成当前有效、互不重叠的 seq 范围
    |      3. 只读查询 workspace/history.db
    |      4. 将行恢复为 AgentScope Msg
    |
    +-- ChatHistoryAssembler
           archived Msg
           + agent.state.context（压缩提示 + live tail）
           -> agentscope_msg_to_message
           -> ChatHistory
```

两个新增单元只服务展示读取，不进入 Runtime/Agent/模型调用链。

## 3. 组件设计

### 3.1 `ScrollDisplayHistoryReader`

建议位置：`src/qwenpaw/app/chats/scroll_history.py`。

职责：

- 从持久化 `agent.scroll.index` 提取有效归档范围。
- 使用 `workspace.workspace_dir / configured_db_filename` 定位数据库。
- 以 `mode=ro` 打开 SQLite。
- 使用参数化 SQL 同时约束 `session_id` 和 `seq` 范围。
- 按 `seq` 升序返回归档 `Msg`。
- 不创建 schema、不迁移、不 quarantine、不写 WAL。

公开接口保持窄：

```python
def read_archived_messages(
    *,
    workspace_dir: Path,
    session_id: str,
    agent_id: str | None,
    scroll_state: Mapping[str, Any],
    db_filename: str = "history.db",
) -> list[Msg]:
    ...
```

不创建策略工厂。调用方先判断 checkpoint 是否可用；无有效 Scroll 数据直接返回空列表。

### 3.2 `extract_index_ranges`

纯函数，遍历 `tiers`，同时兼容旧字段 `levels`。

行为：

- 只接收整数且满足 `seq_lo <= seq_hi` 的范围。
- 排序并合并相邻/重叠范围。
- 拒绝缺失 `session_id` 或 checkpoint session 与 chat session 不一致的状态。
- 空或损坏数据返回空列表，不抛到 API 层。

查询索引范围而不是 session 全表，是防止 `/clear`、`/new` 历史复活的核心约束。

### 3.3 `history_rows_to_messages`

纯转换函数：

- `blocks` 为合法 JSON 列表时优先恢复结构化 blocks。
- 旧行没有 blocks 时，使用 `content` 创建文本块。
- `context_msg`、`model_turn` 恢复各自 role。
- `tool_result` 恢复为包含 tool result block 的 Msg。
- 使用稳定 id/dedup key，避免同一行重复。
- 单行损坏时跳过该行并记录 seq，不使整个会话失败。

### 3.4 `ChatHistoryAssembler`

可实现为路由模块中的小型纯函数，而不是新类：

```python
def assemble_display_messages(
    archived: list[Msg],
    current_context: list[Msg],
) -> list[Message]:
    return agentscope_msg_to_message([*archived, *current_context])
```

归档查询仅覆盖 index spans，因此正常情况下不会与 live tail 重叠。仍按稳定消息 id 做一次相邻去重，防御损坏 checkpoint。

## 4. 数据流

### 4.1 Scroll 正常路径

1. GET 读取 chat spec 与 session state。
2. 解析 `agent.state.context`，得到压缩提示和尾部。
3. 发现 `agent.scroll.index` 有有效范围。
4. 读取这些范围中的归档行。
5. 恢复归档 Msg。
6. 按“归档 + 当前 context”转换为现有 HTTP Message。
7. 前端按现有流程转换并每次显示 10 条。

### 4.2 Native/旧会话路径

没有有效 Scroll index 时不访问 `history.db`，执行原有 `agent.state` / legacy memory 读取逻辑。

### 4.3 部分历史被 retention 清理

SQLite 查询只返回仍存在的行。GET 返回可用归档和当前上下文，并记录缺口；不伪造缺失内容，不返回 500。

### 4.4 SQLite 故障

只读打开或查询失败时记录 warning，归档视为空，继续返回当前 context。

## 5. 模型上下文隔离证明

GET 使用从 session JSON 解析得到的局部对象，并只生成响应：

- 不持有正在运行 Agent 实例。
- 不调用 `state_dict()` 或 session save。
- 不向 `agent.state.context` 赋值。
- 不通过 Runtime builder 创建模型请求。

因此页面看到的完整历史不会影响随后发送给模型的上下文。

## 6. 接口兼容性

保持：

```json
{
  "messages": [],
  "status": "idle"
}
```

没有新增字段、查询参数或前端分支。未来若实测需要分页，采用新增可选接口，不改变本次 GET 合约。

## 7. 安全与可靠性

- SQL 参数绑定，不拼接 session_id。
- DB 路径来自 workspace 和受验证配置，不接受请求路径。
- read-only URI 防止展示请求产生写操作。
- agent/session 双重过滤。
- 范围来自持久化 index，不接受客户端 seq。
- 对缺失、损坏和版本不兼容 fail-open 到当前展示。
- 不在 API 读取路径使用会 quarantine 数据库的 `HistoryStore`。

## 8. 影响审视

### 对现有 GET

响应契约不变；只有有效 Scroll 会话的 `messages` 从“压缩后片段”变为“完整可用历史”，符合需求。

### 对模型

无影响，展示组装不进入模型链路。

### 对前端

无需改动协议或第三方组件。大 payload 风险通过 20/30/50MB 验证量化。

### 对历史数据库

只读，无 schema 变化、迁移或写锁。

### 对 Native

无有效 Scroll checkpoint 时直接旁路，保持原行为。

## 9. 后续分页扩展点

服务端分页不是本次预留空壳代码，而是一条由指标触发的演进路径：

1. 先保存本次 20/30/50MB 基线报告。
2. 后续真实指标与同口径基线比较。
3. 只有出现可复现加载或内存问题时设计新接口。
4. 新接口按完整逻辑消息分组，使用不透明游标。
5. 前端只通过正式可选分页回调接入，不依赖 AgentScope 内部 DOM。
