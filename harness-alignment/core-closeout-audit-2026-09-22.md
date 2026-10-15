# A–E 当前收口审计（暂不宣称完成）

基准：Codex `ddf04ad26789d040f9ef6a96736f76602e35a6cc`、Corki
`e53a368705990cd025bdf2d404b88bb4197c5445`。本文件只解释当前源码和
已执行证据，旧审计中的“待实现”以其后续修复记录及现代码为准；471 项工作树
变更属于既有工作，不以清理它们为验收条件。CLI 风格/斜杆命令仍暂停。

| 范围 | 已有直接证据 | 当前不宜核销的部分及下一动作 |
| --- | --- | --- |
| A 主循环 | `runtime-lifecycle-acceptance.md` 的 Thread/Turn/Step、`ordered-tool-publication.md` 的 FIFO 前缀与真实进程退出冷恢复、`model_stream.py` 的完成终态所有权；非 CLI 全量基线见下文 | 不把外部操作“结果未知”冒称 exactly-once。Codex 默认多 Agent V1 的创建/通信/等待工具是否属于本阶段仍需明确能力范围；Corki 的 `SessionSource.subagent` 与 Hook 名称不是实际子 Agent 编排实现 |
| B 工具 | `registration-source-review.md`、`tool-exposure-review.md`、`tool-search-ranking-review.md`、`tool-result-contract-review.md` 和 `code-mode-acceptance.md` 覆盖普通函数的注册→搜索→定义加载→调用→Observation、曝光、失效与错误；MCP `Interrupt` root/replaced/cold、半截 HTTP 超时和结果提交前/后故障见 `mcp-tool-hook-gap.md` | B10 的 Hook 跨事件/来源不应继续写成笼统未知；须给出具体未验入口。MCP Pre/PostCompact 冷恢复及真实 HTTP 半截响应超时/取消已补，见 `compact-hook-gap.md`；进程强杀与其它未列事件仍需按具体故障窗口核验 |
| C 上下文 | `context-snapshot-wire.md`、`extension-context-producers.md`、`context-budget-review.md`、`compact-recovery-boundaries.md`、`local-history-recall-review.md` 有普通路径角色/顺序、快照、预算、普通摘要、冷恢复证据；默认来源/角色/窗口 483 项通过，同请求首次窗口及插件/工具目录增量变化的冷恢复复核见 `context-section-inventory.md`（后者相关 45 项通过） | `context-section-inventory.md` 的 Persistent、MultiAgent V2、跨机器环境属于缺少运行时支撑的条件能力，不能因插入提示词或模型元数据就称已实现；实时 direct text 与原生 intermediary 角色不同。先按实际启用条件/用户范围核定；已验组合不等于全部条件能力/状态转移的最终核销 |
| D 记忆 | `memory-source-review.md`、`memory-read-feedback-review.md`、`memory-forgetting-review.md`、`memory-close-recheck.md` 已逐项覆盖提取/合并/lease/反馈/污染/后台关闭的确定性 Harness 语义 | 不把模型事实质量、跨文件事务或不协作外部写入当成已有保证；仅在发现具体可复现的所有权/恢复差异时再修复 |
| E 错误与恢复 | `error-classification-acceptance.md`、`model-failure-acceptance.md`、`tool-failure-acceptance.md`、`runtime-close-failure-acceptance.md` 覆盖分类、普通 HTTP、部分输出及资源关闭 | 旧索引称运行中 loop 的同步 create 待决已失效：`construction.py` 现在分配前 fail-fast，`acreate` 拥有异步回滚，`sync-in-loop-construction-decision.md` 与后续全量有证据。未知外部副作用保持明确 unknown，不造 exactly-once 结论 |

最新生产改动后的非 CLI 全量已实际退出 0：**15699 passed、7 skipped、
583.67s**（12 workers/loadfile/禁重启）。本轮修正默认上下文片段顺序；
同一请求组合及相关上下文 **496 passed**。六项
跳过需已不存在的历史原生编译器，一项为当前文件系统不接受非 UTF-8 名称。
此前新增的 MCP 压缩 Hook HTTP 故障测试也已进入这轮全量；其六文件
219 通过是另一项定向证据，不能与全量简单相加。本轮随后新增的
`Interrupt` MCP 三例只做了 74 项关联回归，尚不在 15699 的全量内。
测试均为离线 Harness/MockTransport；
真实供应商工具选择质量和真实外网服务并未由这些结果证明。

下一步优先事项：

1. 对 C1/C2 默认普通路径的 section 逐项核对当前源码与 Codex 初始/增量
   顺序、生命周期和旧快照降级，给出明确一致/差异，而非延续“其他来源”占位。
2. 明确可选 Persistent、多 Agent、跨机器执行环境的范围；若纳入，需真实
   调度/执行与权限边界，不能只补 prompt。没有范围结论前不据此宣布 A–E 完成。
3. 若发现仍开放的 B10/E 故障窗口，先描述可到达的触发点与实际反例，避免
   对已证明的窗口无限追加同构排列。

用户排除项仍以 `objective-core-paused-2026-09-21.md` 为准：不恢复原生
tool search/namespace、专用远程压缩、官方账户/配额/服务。普通兼容模型和
第三方 MCP OAuth 各有独立凭据与网络边界，不能从文件名或旧历史字段反推
当前会调用官方端点。

本轮额外执行默认端点、模型能力统一路径及通用 MCP OAuth 刷新的三个
离线集成文件，**218 passed / 38.97s**（4 workers/loadfile/禁重启）。
`src/corki` 中对 `api.openai.com`、`auth.openai.com`、ChatGPT backend API、
`/responses/compact` 和两个官方组织/项目请求头的定向搜索无匹配；
这只是字面量检查，不能替代上述离线请求目的地/凭据断言，也不覆盖宿主
自行注入的任意 `ModelPort`。
