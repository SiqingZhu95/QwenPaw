# 04. 开发方案与测试验证方案

## 1. 开发原则

- TDD：先写失败测试，再实现最小代码。
- 小范围：后端增加 Scroll 展示读取；前端仅增加大规格测试和指标采集。
- 兼容优先：GET 合约、session 格式、SQLite schema、前端消息类型不变。
- 证据驱动：不因理论上限提前实现分页。

## 2. 设计模式与关联

### 2.1 Adapter

`history_rows_to_messages` 把 Scroll SQLite 行适配为现有 AgentScope `Msg`，随后复用 `agentscope_msg_to_message`。

### 2.2 Assembler

`assemble_display_messages` 负责把归档消息与当前 context 组合成展示序列。它只组装临时响应，不修改任何持久化或运行态对象。

### 2.3 Fail-open Boundary

`read_archived_messages` 是 SQLite 故障边界：归档读取异常被转换为空归档并记录日志，当前会话展示继续工作。

本次不引入 Strategy/Factory。Native 旁路由“是否存在有效 Scroll checkpoint”直接表达，增加抽象层反而扩大修改面。

## 3. 计划新增或修改的代码单元

### 后端

`src/qwenpaw/app/chats/scroll_history.py`

- `extract_index_ranges(scroll_state) -> list[tuple[int, int]]`
- `read_archived_messages(...) -> list[Msg]`
- `history_rows_to_messages(rows) -> list[Msg]`

`src/qwenpaw/app/chats/api.py`

- 在现有 GET 中解析当前 context 后调用只读 reader。
- 将归档置于当前 context 之前。
- 保持响应模型不变。

### 后端测试

建议新增：

- `tests/unit/app/chats/test_scroll_history.py`
- `tests/unit/app/chats/test_api_scroll_history.py`
- `tests/integration/test_compact_chat_history_large.py`

### 前端测试

扩展：

- `console/src/pages/Chat/tests/testLargeSession.test.ts`

建议新增：

- `e2e/tests/test_compact_chat_history_large.py`

### 验证报告

实现与测试完成后新增：

- `docs/0729-compact-chat-history/06-validation-report.md`

报告记录机器环境、命令、20/30/50MB 指标、分页决策和剩余风险。

## 4. TDD 顺序

### 阶段 1：索引范围

先写测试：

- 单层、多层 tier。
- 旧字段 `levels`。
- 相邻/重叠范围合并。
- 非法范围忽略。
- checkpoint session 不匹配。
- 空/损坏状态返回空。

再实现 `extract_index_ranges`。

### 阶段 2：行反序列化

先写测试：

- user/assistant 文本。
- 多 blocks。
- tool call + tool result。
- metadata/timestamp。
- 旧行无 blocks。
- 单行损坏跳过。
- `seq` 顺序稳定。

再实现 `history_rows_to_messages`。

### 阶段 3：只读查询

先用临时 SQLite 构造：

- 当前 session 索引内行。
- 当前 session 索引外行。
- 其它 session 同范围行。
- `/clear` 前遗留行。

断言只返回当前 checkpoint 精确范围。额外验证数据库文件 mtime、row count 和 schema 均未变化。

再实现 read-only 查询。

### 阶段 4：GET 集成

先写失败测试：

- Scroll：归档 + marker + tail。
- 多次压缩：多个范围仍全量、有序、无重复。
- Native：结果不变且不打开数据库。
- 缺库/坏库/缺行：状态码仍为 200，返回当前 context。
- running/idle status 不变。

再修改 GET。

### 阶段 5：大规格验证

创建确定性的 20MB、30MB、50MB 消息生成器。每档数据应：

- 使用多个 user/assistant 轮次，而不是只有一个巨大字符串。
- 包含少量 tool call/result，覆盖结构化 blocks。
- 以 UTF-8 JSON 实际字节数校准，误差不超过目标的 1%。
- 使用固定 seed，结果可重复。

## 5. 性能测试分层

### 5.1 后端 GET

对 20MB、30MB、50MB 分别测量：

- SQLite 只读查询与 Msg 恢复耗时。
- `GET /chats/{id}` 完整耗时，包含 Pydantic/JSON 序列化。
- 响应实际 UTF-8 字节数。
- `tracemalloc` 峰值 Python 分配。
- 返回消息数、顺序和压缩边界位置。

每档先预热一次，再正式执行三次，报告中记录 median 和 max。性能数字不作为普通单元测试的硬阈值；slow 测试设置宽松超时并断言无崩溃、结构正确。

### 5.2 前端转换

对同样规格调用 `convertMessages`，测量：

- `performance.now()` 转换耗时。
- 转换前后 `process.memoryUsage().heapUsed`。
- 输出卡片数量。
- user/assistant/tool 结构。
- 从 20MB 到 50MB 的增长倍率。

为减少噪声，每档预热一次、执行三次，报告 median/max。若运行环境支持显式 GC，则在采样前后执行；不支持时标记为近似值。

### 5.3 页面打开

Playwright 在 Chromium 中 mock 会话列表与 `GET /api/chats/{id}`，分别返回 20MB、30MB、50MB 历史。

测量：

- 从导航开始到最新一条消息可见。
- 从 GET response 完成到第一帧消息可见。
- CDP `Performance.getMetrics` 的 `JSHeapUsedSize`。
- 页面错误、console error、error boundary。
- DOM 实际历史气泡数量，确认仍受 10 条窗口约束。
- 切换到小会话、触发 GC 后的 retained heap。
- 再次进入大会话的耗时和 heap，检查多会话累积。

页面测试为 slow/benchmark 测试，避免拖慢普通 PR smoke suite。

## 6. 分页决策规则

完成测试后必须在 `06-validation-report.md` 明确记录“保持全量”或“触发分页”，不能只给原始数字。

触发分页的证据包括：

- 任一档崩溃、空白页、超时或无法完成转换。
- 50MB 的本机 GET、转换、页面可见耗时稳定越过需求文档中的决策线。
- 浏览器额外保留 heap 稳定超过决策线。
- 20MB 到 50MB 出现超过 4 倍的超线性增长。
- 多会话切换后的 retained heap 持续累积。

未触发时保留以下开发提示：

> Future pagination trigger: keep the current full-history GET until repeatable measurements show a loading or retained-memory regression at the documented 20MB/30MB/50MB benchmarks or in production telemetry. Then add an additive archived-history cursor API and a formal AgentScope history-page callback.

该提示写入大规格测试文件顶部和验证报告，确保后续维护者能发现，但本次不增加未使用的分页代码。

## 7. 回归测试

除新增测试外，至少运行：

- chats unit tests。
- Scroll history/index/manager tests。
- chats integration tests。
- 前端 `testLargeSession`。
- session cache staleness tests。
- Chat 页面相关测试。
- TypeScript build。
- 目标 Playwright 大会话 slow tests。

## 8. 风险与优化复查

### 风险：SQLite 展示读取意外写入

通过 read-only URI、mtime/schema/row-count 测试防止。

### 风险：恢复 `/clear` 前消息

只读 index spans，不按 session 全表恢复。

### 风险：blocks 损坏导致整页失败

按行降级；优先 blocks，失败后使用 content 文本。

### 风险：大会话前端内存

先用 20/30/50MB 实测。若达到决策线再分页，避免在没有证据时修改第三方组件。

### 风险：性能断言在 CI 抖动

正确性使用硬断言；性能使用重复采样和验证报告。只保留宽松超时与超线性回归守卫。

### 风险：Native 行为改变

无有效 Scroll checkpoint 时不进入 reader，并用 spy 测试数据库没有被打开。
