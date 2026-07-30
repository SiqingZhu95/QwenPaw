# 压缩前聊天历史恢复验证报告

日期：2026-07-30  
分支：`codex/v2.0.1-compact-chat-history`

## 1. 结论

本阶段实现已经验证了以下能力：

- 仅恢复 Scroll checkpoint 索引覆盖的压缩前消息。
- SQLite 读取使用只读连接；缺库、坏库、缺表或 retention 缺行时保持页面可用。
- 非 Scroll、无有效 checkpoint 和旧格式 session 保持原有行为。
- 展示恢复不修改 `AgentState.context`、session JSON、Scroll checkpoint 或 `history.db`，因此不会改变实际发送给模型的上下文。
- `GET /chats/{id}` 的路径、参数和 `ChatHistory { messages, status }` 响应结构未改变。
- 20MB、30MB、50MB 全量响应都能完成接口读取、转换和页面打开，页面仅渲染 10 条顶层消息。

性能决策为：**触发服务端分页设计，但不在本阶段直接实现分页。**

触发原因只有一项：50MB 全量历史首次显示耗时在 3 次重复测量中分别为
8.58 秒、8.48 秒、8.50 秒，稳定超过批准的 5 秒阈值。后端接口、前端转换、
内存放大和多会话切换均未触发阈值。分页接口属于新增契约，需要先批准
`07-server-pagination-design.md`，再按其中的 TDD 计划开发。

## 2. 验证环境

| 项目 | 值 |
| --- | --- |
| 操作系统 | Windows 10，10.0.19045，x64 |
| CPU | AMD Ryzen 9 3900X 12-Core Processor |
| 物理内存 | 34,305,429,504 bytes（约 31.95 GiB） |
| Python | 3.11.9 |
| Node.js | v24.14.1 |
| npm | 11.11.0 |
| Chromium | 149.0.7827.55 |

受限测试进程没有继承 Windows 的 `PROCESSOR_ARCHITECTURE`。进程级集成测试
首次启动因此在本地模型架构探测处失败；验证命令显式设置
`PROCESSOR_ARCHITECTURE=AMD64` 后，项目正常识别为 `x64`，314 个相关测试全部
通过。该问题发生在应用启动阶段，聊天接口尚未执行，仓库代码未为此做变更。

## 3. 测试数据和方法

- 每档构造 40 个 user/assistant turn，共 80 条消息。
- 使用 ASCII filler 校准最终 UTF-8 JSON 字节数，误差为 0。
- 后端基准使用真实 `HistoryStore`、Scroll checkpoint、`GET /chats/{id}` 组装
  和 Pydantic JSON 序列化；每档运行 3 次并记录 median、max 和 Python
  `tracemalloc` 峰值。
- 前端转换基准直接测量 `convertMessages`，同时记录 Node heap delta。
- 页面基准使用真实 Chat 页面和 Chromium；测量从 SPA 会话切换开始，到最新
  消息可见且 10 条顶层 bubble 已渲染为止。
- 页面测试在大、小会话间切换两轮，并在切回小会话后触发 Chromium GC，以
  区分当前页面内存、会话缓存留存和逐轮泄漏。

## 4. 后端接口结果

| 规格 | 实际响应 bytes | median | max | Python 峰值 bytes |
| --- | ---: | ---: | ---: | ---: |
| 20MB | 20,971,520 | 288.19 ms | 301.33 ms | 105,197,488 |
| 30MB | 31,457,280 | 403.09 ms | 416.24 ms | 157,578,405 |
| 50MB | 52,428,800 | 672.18 ms | 700.96 ms | 262,440,614 |

判断：

- 50MB median/max 均低于 2 秒阈值。
- 20MB 到 50MB 的耗时约增长 2.33 倍，低于 4 倍退化阈值。
- Python 峰值约为响应大小的 5 倍，主要来自 SQLite 行、`Msg`、Pydantic
  对象和最终 JSON 同时存活；绝对值在 50MB 档约 250.28 MiB。它没有单独
  触发已批准的分页规则，但说明全量组装不适合继续无界增长。

## 5. 前端转换结果

| 规格 | 输入 bytes | `convertMessages` | Node heap delta |
| --- | ---: | ---: | ---: |
| 20MB | 20,971,520 | 0.1949 ms | 93,040 bytes |
| 30MB | 31,457,280 | 0.1829 ms | 92,928 bytes |
| 50MB | 52,428,800 | 0.1816 ms | 92,928 bytes |

