# Codex → Corki Harness 核心对齐审计

## 基准与完成规则

- 目标原文：[objective.md](objective.md)。本文件是持续审计，不是完成声明。
- 开始日期：2026-09-06。Codex：`/Users/corki/IdeaProjects/ad/codex`，commit
  `ddf04ad26789d040f9ef6a96736f76602e35a6cc`；开始时 `git status --short` 为空。
- Corki：`/Users/corki/PycharmProjects/corki`，不是 Git 工作树。修改前受检文件散列保存于
  [baseline.sha256](baseline.sha256)，不覆盖原有文件来建立基准。
- 已完整读取 Codex 根 `AGENTS.md` 与 Corki `development.md`、README、pyproject。
  Corki 及其路径祖先没有 AGENTS.md；Codex 子目录说明仅检索到 TUI bottom_pane，当前不涉该目录。
- Codex 保持只读；`teach.md` 不修改，初始 SHA-256：
  `816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a`。
- Corki 旧 development.md 中“已对齐/已完成”不是证据；其中旧里程碑的 Code Mode、feature gate
  排除不能覆盖本次用户目标。此次不重建 UI、OS sandbox、多 Agent 产品或语音系统，但与发现、
  上下文、记忆、执行安全直接相关的接口/行为仍在审计范围。
- 初始 `.venv/bin/pytest -q`：161 个测试通过，exit 0。只证明旧回归基线，不证明对齐。
- 所有源码路径以下默认相对于各自仓库。Codex 测试目前仅阅读，未运行 Rust 测试；没有运行真实模型。
- 行为结论只使用“一致 / 部分一致 / 缺失 / 行为不一致 / 不适用”。尚未完整追踪的项目
  单独标记“待审计”，不能据此判定一致或不适用。源码对应测试只是候选证据，运行后才记录通过。

## A：主循环与 Runtime（首批调用链已追踪，完整验收未完成）

Codex 普通路径：`tasks/regular.rs::RegularTask::run` →
`session/turn.rs::run_turn` → pre-sampling compact → capture StepContext → world state / hooks / user
input → 循环 capture StepContext → history.for_prompt → `run_sampling_request` →
`try_run_sampling_request` → `stream_events_utils.rs::handle_output_item_done` →
`tools/parallel.rs::ToolCallRuntime` → `drain_in_flight` → usage / pending input / compact / follow-up。
`tasks/mod.rs` 拥有 task 完成与 abort 生命周期；后者的完整关闭调用链仍待继续核查。

Corki：`core/runtime.py::stream/resume_pending/_run_graph` → `core/graph.py::compile` →
prepare → call_model → evaluate → execute_tools / finalize / fail。业务库在
`storage/sqlite.py`，graph checkpoint 在独立的 SQLite；不能把两者当成同一事务。

