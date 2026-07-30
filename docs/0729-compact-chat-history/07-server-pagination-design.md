# 压缩前聊天历史服务端分页设计（待批准）

状态：待用户批准，尚未授权实现  
触发依据：`06-validation-report.md` 中 50MB 全量历史首次显示稳定约 8.50 秒

## 1. 决策摘要

推荐采用“现有 GET + 新增归档历史游标接口”：

- `GET /chats/{id}` 保持已发布路径、参数、`ChatHistory { messages, status }`
  响应结构和 current-context 行为，不再承担压缩前历史的全量传输。
- 新增 `GET /chats/{id}/archived-history`，只分页读取当前 Scroll checkpoint
  精确索引覆盖的压缩前消息。
- Chat 页面初始加载当前上下文；若不足 10 个展示单元，只请求填满当前窗口
  所需的归档页。
- 用户向前浏览时按需加载更早页面，页面始终只渲染 10 个顶层消息单元。
- 归档历史只用于展示，不写回 session，不进入 `AgentState.context`，不改变发送
  给模型的上下文。

这一方案与用户之前提出的方向一致，并直接消除 50MB 全量 HTTP body、
`JSON.parse` 和首次页面构建成本。

## 2. 为什么不修改现有 GET 为分页响应

不采用以下方案：

1. **直接把 `GET /chats/{id}` 改成分页结构**：会破坏现有客户端对
   `ChatHistory` 的解析。
2. **给现有 GET 增加默认全量、可选分页参数**：默认打开仍承受 50MB 首次
   加载问题，无法解决本次触发项。
3. **只做前端虚拟列表，不做服务端分页**：当前已经只渲染 10 条，但 50MB
   仍需完整传输、解析并保存在会话缓存中，实测仍约 8.50 秒。
4. **按 offset 分页**：Scroll checkpoint 和 retention 会变化，offset 在新增
   压缩块或缺行后容易漂移；游标更适合只读历史遍历。

## 3. API 契约

### 3.1 请求

```http
GET /api/chats/{chat_id}/archived-history?cursor={opaque}&limit=20
```

参数：

| 参数 | 规则 |
| --- | --- |
| `chat_id` | 沿用 Chat 访问控制；先解析 chat，再确定 session 和 agent |
| `cursor` | 可选，不透明、带版本的 base64url cursor；缺省表示从最新归档位置开始 |
| `limit` | 可选，默认 20，最小 1，最大 50；表示原始消息上限而非保证的 UI bubble 数 |

服务端另设软响应上限 `2 MiB`。达到消息数或字节上限即停止；单条消息超过
2 MiB 时仍至少返回该条消息，确保历史可达而不是永久跳过。

### 3.2 响应

```json
{
  "messages": [],
  "next_cursor": "opaque-or-null",
  "has_more": false,
  "checkpoint_revision": "sha256-prefix"
}
```

建议模型：

```python
class ArchivedChatHistoryPage(BaseModel):
    messages: list[Msg] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
    checkpoint_revision: str
```

约束：

- `messages` 始终按时间/`seq` 正序返回，便于前端 prepend。
- `next_cursor` 指向更早的归档位置；`has_more=false` 时为 `null`。
- 不返回总数，避免每页额外 `COUNT` 和 retention 缺口导致的虚假精度。
- endpoint 缺库、缺表或 checkpoint 无有效范围时返回空页，使当前聊天页面
  仍可使用；坏 cursor 返回 `400`。
- cursor 中的 checkpoint revision 与当前 revision 不一致时返回
  `409 history_cursor_stale`。前端清理该会话的归档页缓存并从第一页重试一次。

### 3.3 Cursor 内容

cursor 对客户端不透明，内部最少包含：

```json
{
  "v": 1,
  "before_seq": 1234,
  "checkpoint_revision": "sha256-prefix"
}
```

不把 session/agent 作为可信输入。每次请求都根据 `{chat_id}` 重新执行权限检查，
并把查询限制到当前 chat 的 session、agent 和 checkpoint ranges。即使客户端
篡改 cursor，也不能跨会话或跨 agent 读取。

## 4. 服务端读取算法

1. 读取 chat spec 和 session state，确认聊天存在且调用者有权访问。
2. 仅当 agent 使用 Scroll 且 checkpoint 有效时解析索引范围。
3. 对规范化 ranges 计算稳定 revision。
4. 解码并校验 cursor 版本、`before_seq` 和 revision。
5. 用 SQLite read-only URI 查询：

   ```sql
   SELECT ...
   FROM conversation_history
   WHERE session_id = ?
     AND (? IS NULL OR agent_id IS NULL OR agent_id = ?)
     AND seq < ?
     AND (
       seq BETWEEN ? AND ?
       OR ...
     )
   ORDER BY seq DESC
   LIMIT ?
   ```

6. 以 `limit + 1` 或继续扫描 ranges 的方式判断 `has_more`，再把本页反转为正序。
7. 在消息数和 2 MiB 软上限中先到者停止；单条超限仍返回一条。
8. 重建 AgentScope `Msg`，但不写入 `AgentState.context` 或任何持久化状态。

retention 缺行不是错误：跳过缺失 `seq`，cursor 继续向更早范围推进。查询仍在
线程池中执行，避免阻塞 FastAPI event loop。

## 5. 前端数据流

### 5.1 初始打开

1. 调用现有 `GET /chats/{id}`，获得 current context。
2. `convertMessages` 转换为展示单元。
3. 如果可展示单元少于 10，调用 archived-history 第一页；否则不请求归档接口。
4. 把归档消息转换、去重并 prepend，只向 AgentScope Runtime WebUI 传入最新
   10 个展示单元。

