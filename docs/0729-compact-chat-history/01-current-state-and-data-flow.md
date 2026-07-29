# 01. 会话压缩、保存与展示现状

## 1. 结论摘要

Scroll 策略已经把完整消息以写穿方式保存到工作区 `history.db`，但会话详情接口只读取 session JSON 中压缩后的 `agent.state.context`。因此上下文压缩后，模型仍可通过 Scroll 索引召回旧消息，而前端重新进入会话时只能看到“压缩提示 + 当前尾部”，看不到被驱逐的原始消息。

缺失发生在展示读取链路，不在压缩或持久化链路。本需求只需为 `GET /chats/{id}` 增加只读展示组装，不应修改模型上下文、压缩状态或消息写入逻辑。

## 2. Scroll 压缩与持久化

### 2.1 策略装配

`build_scroll_components` 在 `running.light_context_config.strategy == "scroll"` 时创建：

- `HistoryStore`：工作区级 SQLite 历史库，默认路径为 `{workspace_dir}/history.db`。
- `ScrollContextManager`：管理写穿、压缩、驱逐索引与上下文重建。
- Scroll recall 工具：供模型按 `seq` 范围召回历史。

非 Scroll 策略不创建这些组件，继续使用 AgentScope 原生压缩。

### 2.2 写穿

Scroll 在消息产生时把消息转换为 `conversation_history` 行。主要字段包括：

- `seq`：全局递增地址。
- `session_id`、`agent_id`：会话与 Agent 隔离字段。
- `kind`：`context_msg`、`model_turn` 或 `tool_result`。
- `role`、`name`、`content`。
- `blocks`：结构化消息块，可用于恢复完整展示格式。
- `tool_call_id`、`tool_input`、`tool_state`。
- `metadata`、`created_at`、`dedup_key`。

一个 AgentScope `Msg` 可能对应多行：普通用户/助手内容形成一行，消息中的每个 `tool_result` 形成独立行。展示恢复时必须保持 `seq` 顺序，并避免在分页或分组边界拆散一次助手工具调用。

### 2.3 压缩

默认配置在模型上下文达到 80% 时触发压缩，保留最近约 10% 的尾部，且保留量最多为 40K token。

压缩流程为：

1. 把当前窗口完整写入 `history.db`。
2. 计算 token；未达到阈值则退出。
3. 将上下文拆成待驱逐中段与保留尾部。
4. 把驱逐消息的精确 `seq_lo..seq_hi` 范围写入 `EvictionIndex`。
5. 重建模型上下文为：

   `压缩索引提示 Msg + 最近原始消息尾部`

6. 若仍有上下文压力，再折叠可恢复的工具结果。

`EvictionIndex` 使用多层级块保存范围。层级折叠只压缩索引文本，不删除 `history.db` 中的原始消息。

### 2.4 Session 快照

`QwenPawAgent.state_dict()` 保存：

- `agent.state`：当前 AgentState，其中 `context` 已经是压缩后的索引提示和尾部。
- `agent.scroll`：Scroll checkpoint，包含 synthetic ids、连续性信息和 `EvictionIndex`。

因此 session JSON 不再包含全部原始历史，但包含定位当前有效归档范围所需的索引。

## 3. 当前会话读取链路

`GET /chats/{chat_id}` 当前执行：

1. `ChatManager.get_chat(chat_id)` 解析 chat 与 session 身份。
2. `SafeJSONSession.get_session_state_dict(...)` 读取 session JSON。
3. 解析 `agent.state` 为 `AgentState`。
4. 取 `agent_state.context`。
5. 通过 `agentscope_msg_to_message` 转为 HTTP `Message[]`。
6. 返回原有 `ChatHistory { messages, status }`。

该接口没有读取 `agent.scroll.index` 和 `history.db`，所以压缩前消息不会出现在响应中。

## 4. 前端展示链路

前端 `sessionApi.getSession()` 调用 `chatApi.getChat()`，然后由 `convertMessages()` 将后端扁平消息转成 AgentScope Runtime WebUI 的卡片消息：

- 用户消息形成 user card。
- 连续非用户消息聚合为 response card。
- 转换复杂度为 O(n)。

AgentScope Runtime WebUI 当前的 `PAGE_SIZE = 10` 是本地显示分页：

1. `getSession()` 已经取得并转换完整消息。
2. 完整消息保存在 React context。
3. `MessageList` 每次只把 10 条交给 `Bubble.List` 渲染。

因此它能控制 DOM 节点数量，但不能减少网络响应、JSON 解析或 React 状态占用。

前端还维护最多 10 个、TTL 为 5 分钟的转换后会话缓存。会话列表本身会移除消息正文，不会把所有会话详情预加载。

## 5. 其它压缩策略

AgentScope 原生压缩保存的是 summary 与保留尾部，没有 Scroll 的精确 `seq` 驱逐范围，无法从 `history.db` 可靠恢复完整原始消息。

本需求行为边界为：

- 快照中存在有效 Scroll checkpoint：恢复 Scroll 驱逐历史。
- 非 Scroll、旧格式、无有效索引：保持现有 GET 行为。
- 当前运行配置已改变，但快照仍带有效 Scroll checkpoint：按快照数据恢复，避免配置变化使既有会话突然丢失展示历史。

## 6. `/clear`、`/new` 与隔离要求

`/clear` 和 `/new` 会清空当前上下文并丢弃 Scroll checkpoint，但不会立即删除 `history.db` 中的旧行。

因此不能使用“按 session_id 读取全部历史”的方案。展示读取必须只查询当前 checkpoint 的精确索引范围，否则会把清理前历史错误恢复到页面。

读取还必须同时约束 `session_id`，不能只依赖全局 `seq`。

## 7. 实际容量校准

目标会话 `1785340717611-ikrmq85` 的现场数据：

- 已有 2 个 Scroll 压缩块。
- 驱逐范围为 `597..602` 和 `603..625`。
- 压缩前归档共 29 行。
- `content` 约 143KB。
- `blocks` 约 268KB。
- 当前尾部 4 行约 21KB。
- session JSON 约 16.7KB。

`content` 与 `blocks` 存在重复表达，因此数据库字段大小不能直接等同于 HTTP payload。该会话恢复后的响应预计仍在数百 KB 量级。

当前工作区的 `history.db`：

- 38 个会话。
- 598 行。
- 主文件约 2.83MB。

理论上单会话没有字节上限；默认 30 天保留窗口限制时间而不限制字节，配置为 0 时会无限增长。对于默认 128K 上下文，一次压缩通常驱逐约 92K token，即数百 KB；1M 或 2M 上下文模型的一次驱逐可能达到数 MB。

## 8. 根因与修改边界

根因是 GET 展示读取没有把 Scroll checkpoint 指向的归档历史与当前上下文组装起来。

本次修改边界：

- 新增只读 Scroll 展示恢复。
- 保持 `GET /chats/{id}` 路径和响应模型不变。
- 不修改压缩算法。
- 不修改 session 保存格式。
- 不修改 `history.db` 写入格式。
- 不修改模型请求上下文。
- 不修改前端消息协议和 AgentScope Runtime WebUI。