| ID / 场景 | Codex 行为、源码/测试 | Corki 当前行为、源码 | 结论、影响、优先级 | 修复 / 验收 |
| --- | --- | --- | --- | --- |
| A01 混合正文、reasoning、工具 | `turn.rs::try_run_sampling_request` 分项处理，工具设置 needs_follow_up；`stream_events_utils.rs::handle_output_item_done` | `graph.py::_call_model` 保留 canonical items；`evaluation/guards.py::evaluate_model_step` 工具优先于正文 | 部分一致；缺少完整分项流/终态契约验证，P1 | 测试混合输出、不把 commentary 当最终回答、reasoning 保留与 tool 回灌 |
| A02 流中工具执行 | 完整 OutputItemDone 即记录调用并启动 tool task；FuturesOrdered 排序、RwLock 并行/独占；`tests/suite/tool_parallelism.rs` | 只在 ModelCompleted 后执行；`_execute_tools` 连续并行批次 gather | 行为不一致；延迟、断流后的副作用/历史语义不同，P1 | 增加 item-completed 协议及 durable partial-step 状态后接入实际循环；证明工具可先于模型终态启动且不会重放 |
| A03 非工具续接 | Completed.end_turn=false 使 needs_follow_up=true，pending input 也可延续；`turn.rs:2667` | 模型标志持久化/CONTINUE、合法空 completed、工具优先；finalize 对比未采样 durable 输入并原子检查 queue/关闭输入 | 部分一致；显式续接及已定位输入终态/恢复窗口已修复验证；Codex mailbox/hooks 等分支仍未完整映射，P1 | mock Responses、预算/协议失败、真实 checkpoint、终态前输入续接、终态后拒绝与 CLI 下一 Turn 保留均通过 |
| A04 Step 工具视图 | `StepContext` / `ToolCallRuntime` 保留同一个 router，执行引用原 step | state.request_tools 仅固定 specs；执行重新 registry.get，parameters 只浅拷贝 | 部分一致；不能声称 handler/schema 深度冻结，P1 | 明确注册版本、deep snapshot、恢复失效策略；schema/handler 变化测试 |
| A05 实时取消的任务所有权 | `parallel.rs` AbortOnDropHandle + cancel/await；`turn.rs` or_cancel + drain 后传播 TurnAborted | 原实现 wait 子任务无 finally；本批新增 `core/model_stream.py::model_events`，graph、两 provider 显式关闭内层 iterator | 部分一致；本批取消/完成/HTTP stream 泄漏已复现并修复，更多 Runtime 关闭语义仍见 A08，P0 | 新增 parent cancel、双 waiter、普通/realtime completed、两 HTTP adapter closure 用例已通过；不据此声明全部关闭路径一致 |
| A06 并行异常 | `ToolCallRuntime` 持有并回收执行任务，fatal 与 observation 分离；`turn.rs::drain_in_flight` | 原 gather 的存储异常遗留 siblings；本批 graph 在 finally cancel + join，保留 gather 的结果顺序 | 部分一致；本批 claim/complete 故障时 sibling 存活缺陷已修复，完整 fatal/Observation 语义仍需继续对齐，P0 | 两个新增存储故障用例通过，验证 TurnFailed 前 sibling 已结束、unknown claim 不重放；已有 parallel/recovery 回归通过 |
| A07 模型/工具恢复 | Codex rollout 恢复、partial outputs、executed-tool metadata 的全链仍待追踪，不能假定和 SQLite checkpoint 一致 | `load/commit_model_step` 原子业务记录；tool claim 完成复用；call identity 未记录参数；stable result item id | 待完成 Codex 侧审计；现有用例 `test_recovery_and_concurrency.py` / `unit/storage/test_recovery.py` 不能覆盖全部窗口 | 逐窗口 fault injection、旧库/旧 checkpoint、参数碰撞；分别记录本地幂等与不可保证的远程 exactly-once |
| A08 Turn 完成/取消/backpressure/close | `on_task_finished:620-650` 原子移除 task、记录 pending；`steer_input` lock 内验证 task；handle_task_abort 全链待继续核查 | 独立 producer_done；输入关闭与 active 所有权分离；失败/取消补写未 ack 输入，NodeCancelledError 还原取消；aclose 仍无 active-task 所有权 | 部分一致；已定位输入丢失与取消误报已修复，active close/非 realtime cancel 和所有资源异常仍未关闭，P0 | 输入 10 个集成窗口、controller/CLI、此前 backpressure 用例通过；下一批继续 active runtime close 与取消所有权 |

## B：工具发现、召回、选择（首条检索链已重新核实）

Codex：`tools/spec_plan.rs::build_tool_router` / `finalize_tool_router` →
`search_tool_enabled`（model.supports_search_tool AND provider.namespace_tools）→
`handlers/tool_search.rs::ToolSearchHandlerCache::get_or_build` → deferred entries →
BM25 English documents → `handle_call` query / limit → `ToolSearchOutput::to_response_item` →
history → 下次请求。`tools/src/tool_search.rs` 定义默认名称/描述/递归 schema 搜索元数据。