初始请求不会再下载几十 MB 压缩前历史。常见会话只发一个 GET；需要补齐窗口
时最多多发一个受限小页。

### 5.2 向前浏览

- 在用户触达当前最早边界、使用历史目录跳转或显式“加载更早消息”时，调用
  AgentScope history-page callback。
- 同一 session/cursor 的并发请求合并。
- session 切换时使用 `AbortController` 取消未完成请求，并用 generation token
  防止旧响应写入新会话。
- 根据 message id 去重；归档 checkpoint ranges 与 current context 原本应互斥，
  去重作为防御性保障。

### 5.3 展示和缓存上限

- DOM：始终最多 10 个顶层展示单元。
- 每会话归档缓存：最多 3 页、100 条原始消息或 20 MiB，任一先到即淘汰最远页。
- 全局：沿用 SessionApi LRU；淘汰会话时同时释放其 cursor、归档原始消息和
  converted page。
- 避免长期同时保留 raw response string、解析后的 Message 和重复大字符串；
  转换完成后释放临时 raw page 引用。
- 单条超大消息允许临时突破 20 MiB 会话上限，但切出窗口后优先淘汰。

## 6. 兼容性

### 后端

- 现有 `GET /chats/{id}` 的路径和响应模型不变。
- 新 endpoint 是纯新增；旧客户端完全不调用它。
- Scroll reader 继续兼容 `tiers` 和 legacy `levels`。
- 非 Scroll、无 checkpoint、坏库和旧 session 保持 current-context 页面可用。
- cursor 带版本；将来字段扩展不要求客户端理解。

### 前端

- 新前端优先调用分页 endpoint。
- 若服务端返回 `404`（旧后端），降级为只展示现有 GET 返回的内容，不循环重试。
- `409 history_cursor_stale` 最多自动刷新一次，避免 checkpoint 变化时形成重试风暴。
- AgentScope 若尚无正式 history-page callback，先以 QwenPaw adapter 封装；
  adapter 接口保持与未来正式 callback 对齐，避免页面组件直接依赖 HTTP 细节。

## 7. 可观测性和可靠性

新增结构化指标：

- `archived_history_page_duration_ms`
- `archived_history_page_response_bytes`
- `archived_history_page_message_count`
- `archived_history_cursor_stale_total`
- `archived_history_read_failure_total`
- 前端 `chat_history_page_visible_ms`
- 前端每会话缓存 bytes、淘汰次数和 aborted request 数

日志不得记录消息正文、完整 cursor 或用户敏感数据。数据库失败使用限频 warning，
接口空页降级不影响 current context。

## 8. TDD 实施计划

批准后按以下顺序执行，每一步先 RED、再最小 GREEN、最后重构。

### Task P1：分页 reader

测试先行：

- 第一页从 checkpoint 最新端开始。
- 跨多个不连续 range 向前翻页且无重复/遗漏。
- retention 缺行仍能继续到更早 range。
- `limit`、2 MiB 软上限和单条超大消息。
- session/agent 隔离、只读数据库和失败降级。
- revision 稳定、checkpoint 变化后 cursor 失效。

实现：

- 在 `scroll_history.py` 增加 `read_archived_message_page`。
- 复用现有 range parser 和 row-to-`Msg` 重建，不复制反序列化逻辑。

### Task P2：新增 endpoint

测试先行：

- 未授权/不存在 chat 不可读。
- 缺省 cursor、后续 cursor、坏 cursor 和 stale cursor。
- 响应模型、正序消息、`has_more`/`next_cursor`。
- 非 Scroll 和存储故障返回可降级空页。
- 现有 `GET /chats/{id}` 保持 current-context 语义和响应结构。

实现：

- 新增 `ArchivedChatHistoryPage` model 和 route。
- SQLite 工作继续通过 `run_sync_io` 卸载。

### Task P3：前端 API 与有界缓存

测试先行：

- cursor 请求/响应类型。
- 同 cursor 并发去重。
- 404 降级、409 单次刷新和其它错误可恢复。
- session 切换取消请求，旧响应不污染新 session。
- 3 页/100 条/20 MiB 淘汰规则。

实现：

- 扩展 Chat API client 和 SessionApi archive page cache。

### Task P4：10 条窗口与按需加载

测试先行：

- current context 足够 10 条时不请求归档接口。
- 不足 10 条时只请求填满窗口所需页面。
- 向前浏览加载下一页且 DOM 始终为 10 个顶层 bubble。
- 消息去重、tool result、连续 assistant 分组和超大单条消息。
- 旧后端 404 时页面仍可使用。

实现：

- 增加 AgentScope history-page adapter/callback。
- 页面只接收当前窗口，归档 HTTP 细节不进入组件。

### Task P5：20/30/50MB 回归

验证：

- 现有 GET 不随归档总量增长。
- 第一页 endpoint 耗时、响应 bytes 和 Python peak。
- 20/30/50MB 完整归档下首次显示、向前翻页和 retained heap。
- 多会话轮换 20 次后无持续内存累积。
- 50MB 首次显示中位数低于 5 秒；目标值低于 2 秒。
- DOM 始终 10 条，模型上下文快照与分页前完全一致。

## 9. 批准点

开始分页代码前，需要确认以下整体方案：

> 保持已发布的 `GET /chats/{id}` current-context 语义；新增
> `/chats/{id}/archived-history` 游标接口；前端初始按需补齐 10 条、向前浏览
> 再分页加载；缓存同时受页数、消息数和 20 MiB 限制。

批准后将按第 8 节继续 TDD 开发，并以 50MB 首次显示低于 5 秒作为最低验收线。