转换函数只重组约 80 条消息，并复用大文本字符串，没有复制全部内容，因此耗时
和额外 heap 都很小。该结果不包含 HTTP body 接收和 `JSON.parse`；这些成本已
包含在页面基准中。

## 6. 页面打开与多会话内存结果

| 规格 | 首次显示 | 缓存后二次显示 | 首次大页额外 heap | 第一次切回后留存 | 第二次切回后留存 | 顶层消息 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 20MB | 2,507.24 ms | 736.85 ms | 24,752,772 bytes | 20,890,924 bytes | 21,327,644 bytes | 10 |
| 30MB | 3,898.21 ms | 820.89 ms | 35,009,308 bytes | 30,493,808 bytes | 30,903,340 bytes | 10 |
| 50MB | 8,580.67 ms | 983.87 ms | 56,422,624 bytes | 50,589,844 bytes | 48,557,004 bytes | 10 |

50MB 首次显示的额外重复样本：

| 样本 | 首次显示 | 缓存后二次显示 |
| --- | ---: | ---: |
| 1 | 8,580.67 ms | 983.87 ms |
| 2 | 8,481.38 ms | 1,018.49 ms |
| 3 | 8,502.67 ms | 1,004.84 ms |

判断：

- 50MB 首次显示中位数约 8.50 秒，稳定超过 5 秒阈值，**触发分页**。
- 20MB 到 50MB 首次显示增长约 3.42 倍，未超过 4 倍退化阈值。
- 50MB 首次大页额外 JS heap 约 53.81 MiB，远低于 300MB 阈值。
- 切回小会话并 GC 后，留存量与原始 payload 大致相当，符合 SessionApi
  LRU 缓存预期。
- 第二轮切换后没有持续累积：20MB 和 30MB 仅有约 0.4MB 浮动，50MB
  反而下降约 1.94MB，没有发现会话切换型内存泄漏。
- 三档都严格渲染 10 条顶层消息；页面慢点来自首次全量接收、解析和大文本
  UI 构建，而不是 DOM 无界增长。

## 7. 正确性、回归和构建

| 验证项 | 结果 |
| --- | --- |
| Scroll history 与 Chat 相关后端回归 | 314 passed |
| 20/30/50MB 后端基准 | 3 passed |
| Chat 前端回归（默认不跑大规格） | 31 passed，3 skipped |
| 20/30/50MB 前端转换基准 | 32 passed |
| 20/30/50MB Chromium 页面基准 | 3 passed |
| TypeScript + Vite production build | passed |
| `git diff --check` | passed |

生产构建保留项目既有的动态导入和 chunk size 警告，没有新增编译错误。

## 8. 分页决策

逐项应用批准的规则：

| 规则 | 结果 | 是否触发 |
| --- | --- | --- |
| 50MB GET median/max 稳定超过 2 秒 | 672/701 ms | 否 |
| 50MB conversion 稳定超过 1 秒 | 0.18 ms | 否 |
| 50MB latest-message-visible 稳定超过 5 秒 | 中位约 8.50 秒 | **是** |
| 50MB 页面额外 retained JS heap 超过 300MB | 约 48–51MB | 否 |
| 20MB 到 50MB 耗时或内存超过 4 倍 | 耗时 3.42 倍，heap 约 2.28 倍 | 否 |
| 切换 + GC 后 retained heap 持续累积 | 未发现 | 否 |

因此，本阶段不再继续扩大 `GET /chats/{id}` 的全量历史负载。下一阶段采用：

1. 保持已发布的 `GET /chats/{id}` 路径、响应结构和 current-context 语义；
2. 新增只读、游标式压缩前历史接口；
3. 前端初始只加载当前上下文和满足 10 条展示所需的小页；
4. 用户向前浏览时再加载归档页；
5. 每会话缓存同时受消息数和字节数约束；
6. 分页恢复仍然只覆盖 Scroll checkpoint 索引范围，且不影响模型上下文。

具体契约和 TDD 计划见 `07-server-pagination-design.md`，批准前不编写分页代码。

## 9. 长期提示

Future pagination trigger: keep the full-history GET until repeatable
20MB/30MB/50MB measurements show a loading or retained-memory regression.
Then add an additive archived-history cursor API and a formal AgentScope
history-page callback.

本次 50MB 页面测量已经满足该提示中的触发条件，因此该提示已从“未来条件”
转化为下一阶段的设计输入；仍保留原文，供后续回归和阈值调整使用。