| ID / 场景 | Codex 行为、源码/测试 | Corki 当前行为、源码 | 结论、影响、优先级 | 修复 / 验收 |
| --- | --- | --- | --- | --- |
| B01 Deferred 注册曝光 | `tools/src/tool_executor.rs` 曝光策略；`spec_plan.rs::build_model_visible_specs` 只直接曝光 direct | 已接入 ToolSearchTool / ToolPlan；deferred 和 deferred_model_only 可检索，hidden/code_mode_only 不进入结果 | 部分一致；按需检索闭环已实现，Code Mode 仍见 B06 | 真实 Runtime 首轮无 deferred schema、搜索后定义可用、调用回灌和跨 Turn 测试通过 |
| B02 检索/选择 | `handlers/tool_search.rs` BM25，默认 limit=8，空 query/zero limit 错误；索引缓存比较 immutable identity / dynamic search_info / source listing | `tools/search.py` 有 BM25、recursive schema/source 元数据、top-k、按完整 specs 缓存失效；8k token 整体结果预算 | 部分一致，P1；尚无 pinned Rust tokenizer 的 stemming/stopword/deunicode 等价实现 | metadata/schema、排序、no-match、top-k、缓存命中/定义变化/删除、参数错误/超大 schema 测试通过；精确分词差异未关闭 |
| B03 原生搜索协议 | `tools/context.rs::ToolSearchOutput`；`tests/suite/search_tool.rs:748-829` 要求 schemas 留在 output history，不注入顶层 tools | `models/tool_search.py` + Responses adapter 已实现 native call/output；compatible 使用普通 function 调用与命中 schemas 注入 | 部分一致；客户端主链已验证；模型能力使用显式设置而非远端 model catalog | mock Responses 两模式三 Step 实际 Runtime 测试通过，验证 native schemas 只存在于 output history；未执行真实模型 |
| B04 检索后的生命周期 | 原生历史保存搜索输出，normalize 修补空输出/移除孤儿；world-state resume tests | discovered_tools 进入 canonical result / tool ledger；active history 重建加载；压缩重算计划；模型视图过滤失效定义，raw history 不改写 | 部分一致；本批业务恢复、实际 graph checkpoint 与压缩用例通过；动态服务器恢复仍属 B05 | search ledger 已提交/历史已追加/已保存 graph checkpoint 三个窗口恢复通过；无重复采样、搜索、执行；小窗口压缩释放 schemas |
| B05 MCP 与扩展元数据 | `handlers/mcp.rs::search_info/build_mcp_search_text`，server/connector/plugin/name/schema；spec_plan 控制 MCP exposures | Runtime 默认 deferred MCP，disabled 时 direct；MCP source/title/原名/完整描述/属性进入检索；plugin 支持 exposure 参数 | 部分一致，P1；flat canonical 名称保留；运行中 MCP 更新/重连、完整 omit surface policy 未对齐 | stdio MCP + skills + plugin 组合 E2E 改为先搜索再调用；源目录有界且不全量公开 deferred 名称 |
| B06 Code Mode | `code-mode-runtime/src/runtime/globals.rs` 的 tools / ALL_TOOLS；`spec_plan.rs` code_mode gates 与 exposure 区分（本轮完整链尚待重读） | 尚无执行环境，CODE_MODE_ONLY 只是枚举 | 缺失，P1；不能依赖旧文档排除目标 | 审计适用 gate、嵌套工具发现与恢复/取消；没有实现不能声称 Code Mode 对齐 |
| B07 执行身份/错误/schema | `router.rs` / `registry.rs` / `parallel.rs` 的 payload、unknown、fatal/observation 需继续逐项追踪 | executor schema subset；畸形 JSON/handler Exception 可观察；结果内容归一化在 try 外 | 部分一致，P1 | 结果非法类型/内容、JSON/schema、工具失效/超时/超限逐项测试；隐藏与未加载不是同一安全概念 |

## C / D / E：保留全部审计范围，尚未完成的链路

下面不是“不适用”清单，也没有默认认定已有功能正确。每条在源码完整追踪后转成带行为结论的差异项。

| ID | 必须追踪和验收的范围 | 待检查位置（仅定位，不当作结论） |
| --- | --- | --- |
| C01 | instructions、项目规则、环境、扩展、角色顺序、每 Step stable key / tombstone / snapshot | Codex session context/world-state、context_manager；Corki context/builder.py、prompting/assembly.py |
| C02 | 完整请求 token、output reserve、schema、图片与 reasoning 成本、硬上限 | Codex session/context_window、compact_token_budget；Corki context/tokens.py、window.py |
| C03 | 手动/自动/pre-turn/mid-turn 压缩触发、输入保护、摘要失败 | Codex compact.rs、compact_remote*.rs、turn.rs::run_auto_compact；Corki window.py、CLI compact 入口是否存在 |
| C04 | summary replacement 与 append-only 原始记录、tool pairs、reasoning、新输入、取消/恢复 | Codex context_manager/history.rs、normalize.rs、compact_resume_fork tests；Corki history.py、storage/sqlite.py |
| C05 | 连续历史、历史搜索、长期记忆召回边界；原生/provider 特定 compact 路径 | Codex history / memory extension；Corki memory/service.py、context/window.py |
| D01 | 短期 Thread history、工作 state 与长期来源筛选/生成触发 | Codex memory 相关 crate/extension 待定位；Corki memory/pipeline.py、service.py |
| D02 | extraction / consolidation、claim/lease/owner/source version/watermark/去重/失效 | Codex memory/state DB；Corki memory/sqlite.py、repository.py、artifacts.py |
| D03 | summary 注入、搜索、细读、来源引用、usage；显式记住/更新/遗忘 | Codex memory tools/templates；Corki memory/backend.py、tools.py、protocol/memory.py |
| D04 | 外部污染、后台隔离、失败/取消/关闭、发布一致性 | Codex stream_events_utils pollution / memory jobs；Corki pipeline/artifacts/runtime |
| E01 | 错误分类：Observation / retry / fatal / cancel | Codex function_tool、registry、parallel；Corki tools/executor、models/base、runtime；A05/A06/B07 是首批入口 |
| E02 | HTTP/SSE/部分输出/无终态/重试与副作用 | Codex responses_retry.rs：支持基于历史重试，WS fallback 到 HTTPS，有实验性 unbounded connection retry；Corki 两 adapter 只在未输出前重试；两者行为不一致，完整修复依赖 A02 |
| E03 | 压缩失败、memory 失败、扩展 init 部分注册回滚、工具失效/超时 | Codex compact/memory/MCP；Corki context/memory/mcp/plugins，按失败点注入 |
| E04 | 所有终态和 close 路径、一次终态、无未知结果重放 | 与 A05–A08 联合验收，不允许局部修复替代全链证明 |

## 实施顺序与验证账本

1. 先复现并修复已定位的任务所有权问题 A05/A06，避免后续流式发现/执行建立在泄漏的基础上。
2. 继续完整审计 B 的曝光/路由/原生协议/压缩恢复，补齐协议与按需检索主循环。
3. A 的分项流、continuation、恢复及 retry 与 B 联合实现；随后逐条完成 C、D、E 未审计项。
4. 每批新增失败测试 → 记录修复前失败 → 接入 Runtime → 通过对应测试/静态检查 → 更新本表。
5. 最终按 objective.md 全条目重审，运行完整 regression 和组合 E2E；真实模型质量验证独立列出。

当前：审计进行中；未开始宣称全局对齐。未关闭项目仍是有效目标，不因跨 Turn 或工作量缩减。

### 首批故障复现（A05/A06，2026-09-06）

- `tests/integration/test_task_ownership.py` 新增 5 个用例，修改产品代码前全部失败：
  realtime parent cancel 未关闭 response；普通/realtime 两种采样越过 ModelCompleted 读尾部；
  parallel tool claim/complete 两种数据库故障都遗留 sibling 工具。
- 已加入 `core/model_stream.py::model_events` 的 iterator/task 所有权，并由 graph 实际调用；
  `_execute_tools` 在 gather 退出时取消并 join siblings。上述 5 例及既有 realtime/recovery 共 16 例通过。
- 继续追踪发现适配器的外层 `stream` 在 yield 处关闭时，没有显式关闭内层 `_stream_once`；
  HTTP response context 在内层，故 graph 关闭 response iterator 仍不足以释放 HTTP。
  `unit/models/test_transport_reliability.py::test_closing_partial_model_iterator_closes_underlying_http_response`
  的 Chat/Responses 两例已在适配器修复前复现失败。修复方案：明确嵌套 async generator 所有权，
  同步等待内部 aclose；不能依赖 GC 或 asyncio.run 收尾。
- A08 的有界队列组合测试另复现 2 例失败：普通/realtime 的 public runtime.stream 被消费者
  aclose 时，内层 `_run_graph` 没有被同步关闭，graph/response 仍活跃。现有 backpressure 测试
  只检查不死锁，未在关闭返回时断言资源终态。修复范围是 stream/resume_pending 对内层 generator
  的所有权；独立调用 runtime.aclose 时如何结束活跃 Turn 仍需另行审计，不能顺带判定已完成。
- A08 开始事件边界又复现 2 例失败：收到 TurnStarted 就关闭消费者，因 yield 在 try/finally 外，
  业务 Turn 永久 running、realtime activation 不释放。将首事件放进已持有 producer 的收尾作用域；
  用普通/realtime 两例验证 durable turn 不再 running，不以 UI 退出代替持久化终态。

### 本批完成验证与后续入口

- 新增 11 个测试实例全部先看到修复前失败，修复后通过：9 个 Runtime 集成实例 + 2 个 HTTP adapter 实例。
- 完整 `.venv/bin/pytest -q -ra` 通过（172 个测试，exit 0）。包含既有三个 examples 的 subprocess E2E；
  它们不覆盖尚未实现的工具检索，不能当作本次完整组合验收。
- `.venv/bin/ruff check src tests examples main.py`、`ruff format --check`（131 files）、
  `python -m compileall -q src`、`pip check` 均通过。
- 对初始散列核对：既有文件仅 graph.py、runtime.py、两个 provider adapters、transport 测试变更；
  新增 model_stream.py、task_ownership 测试及本审计目录。Codex 仍干净，teach.md 散列不变。
- 未声称 A 全部对齐：活跃 runtime.aclose / cancel_active 非 realtime、resume 更多故障窗口、
  分项流工具执行、partial-step retry、continuation、工具视图深冻结仍需核查或修复。
- 下一批入口：先读完 Codex `spec_plan.rs` 的注册/exposure policy、`router.rs`、
  `handlers/mcp.rs`、`tools/context.rs`、`mcp_tool_exposure.rs` 的恢复/移除测试，以及
  context_manager 对 ToolSearchOutput 的压缩/归一化处理；再审 Corki 对应协议/持久化/请求计划。
  B01–B07 尚未修复，C/D/E 全部保留，不因本批测试全绿而关闭目标。

## 第二批实施前决策：工具发现（B01–B05）

已继续读取 Codex `spec_plan.rs:125-267`（core/MCP/extensions/dynamic 注册及 MCP surface policy）、
`router.rs:177-292`（native search call 与 namespace 路由）、`tools/context.rs:183-220`（原生 output）、
`handlers/mcp.rs::search_info/build_mcp_search_text`、`context_manager/normalize.rs` 的 search pair 修补。
`tools/src/tool_search.rs` 的 plain function 本就可包装进默认 `functions` namespace；Corki 首先沿用
其已有 flat canonical 名称，原生搜索输出使用这一合法路径，不把 flat MCP 名称误称为 Codex connector namespace。

- 原生启用依赖模型和 provider 支持。Corki 不根据 URL 猜测模型支持情况：默认 compatible function
  discovery；显式 native 设置表示使用者确认当前 Responses provider/model 支持原生 search + namespaces；
  disabled 保留 direct 路径。native 设置用于真实 adapter，不是只有枚举。
- deferred 结果保存真实 ToolSpec 到 canonical result 和 tool ledger；从 active history 重建兼容已加载集合，
  不引入脱离 checkpoint 的内存名单。压缩删除结果后可再次搜索；重启保留未压缩的结果。
- 返回 schemas 与候选 request tools 均参与预算。压缩后重新计算已加载集合，避免旧 schemas 使
  compacted request 仍虚假超限；搜索输出有硬预算，不截断半个 JSON/schema。
- registry seal 固定 specs，模型快照深拷贝；恢复时发现 spec 不同则拒绝执行旧定义，不能按新 handler
  schema 默默执行旧调用。动态 MCP 运行中更新/恢复机制仍未完成，另保留 B05 待办。
- 搜索只针对 deferred/deferred_model_only，Hidden/CodeModeOnly 不混入；模型决定何时搜索及调用哪个结果，
  Harness 用 BM25 做词法排序。Codex pinned bm25 2.3.2 还包含 English stemming/stopwords/deunicode，
  本地未缓存该依赖；首批 Python scorer 的 tokenizer 差异必须明确记录，不能宣称排序逐分数一致。
- 此批仍不关闭 Code Mode、分项流执行、全 context/memory 审计，也不把原生 mock 验证称为真实模型选择质量。

### 第二批实现与证据

- 修改产品代码前，`test_search_load_call_observation_and_cross_turn_history` 已失败于首轮根本没有
  tool_search 入口。现已通过真实 Runtime 四次请求（搜索→调用→完成→同 Thread 下一 Turn）。
- `test_responses_search_wire_roundtrip_without_eager_schemas`：compatible/native 两模式均经真实
  HTTP adapter + mock transport + Runtime + ledger；native 初始 search schema、output namespace/完整参数、
  defer_loading、后续顶层不注入定义、实际调用结果均有断言。Repeated done/completed 输出不重复调用。
- `test_search_ledger_resume_restores_definitions_without_reexecution`：账本完成/历史写入/真实 LangGraph
  SQLite checkpoint 三个窗口。第三种测试额外复现 MsgPack 将 discovered_tools tuple 还原为 list，
  已在 canonical dataclass 构造器归一化；不是只测手工状态字典。
- `test_search.py`：递归 schema metadata、来源/曝光过滤、top-k/no-match、cache 失效、参数 Observation、
  schema 整体预算（不受普通输出截断破坏）、失效定义 request-view 过滤、注册/请求深拷贝和旧 spec 拒绝。
- `test_discovery_compaction.py`：2,000 token 窗口，原搜索定义与当前输入各自能放入窗口但合并会超限；
  真实 ContextWindowManager 压缩旧历史、完整保留当前输入，释放旧 definitions 后重算工具预算并持久化。
  初始 800-token 测试还暴露旧 summarizer 的单 item + instructions 硬限制；该独立 C02/C03 边界仍待审计，
  不声称本批解决任意小窗口/任意大 schema 的摘要问题。
- `examples/extensions_demo.py` 与其 subprocess E2E：默认 deferred 的真实 stdio MCP 必须先搜索；
  再执行 skills / plugin / MCP 并回灌。没有用 disabled 配置绕开新行为来维持旧示例通过。
- 旧 JSON tool result 缺少 discovered_tools 时继续使用空值，重新写入空值时省略新键，保持老 item 的
  幂等语义；没有新增 SQL 列，无破坏性迁移。ToolSpec 新字段有默认值；checkpoint 构造归一化已验证。
- 本批工具 metadata/source prompt 可超过 1k tokens：search source 描述截断到 4,000 字符，MCP inventory
  截断到 8,000 字符；搜索结果最多约 8k tokens，截断单位是完整 tool definition，不是半个 schema。
  Context 的统一单 fragment 上限与所有 contributor 预算仍归 C01/C02，不能由这些局部上限替代。

后续仍保留：B02 pinned tokenizer parity，B05 运行时动态 MCP 恢复/移除与完整 source surface policy，
B06 Code Mode，A 分项工具流/continuation/partial-step retry/active close，以及 C/D/E 完整源码审计。
不能因为这次工具搜索闭环可用就宣称 Harness 整体对齐完成。

第二批最终检查：完整 `pytest -o addopts='' -q` 为 **193 passed**（9.49s，exit 0）；
`ruff check src tests examples main.py`、`ruff format --check`（137 files）、`compileall`、`pip check`
均通过。单独执行 `examples/extensions_demo.py` 返回 status=ok、mcp_discovered=true，首请求无 MCP
echo 定义、后续请求有该定义。修正 notice 文案后的 search 局部回归再次通过。
Codex 工作树仍干净，`teach.md` SHA-256 仍为初始值。

本目标上一执行轮及本轮均属于 progress（实际代码、先失败的回归及源码证据推进），不存在待外部输入的阻塞。
下一轮先解决 B02 的 tokenizer 差异并核查 B05/B06；全目标保持 active，C/D/E 不能从旧文档推断为已完成。

## 第三批实施前决策：模型续接与终态（A03 / E02）

优先处理已定位的主循环提前结束问题，B02/B05/B06 等仍保留，不更改完整目标。
Codex 基准 commit 仍为 ddf04ad26789d040f9ef6a96736f76602e35a6cc，工作树干净。

- Codex `codex-api/src/sse/responses.rs:118-127,490-514` 将 response.end_turn 解码为
  Option<bool>；类型错误是协议错误，不做字符串/整数真值转换。
- `core/src/session/turn.rs:2630-2675` 将 Some(false) 转为 needs_follow_up，
  `run_turn:444-610` 合并 pending input 并选择继续/压缩/停止。是否有正文不是完成条件。
- `core/tests/suite/current_time_reminder.rs::current_time_reminders_can_follow_only_user_or_tool_outputs`
  覆盖工具调用→正文+end_turn=false→空 completed，实际请求三次且不伪造用户/工具消息。
- Corki `models/responses.py` 丢弃该字段，`ModelCompleted`/model_steps/CorkiState 无对应状态；
  `evaluate_model_step` 有正文直接 FINALIZE、无正文无工具直接 FAIL。状态：行为不一致，P0，
  会把中间回答误作任务完成，并阻止 reasoning-only/empty 的合法续接。
- 方案：typed optional bool 贯通 Responses→ModelCompleted→原子模型账本→checkpoint→evaluate；
  增加 CONTINUE 到 prepare 的边，工具仍优先执行，显式 false 的续接受 max_steps 限制；
  未提供字段不凭正文猜测继续。有效 completed 可空内容终结，缺失 completed/流截断仍失败。
- model_steps 用 nullable 新列做兼容迁移（旧记录 None），不借用 provider_metadata 保留字，
  幂等提交必须比较续接标志；旧 checkpoint 缺字段按 None，下一模型结果覆盖上一步标志。
- 验收：真实 mock Responses+Runtime 测 text/reasoning/empty 续接、混合工具优先、空完成、
  类型非法不重试、预算停止、模型账本完成窗口和真实 LangGraph checkpoint 恢复不重复采样/输入。
- pending-input 终态竞态、分项流工具执行、partial-stream retry 尚不能由该标志修复证明一致。
- 空 completed 放行必须同时收紧 adapter 终态验证：Codex ResponseCompleted 的 id 是必需 String；
  Corki 旧 `event.get("response") or {}` 把缺失/null/false/list 等伪造成空对象，之前由空正文
  guard 偶然报错，不能继续依赖它。先复现再修正为 dict + 必需 string id；无 completed 仍失败。

### 第三批实现与证据

- 产品代码修改前，新增 mock Responses+真实 Runtime 测试 **15 failed / 1 passed**：
  text 提前结束、reasoning/empty 误报失败、budget 未续接、非法 flag 被接受、合法空终态失败、
  Codex 三次采样序列只请求两次；已全部修复。混合正文+工具且 end_turn=true 原本即通过，保留回归。
- 核心改动：`models/types.py::ModelCompleted.end_turn`，`models/responses.py` 严格解码，
  `evaluation/guards.py` CONTINUE（工具优先），`core/graph.py` 回到 prepare，
  `core/state.py` / runtime 初值 / SQLite nullable column 与原子 ledger 幂等比较。
- `tests/integration/test_model_continuation.py` 20 个实例：16 个 wire/Runtime 场景，以及
  text/empty × 无 checkpoint/真实 checkpoint 的 4 个恢复窗口。恢复只采样下一步，旧用户输入、
  中间模型项各保留一次；没有用人工 state 字典代替 checkpoint。异步 checkpoint write
  必须在 invocation 退出并 join 后检查，避免读到前一个 checkpoint 的测试竞态。
- `tests/unit/storage/test_recovery.py` 新增 4 个实例：None/false/true 持久化、幂等冲突、
  旧 model_steps 无新列的真实 DDL 迁移、重复启动不改变旧 None 语义。
- 空合法终态调整后，额外先复现 **6 failed** 的畸形 completion 解码测试，再收紧 response
  必须为 object、id 必须 string。正常 mock fixture 补齐真实协议 id，未删除其 schema/限额断言。
- `test_guards.py` 原先要求合法空完成报错，与 Codex 证据冲突，现更改为有效空完成可 FINALIZE；
  这不是凭空放宽失败：malformed completion/缺终态/HTTP failure/partial stream 的独立回归继续保留。
- `examples/extensions_demo.py` 扩为搜索→skill/plugin/stdio MCP 执行→正文+false 续接→最终回答，
  subprocess E2E 检查确实第四次请求且最终答案不是中间文本。未声称真实模型会自主选择此序列。

第三批全量验证（组合 demo 扩展前）：**223 passed**，12.72s；ruff check/format（138 files）、
compileall、pip check 通过，teach.md hash 不变。最终组合 demo 和回归结果见后续追加记录。

下一步继续 A03/A08 的 pending-input 接受与终态关闭边界：当前 realtime controller 在最终
graph 完成到 runtime finally 之间仍 active，steer 可接受但随后 deactivate 丢弃；需要先用
确定性故障窗口复现，完整设计最终输入关闭/拒绝语义，不能仅在 evaluate 查看一次队列。
A02 分项流/partial retry、B02/B05/B06 与 C/D/E 全范围继续有效；本批不是全局完成声明。

第三批最终复验：组合扩展 demo 返回 status=ok、mcp_discovered=true、model_continuation=true；
完整 pytest **223 passed**（12.14s，exit 0），ruff check、format --check、compileall、pip check
全部通过。Codex 工作树仍干净，teach.md hash 与初始值一致。真实模型与 Rust 测试未运行。
上一 goal turn 仅重述 /goal，判定 no progress；本轮有源码证据、实际修复和红→绿回归，判定 progress。

## 第四批实施前决策：输入接收与 Turn 收尾（A03/A08/E04）

- Codex `session/turn.rs:460-470` 合并 pending input 和 model follow-up；
  `session/turn_input.rs::steer_input:521-584` 在同一 active_turn lock 内检查活跃 task、
  expected_turn_id 和 steerable kind，再加入输入；没有 task 时返回 NoActiveTurn。
  `session/turn_input_tests.rs::steer_only_requires_active_turn` 是拒绝路径证据。
- `tasks/mod.rs::on_task_finished:620-650` 在 lock 内 take active task，然后 drain pending
  input 并 run_hooks_and_record_inputs，最后发终态并清除 active_turn（844-866）。
  接收与关闭有明确边界；“steered”不等于已被模型消费，但剩余输入不能无记录地丢掉。
- Corki controller.active 直到 runtime finally 才清除，graph._finalize 不检查输入；
  model completed 后到 evaluate/finalize 间输入可丢，terminal event 已发出时 steer 仍可成功。
  take_pending 在 I/O 前清空队列，persist 中断也会丢掉输入。状态：行为不一致，P0。
- 方案：controller 增加原子的 finish_if_idle / close_input（不 await），steer 的检查与
  put_nowait 同步完成。graph finalize 有 pending 时回 prepare，受 step budget 约束；
  无 pending 时关闭接收。运行时异常/退出同样先关闭，再保存尚未记录的已接收输入。
- 排队输入携带稳定 UserMessageItem；dequeue 不等于 persisted，只有历史写入完成才 ack。
  保存原始剩余输入不触发模型、工具或压缩，避免失败/取消收尾增加副作用；不声称进程硬崩溃
  前仅在内存排队的输入已经 durable（Codex 此队列也在内存中）。
- CLI 处理特定“Turn 已关闭”错误，将迟到输入留为下一轮，不把 RuntimeError 一概吞掉。
  Runtime.steer 仍是 steer-only，不暗中启动新的 Turn。
- 验收：模型提交后/evaluate 后/finalize 前新输入→再采样；终态持久化中/终态 event 后拒绝；
  预算耗尽、cancel、模型失败仍保留已接收输入；历史写入成功但 ack 前中断不重复；CLI 迟到输入续交。
  非 realtime cancel_active 和 active aclose 任务所有权尚独立未关闭，不由本批替代。
- 新测试进一步复现：当前安装 LangGraph `errors.py::NodeCancelledError` / pregel retry 层会把
  node 内主动抛出的 asyncio.CancelledError 包成普通 Exception，Corki 错把 stop 标成 TurnFailed。
  需要在 graph invocation 边界明确还原 cancellation；不能只处理父 task.cancel 的路径。
- 恢复进一步检查：输入写入历史成功、finalize→continue checkpoint 尚未提交时，内存 queue
  不再存在，旧 checkpoint 仍可能结束旧回答。finalize 需对比当前 active history 与本次
  request_items 中的同 Turn 用户输入，发现尚未采样的新输入则继续，并恢复最新 user_input。
  该对比使用压缩后的 active history，不能把原始已压缩旧消息误当成 pending。

### 第四批实现与证据

- `tests/integration/test_turn_input_boundary.py`：model commit 后、evaluate 后、finalize 前接受
  新输入均在同一 Turn 再采样；terminal save 中与 terminal event 已 yield 时 steer 均明确拒绝。
  budget/stop/model failure/append-success-before-ack cancel 四条路径保留 one/two 顺序且无重复。
- 首次 9 个实例均红，其中 2 个 graph wrapper 签名未命名 runtime 导致 fixture 错误，已修正；
  不把这 2 个 TypeError 当成产品缺陷证据。其余 7 个真实失败已修复。随后 stop/append_cancel
  又复现 LangGraph 1.2.11 的 NodeCancelledError 被误报 internal failure；边界归一化后取消通过。
  通过可选类型检测兼容旧 LangGraph 没有该 wrapper 的版本，没有引入新的硬依赖下限。
- 额外真实 checkpoint 用例先失败：新输入已 durable、finalize continuation state 未提交，
  重启模型调用数为 0（错误完成旧答案）。修复后调用数 1，实际 request 包含 initial 和新输入。
- controller 的稳定 UserMessageItem 独立于 queue；只有 prepare 持久化返回才 acknowledge。
  runtime producer 退出立即 close_input，正常/失败/取消/consumer close 清理路径补写未 ack 项。
  补写失败不清空未记录项，activate 拒绝覆盖；该持久化持续失败后的交互恢复仍属 E04 未关闭边界。
- CLI 专门捕获 RealtimeTurnClosedError，完整 application.run 测试确认先后提交 initial、late input
  两个 Turn，没有把迟到输入作为 Runtime error 丢掉。不是仅断言内部 pending 变量。
- 当前终态原子边界在单事件循环内，无 await 的 steer check+put_nowait / finish_if_idle；
  不声称 controller 可被多个 OS 线程直接调用，也不声称入队返回已经 durable。
- 本轮最终：全量 pytest **236 passed**（11.56s，exit 0）；ruff check/format（139 files）、
  compileall、pip check 均通过。组合 extensions demo 再次 status=ok、mcp_discovered=true、
  model_continuation=true；Codex commit/clean 状态和 teach.md 初始 hash 不变。未执行真实模型或 Rust 测试。

上一轮与本轮均为 progress，无阻塞。下一步继续 A08 active aclose / 非 realtime cancel_active
以及资源清理异常窗口，之后 A02/B02/B05/B06/C/D/E 按原目标继续；全目标仍 active。

## 第五批实施前决策：Runtime 任务所有权与关闭（A08/E04）

- Codex `tasks/mod.rs::handle_task_abort:907-998` 取消 running task token，等待退出、abort handle，
  调用 task abort hook，记录中断标记再发 TurnAborted；重复取消先检查 token。
  `session/handlers.rs::shutdown_session_runtime:402-429` 先 abort_all_tasks，再关进程、
  Code Mode、MCP；`shutdown:461-480` flush/shutdown persistence 后发 ShutdownComplete。
  测试映射 `tests/suite/abort_tasks.rs::interrupt_long_running_tool_emits_turn_aborted`。
- Corki `_run_graph` 的 producer 只跑 graph，terminal save/取消/输入补写由 consumer 执行；
  `cancel_active` 仅投递 realtime stop，普通模式无效，工具阻塞时无人消费 stop；
  `aclose` 不拥有 active task，直接关闭依赖，慢消费者/暂停迭代时仍可能有运行中的图。
  状态：行为不一致，P0，不是仅缺少一个 try/finally。
- 方案：独立 TurnRun 所有权记录，producer 拥有 graph、取消清理、输入补写、terminal commit，
  terminal 走独立完成通道，不争 bounded queue 槽。consumer 只投递事件与请求取消并 join。
  cancel_active 对普通/realtime/model/tool 都作用于 active run，重复请求不打断正在进行的清理。
- aclose 持有共享 close task：并发 close 等同一结果，取消某个等待者不取消资源清理；
  先停止/等待 active run（含 durable terminal），再关闭独立资源、checkpoint。启动注册与
  close 通过短生命周期锁协调，不等待暂停的 consumer 持有的整轮锁。
- 验收：普通/realtime × cancel/close × model/tool；暂停于 TurnStarted 与队列满时关闭；
  terminal save 阻塞时关闭必须等待且不改写已选择终态；并发关闭/关闭等待者取消；
  独立清理错误不阻断其他资源、正常回归与真实进程取消。
- Python asyncio 没有 Rust task abort 的强制终止能力；本批先验证协作取消与 owned child join，
  吞掉取消的第三方 handler 的超时/隔离路径必须独立保留，不伪称可强杀任意 Python coroutine。
