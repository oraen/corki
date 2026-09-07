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
prepare → call_model → evaluate → execute_tools / finalize / fail；可重试采样失败走
call_model → retry_model → call_model，从已更新历史重建。业务库在
`storage/sqlite.py`，graph checkpoint 在独立的 SQLite；不能把两者当成同一事务。

| ID / 场景 | Codex 行为、源码/测试 | Corki 当前行为、源码 | 结论、影响、优先级 | 修复 / 验收 |
| --- | --- | --- | --- | --- |
| A01 混合正文、reasoning、工具 | `turn.rs::try_run_sampling_request` 分项处理，工具设置 needs_follow_up；`stream_events_utils.rs::handle_output_item_done` | Responses message/reasoning/tool 共用有序分项历史；opaque reasoning 按项重放；phase 保留；最终正文取最后非空消息 | 部分一致，P1；第十二批关闭终态聚合、混合顺序、opaque 覆盖/丢失和跨消息引用过滤缺口；全部 hosted/异常 payload、provider 特例仍待验证 | 实际 Runtime 混合流/断流/下一请求、引用 usage/phase 和旧库回归通过；第十三批更新历史后重试已接入，继续全部 provider 输出契约 |
| A02 流中工具执行 | 完整 OutputItemDone 即记录调用并启动 tool task；FuturesOrdered 排序、RwLock 并行/独占；`tests/suite/tool_parallelism.rs`、`turn.rs::run_sampling_request` | Responses 完整调用先写 partial journal/history，再由 LiveTools 启动；错误先 drain，重试从已更新历史重建；Chat 无分项终态保留全响应路径 | 部分一致，P1；第十一至十三批已验证终态前执行、平行/独占、断流 drain、取消、steering、分项正文/reasoning、history-aware retry；WS 和全 provider 路径仍开放 | 实际 Responses/Runtime 含已执行结果/unknown/检索定义的重试通过；失败事实与 checkpoint 间真实进程退出恢复，预算不重置、handler 不重放；继续完整组合验收 |
| A03 非工具续接 | Completed.end_turn=false 使 needs_follow_up=true，pending input 也可延续；`turn.rs:2667` | 模型标志持久化/CONTINUE、合法空 completed、工具优先；finalize 对比未采样 durable 输入并原子检查 queue/关闭输入 | 部分一致；显式续接及已定位输入终态/恢复窗口已修复验证；Codex mailbox/hooks 等分支仍未完整映射，P1 | mock Responses、预算/协议失败、真实 checkpoint、终态前输入续接、终态后拒绝与 CLI 下一 Turn 保留均通过 |
| A04 Step 工具视图 | `StepContext` / `ToolCallRuntime` 保留router；sampling retry复用worker，Step结束停止准入；旧cell未来调用由当前worker准入；MCP in-flight持有精确client | GraphRunContext持有handler+schema快照，checkpoint仅id；prepare冻结/retry复用；Step间新call/notify等待，已准入调用保留原worker及Turn事件归属 | 部分一致，P1；本批修复retry换router/重置gate及Step间持续准入；完整配置/环境快照、cold pending配置漂移和全部取消/压缩组合仍需核齐 | 第三十一批真实准备间隙/冷tools checkpoint/跨Turn事件、retry gate与唤醒竞态；既有Step身份/发现/ledger回归保留；不外推完整Code Mode一致 |
| A05 实时取消的任务所有权 | `parallel.rs` AbortOnDropHandle + cancel/await；`turn.rs` or_cancel + drain 后传播 TurnAborted | 原实现 wait 子任务无 finally；本批新增 `core/model_stream.py::model_events`，graph、两 provider 显式关闭内层 iterator | 部分一致；本批取消/完成/HTTP stream 泄漏已复现并修复，更多 Runtime 关闭语义仍见 A08，P0 | 新增 parent cancel、双 waiter、普通/realtime completed、两 HTTP adapter closure 用例已通过；不据此声明全部关闭路径一致 |
| A06 并行异常 | `ToolCallRuntime` 持有并回收执行任务，fatal 与 observation 分离；`turn.rs::drain_in_flight` | 原 gather 的存储异常遗留 siblings；本批 graph 在 finally cancel + join，保留 gather 的结果顺序 | 部分一致；本批 claim/complete 故障时 sibling 存活缺陷已修复，完整 fatal/Observation 语义仍需继续对齐，P0 | 两个新增存储故障用例通过，验证 TurnFailed 前 sibling 已结束、unknown claim 不重放；已有 parallel/recovery 回归通过 |
| A07 模型/工具恢复 | Codex 分项调用先记录后执行；executed-tool metadata 为 best-effort feature-controlled，不等于 SQLite exactly-once | 原子 completed step、partial journal/history、互斥 ModelFailure；参数指纹 claim、completed 不可改写、legacy unverified；stable result item id | 部分一致，P1；已验证参数碰撞、partial 未 claim/running/completed，以及失败事实已提交但 checkpoint 未更新的真实退出窗口；完整组合未关闭 | partial 不伪造完整响应、不重采样旧请求、未知不重放；普通/连接计数不重置、双向成功失败冲突被拒绝；继续完整历史与 checkpoint 联合验收 |
| A08 Turn 完成/取消/backpressure/close | `on_task_finished:620-650` 移除 task、记录 pending；`handle_task_abort:907-998` cancel/join/abort/marker；`handlers.rs:402-429` 先停止 task 再关闭依赖，持久化关闭错误可见；CodeMode逐cell错误隔离；startup prewarm有cancel/join所有权 | TurnRun持有图/清理/首笔写入/终态；第41批准入仅等done，第50批cell清理，第51批owned初始化，第52批写线程join，第53批checkpoint回滚；第54批非V2中断历史、先清理后marker后终态、持久取消意图冷恢复 | 部分一致；已定位取消、关闭、迟到提交、checkpoint回滚及非V2marker路径已验证；TurnStarted与预热时序、创建前失败、永久关闭失败、强制隔离、V2及原生RawResponseItem事件仍开放，P0 | shutdown真实exec及第41–53批组合；第54批实际两HTTP请求、配置/冷恢复/替换、CodeMode收尾、压缩与后台记忆组合；不据此关闭整个E04 |

## B：工具发现、召回、选择（首条检索链已重新核实）

Codex：`tools/spec_plan.rs::build_tool_router` / `finalize_tool_router` →
`search_tool_enabled`（model.supports_search_tool AND provider.namespace_tools）→
`handlers/tool_search.rs::ToolSearchHandlerCache::get_or_build` → deferred entries →
BM25 English documents → `handle_call` query / limit → `ToolSearchOutput::to_response_item` →
history → 下次请求。`tools/src/tool_search.rs` 定义默认名称/描述/递归 schema 搜索元数据。

| ID / 场景 | Codex 行为、源码/测试 | Corki 当前行为、源码 | 结论、影响、优先级 | 修复 / 验收 |
| --- | --- | --- | --- | --- |
| B01 Deferred 注册曝光 | `tools/src/tool_executor.rs` 曝光策略；`spec_plan.rs::build_model_visible_specs` 只直接曝光 direct；TokenBudget条件注册new_context为DirectModelOnly | 已接入 ToolSearchTool / ToolPlan；deferred 和 deferred_model_only 可检索，hidden/code_mode_only 不进入结果；第57批显式TokenBudget的两专用工具接Runtime，new_context保持ModelOnly | 部分一致；按需检索闭环已实现，Code Mode完整合同仍见 B06 | 真实 Runtime首轮无deferred schema、搜索加载/调用/跨Turn；TokenBudget Direct/CodeMode曝光及typed remaining返回通过 |
| B02 检索/选择 | `handlers/tool_search.rs` pinned bm25 2.3.2，默认limit8；immutable identity/dynamic info/source listing缓存 | pinned BM25/tokenizer/term identity；prepare完整构建；MCP弱identity/dynamic search-info值缓存；冻结语料、默认Include来源512 KiB UTF-8名称预留；8k结果预算 | 部分一致，P1；实验world-state/Omit缓存策略与namespace wire未关闭，Rust直接差分及big-endian未执行；完整Step router见A04 | 第二十三批golden；第二十五批来源19例；第二十六批缓存/冻结/故障21例，909 strict通过；双模式Runtime相同/变化metadata的新实例、MCP准入刷新后旧搜索语料；不称所有排名位级一致 |
| B03 原生搜索协议 | `tools/context.rs::ToolSearchOutput`；history::for_prompt/normalize不查询registry，schemas留在输出历史 | native call/output已接入；原生历史不按当前registry覆写，dispatch独立；compatible function调用与加载定义注入；第60批namespace投影；第69批入站native search分类与hosted search事件持久化/回放、失败和冷恢复；第70批移除统一16K拒绝，search/web不套函数输出截断策略 | 部分一致；hosted定义尚未接本地发现准入；通用namespace投影已接入，MCP命名来源/owner冲突、namespace_info/model catalog仍待核齐 | mock Responses两模式三Step；第60批namespace/模式及冷恢复；第69批原生无效搜索、hosted事件及恢复/压缩召回55例；第70批大search/web经HTTP与压缩保留完整内容；未执行真实模型 |
| B04 检索后的生命周期 | 原生历史保存loadable定义；执行并发/截断独立于搜索输出 | canonical result/ledger/active history；native保留历史；compatible只过滤定义/身份变化，不因concurrency/output预算变化卸载；dispatch用本Step当前设置 | 部分一致；原生/兼容区别已明确；跨进程未完成call恢复仍需继续A04审计，不能将新Turn恢复等同pending checkpoint恢复 | 第二十八批18例、957 strict；跨Turn/关闭重开只搜一次，新并发门与截断生效；既有ledger/checkpoint三个故障窗口恢复及压缩释放回归通过 |
| B05 MCP 与扩展元数据 | `handlers/mcp.rs` metadata/exposures；session mcp refresh gate/claim；codex-mcp runtime原子连接发布、PreparedMcpCall精确client | 默认deferred；owner原子刷新；prepare/准入消费pending、in-flight持有client；initialize说明检索/展示；兼容投影失效与原生历史保留分开；第68批宿主污染metadata随实际连接消费；第72批每工具输出token配置从admitted连接捕获，结构化/有序媒体/opaque优先、wall-time、独立日志及nested公共原值接Runtime | 部分一致，P0/P1；完整审批权威/catalog revision锁、flat canonical与namespace wire、实验Omit、hosted Apps/plugin来源、background prewarm、auth/environment自动失效及连接复用仍待核齐 | 双模式HTTP六Step来源说明/目录变化；初始化字段类型失败隔离；取消刷新/发布失败；真实stdio旧进程随in-flight调用释放；第68批23例；第72批输出/刷新/退休/HTTP媒体/CodeMode错误44例；既有skills/plugin/MCP组合E2E保留 |
| B06 Code Mode | `code-mode-runtime` service/session_runtime/cell_actor/runtime；core code_mode delegate→通常 ToolCallRuntime；spec_plan 条件曝光 | opt-in QuickJS-NG ES module、tools/ALL_TOOLS、exec/wait、store、通常 ledger 调度与取消；shell/MCP/view_image typed return；有序 media/helper、图片集中解码/缩放/能力、共享预算和 provider 转换已接入 | 部分一致，P1；不是整个 Code Mode 完成 | 第十七至二十批真实引擎/Runtime/HTTP/ledger/checkpoint/compaction；resize notices/媒体 metadata、完整音频 codec、其余工具契约、V8 host、mode override、生命周期与统一计数边界仍开放 |
| B07 执行身份/错误/schema | `tools/function_call_error.rs`、registry unknown→RespondToModel / incompatible payload→Fatal、parallel fatal传播与abort/join；CodeMode host另将分发错误转JS拒绝 | executor 覆盖返回值校验/复制；第71批普通原始结果/错误诊断入账本，模型预算移到history copy，nested消费原值；直接Fatal进入Runtime tool failure，嵌套分发错误持久化后拒绝Promise；取消保持；MCP timeout报告结果未知；第74批shell采集/模型/log/nested预算分离、header与遗漏metadata、独立poll预算 | 部分一致，P1；直接fatal/sibling与嵌套错误值/分发错误已区分；完整schema、媒体有效性、动态工具失效、shell生命周期及其余工具专属输出格式未关闭 | 第三十批实际引擎catch/未catch、ledger replay、提交故障；第71批26例；第74批30例含真实子进程/QuickJS/Runtime/冷历史与1MiB采集；既有坏结果/超长异常/未知工具/参数回归通过，不外推完整JSON Schema |

## C / D / E：阶段性结论与尚未完成的链路

下面不是“不适用”清单，也没有默认认定已有功能正确。C02/C03 已完成默认窗口和本地压缩的
首批追踪、实现与验证；该结论不覆盖同项其余分支。其余项仍按列出的范围继续审计。

| ID | 必须追踪和验收的范围 | 已确认的局部结论 / 仍待检查位置 |
| --- | --- | --- |
| C01 | instructions、项目规则、环境、扩展、角色顺序、每 Step stable key / tombstone / snapshot | 部分一致，P1：第33–39批接入prefix/role/压缩基线、输入正文与目录分离、host预算/别名、per-skill hidden/disabled、头解析/文本路径召回、全局include_instructions与silent状态。环境等section专属diff、来源冻结、完整顺序、多来源目录与统一fragment上限仍未关闭；通用整段替换仅兼容策略 |
| C02 | 完整请求 token、output reserve、schema、图片与 reasoning 成本、硬上限 | 部分一致，P1：raw/usable/auto cap及静态catalog/context-max/global override已接Runtime、skills和提取；第四十八批模型匹配、第五十五批usage反馈/窗口失效/冷恢复/Responses reasoning header；第五十六批BodyAfterPrefix首个input基线/输出与tail增长/压缩重置/恢复估算→新server替代及独立hard cap接Runtime和两HTTP；第五十八批显式TokenBudget fallback buffer只延迟自动阈值，不影响remaining与完整hard cap；第70–71批hosted及普通函数按模型Bytes/Tokens/可信override投影，原档案保留；第72批MCP专属model/log/nested分离及有效连接每工具预算/serialization allowance接入，MCP历史本身可已工具型截断，不能泛称所有工具rollout都是handler原文。显式旧spec字符限制仍为额外兼容策略；shell/CodeMode格式/预算、完整MCP媒体/配置、远程目录权威性、完整本地估算/动态schema成本、完整附件/其他encrypted成本、模型切换组合及usage诊断仍开放 |
| C03 | 手动/自动/pre-turn/mid-turn 压缩触发、输入保护、摘要失败 | 部分一致，P1：local instructions/用户请求、oldest-item/pair fallback、独立retry及最后有效摘要接Runtime/HTTP；第40批manual生命周期/冷恢复；第54批中断标记；第55–56批Total/Body usage；第57批显式TokenBudget默认配置manual/auto/new_context无摘要reset、原档案保留/故障冷恢复；第58批custom reminder/guidance/fallback+buffer接post-sampling、两个adapter及持久去重。remote、token-budget notes/自动激活、模型切换scope组合/hook与完整保留预算仍开放。主采样overflow结束Turn，源码默认路径无立即压缩重采样，不列为缺失 |
| C04 | summary replacement 与 append-only 原始记录、tool pairs、reasoning、新输入、取消/恢复 | 部分一致：Codex context_manager/history.rs/normalize.rs及memories write phase1；第四十五批为保留User副本记录原ID，重复压缩/已持久化pending恢复均保留来源，首次写入pending不打标。原档案保留工具关系并排除明确副本；真实manual/pre-turn/mid-turn→提取/合并与旧checkpoint通过。第54批typed中断标记与实际用户/动态Context分开，压缩移除后原archive及真实记忆提取仍可见。完整provider compact/预算/取消组合仍开放 |
| C05 | 连续历史、历史搜索、长期记忆召回边界；原生/provider 特定 compact 路径 | 部分一致，P1：第59–61批接opaque/namespace及native九工具、可信身份/ingest、hint；第62批非原生路径共用九工具，接原始archive窗口/角色/工具过滤、命中偏移/连续读取、inline图片及Thread隔离SQLite notes，验证小窗口自动reset和冷恢复；第63批保留工具原始JSON/freeform及解析诊断，两种HTTP适配器验证畸形调用reset/冷启动后同ID检索读取且不进入handler。仍缺native auth/config失效、完整ingest/安装ID/ordinal/多Agent；服务端规范化/结果shape无法由本地源码证明，local不解密opaque、不恢复private reasoning/音频细节，不是完整服务端等价声明 |
| D01 | 短期 Thread history、工作 state 与长期来源筛选/生成触发 | 部分一致，P1：已追踪startup→phase1→state；Corki Runtime→pipeline→extraction.py。已移除completed Turn门槛，毫秒年龄/idle/排序与先扫描5000再probe已验证；失败/取消来源接真实Runtime生成→召回；有界prune已接入。完整 source/archive/ephemeral、generation开关组合与启动quota仍开放 |
| D02 | extraction / consolidation、claim/lease/owner/source version/watermark/去重/失效 | 部分一致，P0/P1：claim/重试/水位/retention/selection/use、Phase2 cooldown/backoff、结构化档案/过滤/输出合同和模型预算均有Runtime证据。第四十九批阶段覆盖→provider preferred→Luna/Terra默认，单请求low/medium，内置窗口/自定义目录权威性接两种HTTP适配器；第54批非V2中断事实在压缩后经原archive进入实际后台提取/合并。远程/缓存和完整model能力、Unicode详验、原生Phase2 agent/context/git diff、unowned收尾及控制面仍开放 |
| D03 | summary 注入、搜索、细读、来源引用、usage；显式记住/更新/遗忘 | 部分一致：真实Runtime生成→summary→search→read→citation/usage已验证；第四十三批修正usage累计与retention排序；第64批对齐ad-hoc原文/文件名/不覆盖、写前目录复核、合并信任/派生标记，验证工具写入→冷启动合并→summary/read且原笔记保留；第65批修复read/search行边界、首尾截断、normalized及空查询错误、默认root search链接拒绝，实际6请求组合验证。不是即时遗忘或历史擦除；完整Unicode属性/版本、工具协议、引用解析/恢复、全局reset及完整污染控制仍待审计 |
| D04 | 外部污染、后台隔离、失败/取消/关闭、发布一致性 | 部分一致，P0：后台流/心跳/提取失败隔离、部分发布恢复已验证；第43批prune失败隔离/关闭join；第66批污染mode与清理enqueue事务/水位/owner/cooldown；第67批typed外部结果/失败隔离；第68批MCP实际连接宿主metadata替代名称前缀；第69批原生search call来源事实、hosted模型事件持久化/回放/partial及completed冷恢复、startup通知重检；第70–71批hosted与普通函数模型策略副本和原始提取/召回档案分离，可信单项覆盖不受远端metadata控制。Codex local无跨文件/DB原子发布承诺。完整宿主/CodeMode通知生产链、工具专属输出策略/完整metadata、hosted媒体能力、MCP审批权威与catalog读写锁、共享写锁延迟、service独立close和完整启动路径继续开放 |
| E01 | 错误分类：Observation / retry / fatal / cancel | Codex function_tool、registry、parallel；Corki tools/executor、models/base、runtime；A05/A06/B07 是首批入口。第三十批修复CodeMode分发错误拒绝Promise及错误值分层；第三十一批补Step worker准入/同Step retry/冷tools恢复；第72批MCP远端协议/HTTP/timeout为isError公共结果、nested resolve而非host dispatch reject，超时不自动重试、取消不吞；第73批顶层content/isError坏类型与HTTP非法JSON同样归入MCP错误值，严格整数响应身份，冷历史不重复调用。顶层Fatal和基础设施故障仍失败。完整取消/压缩组合、rmcp完整wire/defaults/404恢复及全部provider故障分类继续审计 |
| E02 | HTTP/SSE/部分输出/无终态/重试与副作用 | 部分一致：第十三/十四批 history-aware retry、连接恢复、SSE typed code/delay/错误优先级；第十五批请求4/采样5、cap100、200ms jitter、HTTP状态与两层预算已接实际 Runtime。HTTP-only 没有 WS fallback；完整 usage/policy 附加诊断、OAuth恢复、超时默认/关闭异常及全部 payload 路径仍待验收 |
| E03 | 压缩失败、memory 失败、扩展 init 部分注册回滚、工具失效/超时 | 第三十二批local压缩独立有界重试、无终态、取消退避及失败不安装替换通过；不污染主采样预算或重放工具。第三十五批显式skill读取失败→临时warning→CLI接通，跳过失败正文、不吞取消，满队列取消已验证。其余Codex compact/memory/MCP对Corki context/memory/mcp/plugins继续按失败点审计 |
| E04 | 所有终态和 close 路径、一次终态、无未知结果重放 | 与 A05–A08 联合验收；第50批关闭CodeMode已定位收尾故障及过早完成竞态，取消终态/其他依赖继续清理；强制隔离等开放，不允许局部修复替代全链证明 |

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

### 第五批实现与证据

- 修改前 `test_runtime_shutdown.py` 初始 **10 failed / 1 passed**：普通 model/tool cancel、
  realtime tool cancel 无效；4 条 active close 提前关闭依赖；TurnStarted/满队列暂停消费时
  close 遗留 RUNNING/图；terminal save 阻塞时 close 提前完成。realtime model cancel 原本通过。
- `core/turn_run.py` 显式持有 task、queue、独立 done/terminal、取消请求与 finishing 状态。
  任务尚未第一次运行时先记取消标志而不直接 task.cancel，保证初始输入和终态仍能收尾。
  已进入 finishing 后不再中断持久化，重复取消不破坏 cleanup；consumer close 只取消并 join。
- `runtime._execute_run` 负责图执行、终态分类、原始输入/未 ack steering 补写及 terminal save；
  terminal 不经过 bounded data queue。consumer 在 Runtime 已关闭后仍能读剩余事件和唯一终态，
  无需再触碰 repository/checkpointer/model。
- Runtime `_active_run` 与生命周期锁协调启动注册/关闭；aclose 的共享 task 用 shield 防止
  等待者取消传染。close 先 join active task，再关闭进程、memory、MCP、plugin、model、存储、checkpoint。
  join 前固定 active 引用，避免 consumer 清除 self._active_run 的竞态。独立资源错误仍继续清理，
  首个错误由所有 close 等待者共同观察；cancel 的 cleanup error 不改成 TurnFailed。
- 当前 20 个 shutdown 实例：初始 11 + 并发 close/等待者取消 1 + 4 个独立关闭故障 +
  真实 exec_command 子进程 cancel/close 2 + 启动注册与 close 竞态 1 + 取消清理异常 1。
  真实进程用当前 Python sleep，检查 returncode、ProcessManager session、reader/timeout task；
  不是仅检查 mock tool 的布尔标志，也不依赖 asyncio.run 退出时自动清场。
- 全量回归在最后新增 cleanup-error 用例前 **255 passed**（15.38s）；静态检查、compileall、
  pip check 通过，Codex 干净，teach.md hash 不变。最后回归结果继续追加。

未关闭项明确保留：启动中（TurnRun 尚未注册）consumer 被直接取消的窗口需要复现；
无法协作取消的 handler 需要超时/执行隔离方案；持久化持续失败后未 ack 输入的重试与下一 Turn
注册需核查；Codex interrupted marker 与 suspend/fork 恢复语义尚未移植验证。
这些属于 A07/A08/E04，不从本批 green tests 推断完成。A02/B02/B05/B06/C/D/E 全范围仍在目标内。

第五批最终复验：全量 **256 passed**（14.22s，exit 0）；ruff check/format（141 files）、
compileall、pip check 通过；extensions 组合 demo status=ok，搜索与非工具续接标志均为 true。
Codex 工作树干净，teach.md hash 不变；未运行真实模型或 Rust 测试。
上一轮和本轮都是 progress，没有外部阻塞，完整目标继续 active。

## 第六批实施前决策：Context 窗口与本地压缩（C02/C03/E03）

- Codex `protocol/src/openai_models.rs::ModelInfo` 的 resolved/usable/auto_compact 窗口三者不同：
  usable 默认 raw×95%，auto 默认 raw×90%，显式 auto limit 仍取 min(limit, raw×90%)。
  `model_context_window_limits_preserve_their_distinct_meanings` 测试 272000→258400/244800。
  `core/src/session/context_window.rs` 还支持 BodyAfterPrefix、fallback buffer、provider usage +
  增量估算；本批不把这些分支与默认 Total/无 fallback 混为一谈。
- 当前源码这条路径未发现固定 max_output_tokens 扣减；因此本批按明确的 model headroom
  对齐，不虚构“Codex 固定预留 N 输出 tokens”。Corki 原来只检查完整 raw window，无 usable cap。
- `turn.rs::run_auto_compact:1248-1322` 按 TokenBudget feature、remote_compaction V2/capability、
  Unsupported local 分流。Corki 本批明确实现 local 路径，remote 与 token-budget reset 仍缺失。
- `compact.rs:248-405` 在 base instructions 下追加合成 user compaction request；context exceeded
  时移除最旧 history item 再尝试；`context_manager/history.rs:496-508` 连同对应 call/output 一起移除。
  `compact.rs::drain_to_completed` 到 Completed 即返回，不等待 stream 尾部；取消直接传播。
- Corki `_summarize` 当前递归分块，最小块 1024 tokens，单大 item/tool pair 永远无法进入较小窗口；
  且覆盖 base instructions、裸 async for 不在 Completed 停止/关闭 iterator。状态：行为不一致，P1。
- 方案：统一 ContextLimits（默认 95% usable、90% auto cap，保留显式更低 auto），实际 Runtime
  的 prepare/压缩请求/压缩结果/保留历史共用 usable cap。设置与新 config 模板同步，不重写用户配置。
- 替换无 Codex 依据的分块递归为本地压缩链：base instructions + 原历史 + user compaction request，
  预估超限或 provider 返回 CONTEXT_WINDOW 时删最旧项及配对结果，直到可采样或仅剩压缩请求。
  原始历史不删除；当前 pending input 不加入摘要并原样保留；如果基础指令+压缩请求本身超限则明确失败。
  被裁减历史应记录 warning，不能声称摘要保留了未发送的内容。
- 验收：usable cap；auto clamp；800-token 小窗口和大工具对裁减；provider 超窗重试保持工具对；
  不改原始历史/当前输入；完成即关闭、取消释放 iterator；真实 Runtime 自动压缩后继续执行。
  server usage 校准、BodyAfterPrefix、manual/remote、generic retry、mid-turn 当前输入完整保护仍保留后续审计。
- 本批实现中继续确认 mid-turn 当前输入可能被 `_retained_user_messages` 截断；按用户目标的
  当前输入保护要求，最新同 Turn user 必须从摘要移出并完整重注入，实在放不下则失败、不提交替换。
  Codex local 路径会按 20k budget 截断保留 user，本项是用户要求优先的明确增强，不称为逐字一致。
- 另需区分 OUTPUT_LIMIT 与 CONTEXT_WINDOW：旧 adapter 把输出耗尽也映射到 context，若直接用于
  新 oldest-item fallback 会错误裁减输入。输出耗尽必须失败而不是删历史重试。

### 第六批实现与证据

- `tests/unit/context/test_compaction_budget.py` 首批 5 个实例在修复前全部失败，覆盖 usable
  headroom、auto cap、小窗口大工具对、provider 超窗 fallback、Completed 后挂起尾部。
  随后 mid-turn 当前输入保护另 1 例先复现失败：旧实现截短当前用户输入后错误安装摘要。
  修复后以上通过；再补 cancel/output-limit/protocol 3 个失败原子性实例，共 9 个。
- `protocol/context.py::ContextLimits` 区分 raw / usable / auto，默认 95% / 90%，显式更低
  auto 保留、更高值取 90% 上限。`CorkiSettings`、新配置模板与真实 Runtime 传参接通；
  已有用户配置不重写。`test_limits.py` 13 个实例验证源码公式、设置默认值与非法配置。
- `context/window.py::_summarize` 不再递归分块或覆盖 base instructions；摘要请求追加合成
  user message，tools 为空。估算超窗或 provider 的 CONTEXT_WINDOW 才裁减最旧项及其
  call/result 配对，再尝试；裁减只影响摘要请求视图，warning 明示无法总结未发送内容。
  原始 SQLite history 不删除，不声称遗漏信息仍被保存在摘要中。
- `prepare` 在 pre-turn 保护 pending input，mid-turn 保护最新同 Turn user，完整复制到替换
  窗口；摘要加当前输入仍放不下则失败、不安装 replacement。其他旧用户输入仍受保留预算约束。
  local compaction iterator 在 Completed 即 break 并 aclose，取消不被捕获成普通失败；
  取消/协议失败/输出耗尽均验证没有写入替换历史，原始历史保持不变。
- `ModelErrorKind.OUTPUT_LIMIT` 与 CONTEXT_WINDOW 分离。Chat finish_reason=length 和
  Responses max_output_tokens incomplete，以及相应 HTTP 输出限制错误不触发删历史重试。
  transport 测试新增 4 个 Chat/Responses × SSE/HTTP 实例；未把所有未知 provider 文案识别
  当成已经完整对齐，通用错误分类仍属 E01/E02。
- `tests/integration/test_runtime_compaction.py` 实际 Runtime：模型调用大结果工具 → 自动摘要 →
  带摘要和原样当前输入继续采样 → TurnCompleted。断言 3 次请求、一次 ContextCompacted、
  全部请求低于 usable cap、原始大工具 call/result 各保留一次。使用确定性模型，不代表真实模型质量。
- 最终复验：全量 **283 passed**（12.10s，exit 0）；ruff check / format（145 files）、
  compileall、pip check 通过。独立运行 extensions demo，status=ok，MCP 按需发现/调用、
  skill recall、plugin 调用、非工具模型续接均为 true。
- Codex commit 仍为基准且工作树干净；teach.md SHA-256 与初始值一致。README / development
  已说明窗口配置、本地压缩行为和限制。未运行 Rust 测试或真实模型。

本批关闭的是已复现的默认窗口与本地压缩缺陷，不关闭整个 C02/C03/E03。provider usage 校准、
BodyAfterPrefix、fallback buffer、手动/远端压缩、token-budget reset、通用 compaction retry、
主采样遭遇 provider 超窗后的恢复、附件/reasoning 成本与完整压缩恢复窗口仍待完成。
A02/A07/A08、B02/B05/B06/B07、C01/C04/C05、D 全链及其余 E 继续有效；完整目标保持 active。

## 第七批实施前决策：记忆合并的所有权与后台流（D02/D04/E03）

- Codex `memories/write/src/start.rs::start_memories_startup_task` 在非 ephemeral、MemoryTool
  feature、root session、state DB 可用条件下后台启动：prune → quota guard → phase1 → phase2。
  phase1 按 eligible interactive rollout claim 并发提取、过滤 contextual user/developer 内容、
  structured output、secret redaction、token 归属提交；这些分支尚未全部映射，不预判 Corki 一致。
- `phase2.rs::run` 在共享 workspace 写入前 claim global lease；`agent::loop_agent:494-557`
  持续 heartbeat，失去所有权或心跳失败则结束工作；`agent::handle:382-489` 先关闭 agent，
  验证产物、再 heartbeat 确认所有权，才重置 baseline / 完成 job。
  `state/src/runtime/memories.rs::heartbeat_global_phase2_job:1217` 只允许 matching token 的
  running job 更新；`mark_global_phase2_job_succeeded:1253` 对选中 snapshot 同时核对
  thread_id 与 source_updated_at。测试 `phase2_global_lock_stale_lease_allows_takeover`。
- Corki `runtime._ensure_ready → LongTermMemoryService.start → run_once` 接入后台提取与合并，
  但没有 heartbeat；`pipeline.py` 先写 MEMORY.md / summary / skills / baseline，最后才
  `complete_consolidation` 检查 token，旧 owner 可先覆盖新产物再被拒绝。状态：行为不一致，P0。
  SQLite selected 标记仅匹配 thread_id，也会把更新后的 source 错标成已被旧合并消费，P1。
- `pipeline._sample_json` 裸 async for 越过 Completed 继续等待尾部，未显式关闭 iterator。
  这条后台采样链并未被此前主循环/compaction 流修复覆盖。phase1 gather 出错的 sibling
  及 service 独立 close 故障仍须完整验证，不能把 Runtime close 测试外推到内部所有权。
- 本批方案：合并任务期间续租、失败取消并 join 工作；workspace 写与最终发布在 SQLite
  matching-token 的写事务保护下执行（消除检查与写之间被接管的窗口），提交 selected 时
  核对精确 source version。阻塞文件写已开始时取消必须等它退出，不能提前释放 claim。
  structured-output 流 Completed 即停止并关闭，缺终态/取消仍失败。
- 验收：真实 SQLite 过期接管后旧 owner 不覆盖产物；heartbeat 延租/丢失/异常；发布期间
  第二 owner 不可接管；取消等待阻塞写；source version 变化不错误标记；Runtime 后台失败隔离。
  多文件发布与 DB 跨介质 crash 原子性、完整提取筛选/lease、内容污染和记忆更新/遗忘仍保持开放。

### 第七批实现与证据

- `tests/unit/memory/test_ownership.py` 有效 fixture 修正后、产品修复前 6 个实例均失败：
  旧 owner 的 MEMORY.md 实际覆盖了新 owner 内容；Completed 后尾部挂起；心跳丢失/异常两例
  不结束采样；无续租接口；发布没有 owner-fenced callback / worker join 契约。
- `SQLiteMemoryRepository` 新增 token-scoped heartbeat 和 workspace write port；final
  `complete_consolidation(..., publish=...)` 在同一 BEGIN IMMEDIATE / matching running token
  临界区完成文件 callback 与 selected/watermark 更新。token 已被替换时不调用写操作。
  心跳与 Codex 一样依据 status/token，不额外要求旧 lease 时间未过（无接管时可续租）。
- `_joined_write` 对真实 to_thread worker 使用 shield + join；重复取消不会提前结束所有权保护。
  测试用 threading.Event 阻塞真实 writer，验证 caller cancel 后仍等待、另一个 SQLite claim
  无法越过发布临界区；writer 结束后提交完成、takeover 返回 None。不是只断言 mock 标志。
- `LongTermMemoryService._run_owned_consolidation` 持有 work/heartbeat tasks，工作时每
  min(30s, lease/3) 续租，心跳失败先取消并 join，再记录 job failure。最终发布前停止 heartbeat，
  避免成功提交清除 token 后被自身心跳误报 ownership lost；最终 publication 自有 DB fence。
- `_sample_json` 在 ModelCompleted 即 break / aclose，缺终态仍失败。后台流不再依赖 GC
  或 asyncio.run 收尾。Codex `memories/write/src/runtime.rs::stream_stage_one_prompt:241-326`
  同样在 Completed break；Corki 仍额外要求明确终态，不把未完成但 JSON 恰好完整当成功。
- 第二次故障注入新增 2 个实例先失败：fail_extraction 本身抛 OSError 会中断批次；取消则被
  该 OSError 覆盖。现失败落库异常追加 warning、保留 lease 到期恢复，不中断其他成功提取；
  原取消继续传播。所有提取 child task 明确持有、finally cancel/join；cleanup tasks 也被 join。
- 另外补 2 个实例：运行期间实际续租超过一次；合并期间 source_updated_at 更新后，新快照
  selected_for_phase2=0、selected_source_updated_at=NULL。10 个 unit 实例全部通过。
- 新增 `tests/integration/test_memory_pipeline_runtime.py` 两个真实 Runtime 场景：
  1. 旧 Thread → 后台提取 → 合并 → 同 Runtime 下一 Turn summary 注入 → memory_search →
     memory_read → citation 持久化及 source usage 增长；2 个后台请求、4 个主采样请求。
  2. 心跳数据库异常 → 关闭后台模型 iterator、记录 failed report/warning → 交互 TurnCompleted。
  两者使用确定性模型；只能证明 Harness 执行链，不证明真实模型的抽取或召回质量。
- 最终全量 **295 passed**（13.50s，exit 0），ruff check/format（147 files）、compileall、
  pip check 通过；extensions demo status=ok，按需 MCP 检索、skills/plugin 与续接标志为 true。
  Codex commit/clean 状态不变；teach.md 初始 SHA-256 不变；未执行 Rust / 真实模型测试。
- README、development、artifact docstring 已纠正“整体原子发布”和“全局核心已完成”的过强声明。
  现仅保证每个文件 atomic replace 与合作 owner fencing；文件集合/SQLite 不是同一 crash
  transaction，I/O 中途失败仍可能混合版本。写锁保护发布可能延迟主会话同库写入，仍需进一步处理。

本轮有实现与验证进展，无外部阻塞。D01/D02 的完整提取筛选、retention/去重/版本水位，D03 的
原生与兼容 read/update/forget 全链，D04 的原子发布/独立关闭/污染边界均继续有效。
此前 A/B/C/E 未关闭项不缩减，完整 goal 保持 active；不能以 295 tests green 宣告核心对齐。

## 第八批实施前决策：记忆工作区变更与产物验证（D02/D04/E03）

- 继续追踪纠正上一批的差异归因：Codex `workspace.rs` 使用 live workspace / Git baseline；
  `ext/memories/src/local/read.rs` 直接读取文件，未发现文件集合与 DB 的联合原子发布。
  因此跨介质原子性是已知可靠性限制，不能直接宣称为 Corki 独有、必须复刻的 Codex 能力。
- 真正可定位的差异是 `phase2.rs:154` 只有 workspace diff 无变化且
  `validate_consolidation_artifacts` 通过才跳过；`workspace.rs:54-90` 验证 regular MEMORY.md、
  summary 首行 v1，并清除/拒绝符号链接。测试 `validate_consolidation_artifacts_rejects_invalid_summary`。
- Corki baseline 仅含 stage-one / extension 输入 digest，跳过条件只有该 digest 相等和两文件
  is_file。因此用户修改输出、summary 损坏、skills 修改/删除不触发合并；已有 skills 内容也未进入
  隔离的 consolidation ModelRequest。状态：行为不一致，P1，可能永久接受损坏产物或丢失旧 procedure。
- 方案：版本化 baseline 同时记录采样前输入与发布后的输出指纹；校验产物后才允许 skip/安装
  baseline。旧 baseline 缺少输出证据时保守触发一次合并，不重写 DB schema；不保存被遗忘内容的
  历史副本。输入/输出扫描不跟随 symlink；现阶段拒绝而不删除用户链接，与 Codex 清理策略差异明示。
  将当前 skills 作为有界 consolidation 输入接入实际后台请求，不只检测变化却不让模型看到内容。
- 验收：修改 MEMORY.md/summary/skills、删除 skill、旧 baseline 迁移、未修改仍跳过；非法版本
  不可跳过或安装成功 baseline；模拟发布中途 I/O 失败不推进 baseline/成功水位且下次可重试；
  symlink 不读取外部内容。跨介质断电原子性不据此声称解决，完整 memory 其余目标保持不变。

### 第八批实现与证据

- `test_workspace_baseline.py` 首批 7 个实例修复前全部失败：detail 修改、summary 版本损坏、
  skill 修改、skill 删除、legacy baseline 都错误 skip；symlink output 也被 is_file 接受；
  注入非法发布结果后仍写成功 baseline 并完成 job。不是从原有 green tests 推断缺陷。
- `artifacts.baseline_matches / write_baseline` 使用 version=2，分别保存采样前输入指纹和
  发布后 output 指纹。任何一侧不同或产物无效均不允许 skip；旧 v1 baseline 不能证明 output
  状态，走正常合并重建一次，不修改数据库 schema。不保存旧正文或引入永久历史记忆副本。
- `validate_consolidation_artifacts` 核验 regular MEMORY.md、summary 首行 v1；可见 workspace
  inventory 不跟随 symlink / 非常规文件。sync 输入、发布、指纹扫描以及 leaf read 均拒绝
  已存在的链接；本批采用不删除用户链接的失败语义，与 Codex 自动清理链接仍有明确策略差异，
  不声称解决恶意并发替换路径的所有 TOCTOU 窗口。
- 同一 inventory 区分输入与 output（MEMORY.md、memory_summary.md、skills），按内容分块
  hash；文件增删与内容改变均可见。baseline 不把模型采样期间新加入的 note 当成已消费，
  测试明确第二次 request 无 late note、第三次 request 有该 note、第四次才 skip。
- `pipeline._consolidate` 把已有 Markdown skills 正文加入 previous_skills 字段，受现有整体
  bounded JSON 策略约束；prompt 明确输出完整 procedure 集合并结合新证据/更新/遗忘请求处理。
  这证明数据入模，不证明真实模型保留/遗忘质量；Code Mode/consolidation agent 工具化路径仍开放。
- 增补 4 个验证实例：部分文件发布后 summary I/O 故障，baseline/成功水位不变且重试重新
  合并；采样中 note 到达；raw input symlink 不读外部、不替换链接；真实子进程 os._exit(23)
  位于 baseline 写完、SQLite commit 之前，退出后 job 仍 running/旧成功水位，lease 到期接管
  使用有效已完成 workspace 跳过重新采样并完成 DB 提交。最后一例不等同于物理断电/fsync 保证。
- 该文件共 11 个实例通过；原真实 Runtime 的提取→合并→跨 Turn index→search→read→citation
  与后台失败隔离继续通过。最终全量 **306 passed**（13.41s，exit 0），ruff check / format
  （148 files）、compileall、pip check 通过。未运行真实模型或 Rust 测试。
- README、development 同步 baseline 迁移、验证与已知共同限制。与第七批相比，审计明确撤回
  “Codex 已有跨文件/DB 原子发布而 Corki 缺失”的未经证明归因；不是缩减目标，而是按源码
  纠正参考契约。仍不承诺发布过程的所有读取都来自同一版本。

本轮为 progress，无外部阻塞。整个目标保持 active；A/B/C、D 完整提取筛选/retention/冷却/
污染/read-update-forget 与 E 各未关闭项继续执行，不以本批 baseline 修复替代全范围验收。

第八批独立复验补充：extensions demo status=ok，MCP discovered/called、skill recall、plugin
called、model continuation 均为 true；Codex 工作树仍干净，teach.md SHA-256 与初始值一致。

## 第九批实施前决策：工具结果与错误边界（B07/E01/E03）

- Codex `tools/src/function_call_error.rs` 明确 RespondToModel / Fatal；
  `core/tools/parallel.rs::handle_tool_call` 仅 Fatal 升为 CodexErr，其他返回失败 response。
  registry 未知工具 RespondToModel，payload kind 不匹配 Fatal；task join/panic Fatal。
  Rust ToolOutput 是类型化结果，不能直接将 Python dataclass 类型注解视为相同的运行时保证。
- Corki executor 只捕获参数校验与 await handler；ToolResult 的内容、显示文本、附件、plan
  缺少完整运行时校验，截断与后续持久化在 try 之外，非法字段可导致 TurnFailed，P1。
  另一方面所有 handler Exception 都被吞成 Observation，缺少显式不可恢复工具错误通道。
  错误消息又绕过 output budget，异常大字符串能撑大模型请求。
- `codex-mcp/connection_manager.rs::call_tool:903-963` 使用 server/requested timeout 的较小值，
  包装 server/tool 身份，工具 transport error 返回调用方；不是全工具统一超时。
  Corki MCPClient.request 已 wait_for per-server timeout，但 TimeoutError 无说明且未知副作用
  状态不明确。本批不虚构 Codex 全局 timeout，也不自动重试结果未知的操作。
- 方案：校验和规范化整个 handler 返回值并复制为受控结果；坏结果返回有界失败 Observation，
  不进入 ledger/state/model。显式 FatalToolError 绕过 Observation 并接入 Runtime tool 终态；
  CancelledError 继续原样传播。错误文本/显示文本与正常输出同样限额；超时明确结果未知。
- 验收：非法 content/display/is_error/attachments/plan、超长异常、未知工具与 JSON/schema
  回归；真实 Runtime Observation 后续接、fatal 取消并行 sibling、不重复执行；MCP 真实
  request wait_for 超时与用户取消分别得到 Observation / TurnCancelled，且 exchange 已关闭。
  完整 JSON Schema、动态 MCP 重连/更新、强制隔离等原目标仍保留，不能以本批替代。

### 第九批实现与证据

- 产品修改前，7 个坏返回值的实际 Runtime 场景均失败：None content、非字符串 display、
  非 bool is_error、非 ImageAttachment、非 ToolStateUpdate、非法 plan status、孤立 surrogate。
  部分坏字段不是立即抛异常，而是被当成成功结果传回模型；测试明确要求失败 Observation。
  fatal parallel 场景则因错误被吞、sibling 永久等候而超时。
- MCP fixture 补齐 transport、取消期望按 Runtime 契约修正后，真实 request wait_for 的
  timeout 场景暴露只有空 TimeoutError、无未知结果说明；取消是已有正确行为的回归，不计为修复。
  另两个 unit 场景先复现 130k 字符 exception / JSON parse error 完全绕过 80-char 工具预算。
- `ToolExecutor._normalize_result` 在同一受控边界内验证与截断整个结果，复制 attachment list
  与 plan dict，保证失败 Observation 不带不可信附件或状态更新。Unicode/type/plan 失败不再
  推迟到 storage/model/graph 才爆炸；tool_search 仍保持已有 whole-schema 专用预算。
- `tools/errors.py::FatalToolError` 为显式不可恢复工具错误；executor 不吞掉，Runtime 生成
  error_kind=tool、retryable=false 的唯一 TurnFailed。真实并行测试验证 sibling 已关闭、没有
  第二次模型采样。并行回收复用原 graph finally cancel/join，不另建脱离主循环的执行器。
- 普通错误与 parse error 均按每工具/global budget 截断，display 最大 4k；异常 __str__ 本身
  抛错时仍生成安全错误文本。TimeoutError 明示 timed out / outcome may be unknown / 不自动
  重放副作用。取消不被 Exception 捕获，MCP exchange finally 已关闭且调用次数严格为 1。
- 新增 10 个 integration 实例与 4 个 unit 实例；其中额外验证正常结果的 plan/attachment
  不共享 handler 的可变容器、损坏异常格式化不逃逸。预设 ToolCall 证明 Harness，不代表模型选择质量。
- 最终全量 **320 passed**（14.43s，exit 0），ruff check/format（150 files）、compileall、
  pip check 通过；Codex 基准 commit 与干净工作树不变，teach.md SHA-256 与初始值一致。
  未执行真实模型或 Rust 测试。README/development 同步错误分类与未实现边界。

本轮 progress，无外部阻塞。B07 的完整 JSON Schema 与媒体内容有效性、B05 MCP 更新/重连/
取消通知、A02 流中执行与 partial retry、A07 故障恢复及其他 C/D/E 未关闭项全部保持有效。
不能把 typed FatalToolError 或这批 green tests 等同于整套 Codex 错误体系已完成；goal 仍 active。

## 第十批实施前决策：流中执行的恢复前置条件（A02/A07）

- 重读 `stream_events_utils.rs::handle_output_item_done:290-400`：完整调用先记录，立即创建
  ToolCallRuntime future；正文/reasoning 也分项记录。`parallel.rs` spawn 拥有任务并用读写锁
  约束并发/独占；`turn.rs::drain_in_flight:2213` 按 FuturesOrdered 顺序回填结果。
  `responses_retry.rs` 在循环层处理重试与 transport fallback，不能等同于重放旧 request body。
- Corki `_call_model` 只处理 delta/ModelCompleted，adapter 也只在整响应终态生成 canonical
  ToolCallItem；`_execute_tools` 是之后的独立 graph node。A02 仍行为不一致，没有被此前修复关闭。
- 分项执行必须先有 durable partial-step 与完整调用身份：否则已执行副作用、但无 ModelCompleted
  时恢复会再次采样旧请求。当前 `SQLiteSessionRepository._claim_tool_call` 只比较 thread/turn/name，
  同 call ID 改参数也会返回旧 completed result；`_complete_tool_call` 又允许覆盖 completed result、
  不核对 tool_name。这是实际恢复契约缺陷，优先修复，不将其称为 A02 已实现。
- 本批先落实调用参数指纹、旧库兼容和结果提交不可改写；Codex executed-tool-call metadata 是
  feature-controlled observability，不能宣称它提供与 SQLite ledger 相同的 exactly-once 保证。
  Python ledger 是 Corki 兼容实现，对齐的是不误认身份、不重放未知结果的用户验收要求。
- 参数以解析后的 JSON 对象规范编码，合法 JSON 的空白/键顺序不影响身份；畸形调用保留 raw
  arguments 身份。旧 rows 缺参数证据时不伪造指纹、不将结果安全复用，返回明确不可验证的错误。
  completed result 相同提交可幂等，不同结果或不同工具名不可覆盖已有事实。
- 验收：running/completed/重启后三个参数碰撞窗口、等价 JSON、旧库迁移、重复结果提交、不同
  tool name；实际 Runtime 从 committed model step 恢复时拒绝不匹配缓存且不执行副作用。
  下一实施链仍须 ModelItemCompleted → partial-step journal → owned live scheduling → final reconciliation
  / cancelled-or-failed drain → checkpoint 恢复，不能只添加事件枚举或继续等待全响应来冒充流中执行。

### 第十批实现与证据

- `test_tool_identity.py` 首批 7 个实例产品修复前全失败：running/completed/reopened 的
  同 ID 不同参数不识别为碰撞；completed result 可覆盖或被改成另一工具名；旧库直接复用
  无参数证据的成功结果；不同 malformed raw arguments 复用同一个旧失败 Observation。
- `tool_executions.arguments_sha256` 为 nullable 兼容迁移列；新 claim 在写事务中保存参数
  指纹，合法解析 JSON 使用稳定键顺序/紧凑编码，非法参数按 raw 身份区分。schema 检查与 ALTER
  现在也在 BEGIN IMMEDIATE 下串行，避免同时初始化的列迁移竞态。
- `complete_tool_call` 在同一写事务核对 thread/turn/tool name，已 completed 的相同语义结果
  提交幂等，不同结果抛 StorageIntegrityError，不覆盖原值。并发冲突完成测试证明恰好一个提交
  胜出，另一个报 integrity error，claim 返回的是胜出结果。
- handler 收到 deepcopy 的 ToolCall，测试验证嵌套 paths list 修改不污染已 claim 的 durable
  模型调用；这不是使 Python dict 成为全局不可变对象，其他注册/调用源冻结仍需按范围核对。
- 兼容迁移不删除旧 rows、旧结果或历史；旧 row 参数列为 NULL 时返回 unverified Observation，
  不执行、不直接复用。曾尝试从同 ID 历史回填，新增反例证明后来追加的冲突调用可能被误认为旧
  参数，已撤回该方案并保留回归测试。当前请求和无时间归属证明的历史都不能当成回填证据。
- 新增 `test_tool_identity_resume.py` 真实 Runtime 从已 committed model step 恢复，ledger
  只有旧参数结果：新请求得到 collision Observation，handler 调用数 0，后续模型调用数 1，
  原 ledger 成功结果保持不变。这是恢复主路径，不是仅调用 standalone hash 函数。
- 本批共新增 11 个实例（9 storage、1 executor、1 Runtime）。最终全量 **331 passed**
  （15.51s，exit 0），ruff check/format（152 files）、compileall、pip check 通过。README/
  development 同步恢复契约和旧库限制；未执行 Rust 或真实模型测试。

本轮是 A02 恢复前置条件的实现进展，不是 A02 已完成：模型事件仍缺分项完成协议，Responses
done item 仍未在流中启动工具，partial-step journal、调度与恢复还要继续实现。该缺口明确保留，
完整 A/B/C/D/E 目标不缩减；无外部阻塞，goal 继续 active。

## 第十一批实施前决策：完整调用项驱动实际执行（A02/A07/E02）

- 基准 commit 仍为 ddf04ad26789d040f9ef6a96736f76602e35a6cc。逐项重读
  `stream_events_utils.rs::handle_output_item_done`、`tools/parallel.rs::handle_tool_call_with_source`、
  `session/turn.rs::drain_in_flight`；对应 Codex 测试为
  `tool_parallelism.rs::shell_tools_start_before_response_completed_when_stream_delayed`。
- 对齐完整 output_item.done 后启动的默认 Responses 路径，不根据可解析的参数 delta 猜测完成。
  Chat 没有等价分项终态时保留整响应完成路径；原生 tool_search_call 也属于可执行完整项。
- 新增 provider-neutral 分项完成事件、SQLite partial-step journal 与真实 model node 所有的
  live scheduler。调用先写 journal/history，再启动；并行组可重叠，独占调用形成顺序屏障。
  完整响应必须与已发布项核对，之后仍走 evaluate/tools 节点，不能重复执行或重复 UI 完成事件。
- partial journal 恢复不是伪造 ModelCompleted：旧 step 不重新采样，先补齐调用结果，再以
  continuation 进入新 step。普通模型流失败 drain 已启动工具，取消收回任务并为未知/未启动调用留下失败 Observation；
  provider 在分项输出后不能透明重放旧请求。缺 response.completed 仍为失败，不标成功。
- 验收采用真实 Responses adapter + 延迟 SSE + Runtime：handler 执行是服务器发送 completed
  的前提；另测截断、重复/冲突项、并行/独占、预算、取消、partial 恢复与 checkpoint 重放。
  这是 Harness 确定性测试，不代表真实模型选择质量；其他 A/B/C/D/E 未关闭项继续有效。

### 第十一批实现与证据

- 首批两个真实 Responses/Runtime 测试在修改产品前失败：服务器等待 handler 执行才发送
  response.completed，旧实现无法执行 handler。现在调用 history/journal 已落库才进入 handler，
  工具结果进入第二次真实 Responses 请求，handler/ToolCallCompleted 均恰好一次。
- `ModelItemCompleted` 进入 model node；Responses function/tool_search done 立即发布，最终
  ModelCompleted 复用同一 canonical item。相同 done 去重，冲突 done/终态参数、done 后 delta
  报协议错。新增 changed_final 反例先失败后修复；另一个 added 顺序与 done 顺序不同的反例
  先失败，现以 done/执行顺序构造最终调用集合，不回退到早期参数 buffer 插入顺序。
- `partial_model_items` 为增量新表，与 canonical item 同事务记录，已有数据库不用删除数据。
  append 的取消等待写线程结束；graph 捕获完整项时 deepcopy，避免 adapter 之后修改嵌套参数。
  partial 恢复直接进入工具/continuation，而不向 model_steps 写伪造的成功采样记录。
- `LiveTools` 属于当前 model node；任务依赖约束平行组、独占屏障和后续平行组。model_events
  同时观察工具致命失败，及时关闭挂起的模型 read；退出必须 cancel/join 全部 owned tasks。
  正常结果经 streamed_tool_results 接入原 tools 节点，不再调用 handler 或重复 UI 完成事件；
  结果 history 按调用完成项顺序写入。工具预算在分项进入时检查，不等整响应结束后才拦截。
- 重新追踪 `turn.rs:2839` 后修正普通模型错误收尾：它必须 drain 已启动工具，不是用户取消。
  原型曾一律取消，新增“断流后延迟返回”的反例失败后已修复；实际结果 observed 得以保存，
  该请求仍 TurnFailed 且 HTTP 请求次数 1。Corki 显式 FatalToolError 仍采用终止并取消 sibling
  的契约；Codex drain 内错误日志/其他 fatal 路径完整对齐尚待 E 项验证，不由本批断言相同。
- Runtime 验证：两平行工具互相等待启动、独占再执行、后一平行组最后启动；四个结果顺序
  确定。挂起模型+挂起工具的 cancel/fatal 都关闭资源并保留 unknown Observation。steering
  先关闭旧模型读取，等工具结果落库后再持久化新输入/准备上下文，下一请求包含结果和新要求。
- partial 恢复先以持久化 fixtures 验证未 claim/running/completed 三窗口，再增加真实子进程
  `tests/fixtures/live_tool_crash.py`，分别在 journal commit、实际文件副作用、ledger complete
  后 os._exit(23)。父进程从原 LangGraph checkpoint 恢复：仅未 claim 调用首次执行，其他
  handler 不重放；running 返回 unknown，completed 返回 saved；旧 step 不重采样、不伪造
  ModelCompleted，磁盘副作用仍仅一份。三例均实际运行，不只是构造数据库或 mock checkpoint。
- 本批新增 **19** 个实例（14 integration，其中 3 个真实进程退出；5 adapter）。最终全量
  **350 passed**（18.16s，exit 0），包括组合 examples 的 e2e；ruff check/format（156 files）、
  compileall、pip check 通过。Codex 工作树仍干净，teach.md SHA-256 不变。未跑 Rust 或真实模型。

当前仍是 progress，目标 active、没有外部阻塞。下一优先项 A01：正文/reasoning 仍终态聚合，
工具先落库后混合历史顺序尚未与 Codex 一致；应完整接入分项身份/引用/opaque reasoning。
A02/E02 的更新历史后重试/transport fallback 也尚未实现，禁止旧请求重放不是它的替代品。
此外保留全部 A/B/C/D/E 未关闭差异；局部 350 green tests 不是整体 Harness 完成证据。

## 第十二批实施前决策：正文/reasoning 的分项身份、顺序与发布（A01/C01/D03）

- Codex `handle_output_item_done` 的 non-tool 分支调用 `finalize_non_tool_response_item`，
  每项独立生成完成事件和历史记录；引用在该项结束时解析/记录 usage，而非整个 response
  结束后再处理。`Reasoning` 的 encrypted content 与 summary 按项保留并重放。
- Corki Responses 目前将全部 text/reasoning 聚合成两个终态项；工具先记录后，会产生历史
  顺序错误。多个 reasoning 的 opaque 内容互相覆盖，纯 encrypted reasoning 还可能丢失。
  graph 的整响应 citation filter 会把下一条消息吞进上一条引用缓冲；若直接记录原始正文，
  终态引用规范化又会与 append-only item 冲突。状态部分一致，P1。
- 方案：独立内容项 accumulator；delta 带可选 canonical item identity；完整 message/reasoning
  在 done 时发布，最终响应复用同一项并核对内容。graph 按消息管理可见输出/引用过滤，规范化
  后记录 partial/history，引用 usage 按完成项记一次；多消息不再折叠，纯 opaque reasoning 保留。
- 验收：混合 message→reasoning→tool→message 的 SQLite/下一请求顺序；两 reasoning 各自
  encrypted replay；消息间引用隔离；delta/final 冲突；断流后完整非工具项保留且不标成功；
  无分项 done 的兼容终态路径、既有 Chat/compaction/memory 全量回归。流重试等剩余目标不缩减。

### 第十二批实现与证据

- 两个实际 Responses/Runtime 混合输出场景在产品修改前均失败：history 首项是工具而非前面的
  正文；纯 encrypted reasoning 未保存，另一个 reasoning 覆盖其 opaque 状态；断流只剩调用
  和结果。现按 message→reasoning→tool→message→reasoning 顺序保存完整项，再回填 tool result。
  下一次实际 Responses 请求同时包含 r1/r2 两个 provider ID 和各自 opaque-one/opaque-two。
- 新增 `models/response_content.py::ResponseContent` 管理逐项 canonical identity、index alias、
  文本片段和完成状态；Responses 在每条 message/reasoning done 时发布 ModelItemCompleted，
  最终 ModelCompleted 复用原有项。多个正文不聚合，纯 opaque reasoning 不丢弃。重复完成项
  幂等，改写正文/opaque/phase、完成后 delta 均报协议错误；内容与工具参数合并计算响应上限。
- `ModelTextDelta/ModelReasoningDelta.item_id` 是向后兼容可选字段；新的 Responses delta 关联
  到稳定 canonical item。匿名 delta + 无 ID 的终态完整正文曾在原型中生成两个相同 item，新增
  反例先失败后修复：anon/index/id 对齐后仅一项；内部 anonymous/index 不会伪造 reasoning
  provider ID。未提供 item.done/output 的旧式 delta 仍能在有效响应终态生成规范项。
- `core/output.py::ModelOutput` 按消息过滤引用、补齐缺失尾部并只发一次完成事件。交错消息
  不共享 citation buffer；graph 在每个正文项完成时先规范化引用再记录 partial/history，避免
  最终归一化引起 append-only 冲突。正常与断流测试均验证结构化 citation 已落库，然后
  mark_memories_used 被调用一次；终态不重复该统计。引用隐藏块不进入 UI，后续正文正常显示。
- 旧 ModelPort 仍可发无 item_id 的 delta：新增“一个 delta 跨两条最终消息”的反例先复现 UI
  firstsecondsecond，现按完成项消费已显示前缀，保持 firstsecond；不强迫 Chat/扩展立即迁移。
- 补读 Codex `turn.rs:2484` 和 `stream_events_utils.rs::finalize_non_tool_response_item`：
  last_agent_message 是最后非空消息，不是同响应全部正文连接。新增反例曾得到 checkingdone，
  修复后 TurnCompleted.final_answer 为 done，commentary 仍正常流式显示、保存和重放。
  `AssistantMessageItem.phase` 为可选字段，Responses 保存/重放 commentary/final_answer；旧
  SQLite 行无 phase 时以 None 兼容，测试验证重开和幂等 append 不冲突、不重复。
- 无工具的完整正文之后发生 steering，也能保留该消息并接受新输入继续；不会错误进入
  “missing tool calls” 分支。该场景使用真实 Runtime，同 Turn 第二次请求含旧正文和新输入。
- 新增 **14** 个实例：3 integration（混合正常/截断与无工具 steering）、8 adapter（内容
  冲突/总预算和身份兼容）、2 output presenter、1 旧库；此前第十一批真实进程退出/恢复、
  原生/兼容 tool discovery、compaction/memory 及组合 examples e2e 均继续运行。
- 最终全量 **364 passed**（17.38s，exit 0）；ruff check/format（161 files）、compileall、
  pip check 通过。Codex 仍固定原 commit 且无工作树变更；teach.md SHA-256 不变。未执行 Rust
  或真实模型验证，预设 SSE/ToolCall 只证明 Harness，不代表工具选择质量。

本轮 progress，无外部阻塞，完整 goal 保持 active。下一批优先 A02/E02：核对 Codex 如何在
drain 已完成项后，用更新历史重试采样及传输 fallback；不能把“拒绝透明重放旧请求”当作已对齐。
其余 A/B/C/D/E 差异不缩减；全部 hosted 输出类型、畸形 provider payload 和能力特例也仍需
按适用路径验收，本批不宣称所有模型输出协议或整个 Harness 已完成。

## 第十三批实施前决策：基于已更新历史的流重试与连接恢复（A02/A07/E02）

- 完整追踪 Codex `turn.rs::run_sampling_request:1411-1517`：第一次使用 initial_input，后续
  attempt 使用 clone_history().for_prompt()；StepContext/base instructions/tool runtime 保持
  当前逻辑 Step，成功返回前不进入外层新的 Step。`try_run_sampling_request` 在 stream error
  后先 drain_in_flight，再由 is_retryable 与 responses_retry 控制退避、上限和 transport fallback。
- Corki 当前 adapter 只能在无已发布内容时重发原 payload；graph 遇 ModelError 即失败。
  需要分离 logical step_count 与 durable sample index，避免 retry 消耗新 Step 或重复读旧 partial；
  同时存储 ModelFailure/已用 retry 次数，checkpoint 落后时也不能把失败当作成功或重置次数。
- 主 Runtime 的 ModelRequest 显式指定由 Harness 管重试，内置 adapter 此时不另做内层重试，
  防止次数相乘；独立 compaction/memory 请求保留其原 adapter 边界。本批不把两类路径混为一谈。
  transport/可重试 server/缺终态可重新采样；鉴权/上下文超限/canonical 契约破坏不可重试。
  补读 SSE 路径后纠正：Responses envelope 解码失败先跳过，不立即重采样；无有效终态时
  才由 Stream 错误进入 retry。completed 结构解码失败和 incomplete 属可重试 Stream，不能
  仅凭 `is_retryable::Json` 枚举推断所有畸形 payload 的具体行为。
- 重试前先保存已完成项与工具结果；回灌 tool result/plan/已发现定义后用更新历史发下一请求。
  不执行未完成的参数 delta，不重放未知副作用。等待可取消/可被新输入打断；预算归属逻辑 Step。
- 本地 Codex 的 fallback 依赖已启用 WebSocket session；Corki 当前只实现 HTTPS，对齐其 HTTPS
  重试路径，不能宣称有 WebSocket 降级。纠正先前分类：`features/src/lib.rs:1219-1224`
  的 UnboundedConnectionRetries 是 Stable、default_enabled=true，并非实验功能。
  `responses_retry.rs` 限定 Sampling、ConnectionFailed、非 internal、非 Bedrock；使用独立
  connection counter 和 5→10→20→40→60s 退避，不消耗普通断流上限。Corki 主采样接入同类
  默认路径和关闭开关；独立 memory/compaction adapter 请求仍有界。HTTP-only 不虚构 WS。
- 验收：实际 Responses done tool/message→断流→新请求包含事实→成功；unknown 只回灌不执行；
  重试上限/不可重试分类/逻辑 Step 上限、退避取消与 steering；失败记录与 checkpoint 间崩溃
  恢复不能额外采样。先写反例，再接入真实 Runtime，不以 standalone retry helper 代替闭环。

补充存储边界决策：Codex sampling 将每次 try 的 Ok 与 Err 分离；Corki 新增失败表也必须与
同一 attempt 的 model_steps 互斥。当前两个 BEGIN IMMEDIATE 只检查各自表，可能同时留下
成功和失败事实，使恢复优先级改变结果；需要双向拒绝冲突并验证并发只有一个结果胜出。
旧失败 payload 缺 connection_retries_used 应按 0 加载且允许语义相同的幂等提交，不改写原记录。

### 第十三批实现与证据

- 新增 `ModelFailure`、SQLite `model_failures` 和 graph `retry_model`，失败 attempt 与成功
  ModelCompleted 分开。主请求设置 harness_managed_retries，Chat/Responses 不叠加内层预算。
  physical sample_count 与 logical step_count 分开；普通重试保留 instructions，从 active
  history 重建 request_items 和发现工具视图。20 次失败后成功仍只占一个逻辑 Step。
- `tests/integration/test_history_retry.py` 实际 Responses 请求先完成 probe，再中断 SSE：
  retry 的真实 HTTP payload 含 observed，handler 只执行一次。adapter 上限 7 / Runtime
  上限 2 时最多 3 次请求。兼容搜索输出在失败后使下一请求获得定义；预先 claim 的未知
  副作用只回灌 unknown，不能重放。鉴权/输入超限不重试，503/无终态按有界路径处理。
- 分开检验解码与语义契约：Codex `codex-api/src/sse/responses.rs:615-630` 跳过坏 envelope，
  Corki 新增“坏 JSON、数组 envelope 后仍有有效 completion”反例并修复；不得把跳过事件
  等同忽略缺终态。completed 无效 end_turn 的五项旧测试改为耗尽 3 次重试后失败，不再
  强制一次请求；任何 attempt 都没有成功 Completed。incomplete 的输出耗尽标签保留，
  可重新采样但不删历史。完整 item payload 的容错/严格语义差异仍待逐类审计。
- `tests/integration/test_connection_retry.py` 通过实际 Chat/Responses adapter 注入连接错误：
  默认网络恢复 6 次后成功，延迟 [5,10,20,40,60,60]，不受普通上限 1 限制；关闭开关或
  显式 Bedrock 名称时最多 2 次请求。连接/读取失败交替：连接计数 1、2，普通计数仅 1，
  第二次普通错误失败，不重置网络延迟。两 adapter standalone 请求仍最多 2 次，未扩展
  internal sampling 的无界语义。对应 Codex `stream_no_completed.rs:103` 和
  `retry_after.rs:1326` 的场景已阅读；Rust 测试没有运行。
- `core/retry.py` 拥有 backoff 与 realtime wait，取消/steer 后 cancel+join 所有 wait tasks。
  实际 Runtime 分别在 60s 普通退避和 5s 网络退避中取消或追加输入，2s 内结束或继续，新
  请求包含新输入，无悬挂 retry task。长退避数值测试仅替换等待时钟，不替换 Runtime/adapter。
- `tests/fixtures/retry_crash.py` 真实子进程在 save_model_failure commit 后 os._exit(23)，
  原 checkpoint 恢复：尚有普通次数时只追加一次采样，已耗尽时零采样；文件副作用只有一份。
  连接路径在第 4 次失败 commit 后退出，恢复使用 attempt 4 / 40s，不回退到 attempt 1 / 5s。
  这三个场景均实际运行，不只是手工构造恢复状态。
- 存储新增反例先复现四个失败：旧 failure 可读取却不能幂等提交；success→failure、
  failure→success 和并发提交均留下矛盾事实。现 BEGIN IMMEDIATE 内双向检查，同一 attempt
  只能有一种终态，并发只有一个胜出；旧 payload 按默认值比较且不改写。取消与失败写错误
  同时发生时仍 join 线程并保留 CancelledError，不把背景 RuntimeError 当成主结果。
- 布尔配置反例先复现字符串 'false'/数字/数组被接受，现全部明确报配置错误。CLI 新测试
  检验 interrupted reason、流关闭/重开和有界/∞ 提示；网络重试不再声称收到了用户新输入，
  默认 reason=steering 保留旧事件构造兼容。README/development 已更新，不再说部分输出后
  必须直接失败，也不再把默认持续连接恢复归为实验功能。

本批仍不关闭整个 A02/A07/E02：Corki 没有 WebSocket transport；源码
`retry_after.rs:247/922` 的 HTTP request 层/Retry-After 规则，typed ServerOverloaded 与
UsageLimit/RateLimit 等完整 server code 映射，以及全部畸形 item/hosted 输出路径仍需对齐。
例如仅凭 generic SERVER 可重试，不能推导 Codex ServerOverloaded 也会重试。
其余 B02 精确 tokenizer/ranking、B05 动态 MCP、B06 Code Mode、C/D 全生命周期与 E 未关闭项
继续保持原目标，不用本批局部测试替代全范围验收。

最终验证：相对第十二批新增 **43** 个测试实例，完整 `.venv/bin/pytest` **407 passed**
（25.52s，exit 0，含组合 examples/MCP/skills/plugin 与 CLI PTY e2e）。ruff check、format
（167 files）、compileall、pip check 全部通过。Codex commit 不变且工作树干净，teach.md
SHA-256 不变。没有执行 Rust 或真实模型；mock SSE/预设 ToolCall 不证明真实工具选择质量。
本轮是实现及验证进展，无外部阻塞，完整 goal 继续 active。下一优先项 E02：完整追踪
provider/server error code → ApiError → CodexErr → retry 的映射及 HTTP request/stream
两层预算，先确认实际触发路径，避免用笼统 HTTP status 或 SERVER 分类冒充 Codex 故障语义。

## 第十四批实施前决策：Responses SSE 错误分类与错误终态（E01/E02）

- 完整追踪 `codex-api/src/sse/responses.rs::process_responses_event/process_sse_with_treatment`
  → `api_bridge.rs::map_api_error` → `protocol/src/error.rs::is_retryable` → 主采样 retry。
  response.failed 按 error.code 区分 context_length_exceeded、insufficient_quota、
  usage_not_included、cyber_policy、misalignment_policy_violation、invalid_prompt/bio_policy、
  server_is_overloaded/slow_down，均不可重试；rate_limit_exceeded 与未知 code 可重试。
  只有 rate_limit_exceeded 从 message 的 try again in N s/ms 提取等待时间，不靠文字猜类别。
- Corki 当前只识别 context/输出限制，其他全部 SERVER retry；会重复发送已被拒绝的请求，
  丢失 rate-limit 类型与服务端指定延迟。应拆独立 Responses 错误归一化模块，接入实际 adapter
  与 Runtime；故障记录沿用现有 kind/retryable/delay 字段，不改变已有 SQLite 结构。
- Codex SSE parser 暂存最后一个 ResponsesEventError，在正常 EOF 时上报；流 I/O 错误直接
  覆盖为 Stream，后续有效 completed 可以正常终结。Corki 即时 raise 会丢弃后续完整项；应
  同样暂存 error，继续发布完整项，仅有效 completion 成功。顶层未知 event（含 error）按
  SSE 路径忽略；不把 WS 特有 error envelope 处理混入 HTTP SSE。
- completed 的结构错误也应暂存；canonical 已完成项改写等语义完整性异常仍立即失败。
  待验收：错误 code 矩阵、误导性 message/畸形 Error 结构、rate delay 单位、failed 后完整项
  与 EOF/valid completed/IO error 的组合；真实 Runtime 工具先完成后 quota 不能重放。
- 同步已追踪 HTTP：endpoint/session.rs → telemetry.rs → codex-client/retry.rs，provider
  默认 request_max_retries=4 / stream_max_retries=5，均 cap100；request 5xx 和 transport 可
  重试、429 不重试；200ms 指数退避带 0.9..1.1 jitter，不遵循 HTTP Retry-After。HTTP 429
  最终映射 RetryLimit 或 usage 类型，HTTP 503 overload 可先经过 request retry 再致命。
  Corki 仍缺这层独立预算，默认 3/0.5s/8s cap 也不同。本批先修 SSE 分类与事件生命周期，
  HTTP 两层实现/default/backoff 保持明确未关闭，不用 SSE 修复冒充完整 E02。

### 第十四批实现与证据

- 新增 `models/response_errors.py`，区分六个新增 ModelErrorKind；现有 ModelFailure 的
  kind/retryable/retry_after_seconds 自动持久化，不需要改写旧库。非重试 code 不受普通
  stream budget 驱使重新采样；rate-limit 与 unknown 分开，不将 context 字样或延迟文字
  当作错误类别。message 缺失/空白时为 policy 错误提供明确说明，不自动提交继续指令。
- 真实 Runtime 12-code 矩阵与 3 个指定延迟用例在修改前 **12 failed / 3 passed**：
  原实现 quota/policy/invalid/overload 错误均为 server+retryable，rate-limit 类型和指定
  delay 也丢失。修复后 15 项全通过：probe 完整调用只执行一次，结果持久化，致命错误只
  发一次 HTTP 请求；可重试错误下一请求含 observed，耗尽预算后以准确 kind 失败。
- SSE delay 按参考 regex 的 seconds/ms 规则：11.054s、125.9ms→0.125s、大小写 seconds
  均抵达 ModelRetryScheduled；HTTP response 上的 Retry-After=99 不覆盖 SSE message。
  负值/NaN/浮点溢出不安装为等待时间，unknown code 即使带 try-again 文字也不提取延迟。
- adapter 现在把 response.failed/incomplete 和已校验的 completed 解码错误暂存，EOF
  才抛最后一个错误；IO ReadError 覆盖 pending typed error。后续有效 completed 可以正常
  终结，后续完整 message 保持分项发布；只有 unknown 顶层 error 事件时仍要求有效终态。
  canonical 内容改写等完整性错误仍直接终止，不因 pending error 而忽略。
- 25 个 adapter 场景验证：四类前置错误/未知事件后的有效 completion、最后错误覆盖、
  IO 覆盖；畸形 Error 的 string/i64 类型、误导文字、policy fallback、非法 retry delay。
  不可解码 typed Error 降为 generic retryable failure，不能因字段值真假而误标 quota。
- 再补 3 个实际 Runtime 延迟 SSE 场景：先收到 quota error，再收到完整 probe 调用；
  EOF→quota TurnFailed、valid completed→执行结果回灌后下一 Step 正常完成、挂起→取消。
  三条路径 handler 都只执行一次，HTTP stream 均关闭；取消没有 model_failure 记录，
  成功只发生在有效 completion 后，没有错误事件触发的提前终止或假成功。
- 仍未闭合：结构化 misalignment 附加诊断/continuation UI、完整 usage schema（本地默认
  缺字段为 0 与参考 required i64 有差异）、所有 hosted/item payload；本批不声称这些完成。
  HTTP 429/503/auth/usage、两层 request/stream budget 及 jitter/default 进入下一批；Corki
  目前默认 3、0.5s、8s cap 并不等于参考 5、200ms、无固定8s cap，不能因测试有界就称等价。

最终验证：本批新增 **43** 个实例（18 Runtime integration、25 adapter），全量
`.venv/bin/pytest` **450 passed**（24.99s，exit 0，含组合 e2e）。ruff check/format
（170 files）、compileall、pip check 通过；Codex 工作树干净，teach.md SHA-256 不变。
未运行 Rust 或真实模型，mock 请求只证明 Harness 和协议行为。上一轮与本轮均为实际实现
进展，无外部阻塞；完整 goal 保持 active，下一批按已追踪源码落实 HTTP 层和默认重试参数，
其余 A/B/C/D/E 范围不缩减。

## 第十五批实施前决策：HTTP 请求重试与默认预算（A02/E02）

- 重读 pinned `model-provider-info/src/lib.rs:27-35/314-321/359-370`、endpoint/session.rs、
  telemetry.rs 与 codex-client/retry.rs：请求层默认 4 次重试，只重试 5xx 和建立响应前的
  transport 错误，不重试 429；成功取得 SSE 响应后，读取错误归 stream 层，不能重放旧请求。
  流层默认 5 次，两层配置 cap100；200ms 指数退避、0.9..1.1 jitter，无 Corki 固定 8s cap。
- 两层次数可能相乘，这是各层独立职责，不是第十三批阻止的重复“流重试”。请求层重用
  同一 payload、还没有模型输出；流层必须重建 durable history。HTTP Retry-After 不覆盖
  本地 backoff，SSE rate_limit 的指定延迟仍生效。连接持续恢复发生在请求层耗尽之后。
- api_bridge.rs 在请求层耗尽后才分类：503 overload/slow_down 致命，普通 503/500 可流重试；
  429→RetryLimit 或 usage_limit_reached/usage_not_included，均致命；400→InvalidRequest
  （特殊 cyber/misalignment/invalid image 单独分类），不是靠 context 文字判断压缩。
  其余 status→UnexpectedStatus 可流重试，包括没有 auth recovery 的静态 key 401/403。
  缺失本地 key 仍直接失败；不擅自增加 OAuth/自动换账号行为。
- Corki 两 adapter 当前只在 stream 边界重试，HTTP 分类/默认值也不同。实施独立、拥有响应
  close 的 HTTP context manager，仅把成功响应交给 SSE parser；yield 后错误不进入请求重试。
  默认配置改为 stream5/request4/base0.2；保留 max_retries 为 stream_max_retries 的旧别名。
  构造参数和真实 Runtime composition 都要接入，取消/steer 在 HTTP 退避期间仍可中断。
- 验收：实际两种 adapter 的状态/code 矩阵、两层次数与持久化 sample 数；请求内恢复不产生
  假 ModelFailure；响应关闭先于退避；HTTP wait 取消/steer、部分 SSE 失败不走 HTTP replay；
  配置默认/别名/非法值/cap、jitter 与大于 8s 的 backoff；完整旧回归按正确层次更新断言。

补充底层核对：`http-client/src/transport.rs:139-157` 只有 2xx 是成功，读取失败响应 body
使用 .text().await.ok()，不会把 HTTP status 改成 transport error。新增反例复现 Corki
把带 completed SSE 的 302 当成功、把 body 截断的 429/503 改为 transport；需要保持 status
权威，诊断 body 读失败用空值，取消不进入普通 body 读取错误捕获。

### 第十五批实现与证据

- `models/http_stream.py` 独立 request loop，response 成功后在 retry catch 之外交给 SSE；
  failed response 在 backoff 前关闭。`http_errors.py` 按 HTTP status/code/type 分类，
  不再借用 SSE 文本判断；`backoff.py` 统一 millisecond/jitter/cap-u64 运算。settings→
  `_create_model`→两 adapter 的实际 composition 接入，stream5/request4、两者 cap100；
  stream_max_retries 优先于旧 max_retries。独立内部 adapter 保持有界两层，不启用无界连接。
- 首批 503/400 的 10 个两协议反例全部先失败：错误的 HTTP Retry-After=99 导致等待超时，
  context 文本触发错误类型，cyber code 丢失。修复后 26 个实际 Runtime/Chat/Responses
  status-code 实例通过，验证 HTTP 请求数、stream retry event 数、failure sample index
  和同请求 payload：503 overload=5次请求无流重试；普通503/500=10次请求、1次流重试；
  HTTP429一次即失败；静态key401/403及404为有界 UnexpectedStatus 类语义。
- 真实默认组合持续503产生30个HTTP请求、6个sampling失败记录，不是30个ModelFailure；
  request delay [0.2,0.4,0.8,1.6] 每个 sampling 重置，stream delay 单独 [0.2,0.4,0.8,1.6,3.2]。
  两次请求内503后成功只保存成功 sample0；5次连接失败后才进入独立5s网络恢复，然后成功。
  测试替换等待时钟和 jitter 值，未替换 Runtime/adapter/HTTP 实际调用链。
- HTTP wait 的 cancel/steer 用无限等待 gate 验证2s内取消或进入新输入请求；进入 wait 前
  failed response 已关闭，旧请求不继续发。原先真实 SSE 完整工具→ReadError→新历史重试
  回归保持通过，证明 body 错误没有退回 HTTP 层重放旧 payload/handler。
- 16 个配置/backoff 实例验证默认值、别名优先、cap100、非法类型/负数、毫秒jitter，
  指数退避可大于8s，SSE指定delay优先；巨大有限配置值使用饱和运算，不溢出为inf。
- 底层3个反例先失败后通过：302携带合法completed不能成功；429/503错误body ReadError
  仍保留HTTP状态，不提升为transport retry。追加长JSON反例发现先截正文会丢失末尾
  server_is_overloaded，现完整解析code后仅截展示文本，正确致命类别与4k展示上限同时成立。
- 旧测试按职责更新：连接恢复场景显式 request_max_retries=0 隔离流层；standalone连接
  上限1 + 默认request4明确期望10次；HTTP内恢复不产生 ModelRetrying（只记录request
  telemetry）；malformed completed 默认stream5故期望6次。原测试没有用关闭全部重试来掩盖
  新行为。HTTP输出耗尽400为InvalidRequest且不触发输入压缩，SSE incomplete标签继续保留。

未关闭：OAuth/managed auth恢复、响应诊断附加headers/usage/misalignment数据、timeout默认
与所有close抛错窗口、WS transport及全部provider特例。HTTP request attempt自身不持久化，
未收到模型输出前的进程崩溃可能再次发HTTP请求；不虚构远程采样exactly-once。其余A/B/C/D/E
范围继续有效；本批预算/分类通过不等于整个Harness完成。

最终验证：本批新增 **51** 个实例（31 Runtime integration、16 配置/backoff、4 HTTP边界），
完整 `.venv/bin/pytest` **501 passed**（30.37s，exit 0，含组合e2e）；ruff check/format
（176 files）、compileall、pip check通过。Codex工作树干净，teach.md SHA-256不变。
没有执行Rust或真实模型验证。本轮是实际实现进展，无外部阻塞，完整goal保持active。
下一优先项回到 B06 的完整缺失：从 spec_plan 的 effective_tool_mode/register_code_mode_executors
追踪到 code-mode-runtime 的 tools/ALL_TOOLS 与嵌套调用、wait/取消/恢复，不以曝光枚举替代
执行环境；本轮仅定位入口，尚未完成该调用链审计，也没有声称 Code Mode 已实现。

## 第十六批实施前决策：Code Mode 的 raw-tool 协议前置（B06/B07/A01）

- 本轮追踪 tools/mod.rs 的 requested/effective_tool_mode（模型 tool_mode 优先、feature
  次之，host 不可用时 CodeMode 可降 Direct、CodeModeOnly 不降）；spec_plan.rs:791-895
  只将 Direct/Deferred/CodeModeOnly 纳入嵌套工具，排除 model-only/hidden/被排除namespace。
  normalized name 碰撞跳过；exec/wait 注册在前；deferred 定义不全量进入 exec description。
- execute_handler 收原始 JavaScript，parse_exec_source→CodeModeService→session provider
  →runtime service/session actor→每 cell 独立 V8 module。globals 的 tools/ALL_TOOLS 提供
  callable 与 name/description；没有 console/Node/导入能力。delegate 经 ready gate 和
  当前 Step 的 ToolCallRuntime 调度嵌套调用，不绕过通常执行路径，禁止 exec 自调用。
  wait 对 yielded cell 读增量；terminate/abort/shutdown 处理存活 cells。store/load 属
  session 值，不等于长期记忆。多媒体、限额、actor 与远程 host 的全部细分仍须继续读。
- Corki 实际链中 ToolSpec 仅 JSON function；ToolCall 仅 parsed object；Responses 只处理
  function/tool_search 调用并将历史全部写成 function_call。直接加 JS handler 会先被 JSON
  parser 拒绝，或把 custom_tool_call 静默遗漏。CODE_MODE_ONLY 仍没有执行环境。
- 按依赖先实现 raw/freeform 契约，接真实 registry/adapter/graph/SQLite：native Responses
  custom definition/call/output；兼容 provider 使用 {input:string} wrapper，canonical 调用
  保留原始 source，不把源代码当 JSON。JSON/freeform 类型不匹配按执行契约 fatal，畸形
  兼容 wrapper 作为 Observation；调用指纹必须区分 raw source 与 JSON，旧 JSON 指纹不变。
- native freeform 采用独立显式配置/能力声明，不与 native search 或仅 Responses 模式混同；
  native search 的返回定义也必须保留 freeform 类型。新字段兼容旧 history/checkpoint/ledger。
  验收真实 Runtime 原生/兼容/Chat 的定义→调用→结果→下一请求与重开；raw delta/done
  一致性、空串/Unicode、坏 wrapper、调用类型冲突和旧数据幂等。
- 这是 B06 必需的协议前置，不是 Code Mode 已实现。测试 raw handler 不冒充 JavaScript
  执行器；后续仍需实现 cell、嵌套调度、helpers、wait/取消/恢复及条件性曝光。

## 第十六批实施结果：raw/freeform 接入真实主循环（B06 前置，B07/A01/C02）

本轮先重新读取完整 objective，确认上一轮仅输出目标文本属于 no progress，随后核对工作树。
Codex 仍为 `ddf04ad26789d040f9ef6a96736f76602e35a6cc` 且干净；6 个新真实 Runtime
反例全部先在 ToolSpec(input_kind=...) 失败，未将前一轮实施意图当成已有代码。

源码基准补充：`core/src/tools/code_mode/execute_spec.rs::create_code_mode_tool` 注册
Freeform grammar；`core/src/tools/router.rs` 的 CustomToolCall→ToolPayload::Custom，
`core/src/tools/registry.rs:549` matches_kind 失败为 Fatal；`codex-api/src/sse/responses.rs`
的 custom input delta 支持 item_id/call_id，input.done 不授权执行，output_item.done 产生
完整项；`protocol/src/models.rs` 的 CustomToolCallOutput 支持与 function output 相同的
文本/结构化 body，name 可省略。已对照这些边界读取 Corki 的 adapter→LiveTools→graph
executor→ledger→history→下一请求，不以 raw handler fixture 冒充 JavaScript 运行时。

实际实现：

- `protocol/tools.py` 增加 ToolInputKind 与隔离拷贝的 freeform_format。native Responses
  输出 custom definition/原始 input/custom output；兼容 Responses/Chat 使用严格
  `{input:string}` wrapper。canonical 调用 arguments=None、raw_arguments 保留完整原文；
  空串不被替换为 `{}`。grammar 原生由 provider 约束，兼容路径不执行 Lark/regex 校验，
  handler 必须验证自己的语言和输入；目前没有 Code Mode handler。
- `tools.freeform_mode` 默认 compatible，native 为独立显式能力声明，只适用于 Responses；
  settings→Runtime capability→ModelRequest→adapter 全部接入，与 tool search 模式独立。
  native search 的 typed discovered definitions 保留 custom/grammar；native search 配合
  compatible raw 时，从当前请求发现历史识别 wrapper，不要求定义重复出现在顶层 tools。
- `models/freeform.py::CustomCalls` 按 item/call/index 关联流项，只在完整项发布
  ModelItemCompleted；delta 仅计入预算，partial 不合成为可执行调用，重复 done/final
  复用同一 canonical item，已经完成后修改或追加 delta 为协议失败。原始调用能在
  response.completed 之前进入通常调度器，保留既有并行/独占/取消/失败 drain 所有权。
- `ToolExecutor` 先核查 JSON/freeform payload kind，冲突 Fatal；坏兼容 wrapper 返回
  Observation，不运行 handler。ToolResultItem 保留调用类型，缓存结果和取消后未知结果
  也能正确回放 custom output。新 JSON payload 省略默认新字段，旧 schema history 和
  ledger result 重提交不改变；freeform 指纹与 JSON 隔离，并区分原文及 parse_error。
- 新 grammar 预算反例先失败（40k grammar 只计 53 tokens），修复后 request schema、
  discovered history 与 search 完整定义预算均保守计入原生 grammar。兼容 raw 源码的
  换行/引号 JSON 转义也计入输入估算；这仍不是全 provider 精确 token 计费。

行为验证（新增 49 个实例）：

- `test_freeform_tools.py` 6 个真实默认 composition 场景验证原生 Responses、兼容
  Responses、Chat × 中文多行源码/空串，handler 一次、下一请求正确、SQLite 重开相等。
- `test_freeform_discovery.py` 4 个 search/native-freeform 组合验证初始不曝光目标 schema
  →搜索→只加载检索命中→raw handler→Observation→继续；另 6 个坏 wrapper/类型冲突
  验证 Observation 与 Fatal 的实际回合边界和 handler 零执行。
- `test_freeform_events.py` 14 个 adapter 场景覆盖 delta 三种身份、done/final 去重、完成后
  变更、非法完整项、partial+EOF/Completed、正文和 raw 的共享限额及能力未声明拒绝。
- `test_freeform_identity.py` 4 个持久化场景验证改变 raw source/JSON kind/parse_error
  不能复用 claim，旧 JSON 指纹、discovered schema 和 history/ledger 幂等性不变。
- `test_live_tools.py` 增加 7 个 raw 变体：流完成前启动与 ReadError 后 drain 2 个；
  Fatal/取消同时 join 挂起工具与模型 2 个；实际子进程 os._exit 后 unclaimed/running/
  completed 三窗口 3 个。重开 checkpoint 不重采样旧 Step；未知副作用不自动重放，
  完成结果复用，未 claim 的调用才执行，并验证 raw 调用/结果类型和原文。
- `test_search.py` 新增 1 个 grammar 预算反例；`test_freeform_config.py` 7 个非法设置、
  native/Responses 独立门槛、转义成本与 grammar 可变别名隔离实例。

最终 `.venv/bin/pytest --tb=short` **550 passed**（33.14s，exit 0，含组合 e2e）。
ruff check src/tests、ruff format --check src/tests（177 files）、compileall 与 pip check
通过。全目录 format --check 另外报告已有 teach.md 代码块格式差异；未改该文件，且
SHA-256 仍为 `816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a`。
没有运行 Rust、真实 provider 或真实 JavaScript 执行验证。

仍未关闭：B06 执行环境完整缺失，尚无 cell/session lifecycle、tools/ALL_TOOLS、嵌套
通常调度器调用、输出 helpers、yield/wait/terminate/store/load 或执行隔离。当前严格
custom malformed envelope 失败策略与 Codex parser 跳过非法项并不完全一致，流输入
preview 也未实现，继续列在 A/E 协议边界，不宣称所有原生行为已一致。其余 A/B/C/D/E
原始范围保持有效。本轮是实际实现进展，无外部阻塞，goal 保持 active。

下一批必须继续 B06 的 cell/runtime 实现审计与接入：从 code-mode-runtime session actor、
service、callback/timer/store 生命周期读到 delegate 的通常 ToolCallRuntime 嵌套调用，
明确可用引擎和隔离边界；不能停在当前 raw 输入前置，也不能将 Node vm 当作安全沙箱。

## 第十七批实施前决策：Code Mode cell 与通常工具调度（B06/A01/E01）

完整目标再次读取；上轮 550 项及源码/测试交付属于 progress。继续读 Codex
code-mode-runtime 的 service、session_runtime、cell_actor/callbacks、runtime globals/
module_loader/callbacks，和 core code_mode execute_handler/delegate、tools/parallel：
cell 初始 snapshot，completed（含 JS error）提交 store writes，terminate 不提交；
输出每次观察增量消费，yield 后继续运行；只有一个 observer；完成后未观察结果暂存，
观察终态后移除。模块 promise settled 就结束，不让未 await 的工具/timer 延长 cell；
结束需取消并 join 工具，正常结束先 drain notifications。nested tool 经当前 Step
独立 ToolCallRuntime 的并行/独占门，完整执行契约及取消路径，exec 不递归。

Corki 缺少全部 cell/runtime/嵌套桥接；Graph._execute_one 是现成 ledger+executor+events
边界，不能直接 tool.execute 绕过。实现应保留 nested 调用独立 ledger 身份，不把 nested
调用伪装成模型采样输出；yielded 外层结果重放不重新启动失踪 cell，旧 cell id 不能在
新进程意外命中新 cell。会话服务由 Runtime 持有，取消/关闭先回收 cell 再关工具资源。

引擎选择证据：agent-reach 的 Exa/Jina 查询官方包资料
https://pypi.org/project/quickjs-ng/ ，安装并验证 quickjs-ng 0.16.2.1 的 Context.module
确实返回 module promise，顶层 await 经 execute_pending_job 完成。每 cell 使用隔离
Python 子进程内的独立 QuickJS context；不安装 std/os/module loader，不暴露 Node/Python
对象，只允许 JSON 工具桥接；限制内存、输入/输出和 cell 数，父进程可终止 busy JS。
这不是 OS sandbox 或抵抗引擎漏洞的安全承诺，QuickJS 与 V8 的全部语言差异仍需列明。
新增可选 code-mode 依赖，默认 Direct 不依赖引擎；CodeMode 可按配置降 Direct，
CodeModeOnly 缺少引擎时 fail closed。不能以这个选择缩小 cell/嵌套/取消原目标。

先验收真实 Runtime：exec raw ES module→ALL_TOOLS 元数据筛选→nested 通常工具→text
→下一模型请求；yield/wait 增量、store/load、工具 Observation/JS failure、互斥/并发、
不支持 import/外部全局、取消 busy VM 与工具、进程重开 wait missing 不重放副作用。

## 第十七批实施结果：真实 Code Mode cell 已接入，但 B06 仍是部分一致

实现路径为 settings `[tools].mode` → Runtime startup 注册 exec/wait → 模型 raw 调用
→ Graph._execute_one 外层 ledger → CodeModeService/Cell → 独立 worker.py QuickJS context
→ ES module promise/JSON bridge → 当前 Step 的 nested 并发门 → 同一个 Graph._execute_one
及 ToolExecutor/ledger → JS promise → text/notify → 外层结果/下一次模型输入。
没有直接调用 tool.execute 的旁路；nested 调用不伪装成模型采样 item。工具的污染标记、
schema、返回值校验、Fatal/Observation 边界和持久化 claim 都经现成执行路径。

- 默认 Direct 不启动 engine；CodeMode 增加控制工具；CodeModeOnly 只保留控制工具和
  model-only 工具供模型直接调用。Direct/Deferred/CodeModeOnly 可进入 JS tools，
  model-only/hidden 以及 exec/wait/tool_search 不进入嵌套表。ALL_TOOLS 只携带名称/描述，
  deferred schema 不全量进入 exec description。缺少可选 quickjs-ng 包时 CodeMode
  按配置可降 Direct；CodeModeOnly/disable_fallback fail closed。检查放在 owned Runtime
  的异步启动阶段，失败后 model/resources 仍可关闭；控制名称冲突不留下半注册 exec。
- 每 cell 独立进程及真正的 ES module，支持顶层 await，不以 async-function 字符串包装
  偷换 module 语义。未安装 module loader，不暴露 Node、Python对象、console/Atomics/
  SharedArrayBuffer/WebAssembly；JS 外部效果只经已冻结工具表。没有实现完整 OS sandbox，
  也不宣称可抵抗引擎漏洞。optional dependency 固定 quickjs-ng==0.16.2.1；V8 host 仍开放。
- 实现 text、notify、store/load、setTimeout/clearTimeout、yield_control、exit。store
  在 cell 入场时 snapshot，JS 正常/错误完成提交 writes，terminate 不提交；局部变量不跨
  cell。JS module settled 就结束，不让未 await 工具/timer 延长生命周期。正常结束 drain
  notify，再取消/join 工具；最终输出返回前已经 join worker，不只是设置状态文字。
- exec/wait 单 observer、增量输出；主动 yield 和超时 yield 后 cell 继续运行，>=10s 等待
  加 1s grace 对照 service.rs。正常 Turn 结束不会杀死已启动 cell，新的嵌套请求等下一个
  active turn；旧工具的迟到事件通过 CellEventSink 与旧 turn inactive gate 竞争，不能
  堵在无人消费的旧 UI queue。用户取消/失败/Runtime.close 先回收 cells 再关工具与存储。
- 初版把 nested gate 维持到整个 Turn，重读 `session/turn.rs::run_sampling_request`
  后确认 Codex 每 sampling Step 建立新 ToolCallRuntime；现每次 activate 重置 step gate，
  保留 Turn 内 nested 计数。旧 cell 的挂起调用不锁死下一 Step 的 release 工具。同 Step
  并行允许执行顺序不同，独占必须等先前并行完成；测试不再错误要求并行 handler 先后顺序。
- 新 cell 使用 UUID 避免 restart 后命中旧 ID。outer ledger 的 running/yielded 结果不会
  启动第二次 JS；新进程 wait 旧 ID 报 missing，不能自动重放脚本。working store 是进程
  内 session 状态，不是长期记忆。nested update_plan 的状态经外层 ToolResult 回到 Graph。
- 限制每 cell 64 MiB JS heap、32 retained cells、4 MB bridge/store/output、4096 output
  fragments、64 次且总量受限的 notify，以及 nested call 配额。父进程可终止同步 JS
  忙循环；worker stdin reader 在父进程死亡 EOF 时直接退出自己的进程，避免孤儿 CPU
  循环。spawn shield/join 确保取消不会丢掉刚创建的进程句柄。多媒体与精确 token 预算未关闭。

验证新增场景：

- `tests/integration/test_code_mode.py`：18 个真实引擎+Runtime 实例，含 CodeMode/Only ×
  Direct/Deferred/CodeModeOnly 元数据选择并调普通 handler，核查两个真实 ledger 条目；
  yield/wait/store/局部变量隔离；禁止 imports、缺少外部 globals、JS error、exit、未 await
  timer；同步忙循环及挂起工具取消；nested Fatal 不能被 JS catch 变成成功 Turn；同 Step
  并行/独占及未 await 挂起工具必须 join。
- `test_code_mode_lifecycle.py`：7 个实例，含跨 Turn 观察、close/reopen 不重放、原生及
  兼容 Responses→真实 ES module→nested tool→下一 HTTP 请求；真实父进程 os._exit
  后忙循环 worker 消失；JS error store commit/terminate discard/update_plan 回灌；
  旧 Step 已进入的并行 hold 与新 Step exclusive release 不死锁。最后一项先前使用
  0ms yield，无法证明 hold 已进入旧 Step；现用 ready handshake + yield_control 明确
  事件顺序，不以时间猜测 admission，也没有为通过测试放松互斥规则。
- `tests/unit/tools/test_code_mode_config.py`：14 个无 engine 依赖场景，缺失引擎 fallback/
  fail closed 与 model.close、控制名称冲突零部分注册、非法 pragma/安全整数、原文保留。
- `test_code_mode_cells.py`：4 个真实 worker 场景，single observer/cancel/terminate、cell
  配额及 store rollback、超大 IPC frame bounded failure+join、延迟启动与入场 snapshot。

未关闭及下一批：tool-specific typed return（shell/MCP 等不应始终退化成通用文本）、
image/audio/generatedImage helpers 与媒体计费、V8/remote host、模型 tool_mode override、
excluded namespaces 和全部 normalized-name 边界、PendingFrontier 模式、完整 metadata/
runtime trace、跨 cell/Turn 极端失败竞态。nested 调用已持久化但尚无独立 cell checkpoint；
仍是失踪则不重放，而不是恢复 VM。主循环 outer 与 nested 的总预算/恢复计数尚需统一审计，
不能把分别有界当成完整 accounting parity。strict malformed custom SSE 仍与 Codex skip
策略不同。A/B/C/D/E 其余差异全部继续有效，不将 B06 的这条纵向闭环冒充整个目标完成。

最终验证：新增 **43** 个实例（25 Runtime integration、14 配置/pragma、4 engine-cell 边界），
完整 `.venv/bin/pytest --tb=short` **593 passed**（38.60s，exit 0，无 warning）。
ruff check/format src/tests（188 files）、compileall、node --check bootstrap.js、pip check
全部通过。Node 仅用于静态语法检查，不是运行后端；测试实际执行 QuickJS-NG。

发行验证：`pip wheel . --no-deps` 成功，最终 wheel SHA-256
`be583b1c45e261e5920624216946fbc1283702631441f233492e40ffe30de15b`，检查 wheel 包含
bootstrap.js/worker.py 和 code-mode 可选依赖元数据；安装到独立临时目录后，从 `/tmp`
强制 import 该发行包，实际 worker 执行 `text(await Promise.resolve(42))` 返回 completed/42，
cell 注册表为空且服务关闭。没有只用源码树导入掩盖打包资源遗漏。

Codex 工作树仍干净，参考 commit 不变；teach.md SHA-256 不变。没有执行 Rust、真实模型、
Linux/Windows 跨平台或安全渗透验证。agent-reach 用于核查官方引擎文档，未以第三方介绍
替代 Codex 调用链证据。此轮为实际实现进展，无外部阻塞，完整 goal 继续 active；下一批
优先补 B06 的 tool-specific structured results 与 media/helpers，再继续完整 A/B/C/D/E。

## 第十八批实施前审计：Code Mode typed result 与 MCP metadata 边界

Codex `tools/src/tool_output.rs::ToolOutput::code_mode_result` 默认返回文本/媒体 URL
拼接，但 `JsonToolOutput` 覆盖为原始 JSON 值（包括 null/primitive），MCP
`CallToolResult` 覆盖为 content/structuredContent/isError 并删除顶层 `_meta`；
content block 内 `_meta` 保留。`core/src/tools/context.rs::ExecCommandToolOutput`
覆盖为 output/wall_time_seconds/可选 exit_code、session_id 等结构化字段。
诊断 log_output 不是 authoritative result。Code Mode nested dispatcher 调用这一契约，
不是对模型可见文本任意 JSON.parse。

Corki `CodeModeService.invoke` 当前把 Graph 返回的 ToolResultItem.content 和 attachment
URLs 拼成字符串；shell `_result` 只保留格式化文本；MCPTool 丢掉 typed 结果。MCP 空内容
fallback 更会把顶层 `_meta` 序列化进模型文本。状态为行为不一致，B06/E 高优先级：
脚本无法可靠使用 result.output/structuredContent，私有 client metadata 可泄漏。

方案：ToolResult 增加显式可选 CodeModeOutput(value)，区分无覆盖和 JSON null；执行器
严格 JSON 校验、深复制、有界拒绝而不截断改变 JSON 含义。ledger 保留覆盖，旧 payload
缺省不新增 key，保护已完成结果不可变比较。Graph nested 分支在相同 claim/execute/
complete/events 路径后返回 authoritative ToolResult，普通路径继续创建 ToolResultItem；
typed value 不重复注入模型历史或 token 预算。shell 从 ProcessObservation 构建；MCP
仅开放协议 content、非 null structuredContent/isError，私有顶层 metadata 不进入文本、
JS 或 ledger，内容块 metadata 保留。shell session_id 继续使用 Corki 的真实 string ID，
不伪造 Codex int ID；chunk_id/original_token_count 缺少原始观测，不能编造。

验收先用真实 Runtime + worker + shell/MCP fixture 暴露当前字符串退化及 metadata
泄漏，再验证 null/primitive、非法 JSON/超大结果 Observation、handler 后续修改隔离、
ledger reopen/不可变结果/旧 payload、普通文本不被自动 JSON.parse。媒体 helper、媒体
顺序/计费、完整 A/B/C/D/E 仍开放；不能把 typed JSON 通路当成媒体输出已经实现。

## 第十八批实施结果：shell/MCP authoritative JSON 值已接入

真实链路：Responses 原生 custom/兼容 function 或 ScriptedModel → exec → worker module
→ tools promise → CodeModeService.invoke → Graph._execute_one(nested_spec) → 通常 executor
校验/深复制 → complete_tool_call → 返回 ToolResult → JSON bridge → JS 读取字段 → text
→ 外层普通 ToolResultItem → 下一模型请求。普通调用仍返回历史 projection；通过 overload
明确 nested 与普通返回类型，没有绕开 ledger 或把 nested output 伪装成额外模型消息。

1. `protocol/tools.py::CodeModeOutput` 显式区分 absent 和 null；不猜测文本是否 JSON。
   executor 校验仅 JSON 类型、string keys、有限数值/合法 Unicode，拒绝 cycle、>64 层、
   >100 万节点及 >32 MB UTF-8 JSON；JSON round-trip 深复制，handler 后续修改不能改结果。
   遍历节点上限也限制共享 Python 子图的指数展开，不只在序列化结束后才检查长度。
   这些为 Corki 明确的本地防护限制，并非宣称数值与 Codex 相同。
2. ledger JSON 可选保存 code_mode_output.value；缺省不新增 key，旧完成记录仍字节兼容。
   reopen 后 claim 返回同一个结构化结果（包括 null），更改值无法覆盖已完成结果。
   普通模型历史不携带这一额外字段，不重复增加 schema/history token 占用。
3. shell exec_command/write_stdin 从 ProcessObservation 返回 output/wall_time_seconds、
   存在时才有 exit_code/session_id；非零 exit_code 保留，运行中不伪造完成字段。实际 string
   session_id 可直接传给 write_stdin。Codex chunk_id/original_token_count 仍未补，保留差异。
4. MCP 保留 content、存在且非 null 的 structuredContent/isError；只从协议公开字段构建
   返回值，顶层 _meta 和未知 transport 字段不进入 JS/文本/ledger。content block 中 _meta
   不做递归删除。空内容 fallback 的私有信息泄漏已修复；非法 content/isError 类型返回
   Observation，不把字符串 "false" 等当合法协议。MCP isError=true 仍为可供脚本检查的
   正常结果对象，不无故把工具错误变成 JS exception。
5. cell bridge 仍使用 4 MB ASCII JSON frame 上限，比 durable typed value 限制小；已完成
   但 frame 超限的嵌套调用持久化结果、reject JS promise，不截断 JSON，也不自动重放。
   通用文本无附件时原样返回；有附件时只拼接非空片段，与 Codex fallback 分支对应。
   exec 的模型说明新增 tool-specific return 约定，不只在 README 说明字段。

行为映射：已逐段阅读 Codex `core/tests/suite/code_mode.rs` 的
`code_mode_can_return_exec_command_output`、`code_mode_can_print_structured_mcp_tool_result_fields`、
`code_mode_can_print_content_only_mcp_tool_result_fields`、`code_mode_can_print_error_mcp_tool_result_fields`；
并确认 `core/src/tools/code_mode/mod.rs` nested 调通常 ToolCallRuntime 后调用 code_mode_result。
未运行 Rust 测试，以下为 Corki 的离线确定性映射，不是真实模型选择质量验证。

修复前新增 6 个真实 Runtime 场景全部 RED：shell typeof 为 string、字段 undefined；
MCP 结构化字段丢失，空内容暴露 PRIVATE。修复后对应全部 GREEN。此批总计新增 41 个实例：

- `tests/integration/test_code_mode_results.py` 17 个：两种 mode 的 shell/MCP（含空内容、
  block metadata、isError）、7 种 JSON 值、JSON-looking 普通文本、真实 oversized bridge
  rejection 且 handler 只执行一次、真实 shell running→write_stdin→完成、非法 typed result
  Observation 经 JS 回到模型。核查 nested/outer ledger、无额外 nested history、cell 清空。
- `tests/integration/test_code_mode_lifecycle.py` 扩增 2 个：HTTP mock Responses 原生/兼容
  →实际 JS→typed handler→JS 取 answer→下一 HTTP 请求；保留原有 2 个普通文本场景。
- `tests/unit/tools/test_code_mode_output.py` 22 个：JSON 值保存/reopen/完成不可变、非法值、
  循环/错误 wrapper、UTF-8 byte 超限、共享子图节点上限、深复制、旧 ledger 精确兼容、
  direct MCP 私有字段隔离/延迟修改、非法 MCP envelope。

完整测试 **634 passed**（最终复跑 42.96s，exit 0，无 warning）；ruff check/format src/tests
（190 files）、compileall、pip check、node --check 均通过。Codex commit 未变、工作树干净；
teach.md SHA-256 仍为 816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a。
本批未重做 wheel 安装验证（未新增运行时资源/依赖）、未执行真实模型、Rust 或跨平台验证。

仍未关闭：其他 builtin JsonToolOutput 对应契约、shell chunk/token 原始观测、image/audio/
generatedImage helpers、按序媒体内容/provider capability/context 成本、V8/remote host、
mode override、namespace/exclusions、PendingFrontier、统一 nested/outer 配额与恢复计数。
本批没有媒体实现，不把保留 MCP 图片内容块描述为图片已到达模型。A/B/C/D/E 其余原目标
继续有效，无外部阻塞，goal 保持 active；下一批先补 Code Mode 媒体端到端路径。

## 第十九批实施前审计：有序工具内容与 Code Mode 媒体输出

Codex runtime/value.rs 与 callbacks.rs：image 接受 data URL、image_url 对象或 MCP image
block，detail override 优先于对象/合法 block metadata；不接受远程 URL。audio 对应 data
URL/audio_url/MCP audio block；runtime/audio.rs 按实际 WAV data chunk 测量，<25ms 改成
明确 omission 文本。generatedImage 先校验 output_hint，再按 image 归一化，依序 emit image
和可选 hint。不是把所有文本先合并、再把附件放在末尾。

core/tools/code_mode/mod.rs::handle_runtime_response 保留 content items 顺序，追加 JS error，
先 truncate 再插入 status/wall time header。utils/output-truncation/src/lib.rs 的混合输出
共享文本/音频预算，图片不受该工具文本预算截掉（图片仍占完整上下文成本）；音频超预算
整项省略，保留说明。utils/audio/src/lib.rs 使用解码时长×10 token/s，未知格式回退 URL
字节估算。全目标中的精确 tokenizer/音频 codec 处理仍需另行核对，不因当前无库而豁免。

Corki bootstrap 只有 text；Cell.output 是 string list，observe 返回拼接字符串；协议仅
content+独立 image attachments，provider 总是把图片放到文字后，无法保存交错顺序，更无
audio、tiny WAV、共享 audio budget。状态缺失/行为不一致，B06/C/E 高优先级。

方案：新增有序 TextContent/ImageAttachment/AudioAttachment union，ToolResult 和历史
ToolResultItem 可选 content_items；旧序列化缺省不新增 key。既有 content 留作文本诊断，
有序字段为权威模型内容，不与旧 attachments 同时使用。executor 统一校验/预算，ledger、
Graph、checkpoint/history、context estimator 和两 provider 全链路保留顺序。
CodeMode Cell 返回结构化 observation，不通过字符串 subclass 或全局 side channel 传媒体。
JS helpers 同步校验 URL/参数，parent 接收有序事件并做 tiny WAV suppression/输出预算；
yield/wait 增量消费且终止/error 保留已发出的媒体。

Responses 使用原生有序 input_text/input_image/input_audio；Chat 的 tool message 不承载
媒体时使用关联 call ID 的后续 user content 数组，保留数组内部顺序，明确这是兼容差异。
音频支持由 provider.supports_audio_input 显式配置；缺省不假定任意模型能听音频，不支持
时用明确 omission 文本代替、保留原始 durable 内容供支持能力的恢复路径使用。Chat WAV/MP3
有明确 input_audio 转换，其他格式不能无声丢失。image original detail 的模型能力推导与
完整压缩音频解码仍保留审计缺口，不能宣称仅以 wire fixture 证明真实模型可用。

先写真实 Runtime worker/helper RED，再验证 provider 输出顺序、预算、tiny WAV、坏参数、
yield/wait/terminate、MCP block→helper、reopen/旧 payload、上下文/压缩保留和资源释放。

实施中新增恢复证据：真实 JsonPlusSerializer round-trip 先验收失败，ToolResultItem 的旧
attachments 从 tuple 变 list；默认 serializer 还产生未注册 Corki 类型的日志警告，strict
模式会拒绝。当前 Runtime 使用 AsyncSqliteSaver 默认 serializer；此前 turn 完成后重开
只证明 SQLite canonical history，不足以证明 strict checkpoint 恢复。补充修复计划：
为 CorkiState 实际涉及的 protocol dataclass/enum 建立显式有限白名单并接入 owned saver，
不开放任意 module/pickle；规范化历史附件、plan、memory citation 的 tuple 字段。新增
serializer 精确 round-trip 和 strict 模式真实 Runtime/恢复回归，而不是只在测试关闭限制。

## 第十九批实施结果：有序媒体/helper 与 checkpoint 恢复闭环

实现链路：bootstrap.js image/audio/generatedImage → ordered content IPC → Cell（tiny WAV、
增量 buffer/预算）→ CellObservation → CodeModeExecTool/WaitTool → 同一个 ToolExecutor
→ ToolResult.content_items / ledger → Graph ToolResultItem.content_items → 原生/兼容 Responses
或 Chat 内容转换；同时进入 context estimator/压缩采样与 canonical/checkpoint 恢复。

- TextContent / ImageAttachment / AudioAttachment 为有序 union；content 只是无 data URL
  的诊断 projection，provider 和 context 使用权威 content_items。旧字段缺省不写新 key，
  attachments 与有序字段不能同时发布。executor 验证类型/Unicode/URL/detail、8192 项与
  32 MB media 上限；普通工具施加有序预算，Code Mode 已按 pragma 处理的输出不重复截断，
  因而不会因通用 40000 字符预算把 status header 或较高合法 max_output_tokens 再切掉。
- helpers 接受 data URL / URL object / matching MCP block；不读取文件或网络。image
  detail 显式 override 优先，block metadata 仅合法值生效；generatedImage 校验 hint
  后按 image→hint 顺序发送。同步参数错误可 catch；helpers 返回 undefined，不再把
  Python emit 回调的 null 当 JS 返回值。未捕获错误保留已输出媒体且 is_error=true；
  terminate 为受控终止，未伪装成 JS failure。
- CellObservation 是显式结构化返回，不依赖 str subclass 或 side channel。文本/媒体
  共用有界 buffer，媒体不截成损坏 data URL；yield/wait 只消费新项；error/terminate
  仍 join owned worker/工具。status/wall time header 在预算之后插入。有混合媒体时图片
  不占工具输出预算但仍占 context；文本/音频按顺序共享预算，音频不足整项省略并报告；
  纯文本保留 Codex formatted truncation 的分支/原大小提示（具体 token/截断算法仍为
  Corki 保守估算，不能称与 Codex 逐 token 一致）。
- protocol/audio.py 有界解析实际 RIFF/WAVE fmt/data chunk，含 streaming oversized
  length、奇数 padding、PCM/float/extensible subtype。<25ms 改 omission text；已知 WAV
  duration×10 token/s，未知/压缩格式目前回退 URL 大小，没有把未知媒体当零成本。
- Responses 在 custom/function output 内保留 input_text/image/audio 顺序；Chat 为工具
 结果加 call-labelled 后续 user content 数组（与原生 tool output 不同的兼容投影）。
  provider.supports_audio_input 缺省 false，显式 boolean 配置启用；禁用时明确 omission，
  原始媒体仍 durable。Chat 支持 base64 WAV/MP3，其他格式明确 omission，不假装已听到。
- 图状态现在使用 core/checkpoint.py 的有限 protocol type 白名单，无 pickle 或任意 module
  开放；接入 owned AsyncSqliteSaver 的 serde 和 metadata serializer。历史 attachments、
  plan、memory citation 的 tuple 在解码后规范化；修复前精确 round-trip 的 attachments
  []/() 差异已复现。旧字段 JSON 表达保持兼容；未知类型不被构造。不能由此推断任意被
  篡改 checkpoint 都是有效 CorkiState，完整 corruption validation 仍属 E 后续核查。

测试证据（相对 634 基线新增 55 个实例）：

- `tests/integration/test_code_mode_media.py` 26 个：三个 helper 的真实 worker RED→GREEN；
  detail/MCP image+audio forwarding、hint 顺序、tiny WAV 24/25ms、7 个 catchable 参数错误、
  prior image+uncaught error failure；3 provider transport × audio capability 的 HTTP wire；
  undefined return/零预算 image+audio；yield→wait/terminate 增量、后台 handler join；完整
  Turn reopen 无重跑；媒体成本触发真实 compact→当前输入保留→原始媒体留档；已执行
  exec 后阻塞下一模型采样，取消底层 graph invocation，再从真实 SQLite graph checkpoint
  resume_pending，媒体仍可见、exec call 只有一个（不是仅测 JSON 序列化）。
- `tests/unit/tools/test_ordered_content.py` 29 个：actual WAV/streaming 8 边界、extensible/
  odd padding、whole audio budget/image 保留、pure-text truncation 提示、context 不重复计费、
  history+ledger 重开及不可覆盖、有序结果非法项 Observation；capability TOML/boolean、
  Chat MP3 与格式 omission；typed checkpoint round-trip/旧集合字段/未知类型不构造。
- 原 CodeMode cell 测试改为检查结构化 observation.content；原 Responses 测试检查有序
  text item 的正文，不再假设 wire output 必为字符串。原 yield 测试曾以 substring
  "second" 排除下一段，现会误匹配新增 wall time 的 "seconds"；已改为精确断言 emitted
  content_items（不含 header），没有放宽增量消费条件。

已逐段阅读 Codex service_audio_tests 的 all-input-forms 与 bounds/yield 场景；尚未执行
Rust 或真实模型。HTTP mock 证明 Harness/wire 合同，不证明媒体可被真实模型解码或选择。
新增恢复检查中，strict 全套回归 686 passed 后又新增 3 个场景，最终结果另记下方。

未关闭：Codex `code_mode_replaces_malformed_image` 明确要求将坏图片改为 processing omission，
Corki 当前仅验证 URL 形状，尚缺最终媒体 decode/resize/preparation，original detail 的模型
能力推导也未对齐；完整 mp3/m4a/webm/ogg 等时长解析/转码仍缺。下一批优先沿 Codex 的
最终请求媒体 preparation 补这一真差异，不把 helper 可调用当作媒体对齐完成。
V8/remote host、mode override、namespaces/exclusions、PendingFrontier、统一 nested/outer
预算与恢复计数、其余工具 typed result，以及完整 A/B/C/D/E 未关闭项均保留，goal active。

第十九批最终验证：`LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest --tb=short`
**689 passed**（43.57s，exit 0）；其中真实 media checkpoint 中断恢复与全部原有 recovery/
live-tools/retry 故障窗口均在这次 strict 全套运行内。ruff check/format src/tests
（197 files）、compileall、pip check、node --check 全部通过。未知 checkpoint 类型的阻止
日志是专门测试的预期证据，不是把许可模式警告静默掉。

最终 wheel 构建成功并安装到独立临时目录，SHA-256
`46b6cf4fbd1e219b68c2972ed70b410d5a24f3643bdd1fa8bc7ae682597f5a54`。
从 `/tmp` 以 Python isolated mode 强制导入安装包，检查新 runtime 资源在 wheel 内；
真实 worker 执行 text→image→audio→generatedImage→text，验证有序类型、无 error、
cell 已 join/清空。该 smoke 验证打包和实际引擎，不声称测试 fixture 的媒体字节被解码。
验证包位于 `/tmp/corki-media-wheel.WXBbao/verified/corki-0.1.0-py3-none-any.whl`。

Codex 工作树干净，commit 仍为 ddf04ad26789d040f9ef6a96736f76602e35a6cc；teach.md
SHA-256 仍为 816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a。
未运行 Rust、真实模型、跨平台、安全渗透验证。此轮为实际实现进展，无外部阻塞，
完整 goal 保持 active；下一轮按上述媒体 preparation 真差异继续，不能将此批局部闭环
替代全目标的最终验收。

## 第二十批实施前审计：图片 preparation 的真实边界

Codex `session/mod.rs::prepare_conversation_items_for_history` 在新历史入场时调用
image_preparation::prepare_response_items，再处理 audio；apply_rollout_reconstruction 对旧
rollout 构造的 history 重做 DetailBased preparation，不改旧 rollout，关闭 resize notices。
`image_preparation.rs` 保留 content 顺序，失败改同位置文本；http(s) 有专用 omission；
data URL 仅 base64，按真实字节猜格式，不相信 MIME。默认 DetailBased：auto/high 使用
2048 边长且 2500 个 32px patches；original 使用 6000 边长、10000 patches；low 被拒绝。
UnifiedImageBudget 为默认关闭的实验功能，且仅在 original-capable/Responses Lite 模型
生效：使用 original budget 并设置 original hint。ImageResizeNotice 也默认关闭。

`utils/image/src/lib.rs` 全链已读：PNG/JPEG/WebP 不需缩放时原字节保留；GIF 转 PNG；
缩放保持 PNG/JPEG/WebP 编码种类，JPEG quality85、WebP lossless；RGB ICC/EXIF 保留，
非 RGB ICC 不复制。缓存32项/64MiB、按字节摘要+模式，单大结果不缓存。Rust image cargo
此模块仅启用 jpeg/png/gif/webp，不能因 Python 可打开 EPS 等格式就调用外部解码程序。

`view_image.rs` 先解码验证，拒绝非图片且不让文件正文进入 Code Mode；CodeModeOutput
返回 image_url/detail 对象（统一预算时省略 detail）。本轮对应 Corki 仍按文件扩展 MIME
判断，完整 read_bytes 后才限大小；既不验证解码，也没有该 typed override，属于真实 B/E
缺口。Corki 现有 media pipeline 只校验 URL/顺序；多数上轮 fixture 是坏图，尚不能证明
实际图片进入请求。此批新测试必须使用真编码图片，坏图专门验 omission，不改测试来保留旧错行为。

方案：共用 image preparation 服务接入 Runtime 工具结果入 ledger/history、context 准备
与压缩前估算、旧历史/重试/最终 model request 投影。既有持久化历史只读投影，不原地改行。
UserMessageItem 补有序内容字段以保持坏图替换位置而不改写用户正文；旧缺省 payload 兼容。
Pillow 明确作为依赖，限制纯内存 PNG/JPEG/GIF/WebP 解码；CPU/文件读取用 owned thread
任务并在取消时 join。有效图按同一 patch math 缩放/缓存，错误只给稳定 omission、不回显
原 bytes。view_image 有界读取/完整验证，原始有效图以 typed 值给 JS，集中路径处理模型
可见图片。provider 原图/图片能力、unified budget 显式配置；不凭模型名字猜能力。

验收：真实编码 PNG/JPEG/WebP/GIF、patch 极端长宽比/不放大/元数据/缓存、坏格式/MIME/
remote/low/error不泄漏，真实 Runtime→HTTP 请求与 ledger、旧数据库投影不改行、重试与
压缩成本、取消处理中无后台遗留。ResizeNotice 与完整音频 preparation 若未实现仍明确
开放；不能把默认图片处理的修复外推为所有实验 media 路径完成。

补充追踪：`context_manager/history.rs::estimate_token_count_with_base_instructions` 按
history items 估算，`normalize::strip_images_when_unsupported` 在请求视图再删图；因此
text-only 模型仍按持久历史图片保守估算，并非本批要偷偷优化掉的差异。Corki 保留这个顺序。
另发现 `compact.rs::build_compacted_history_with_limit` 只把过去用户消息的文字重新注入，
而 Corki `_retained_user_messages` 曾复制整个带图 item，截短 content 后 content_items 仍可
覆盖截短结果。修复需将过去用户消息先投影为纯文本再限额；当前输入仍按原保护规则完整保留。

## 第二十批实施结果：图片 preparation 与真实像素闭环

`media/images.py::ImagePreparation` 为 Runtime 所有，注入 ToolExecutor、ContextWindowManager
和 Graph。工具 normalized result 在 ledger 完成前准备；window 在历史读取/当前输入/估算
前准备；最终 model request（含 retry/checkpoint 路径）及压缩请求再次投影，重复准备可缓存。
Runtime 的 realtime flush 与 finally 保存 pending input 采用相同准备，以免同一 immutable
item id 因原图/准备图 payload 不同而触发冲突。UserMessageItem 新增可选 content_items，
不改变用户 content 正文，provider/token 使用有序字段；旧缺省 payload 不增加 key。

- 仅 PNG/JPEG/GIF/WebP 按真实字节识别并完整 load；高/原图边长和patch数学、无放大、
  小PNG/JPEG/WebP原字节保留、GIF→PNG、JPEG85/WebP lossless、RGB ICC/EXIF保留已实现。
  错误就地替换 omission，不把原始字节塞入提示。provider图片/original/unified为显式bool；
  unified仅original-capable模型生效；缺省original降high，默认low omission。
- 32项/64MiB内容摘要+模式缓存，过大单项不缓存；CPU受semaphore限制，owned线程在
  一次/重复取消后join再传播取消。view_image有界读取、完整验证后才向JS返回原dataURL；
  直接输出只有image，typed值包含image_url/detail，unified时省detail。扩展名不决定有效性。
- 过去用户消息压缩先投影纯文字再算预算，修复旧content_items覆盖截短content的问题。
  当前输入仍受完整保护；text-only仅改请求视图，不清除历史有效图片，估算仍按历史保守计费。

测试证据（相对689基线新增45实例）：

- `tests/integration/test_image_preparation.py` 11例：Direct/CodeMode/Only坏图3例先RED后
  GREEN；真实view_image × Direct/Only × native Responses/compatible Responses/Chat
  共6例：2048²原图→1600²模型wire，typed ledger仍为原字节，JS拿到原URL长度/detail，
  调用只完成一次；旧user/tool SQLite历史在图片模型与text-only的2例投影，顺序/正文
  不变，数据库原对象保持完整相等。
- `tests/unit/tools/test_image_processing.py` 34例：6维度边界、3小图字节保留、4编码缩放、
  4错误omission、4能力gate、ICC/EXIF、LRU、CPU重复取消join、4view_image typed分支、
  4坏图/残缺像素/大小/模型拒绝、2过去用户媒体不重注入与截断生效（后2例先RED）。
- 上批code_mode_media 26例中的伪图fixture改有效PNG，MCP low断言改history omission；
  unit builtin/demo也用真图。旧1px样本有损坏像素，完整解码失败证明旧测试不足，不据此
  关闭解码校验。全量包含真实checkpoint恢复、媒体压缩/current input、yield/wait/terminate。

最终 `LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest --tb=short`：**734 passed**，47.27s，
exit0。ruff check/format --check（src/tests+改动的builtin示例，202 files）、compileall、
pip check、node --check均通过。不把单元取消覆盖扩大为所有媒体资源窗口均已验证。

wheel SHA-256 `34d6e487c8207a43b16cb61e43cf21fe749acc62f3d11e0d986387971515a136`，
位于 `/tmp/corki-image-wheel.na4XZl/corki-0.1.0-py3-none-any.whl`。安装到同目录installed后，
从`/tmp`以`python -I`强制导入安装包，检查Pillow依赖元数据，真实Runtime/QuickJS worker
调用view_image→image(r)，模型端实际解码得到1600²像素，Turn完成、cell为空、Runtime关闭。
该验证是scripted model，不证明真实模型视觉理解或工具选择质量。

仍有差异：Pillow与Rust Triangle/JPEG/WebP不保证逐像素/逐字节一致；Corki另限6400万
解码像素，缓存按dataURL而非Rust原编码字节计，更保守；view_image保留20MiB读取限制。
Preparation的1GiB输入限额还受工具32MB和CodeMode4MB桥约束，不声称1GiB图全部可用。
模型能力配置并非完整ModelInfo发现，view_image能力驱动schema隐藏/历史参数兼容待审计。
ImageResizeNotice、媒体provenance/tracing、完整音频codec/preparation、其余工具typed、
V8/remote host、mode override、namespaces/exclusions、PendingFrontier、nested/outer统一
预算，以及全部A/B/C/D/E未关闭项保留。集中服务保证Runtime路径，不声称直接调用裸provider
adapter也带媒体准备。未执行Rust、真实模型、跨平台测试。

Codex commit及clean状态未变；teach.md SHA-256仍为
816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a。
本批无外部阻塞，goal保持active；下一批从工具spec/handler能力边界及最终audio preparation
继续追踪，再完成原目标的其余核心审计，不能宣布整体对齐完成。

## 第二十一批实施前审计：音频 preparation 不是音频转码

完整读取 `utils/audio/src/lib.rs` 和163行测试，追到session/mod.rs新历史3269与旧rollout
1574：均在图片处理后调用audio preparation。这个阶段只接受data URL、检查MIME别名、
base64与50MiB decoded限制，规范化为wav/mpeg/mp4/webm/ogg；不要求内容能被codec解码。
测试明确使用base64("audio")仍保留为音频，不能拿Pillow的图像解码逻辑照搬到音频上。
非法输入/不支持格式/超限有三种不同的稳定omission，保留tool success与内容位置。
`estimate_audio_token_count` 才用Symphonia探测默认音轨timebase/duration，32项摘要key
缓存，10 tokens/s；无法探测按URL大小回退。CodeMode tiny WAV又是独立的实际数据时长
解析（runtime/audio.rs完整已读），并不以容器duration替代短片判断。

Corki当前只有CodeMode tiny WAV与PCM时长估算；Runtime会原样保存并发送错误base64、
不支持格式与非规范别名。provider只在末端处理audio capability，ScriptedModel请求看不到
统一模型能力投影。属于B06/C/E真实缺口，先实现独立AudioPreparation与图片服务组合，
接入现有Runtime全部媒体边界，历史不回写，typed nested payload仍原始。音频不支持时
仅请求投影替换成Codex稳定提示；原媒体仍持久化。CPU用已有owned-thread模式处理。

本批验收：Codex四个测试场景与所有MIME别名/大小边界，真实Direct/CodeMode/Only回灌、
三HTTP协议canonical音频、旧user/tool媒体恢复不回写、取消join和metadata不丢失。
压缩音频duration探测与缓存仍是下一独立缺口；此前记录“完整音频codec/preparation”过于
含混，今后区分规范化、时长探测、能力过滤，不能把转码当作Codex默认要求。
同时已完整读view_image_spec.rs：detail只在original-capable且非unified时曝光，但handler
兼容旧detail参数。该schema/validation双契约尚待实现，不用全局放宽JSON验证来绕过。

## 第二十一批实施结果：音频规范化、能力投影与恢复

Runtime持有`media/preparation.py::MediaPreparation`，组合ImagePreparation与audio.py。
Graph/Window/Executor的注入统一命名为media_preparation，新历史/工具ledger前、旧历史、
pending/realtime、压缩和最终模型请求均复用此服务；次序image→audio，CPU通过有界gate和
owned线程执行，取消join后传播。新audio规范化不改变typed nested原值、call身份或is_error。

audio.py覆盖全部11个MIME别名及大小写，输出5个canonical MIME；检查data URL、base64、
encoded/decoded 50MiB限制，分别返回processing/format/size omission。额外验证canonical
base64，避免Python validate=True仍接受非零尾bits或额外padding，与Rust STANDARD不同。
合法base64但不可解码的音频与空base64均保持音频：这才是Codex preparation的可观察行为。
不支持音频时仅模型投影替换，模型无关的ScriptedModel也看到同一能力过滤；provider裸调用
仍有同文案fallback。旧数据库行/typed工具值不回写。

新增40测试实例，相对734基线：

- `tests/integration/test_audio_preparation.py`11例：3输入（alias/坏base64/unsupported）
  ×Direct/CodeMode/Only共9例先RED后GREEN，顺序和成功语义保留；2个旧user/tool媒体
  恢复能力分支，canonical或unsupported projection、原用户正文/is_error/数据库内容不变。
- `tests/unit/tools/test_audio_processing.py`29例：11 MIME/case、12 processing/format错误
  （含padding/trailing bits/非ASCII/remote）、3 encoded与decoded大小边界、空base64、
  metadata/typed/user payload roundtrip、重复取消join。大小边界缩小常量后分别验证两层
  检查，不分配50MiB的重复大fixture。
- 原code_mode_media的三HTTP协议×audio capability六例改用audio/vnd.wave输入，验证
  provider收到规范audio/wav；helper-only的ScriptedModel显式启用音频能力。
  真实SQLite graph checkpoint中断恢复现同时携带image和audio，验证恢复媒体完整且exec
  不重跑，而非只测dataclass序列化。既有tiny WAV/预算/压缩及故障窗口仍在全量测试内。

全量strict验证：`LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest --tb=short` → **774 passed**，
46.69s。ruff check、format --check（206 files）、compileall、pip check、node --check通过。
wheel `/tmp/corki-audio-wheel.klgg4G/corki-0.1.0-py3-none-any.whl`，SHA-256
`2377756e9d8adead6522df728911de095a9207a30694d0ccb6d85875379b4d85`；独立installed目录、
从`/tmp`使用`python -I`确认Runtime/MediaPreparation来自安装包，运行三种模式真实工具/
QuickJS以及两个历史能力分支，全部通过。该smoke重用测试函数，但执行的是安装包核心代码。
未执行Rust、真实模型、跨平台或压缩音频时长探测验证。

仍未关闭：Symphonia等价的压缩音频容器duration探测/32项缓存、view_image能力驱动schema
与旧参数兼容、图像resize notices/metadata、B06其他已列缺口，以及A/B/C/D/E原目标。
“音频必须转码”不是已读Codex默认路径要求，不作为虚构缺口；不因此宣称媒体总体一致。
先前B06表中的“完整音频codec”此后特指尚缺的duration probe，不再涵盖已完成的规范化。

Codex仍为固定commit且工作树干净，teach.md摘要未变。本批属于有代码和全链测试的进展，
goal保持active。接下来优先回到用户最初关心的B02检索：已重新追踪tool_search.rs的
registry/cache→English BM25→top-k→coalesce输出，核对pinned bm25 tokenizer和实际排序，
避免让外围媒体改进取代工具召回与其余主循环/context/memory核心差异的完成。

## 第二十二批实施前审计：pinned BM25评分与缓存发布

本地Cargo.lock固定bm25 2.3.2，依赖源码不在本地缓存。使用agent-reach网页/Jina路径读取
官方docs.rs，然后下载crates.io原包到`/tmp/corki-bm25-source.Zh6lz9`；SHA-256与lock完全
一致：1cbd8ffdfb7b4c2ff038726178a780a94f90525ed0ad264c0afaa75dd8c18a64。
完整读取default_tokenizer.rs、embedder的构建/embedding、scorer的索引/评分、search的
构建/search链；Codex tool_search.rs生产路径与cache/namespace测试、tools/tool_search.rs
默认metadata全链也已读。没有安装Rust toolchain，尚未直接运行Rust差分程序。

确认：默认k1=1.2/b=.75；平均长度按f64除法再转f32；文档token embedding缓存tf归一
权重，IDF用含词文档数。query embedding.indices保留重复词及顺序，逐项f32累加，不去重。
倒排表只访问含查询词的候选。Codex同分顺序来自HashSet遍历，不承诺固定注册顺序。
Corki却k1=1.5、query先set再sort、所有文档全扫；可构造无分词歧义的ASCII语料改变top1。
且现index先赋self.specs再分词：构建异常后可能把旧documents与新specs搭配，下一次跳过
构建。Codex cache先完整构建handler再发布，不能保留这种半更新状态。

先关闭这组独立评分/缓存缺口：文档权重与倒排索引、f32运算、query multiplicity、构建后
单一snapshot发布、复制输入spec防外部嵌套字典修改污染缓存。测试先复现top1变化和构建
失败后的重复调用，真实Runtime验证搜索→只加载命中工具→调用→Observation。
分词仍须下一阶段闭合：当前Unicode regex不同于deunicode1.6.2→lowercase→Unicode word
boundary→English stopwords→rust-stemmers1.2.0；u32 fxhash碰撞语义也未实现。不得把本批
评分修复描述为B02完整一致；精确tie因上游不确定，Corki保留稳定注册顺序并明确说明。

另沿tools/tool_search.rs确认两个metadata分支：from_spec接收的显式空search_text不会
回退默认文本，Freeform默认metadata含format.syntax而非grammar.definition。Corki的
`spec.search_text or ...`与统一JSON-schema路径都不符合；本批一并加入空索引和grammar
syntax召回测试，不能用工具名称偶然命中掩盖这些分支。

## 第二十二批实施结果：评分、metadata与失败缓存修复

新增`tools/bm25.py`作为实际ToolSearchIndex评分器：k1=1.2/b=.75、先按文档算tf权重与IDF，
倒排只访问相关候选，query重复词按原顺序累加，主要算术边界显式转f32。同分使用稳定注册
顺序，不伪称匹配Rust HashSet的不确定顺序。MIT许可随`tools/BM25_LICENSE.txt`打包，
THIRD_PARTY_NOTICES记录来源；依照[固定版本官方源码文档](https://docs.rs/bm25/2.3.2/bm25/)
与已校验归档实现，而不是更换成另一套BM25参数。

ToolSearchIndex先deepcopy输入、完整构建scorer后再发布单个_IndexSnapshot；构建失败保留
旧generation，返回spec也复制，避免嵌套schema别名污染缓存。依旧比较完整spec值触发失效，
不把此机制称为Codex的Weak handler identity/source listing策略已全部实现。
显式空search_text不回退；freeform默认只把syntax加入metadata，不把grammar.definition
当搜索文档。正常JSON工具仍保留递归description/property/items/anyOf/source路径。

新增17测试，相对774基线：

- `tests/unit/tools/test_bm25_alignment.py`10例：k1造成真实top1差异、query重复方向2例、
  构建异常不发布、输入schema污染、上游Search示例的两个golden分数（显式已分词输入，
  误差1e-7）、1001文档只访问相关候选、返回schema隔离、显式空文本、freeform syntax。
  其中6个实例修复前失败；其他为边界和来源golden验证，不冒充全是先RED。
- `tests/integration/test_search_ranking.py`7例：3个排名场景×原生/兼容计划，初始隐藏
  deferred→只返回top1定义→模型显式调用该命中→Observation→Turn完成；另1例注入
  连续两次构建故障，两次均返回error Observation且generation0，消除故障后重建generation1
  才能加载调用，执行一次。原生测试验证Runtime计划，HTTP wire由原有实际mock用例回归；
  不把ScriptedModel结果描述为真实模型的工具选择质量。

最终strict全量：**791 passed**，49.19s，命令
`LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest --tb=short`。ruff check/format（209 files，
src/tests+改动的builtin示例）、compileall、pip check、node --check通过。
wheel `/tmp/corki-ranking-wheel.R03g9R/corki-0.1.0-py3-none-any.whl`，SHA-256
`b37de1abee32bf18040d1083f027289ae4fa948bd7cf5aa23c145d751830ffe7`；独立installed目录、
从/tmp的python -I确认评分器来自安装包，两个检索模式与重复失败→重建Runtime场景通过，
确认MIT许可资源可从安装包读取。Codex工作树仍干净，teach.md摘要未变。

本批使用agent-reach取得固定版本依赖文档，未运行Rust（无toolchain）或真实模型；源代码
对照与golden样本不证明所有跨平台libm/f32位级一致。后续分词依赖已锁定：deunicode1.6.2、
rust-stemmers1.2.0、stop-words0.9.0、unicode-segmentation1.12.0、fxhash0.2.1。
先继续关闭这些实际召回差异，再处理B05动态MCP与其余A/B/C/D/E；全目标保持active，
本批不是完整工具召回或整个Harness的完成声明。

## 第二十三批实施前审计：实际English tokenizer与term identity

沿固定bm25 2.3.2的DefaultTokenizer::_tokenize确认顺序：deunicode1.6.2的
deunicode_with_tofu_cow(text,"[?]") → lowercase → unicode-segmentation1.12.0的
unicode_words → stop-words0.9.0 → rust-stemmers1.2.0 English。重要配置更正：
bm25/Cargo.toml:95显式features=["nltk"]且default-features=false；实际English词表
是src/nltk/english的179项，不是该crate默认ISO的1298项。build.rs先插入NLTK，即使
ISO因feature合并也不会覆盖英文。会测试ISO独有词仍可召回，不能只验证常见the/and。

固定依赖归档已下载至/tmp/corki-tokenizer-source.OJOlSg并逐一核对Codex Cargo.lock
SHA-256；完整读取normalizer生产路径、NLTK build.rs/英文表、词边界ASCII状态/分类、
fxhash32的write32/hash_word。deunicode数据保证输出ASCII，故Unicode word算法在这条
链上只会遇到ASCII类别；需保留underscore、内部单引号/小数点/字母间冒号、数字间
逗号/分号，而不能继续用原先剔除所有标点的正则。未知Unicode映射为[?]；不能用另一
套转写库或NFKD近似代替emoji/CJK及空格修整。

拟随包提供锁定deunicode的pointers.bin/mapping.txt无损压缩数据及NLTK英文表、完整
来源许可；normalizer/scanner接入现有ToolSearchIndex。词干采用固定snowballstemmer
2.2.0的纯Python EnglishStemmer（不走可选PyStemmer自动替换）：已对rust-stemmers
发布包test_data的29417个英文词形逐一比对，0差异。该外部golden语料不复制进仓库；
这不是执行Rust或穷尽所有输入的证明。旧regex会使词形、重音字母、emoji查询漏召回。

BM25Embedder还使用fxhash0.2.1的hash32作为term identity；拟复现native-endian u32
分块、rotate5/xor/wrapping multiply及Rust str Hash的独立0xff终结写入，核对上游
公开embedding样例，避免字符串identity掩盖32bit碰撞语义。评分器已支持Hashable。

先为旧_tokens及真实Runtime双检索模式添加RED回归，随后验证完整pipeline、数据摘要、
空/停用词查询、边界、源码golden hash、碰撞和并发词干隔离。完成后仍须追踪B02缓存/
来源列表、B05动态MCP及其余A/B/C/D/E缺口；本批不定义新的整体完成条件。

## 第二十三批实施结果：分词与词项身份进入真实工具召回

新增`tools/tokenizer.py`并替换ToolSearchIndex的文档/查询两侧输入。顺序固定为
deunicode1.6.2 → lower → unicode-segmentation1.12.0在ASCII输出上的可达状态 →
stop-words0.9.0的NLTK English179 → snowballstemmer==2.2.0 EnglishStemmer →
fxhash0.2.1 u32身份。Unicode不是直接丢弃：CJK/emoji/重音字母均使用原包数据转写。
保留原normalizer的ASCII前缀快路径、空映射、未知[?]及单字符前瞻空格处理；保留
underscore/内部引号/小数点等word边界。词干实例每次调用独占，避免生成器可变cursor
跨线程共享；直接导入纯Python类，不受可选PyStemmer影响。不增加语言自动检测或
语义向量检索，也不将本批表述为中文语义搜索。

无损压缩的`tokenizer_data.json`包括原始pointers419994字节、mapping56405字节和NLTK
179词；首次加载校验三个摘要后以不可变数据缓存，数据损坏报错而不静默换分词器。
`TOKENIZER_LICENSES.txt`/THIRD_PARTY_NOTICES保留固定来源许可，wheel含资源和许可。
NLTK分支来自bm25显式关闭default features并开启nltk；修正了本批早先口头提到的
ISO1298表，未将ISO表写进实现。u32碰撞只影响召回term identity，不作为工具调用身份。
未新增数据库字段；已有历史与checkpoint仍通过全量恢复/压缩测试。

新增57个测试实例（相对791基线）：

- `tests/unit/tools/test_search_tokenizer.py`45例：8组pipeline、停用词/边界误命中、16组
  deunicode源码例子与特殊前瞻、6组ASCII词边界、并发词干/异常词、7个上游embedding
  ID、真实index碰撞、原始数据摘要/词表分支、3种损坏数据不发布index或静默fallback。
- `tests/integration/test_search_ranking.py`新增12例：词形、café、emoji、3.14、北京→
  bei jing、停用词无结果后再查cobalt，均覆盖native/compatible Runtime计划→搜索结果
  →定义可用→只执行命中工具→Observation→完成。旧排名与重复构建故障用例保留。
  第一次运行在修改实现前复现17例失败（9单元、8Runtime）；其余为边界和新验证，不
  冒充全部先RED。ScriptedModel只证明Harness链路，不证明真实模型选择质量。

保存可复现、只读外部来源校验脚本`harness-alignment/verify-tokenizer-sources.py`。
命令：`.venv/bin/python harness-alignment/verify-tokenizer-sources.py
/tmp/corki-tokenizer-source.OJOlSg`。脚本先核对五个crate归档SHA再直接读取tar成员：
476399字节转写数据逐字节相同，NLTK179相同；rust-stemmers1.2.0的29417词形golden
全部相同；unicode-segmentation1.12.0的TEST_WORD中477个ASCII案例全部相同。非ASCII
原始词边界样本不在可达子集中，不能先转写它们再沿用旧预期。外部词干语料不随仓库
分发。没有Rust toolchain；这里不是执行Rust，也不是所有输入/平台的穷尽证明。

最终strict全量：**848 passed，54.04s**，命令
`LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest --tb=short`，exit0。ruff check与
format --check（src/tests+验证脚本+builtin_tools_demo，212 files）、compileall、
pip check、node --check全部通过。首次wheel尝试--no-build-isolation因本地未安装
hatchling失败，恢复标准隔离构建后成功；未把失败构建当作交付。

wheel `/tmp/corki-tokenizer-wheel.0lqi4c/corki-0.1.0-py3-none-any.whl`，SHA-256
`fb679d3c17a00dfcd2a8412804ef08b3334b55450a16d5ed60acd4a4dff404a0`。
安装Corki和固定snowballstemmer到独立installed目录，从/tmp用python -I确认二者实际
从安装包加载、依赖metadata含精确pin、许可可读；14个双模式Runtime场景通过，来源
校验脚本也对安装包再跑通过。不是依赖源码工作树资源的假安装成功。

本批使用agent-reach取得固定依赖源码文档；normalizer参考
[deunicode1.6.2官方文档](https://docs.rs/deunicode/1.6.2/deunicode/)及校验过的crate。
Codex仍固定ddf04ad26789d040f9ef6a96736f76602e35a6cc且工作树干净；teach.md摘要仍为
816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a。

B02已确认的tokenizer及u32身份缺口关闭；不推导为全工具系统一致。B02缓存仍按完整
spec值比较而非immutable handler Weak identity/dynamic search info/source listing；
tool_search描述目前构造时固定，后续来源变化的更新还需处理。已重新读上游cache
get_or_build与sources_match准备下一批。B05运行中MCP动态更新/重连、B06其他未关闭
项、A/C/D/E全范围继续有效。big-endian、直接Rust差分、真实模型验证尚未执行。
本批是局部行为闭合与全链进展，全goal保持active，不宣称Harness整体对齐完成。

## 第二十四批实施前审计：运行中MCP刷新与发布所有权

固定Codex源码链：session/handlers.rs::refresh_mcp_servers置reconnect_pending并请求失效；
session/mcp_refresh.rs用单gate与claim/Drop guard保证取消恢复pending；session/mcp.rs::
refresh_mcp_if_dirty解析最新desired state并循环发布；codex-mcp/src/runtime.rs::replace/
publish以ArcSwap替换当前connections/config，旧binding继续持有精确客户端。
session/mcp_runtime.rs::prepare_mcp_call先刷新再捕获当前binding，调用开始后不重绑。
core/tests/suite/mcp_refresh_cleanup.rs明确验证旧进程在in-flight调用期间存活，调用取消后
旧进程退出而新进程仍活；session/tests.rs::cancelled_mcp_refresh_remains_pending验证取消
后下一次刷新仍能执行。已读这些生产函数和测试，不沿用旧说明中的过期manager文件路径。

另查rmcp-client/logging_client_handler.rs，on_tool_list_changed仅记录日志；不虚构
Codex会自动响应该通知更新全部工具。Corki本批对齐显式刷新/配置更新与Step准备路径，
不引入自动重试tools/call、轮询工具目录或执行未知副作用。hosted Apps/auth/environment
专属投影继续单独审计。

Corki当前Manager只支持一次start，registry.seal后不能改变任何工具；搜索说明也在启动
时固定。即使server恢复或目录更新，也只能重启Runtime，无法移除失效schema/来源。
Graph准备过程中多次读取registry，单纯放开register会产生本Step不一致。优先级P1。

拟实现：保留普通注册在seal后禁止；composition预先创建受控owner，owner只能原子替换
自己名下handler+schema，不能覆盖builtin/plugin或其他owner。Manager以串行gate消费
显式dirty，先完成新连接/discovery/验证，再一次发布；取消或全局发布失败保留旧状态和
pending并关闭临时连接；可选server单点失败产出warning，不阻止其他server。被替换的
连接等待已准入调用释放，关闭由manager持有，shutdown取消/回收所有剩余调用与关闭任务。

Runtime提供request_mcp_refresh并在真实prepare节点刷新；该Step的工具计划、历史失效
投影与压缩schema预算使用一次捕获的spec快照。搜索入口由Runtime owner更新/增删，
来源改变不再停留在旧description。先测试运行中显式刷新、取消恢复、失败隔离、空目录
增加/删除、跨Step旧schema失效以及in-flight不重放不提前关闭，再做完整严格回归。
本批先闭合动态注册所依赖的生命周期；B02 immutable identity/source-description/world
state omit策略仍需后续按已读源码完成，不把名字更新描述为完整来源策略一致。

## 第二十四批实施结果：显式MCP刷新、调用准入与连接回收

`ToolRegistry`保留seal对普通register/unregister的限制；composition创建的owner仅可
替换自己名下工具。`replace_owned`先捕获并deepcopy所有spec、检测重名/跨owner冲突，
再以单个_PublishedRegistry绑定发布handler+spec；失败不留下半套工具。seal后清空旧
composition map，避免它永久持有已替换handler。此owner是程序内发布约束，不是新的
用户审批或OS安全边界；受信Python插件的权限未被扩张。

`MCPManager`已有串行refresh gate、显式pending、最新完整server settings、先暂存再发布
与取消/失败恢复pending。构造/start/list单服务器错误隔离成有界warning，其他server
仍能发布；全局发布异常保留旧registry与连接，关闭临时连接并允许重试。每次显式请求
重建连接，支持重连、新增、替换、空集合移除。普通CLI新增`/mcp refresh`；嵌入Runtime
新增`request_mcp_refresh(servers=None)`，传tuple表示完整目标配置，不是持久化修改磁盘
config。无自动tools/call重试，无tools/list_changed热更新或目录轮询。

实施中进一步核对并修正了“只在下一Step刷新”的初版安排：Codex session/mcp_runtime.rs::
prepare_mcp_call会先消费dirty再捕获当前binding，不能让尚未开始的调用沿用已失效连接。
现MCPTool经manager.call_tool路由，调用准入先刷新并确认原始remote name仍在当前目录；
移除的server/tool返回Observation，不回退到旧客户端。资源/模板/prompt入口同样刷新，
跨服务器列表先同步取得全部lease，再执行网络await，避免半次聚合跨越两代连接。
测试已区分：待准入调用走新client；已准入调用始终持有旧client并只执行一次。

`MCPConnection`独占client与调用lease；被替换后禁止新lease，现有lease释放才发起关闭。
退休连接及关闭任务由manager持有；shutdown会取消/等待所有剩余调用，再关闭新旧连接；
取消一个aclose等待者不会取消其拥有的shutdown。stdio仍沿用现有terminate回收策略，
测试验收的是调用结束后旧进程退出，不虚构退出码必须为0。

真实Graph prepare先刷新，再捕获一次完整spec tuple，预算/压缩resolver、请求工具计划、
历史定义失效投影均使用该tuple；不在await之间多次读取不同代注册表。Runtime owner
增删/更新tool_search入口和当前source名称，沿用已有BM25 index，其spec值改变触发已有
重建机制。raw工具搜索结果/业务历史不覆写，只对模型视图过滤已失效定义。无新增DB字段。

新增21例（848→869）：

- `tests/unit/mcp/test_refresh.py`10例：旧in-flight客户端保留、刷新取消恢复pending、
  全局发布失败回滚、start/构造单点失败2例、刷新中的新请求与串行waiter、shutdown取消
  旧调用/回收两代连接、取消close等待者、跨server资源lease、待准入调用主动消费dirty。
  前两例在旧Manager上实际RED；其他是实现后的边界验证，不冒充全部先RED。
- `tests/unit/tools/test_registry_publication.py`4例：handler/schema一起发布、嵌套隔离、
  跨owner/普通工具冲突失败原子性、schema捕获失败保留原代、旧handler不被composition
  map额外持有（其中相关断言合并在同一测试）。原有sealed普通注册回归保留。
- `tests/integration/test_mcp_refresh_runtime.py`4例：native/compatible × 已有server/
  首Turn空目录，HTTP initialize/list/call/delete经真实MCP adapter与实际Runtime执行；
  搜索旧source→请求替换→尚未准入的旧server调用错误→下一Step移除旧定义/更新来源
  →再搜新工具→调用新schema→删除全部server→模型视图清空，raw历史保留。空目录分支
  还覆盖同Thread跨Turn新增服务器。ScriptedModel不证明真实模型选择质量；这里的native
  是Runtime检索计划，provider wire由原有mock Responses测试覆盖。
- `tests/integration/test_mcp_refresh_process.py`1例：真实stdio进程、阻塞中的tools/call、
  新PID连接发布、旧PID调用结束后退出、新PID仍活、最终所有reader/stderr任务退出。
- CLI parser/application 2例：命令进入Runtime而不是作为模型用户输入。

最终 `LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest --tb=short`：**869 passed，52.74s**。
另单独复跑 `tests/e2e/test_examples.py`：3 passed，包含project/plan/shell/patch/memory、
长进程/图片、skills/plugin/stdio MCP按需搜索组合。ruff check及format --check覆盖
src/tests/examples/main.py与来源验证脚本，222 files；compileall、pip check、node --check
通过。无真实模型或Rust运行。

本批尚不宣称B05全部关闭：Codex的bounded-channel后台prewarm与auth/environment自动
失效、常规配置投影中的未变连接复用、hosted Apps目录revision与完整surface policy
仍需继续。当前显式刷新和关键prepare/调用准入正确性路径已接入；不是用这些剩余差异
缩小原目标。一般handler仍在dispatch时核对当前spec，与Codex完整冻结router的边界仍
见A04。CodeMode pending cell与后台刷新组合也需进一步验证。

B02仍缺immutable handler identity/dynamic search info/source listing cache精确策略，
source description/name预算保留和DeferredToolWorldState控制的Omit路径；本批只修复
来源名称不随Runtime变化的问题。A/B/C/D/E原全范围不变，goal继续active。

最终wheel `/tmp/corki-refresh-final-wheel.2aHpPU/corki-0.1.0-py3-none-any.whl`，SHA-256
`6f1b428e23a83ec1d56322b920589a691f63912f985ad56df73c93dbb220e69d`。从/tmp用python -I
载入独立installed目录，确认Runtime、MCP manager与registry实际来自安装包；4个HTTP
Runtime场景、1个真实stdio场景、2个调用准入/旧连接所有权场景全部通过。没有把源码
工作树导入当作安装验证。Codex仍为固定commit且工作树干净，teach.md摘要未变。

## 第二十五批实施前审计：MCP来源说明与搜索入口预算（B02/B05/C01）

本批重新核对Codex commit ddf04ad26789d040f9ef6a96736f76602e35a6cc，工作树干净。
`codex-mcp/src/rmcp_client.rs::regular_mcp_tool_info_from_listed_tool`把initialize的
server instructions赋给namespace_description，并剥离普通MCP冒充connector的元数据。
`core/src/tools/handlers/mcp.rs::build_mcp_search_text/search_info`将完整namespace
description用于搜索及source说明；普通MCP的callable_name与raw name相同，但两者都
参与搜索文本，不能去重而改变BM25词频。MCP索引仅包含顶层schema属性名，不是一般
function的递归schema描述；之前继续加递归MCP描述的设想不符合源码，本批不实施。

`tool_search_spec.rs::create_tool_search_tool`默认Include来源，按名称排序去重，首个
非None说明胜出；聚合来源列表有512 KiB UTF-8上限，先预留全部名称，再分配说明预算，
不会把整段逗号列表截成半个名称。`mcp_search_tests.rs`和search spec内测试覆盖完整
metadata、多字节边界及聚合说明。实验DeferredToolWorldState默认关闭，开启时Omit
来源、另经ToolsState输出4 KiB有界增量namespace fragment，不是简单删掉来源。

Corki当前initialize丢弃instructions；MCPTool缺少source_description，搜索少了
namespace说明和第二次raw/callable名称；搜索入口只有来源名称且截断4,000字符。
影响：仅在服务器说明中描述的能力无法召回，长来源列表丢失后部工具入口。P1。
当前全spec值缓存可以在metadata加入后失效，但尚非Codex的immutable弱identity/
dynamic search-info缓存；完整缓存策略及world-state实验路径仍单独待闭合。

本批拟先修复可验证的默认普通MCP链路：initialize类型校验并保留说明→manager传入
MCPTool→完整搜索metadata与来源说明→有界来源列表→实际Runtime搜索/调用/刷新。
新增可选持久化字段保留旧payload无字段时的字节兼容；来源信息不提升为system规则。
验收先运行回归观察RED，再实现并复跑native/compatible真实Runtime、说明单独变化的
刷新和历史回读，以及UTF-8预算、来源去重、普通MCP不信任connector元数据。
不将该阶段描述为B02、namespace wire、插件/hosted Apps或C01整体完成。

## 第二十五批实施结果：默认普通MCP来源说明真实接入

`MCPClient.initialize`保留可选server_instructions并验证字符串/null类型，非法值在
发布initialized状态前失败；`MCPManager`将说明传入每个MCPTool。adapter按上游顺序
索引canonical名称、callable名称、raw名称、server名称、非空title/description、完整
instructions及排序顶层属性名；普通MCP不读取tool._meta中的connector自报身份。
来源名称/说明做trim及空值规范化，不把工具description伪装成namespace说明。

`ToolSpec.source_description`是末尾可选字段，不破坏位置参数；历史序列化仅在非None
时写入，JSON/freeform旧payload形状均保持，旧历史读取有默认值。来源说明用于搜索
入口的metadata而非新增system指令。`tools/search_sources.py`实现默认Include来源
排序去重、首个非None说明、512 KiB UTF-8聚合上限、名称预留及逐字符边界截断。超长
名称整项跳过后仍尝试后续项；说明中的多行内容按上游保留，不自行扁平化。完整请求
预算仍计算实际搜索工具description，512 KiB不是新增的context额度。

说明变化经过已有Runtime prepare刷新重新生成搜索入口；全spec值比较让索引重建，
历史发现定义的模型视图失效，raw结果保留原source_description。本批未将缓存误称为
已符合immutable identity策略，也未只添加一个没有world-state配套的Omit开关。

新增19例（869→888）：
- `unit/tools/test_search_sources.py`10例：去重/排序/首说明、8来源长emoji预算、超长
  名称跳过且保留后续名称、MCP完整metadata/双名称词频/不信任connector_meta、3种空
  instructions、JSON/freeform旧payload兼容、来源说明计入完整请求预算。
- `unit/mcp/test_source_instructions.py`7例：null/空/多行Unicode字符串与4种非法类型；
  经真实HttpMCPClient MockTransport初始化→Manager发布，非法server不发送initialized
  或tools/list，关闭client并产生warning，健康server仍可用。
- `integration/test_mcp_source_discovery.py`2例：native/compatible真实六Step Runtime，
  仅initialize.instructions含查询词；搜索→加载→调用→仅说明变化的重连→旧发现视图
  失效→新词搜索→再次调用→完成；从SQLite回读两代原始发现记录。原4个目录刷新测试
  按源码来源列表格式更新断言，未移除目录/连接生命周期验收。

首轮新增10例确实RED（包括2个真实Runtime场景），实施后通过；其余9例是新增边界
验证，不声称都先观察过失败。最终`LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest
--tb=short`：**888 passed，53.85s**。ruff check/format --check覆盖src/tests/examples/
main.py及tokenizer来源验证脚本，226 files；compileall、pip check、node --check通过。
单独e2e examples 3 passed，3.04s。没有真实模型或Rust执行，不证明模型选择质量。

剩余B02缓存问题已再次核到具体路径：Codex get_or_build将不可变MCP runtime以Weak
identity作key，dynamic以完整ToolSearchInfo作key，并将source_listing纳入；handler
持有冻结search_infos和engine。Corki仍每次spec值比较、索引延迟构建、execute读取当下
registry，不能因为metadata回归通过就称缓存或完整Step router冻结已完成。后续还需
明确处理缓存不保留旧handler、原子发布失败、相同schema但不同runtime、以及模型请求
到工具执行之间刷新等场景。DeferredToolWorldState需从实际namespace map生成4 KiB
增量fragment；普通source名不是namespace的可靠替代。以上及A/B/C/D/E原范围均保留。

最终wheel `/tmp/corki-source-metadata-wheel.hfIdFF/corki-0.1.0-py3-none-any.whl`，
SHA-256 `a232b82db8ffe3713cf3652e03a66e6cf843ee1ad84e7b2d61c0fdfb1461b571`。
最初no-build-isolation因当前venv没有hatchling失败，随后标准隔离构建成功，未改变
运行venv依赖。安装到独立installed目录，从/tmp使用python -I执行本批19例和已有4个
HTTP刷新场景：23 passed，1.49s；检查全部122个已加载corki模块均来自该安装目录，
不是源码树回退。Codex工作树仍干净；teach.md SHA-256仍为
`816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a`。Goal保持active。

## 第二十六批实施前审计：搜索handler缓存与冻结语料（B02/A04）

重读Codex `core/src/tools/handlers/tool_search.rs::get_or_build/new`及两个cache测试：
不可变runtime用Weak身份、动态runtime用ToolSearchInfo值、并按registry顺序匹配；
新handler在发布前完成BM25构建，旧handler持有原搜索信息，不在execute读取新registry。
CoreToolRuntime::immutable_spec约定spec和search metadata均不可变，McpHandler显式实现。
相同schema但不同MCP runtime必须重建；相同动态搜索信息的新runtime可以复用。

Corki目前ToolSearchTool持有整个registry并在execute重新取deferred specs，Runtime
只根据公开tool_search说明是否相同决定替换，且共享可变index。旧入口的来源说明可能与
执行时的新语料不一致；cache也间接保留整个registry。索引第一次搜索才构建，与参考的
prepare阶段完整发布不同。P1，不能仅将full-spec相等描述为identity缓存已对齐。

拟新增真实Runtime使用的搜索缓存：immutable工具弱引用identity、dynamic的搜索文本+
可加载wire定义+来源信息值；保存独立冻结语料和完整索引，失败不发布半代。不变MCP
cache hit不重读schema；显式immutable契约以隔离的MCP spec支撑。Python ToolSpec另含
并发/结果预算等非搜索执行信息，这些变化可以复用索引，但必须重新绑定当前返回定义，
不能把旧执行参数藏进搜索结果。默认Include先接入，实验source-listing/Omit需与真实
world-state路径一起完成，不能先加空开关。

已有Runtime“构建错误返回Observation”和resume用generation=0推断未执行的测试必须
按源码语义校正：prepare构建错误不进入模型/搜索执行，查询阶段错误仍应Observation；
恢复时重新准备索引并不等于重放durable search，应直接监测execute次数。保留原单元
构建失败原子性验收并增加Runtime初始化失败可重试证据。

本批同时验证MCP调用准入刷新后，同一模型Step尚未执行的旧搜索handler仍用旧语料；
下一prepare才更换。完整通用router冻结、外部强制调用_refresh_tools造成search handler
本身被替换的执行边界及恢复前跨进程handler identity，仍需后续A04处理，不提前声称完成。

## 第二十六批实施结果：默认搜索缓存与语料冻结

新增`tools/search_cache.py::ToolSearchHandlerCache`并接入Runtime._sync_tool_search。
缓存按deferred注册顺序比较：显式immutable工具用弱identity（不用handler.__eq__），
动态工具用搜索文本、loadable wire定义、来源名称/说明值；名称、schema、input kind、
描述、显式空search_text、顺序和曝光移出均影响相应key。default Include来源由这些
metadata生成；不实现没有world-state配套的Omit开关。

`ToolRegistry.deferred_entries`从已发布的spec曝光与handler映射取得同代有序条目，
不复制全部大schema。MCPTool声明immutable_search_metadata，并deepcopy导出的spec，
确保嵌套参数修改不能破坏该声明。不可变cache hit不读取registry.spec。普通自定义工具
默认动态；不支持weakref的slots工具使用值缓存，不强迫插件修改对象布局。缓存及旧
搜索handler只保留metadata/索引，不持有来源handler或registry，weakref/GC测试已验证。

`ToolSearchTool`保存冻结定义，不再在execute读取当前registry。索引在构造完成之前
完整prepare，查询只rank原语料。每个缓存miss得到独立index，不再由新handler修改旧
handler的共享index。Python ToolSpec的并发/输出预算不属于搜索key：仅这些字段变化
时使用新结果绑定复用同一engine，返回新执行定义；旧handler仍返回原定义。该wrapper
差异服务于Corki的协议字段分层，不是把执行字段变化误判为无需更新。

失败语义与准备/执行阶段分开：构建失败不发布cache或搜索入口，初始化允许重试；运行
中的prepare构建失败在下一采样前令Turn失败，不把旧index配新metadata继续发给模型。
查询分词故障仍经ToolExecutor转Observation。原重复构建Observation测试已改为查询
故障；构建失败覆盖由原unit原子snapshot测试、本批cache双次失败和Runtime准备失败
接替。Durable resume不执行已记录搜索，以execute失败spy直接验收，不再用generation0
间接推断；恢复时允许重建新的进程内index。

新增21例（888→909）：
- `unit/tools/test_search_snapshot.py`1例：旧搜索handler在owner替换后仍能搜旧语料。
  本例在修复前实际RED（返回空结果），修复后GREEN。
- `unit/tools/test_search_cache.py`14例：不可变identity reuse且不重读schema/不持有旧
  handler和registry；同值动态实例复用、执行字段重新绑定；7类动态metadata失效；
  双次构建失败保留前代且可重试；顺序/曝光/空目录；自定义__eq__不误命中；slots回退；
  MCP嵌套schema导出隔离。其余新用例为实现后的边界测试，不冒称都先RED。
- `integration/test_search_cache_runtime.py`6例：native/compatible × metadata变化/
  完全相同，真实Runtime同Step先MCP资源调用消费pending，再旧搜索handler查原语料；
  下一prepare新实例即重建、后续Step复用；新查询→加载→MCP执行。另有初始化两次失败
  后重试、运行中刷新索引失败终止下一sample并允许下一Turn恢复2例。测试构造中曾给
  同响应两次ToolCall不同step_id，被现有协议校验正确拒绝；修复fixture后通过，未放松
  production校验。

最终严格全量：`LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest --tb=short`，
**909 passed，55.81s**。此前908 passed 54.63s是增加最后故障窗口用例前的中间结果。
ruff check/format --check覆盖src/tests/examples/main.py与来源验证脚本，230 files；
compileall、pip check、node --check通过。3个组合e2e examples单独复跑通过，2.74s。
没有真实模型或Rust运行；ScriptedModel只证明Harness路径，不证明模型选择质量。

本批未闭合完整A04：如果外部在Step准备后强制发布新的search handler，通用executor
仍从当前registry选handler；本批解决的是冻结handler自身的语料与正常MCP准入刷新链路，
不是全部路由身份快照。source-listing实验开关/ToolsState与namespace wire仍需一起完成。
主循环、context、memory与错误处理的原目标仍完整保留，不能用本批全绿缩小完成条件。

另一个后续需核齐的边界：discovery.py仍以完整ToolSpec值判断旧发现定义是否可用，
仅并发/输出预算改变也会让旧结果失效。本批保证新查询返回当前执行字段并复用索引，
不代表“已发现定义的执行元数据更新”也已完成等价对齐；应与A04的冻结路由/恢复一起
核对，不能不加区分地放宽所有schema变化检查。

最终wheel `/tmp/corki-search-cache-wheel.VerFpq/corki-0.1.0-py3-none-any.whl`，SHA-256
`87d5c7ae12e5d805b10adcd24c085aeb769cae61c3fbeb46e5ed7793dcdecf14`。从/tmp使用python -I
加载独立installed目录，运行本批缓存/快照/Runtime测试、排名、durable resume、native
wire及两组来源/目录刷新测试：52 passed，4.61s；验证全部123个已加载corki模块均来自
安装包。Codex工作树干净，teach.md SHA-256保持
`816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a`，Goal继续active。

## 第二十七批实施前审计：进程内Step router快照（A04/B02）

Codex `session/step_context.rs`的tool_router是采样前固定的Arc；turn.rs请求使用其
model_visible_specs，tools/parallel.rs::handle_tool_call_with_source从同一router取
parallel属性和runtime并clone到调用future。router.rs拥有registry，dispatch不会从
Session的最新registry重新选择同名handler。MCP handler仍经prepare_mcp_call解析最新
连接，不能把冻结router误解为禁止MCP准入刷新。

Code Mode delegate.rs::start_turn_worker为当前Step建立ToolCallRuntime，execute_handler
从invocation的Step router生成可用工具表。跨Step旧cell的broker分发由当前worker处理；
不预设所有旧cell的未来调用必须永久绑定创建cell时的router。

Corki现有request_tools/dispatch_tools只保存schema；ToolExecutor执行时从全局registry
取handler，因此相同schema换实例会无声换执行对象，schema变化则拒绝本该继续使用旧
Step handler的调用。search handler同样受影响。拟增加只存活于GraphRunContext的单代
router holder，checkpoint仅保存关联id，不序列化handler/连接；prepare/retry捕获同代
spec+handler，live与普通执行显式使用该snapshot。旧任务自己持有精确snapshot；新Step
替换holder后允许旧代释放。Code Mode本Step的新cell目录和nested调度使用同一snapshot。

恢复时若进程内snapshot不存在，只能重建当前router，并继续用checkpoint的dispatch
定义校验及ledger防重放；不存在的旧实例不可虚构恢复。需覆盖旧checkpoint兼容、同值
实例替换、改schema/移除、搜索入口刷新、流式/非流式工具、取消和Code Mode。旧发现定义
对非模型执行字段的full-spec失效策略仍单独保留，不在本批盲目放宽schema校验。

## 第二十七批实施结果：进程内Step工具路由固定

`ToolRegistrySnapshot`持有已发布的不可变mapping代，读接口导出spec副本，handler身份
保持精确；未seal的单元/组合调用也捕获隔离spec。`GraphRunContext.tools`中的
StepToolState只保存最新binding，prepare在任何context构建await前捕获并生成随机id；
checkpoint的新增可选字段tool_snapshot_id只有字符串，不序列化handler/客户端。live
任务和本Step nested closure独立持有snapshot，不随holder下一次bind被换掉。普通执行
同样从holder解析本Step快照；executor默认无snapshot时仍保留原当前spec校验行为。

prepare的request/dispatch/历史视图/预算resolver源于同一快照，采样retry刷新后重新
捕获一代并更新id。正常模型工具执行及legacy request_tools fallback不再读取最新
registry决定handler或schema。显式替换同名同schema对象、修改schema、隐藏或移除，
都不改变已经准备的Step；下一prepare才使用新对象。此行为不是OS权限撤销机制，
普通Python工具的内部可变状态并未被冻结。MCP handler仍经Manager在准入时解析最新
连接，旧lease处理保持，没有将连接刷新错误地锁死在Step创建时。

Code Mode activate获得本Step snapshot，新cell的nested_specs从该快照产生，当前worker
的nested dispatch也使用同一快照。另核spec_plan.rs::build_code_mode_tools与
execute_spec.rs：exec说明必须随Step工具表更新；因此新增Runtime受控CodeMode owner，
原子更新exec/wait控制工具，避免执行表已更新而模型看到旧直接工具参数。原reserved名
校验保留，初始化时才可接管同service的调用方控制工具；运行中普通registry仍sealed。

冷恢复/旧checkpoint找不到进程内对象时，StepToolState重建当前snapshot，并继续用
保存dispatch定义校验；变化的定义拒绝执行，已完成call由ledger提供结果。不虚构跨
进程恢复旧Python对象identity；相同定义的当前handler属于恢复绑定，而非旧对象存活。

新增30例（909→939）：
- `integration/test_step_tool_snapshot.py`21例：16个普通工具场景（模型阶段/prepare
  await阶段 × 流式/完整响应 × 同值实例/schema变化/隐藏/移除），4个真实QuickJS
  CodeMode场景（同值/schema × 流式/完整响应），1个采样retry换代后再冻结场景。
  CodeMode还验证下一Step exec描述确实换成新schema。首8个模型阶段普通场景修复前
  实际RED，表现为误用new-handler或原工具不执行；不是仅观察名字相等。
- `integration/test_step_search_refresh.py`4例：native/compatible × 流式/完整响应，
  在模型中强制刷新并替换tool_search handler；该Step仍搜旧语料，raw历史记录旧定义，
  下一Step旧发现视图失效→搜索新工具→真实执行新handler。
- `unit/tools/test_step_registry_snapshot.py`5例：snapshot持有精确handler并隔离导出
  schema，独立运行引用释放后GC；默认/指定snapshot分发差异；旧/新checkpoint key冷
  绑定拒绝changed spec两例；空snapshot不回退到新注册工具。

最终 `LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest --tb=short`：**939 passed，56.09s**。
921 passed 55.32s是扩充准备await/搜索/恢复边界之前的中间结果。ruff check与format
--check覆盖src/tests/examples/main.py及tokenizer来源验证脚本，234 files；compileall、
pip check、node --check通过。retry fixture曾使用不合法的0退避，被既有settings校验
拒绝；修为0.001后通过，没有放松生产配置校验。未执行真实模型或Rust。

剩余项保持明确：全ToolSpec比较导致已发现工具仅执行字段变化也失效，尚未调整；跨
Step旧cell保留的旧definition与当前worker的定义变化如何处理仍待源级核齐。冷恢复
对象身份不可能跨进程保留，需继续按实际Codex恢复链审计，而非用进程内测试声称全部
恢复对齐。realtime输入单独持久化时的window准备与retry完整token预算路径也应继续
审计；本批不将handler快照扩张描述为完整配置/环境/context快照已完成。原A/B/C/D/E
全范围及未关闭项保留，Goal继续active。

3个组合e2e examples另行复跑通过，2.85s。最终wheel
`/tmp/corki-step-router-wheel.p1Welo/corki-0.1.0-py3-none-any.whl`，SHA-256
`9141de7b106a318554a336ac0e8130243ff5f5377e51b5961f1f0a195dd78509`。从/tmp使用python -I
加载独立installed目录，运行本批30例、4个HTTP MCP刷新、1个真实stdio刷新及6个
deferred discovery/resume/native wire场景：41 passed，4.10s；全部124个已加载corki
模块来自安装包。Codex工作树仍干净；teach.md SHA-256保持
`816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a`。

## 第二十八批实施前审计：原生搜索历史与执行配置分层（B03/B04/A04）

重新追踪tools/src/tool_search.rs::ToolSearchInfo::from_spec、core/tools/context.rs的
ToolSearchOutput、context_manager/history.rs::for_prompt/normalize_history及normalize.rs：
搜索结果保存的是loadable模型定义，不含runtime并发属性或输出截断策略；McpHandler的
supports_parallel_tool_calls及prepared call的truncation_policy在执行侧独立解析。
历史normalize处理call/result配对、孤儿结果和媒体能力，不接收当前registry，也不因
handler/schema变更去覆写历史tool_search_output。当前router独立选择可执行handler。

因此纠正此前过度对齐判断：Corki的current_discovery_history将“当前ToolSpec不等于
旧ToolSpec就清空旧定义”同时用于native/compatible，原生路径与源码不一致，不应继续
用既有测试断言证明它正确。兼容路径需要本地维护加载集合（Codex原生没有这一function-
calling补偿），但仅concurrency/output_char_budget变化不影响模型已加载的定义，不能
因此要求重新搜索。现有full-spec比较也把执行字段错误地混入该判断。

拟实现：native模型视图保留原发现历史，当前Step dispatch继续来自最新router；兼容
路径保留原内容，但加载匹配忽略这两个明确的执行字段，并使用当前Step spec调度与
输出预算。schema/name/input kind/exposure/来源变化等现有兼容失效约束仍保留，避免
把flat名称映射的不同身份混为一谈；这是明确的兼容边界，不冒称Codex原生也有此过滤。
新增RED用例后更新此前native刷新测试的过期断言，保留raw历史/最新router/不重放验收。

## 第二十八批实施结果：纠正原生历史过度失效与兼容执行字段耦合

`current_discovery_history`现在明确接收mode，真实Graph prepare与retry均传入当前模式。
native返回原active-history条目，不因名称消失、schema变动或来源说明变化清空历史搜索
输出；模型工具调用仍按当前Step router的dispatch集合与参数校验执行。原生历史不是
授权清单，也不使被移除/隐藏的工具重新可调用。压缩移除active history仍释放定义，
没有将“保留历史内容”误做成永久向模型注入所有原始记录。

compatible的加载比较排除仅concurrency/output_char_budget两个执行字段；模型可见
schema、名称、输入类型、曝光和来源等现有匹配约束保持。匹配后使用当前spec形成
advertised/dispatch tuple，不把旧并发或预算带入新Step；历史里的旧ToolSpec和content
保持不变，序列化载荷不被静默改写。native本来就从当前registry构造dispatch，本次
主要修正其历史视图。冷恢复尚未完成调用的executor full-spec校验没有在本批放宽。

本次明确修正第二十四至二十七批中“原生也应清空失效搜索定义”的错误假设，而不是
让实现继续迎合旧测试。相关HTTP/MCP刷新、强制search替换、source说明更新测试现在
分别断言native保留旧历史与compatible过滤失效视图，原有最新handler/连接、raw历史
和不重复副作用的验收全部保留。历史章节保留当时实施记录，本段与顶部矩阵为新结论。

新增18例（939→957）：
- `unit/tools/test_discovery_lifecycle.py`12例：JSON/freeform仅执行设置变化继续加载；
  native在schema/移除/隐藏/metadata变化时保留原输出、dispatch独立4例；兼容schema/
  名称/描述/来源/曝光/input kind变化仍拒绝6例。首次实际6 failed、6 passed；实现后
  全部通过，不冒称原来已通过的身份保护用例也是RED。
- `integration/test_discovery_execution_settings.py`4例：native/compatible × 同Runtime
  跨Turn/关闭后重开数据库。仅首次搜索两工具，修改并发与输出预算后不重搜，两个调用
  实际通过共同启动屏障验证并行（错误旧独占策略会超时），400字符输出按新的80预算
  截断；SQLite仅有一次搜索且仍保存原执行字段。
- 现有Responses真实adapter三Step测试增加更新工具参数化2例：第二次HTTP请求期间
  更新注册对象/描述，本Step旧handler仍执行；第三次native请求的tool_search_output
  与第二次完全相同，compatible则移除变更schema的加载入口。原无刷新2例继续保留。

最终 `LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest --tb=short`：**957 passed，56.67s**。
ruff check/format --check覆盖src/tests/examples/main.py及tokenizer来源验证脚本，236
files；compileall、pip check、node --check通过。无真实模型/Rust执行，不把mock模型
主动发出的工具调用当作模型选择能力证据。

剩余完整namespace wire、source-listing/world-state、跨Step旧CodeMode cell与冷恢复
pending calls、retry/realtime上下文预算等仍在原目标内。本批没有把所有metadata变化
都当执行设置忽略；flat身份映射的兼容约束与Codex namespace原生路径必须继续区分。
原A/B/C/D/E全范围保留，Goal继续active。

3个组合e2e examples单独复跑通过，2.75s。最终wheel
`/tmp/corki-discovery-lifecycle-wheel.ptuJUl/corki-0.1.0-py3-none-any.whl`，SHA-256
`284a22262d4fcf1daae062b7c24537db536485e165aea8ce86782473bf969c88`。Codex固定commit
工作树仍干净，teach.md SHA-256仍为
`816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a`。
从/tmp使用python -I导入独立installed目录，复跑发现生命周期、执行设置跨Turn/重开、
真实Responses wire、MCP来源/目录刷新及强制search替换等40例：40 passed，4.28s；
验证全部124个已加载corki模块均来自安装包，没有退回源码目录。

## 第二十九批实施前审计：旧 Code Mode cell 的新调用准入（A04/B06/E01）

Codex `core/tools/code_mode/delegate.rs::start_turn_worker` 为当前 StepContext 创建
ToolCallRuntime；CoreTurnHost::submit_tool 将 broker 收到的调用交给该 runtime，并非
cell 创建时的 router。`code_mode/mod.rs::submit_nested_tool/build_nested_tool_payload`
保留 cell 提供的真实 tool_name 和 tool_kind；`parallel.rs::handle_tool_call_with_source`
在等待执行锁前捕获当前 router/runtime/并发支持。已准入调用继续持有该 Step，不随之后
的 worker 切换。`registry.rs::tool/dispatch_any_with_terminal_outcome` 按真实名称选择
当前 handler，没有对比旧 cell 的完整 schema；kind 不兼容是 FunctionCallError::Fatal。

Corki `Cell._invoke` 传入 cell 创建时的 ToolSpec，`CodeModeService.invoke` 用旧 spec
决定并发，并把它传入当前 Graph nested closure；executor 的 full-spec 相等检查因此
拒绝跨 Step 的正常 schema/描述/输出预算更新。结论：行为不一致，P1。本批拟在准入时
从当前 Step snapshot 按真实名称捕获执行 spec，payload kind 仍取旧 cell；未知工具交给
普通 executor 返回错误；不修改普通/冷恢复 pending calls 的 full-spec 校验。

曝光语义也必须按源码记录，而非按名称猜测：Codex tools/src/tool_executor.rs 明说
Hidden 是“保留 dispatch、但不曝光”，registry.tool 不排除 Hidden/ModelOnly，Hidden
仅强制非并行；is_available_in_code_mode 过滤的是新 cell 的工具表。旧 cell 已持有的
名称在下一 Step 变 Hidden/ModelOnly 后仍可路由，真正移除注册才阻止执行。因此本批
保留新 cell 曝光过滤，但旧 cell 不把曝光改变当作撤权。此行为不是安全审批/授权实现；
需要撤销执行权限时必须移除 handler，不能仅改 exposure。相关 source-only 测试参考
router_tests.rs::mcp_parallel_support_uses_handler_data，尚未执行 Rust 测试。

另发现独立 E01 差异：CoreTurnHost::submit_tool 最终将 FunctionCallError（包括 Fatal）
转成 String 给 cell，而 Corki Cell._invoke 会调用 service.fail 终止 Turn。此项本批先
登记为行为不一致、P1，后续必须修复并覆盖 durable 失败记录；不能再将现有 nested fatal
测试当成 Codex 一致证据。本批不为修准入而静默转换 payload kind，也不宣称此错误路径
已对齐。验收使用真实 QuickJS cell 跨 Step/Turn，验证新 handler/schema/并发/预算、旧
名称映射、移除与曝光变化，以及等待 gate 的已准入调用仍绑定原 Step。

## 第二十九批实施结果：当前 worker 准入与 cell 原始调用身份分离

`code_mode/service.py::invoke` 继续用旧 cell 的 name/input_kind 组装 payload，然后在
任何执行 gate await 前从当前 step_registry 取 spec，捕获 dispatch closure 与执行
定义。当前 spec 决定并发（Hidden 强制独占）、schema 校验和输出预算；当前工具不存在
时仍走真实 executor unknown-tool Observation 与 ledger，不按 sanitized JS 别名改找
另一个工具。没有修改普通调用或 cold pending checkpoint 的 full-spec 防漂移检查。
当前 worker 之后又切换时，已准入调用仍持有原 closure/spec，不改绑正在排队的调用。

新增27例（957→984），其中首次实际 RED 是19 failed、4 passed：
- `integration/test_code_mode_step_admission.py` 首批20例：JSON/freeform × 同Turn跨Step/
  同进程跨Turn × 执行设置、新schema、Hidden、ModelOnly、移除；其中16例修复前失败，
  移除4例本来通过。实际 QuickJS 旧 cell 等待工具，由下一 Step 释放；Step已准备后再
  发布too-new实例，旧cell后续两个调用仍使用current而非old/too-new。共享启动事件证明
  新并发配置生效，400字符结果使用新80预算，真实名称被移除不误路由到相同JS别名。
- `unit/tools/test_code_mode_admission.py`3例首次全部失败：双向payload kind变更不静默
  重解释旧输入；当前Hidden即使标parallel仍独占；第二个已准入调用在gate排队后更换
  worker仍使用原worker/spec。kind用例只证明桥接契约，未冒称nested fatal已对齐。
- 实现后另加invalid-schema4例（未记录为RED）：JSON旧cell输入被新schema拒绝，freeform
  仍按原始字符串输入而非JSON schema解释。24个集成用例都直接检查SQLite两个真实名称
  调用恰好各完成一次，失败/成功标记正确，成功记录的输出已经按新预算截断。

第一次全量980 passed，61.04s；补充验收后的最终
`LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest --tb=short`：**984 passed，63.35s**。
ruff check和format --check覆盖238files，compileall、pip check、正确路径bootstrap.js的
node --check通过（初次误用不存在runtime.js导致检查命令失败，纠正后通过，不隐瞒）。
3个组合examples E2E单独复跑通过，2.88s。没有真实模型或Rust测试执行。

最终wheel `/tmp/corki-cell-admission-wheel.dDbItH/corki-0.1.0-py3-none-any.whl`，SHA-256
`897b0d5566176b290b8086f24396cc14d21dbe45fbaddd2e64ad16bc3d104dc9`。安装到该目录下的
独立installed，从/tmp用python -I复跑新准入、原CodeMode生命周期、Step snapshot、typed
结果与ledger等74例：74 passed，9.23s；全部124个已加载corki模块路径属于安装包。
Codex仍为固定commit且工作树干净，teach.md SHA-256仍为
`816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a`。

本批不关闭A04/B06/E01全项。除上节nested Fatal传播外，又定位到待修准入生命周期：
Codex session/turn.rs::run_sampling_request 拥有 `_code_mode_worker`，delegate.rs的
CodeModeDispatchWorker::drop发送shutdown；Corki _call_model的finally仅移除failure
callback，service.active直到Turn deactivate才清除，因此Step之间prepare/compact期间
旧cell的新请求是否应排队、以及兼容完整响应后普通tools节点如何持有worker，需要继续
追踪与行为修复。本批测试明确在新worker激活后释放旧cell，不覆盖这段间隙，不能把
“准入时捕获正确spec”夸大为整个worker生命周期已一致。A/B/C/D/E原目标仍全部有效，
Goal保持active。README/development已同步旧cell身份、执行定义与曝光非撤权边界。

## 第三十批实施前审计：Code Mode 分发错误与工具错误值（B06/B07/E01）

继续验证 `CoreTurnHost::submit_tool`：submit_nested_tool 的即时/异步 FunctionCallError
统一map_err为String，code-mode-runtime 的callback失败送回JS Promise拒绝；对应
cell_actor/callbacks_tests.rs::tool_callback_panic_rejects_the_js_promise_and_reports_failure。
成功的AnyToolResult则走code_mode_result，即便工具结果本身表示失败，仍是返回值。
顶层parallel.rs::handle_tool_call的Fatal→CodexErr::Fatal与这条嵌套路径不同。

Corki executor.error把unknown/参数/handler异常等分发错误与ToolResult(is_error=True)
混为一种值，service.invoke通常返回其content，JS catch收不到这些错误；Fatal则穿过
Graph nested的BaseException清理，可能写入“cancelled before dispatch”后由Cell终止Turn。
结论：行为不一致，P1；错误分型、失败事实和模型可恢复性都受影响。

拟增加仅用于内部执行账本的dispatch_error标记：executor生成的分发错误标记为true，
handler正常返回的错误ToolResult仍为false；嵌套executor的Fatal就地转成带标记失败结果，
先完成ledger/events再由service拒绝JS Promise。顶层Fatal继续终止Turn；取消不捕获。
旧ledger无此字段保持原语义，不靠is_error猜测旧记录类型，不改写不可变旧结果。
基础设施异常不能作为正常工具错误吞掉：Cell仅接收明确的桥接/分发错误为可捕获JS错误，
非工具执行边界的持久化等异常仍上报Turn failure。先建立实际QuickJS/Runtime/ledger的
RED用例，验证catch/未catch、返回错误值、fatal/普通异常/timeout/坏结果及持久化回放。

## 第三十批实施结果：嵌套错误拒绝 Promise，持久化与故障边界不混淆

新增ToolResult.dispatch_error（默认false、末尾可选字段）；executor.error生成的unknown/
参数校验/handler异常/timeout/坏结果等分发失败为true。handler正常返回的is_error结果
仍是工具值，不因is_error变成Promise rejection。Graph仅在nested executor边界捕获
FatalToolError，把实际失败转换为带标记结果，经过原claim/complete/events后返回给service；
service.invoke遇到该标记抛CodeModeToolError，Cell把错误字符串传给JS。bootstrap原本就
reject字符串，与Codex runtime/module_loader.rs::resolve_tool_response一致，未改成Error
对象或改写payload kind。未catch时cell为failed、exec输出is_error，Turn可由模型继续。

顶层executor Fatal仍为Turn failure，asyncio.CancelledError不捕获/降级。Cell不再将所有
Exception都当成可恢复工具错误：非CodeModeToolError的桥接/存储异常上报FatalToolError，
同时释放等待JS Promise；这样即使非流式普通tools节点已移除model failure callback，
observe仍能传播基础设施失败，而不是被外层普通exception normalization吞掉。

dispatch_error仅写内部tool_executions.result_json，不加入模型ToolResultItem或provider
wire。true时才序列化，缺字段默认为false；已完成的旧错误行保持原语义，不从is_error
反推类型，不回填或覆写旧记录。ledger自身拒绝碰撞、旧参数未验证或执行结果未知时也
返回分发错误，不能把这种拒绝作为正常工具值。正常handler返回值的该内部标记不从扩展
透传，仍以executor边界区分异常与返回值。

新增50例（984→1034）：
- `integration/test_code_mode_error_channels.py`40例：fatal/普通异常/timeout/非法typed
  result/正常error-valued result × JS catch/未catch × streamed/完整响应 × 正常/强制同
  call id回放。第一轮20例实际16 failed、4 passed（正常错误值原来已通过）；实现后
  全40通过。回放触发真实Graph ledger claim，handler仅执行一次，拒绝/返回通道不变。
- 同文件4例故障注入：streamed/非streamed × nested complete前/已提交后抛OSError；
  JavaScript即使try/catch也不能让账本故障继续模型循环。提交前保留unknown/running或
  interrupted且无结果，提交后原completed结果保持，均单次handler且TurnFailed、cell清理。
- `integration/test_code_mode_step_admission.py`新增4例：旧cell跨Step/Turn后JSON/freeform
  反向kind变化，当前handler未调用，JS捕获incompatible payload，ledger存真实分发失败。
  原移除/invalid-schema用例改为逐Promise catch，仍验证两次实际调用和失败账本，不再
  错误断言分发失败应正常resolve。原nested fatal/invalid typed output测试同步纠正预期。
- `unit/storage/test_dispatch_error.py`2例：带标记与无标记错误的SQLite重新打开、同调用
  claim结果、幂等complete与原始JSON逐字不变，覆盖旧无字段格式。不是恢复旧JS进程的
  证明，Code Mode cell仍不能跨进程重建；不把这一点混为ledger恢复。

最终 `LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest --tb=short`：**1034 passed，65.58s**。
ruff check/format --check覆盖240files；compileall、pip check、node bootstrap.js语法检查
均通过。静态检查首次发现旧TurnFailed未使用import和测试长行，已修正后复验通过。
3个组合examples E2E另跑通过，2.75s。Codex源码与测试只读，未运行Rust或真实模型。

最终wheel `/tmp/corki-code-mode-errors-wheel.bg87hz/corki-0.1.0-py3-none-any.whl`，SHA-256
`67e8aa117f9953d0bde62a1afd7afd7799070acddb2388499ca24e5a927b6ccb`。独立installed目录、
/tmp下python -I复跑嵌套错误/跨Step准入/typed值、直接fatal边界、live cancel和账本身份/
重开等131例：131 passed，17.77s；全部124个已加载corki模块均来自安装包。
Codex固定commit工作树仍干净，teach.md SHA-256仍为
`816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a`。

此结果纠正第十七批以来“nested Fatal必须终止Turn”的错误预期；不据已有测试反证源码。
第二十九批发现的Step间worker关闭/queued admission生命周期仍开放，本批未混入修改。
完整A/B/C/D/E原目标、Context预算/压缩与Memory剩余审计继续有效，Goal保持active；
这不是Code Mode或整个Harness已完成的声明。

## 第三十一批实施前审计：worker 作用域、Step 间排队和同 Step 重试

再次完整追踪Codex session/turn.rs::run_sampling_request：StepContext、ToolCallRuntime
及_code_mode_worker在内部retry loop外创建；try_run_sampling_request结束前drain_in_flight，
最终返回后worker Drop发送shutdown。delegate worker只停止接收新请求，已spawn且捕获
host/runtime的调用不因此取消。无worker时broker保留请求；notify也经同一个dispatch_rx。

Corki worker只在Turn结束deactivate，prepare/compact期间旧cell仍能调用旧router；
_retry_model却refresh并重绑snapshot，activate每次重置step_calls/barrier。后者也与Codex
不一致，纠正第二十七批“采样重试应捕获新router”的旧断言。普通完整响应的工具在单独
execute_tools节点执行，所以不能简单在call_model无条件关闭worker，否则exec/wait挂起。

拟保持逻辑sampling Step worker覆盖model及完整响应后的tools drain；有界采样重试保留
同一snapshot/gate，只重建更新后的历史视图；正常Step结束/准备新上下文前停止准入并
释放worker引用，已准入任务继续完成。新调用及notify在下一worker激活前等待，取消仍
由cell/Turn收回。冷checkpoint直接从tools节点恢复时重新建立当前可用worker，仍遵守
原saved-spec/ledger保护；不声称能恢复旧进程对象。此项为A04/B06/E01行为不一致、P1。
本批也核对等待唤醒后再次pause的竞态与old worker事件sink的Turn归属，不扩大为完整
Codex UI/安全系统。先建立准备间隙、retry gate和pending checkpoint的实际行为测试。

## 第三十一批实施结果：Step worker 作用域及采样重试保留绑定

Graph的_activate_code_mode统一构造worker闭包，call_model使用它，cold checkpoint直接
进入execute_tools时也可建立worker。正常模型响应若还有未stream执行的调用，worker
保留到普通tools drain结束；全部streamed调用已完成则在model node结束暂停。prepare
入口在任何refresh/build/window await前也暂停，作为明确的新Step边界。采样失败走
retry_update时保留worker；_retry_model现在复用StepToolState中的snapshot，不refresh或
bind新代，只从durable active history重建加载集合与请求视图。steering转新prepare仍
建立新Step，这与普通采样retry区分。

CodeModeService.activate在active且同Turn、同snapshot时保留step_calls/barrier；新Step
才重置gate。pause同步关闭新准入并清除dispatch/notifier和Step引用，不取消已准入的
任务；它们已捕获原closure/spec/dependencies。invoke与notify通过_wait_for_worker等待
新worker，Event被唤醒后若已经再次pause会重新检查，不使用空dispatcher。成功Turn
deactivate也清除worker引用，不再将旧闭包一直留到下一Turn。notify不再在间隙静默丢弃。
闭包捕获所属Turn的inactive Event，旧worker排队调用即使到新Turn才真正执行，也不能
借用新Turn的事件生命周期向已放弃的旧UI队列阻塞写入。

新增12例（1034→1046），并更正1个旧retry测试：
- `integration/test_code_mode_worker_scope.py`6个真实QuickJS准备间隙场景，streamed/完整
  响应 × refresh/build/window：旧hold已准入，间隙释放后新probe/notify等待，下一worker
  执行current handler且notification进入历史。最初6例使用10ms yield，修复后暴露出
  “yield并不保证hold已经准入”的fixture问题；改用hold/ready事件屏障，不增加睡眠猜时序。
  对最终fixture另用独立pytest进程将pause临时替换成no-op，6例全部失败（1.22s），退出
  后源码未改；正常6例通过。这是反向故障验证，不冒称测试调整本身是产品修复。
- 同文件冷恢复1例：真实LangGraph SQLite checkpoint停在execute_tools，关闭原Runtime、
  新Runtime.resume_pending重建worker；模型已提交exec未重采样，nested handler仅执行一次。
  另1例直接恢复已准入旧worker闭包，切换新Turn后旧inactive保持有效，旧关闭sink不会
  被写入；不是声称该直接闭包测试覆盖所有跨Turn UI竞态。
- `unit/tools/test_code_mode_admission.py`增加3例：同snapshot重试保留exclusive gate及
  等待任务；invoke/notify在Event set后立即pause重新等待而非穿透或丢消息。
- `integration/test_code_mode_lifecycle.py`原新Step gate场景增加retry参数1例：真实stream
  在已启动hold后断流，retry仍保留2个嵌套任务和未完成hold；原新Step分支则gate为空。
  `test_step_tool_snapshot.py`旧retry断言改为保留first handler，直到下一Step才看到third。
  该更正测试在修复前实际失败；不再把此前“retry捕获second”作为Codex一致证据。

首轮修复前7 failed（6个准备间隙初版和旧retry更正）；测试改为确定性屏障前曾有6个
fixture Timeout失败，记录如上。最终
`LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest --tb=short`：**1046 passed，67.44s**。
ruff check/format --check覆盖241files；compileall、pip check、bootstrap.js语法检查通过。
3个组合examples E2E另跑通过，2.82s。未运行Rust或真实模型，不把ScriptedModel主动请求
工具当成真实模型选择质量证据。

最终wheel `/tmp/corki-worker-scope-wheel.qmfIyv/corki-0.1.0-py3-none-any.whl`，SHA-256
`104e54ce7bff986e4441d7279bd260a2a14827da14492d01d13fde7e0465c2e5`。从/tmp用python -I
导入独立installed，复跑worker作用域、准入/Step snapshot、CodeMode生命周期/错误通道及
真实checkpoint恢复99例：99 passed，11.14s；全部124个已加载corki模块来自安装包。
Codex仍为固定commit且工作树干净；teach.md SHA-256仍为
`816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a`。

本批纠正第二十七批的retry重绑判断；原历史记录不删，以本节及顶部矩阵为当前结论。
这里只关闭已验证的worker准入、retry绑定和cold tools启动差异，不等于完整配置/环境
snapshot、cold pending配置漂移或所有取消/压缩组合已完成。_persist_realtime_input的
独立窗口准备、retry/realtime完整请求预算、手动/remote/reactive compaction、Context
stable key/tombstone及Memory剩余项继续按原A/B/C/D/E目标审计；Goal保持active。

## 第三十二批实施前审计：local compaction 的独立重试与错误路径

先追踪Codex compact.rs::run_compact_task_inner_impl/drain_to_completed及turn.rs的调用方：
本地压缩使用独立client session、provider.stream_max_retries和util::backoff；Interrupted/
TurnAborted直接退出，SessionBudgetExceeded直接报错，ContextWindowExceeded在还有历史时
删除最旧项、将重试计数归零，只有剩压缩请求时失败；其余错误不依赖is_retryable，按独立
次数重试。每次请求仍是不带工具的base instructions + summary request。最终摘要取最后
assistant message，不能把多条assistant正文拼成一个摘要。remote/token-budget路径独立，
本批不把它们混成local请求。

也纠正之前审计中的“主采样reactive overflow缺失”：turn.rs::run_sampling_request遇
ContextWindowExceeded设置full token usage并返回，run_turn的Err分支结束当前Turn；该
源码默认路径没有立即压缩后重采样。压缩内删项重试与主采样自动恢复不能混称。Corki主
采样当前同样失败退出，因此不能继续把“立刻压缩重试”当成该路径的Codex验收要求。

Corki ContextWindowManager._summarize只处理context-window删项，其他ModelError立即
失败；ModelRequest.harness_managed_retries默认false，真实adapter可能先自行进行采样
重试，与manager未来重试叠加。摘要拼接所有assistant正文而非最后一条。结论：C03/E03
行为不一致，P1。拟加入local独立重试上限/退避、关闭每次流、context缩减后重置计数，
manager请求明确接管采样重试但保留HTTP请求层重试；由Runtime传入provider设置，输出
标注compaction用途的retry事件，不污染主采样checkpoint计数。失败与取消不写replacement。

当前Corki尚没有Codex TokenBudget的SessionBudgetExceeded生成路径，这属于已登记的
token-budget功能缺口，不能把provider insufficient_quota当成它，也不新增空枚举冒充。
保留Corki要求非空实际摘要的校验（用户要求不得把失败或半截输出当成功），仍需记录与
Codex空suffix容忍之间的边界。原始partial压缩响应日志、manual/remote压缩、usage驱动
预算等不在本次局部结果中宣称完成。先通过实际Runtime及mock HTTP故障建立RED证据。

## 第三十二批实施结果：本地压缩独立重试，最后有效摘要安装

ContextWindowManager接收max_retries/retry_base_seconds，Runtime传入现有provider采样设置。
_summarize维护独立计数，generic ModelError按上限重试（不读取普通采样retryable predicate
或Retry-After）；本地估算/模型报告超窗删最旧项时重置计数；只剩摘要请求仍超窗则退出。
每次_summary_attempt通过aclosing读到有效ModelCompleted即关闭，只取最后非空assistant
正文；无终态/无有效摘要是失败，不发布partial delta或拼接多条assistant。取消和非
ModelError的基础设施异常不被该重试环捕获。原始历史与replacement安装原子性保留。

摘要请求harness_managed_retries=true，避免adapter再套一层采样重试；HTTP请求层仍可
按原request_max_retries工作。Graph的三处window.prepare入口均传入retry callback，
ModelRetryScheduled新增默认sampling的purpose字段，压缩使用compaction；CLI明确显示
Retrying compaction。该计数不写主采样ModelFailure/sample_count，不消耗普通模型Step，
不重放触发压缩的工具。未新增数据库字段或改写旧checkpoint。

window.py现427行，已按开发指引检查职责：本批把单次模型流/摘要选择隔离在
_summary_attempt，外层只管理history缩减与retry状态；其余既有helpers负责replacement
预算/保留策略。仍是同一上下文窗口事务边界，没有混入provider wire或CLI输出；本次
不为行数进行无关拆包，后续manual/remote实现需重新评估领域拆分。

新增15例（1046→1061）：
- `integration/test_compaction_retries.py`5例首次实际全部失败；实现后通过。真实Runtime
  large-tool → auto compact → 后续模型，覆盖transport、普通采样不重试的invalid_request、
  partial后无终态、耗尽和超窗删项后重新获得预算。max_steps=2仍可完成，handler仅一次；
  notices为独立compaction attempt，忽略fixture的60s Retry-After；最后摘要不含前一条
  assistant消息，失败无CompactionItem，原大工具结果仍在业务历史。
- `unit/context/test_compaction_retry_control.py`5例：0/1/5/500（cap100）重试边界、每流
  关闭、retryable=false仍按local规则重试；取消60s退避立即退出且原历史逐项不变，没有
  实际等待60s。旧单次失败原子性测试明确max_retries=0，保留原测试意图；首次默认新
  行为导致旧“仅一次”断言2例失败，已调整配置而非降低新默认预算。
- `integration/test_compaction_retry_http.py`5例：真实Responses/Chat adapter × 成功/耗尽，
  故意设置adapter采样上限7、manager上限1；实际仅2次摘要HTTP请求，恢复成功后第3次
  才是主采样，未出现8次隐式adapter尝试或双层乘法。请求payload相同、无工具、重试事件
  恰好1个，压缩失败不产生主采样ModelFailure。另1例真实Responses结构化context overflow
  明确1次主请求后TurnFailed，不暗中压缩或再次请求；这是与当前源码一致的行为证据。

最终 `LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest --tb=short`：**1061 passed，67.27s**。
ruff check/format --check覆盖244files；compileall、pip check、bootstrap.js语法检查通过。
3个组合examples E2E另跑通过，2.76s。未运行Rust或真实模型，不将预设工具选择当模型
选择质量证明。README/development已更正“compaction仍由独立adapter重试”的旧描述。

最终wheel `/tmp/corki-compaction-retry-wheel.jHL5Ap/corki-0.1.0-py3-none-any.whl`，SHA-256
`ce6069d65f861e4a8ea6f68e75893ebcac3105af09e15cdffc1d6d9aa606ba8e`。/tmp下python -I导入
独立installed，复跑完整context单测、新Runtime/HTTP重试、既有工具后压缩和CodeMode
worker/context边界62例：62 passed，2.17s；全部124个已加载corki模块来自安装包。
Codex固定commit工作树仍干净；teach.md SHA-256仍为
`816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a`。

本批仅关闭local通用重试与多消息摘要选择差异，并纠正主采样reactive overflow的错误
验收假设。SessionBudget/TokenBudget、manual/remote、provider usage驱动阈值、完整请求
估算、realtime准备、Context增量/删除基线和Memory剩余项仍按原A/B/C/D/E目标推进。
retry计数是一次压缩任务内状态（Codex同样是local变量），不宣称崩溃后能恢复旧摘要流；
partial压缩响应日志等差异仍保留。Goal保持active，未宣称核心对齐完成。

## 第三十三批实施前审计：上下文消息日志与比较基线（C01）

重新读取目标；上一goal turn只整理目标文本，归类no progress。本轮已核实Codex仍为
ddf04ad26789d040f9ef6a96736f76602e35a6cc，工作树干净。遵循用户明确要求先读本地源码；
OpenAI Docs技能的在线文档优先规则不覆盖用户指定的源码基准，不以在线新版本替代本地行为。

Codex真实链路：session/turn.rs::run_turn先capture Step，调用session/mod.rs::
record_context_updates_and_set_reference_context_item；首轮build_initial_context_with_world_state，
后续ContextManager.update_world_state -> WorldState.render_history_diff。Step内调用
record_step_world_state_if_changed -> render_diff(previous_snapshot)，先追加ResponseItem，
再记录WorldState merge patch；该patch是比较基线，不是改写旧模型消息的命令。
context_manager/history.rs::for_prompt只规范化工具配对和媒体，不按section删除旧消息。
world_state/agents_md.rs明确追加replacement/removal notice；WorldStateHash包含role+rendered
text，不能只比较正文。managed developer、collaboration、environment及plugins各自拥有
diff策略：例如plugins不可用不生成移除消息，不能把所有section混成一个空值删除协议。

compaction通过ContextManager.replace_compacted清空基线，Session安装replacement后可记录
新的full WorldState；rollout_reconstruction.rs在Compacted重置，仅回放新窗口状态。
证据测试：agents_md_tests.rs及其snapshot；rollout_reconstruction_tests.rs::
reconstruct_history_replays_world_state_from_latest_compaction_window；suite/compact.rs的
remote-v2同路径global instructions cold resume明确断言完整旧prefix保留，再追加replacement。
该remote测试只作为历史追加/恢复证据，不宣称Corki已实现remote-v2。Codex运行中global
instructions保留创建时快照，而Corki当前每Step重新读项目文件；来源刷新时机仍待独立对齐。

Corki真实链路：Graph.prepare -> ContextBuilder.build -> Window.prepare ->
_changed_context_items扫描全量业务历史，比较content而忽略role；active_history按key
只保留最后非空ContextItem。删除仅存空tombstone且不发给模型。因此旧prompt prefix被改写，
规则更新无replacement说明；marker之前的旧基线会抑制压缩后同内容的重新注入。结论C01
行为不一致，P1。旧development中“同key仅最新值可见”是错误对齐假设，需要更正。

拟分离ContextItem的持久化比较值与已渲染消息：保留模型历史prefix，追加有语义的更新；
AGENTS采用对应notice；扩展通用整段替换作为明确兼容策略，不冒充所有Codex typed diff。
比较值纳入role且只从当前compaction窗口恢复；角色变化先以旧角色声明失效再发布新角色。
新增可选持久化字段需保证旧payload不加默认null、不回写旧行；旧空tombstone在请求视图
恢复失效notice，不能把失效规则无说明地重新激活。压缩仍只重注入当前完整快照。
验收先RED：真实Runtime多Step/跨Turn/冷重开请求prefix、替换/删除只一次、角色变化，
缺失compaction baseline的重新注入；再检查两种provider wire、旧数据及完整回归。
section专属细粒度diff、fragment上限、完整来源顺序/冻结与其余A/B/C/D/E缺口仍保持开放。

## 第三十三批实施结果：保留上下文历史，独立恢复比较基线

新增context/world_state.py（120行），将差异生成和旧历史投影移出window。ContextItem
增加末尾可选snapshot_content：None兼容旧记录，以content比较；非None保存未渲染比较值，
空串表示删除。新update的content是模型实际收到的消息，snapshot_content不会进入两种
provider wire，也不被当成额外模型token；estimate仍计算全部已渲染历史及notice。
item_to_payload在None时省略字段，旧JSON和幂等重放字节保持不变，不需要DDL迁移。

active_history不再按key消除旧ContextItem。新消息先持久化replacement/removal，再作为
稳定prefix回放；AGENTS使用Codex对应措辞与INSTRUCTIONS包装，提示正文放在三个新增
prompts/context资源中。通用贡献采用按key的整段失效/新值说明，是当前兼容策略，不等于
environment的字段级diff、plugins的静默不可用状态或skills的专属注入生命周期。
role-only变化现在有两个消息：host以旧role结束旧section，然后用新role引入新内容，
没有将低role内容提升权限。这是角色可变的Corki扩展契约，不宣称Codex内置固定role
section普遍都会变role，也不是审批/OS权限实现。

changed_context_items在CompactionItem处清空基线，仅使用replacement及之后的Context。
因此相同规则未被保留时重新注入，已在replacement中保留时不重复；压缩重注入只使用完整
比较值、不复用旧notice。上下文更新与pending input仍共享一次append事务；没有新增
“已比较但未持久化”的内存缓存，失败/取消后的重试从业务事实恢复。

旧快照/空tombstone仅在请求视图生成notice，不回写旧行。旧角色切换的投影失效消息使用
稳定UUIDv5，重复读取不产生不同身份。这会纠正旧客户端按key删历史后的请求视图，因此
不承诺升级前后旧缓存请求字节完全一致；修复后的连续请求prefix保持稳定。尚未提交的
旧checkpoint保留其已冻结请求，新prepare才进入更新后的投影，不主动改写旧checkpoint。

源码核实期间发现当前Corki已有Git仓库：root=/Users/corki/PycharmProjects/corki，HEAD=
041bf05eb93d353e3a67a402086ebf8cf04b39f0（init）。不能继续沿用先前“不是Git仓库”的状态。
大量既有tracked/untracked改动及用户暂存docs/flow.md全部保留；未stage、commit、reset或
覆盖无关文件。Codex仍固定ddf04ad，工作树干净。window.py现412行，比本批开始减少15行；
增量世界状态已隔离，window仍负责同一压缩事务/预算，未增加provider或UI转换职责。

新增16例（1061→1077），并更正1条旧“删除就不回放”的错误期望：
- unit/context/test_world_state_journal.py：10例。旧消息prefix及显式失效投影、role两个
  方向相同正文变更/冷库重开、compaction保留/不保留相同规则、append提交前/后×取消/
  OSError四个故障窗口；旧数据库重开后比较payload_json逐字节不变，旧append重放仍幂等。
- integration/test_context_update_journal.py：6例。真实Runtime工具改规则→下一Step、
  后续删除/重复不变/冷重开/重新出现；真实LangGraph在call_model前checkpoint冷恢复，
  已渲染notice与snapshot_content完整恢复，未重采样旧步骤或重复append；真实大工具结果
  触发auto compaction成功/失败，摘要看到旧规则和替换消息，安装只带当前完整规则，
  当前输入原样保留，失败不发布marker；Responses/Chat真实adapter HTTP payload连续
  prefix断言、replacement/removal各一次，内部比较字段不上wire。

首次8例中7失败/1通过，其中Responses用例先暴露fixture缺response.id；修正fixture并
用真正删除文件代替空文件后，两种HTTP用例都在prefix断言RED。其余5条失败断言也有
修复前RED，保留了已在replacement中存在规则的1条通过对照；新增故障/压缩/checkpoint
用例是进一步边界覆盖，不冒称全部16例曾跑旧实现。
实现后局部66通过；最终新增16例全部通过。最终严格全量：
`LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest --tb=short`：**1077 passed，68.86s**。
ruff check/format --check（247files）、compileall、pip check、node --check bootstrap.js、
git diff --check均通过。3个组合examples E2E另跑：3 passed，2.73s。

wheel：`/tmp/corki-context-journal-wheel.LwacUi/corki-0.1.0-py3-none-any.whl`，631534bytes，
SHA-256 `b8b2e619c88ccf33887fb7bd77b336cb9dc708b575a7221ec096d42a1f9890bd`。
在/tmp用python -I导入独立installed后复跑完整context单测、新Runtime/HTTP/checkpoint、
compaction retry和CodeMode worker/context组合：**77 passed，2.85s**；检查全部125个
已加载corki模块均来自安装包，新增notice模板随wheel可读。
teach.md SHA-256仍为816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a。
未运行Rust或真实模型；上述预设调用不证明模型工具选择质量。

本批关闭的是prefix改写、角色漏更新和跨compaction错误基线三个具体问题。C01仍部分一致：
须继续逐section核对来源加载/冻结、字段diff、删除策略、独立消息合并和bounded fragment；
尤其plugins的不可用不等于撤回通用指令、显式skill正文不应误作每Step当前目录快照。
C02/C03完整usage/预算和manual/remote、C04/C05及Memory和其余A/B/E范围均保持原目标。
Goal保持active，不宣称Harness核心对齐完成。

## 第三十四批实施前审计：显式技能正文不是每Step世界状态（C01/B05）

上一goal turn有实际实现、1077严格测试及安装包证据，归类progress。本轮重读原目标并核实
Codex固定ddf04ad且干净；Corki保留既有dirty worktree（包括暂存docs/flow.md）。

Codex session/turn.rs::run_turn在捕获first Step后调用build_skills_and_plugins：
HostSkillsSnapshot.load_skill_prompts读取selected host skill，ext/skills/fragments.rs::
SkillInstructions是user fragment；extension turn_input contributor同样产出显式正文。
run_hooks_and_record_inputs先记录用户输入，再record_conversation_items追加injection_items。
该入口在run_turn仅调用一次；之后Step刷新world_state不会重新读取/注入显式正文。
InjectedHostSkillPrompts只在本Turn内避免host/extension重复，不是按Thread永久去重。
相同技能在新的显式Turn会再次注入；未再次提及时旧正文仍是历史消息，不生成撤回指令。
catalog是另一条ext/skills/world_state.rs增量链，包含空目录/隐藏/超预算的专属说明。

compact.rs::compacted_user_message通过event_mapping.parse_turn_item排除contextual user
fragments；build_initial_context只重建world_state，不重新调用显式技能读取。因此mid-turn
压缩不自动复活技能全文；摘要可以包含它的内容，新Turn显式提及仍可再读。
suite/skills_extension.rs验证host/repo/plugin正文user role、path和内容；相应fragment与
host_prompt源码明确本Turn读取/去重，不能与shadow selection或orchestrator实验混为一谈。

Corki ContextBuilder把SkillContextContributor的catalog和selected都放snapshot.items；
Graph每Step用未清除的user_input重新build，读取正文后以stable key diff。后果：正文先于
触发它的用户输入；同Turn文件改变会替换正文；下Turn未提及则生成失效notice；相同技能
跨Turn提及可能被去重；mid-turn压缩会从当前snapshot重注入全文。结论行为不一致P1。

拟区分输入附加片段与世界状态：PromptContribution标记input_scoped，ContextSnapshot
单独返回input_items；Runtime只在原始Turn输入准备时选择显式正文，Window以稳定输入ID
绑定，在用户之后原子追加。正文不参与世界状态替换/删除、不在普通Step重读；当前输入
首次准备发生压缩时保留新正文，已采样后的压缩不把旧正文作为full context重注入。
旧selected key识别仅用于兼容历史，不回写旧数据库；新关联字段缺省省略，冷恢复不重复
注入。先用真实Runtime建立RED，再覆盖文件变更/删除、重复提及、恢复、压缩和输入顺序。
技能catalog细粒度diff/预算/警告、选择器完整匹配以及其余A/B/C/D/E范围不因此关闭。

## 第三十四批实施结果：技能目录与输入正文生命周期分离

SkillContextContributor.contributions仅提供catalog，实际正文读取移到
InputContextContributor.input_contributions。ContextBuilder通过include_input_context在
原始Turn输入准备时调用该入口；普通world-state contributor仍收到最新user_input，不能
为了停止读技能正文而把所有contributor的用户输入清空。PromptContribution.input_scoped
经assembler保留，ContextSnapshot.input_items与完整world-state items分开。
该可选Protocol由真实SkillContextContributor实现并进入Runtime，不是空扩展接口。

新增context/input_context.py（68行）：Window以原始UserMessageItem.id绑定正文，UUIDv5
由input ID/key生成、created_at取原始输入值。ContextItem末尾source_input_id为可选字段，
None时不写JSON；provider只收到原有role/content，不接收内部输入关联或snapshot_content。
world-state diff忽略输入片段，normal prepare一次append提交context更新→用户→技能正文。
新的显式Turn有新的输入身份，相同正文仍会再次注入；同一输入prepare重试优先使用已记录
正文，不以文件的新内容覆盖。已在raw history出现但被压缩移除的正文不因重试重新复活。

Window预算包含新输入正文；pre-input压缩保护本次尚未采样的用户及正文，replacement
把正文放在用户之后。mid-turn已经采样的正文参与历史摘要，不属于full context重注入。
因此后续Step不会逐次读文件，也不会因为下一Turn没提及或文件删除而生成正文失效notice。
旧selected key只作为兼容识别：此前错误产生的replacement/tombstone只修正请求投影，
不改写旧数据库。旧行没有输入ID时以同Turn的legacy selected记录识别已有注入，不声称
能恢复旧版每一次steering选择的精确来源。旧checkpoint保留已有冻结请求，新的prepare
才使用新投影，不强行改写已提交模型决策。

另核实session/turn.rs::run_hooks_and_record_inputs与pending-input循环：steering记录输入、
hooks和MCP依赖准入，不再次调用build_skills_and_plugins。Corki三处builder调用现在明确
区分初始输入选择/普通Step/steering；后两者不做自动全文注入。技能目录提示说明模型仍可
用skill_read读取steering中新提到的技能，或重读被压缩掉的正文。这不等于已实现Codex的
全部skill MCP依赖安装、extension/orchestrator选择或语音realtime。

新增12例（1077→1089），修改了旧错误顺序/steering期望：
- integration/test_skill_input_lifecycle.py 3例首次全部RED：同Turn编辑/删除文件，原正文
  必须保持、读取次数不增、正文必须在用户之后；跨Turn不撤回，冷重开后两次同名显式请求
  各自新注入；大工具→真实auto compact不重注入旧全文，原始记录保留，新Turn可以再读。
- integration/test_skill_input_recovery.py 4例：真实LangGraph在call_model前checkpoint，
  以及input+skill已commit但prepare抛OSError两个冷恢复窗口，重启改文件仍采样原正文一次，
  下Turn不重复；真实Responses/Chat HTTP wire验证user role、输入之后、prefix不改写、
  内部关联字段不上wire。
- unit/context/test_input_context.py 4例：legacy selected错误替换/失效投影不污染world-state；
  pre-input compaction新输入/已落库输入两种状态完整保护正文，重试不以新render覆盖；
  关闭输入选择不隐藏最新用户输入给普通world-state contributor。
- 旧integration/test_realtime_steering.py扩为2例（净增1）：初始已选正文在steering后保留；
  仅steering新提及不触发第二次Turn-start自动注入，输入仍持久化并用于后续采样。
  unit/skills和context recall旧测试改为input_items和user之后的位置，未删除原先召回验证。

初版实现3个RED回归通过，后续87通过/2个旧顺序断言失败得到修正。首次完整1086通过，
新增HTTP后1088通过。分离可选input contributor时发生单元素tuple漏逗号，中间局部9失败、
已启动的全量31失败；修正返回契约后新增边界13通过，重新启动最终完整回归，没有沿用
中间版本的绿灯。最终：`LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest --tb=short`：
**1089 passed，75.17s**。ruff check/format --check覆盖251files；compileall、pip check、
node --check bootstrap.js、git diff --check通过；3个组合examples E2E另跑通过，2.90s。

最终wheel（独立入口版本）：`/tmp/corki-input-context-final.20RnaY/corki-0.1.0-py3-none-any.whl`，
633680bytes，SHA-256 `6ba4a7c67c0306ae8d57819ec2fe8cd53f71ce6e2134cea37b7b9b6d1e6c2512`。
在/tmp用python -I先导入独立installed，再复跑全部context/skills/prompting单测、技能
Runtime/HTTP/冷恢复、steering、context recall及CodeMode worker/context组合：
**101 passed，5.98s**，全部126个已加载corki模块来自安装包。早先8zcvNM的wheel是
接口分离前的中间产物，不作为最终版本证据。

window.py现418行，仅增加输入片段绑定/保护与事务排序，独立绑定规则放在68行模块；
Graph只切换输入选择入口，不把技能读取、模板或关联身份塞进graph。没有新DDL、审批、
外部写入或用户目录扫描范围扩展；保持所有既有Git改动和暂存docs/flow.md。
Codex固定commit仍干净；teach.md SHA-256仍为
816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a。
未运行Rust和真实模型，脚本化调用只证明Harness链路，不证明模型选择质量。

本批只关闭显式正文的注入位置、Step读取、历史撤回、跨Turn去重和压缩重注入差异。
SkillService仍存在128KiB整份读取/失败无runtime warning、catalog预算/隐藏/空状态专属diff、
完整选择匹配及来源加载快照差异；它们继续在C01/B05/E03内推进。环境/项目来源冻结、
C02/C03完整usage/手动远端压缩、C04/C05、Memory全链及A/B/E剩余范围保持原目标。
Goal保持active，未宣称核心对齐完成。

## 第35批修改前审计：显式技能读取失败的非致命诊断

本批继续C01/E03，不改变完整A/B/C/D/E目标。Codex基准仍为
ddf04ad26789d040f9ef6a96736f76602e35a6cc；保留Corki全部既有改动及暂存docs/flow.md，
不修改teach.md。上一批最终基线1089 tests通过。

先追踪Codex：ext/skills/src/host_prompt.rs::load_skill_prompts逐项读显式选中技能，
失败只把`Failed to load skill <name> at <path>: <error>`加入warnings，不创建正文，
继续处理其他技能；core/src/session/turn.rs::build_skills_and_plugins（875–910）
将warnings经Session::send_event发出WarningEvent，然后记录成功的skill fragments。
core/tests/suite/skills_extension.rs::production_turn_warns_and_omits_unreadable_host_skill
直接验证生产Turn中missing/available两个技能：恰一warning，模型仅收到可用技能正文。
ext/skills/src/extension.rs（465–535）也在资源读取失败时emit_warning并继续。
host plugin正文截断及extension资源上限是另外的配置路径，本批不混同本地读取上限。

事件链：core/src/session/mod.rs::send_event → send_event_raw_with_persistence →
rollout store policy；codex-rs/rollout/src/policy.rs将Warning明确归于transient/non-durable，
不是持久化conversation item。tui/src/chatwidget/protocol.rs:177将Warning通知交给
on_warning。不能仅因send_event注释说persist就声称warning会进入模型历史或冷重放。

Corki对应：skills/context.py::input_contributions对OSError/UnicodeError/ValueError
静默continue；builder只有正文输出、protocol/events.py没有warning、Graph和CLI没有
对应消费链。可用技能仍注入，但用户看不到失效原因，属于“部分一致”，优先级P2。

实现方案：可选输入contributor返回不可变的fragments+warnings结果，兼容既有tuple
contributor；ContextSnapshot末尾默认warnings不进入items/input_items，Graph在prepare
中通过已有有界事件队列发WarningEvent，CLI以字面文本显示。读取错误消息限制长度并
安全处理异常__str__/非法Unicode，绝不将CancelledError或任意基础设施异常一概降级。
无新DDL/checkpoint字段，无后台共享warning队列，不承诺崩溃边界恰好一次通知。

验收：真实Runtime对应Codex缺失/可用双技能场景，读取类异常分别覆盖；warning先于
采样、普通下一Step不重新读取、不重复warning，同Thread新显式Turn仍可重试读取；
warning不进请求/存储、不伪造正文或tool result；取消继续作为取消控制流，意外异常
仍失败；满事件队列时取消不死锁；CLI保留路径中的Rich标记字面内容；tuple扩展兼容。
先写失败测试，再实现，最后运行相关回归与完整静态/行为验证。本批尚未宣称修复完成。

### 第35批结果：读取失败 warning 接入真实 Runtime 和 CLI

新增不可变InputContextContributions，输入contributor既可返回该结果，也可保留旧tuple
契约。SkillContextContributor只对原有可恢复读取异常返回诊断与成功正文；每条诊断含
技能名、路径、异常类型及原因，最多4000字符，异常__str__失败不升级Turn错误，非法
Unicode以replacement编码。CancelledError和任意未分类RuntimeError不被catch降级。
这4000字符是Corki传输防御上限，不宣称来自Codex的技能正文截断预算。

ContextBuilder将warnings放在ContextSnapshot末尾默认字段中，不加入任何ContextItem；
Graph三处prepare入口均先经原有有界EventSink发WarningEvent再继续窗口准备。没有增加
后台任务或共享诊断缓存。WarningEvent只在RuntimeEvent union/CLI消费，不进业务历史、
请求或checkpoint。普通后续Step不重新选择正文，因此不重新报告相同读取失败；同Thread
的新显式输入可再次尝试。warning不是工具Observation，不让模型误以为执行了工具。
CLI通过show_notice显示`Warning: ...`，TerminalUI用Rich Text字面渲染，错误字符串中的
方括号不作为markup解析。无新UI协议方法、无DDL、无旧数据重写。

新增9例（1089→1098）：
- integration/test_skill_load_warnings.py 7例：FileNotFoundError、UnicodeDecodeError、
  长/非法Unicode ValueError、异常__str__再次失败；真实Runtime双技能→成功正文→工具
  Step→再采样→跨Turn再次请求，验证单次warning、正确Thread/Turn身份、成功正文不丢、
  下一Step不重读、请求与SQLite不含失败诊断。对应Codex生产缺失/可用双技能测试。
  另覆盖未分类RuntimeError仍TurnFailed、CancelledError仍传播且只有一个取消终态；
  event_queue_size=1时第二warning确实堵在满队列，cancel_active能结束、没有模型采样、
  running Turn清除，不把被取消的第二次投递标成成功。
- unit/cli/test_application.py新增2例：warning进入notice而非assistant error；含
  `[red]`和不匹配闭合tag的路径/错误消息字面保留，Rich不抛markup错误。
旧input contributor tuple返回的正/负入口、技能生命周期与冷恢复等回归保留。

实现前新增Runtime测试为**4 failed / 2 passed**，四个失败都是没有warning而不是导入
缺失；修复后第一组相关36通过，追加UI/backpressure边界后14通过。完整最终回归：
`LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest --tb=short`：
**1098 passed，71.61s**。ruff check及format --check（252files）、compileall、pip check、
node --check bootstrap.js、git diff --check均通过。组合examples E2E另跑3通过，2.56s，
覆盖主循环/跨Turn记忆、内置工具/媒体、skill/plugin/MCP按需搜索与后续调用。

第一次--no-build-isolation打包因当前venv未安装hatchling失败，未改项目依赖或虚报成功；
改用项目声明的隔离构建后wheel成功：
`/tmp/corki-skill-warning-wheel.QhbmzN/corki-0.1.0-py3-none-any.whl`，634359bytes，
SHA-256 `50e53e462e3382108aef67d2c3c638b98840add7488ead1ed1c2ad5f0572b67d`。
在/tmp用python -I先导入独立installed包，再执行全部context/skills/prompting、CLI事件
单测及技能warning/生命周期/冷恢复/HTTP、steering、context recall集成：
**107 passed，4.29s**，131个已加载Corki模块全部来自独立安装包。

development.md记录返回契约、错误分类及诊断非持久化边界。Codex固定commit仍干净，
teach.md SHA-256仍为816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a。
未运行Rust测试或真实模型，预设工具决策仅证明Harness链路，不证明模型选择质量。
warning不承诺跨崩溃恰好一次：prepare在checkpoint前重跑可重复报告，持久化完成后的
请求继续使用已有输入正文。发现阶段已消失而未被选中的技能、完整选择匹配、catalog
隐藏/空状态/预算及来源冻结仍是独立未关闭项。本批不把E03或C01整项标记为一致。
完整A/B/C/D/E目标继续active，下一步继续源码审计其余context/skills边界及既定剩余范围。

## 第36批修改前审计：host技能目录预算、路径别名与渲染诊断

上一Turn属于progress：已有代码、RED→GREEN和1098完整回归。重新读取完整objective，
保持A/B/C/D/E范围不变。Codex仍ddf04ad26789d040f9ef6a96736f76602e35a6cc且干净，
Corki保留用户及前批改动。OpenAI Docs适用说明已检查；本任务按用户指定的本地源码
优先进行实现审计，不以在线产品说明替代固定版本调用链。

源码链：ext/skills/src/world_state_catalogs.rs::CatalogContext::new从ModelInfo取得
resolved_context_window和skills.max_context_tokens；render_catalogs走
render_combined_available_skills，host-only退到CoreCompatible render_available_skills。
render.rs::skill_metadata_budget默认窗口2%（至少1token），未知窗口8000字符，显式
正整数max_context_tokens最多10000token。metadata_line_cost包含每行换行，token成本
是UTF-8 bytes/4向上取整，不是Corki通用请求估算器，也不是精确模型tokenizer。

CoreCompatible排序为system/admin/repo/user/其余，再name/path；description最长1024
字符含...。allocate_skill_lines先判断完整目录，然后保留所有minimum name/path行并
逐字符round-robin分配description，连minimum都放不下才按顺序保留能放下的完整行，
不是遇到大行就停止。render_available_skills还比较absolute与aliased两种候选：先最大
included_count、再最少truncated_description_chars、最后更低预算成本。根表自身收费，
alias使用最长目录前缀且必须匹配路径分隔符。aliases.rs、host_aliases.rs实现根去重、
root discovery顺序、plugin cache单skill版本共享marketplace根；外部symlink无法匹配
root时保持真实绝对路径。catalog_prompt.rs提供根表及展开说明。

render_tests.rs已有预算比例/字符回退、描述公平分配、UTF-8、别名压缩/收益比较、
omission和warning平均缩短严格>100的测试。world_state_catalogs.rs::build_world_state_section
仅在section渲染时发预算warning，并由EmittedCatalogBudgetWarnings按Turn/message去重。
world_state.rs对body相同无更新、初始absent不显示空目录、移除显示host unavailable提示；
不是每次采样重复warning，也不是通用“旧指令失效”的文本替换。

Corki：SkillService.render_catalog固定48000字符整体截断，忽略模型窗口、元数据行完整性、
公平描述预算及alias；SkillContextContributor每Step提供全字符串；world_state以通用
section notice包装目录更新。只有上一批input warning，无section-render条件诊断。
该差异在小窗口/大量技能/长路径时直接影响可发现集合及有效路径，C01/B05部分一致，P1。

修复计划：独立host catalog renderer实现上述分配及alias候选，不改原始发现集合或
显式技能解析，省略目录项仍可skill_list/skill_read；新增skills.max_context_tokens从
TOML到真实Runtime，默认用配置模型窗口。PromptContribution携带仅渲染侧诊断，builder
分离section warnings，Window只在section变更/压缩重新渲染时触发，GraphRunContext保存
临时Turn去重集合，不持久化warning。目录使用专属追加/移除语义，保留原始历史前缀。
先建RED再接线验证Runtime多Step、目录变化、按需读取和取消/恢复回归。

本批对齐实际存在的host本地来源，不伪造executor/orchestrator包协议；多来源共享预算、
独立隐藏配置、完整选择匹配及来源冻结仍需另行落实，不能据此关闭整个C01/B05。

### 第36批结果：完整元数据行分配及host路径别名进入主循环

skills/catalog.py独立实现MetadataBudget、CatalogReport、CatalogRender和host分配器，
替代SkillService的48000字符整体切片。默认按真实Runtime配置窗口2%预算；未知窗口
字符回退；TOML `[skills] max_context_tokens`正整数覆盖并在渲染端封顶10000token。
配置不改动已发现技能集合，省略只影响模型目录。初始空集合不再输出空技能surface；
有技能但全部minimum都超预算时保留host-only空目录fragment及omission report，匹配
Codex CoreCompatible preserve_empty_fragment路径，不伪称其等于混合来源host omission文本。

完整行含换行计费、描述预裁1024字符、minimum优先、round-robin按UTF-8边界分配，
大minimum可被跳过以保留后面的短行。absolute与aliased候选都经过相同分配器；选择
优先覆盖数量，然后描述保留，再预算成本。根表按发现顺序去重、别名按最长目录边界
匹配；plugin cache单技能版本共享marketplace根；scope显示顺序与发现/覆盖优先级分离。
Corki现有project/user/system映射到对应host显示顺序；额外plugin scope仍保留qualified
identity，完整Admin/插件来源映射及loader display_path/discovery_path契约不能由本批外推。

元数据正文/根表/移除说明在新增3个prompt资源中；usage说明新增短file路径展开规则，
skill_read仍可按skill name直接调用。Apache来源及修改说明记录在THIRD_PARTY_NOTICES，
完整上游license/notice随CODEX_CATALOG_LICENSE.txt进入安装包。

PromptContribution/RenderedPrompt末尾warnings字段只成为ContextSnapshot.section_warnings；
Window将真正更新或压缩重新渲染的section诊断放入PreparedHistory.warnings，Graph三处
prepare路径消费，并在GraphRunContext的临时set内按Turn/message去重。此set放在字段
末尾，保留原来的位置参数顺序。warnings不会变成ContextItem、tool Observation或
checkpoint字段；不同于Codex同步on_render，当前通知在窗口事务成功后发出，不保证
跨崩溃恰好一次。读取失败warning仍走上一批独立输入诊断入口，不混同预算警告。

world_state对skills catalog新增专属渲染：更新直接追加完整skills fragment，不插入
通用“旧指令失效”notice；移除追加“No host skills are currently available.”并存空基线，
没有删改已有prefix。其它section仍用既有规则，不把该修复扩张为全部typed diff已完成。

新增27例（1098→1125）：
- unit/skills/test_catalog_budget.py 25例：最早两例在旧实现为2 RED，分别发现元数据
  21880字符超8000回退预算、空集合仍输出surface。随后覆盖7组窗口/覆盖/回退预算、
  公平分配精确结果、UTF-8/字符两种计费、超大行跳过、全部省略report、描述1024上限
  与平均缩短严格>100 warning阈值、alias根表成本与可逆路径、最长目录边界、发现顺序
  与scope顺序分离、plugin共享根、5组非法配置/TOML、目录追加/移除/稳定基线。
- integration/test_skill_catalog_runtime.py 2例：实际Runtime默认100000窗口→2000token
  目录，以及max_context_tokens=1→全部省略。初次采样→工具修改技能描述→目录新片段
  →skill_read→正文Observation→完成→同Thread新Turn。验证配置贯穿、预算不超限、
  省略不禁止读取、旧prefix不改写、同Turn相同预算warning去重、下Turn目录未变不重报，
  警告不进SQLite。初版测试错误访问ToolResultItem.result导致两例失败，改为真实content
  字段后通过；没有把这个fixture错误当成产品RED证据。

第一轮完整1125通过（70.69s），安装前逐文件静态复核发现一个原有验证范围问题：
pyproject.toml的exclude="skills"同时跳过src/corki/skills与tests/unit/skills。此前
全量Ruff命令的绿灯不能证明这些目录覆盖。改成只匹配根资源树的skills/**，以--show-files
验证9个skills源码和2个单测文件确实纳入、根捆绑资源仍排除。新增renderer的5处zip
补strict=True以校验内部等长契约；没有机械格式化第三方资源。扩大后Ruff check和
format --check覆盖264files均通过，原有skills代码也实际通过。

最终重新跑完整：`LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest --tb=short`：
**1125 passed，71.67s**。compileall、pip check、node --check bootstrap.js、git diff --check
通过；组合examples E2E另跑3通过，3.04s。最终独立wheel：
`/tmp/corki-skill-catalog-final.p53Bvm/corki-0.1.0-py3-none-any.whl`，643320bytes，
SHA-256 `24ca98ce1388267d1c83cb30e6203ba7cebb5f430539b41258b534a903feff28`。
在/tmp、python -I先载入独立installed后，全部context/skills/prompting、CLI事件、目录
Runtime/读取失败/技能生命周期/冷恢复/HTTP/steering/context recall回归：
**134 passed，5.12s**，132个已加载Corki模块全来自安装包，新prompt资源及license可读取。
早先sha95d8...是修正strict zip前中间wheel，不作为最终证据。

README/development已记录预算及边界；未执行真实模型或Rust测试。Codex固定commit仍
干净，teach.md SHA-256不变：816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a。
保留用户既有改动及docs/flow.md暂存。目录预算只覆盖metadata，不能替代C02完整请求
估算或C03压缩验证；完整选择/隐藏/来源冻结、多来源共享预算、Memory各阶段及其余
A/B/C/D/E差异均保持开放。目标仍active，未将局部绿色回归解释为核心对齐完成。

## 第37批修改前审计：技能隐式可见性与启用状态分离

重新读取完整objective。上轮属于progress：目录算法与真实Runtime修复已验证1125tests。
Codex仍固定ddf04ad且干净，Corki既有改动保留，不修改teach.md。

Codex链路：ext/skills/src/loader/host.rs::load技能时并行读取SKILL.md与agents/openai.yaml；
loader/metadata.rs::load_host_skill_metadata对缺失/读取失败/畸形可选元数据仅tracing warning，
保留有效SKILL.md并回退默认。skills/src/model.rs::allows_implicit_invocation缺省true；
provider/host.rs::catalog_entry_from_skill将false转为prompt_visible=false而非enabled=false。
catalog.rs::is_model_visible为enabled&&prompt_visible，render和tools/list据此过滤；
skills/src/selection.rs默认host显式选择只排除disabled_paths，并不排除hidden；
ext/skills/src/selection.rs同样仅检查enabled。tools/read.rs已知package查找只要求enabled，
不要求prompt_visible。该read/list是executor/orchestrator接口；Corki host name-based
skill_read/list是等价可见性边界，不声称具备其资源协议/游标/分页。

config/src/skills_config.rs：skills.config规则按顺序、后规则覆盖前规则，selector为
name或canonical SKILL.md path。host_service.rs::load应用规则得到disabled_paths，保留
原发现metadata；disabled不等于发现失败，也不允许因禁用高优先级定义而偷偷回落同名低
优先级定义。当前Corki只有整个skills_enabled，metadata无policy，render/list/resolve
都使用全部snapshot.skills。自带review-agent/agents/openai.yaml明确false却仍曝光，
属于B05/C01行为不一致，P1。_root_signature只跟踪SKILL.md，sidecar改动不会失效缓存。

方案：在metadata末尾加入缺省true的隐式可见字段，单独加载可选sidecar，失败按原始
日志而非Turn失败处理；snapshot分开enabled与visible，目录/list过滤visible，显式
resolve/read保留enabled hidden。新增有序skills.config name/path规则经TOML→Runtime→
SkillService落实，不改变发现顺序或同名覆盖。指纹包含sidecar的创建/编辑/删除，保留
取消和非预期异常边界。测试先暴露真实自带策略漏读和禁用缺失，再覆盖Runtime显式
注入→工具读取、隐藏目录、规则覆盖及缓存恢复。无新数据库字段或安全审批边界扩大。
全局include_instructions隐藏surface、完整mentions/path语法、来源冻结和MCP依赖准入
仍为另外的明确开放项，不把本批当成B05/C01全项完成。

### 第37批结果：hidden与disabled贯穿目录、显式输入和skill工具

SkillMetadata末尾allow_implicit_invocation缺省true，parser调用独立skills/policy.py
读取实际SKILL.md同目录的agents/openai.yaml。缺失、I/O/Unicode/YAML或已检查的已知
字段类型错误只记录日志，metadata回退默认，不产生SkillLoadError、不删除有效正文。
policy、interface和dependency已知字段形状在此验证，但没有把dependency准入或interface
UI标为实现；products只识别已有值，产品限定来源路径仍需单独对齐。没有把可选metadata
诊断误当成上一批模型采样前的必需正文读取warning。

SkillSnapshot末尾disabled_paths保留整个发现结果；is_enabled只检查配置禁用集合，
is_visible再检查隐式可见性。Service.catalog及SkillListTool使用visible；resolve、
explicit_mentions、SkillReadTool使用enabled，hidden但enabled的技能仍可被已知名称
显式调用。自带review-agent因此退出自动目录，但内部snapshot仍有10个捆绑技能，
其显式注入/读取正常。该区别不禁止shell读文件、不撤回对话中已注入的历史正文。

新增config/skills.py的有序SkillRule、规则解析及disabled-path计算；Settings读取
[[skills.config]]后传入真正Runtime构造的SkillService。name精确匹配现有qualified
identity，path在配置目录解析并canonicalize；相同selector保留最后位置，后规则覆盖前
规则；未发现path的禁用身份仍保留。规则作用于完成同名优先级合并后的snapshot，
禁用胜出定义不会偷偷启用另一份同名低优先级定义。无效selector日志跳过，规则结构
非法日志回退空规则，与Codex规则提取的fail-open方向一致。Corki当前单配置文件和
直接Settings输入不代表Codex全部用户/session分层配置、完整path类型归一化已完成。

缓存指纹同时探测每个SKILL.md和其实际目录的sidecar，即使sidecar最初不存在也占
签名位置，创建/编辑/删除能失效；不把隐藏列表缓存与禁用状态混成永久删除。
无新DDL、无checkpoint字段迁移，SkillRule不进入模型请求；保留现有config/source
冻结差异，不以这次缓存刷新声称已对齐Codex的整个Turn加载快照生命周期。

新增17例（1125→1142）：
- unit/skills/test_visibility.py 15例：最早2例确为修复前RED，自带review-agent仍在
  自动目录、skill_list重新披露hidden技能；随后覆盖显式可读、7组畸形/缺省可选metadata、
  sidecar三种文件变更及稳定缓存命中、高低优先级同名禁用不回退、2组后name/path规则
  覆盖、未加载path保留、TOML路径归一化及重复selector位置、无效配置日志回退。
- integration/test_skill_visibility_runtime.py 2例：真实Runtime分别使用hidden-enabled
  与hidden-disabled的同一个技能，输入$fixture→skill_list→skill_read→Observation→完成。
  前者目录/list无隐藏描述但用户之后有正文且read成功；后者没有正文注入，read返回
  is_error Observation，SQLite也无禁用正文。不是仅在独立parser中新增一个布尔字段。

完整最终`LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest --tb=short`：
**1142 passed，72.57s**；Ruff check/format --check覆盖268files（包括src/tests下skills），
compileall、pip check、node --check bootstrap.js、git diff --check通过。组合examples
E2E另跑3通过，2.86s。独立wheel：
`/tmp/corki-skill-policy-wheel.KpACoP/corki-0.1.0-py3-none-any.whl`，646208bytes，
SHA-256 `174be02a16f018d557891b8e384bb3bd83dbfa8b5e562e8177adf1579783be03`。
/tmp python -I先加载installed，再跑context/skills/prompting/CLI事件全部单测与visibility、
catalog、warning、skill lifecycle/冷恢复/HTTP、steering、context recall组合：
**151 passed，5.43s**，134个Corki模块全部从安装包加载。未执行真实模型或Rust测试。

README/development已说明控制语义和限制。Codex固定commit仍干净；teach.md SHA-256
仍为816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a，用户已有改动
包括docs/flow.md暂存保持。可选metadata现在基于PyYAML，尚未证明其所有标量类型、
重复key等细节与serde_yaml等价；尤其需继续核对no/off等YAML布尔别名及完整frontmatter
解析。include_instructions全局隐藏、结构化/路径mentions、产品限定来源、完整MCP依赖
准入和来源冻结等仍明确开放。完整A/B/C/D/E与Memory目标保持active，不缩小完成条件。

## 第38批修改前审计：frontmatter容错、类型化YAML及文本显式召回

重新读取完整objective；上轮实际修复及1142测试属于progress。本批不改变A/B/C/D/E
范围。先读skills/src/parser.rs及其全部6个parser_tests、mentions.rs及全部mentions_tests，
并重新核对默认host skills/src/selection.rs（不是实验extension混合catalog首匹配路径）。
loader/host.rs::default_skill_name取父目录名并折叠空白。Codex parser允许缺省/空name，
description只要求非空、不设1024上限，短描述同样完整保存；1024属于render预算阶段。
frontmatter首/尾边界以整行trim后===---判断，而非starts_with或寻找任意\n---前缀。
失败时只对特定未加引号的scalar行修复，保留block scalar、引号和行尾注释；仍失败应
保留原始错误。Corki现有16KiB前缀解码、强制name、1024解析上限、无修复会漏掉有效技能。

默认host召回先路径后plain name，并按发现顺序输出；名称必须精确匹配且无歧义，文本
解析忽略常见环境变量、支持[$name](path)并区分app/mcp/plugin链接。名称字符不含点号；
链接中的名字不能在path失配时偷偷回退plain name。Corki正则只有$token、包含点号、
casefold匹配、按提及顺序，无法正确处理链接或环境变量，属于B05行为不一致P1。
需要保留logical discovery path与canonical identity两者，以匹配目录symlink的显式链接。

上轮YAML标量疑点用agent-reach网页/Jina读取锁定依赖的原始源码核实：Cargo.lock为
serde_yaml 0.9.34+deprecated；[de.rs](https://docs.rs/serde_yaml/0.9.34+deprecated/src/serde_yaml/de.rs.html)
932–937仅接受true/false及固定大小写；1266–1288要求plain或显式bool tag，quoted false
不允许。1472–1500对String直接读取scalar原文而不先转换数字/布尔；1517–1559对Option
区分plain null、quoted null和显式tag。PyYAML safe_load通用构造会混淆这些类型，不能
只改一个bool resolver同时仍拒绝合法String标量。此查询只补本地锁定依赖证据，未更新
任何依赖或用最新产品文档替代本地Codex基准。

计划：用PyYAML安全语法树做少量类型化字段读取，保留scalar原文/style/显式tag，已知
字段重复时报错、未知字段仍忽略；frontmatter完成默认name、完整描述、限定repair。
policy复用相同字段边界，修正no/off、quoted/explicit-tag bool等行为。独立mentions
模块实现默认host文本选择，保存discovery_path且不改已有canonical读路径/启用规则。
先写真实Service/Runtime可暴露的RED，再覆盖Codex原用例和失败/Unicode/目录symlink。
结构化UI输入类型、hosted connector slug歧义计数和完整MCP准入仍另列，不以文本路径
实现假称这些入口已存在；也不把整个YAML规范或所有provider协议宣称逐字节等价。

### 第38批结果：完整技能头解析与默认host文本选择进入Runtime

新增skills/frontmatter.py，parser读取完整UTF-8文档后委托它提取、类型化读取与限定修复；
缺省/空name回退已清洗父目录名，name保留64字符上限，description和short_description
不在解析时截断。---边界改为整行trim相等；有限scalar修复仅在初次语法/字段类型读取
失败后运行，保留block缩进、已有引号和注释，二次失败保留原错误。不是接受任意畸形YAML。
正文skill_read的128KiB预算未因本次metadata读取修改，仍需与host正文读取契约单独核对。

新增yaml_fields.py，使用PyYAML BaseLoader安全语法树而非通用Python值构造，保留scalar
原文/style/显式tag；已知字段重复拒绝、未知字段不解释。policy复用typed字段读取，修正
no/off、quoted false、显式bool/string tag、null和字符串数字/日期的行为；已检查的可选
metadata失败仍只日志回退默认，不删除有效技能。锁定serde_yaml源文件的网页读取直接
影响了这项修正，未更新依赖。整数、别名等仅有实现/源码检查，不宣称完整YAML规范等价，
没有执行Rust比较程序；原utf-8-sig兼容读取也不作为逐字节Codex行为一致证明。

新增skills/mentions.py：默认host文本语法、环境变量排除、链接路径优先与精确无歧义
名称选择；app/mcp/plugin链接不算技能路径，路径不匹配不偷偷回退链接标签。
先路径后名称，各按原snapshot发现顺序。SkillMetadata末尾新增short_description与
discovery_path默认字段，discovery保存逻辑发现路径，canonical identity仍用于正文读取，
catalog展示与显式路径匹配同时支持逻辑symlink。Service.explicit_mentions实际调用新
选择器，未改既有enabled/visible规则、输入正文一次注入、冷恢复、压缩后的生命周期。
没有新增DDL或checkpoint字段；旧构造位置保持兼容。

新增43例（1142→1185）：frontmatter 29、mentions 12、真实Runtime 2。
最初15例RED包括8个有效头遗漏、4个YAML别名policy错误、3个文本选择错误；4个policy
用例最初被缺省name失败遮蔽，已补name并在旧实现上单独重跑，确认实际布尔规则RED。
首轮实现后目标30例通过。旧Runtime恢复/警告组合4失败、14通过，4个失败均是断言沿用
文本提及顺序，按默认host发现顺序修正预期，未改恢复处理去迎合用例。
扩展技能组随后97通过、1失败：新增负例误以为description: {bad: value}不会被修复；
重新核对parser.rs确认类型读取失败后colon+whitespace会触发整行引用。负例改为没有
该触发条件的{bad}，并新增原映射被修复为文本的正例，不修改已符合源码的实现。

新Runtime双场景使用真正Service、输入上下文、模型Step、skill_read、SQLite关闭重开：
[$alpha](skill://beta路径)只注入修复头后的beta而非alpha，忽略$PATH；canonical和
logical symlink分别覆盖。模型下一Step收到beta工具结果，冷恢复后显式$alpha追加新正文，
旧beta ContextItem保持相同身份/内容且不重复。不是独立parser单测代替真实主循环。

最终严格msgpack完整回归：**1185 passed，73.71s**。Ruff check及format --check通过，
覆盖274files（含src/tests的skills）；compileall、pip check、node --check bootstrap.js、
git diff --check通过。组合examples E2E另跑**3 passed，2.71s**。独立wheel：
`/tmp/corki-skill-parsing-wheel.IkZ0MK/corki-0.1.0-py3-none-any.whl`，649433bytes，
SHA-256 `f1f662409e2780ecf421141212ca70df4a3f18140f81472f0eb9db4cb3bb2dc0`。
/tmp python -I先加载installed包再运行context/skills/prompting/CLI单测、所有skill集成、
steering及context/tool recall：**194 passed，6.08s**；137个Corki模块全部来自安装包。
上述模型均scripted/mock，未验证真实模型选择质量、未运行真实远程模型或Rust测试。

README/development和第三方许可说明同步更新。Codex固定commit保持干净，teach.md
SHA-256仍为816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a；用户的
docs/flow.md暂存保持。本批关闭已列文本/解析场景，不关闭整个B05/C01：结构化UI Skill
输入及其blocked-plain-name规则、hosted connector slug歧义检查、完整来源冻结/overlay、
include_instructions、MCP依赖准入、产品限定来源仍开放。A/B/C/D/E完整目标保持active，
压缩、记忆管线及其他跨模块故障窗口的剩余审计/修复没有因本批技能工作被移出目标。

## 第39批修改前审计：自动目录开关与无正文的世界状态基线

完整重读objective，上轮为实质progress；固定Codex commit仍干净，Corki已有dirty内容
包括用户暂存docs/flow.md保留。本批继续B05/C01，不缩小完整A/B/C/D/E目标。
源码链：core/config/mod.rs:3923解析skills.include_instructions缺省true；app-server/
extensions.rs:124映射SkillsExtensionConfig；ext/skills/src/world_state_catalogs.rs的
discover_host_catalog/render_catalogs/build_world_state_section控制目录生成和warning，
world_state.rs::skills_world_state_section同时比较body和includeInstructions。首次无body
不发消息，已有基线后关闭目录发送hidden notice，而不是no-host-skills；保持关闭不重复，
重新开启但无技能发送no-host-skills。host_skill wrapper还区别预算全遗漏状态。
ext/skills/tests/skills_extension.rs的host重复渲染、hidden shadow选择与配置切换案例
映射这些条件。core/session/turn.rs::build_skills_and_plugins独立调用host snapshot的
显式选择/正文加载，无include_skill_instructions门控，所以不能把该开关接成skills.enabled。
Guardian另有session-source隔离，不能以普通host显式可读结论覆盖Guardian路径。

Corki settings尚无include_instructions，SkillContextContributor始终渲染目录；空目录
直接返回()，world_state只用role/body比较，无法记录首次无消息的已知状态，也不能区分
隐藏与没有技能。状态：行为缺失/不一致，P1。已有skills.enabled完全关闭service/tools，
per-skill allow_implicit_invocation控制单项自动可见性，均不能代替全局目录开关。

方案：增加缺省true的skills.include_instructions并接入真实Runtime contributor，仅门控
自动目录，不撤销显式输入/读取或按需skill_list。为ContextItem及prompt contribution末尾
增加可选、feature-owned的snapshot_state比较值，与模型正文分离；旧None字段省略JSON
确保旧payload幂等。空正文但有状态的记录作为静默基线持久化、从模型视图排除。技能目录
按正文与开关共同diff，首次无消息/known-hidden/known-empty分别处理；压缩重建当前状态
不重新注入hidden旧通知。不得用空模型消息或隐藏说明假装所有技能不可用。

验收先写TOML开关被忽略的真实Runtime RED；随后覆盖显式正文+skill_list/read Observation、
多Step不重复、listed→hidden→empty→listed的跨Turn/冷恢复、首次silent基线与压缩、
旧JSON与strict checkpoint兼容，完整测试及独立wheel。全局热配置事件总线、其它typed
sections/来源冻结、结构化mentions和memory等剩余范围仍保持开放。

### 第39批结果：自动目录可隐藏，静默状态进入持久化/恢复/压缩链路

Settings末尾skills_include_instructions缺省true，读取[skills].include_instructions并
严格校验bool；Runtime构造SkillContextContributor时传入。关闭只跳过自动catalog生成，
不删Service，不关闭原有显式输入hook或skill_read/按需skill_list；没有目录预算warning。
per-skill allow_implicit_invocation和skills.enabled仍是不同维度。配置重开Runtime生效，
未伪称实现Codex全量live config event bus或Guardian隔离。

PromptContribution/RenderedPrompt/ContextItem末尾新增snapshot_state可选不可变字符串，
用于feature-owned比较状态；旧None不写入JSON。contribution允许template_name=None
表示没有模型正文，但必须有比较状态；input_scoped不允许携带world-state metadata。
ContextBuilder传递状态，changed_context_items同时比较role、raw body和状态；首次空
正文仍原子追加静默基线，区别unknown/known-empty。技能目录状态为skills.listed/hidden，
known状态后转hidden与转listed-empty分别使用不同通知；稳定状态不重复渲染，旧记录
不回写。保留之前host全遗漏预算说明路径，不把遗漏与没有技能混为一谈。

ContextItem.is_snapshot_only区分静默基线，active_history/provider直接adapter/legacy
load_messages均不提交空消息，token成本为0。比较状态不进入两种HTTP wire格式。
压缩重建新的当前raw state并保留状态字段，空目录只持久化静默记录，不再发送过去的
hidden/unavailable说明；fixed-budget计算先用实际模型视图过滤这些记录。
Legacy消息兼容出口在assistant分组前跳过静默记录，避免无正文记录仍切断同Step合并。
新字段不需DDL，strict msgpack和SQLite payload往返均验证；这是host目录所需的比较
状态，不声称已经实现所有Codex section的完整typed snapshot/diff/retention matcher。

新增/扩展19例（1185→1204）：
- integration/test_skill_catalog_visibility.py 3例：首个TOML false真实Runtime在旧实现
  确认RED（仍自动发送目录）；修复后false+1token预算不发目录/warning，$fixture正文、
  skill_list→skill_read两次工具Observation均成功。另2例分别从listed与silent-empty
  开始，5次真正Runtime冷重开验证listed→hidden→hidden→listed-empty→listed，前缀保留，
  body与内部state数量、不同通知及不重复，首次静默记录实际入SQLite但不在模型请求。
- unit/context/test_silent_catalog_state.py 8例：两组初始模式/切换、旧payload+strict
  checkpoint往返、3组非法设置、真实Window/SQLite压缩重建及冷恢复、legacy同Step合并。
  legacy合并用例在首版过滤位置确为RED（两条助手消息），移到分组前过滤后通过。
- input_recovery扩展4例：include_catalog两值覆盖提交后故障/准备checkpoint冷恢复，
  实际输入正文不重复；两种HTTP provider验证内部状态不泄漏、隐藏时无catalog tags。
- input_lifecycle扩展1例：真正Runtime的大工具Observation触发mid-Turn压缩，隐藏目录
  前后都不发空消息，SQLite有初始/压缩后两个silent baseline，选中旧正文不重新注入。
- unit/models/test_silent_context.py 2例直接adapter payload过滤（不依赖Runtime先过滤）；
  prompting新增1例验证silent模板行为和错误边界。

阶段证据：首批相关119通过；恢复/压缩组合22通过；首轮完整1200通过74.99s（不是最终
版本）。直接Responses新增测试曾误把其input_text结构预期成普通字符串，目标组1失败/
11通过、同期完整1失败/1202通过76.53s；按既有provider契约纠正断言，未改正确wire行为。
最后legacy分组位置修复后目标10通过0.50s；provider+组合examples另跑5通过2.86s。

最终发行包（不用首个未含legacy分组修复的650730byte中间wheel作为最终证据）：
`/tmp/corki-skill-state-final.dq3VRH/corki-0.1.0-py3-none-any.whl`，650739bytes，
SHA-256 `9ac8c46dca590642a6387d7018b136f480e3dcbbfa9e387a93a255daa5f2a7af`。
/tmp python -I先import安装包，运行context/skills/prompting/protocol/CLI单测、新provider
silent测试、所有skill集成、steering/context recall：**218 passed，7.75s**，137个Corki
模块全部来自installed目录。模型全部scripted/mock，未运行真实模型或Rust测试。

README/development/许可说明同步。teach.md SHA-256仍为
816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a，Codex固定commit保持
干净，用户docs/flow.md暂存保持。完整A/B/C/D/E目标保持active；来源冻结、结构化技能
输入、MCP依赖、手动/远程压缩、完整memory管线和其他故障窗口的剩余项未移出范围。

最终严格msgpack全套：**1204 passed，74.37s**。Ruff check和format --check通过，
277files（包含实际src/tests skills）；compileall、pip check、node --check bootstrap.js、
git diff --check通过。上述最终完整结果包含最终legacy分组修复与修正后的Responses断言，
不以中间版本通过数代替最终版本验收。

## 第40批修改前审计：本地手动压缩的独立Turn、持久化及恢复

重新读取完整objective，上轮为progress，Codex固定commit干净；不修改teach.md或用户
暂存文件。先追踪core/session/handlers.rs::compact→tasks/mod.rs::spawn_task（替换并
取消旧任务）→tasks/compact.rs::CompactTask。TokenBudget实验路径优先；否则按provider
RemoteCompactionSupport/V2 feature选择远程，本批对齐Unsupported的本地请求兼容路径，
不把本地功能称作远程原生协议完成。

core/compact.rs::run_compact_task发TurnStarted，内层使用当前历史+临时summarization
prompt、创建时base instructions、空工具；复用独立压缩重试规则。成功使用DoNotInject，
只保留预算内真实用户文本+最后摘要，reference context/world-state baseline清除；下一
普通Turn重新注入。即使无历史仍请求摘要（suite/compact.rs:5158测试）。同文件manual
twice/custom prompt/creation-time instructions和retry相关测试作为验收映射。
tasks/compact.rs有吞掉部分非取消错误后返回Ok的现状，suite/compact.rs:3840明确标注
known-incorrect并忽略对应测试；本目标明确禁止失败标成功，因此Corki须保留TurnFailed，
记录这项有源码依据的有意差异，而非复制已知错误。

Corki当前只有Window.prepare阈值自动压缩；无Runtime.compact或/compact，缺失P1（C03/A/E）。
不能把用户命令作为普通文本输入后希望模型自主总结，也不能用强制小阈值替代独立任务。
计划复用现有TurnRun、事件队列、取消/关闭、LangGraph checkpoint路径，增加compact入口
节点和持久化TurnRecord.operation（旧行默认normal）；没有checkpoint时也能恢复正确任务。
手动任务不构建/注入新的world state、技能正文或工具，复用已有摘要重试；成功marker+
retained users原子追加，提交后checkpoint前恢复通过同Turn marker去重，不再采样。
空历史marker through_item_id允许None，不制造指向不存在历史的UUID；既有字段/payload兼容。
支持配置compact_prompt并复用到自动路径；新前后事件与CLI命令接入，不扩张成远程压缩。

验收：先真实Runtime无入口RED；空/非空与重复压缩→后续正常工具/上下文循环；故障/取消
保留旧历史；提交前后冷恢复和无checkpoint恢复；运行中替换、queue1 backpressure/关闭；
旧DB迁移、custom prompt、CLI不将/compact发送给模型。全量/静态/组合与wheel验证。
完整token校准、远程/TokenBudget压缩、完整memory与其它A/B/C/D/E未关闭项继续保持。

### 第40批结果：本地手动压缩进入CLI/Runtime/持久化恢复

AgentRuntime/LangGraphRuntime提供compact()事件流；stream与compact复用_start_turn、
TurnRun、事件队列、终态持久化和资源清理，compact先请求取消已有工作。新增core/
compaction.py节点，由可恢复operation从START路由，直接到END，不经过普通模型/工具
执行/技能注入；不创建真实UserMessageItem。stream仍要求str，不能用None偷偷变成compact。
开始ContextCompactionStarted→成功ContextCompacted→临时warning→空答案TurnCompleted；
失败沿既有边界返回TurnFailed，取消沿TurnCancelled，不把错误或无有效摘要标成成功。

Window.compact复用_summarize的独立重试/输入缩减规则，base_instructions通过builder
缓存模板取得，不刷新项目、技能等world-state贡献。顶层compact_prompt字符串传至窗口，
strip后空串回退默认，同样作用于自动摘要。手动操作不检查auto阈值，空历史也请求模型。
成功只追加marker+预算内历史用户文本，summary位于模型视图末尾，DoNotInject清空旧
基线；下一普通Turn提供当前context，deferred工具按压缩后历史重新发现。未复制source
的20k用户保留预算之外全部token/原生provider usage细节，现有估算差异仍属C02开放项。

TurnRecord末尾operation默认normal、校验normal/compact；旧SQLite turns表在既有
BEGIN IMMEDIATE迁移锁内增列，旧行默认normal，不回写旧conversation payload。
无checkpoint恢复读取持久化operation，避免把空字符串当正常用户输入。已有同Turn
CompactionItem时恢复直接采用安装结果，不再采样；提交前失败可重新采样，无工具
副作用重放。marker+retained records一次事务；空历史through_item_id为None，不伪造
指向不存在历史的UUID，旧非空指针编码不变。取消在提交后不能撤销已经持久化的摘要。

CLI /compact不作为普通消息或realtime steering提交；运行中收到该命令，先取消旧Turn，
保留pending命令再开启独立压缩。成功显示开始/压缩notice，不伪造助手回复；失败显示
错误。共享help和事件渲染已接入真实application，不是只提供未调用的Runtime方法。

新增20例（1204→1224）：
- test_manual_compaction.py 8：原无入口真实RED（AttributeError）；空历史→普通Turn，
  搜索→deferred定义→实际工具Observation→重复手动压缩→再次普通Turn（定义重置，
  tool_search仍在，context重新注入，旧历史前缀不变），TOML custom prompt/空工具；
  auth/空完成/非Model异常×空/非空历史6例，重试边界、失败无marker、不标成功。
- recovery 4：无checkpoint、摘要提交前、提交后节点checkpoint前、节点后。四组真正
  SQLite/checkpoint冷重开，已提交摘要不重新采样、不生成虚假用户输入；未提交可重试。
- lifecycle 4：queue1取消/Runtime关闭时关闭流并保留旧历史；替换有活跃消费者的普通
  Turn；未消费事件的queue1在marker提交后关闭不死锁、不回滚既有marker。
- SQLite旧turn表迁移1，CLI成功/失败路由2，真实Runtime realtime /compact端到端1。
初次失败边界测试3例使用0退避违反原Settings的正数要求，修正为0.001后通过；不是
为通过测试放宽配置校验。中途apply_patch有一次上下文匹配失败，未部分应用，核实后重试。

最后严格msgpack完整回归：**1224 passed，78.53s**（中间1220通过77.42s不作最终数）。
Ruff check/format --check通过，282files；compileall、pip check、node --check bootstrap.js、
git diff --check通过。组合examples另跑**3 passed，2.69s**。独立wheel：
`/tmp/corki-manual-compact-wheel.3RQvpK/corki-0.1.0-py3-none-any.whl`，653222bytes，
SHA-256 `b5d54617b7496e28beb1f5cd675615983398135c50d85fdedaed73dd8fd07bd1`。
/tmp python -I先加载installed，再跑context/skills/prompting/protocol/CLI/storage单测、
silent provider、所有skills/manual集成、deferred search/steering/context recall：
**287 passed，9.75s**；138个Corki模块全部来自安装包。全部模型scripted/mock，未运行
真实模型/Rust/原生远程compact endpoint；README/development/许可说明已更新。

**新增确认的A/E准入缺口（下一批优先）**：当前_turn_lock仍由事件iterator持有，
而非后台工作任务持有。在临时SQLite诊断中只消费旧stream的TurnStarted，让模型进入
等待，然后并发compact；权威old_run.done已完成，old_run.result为TurnCancelled，
新compact的anext仍未完成（50ms观察）；显式关闭旧iterator后立即得到TurnStarted并
正常TurnCompleted。这是已终止工作与未关闭观察者耦合，不是模型仍运行或外部等待。
文档明确事件iterator需消费/关闭，但该调用约束不算与Codex spawn_task所有权一致。
本批不把它隐藏为可接受差异；需将调度准入绑定工作完成，并验证旧observer晚关闭不会
取消新任务/释放新任务的锁。源码tests与全绿不能替代此缺口的修复证据。

因此只交付已验证的手动路径进展，不关闭整个A/C03/E；remote/TokenBudget、完整usage、
hooks/source freeze、Memory和其余全目标范围继续active。Codex固定commit保持干净；
teach.md SHA-256仍为816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a，
用户docs/flow.md暂存保持，未创建提交或扩大外部权限。

## 第41批修改前审计：准入等待工作完成，而不是事件观察者关闭

重读完整objective，上轮为progress。Codex固定commit干净；源码再次核对tasks/mod.rs
spawn_task→abort_all_tasks/handle_task_abort→start_task，以及on_task_finished释放task。
后台task负责统一完成/取消，UI读取事件不拥有执行准入。Corki TurnRun已有独立worker、
done和不占queue槽的terminal；但Runtime._start_turn/resume_pending把_turn_lock持有到
async generator结束，造成第40批已复现的observer阻塞，状态A/E不一致P1。

方案：_turn_lock仅序列化准入，持锁时等待上一TurnRun.done（包括终态持久化和资源清理），
然后在_lifecycle_lock下注册新run；事件yield放到准入锁之外。不能直接删锁允许两个
活跃worker重叠。等待done必须可取消且不能持lifecycle锁，否则关闭会死锁。
compact在请求时和获得准入后分别检查取消当前工作，避免其排队期间前一请求已启动
新任务却不被替换。旧observer继续持有自己的run，晚关闭只join/cancel该run；_active_run
清理继续用identity判断，不释放新任务准入或触碰新任务realtime/CodeMode状态。
不增加持久化字段或削弱backpressure，保留真正worker仍活跃时普通请求排队的语义。

验收先复现旧iterator停在Started或terminal仍阻挡compact/普通请求的RED；覆盖新任务
运行期间旧iterator晚关闭、多请求排队、取消准入等待者、Runtime关闭、resume_pending、
终态持久化/cleanup未结束不提前放行、queue1取消。完整A/B/C/D/E目标不缩小。

### 第41批结果：旧观察者不再持有新任务准入

Runtime._start_turn和resume_pending在_turn_lock内等待前一TurnRun.done，再持
_lifecycle_lock完成初始化/持久化初始Turn/注册worker；随后释放准入锁才yield事件。
不是直接删除锁或让两个worker并行。done由原TurnRun在operation返回、终态保存、
CodeMode/realtime清理全部结束后发布。等待done不持lifecycle锁，关闭能取消前一任务
并回收资源；等待者自己的取消不会传播到前一worker，且会释放准入锁。

compact入口保留即时cancel_active，并在获得准入锁后再次取消真正current worker，
覆盖等待期间另一个先排队请求已启动的情况。原_run_graph的observer只操作自己捕获的
run；晚关闭时identity判断仍防止清空新_active_run。无新的lease/token手工释放路径，
不让旧observer去释放当前准入锁。没有DDL、checkpoint/payload字段或工具权限变更。
未消费但仍阻塞在数据queue的worker仍算活跃：普通请求等待；显式compact/close可取消，
terminal不受队列槽限制。这与“worker结束但observer未关闭”的已修复问题区别处理。

新增integration/test_turn_admission.py 9例（1224→1233）：
- 两个原始RED：旧流停在Started（compact替换）或TurnCompleted（普通新Turn），
  old_run.done已终态而新anext仍超时。修复后新任务开始，不关闭旧流；第二个worker
  被测试闸门保持活跃，第三请求排队，此时关闭旧流不会取消第二任务/关闭新realtime/
  误放行第三请求；最终三个任务按序完成。
- 两组真实终态写入/CodeMode清理await闸门：前一done未发布时新请求不准入，完成后
  才继续。最初使用缺少registry的Cleanup替身，在实际工具刷新阶段已失败，未到闸门；
  相关1失败/8通过，以及同期全套1失败/1232通过79.59s。修正测试为真实CodeModeService
  子类，仅在实际deactivate前增加闸门，再调用super，不为替身添加生产特殊分支。
- old→queued normal→compact：compact等待期间真正current改变，获得锁后二次取消，
  两个旧observer都晚关闭仍不干扰新压缩。
- resume_pending两组：旧工作completed无需恢复，或终态写入一次失败、已提交model/
  checkpoint恢复补终态；均无需旧流消费，不重复模型采样。
- 准入等待者取消不取消前一worker；Runtime关闭可完成前一取消并使等待者收到closed，
  不持lifecycle锁造成死锁。

最终严格msgpack完整回归：**1233 passed，77.13s**。Ruff check/format --check通过，
283files；compileall、pip check、node --check bootstrap.js、git diff --check通过。
组合examples另跑**3 passed，2.89s**。独立wheel：
`/tmp/corki-turn-admission-wheel.R9Wrqn/corki-0.1.0-py3-none-any.whl`，653489bytes，
SHA-256 `4d29f2bd6413a5f1c1e820ff3c4d9f80241bdcf218c846844c00576b0b603e40`。
/tmp python -I先加载installed，再运行上批287项组合并加入准入、runtime shutdown及
CodeMode lifecycle：**326 passed，13.73s**，138个Corki模块全部来自安装包。
未执行真实模型或Rust测试；scripted/mock只证明Harness行为，不证明工具选择质量。

README/development移除旧观察者阻塞准入的未实现描述，保留消费/关闭iterator用于释放
观察数据的责任；顶部A08、C01、C03矩阵同步到最新证据。第40批明确复现的准入缺口在
以上场景关闭；不外推所有任务优先级、强制隔离、终态/清理异常或完整配置快照均一致。
完整A/B/C/D/E目标继续active，尚有remote/TokenBudget、usage、来源冻结、Memory等
明确剩余范围。Codex固定commit干净，teach.md SHA-256仍为
816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a；用户docs/flow.md
暂存保持，未创建提交或改变外部权限。

## 第四十二批实施前审计：长期记忆来源领取与空结果失效（D01/D02/D04）

本批基准仍为干净的Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc。
`memories/write/src/start.rs::start_memories_startup_task`检查ephemeral、MemoryTool与
non-root来源，然后后台prune→quota guard→phase1→phase2；`phase1.rs::claim_startup_jobs`
传入interactive来源、当前Thread排除、5000扫描上限、配置年龄/空闲/领取上限。
`state/src/runtime/memories.rs::claim_stage1_jobs_for_startup`136–292先按毫秒更新时间
筛选未归档enabled来源并截断扫描，再检查记忆库；没有成功Turn门槛。
`try_claim_stage1_job`658–857在BEGIN IMMEDIATE内检查output/last_success >= 来源版本，
全局有效running lease数量小于max_claimed；同源最多3次失败，仅更新来源版本才能重置
耗尽/退避，过期接管不消耗失败次数。`mark_stage1_job_failed`1008起按owner递减。
`phase1.rs::job::run`任一raw/summary为空都算成功无输出；
`mark_stage1_job_succeeded_no_output`删除旧stage1并在实际删除时enqueue phase2。
相关源码测试覆盖毫秒idle边界、先扫描再probe、全局cap、跨启动批次与空输出删除。

Corki真实入口为Runtime._ensure_ready→LongTermMemoryService.start/run_once→
SQLiteMemoryRepository。修改前存在以下已确认缺口：

| 行为 | 修改前差异与影响 | 验收 |
| --- | --- | --- |
| 来源/窗口 | 强制有completed Turn；datetime丢失毫秒；scan=max(limit*8,limit)，且先排除已有output | 失败/取消/无Turn来源、精确年龄/idle边界、5000扫描先于记忆probe |
| 全局领取 | 每次调用只限制自己领取数量；多个Runtime可超额采样 | 独立repository并发共享cap，过期lease不占容量 |
| 重试与水位 | 无次数上限；仅判断source相等和当前status，无独立成功水位 | 3次失败停止，更新来源恢复，旧/相等来源不重置；无输出成功跨重启去重 |
| 空结果失效 | 任一为空另一非空时报错；两者为空时旧output仍保留 | 真实Runtime提取空结果删除旧stage1、同步移除旧summary、触发合并，重复启动不重采样 |

采用已有SQLite事务/Runtime链，不建立另一套任务调度。新增retry_remaining和成功来源水位
要兼容旧库；旧失败次数未记录，不能伪造，迁移从3开始并保留既有退避/lease。
本批不把source/archive/ephemeral模型缺失判为“不适用”；仍须继续完整审计这些入口，
也不声称完成retention、quota、phase2原生subagent或外部污染策略。先写RED再修改实现。

### 第四十二批实施结果：来源领取、重试水位与空提取失效已接入

修复前 `tests/unit/memory/test_extraction_claims.py` **11项全部失败**：无成功Turn来源、
毫秒窗口、共享cap、重试耗尽、成功水位、空结果删除和两个扫描顺序案例均暴露实际差异。
Runtime测试再复现**5项失败、2项通过**：原completed/心跳隔离仍通过，失败/取消来源
未被提取；新空结果继续保留旧MEMORY，两种部分为空被错误计为失败。

`memory/extraction.py`现在拥有事务内的来源筛选/领取；SQLiteRepository保留原异步port，
不绕过Runtime，也不在prompt里模拟领取。取当前时钟的毫秒cutoff，与旧CURRENT_TIMESTAMP/
当前ISO存储兼容；先选最新5000个enabled非当前来源，再检查output和成功水位是否>=来源。
BEGIN IMMEDIATE覆盖容量检查、owner分配和历史快照，两个独立repository并发共用cap。
有效running lease占容量，过期接管换token且不消耗失败次数；相等/倒退版本不重置预算，
真正新版本才恢复3次并绕过backoff。DEFAULT年龄10天、idle6小时、startup2与固定
Codex config/src/types.rs:48–50相同；这不是把其测试中的max_claimed=64误当生产默认。

新增retry_remaining、last_success_source_updated_at两列；executescript之后重新取得
BEGIN IMMEDIATE执行加列，4个并发旧库打开者通过；保留旧owner/lease/backoff及原列内容。
旧succeeded来源迁移成功水位，旧失败次数从3开始（没有足够历史重建，不捏造次数）。
新版本失败后旧成功水位仍保留，冷重开/来源倒退都不会重复提取旧成功版本；失败写入受
running+owner限制，迟到重复fail不再次扣次数。Phase2失败逻辑不顺带应用Stage1预算。

`complete_extraction(None)`在同一事务删除该Thread旧stage1，只有实际输入变更才enqueue
合并，再推进成功来源水位。pipeline将任一raw/summary为空归为成功无输出。
真实Runtime的新测试通过三个冷重开阶段：旧来源生成/发布→新版本空提取删除旧输入/
rollout summary并重新合并→相同来源及workspace无重采样。两种部分为空也走此链，模型
iterator全部关闭；文件变化来自真实service/repository/artifacts，而非测试直接删产物。
原generation→summary→search→read→citation/usage闭环扩展到失败/取消来源。

最终全量 `LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest --tb=short`：
**1251 passed，79.81s**（较上批新增18项：13个repository与5个Runtime场景）。
新增迁移与后续失败水位测试包含在全量；首轮52个Memory相关用例也通过。
Ruff check/format **286 files**、compileall、pip check、Node bootstrap语法、git diff --check
通过；三个独立进程示例通过。首次Ruff在格式化前报长行/未使用import，格式化及拆SQL后
复检通过，不把首次检查描述为绿色。

剩余D范围仍为真实开放项：Thread完整source/archive/ephemeral与启动quota/prune、
stage1内容过滤/结构化历史/输出空白与optional slug处理、retention/usage排序、Phase2
原生subagent/冷却/水位/工作区完整策略、显式更新遗忘及污染控制。本批没有修复所有历史
旧库曾经产生的错误空输出产物；正常新来源提取才会沿已验证失效路径处理。不将这些限制
改标“不适用”，也不宣称D或整个Harness完成。teach.md与用户暂存docs/flow.md保持不变。

最终wheel：`/tmp/corki-memory-claims-final.YSf6Jr/corki-0.1.0-py3-none-any.whl`，
655367 bytes，SHA-256 `2ec31beb75dc202224f366b498c0d87cfda84fbf2cc55a29ec9dab85d0fbc6c7`。
安装至同目录installed，从`/tmp`用`python -I`运行前批context/skills/CLI/Runtime/恢复等
选择并加入unit/memory和三个memory integration文件：**384项全部通过**，进程exit 0；
139个Corki模块全部来自installed，未借用工作树模块。初次与补充第三方说明后的wheel相同
hash（说明文档不进入当前wheel文件集合），实际安装验收的是上述最终路径。
未执行Rust或真实模型采样；scripted场景证明harness和持久化/产物链，不证明提取质量。

## 第四十三批实施前审计：记忆保留、反馈与合并输入（D02/D03/D04）

先读固定Codex `memories/write/start.rs`：后台启动先phase1::prune（错误只告警）再提取/
合并；`phase1.rs::prune`每次最多200行。`state/runtime/memories.rs`391–430的清理只删除
selected_for_phase2=0且COALESCE(last_usage,source_updated_at)早于cutoff的记录，按
recency/source/thread ASC删除最旧者，保留jobs成功水位，不把generated_at当recency。
`get_phase2_input_selection`433–542仅选择enabled来源和至少一个非空白raw/summary，
以usage_count DESC、COALESCE(last_usage,source_updated_at) DESC、source_updated_at DESC、
thread_id DESC选Top-N，再按thread_id ASC返回。Codex分库需要分页跳过disabled；Corki
同库可JOIN后LIMIT，必须得到同一有效Top-N，不能让被过滤的候选占名额。
`mark_stage1_job_succeeded`不清除上一轮selected标记/版本；新的phase2成功才重写baseline。
`record_stage1_output_usage`55–84逐次计数，重复ID也是独立使用信号，同批固定一个now。
源码测试已核实：优先source而非generated、污染来源排除、stale used/fresh unused、
batch prune、保留selected和jobs、刷新后仍保留旧selected版本，以及重复usage计数。

Corki修改前：load_consolidation_inputs按generated_at保留/排序，没有JOIN enabled、空白
检查、完整tie-break或排序后返回契约；没有后台prune；提取刷新把selected标记清零；
usage入口无条件去重且CURRENT_TIMESTAMP在每条SQL求值。影响包括旧来源因刚提取而
被重新当成新知识、禁用/污染旧结果进入新合并、旧baseline失去清理保护、过期行积累。

拟修改：现有MemoryRepository增加有界prune方法并接run_once，失败告警继续、取消传播；
真实SQLite选择查询修正recency/可见性/排名和稳定返回；保留提取更新前的selected baseline；
usage按输入事件累计，缺失ID忽略。清理不改conversation或jobs，不调用模型、不直接删
已发布MEMORY；合并依旧负责产物更新。新增测试先RED，覆盖确定性cutoff、top-N排除、
retention上限与baseline跨刷新、usage重复/缺失，以及Runtime启动清理→合并/失败隔离。
Phase2锁的完整cooldown/dirty gate、stage1原始输入投影等继续开放，不在本批宣称完整D对齐。

### 第四十三批实施结果：source/use retention与合并选择接入实际Runtime

修复前6个repository场景**全部RED**：generated recency错误、disabled/polluted/空白
占Top-N、thread tie-break错误、缺少prune、提取刷新误清selected、usage重复信号丢失。
再增加3个Runtime场景**全部RED**：未清理/过滤导致合并输入错误，prune错误与取消入口
根本未被调用。修复后首轮相关63项通过，另补实际清理写线程的Runtime关闭验收。

`MemoryRepository.prune_stage_one_outputs(max_unused_days,limit)`已接
LongTermMemoryService.run_once最前端，固定200行一批。SQLite删除语句单事务选择最旧
unselected过期行，仅按last_used_at/source_updated_at判断，不删jobs或conversation，
不直接改写MEMORY。错误进入warnings后继续提取/合并，CancelledError不捕获为普通错误。
SQLiteRepository复用泛型化_joined_write：取消必须等已启动写线程退出，Runtime关闭
不会先销毁资源或留下继续写库的worker；不会把一次取消清理当成模型失败/重试。

合并查询JOIN线程enabled过滤，raw或summary至少一个trim非空；同库直接先过滤再排名，
等价于Codex分库分页补足有效Top-N，而非先LIMIT再移除污染候选。recency、source和
thread-id完整tie-break接入；选出Top-N后按thread ASC返回。输出生成时间不再延长来源
保留期。提取刷新保留上轮selected标记与精确版本，只有合并成功重写；stale selected
仍不进入本轮输入，成功更新baseline后下次prune才可删除。Usage逐输入信号累加，同批
捕获一个秒级UTC时间，缺失ID忽略；原citation解析本身的去重与恢复路径未顺带改写。

新单元测试验证严格cutoff边界、最旧批次/limit0、selected保护、jobs逐字段不变、清理后
成功来源不被重采样、来源刷新/合并成功/prune序列与重复usage恢复recency。旧ownership
测试原来使用不可解析的old-version/new-version，改为真实ISO时间并断言实际选中1条；
避免新筛选下空集合导致的假通过，不改变被验证的“只标记精确selected版本”契约。

Runtime测试以204条来源跨两次冷启动：首次只清理200条，stale selected留库但不入模，
污染来源不占有效输入；真实合并只收到valid来源，并由随后普通Turn读到新routing index。
第二次清理剩余过期行，全部204个stage1 job水位仍在，未重新采样/重复合并。故障注入
证明prune错误后主Turn及合并均可成功，prune取消不被吞；真实线程暂停清理时调用
Runtime.aclose，关闭等待worker结束，模型无新采样、无残留写线程。

最终全量：`LANGGRAPH_STRICT_MSGPACK=true .venv/bin/pytest --tb=short`
**1261 passed，78.20s**（新增10项：6 repository、4 Runtime）；三个进程示例
**3 passed，2.83s**。Ruff check/format **288 files**、compileall、pip check、
Node bootstrap语法及git diff --check通过。首次Ruff发现新测试SQL长行，拆分后复检通过。
本批无数据库列/checkpoint迁移，新增的是MemoryRepository的prune端口与已接入实现。
README/development和顶部D02/D03/D04同步本批结论，不只追加流水账。

仍不等于完整Memory对齐：生成关闭/ephemeral/source/archive/quota启动组合、完整stage1
内容与输出协议、Phase2 cooldown/dirty gate/原生subagent、显式更新遗忘、引用恢复和全部
污染策略仍开放。清理成功并不证明发布文件已经遗忘；文件失效仍经真实后续合并，失败时
可能保留上一代文件，这是必须继续审计的发布边界。完整A/B/C/D/E目标保持active。

安装包验收：`/tmp/corki-memory-retention-wheel.Ugcgh8/corki-0.1.0-py3-none-any.whl`，
655908 bytes，SHA-256 `761d1566ec9dedd28080c7e540fcf27f34cfed806978749b77ef66ba47cec241`。
在同目录installed安装，从`/tmp`以`python -I`运行前批384项选择加本批10项：
**394 passed，17.79s**，139个Corki模块全部来自wheel。没有执行真实模型或Rust；
这些证据验证harness筛选/持久化/产物/关闭链，不证明真实模型的记忆质量。
Codex基准未改；teach.md SHA-256仍为
816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a；用户docs/flow.md
暂存保持，未改真实用户记忆库、未创建提交。

## 第四十四批实施前审计：Phase2领取、退避、冷却与工作区触发（D02/D04/E03）

固定Codex `state/runtime/memories.rs::try_claim_global_phase2_job`1070–1210：缺少行时
直接创建/领取；水位不作为dirty gate；先检查retry_at，再有效running lease，再无error且
finished_at处于成功后6小时内的cooldown。入队保留finished_at/error，只有非running时
清retry_at，重置可用retry计数，并确保input_watermark严格递增（小于/等于旧值则+1）。
`mark_global_phase2_job_succeeded_row`写成功时间及单调完成水位；新输入在运行期到达
也不绕过成功冷却。失败记录时间/error，递减计数但Phase2领取不以次数耗尽永久禁用，
这和Stage1的3次硬限制不同。已阅读fresh/missing/stale runner、cooldown、monotonic
enqueue等相关测试。

`memories/write/phase2.rs::run`先claim，再同步DB输入/扩展工作区，检查diff和产物合法性；
无变化且产物有效才跳过模型。`workspace.rs::validate_consolidation_artifacts`要求真实
MEMORY.md与首行v1的summary。`storage.rs`空输入也写No raw memories yet。因此干净新库
缺少产物仍需要一次合并初始化，不是“无stage1永不合并”。阶段失败不能假装生成成功。

Corki修改前：缺行/完成水位相等都拒绝claim；无finished_at/cooldown；run_once不论是否
真正新增输入，都根据stage1/扩展文件存在性enqueue，改变failed为pending使退避检查失效。
所有源删光或只修改已有MEMORY时可能永远不检查工作区，过期watermark不是dirty证据。

拟实现：事务性缺行领取+六小时成功冷却；真实新增输入/显式enqueue才调整水位/退避，
移除startup无条件enqueue；claim后复用真实workspace指纹/校验与隔离模型合并。
新增finished_at兼容旧库，不把旧updated_at猜成成功时间。补新空库、无DB输入但已有文件
变化、删除最后输入、失败后重复启动、冷却边界/新输入不绕过、过期接管/旧owner写入测试。
旧测试中立即第二次成功合并必须显式模拟cooldown已过，不新增关闭cooldown的生产后门。
这批仍不等于原生subagent/完整git diff协议已对齐，其他A/B/C/D/E开放项不缩减。

### 第四十四批实施结果：工作区驱动合并与六小时成功冷却

新增5个repository policy场景、4个Runtime场景先运行：**9项全部RED**。覆盖缺行领取、
共享锁、冷却期间新输入、相等水位不阻止后续检查、status独立退避、重复enqueue单调递增、
过期接管/旧owner、Phase2失败不永久耗尽，以及空库/仅文件修改/最后note删除/重复启动。
实现后9项全部通过。随后旧Memory相关回归中17项失败，逐项核对源码后更新原先立即
重复合并/空库不初始化的前提；没有为了保留测试而关闭或参数化生产冷却。

`memory/consolidation.py`保存真正共享的claim/enqueue策略；SQLiteRepository的BEGIN
IMMEDIATE负责并发原子性。缺少global行可直接领取，等于/落后完成水位仍可以检查工作区。
先retry_at再有效running lease再成功6小时cooldown，随后绑定新owner并清finished_at。
Phase2成功统一status=succeeded并记录秒级finished_at，即便运行中有新input也受冷却；
完成水位保持单调。失败保留error/退避并记录完成时间，retry_remaining递减到0但不将
Stage1硬重试上限套到Phase2。显式enqueue与提取结果变化复用同一SQL：非running清退避、
保留成功时间/error，水位在candidate不大于现值时+1，running owner不被替换。

run_once移除“有stage1/extension文件就enqueue”的启动旁路，直接领取后进入已有workspace
sync→fingerprint/artifact验证→隔离model→受控发布。新空库缺产物会正常初始化；没有
新DB输入但文件编辑、summary损坏或最后note删除，也在冷却后检测并修复。失败重复启动
不会虚构新输入来清退避。兼容force参数只保留调用形状，不再作为空库/冷却绕过开关。

新增finished_at列保持旧库原字段/owner/lease/backoff不变，不把updated_at猜成成功时间。
额外4种legacy状态(succeeded/pending/running/failed)验证：未知成功时间正常领取以检查
workspace，已有有效租约/退避仍被尊重。旧Stage1迁移测试相应检查三列而非两列，并保留
原字段逐项比较。没有checkpoint或模型事件迁移。

workspace故障/副作用测试显式使持久化成功时间过期，再触发原来的写失败、坏产物、
symlink、采样期间新note与真实子进程exit窗口。退出恢复仍不重采样；文件变化没有虚构
DB水位，因此原“completed_watermark必须增大”改为保持不变且status成功，真实文件
与baseline/模型次数断言保留。空提取测试现在提供独立初始化合并响应，不吞异常假通过。
新增Runtime policy用可控时钟验证6小时边界、文件修改受冷却、到期合并及下一期无变化
不采样；失败后重启等60秒退避，正常主Turn始终成功。

首轮全量（新增legacy参数用例前）**1270 passed，78.54s**；legacy四种状态随后通过，
最终完整全量为**1274 passed，79.35s**（较上批新增13项）。Ruff check/format **291 files**、compileall、pip check、
Node bootstrap及git diff --check通过；三个独立进程示例**3 passed，2.88s**。
README/development与第三方来源说明同步本批可观察默认行为及安装声明。

范围仍完整active：stage1结构化内容/输出/完整来源、Phase2原生subagent/git diff内容协议、
unowned失败兜底/控制面清理、引用恢复/显式遗忘/污染控制和全部A/B/C/E开放项继续审计。
没有用新默认请求的mock通过来宣称真实提取质量、provider原生能力或整个Memory已完成。

最终wheel：`/tmp/corki-memory-phase2-wheel.o9mTl8/corki-0.1.0-py3-none-any.whl`，
657548 bytes，SHA-256 `50350af40d53263fc4321f6dff68f6c17d4e2b482b05594ee21fc8ba97427cad`。
安装至同目录installed，从`/tmp`使用`python -I`运行上批394项选择加本批13项：
**407 passed，18.17s**，140个Corki模块全部来自安装包。
未执行真实模型或Rust；Codex固定commit干净，teach.md SHA-256仍为
816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a；用户docs/flow.md
暂存保持，未修改真实记忆库、未创建提交或外部写操作。

## 第四十五批实施前审计：提取输入的结构、过滤与压缩副本来源（C04/D01/D02）

先读Codex memories/write/phase1.rs::serialize_filtered_rollout_response_items 404起：
从raw rollout筛选ResponseItem及InterAgentCommunication，排除WorldState、RetainedContext、
Compacted/TurnContext/events等元记录；rollout/policy.rs:69还排除reasoning、configuration、
image-generation和compaction类项。Message排除developer；user content逐块去掉首尾
完整匹配的AGENTS.md instructions/<skill>，ASCII大小写不敏感且忽略首尾空白，其他用户
内容/媒体保留。随后序列化为结构化JSON再best-effort脱敏。源码memory prompt将其视为
历史数据、明确禁止跟随其中的指令。tool call/output的关联ID和结构不会被改成无ID文本。

Corki pipeline._render_transcript目前只拼USER/ASSISTANT/TOOL_CALL/TOOL_RESULT文本：
丢失call_id、错误标志、phase、discovered tools和有序媒体；未过滤伪装为普通user的
完整AGENTS/skill片段。ContextItem/reasoning/CompactionItem本体已被忽略，但压缩持久化的
retained UserMessage副本无来源标记，因此会重复提取。不能跳过marker后全部replacement：
window.prepare的pending_after_checkpoint里也有尚未持久化的新用户输入。

拟采用provider-neutral结构化档案投影（不是伪造历史native wire协议），保留规范化协议
字段/调用身份/发现定义/有序内容，过滤world-state/reasoning和指定user片段；标注新生成
retained UserMessage的原item ID，提取跳过明确副本但不丢首次写入的pending User。
新字段None不写入旧JSON，旧checkpoint可默认构造；没有来源证据的旧副本不猜测删除。
脱敏保持现有best-effort覆盖，但在JSON编码前处理字段以避免破坏转义结构。Codex完整
sanitizer模式、StageOneOutput可选slug/空白和模型窗口70%预算仍是待对齐的独立合同，
不在本批宣称已一致。先编写字段/过滤/副本和真实Runtime压缩→提取测试再改实现。

## 第四十五批实施与验证：结构化提取档案及压缩来源（C04/D01/D02）

Codex固定基准不变。实际文件为codex-rs/memories/write/src/phase1.rs:404–490，
以及codex-rs/rollout/src/policy.rs:69–94；后者保留function/custom/tool-search等调用/
结果，排除reasoning/configuration/compaction等项。phase1逐块过滤user标记片段，
排除WorldState和RetainedContext；本批未把主模型active window误作原始提取档案。

修改前新增12个unit用例：首次一个freeform fixture误传{}，修为合法None后，11个
因非JSON旧转录失败、1个因缺retained_from_id失败。真实Runtime三条链路最初一个
fixture误读CompactionItem.content，修正类型判断后，三条均因来源字段缺失失败。
这些fixture问题单独记录，不冒充产品RED。产品修复后首组15项通过；增加媒体、旧
checkpoint和数据库重开/幂等写入共4项，共19项通过。补充媒体测试一度误用url键，
改为既有ImageAttachment.data_url合同；未因此改变产品协议。

实现：

- memory/transcript.py::render_transcript由pipeline._extract经_render_transcript真实调用。
  使用item_kind+item_to_payload的provider-neutral JSON数组，保留call/result关联ID、
  raw freeform输入、错误、phase、发现定义、有序text/image/audio；不声称是Responses
  原生wire日志。ToolResult剔除UI display_content和state_update；权威content_items
  存在时剔除旧content/attachments，避免重复媒体或重新混入已过滤文字。
- 排除ContextItem、ReasoningItem、CompactionItem和明确retained副本；user的每个
  TextContent独立匹配AGENTS/skill完整首尾、ASCII大小写不敏感、首尾空白可忽略。
  未完整标记、普通环境/子agent文本和Unicode长s等非ASCII伪匹配保留；被过滤的
  旧文本有附件时保留附件。没有新增跨agent通信协议，本项不冒充该路径已对齐。
- protocol/items.py新增末尾可选retained_from_id，None不写JSON；旧构造/MsgPack默认
  None。window._retained_copy统一新ID和原来源：手动/自动保留、截断副本和已存pending
  恢复都标注，重复压缩保持初始来源；首次写入的新pending不标注。原append-only记录
  不改写，主模型仍见保留的用户输入，记忆提取不重复；旧无标记副本不猜测清除。
- 脱敏对结构中的字符串在JSON编码前执行，避免删除半个转义导致输入破损；持久化
  对象不受修改。现有模式仍是best-effort，不等价于Codex完整sanitizer。过长转录仍
  可被后续head/tail文本预算截断，本批不保证截断后JSON可解析或模型窗口70%已对齐。

组合Runtime证据：真实tool_search召回→schema可用→fact执行一次→Observation回灌，
分别经过manual两次、pre-turn自动、mid-turn自动压缩；普通后续Turn仍看到原/新用户
请求。关闭source Runtime后，由新的Runtime后台提取原档案、合并发布MEMORY.md：两条
原调用/结果ID对应且仅一次，两条真实用户输入仅一次，摘要和环境未混入；不是脱离
Runtime的helper演示。既有stored-pending恢复测试新增来源ID断言，防止该分支漏标。
旧MsgPack构造字节、SQLite重开后原item和copy重复append均通过兼容性验收。

验证账本：首轮全量1292 passed/80.04s；追加1项存储兼容后最终
**1293 passed/79.49s**（LANGGRAPH_STRICT_MSGPACK=true）。Ruff check及294文件
format检查、compileall、pip check、Node bootstrap语法、git diff --check均通过；
examples独立**3 passed/2.74s**。最初静态检查发现Python3.12类型参数语法不兼容
项目3.11目标，已改TypeVar并重新全绿，没有提高最低Python版本。

最终wheel：`/tmp/corki-memory-archive-wheel.VXcPe0/corki-0.1.0-py3-none-any.whl`，
659229 bytes，SHA-256 `b4c1f52459158c4a6a0b0582438b52ef541a4a41a8acea3ad838065549ec5809`。
安装至同目录installed，从/tmp以python -I运行前批407项选择及新增19项，
**426 passed/19.66s**；141个Corki模块全部来自安装包。未执行真实模型或Rust测试。
Codex commit仍ddf04ad26789d040f9ef6a96736f76602e35a6cc且工作区干净，teach.md
SHA-256仍816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a；
用户docs/flow.md暂存保留，无提交、无真实记忆库或外部写操作。

仍开放：完整Stage1 sanitizer、输出反序列化与空白/可选slug的合同（注意Codex
output_schema本身确实要求nullable rollout_slug，不能与反序列化optional混为一谈）、
提取模型实际窗口70%预算、source/archive/ephemeral/quota，以及原生phase2 agent/git
diff、unowned收尾、引用恢复/显式更新遗忘/污染控制和A/B/C/E的其他开放项。Mock只能
证明Harness的数据流与故障语义，不能证明真实模型选择或提取质量；整体目标保持active。

## 第四十六批实施前审计：Stage1输出合同与best-effort脱敏（D02/D04）

Codex memories/write/src/phase1.rs:52–65定义deny_unknown_fields的StageOneOutput：
raw/summary为必需String，slug为default Option<String>；output_schema:139仍要求三个
字段且slug nullable。sample:320直接serde解析后脱敏，不trim；run_job:260以is_empty
判无输出，纯空白不在此处删除旧输入。Corki _sample_json要求三个键一律存在，_extract
对raw/summary/slug strip，会错误拒绝可解析输出或将纯空白作为no-output失效处理。

Codex secrets/src/sanitizer.rs全文87行已读：Bearer先替换（仅空格/tab、至少16字符，
保留后缀分隔符），再OpenAI sk字母数字20+、AWS AKIA16、最后通用assignment 8+；
替换标记REDACTED_SECRET且保留assignment前缀/分隔符/引号。Corki只有三个不同阈值
正则，缺AWS/通用token；Bearer使用\s跨行匹配，替换还丢失分隔符。拟按源码模式/顺序
对齐并映射上游7个正例和7个反例，保持上一批JSON编码前脱敏，不改写durable历史。

实施前先加RED单测和真实Runtime提取→数据库→合并用例，覆盖缺slug、空白保留、
未知/重复字段拒绝及输入/输出secret不进入提取提示或产物；错误仍隔离于主Turn。
模型窗口审计也确认当前固定150000不等于Codex的有效提取模型窗口70%（prompts.rs:100），
但Corki尚无独立提取模型元数据解析链，不能把主模型窗口无条件套用到不同模型。本批
关闭内容合同，预算/模型元数据继续作为有效开放差异，不预设不适用。

## 第四十六批实施与验证：输出保真、严格解析及secret模式（D02/D04）

上一目标Turn为实质progress（结构化档案与压缩来源已落地并验证），本轮继续完整目标，
没有遇到阻塞或缩减全局验收范围。Codex固定源码干净，未修改参考仓库。

RED证据：新增25个sanitizer单测（含上游全部7个Bearer正例和7个反例）及7个真实
Runtime内容合同用例，修改前26 failed/6 passed。输入含AWS和token的Runtime测试先
暴露未脱敏问题；仅修sanitizer后28 passed/4 failed，独立复现缺slug拒绝、首尾空白
丢失、纯空白触发no-output、重复raw_memory字段被静默接受。随后修解析与保存合同。

实现与作用域：

- 新memory/sanitizer.py按Codex源码顺序Bearer→OpenAI→AWS→assignment执行四种
  模式和REDACTED_SECRET标记；Bearer限定空格/tab及16字符阈值，不再跨行/不换行空格
  或把大小写开关扩大到token字符类；assignment保留键名、分隔符与开引号。新增AWS
  access ID和通用token匹配。旧更短阈值也按上游调整，不能将任意短词都标作secret。
  仍是best-effort，不是凭据检测完整性保证。Python/Rust正则的Unicode word/space
  类边界尚未逐码点对照，不能仅凭相同表达式宣称所有Unicode输入语义一致。
- pipeline._sample_json区分required与optional解析字段；Stage1允许缺省slug，仍使用
  原严格三字段output_schema，不降低provider请求约束。object_pairs_hook拒绝重复键，
  继续拒绝未知字段/缺raw或summary/错误slug类型；同一JSON读取入口也保护合并输出。
- _extract不再strip raw/summary/slug，缺省/null slug为None，空字符串slug保持为空。
  no-output仍是raw或summary实际为空，纯空白作为Stage1成功写入；合并候选的独立trim
  过滤没有删除或混改。原始用户历史不被脱敏重写；档案投影仍在JSON编码前处理字符串。
  输出raw/summary/slug和现有合并产物调用点使用同一个sanitizer，不只修helper示例。

Runtime验收：每个case从真实SQLite source经新Runtime启动后台claim→输入脱敏→
ModelPort→解析→Stage1提交→合并发布；同时普通Turn完成。缺slug、padded、whitespace
分别断言数据库原字符串/None/空slug；unknown、duplicate、missing_raw、invalid_slug
均仅失败提取，不留下Stage1成功输出，合并初始化仍可运行。AWS/token不进入提取提示，
模型输出AWS不进入合并提示或MEMORY.md，原append-only用户记录保持原样。没有调用
真实provider，此证据不证明模型提取质量或所有secret类型覆盖。

中间回归109 passed/1 failed：旧pipeline测试硬编码REDACTED标记，依据源码替换为
REDACTED_SECRET，保留secret不得出现的原断言；另修测试SQL长行静态错误。
最终**1325 passed/81.79s**（LANGGRAPH_STRICT_MSGPACK=true），Ruff check/297文件
format、compileall、pip check、Node bootstrap语法、git diff --check均通过。
examples独立**3 passed/2.78s**。README/development/THIRD_PARTY_NOTICES已同步合同
及上游Apache-2.0归属；未提高Python最低版本。

wheel：`/tmp/corki-memory-contract-wheel.CVn3AI/corki-0.1.0-py3-none-any.whl`，
660107 bytes，SHA-256 `3d26f49c575f5252b9bd6ddfc8536323588d1e4f4262b93631bbdb2822c69c8b`。
同目录installed从/tmp用python -I执行前批426选择加32新增：
**458 passed/18.48s**；142个Corki模块均来自安装包。未执行Rust或真实模型。
teach.md SHA-256仍816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a，
docs/flow.md用户暂存保持，Codex仍ddf04ad26789d040f9ef6a96736f76602e35a6cc且干净。

下一步有效源码入口：Codex memories/write/src/runtime.rs::stage_one_request_context
经models_manager.get_model_info(extract_model, config)获取独立模型信息，再由prompts.rs
计算raw window×effective percent×70%，无窗口才fallback150000。Corki Runtime:293起
可使用独立memory_model，但ModelPort仅有stream/aclose，pipeline仍固定configured
150000；必须补真实模型元数据/配置解析链，不能仅把主模型窗口套到不同extract_model。
原生Phase2 agent/git diff、source/archive/ephemeral/quota、引用恢复/更新遗忘/污染及
A/B/C/E其他未关闭项继续有效；整体goal保持active。

## 第四十七批实施前审计：模型窗口解析与Stage1预算（C02/D02）

上轮为progress；本轮重新读取目标及源码。Codex runtime.rs:209读取extract_model的
ModelInfo，models-manager/manager.rs:187→construct_model_info_from_candidates，
model_info.rs:25 with_config_overrides把显式global model_context_window应用到该模型，
并用max_context_window截顶。因此上批“不应无条件套主窗口”仍成立，但必须补充：显式
全局覆盖确实跨模型共享，不能误作只作用于main。protocol/openai_models.rs:499优先
context_window再max_context_window；prompts.rs:110按raw×effective/100再×70/100、
最小1，未知/无效窗口fallback150000。utils/string/src/truncate.rs全文的头尾截断使用
4 UTF-8 bytes/token，左右各半、完整字符边界，marker额外附加而不占保留预算。

Corki Settings只有main窗口，ModelPort没有catalog合同；pipeline._extract始终固定
memories_extraction_token_limit=150000，未使用当前模型有效窗口，且沿用更保守Unicode
估算/不同marker。会使小窗口提取溢出，也会不必要限制大窗口。

拟先实现可运行的静态模型窗口catalog路径（对应Codex StaticModelsManager/自定义
catalog）：models.catalog按完整模型名提供context/max/effective元数据；显式
models.context_window_override跨模型应用且按max截顶；main未列catalog时沿用现有
agent窗口配置，其他未知模型不继承main默认。该解析同时供Runtime主窗口/skills和
提取使用，不能留成无消费者的配置。远程catalog、provider预选模型/namespace后缀匹配
另列开放，不伪造上游模型实时参数。extraction_token_limit默认改为自动，已有显式值
作为已知模型的额外上限（未知模型可指定fallback）；默认150000仅用于未知窗口。
Stage1使用独立源码式UTF-8头尾截断，其他主上下文/合并预算不混改。先加配置/公式/
边界RED及真实Runtime不同模型提取→合并测试，再改产品代码。

实施中补读model_info_from_slug:143和construct_model_info_from_candidates:654后修正
上面方案的“未知”分类：目录缺失的slug有generic context=max=272000、effective95，
与目录项存在但两窗口均None不同。首轮实现将前者也按150000处理不正确，新增2项RED
分别暴露150000（应180880）及global override1000000未被272000截顶（应180880）。
修正采用源码generic回退并给出警告；150000只用于实际无窗口元数据。此处是源码证据
修正先前计划，不将首轮1354全绿当作模型解析完全对齐证据。

## 第四十七批实施与验证：静态模型解析、有效窗口与提取截断（C02/D02）

产品路径：config/model_context.py从[models.catalog.<完整模型名>]生成冻结的
protocol/context.py::ModelContextInfo，Settings.model_context_info统一解析选择、
context优先于max、显式全局override按max截顶。main无目录项时保留现有agent窗口配置，
独立缺失model按Codex generic context=max=272000/effective95并发warning；目录项
存在但窗口全None才保留为未知窗口。main_context_limits在配置校验阶段检查可用性，
并真实供Runtime的ContextWindowManager及SkillService预算使用。

memory/inputs.py::extraction_token_budget用于pipeline._extract实际发起采样之前：
先raw×effective/100取整，再×70/100取整、下限1。缺model generic预算180880，与
实际无窗口的150000 fallback分开。memories.extraction_token_limit默认None表示自动，
显式旧配置值作为已知窗口的附加上限，无窗口时可指定fallback；已知百万窗口默认
预算665000而不是被150000硬截。没有修改其他合并token预算或主请求估算器。

truncate_rollout按Codex utils/string/src/truncate.rs保留4 bytes/token，首尾各半、
UTF-8边界安全，返回“…N tokens truncated…”；N使用源码的预算差字节估算，不冒充
精确tokenizer。marker额外附加，不从保留预算扣除；截断后不是有效JSON承诺。原始
conversation及stage1 claim档案不改写。新增配置可加载/校验，未只新增接口或示例。

RED与修正轨迹：22个初始unit全失败（缺预算实现及非法新配置被忽略）；5条Runtime
中4失败/1通过，缺所选模型预算与源码marker，原有无截断路径通过。实现初次漏写
future annotations导致ModelContextInfo自引用收集错误，已修后58项通过；补主循环
catalog→真实自动压缩和main不可用窗口校验后针对性230通过。首轮全量1354通过仍未
完成源码审计；进一步追踪generic fallback发现并新增2个RED，修正缺model与无窗口
分类，并增加大段Unicode无窗口Runtime测试，最终新增共32项通过。

Runtime组合覆盖：main configured window、独立小模型、max-only+global clamp、
缺model generic、真实无窗口、显式cap六条后台提取→Stage1提交→合并发布；逐条验证
实际ModelRequest.model、原档案头尾、UTF-8字节范围和省略数量，后台与主Turn均完成。
另一个main目录case原agent window100000经global50000/max8000/effective80解析为
6400可用，实际自动压缩一次并继续回答，证明目录不是只影响helper公式。

最终**1357 passed/82.37s**（LANGGRAPH_STRICT_MSGPACK=true）；Ruff check及301文件
format、compileall、pip check、Node bootstrap语法、git diff --check均通过。
examples最终**3 passed/2.55s**。README/development与THIRD_PARTY_NOTICES已说明
新静态配置、回退假设、旧extraction_token_limit兼容语义和Apache-2.0来源。

第一次wheel仅为中间实现（662609 bytes，sha6055c8295f317fe54afee467a650d7930ae53b4a3b3f6f949dfe23f9c60c3dbf），
generic修正后重新构建最终wheel：
`/tmp/corki-model-window-final.aLWkmz/corki-0.1.0-py3-none-any.whl`，662761 bytes，
SHA-256 `5fb4c9e3bedea73092d4f0a0e83376194314735f0658b3c70fdfd759763915a0`。
同目录installed，从/tmp使用python -I，前批458选择加32新增，
**490 passed/20.63s**，144个Corki模块均来自安装包。未执行真实provider或Rust测试。
Codex仍ddf04ad26789d040f9ef6a96736f76602e35a6cc且干净；teach SHA-256仍
816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a，用户docs/flow.md
暂存保持，无提交、无真实memory库写入。

完整目标仍开放：当前目录只按完整名精确匹配，而Codex有longest-prefix及受限namespace
suffix回退；远程/缓存/内置catalog权威性和provider默认extract模型选择未接通，main
缺catalog仍沿用Corki已有agent配置，不能据此宣称默认模型元数据已完全一致。其他C02
usage/BodyAfterPrefix/附件成本、原生Phase2 agent/git diff、source/archive/ephemeral/
quota、Unicode sanitizer边界、引用/遗忘/污染及A/B/C/E剩余项继续有效。整体goal active。

## 第四十八批实施前审计：模型目录名称匹配及来源边界（C02/D02）

上轮为progress；本轮重读目标与当前实现。Codex models-manager/manager.rs:618–676：
先完整请求名starts_with候选slug，按最长slug选择且同长保留首项；不是版本后缀分隔符
校验。完整名未命中才split_once('/')；namespace必须非空且仅ASCII字母数字/_/-，
suffix不得含第二个'/'，再按最长前缀查找。若命中，返回metadata但slug改回原请求名，
最后应用global config override。manager_tests.rs:755–831覆盖custom catalog版本
后缀、单层/带连字符namespace及多层拒绝。Corki Settings.model_context_info只用
info.model==model；合法别名因此走generic窗口，影响真实Stage1预算或main压缩时机。

拟在config/model_context.py补最长前缀+受限suffix解析，保留请求身份与冻结候选，
仍由Settings→Runtime/main/skills和memory.inputs→_extract使用；先加正反例RED，
再通过实际namespaced/version请求→提取截断→提交/发布和main压缩验收。

来源进一步审计：OpenAiModelsManager构造时读bundled catalog，StaticModelsManager
自定义catalog是authoritative。manager.rs:389–438按Offline/OnlineIfUncached/Online
调度cache/network，should_refresh_models需Codex backend或command auth；不能把
普通API-key的API /models列表视作自动有完整窗口元数据。当前Corki仍只有显式静态
窗口目录和legacy main配置，bundled/权威性/缓存组合继续开放。provider.rs:137/141
默认extract=gpt-5.6-luna、consolidate=gpt-5.6-terra；Bedrock按backend改ID，Corki
仍缺省复用main。该差异牵涉provider身份与metadata默认来源，继续单独实施，不以
本批名称匹配通过宣称默认模型选择或完整目录路径已对齐。

## 第四十八批实施与验证：目录匹配接入实际请求预算（C02/D02）

新增config/model_context.py::match_model_context，由Settings.model_context_info实际
调用：先按完整名最长前缀选候选；仅无完整名匹配时允许单层ASCII provider namespace
fallback，再做最长前缀。无额外分隔符限制、大小写敏感、同长首项保持；显式多层路径
目录项仍允许直接匹配，不能把“禁止多层suffix剥离”扩大成禁止全部多层model名称。
使用replace复制元数据并保留原请求model，再按上批global override/max clamp处理。
冻结catalog不变；provider收到的请求名不被替换成候选slug。

修复前20个新unit中11 failed/9 passed，覆盖最长/版本、namespace ASCII范围、
点号/空段/多层/Unicode拒绝、完整名前缀优先、candidate不可变及匹配后max clamp；
同长首项通过helper序列验证（TOML配置自身不允许重复键）。新增Runtime三条提取
version、namespace、direct-prefix-priority全部错误回退预算；两条main alias/namespaced
case未按目录窗口压缩而进入普通采样，均RED。原有7条Runtime保留通过。

接入后配置+memory+Runtime联合193 passed；三种提取名称逐一验证实际请求的model
身份、UTF-8保留预算、省略marker、Stage1提交和合并MEMORY.md发布；main版本后缀和
单层namespace分别触发真实自动压缩并继续Turn。新增总计25项，不只验证匹配函数。
旧模型目录/提取/压缩用例没有删除或降级断言，无新增runtime依赖。

最终**1382 passed/81.37s**（LANGGRAPH_STRICT_MSGPACK=true）；Ruff check及302文件
format、compileall、pip check、Node bootstrap语法、git diff --check均通过。
examples独立**3 passed/2.81s**。README/development及第三方来源声明同步最长前缀/
受限namespace语义，不再写精确匹配；Codex Apache-2.0归属沿用已有随包license。

最终wheel：`/tmp/corki-model-lookup-wheel.OVkG6D/corki-0.1.0-py3-none-any.whl`，
663280 bytes，SHA-256 `f63ac95004921702289af9d5f92dd86c9cd893b44bc1de68363ec8dbab848809`。
安装至同目录installed，从/tmp以python -I运行前批490选择+新config lookup20项和
新增Runtime5项：**515 passed/19.68s**；144个Corki模块都来自安装包。
未执行真实provider或Rust；这些mock只证明Harness请求/预算/持久化链路，不证明
真实模型的输出质量或可用窗口。Codex固定commit仍干净，teach SHA-256仍
816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a，用户docs/flow.md
暂存保持，无提交、无真实记忆库修改。

后续仍有效：bundled/static-authoritative/cache/remote模型目录来源与provider preferred
extract/consolidate选择、旧main默认窗口来源、完整usage/BodyAfterPrefix/附件成本，
以及phase2 agent/git diff、source/archive/ephemeral/quota、Unicode sanitizer、
引用恢复/更新遗忘/污染、A/B/C/E全部未关闭项。整体goal保持active，未宣称完整对齐。

## 第四十九批实施前审计：记忆模型/effort选择与内置窗口来源（C02/D02）

上批progress。本轮完整追踪phase1 build_request_context→provider preferred model→
stage_one_request_context及phase2.rs:358–368：用户memories阶段model覆盖优先，否则
provider默认extract gpt-5.6-luna/consolidate gpt-5.6-terra；stage_one effort=Low、
stage_two=Medium（write/lib.rs:79/103），不继承main的effort。Bedrock明确覆盖模型ID，
不是根据URL临时猜测。Corki _extract/_consolidate均or settings.model；adapter effort
只能在构造时设置且可能被main和后台共享，没有单请求覆盖。

拟增加provider级preferred模型配置并按阶段显式覆盖→provider配置→源码默认选择，
接入实际ModelRequest及提取预算；ModelRequest增加可选effort，阶段请求low/medium，
普通请求缺省继承adapter设置，capability不支持则不发送字段。不修改共享adapter状态，
避免后台与普通Turn互相污染。不支持默认模型ID的第三方provider可配自己的preferred
ID；错误仍失败该后台任务，不偷偷改用main或重放主Turn。

顺带必须补默认元数据来源：已读取models-manager/models.json全部11条的窗口投影，
并核对ModelInfo缺省effective=95。Luna/Terra raw272000但max872000，不能使用generic
max272000误截全局override。拟随包发布固定commit的纯窗口元数据；未提供静态catalog
时使用它，明确空catalog/自定义catalog则authoritative，不与内置混合。保持main既有
agent窗口设置（内置max仍截顶），main默认配置来源不据此宣称与Codex全同；远程/缓存
刷新、完整model能力元数据另留开放。先加默认/覆盖/空目录/effort RED及真实HTTP模拟
Runtime提取→合并→主Turn请求隔离测试，再实施。

## 第四十九批实施与验证：默认记忆模型、请求effort与内置窗口（C02/D02）

Settings新增provider_memory_extraction_model/provider_memory_consolidation_model，
对应[provider].memory_extraction_model/memory_consolidation_model；校验非空字符串。
resolved_memory_*按阶段显式memories配置→provider配置→Codex默认Luna/Terra选择。
pipeline两阶段ModelRequest与memory.inputs提取预算调用同一解析，不再or main。
第三方provider可以提供自己的有效ID；未实现Bedrock transport的具体ID路由，不伪称
新增了该provider。默认值来自固定源码，不保证部署的服务提供这些模型。

ModelRequest新增可选reasoning_effort（缺省None继承adapter值），阶段明确low/medium；
Chat/Responses在构造各自payload时选择request值，不修改共享adapter._reasoning_effort。
provider capability不支持时仍不发送；OpenAI Chat profile补supports_reasoning_effort。
普通主Turn之前/之后均保持自身high；该设置是请求合同，不是新增数据库字段。完整
逐model能力解析仍开放，不能把provider-level支持等同每个任意模型均可接受effort。

config/bundled_models.py随包提供固定Codex models.json的11条窗口投影，缺省百分比95。
用jq投影与实际Python dataclass序列作diff逐项一致，源models.json SHA-256为
d7136a413cfac1b5b1686d9e0dcc5c80ca05bebed5e9fc3911376561d0ef6ee8。
Settings.model_contexts=None与空tuple刻意区分：未配置使用内置，显式自定义（含空）
目录不合并内置。Luna/Terra的max872000不再丢成generic max272000；main现有agent
窗口/effective设置保留且被内置max约束，这仍不是Codex主模型默认配置来源完全对齐。

RED：新配置/内置权威性11项、实际HTTP Runtime10项修改前全部失败；额外请求effort
8项因缺字段全部失败。实现后29新增通过。组合回归334 passed/2 failed，均是旧测试
假设默认复用main；将这两个既有“main窗口”场景改为显式选择main（单测重命名），
原预算/主循环断言保留，新的默认行为由独立default用例覆盖；随后66项通过。

HTTP Runtime证据：Chat/Responses各覆盖default、provider preference、stage override、
stage-over-provider及模型不可用五种情况。两阶段真实schema payload中的model、effort
及小窗口截断均受断言；同一个HTTP adapter同时服务普通Turn和后台，前后main high
不被改写。HTTP400 model_not_found只使Stage1失败，主Turn仍完成，phase2可初始化；
每case四次请求，无隐式main fallback或额外重采样。产物经真实SQLite/文件发布验收。

最终**1411 passed/84.18s**（LANGGRAPH_STRICT_MSGPACK=true）；Ruff check及306文件
format、compileall、pip check、Node bootstrap语法、git diff --check均通过。
examples独立**3 passed/2.94s**。README/development及第三方声明已更新，明确新的
默认模型及如何显式保留原main选择；未宣称这些固定模型名是实时可用性验证。

最终wheel：`/tmp/corki-memory-defaults-wheel.KUsrJL/corki-0.1.0-py3-none-any.whl`，
664794 bytes，SHA-256 `af2889920d4808b514fd4528deeb4c04ac392386325d0ce7185e2b03f87eb0ef`。
同目录installed从/tmp用python -I运行前批515选择+新增29：
**544 passed/20.91s**，145个Corki模块均来自安装包。未执行真实模型或Rust；Codex
固定ddf04ad26789d040f9ef6a96736f76602e35a6cc且干净。teach SHA-256仍
816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a，用户docs/flow.md
暂存保持，无提交/真实记忆库写入。上一目标Turn与本轮均为实质progress，非等待/阻塞。

整体goal仍active：远程/缓存目录权威性、主模型默认来源、完整逐model能力/usage/
BodyAfterPrefix；原生Phase2 Agent、其上下文/权限/git workspace-diff合同与unowned
收尾（当前兼容合并仍是隔离单请求，其输入预算仍须连同该路径审计）；source/archive/
ephemeral/quota、Unicode sanitizer、引用/更新遗忘/污染，以及A/B/C/E剩余终态/资源
收尾/故障组合继续有效。不能凭这些HTTP mock通过宣称真实模型质量或全局对齐。

## 第五十批实施前决策：Code Mode 收尾失败与 Turn 终态（A08/E04）

重新先读固定Codex tasks/mod.rs的spawn→on_task_finished与handle_task_abort，及
core/src/tools/code_mode/mod.rs::interrupt_active_cells/shutdown、session/handlers.rs
的shutdown_session_services。interrupt feature启用时join_all所有活跃cell的terminate，
每项失败warn，不把一次清理失败变成另一种Turn终态；Session关闭Code Mode失败也warn
后继续MCP等依赖。正常Turn完成并不强制终止所有cell，rollout flush失败也不是本批
可据以宣称Codex原子终态持久化的证据。remote_session/connection/driver_tests.rs::
terminate_closes_cell_without_waiting_for_delegate_cleanup另明确远程delegate关闭通知和
实际清理join不同；不能把Corki本地owned task join说成该远程协议逐项相同。

Corki Runtime._execute_run在最后finally先await CodeMode.deactivate再realtime.deactivate；
前者异常可遮蔽已选终态并跳过realtime释放。正常deactivate仅同步变更字段，本身没有
外部I/O，不能将人工override的异常夸大成正常路径频繁失败。更直接的生产边界是
Cell._run.finally顺序kill→wait→stdin.close→cancel tools→cancel notifications：任一
进程收尾异常跳过后续步骤及ready/changed通知，而Cell.terminate/observe的gather
return_exceptions会吞掉任务异常，可能漏回调或显示completed。Service.deactivate的
外层gather又会在某个terminate抛错时提前退出，跳过独立calls清理。这些为P0部分不一致。

拟先用真实Runtime/QuickJS子进程注入wait/pipe关闭故障和取消窗口；单独覆盖多个cell
终止中一个报错、另一个仍在收尾时的join及独立call回收。修复目标：每类资源均尝试
收尾、通知始终释放；进程已退出的ProcessLookupError无害；真正基础设施清理失败
保留诊断且活跃执行不得伪装成功，取消仍为取消；deactivate不早退跳过其他资源，
Runtime不因清理异常丢失终态/realtime收尾。关闭对外仍遵循Corki已有的“完成所有关闭
后抛首个错误”API，并明确Codex对应是warn不是同名API抛错。不自动重放工具副作用，
不改变正常cell跨Turn存活；OS永久拒绝杀进程和不响应取消的handler强制隔离仍开放。

## 第五十批实施与验证：收尾故障、观察终态与资源回收（A08/E04）

Cell将清理放入自身持有的shielded task，重复terminate或在收尾期间首次取消仍join到底，
不会遗留该清理task。kill、wait、stdin.close各自记录异常并继续后续步骤；已退出竞态
ProcessLookupError不算失败。工具回调及通知取消/join不因上述异常跳过，ready/changed
最后始终释放。真正清理错误留在Service首错诊断；若不是已取消cell，则置failed并通过
既有FatalToolError failure channel通知实际主循环，不把基础设施失败做成功脚本结果。
这是本地引擎资源边界，不声称实现了Codex远程host/delegate的所有关闭协议。

Service.deactivate使用return_exceptions收齐所有cell终止后再清理独立calls，最后记录
并抛首错；aclose在完成当前清理后也报告历史首错，不因旧Turn observer离开而忘记。
Runtime最后deactivate的Exception记录run.cleanup_error且不遮蔽已选Turn终态，realtime
deactivate位于独立finally。取消仍取消，既有model/tool失败仍失败；正常deactivate无
I/O，未将注入式override当成普通完成路径的真实故障。Runtime.close依然尝试全部依赖
再报告首错；Codex此处warn而非抛CorkiAPI错误的区别保留在审计，没有伪装一致。

新增integration 5例（真实QuickJS/Runtime的wait、pipe故障×完成/取消，以及两个cell
终止一错一阻塞）修改前全RED：完成侧超时、取消侧通知未释放、多cell侧worker过早done。
修复后5通过。Pipe注入使用Cell私有writer代理，不改asyncio自身协议回调；故障边界
实际kill/reap后再注入异常，不宣称测试了OS永久拒绝杀进程。每例还验证callback结束、
进程已回收、无running Turn、realtime关闭、唯一终态、下一Turn可完成且工具只调用一次；
后台首错在旧Turn离开后仍使close报告失败。两cell实例另验证最后model依赖仍被关闭。

随后unit新增4例，其中两个实际Cell observer在await清理前缓存completed的竞态继续RED：
清理失败/收尾期间取消后仍返回Script completed。把status/failure读取移到join之后，
现在分别返回failed/terminated；重复terminate不会中断尚未结束的清理。另两例fake进程
分别验证kill OSError与无害ProcessLookupError，均继续reap/close及join回调。首次组合
收集因integration/unit同basename冲突中断，unit改为test_cell_cleanup.py后正常运行。

组合**114 passed/12.21s**；最终新增9例独立**9 passed/1.03s**。全量
**1420 passed/80.45s**（LANGGRAPH_STRICT_MSGPACK=true），Ruff check/308文件format、
compileall、pip check、Node bootstrap及git diff --check通过；examples独立
**3 passed/2.91s**。README补充关闭错误与正常cell跨Turn边界，teach未改。

最终wheel `/tmp/corki-cell-cleanup-wheel.h7lxOR/corki-0.1.0-py3-none-any.whl`，
665594 bytes，SHA-256 `3a07dc3791880e18cf6cf860521c69942ef21bc67bfb7b3fa90f67ccf2b4769e`。
同目录installed从/tmp用python -I运行前批544选择+本批9：
**553 passed/21.46s**，145个Corki模块均来自安装包。未执行真实模型或Rust测试。
Codex仍固定ddf04ad26789d040f9ef6a96736f76602e35a6cc且干净；teach SHA-256仍为
816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a。用户docs/flow.md
暂存保持，无提交；本批没有真实provider或用户记忆库写入。

本批只关闭上述故障窗口，goal仍active。A08的初始化取消、强制隔离、interruption
marker及其他A/B/C/E组合仍开放；D的原生Phase2 Agent、权限/上下文/workspace-diff、
source/archive/ephemeral/quota与更新遗忘污染等，以及C的远程目录/模型能力/usage
与主模型默认来源等沿用上一批开放清单。正常Cell跨Turn既有回归通过不等于完整远程
CodeMode关闭协议一致；OS/handler永久不合作的情况没有被本地mock验证覆盖。

## 第五十一批实施前决策：初始化取消、关闭与回滚所有权（A08/E04）

先复核固定Codex session_startup_prewarm.rs::SessionStartupPrewarmHandle::abort（abort并
await）、session/mcp_prewarm.rs的shutdown token与select refresh、stop worker cancel/join，
以及tools/code_mode/mod.rs::session（初始化与shutdown token竞争；创建后再查shutdown，
关闭刚创建的session）、shutdown（join进行中的初始化，不初始化未使用的服务）。
session/handlers.rs shutdown先停止这些启动工作，再关闭依赖。上述有默认/feature控制
的预热路径，不等同Codex所有构造操作都可取消；本批对齐资源所有权及停止/发布边界，
不声称移植WS预热或远程CodeMode host。尚未被接纳的输入不产生伪TurnStarted/终态。

Corki._start_turn持有_lifecycle_lock等待_ensure_ready；aclose的_close_resources也要
同一锁，只能等初始化自己结束，不能先取消进行中的MCP连接或checkpointer setup。
_ensure_ready虽然对setup异常会退出checkpoint context，但清理仍属于调用方任务，
重复取消能截断rollback；MCPManager已有暂存回滚/connection独立close，但不能替代
Runtime启动任务所有权。SQLite.create_thread直接await to_thread，取消返回时底层写
线程仍可继续，Runtime无法证明关闭前已join该启动写入。这些为已确认P0差异。

拟引入单次尝试的owned initialization task，关闭先请求取消，再等rollback及现有锁；
调用方取消也取消并join此任务，重复取消不得再次打断回滚。初始化失败不发布compiled/
checkpointer，重试创建新context；closed禁止发布新启动结果。线程建档写入复用已有
joined-write合同，取消等实际写线程结束但不继续接纳Turn。不新增重试工具调用。
先以实际Runtime、HTTP MCP mock、真实SQLite checkpoint包装及受控建档线程复现，
覆盖close/调用方取消/重复取消、部分MCP注册回滚、取消后重试和无模型采样/伪终态。

追加入口证据：CLI._consume_realtime_turn的/stop调用cancel_active后等待consumer，
不是直接cancel consumer；当前cancel_active只处理_active_run，因此初次初始化时无效。
补该公开入口的3个RED并纳入同一owned startup取消。Codex session/tests.rs::
interrupting_regular_turn_waiting_on_startup_prewarm_emits_turn_aborted先发TurnStarted
再等待预热，Interrupt产生marker和TurnAborted；Corki当前是Thread/MCP/checkpoint
在Turn接纳之前初始化，本批未迁移该事件时序，不能把“无伪Turn”声明成上述Codex
已开始Turn的事件合同一致。把这一具体时序差异保留开放，不用本批关闭A08全项。

## 第五十一批实施与验证：可取消且完整回滚的初始化（A08/E04）

Runtime._ensure_ready将一次尝试放入corki-initialize task，由_ready_lock保持single-flight。
普通等待使用shield；调用方取消只向startup发一次cancel，并持续join rollback，重复
取消不会传入清理。aclose先关准入并取消startup，再由原有_close_resources等待生命周期/
ready锁、关闭剩余依赖，解除旧实现“拿不到锁便无法取消初始化”的等待问题。cancel_active
也取消startup，让实际CLI /stop入口生效。startup退出后清除句柄，失败重试仍构造新
checkpoint context，未改变既有图/registry发布单位。SQLite.create_thread改用已有
_joined_write，取消也等待已开始的线程写结束；不改变表结构、Thread ID和幂等插入。

初始化在建档、MCP以及checkpoint setup返回后检查closed与owned task的cancelling状态。
依赖即使捕获CancelledError后返回成功，也不能继续后续启动阶段或发布compiled结果；
这是对应Codex持续shutdown token的本地任务合同，不是假定所有依赖都会正确重抛取消。

integration新增主矩阵3边界×4入口（caller cancel、repeat cancel、stop、close）12例。
初版9例RED为7 failed/2 passed；已有单次MCP/checkpoint caller取消本来可正确回滚，不
冒称全部新增能力。实现后连同旧startup/search回归25通过；后补公开stop入口3例全RED
并修复。继续新增MCP/checkpoint依赖取消后晚返回×stop/close共4例，先2 failed/2 passed
（close已有closed保护，stop还会继续），再补持续取消检查。最终新增**16 passed/0.89s**。

主矩阵使用真实Runtime、HTTP MCP adapter+MockTransport、真实SQLite checkpoint包装和
受控建档线程；冻结清理时验证consumer/close不能提前完成，取消后无模型请求、无假
TurnStarted/终态、不激活realtime、不留下running Turn或compiled/checkpointer。
MCP两个server中已初始化的暂存项及当前失败项都关闭且不发布工具；未关闭Runtime可
重试，真正模型请求只出现一次且只含retry用户输入。Close后拒绝新请求，模型依赖仍关闭。
晚返回4例另覆盖搜索发布前门禁和checkpoint最终发布门禁。没有真实模型/远程MCP访问。

旧Runtime shutdown/admission/recovery、search、MCP组合**86 passed/5.16s**；最终全量
**1436 passed/83.90s**（LANGGRAPH_STRICT_MSGPACK=true），Ruff check/309文件format、
compileall、pip check、Node bootstrap、git diff --check均通过；examples独立
**3 passed/2.69s**。README补充初始化取消语义及接纳时序，不修改teach.md。

尚未关闭的邻接故障窗口：checkpoint rollback __aexit__自身异常的保留/收尾政策，以及
_start_turn在save_turn(RUNNING)之后、TurnRun注册之前的取消/实际线程写入窗口，需要
继续从Codex追踪。当前初始化在TurnStarted之前，Codex预热可发生在已开始Turn内部；
具体事件时序/中断marker没有在本批迁移。所有其他A/B/C/D/E开放项继续有效，goal active。

最终wheel `/tmp/corki-initialization-wheel.Yq1cMq/corki-0.1.0-py3-none-any.whl`，
666048 bytes，SHA-256 `57392fd36bf985a31fac439ce983507e71d0452743d02e85221f8727916a52d3`。
同目录installed从/tmp用python -I运行前批553选择+本批16：
**569 passed/21.91s**，145个Corki模块均来自安装包。Codex固定commit仍为
ddf04ad26789d040f9ef6a96736f76602e35a6cc且工作区干净；teach SHA-256仍为
816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a。用户docs/flow.md
暂存保留，无提交或真实provider/用户记忆库写入。未运行真实模型和Rust测试；上轮
及本轮均有源码/代码/RED与验证推进，不是等待或阻塞，未缩减整体目标。

## 第五十二批实施前决策：首笔 Turn 写入的所有权与取消（A07/A08/E04）

先读固定Codex tasks/mod.rs::spawn_task：active_turn锁内spawn并安装RunningTask，持有
AbortOnDropHandle/cancellation token/done；RegularTask::run发送TurnStarted，然后等
prewarm/run_turn；预热被取消仍run_hooks_and_record_inputs，abort统一记录中断并发
TurnAborted。Codex没有Corki的SQLite RUNNING+LangGraph checkpoint双库合同，不能
把其rollout flush的warning策略外推成相同事务。本批对齐“被接纳的工作先有任务所有者，
取消收尾保留输入且不遗留无主执行”的行为，不宣称已实现所有崩溃原子性或中断marker。

Corki._start_turn在消费者任务中await repository.save_turn(RUNNING)，随后才在_run_graph
创建TurnRun并发TurnStarted。取消消费者可发生在数据库提交后、worker尚未注册前；
cancel_active此时也没有active worker可取消。SQLite.save_turn又直接await to_thread，
取消等待不等实际写线程，潜在迟到RUNNING提交会覆盖已选择的终态。初始化owned task
不覆盖这个后续写入窗口，属于独立P0缺口。

拟移除_start_turn的写入await，先注册TurnRun，再由_execute_run持有新Turn的首笔写入；
恢复已存在Turn不重复写RUNNING。SQLite.save_turn复用joined-write，取消会等在途写入
结束后由统一终态路径保存cancelled和初始输入。首笔写入失败应发storage TurnFailed，
不采样/调工具；正常/手动compact都走同一合同。TurnStarted表示worker已经接纳，不再
承诺数据库首笔写入已完成。先用真实SQLite线程提交前/提交后暂停复现，覆盖stop、
重复消费者取消、close、普通/realtime/compact以及冷恢复不得重新执行已取消请求。

## 第五十二批实施与验证：owned 首笔写入与取消后冷恢复（A07/A08/E04）

_start_turn不再在消费者任务中写RUNNING：构造初始输入后立即注册TurnRun，发出
TurnStarted；_execute_run在新Turn开始时先保存RUNNING，随后才进入图。resumed标志
传至worker，恢复路径不额外覆盖既有Turn状态。首次写入的Exception被归类为storage
TurnFailed，不启动模型；取消依然优先进入统一TurnCancelled收尾。SQLite.save_turn
复用_joined_write，取消等待不放任底层提交线程晚到；取消终态只能在首笔写入完成后
保存。初始输入/operation由原有统一终态路径持久化，无表结构或checkpoint字段变更。

事件合同明确更新：TurnStarted表示已注册worker，不代表首笔数据库提交已完成；
普通与manual compact一致。前一批的Thread/MCP/checkpointer初始化仍在Turn接纳之前，
不能把首笔写入移入worker宣称成整个初始化/预热时序已与Codex一致。

新增integration 22例先全部RED：18例提交前/提交后×普通/realtime/compact×stop/
重复消费者取消/close在旧实现都没有已注册worker/TurnStarted；4例首笔存储错误
（提交前后×普通/compact）直接从消费者抛出OSError而非TurnFailed。实现后22通过。
在真实SQLite _save_turn线程内使用gate，取消与close在gate释放前必须保持等待；
最终写入序列严格RUNNING→CANCELLED，唯一取消终态、无模型采样、realtime关闭。
随后关闭并用新Runtime冷恢复，resume_pending必须为空，用户输入保留一次（compact
不伪造用户），新请求能正常完成且仅产生一次模型请求。故障用例验证storage分类和
无错误恢复。再补2例“取消已发生、底层写线程才报错”，取消不变TurnFailed、错误日志
可见、无running记录；这是既有joined-write策略的组合回归，不额外冒称独立RED。
最终新增**24 passed/1.87s**。

首次组合98 passed/1 failed，旧shutdown测试仍假设首笔写入期间无法取消；已将其
重命名为test_close_cancels_and_joins_initial_turn_write_before_dependencies，让fixture
在收到取消后保持清理gate，验证close必须等待且model依赖未关闭/未采样，未删除收尾
验收条件。后续组合**99 passed/6.56s**。并行启动的旧全量仍使用已收集的旧测试，记录
为**1457 passed/1 failed/87.51s**，同一过时预期，不能作为全绿结果；已重跑最终全量。

最终全量**1460 passed/83.22s**（LANGGRAPH_STRICT_MSGPACK=true）；Ruff check/310文件
format、compileall、pip check、Node bootstrap、git diff --check均通过，examples独立
**3 passed/3.28s**。README明确TurnStarted的worker接纳而非数据库commit含义。
wheel `/tmp/corki-turn-write-wheel.RKC2jW/corki-0.1.0-py3-none-any.whl`，666317 bytes，
SHA-256 `89ed4d91147589ce81576c9e24eb6f95f43e800c7b419b6403224ff7369a3179`。
同目录installed从/tmp用python -I运行前批569选择+本批24：
**593 passed/24.19s**，145个Corki模块均来自安装包。没有真实模型/远程MCP或Rust测试；
离线模型仅证明Harness取消/恢复合同，不能证明真实模型选择或对保留输入的理解质量。

Codex仍固定ddf04ad26789d040f9ef6a96736f76602e35a6cc且干净；teach SHA-256仍为
816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a。用户docs/flow.md
暂存保留，无提交，无真实provider/记忆库写入。本批与上一批均为实质progress。

整体goal仍active。首笔Turn写入的上述取消窗口已关闭，但初始化rollback本身错误的
保留/关闭政策、预热与TurnStarted时序、中断marker、强制隔离及其他资源边界仍开放。
永久存储故障可使终态不能落盘，本批不承诺在数据库不可写或进程崩溃时恰好一次；
正常崩溃恢复与取消后的恢复是不同场景。其余A/B/C/D/E开放清单不因本批绿色而缩减。

## 第五十三批实施前决策：检查点回滚错误与连接关闭（A08/E04）

重新先读Codex session/handlers.rs::shutdown_session_runtime：CodeMode shutdown错误warn
后继续MCP等依赖；显式shutdown在live_thread.shutdown失败时warn并发Error事件，submission
channel关闭分支也warn。tools/code_mode/mod.rs::shutdown join已进行的初始化，不新建
未使用的服务。没有与Corki相同的LangGraph SQLite checkpoint rollback API；本批对齐
次级清理失败不遮蔽原始控制流、失败可见、其他资源继续关闭，不虚构Rust同名fallback。

Corki._initialize在setup失败/取消后用suppress(BaseException)退出context，回滚错误
没有日志、没有保留到close，context/checkpointer也不发布；若context未真正关闭连接，
连接所有权随局部变量丢失。正常_close_resources虽报告context退出错误，但无实际连接
fallback。已读当前安装的AsyncSqliteSaver.from_conn_string和aiosqlite.Connection.close：
前者async with连接yield saver；后者公开close在_connection=None时幂等返回，异常也在
finally stop。因此不应把所有close异常说成实际连接必然泄漏，但context提前失败时仍
需要针对持有的saver.conn释放，不能盲目重调一次性__aexit__。

拟复用专用checkpoint关闭helper：先退出context，失败则尝试实际conn.close，记录
fallback次级错误并保留首个context错误。初始化保留原始setup错误/CancelledError，
回滚错误记日志并留存；Runtime最终close仍关闭全部其他依赖后报告首个清理错误，
与既有Corki错误API一致而非冒称Codex同样抛API异常。覆盖setup失败、取消、正常关闭，
context真正关闭前/后报错，以及fallback也报错；测试用实际SQLite连接，故障注入不
宣称永久OS存储故障可被恢复。不重放模型/工具，不重复进入已消费的context。

## 第五十三批实施与验证：检查点清理的原错保留与 fallback（A08/E04）

新增core/checkpoint_lifecycle.py::close_checkpoint同时接入初始化rollback和正常Runtime
close。context退出失败先记录日志，再尝试saver.conn.close；fallback也失败则单独记录，
最终抛首个context错误，不重试已经消费的__aexit__。这基于实际aiosqlite连接close的
幂等合同，不是对任意资源close重放。_initialize捕获该清理错并保存在Runtime中，再
重新抛原始setup错误或CancelledError；成功重试也不清除先前清理诊断。_close_resources
以该首错为初始错误，继续所有依赖及当前checkpoint关闭后报告，不让后来的active
error覆盖它。没有把初始化失败伪造成已开始Turn，也不改变原有恢复/注册时序。

新增integration 12例：setup错误/取消/正常关闭×context实际关闭前/后报错×fallback
是否也报错。RED为11 failed/1 passed：真实连接仍打开、回滚日志缺失、fallback次错
不可见；本来已正确的“正常close、连接已关闭、无fallback错”场景仍保留回归。实现后
连同初始化、恢复及shutdown **58 passed/3.33s**。通过实际AsyncSqliteSaver/aiosqlite
连接验证关闭、原始setup异常对象身份/取消不变、context只退出一次、初始化成功重试
后模型只请求一次、两次Runtime.close报告同一个首错、model和repository各关闭一次。
fallback故障注入在真正close之后抛错，验证首错和次错同时可见；不宣称它证明了永久
不能关闭连接时也能完成释放。RED测试finally显式释放夹具连接，避免测试自身泄漏。

首次Ruff发现移除suppress用法后的unused import，已删除并重新check通过。最终全量
**1472 passed/85.29s**（LANGGRAPH_STRICT_MSGPACK=true）；Ruff check/312文件format、
compileall、pip check、Node bootstrap、git diff --check通过；examples独立
**3 passed/2.83s**。README补充清理错误可跨初始化重试保留，teach.md未改。

wheel `/tmp/corki-checkpoint-cleanup-wheel.QzMey2/corki-0.1.0-py3-none-any.whl`，
667135 bytes，SHA-256 `6a939a92095faa452a898a4e9ed49f7dcf6e55bd26f2befae49df91582f763e6`。
同目录installed从/tmp用python -I运行前批593选择+本批12：
**605 passed/23.37s**，146个Corki模块均来自安装包。未运行真实模型和Rust测试。
Codex固定ddf04ad26789d040f9ef6a96736f76602e35a6cc且干净；teach SHA-256仍为
816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a。用户docs/flow.md
暂存保留，无提交、真实provider或用户记忆库写入。前一批和本批均为progress。

整体goal仍active。此处只关闭已取得checkpointer后的上述清理诊断/连接fallback缺口，
没有证明__aenter__尚未交付连接时的所有创建失败、永久资源关闭失败或任意第三方
context的所有权都已解决。TurnStarted/预热时序、中断marker、强制隔离及其他A/B/C/D/E
开放项不变。下一相邻核对点是Codex的模型可见turn_aborted标记及其触发/保留/压缩合同，
已定位session/tests.rs中abort_regular_task_emits_marker_before_turn_aborted等场景，
不能把Corki已有model_interrupted采样控制字段当成该历史标记已实现。

## 第五十四批实施前决策：模型可见中断历史（A08/C/D/E04）

先读Codex tasks/mod.rs::InterruptedTurnHistoryMarker/from_config_and_version及
handle_task_abort：仅Interrupted原因写标记，Replaced不写；spawn_task先abort_all_tasks
Replaced。config/mod.rs从[agents].interrupt_message读取，缺省true；非V2（Disabled/V1）
为ContextualUser，V2为developer且措辞不声称用户中断。context/turn_aborted.rs定义
完整固定文本及generic.turn_aborted种类。取消任务收尾后record marker并flush，然后
才发TurnAborted；flush失败warn。session/tests.rs覆盖非协作及优雅取消marker先于
abort事件。Corki没有multi-agent V2，此批实现非V2单Agent路径，不伪称接入V2路由。

Context：contextual_user_message将TurnAborted识别为contextual user；event_mapping
不把它解析成用户提问，compact.collect_user_messages因此不重新保留它。原上下文中
它可以进入摘要输入；remote compact清理测试也去掉旧turn_aborted片段。Memory则不同：
memories/write/phase1.rs只排除developer和user中的AGENTS/skill片段，普通user角色
中断标记仍进入提取原始archive。不能把标记作为真实用户请求反复保留，也不能把它
误当world-state snapshot而在下一Step生成删除/tombstone。

Corki目前只有TurnCancelled事件和model_interrupted采样控制字段，历史中没有中断
原因/部分执行警告；后续模型与记忆提取无法看到这条事实。拟增加独立TurnAbortedItem
历史类型，接入SQLite/MsgPack、两HTTP adapter、token预算、legacy投影及memory archive，
不作为UserMessageItem或ContextItem。默认启用，兼容[agents].interrupt_message=false；
取消写入确定性ID并在TurnCancelled前可读，正常/失败/替换/仅steering不生成。compact
替换路径区分Replaced，其他显式stop/消费者关闭/runtime close仍Interrupted。取消前
先join CodeMode及其他工具收尾，避免结果晚于marker。原始marker保留供archive，压缩
不将其作为用户请求重注入。若marker已提交而cancelled终态写入失败，冷恢复应遵守
已持久的取消意图，不重新采样；这属于Corki双库恢复兼容实现，不声称Codex有同名表。

先以Runtime取消→后续真实HTTP payload、冷恢复、compact及memory归档、禁用/替换
路径复现；新增协议兼容性和预算测试。UI暂无通用RawResponseItem事件流，不在此伪造
该接口；核心验收为模型历史及取消事件可见前的持久化顺序。永久存储失败仍需如实
报告，不能将marker存在等同跨进程/文件系统恰好一次保证。

## 第五十四批实施与验证：非V2中断历史及恢复（A08/C03/C04/D02/E04）

新增独立TurnAbortedItem与context/interruptions.py固定指导文本、确定性turn级ID；接入
协议JSON/MsgPack allowlist、SQLite新旧历史投影、两HTTP adapter、token预算及memory
archive。它不是UserMessageItem，也不是会被world-state刷新移除的ContextItem。配置
缺省启用，读取[agents].interrupt_message且严格验证bool。TurnRun记录首个接受的取消
原因；Runtime.compact和CLI realtime compact均使用replaced，不因替换产生用户中断
标记；普通完成、错误和模型自身CancelledError也不伪造显式中断事实。

Runtime取消时先terminate进程并deactivate/join CodeMode，再写输入/marker/取消终态。
收尾或marker写入失败记录错误并保留取消控制流，不将取消改成成功。恢复RUNNING记录
之前检查同Turn已提交的typed marker：即使当前配置已禁用新marker，也遵守既有取消
意图，不重采样checkpoint。确定性ID使重复写入去重，SQLite既有合同忽略created_at
差异；不依赖用户文本中的标签识别取消。未增加V2 developer或原生RawResponseItem
事件流，不把当前单Agent实现描述为这些路径也已对齐。

首轮10个Runtime集成RED为10 failed/0.65s（缺配置字段/无历史marker），实现后
10 passed/1.34s；相关既有CLI/CodeMode/生命周期/写入测试76 passed/6.39s。继续补充
后曾出现CodeMode测试夹具错误：先错误期待nested结果直接进conversation，再错误
期待cancelled ledger调用complete_tool_call。源码合同实际是嵌套调用不伪造顶层历史，
中断后的第二次claim返回unknown cached error；已修正为观察该claim结果及handler
finally完成先于marker写入，而未改产品代码迎合错误断言。首次全量1491 passed/
4 failed/85.59s还暴露3个旧manual compaction夹具把“摘要未提交”等同“绝不追加任何
历史”；已保留原摘要提交断言并独立断言新中断标记，不弱化压缩原子性。

最终新增17个integration、7个protocol/config测试。覆盖两实际HTTP adapter×热/冷
后续Turn×启用/禁用，marker在随后两个Turn请求恰好一次、角色user、部分执行警告；
普通/失败/自身取消不生成；显式compact替换与CLI替换不生成；标记写入失败保留取消
且日志可见；实际QuickJS嵌套工具清理先于标记；标记提交后终态写入失败的启用/禁用
两种冷恢复都不重采样。组合场景实际取消→manual compact→原archive→新Runtime后台
提取/合并，断言摘要输入有marker、压缩窗口不再保留marker、用户请求仅保留一次、
原档案有marker、Phase1收到中断事实、最终extracted=1/consolidated=true/failed=0。
含该组合和既有CLI的中间针对性回归46 passed/2.09s；后再补一项禁用配置冷恢复。

前一轮最终进程输出在上下文交接中丢失、原session已不可读取，未将预期数字当结果；
重新运行完整回归取得可核对的 **1496 passed/88.71s**（LANGGRAPH_STRICT_MSGPACK=true）。
Ruff check及实际 **314文件**format检查、compileall、pip check、Node bootstrap、
git diff --check均通过；examples独立 **3 passed/2.88s**。README记录配置、清理顺序
及冷恢复合同，THIRD_PARTY_NOTICES记录Codex指导文本来源。

wheel `/tmp/corki-interruption-wheel.tAwBvr/corki-0.1.0-py3-none-any.whl`，668651 bytes，
SHA-256 `223d9f45990c4b0b544220df45a28c78ae91b31ec9a186d16d0d73ab4ac12cab`。
同目录installed从/tmp使用python -I验证前批605选择+本批24：
**629 passed/26.90s**，147个Corki模块均来自安装包。没有运行真实模型或Rust测试，
ScriptedModel/mock HTTP仅证明harness合同，不证明真实模型选择工具或理解中断的质量。
Codex固定ddf04ad26789d040f9ef6a96736f76602e35a6cc且干净；teach SHA-256仍为
816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a。用户docs/flow.md
暂存保留，无提交，无真实provider或用户记忆库写入。

整体goal仍active，本批与前批均有实质progress。此处只关闭已验证的非V2中断历史
路径；V2/developer/fork快照、原生事件接口、预热/TurnStarted时序、尚未取得资源时的
初始化失败、永久存储/清理故障、强制隔离，以及其余A/B/C/D/E开放项仍有效。未以
存储暂时可用的测试承诺永久故障可恢复或恰好一次。后续继续按源码确认开放项，
不得仅因当前全量绿色就将整体核心对齐标为完成。

## 第五十五批实施前决策：provider usage反馈至上下文窗口（C02/C03/A08）

先追踪Codex session/turn.rs::ResponseEvent::Completed→record_token_usage_info→
HistoryManager.update_token_info，context_window.rs再以get_total_token_usage判断窗口。
history.rs::get_total_token_usage取最近一次total_tokens而非累计账单，并加最后模型
item之后的本地item估算；server_reasoning_included控制旧encrypted reasoning补计。
history_tests.rs::total_token_usage_includes_all_items_after_last_model_generated_item
覆盖新增user/tool output。session/mod.rs::recompute_token_usage在替换历史后改为新窗口
本地估算，不能将旧请求usage继续用于新摘要。Total默认scope与BodyAfterPrefix配置
路径不同，后者及fallback buffer另有窗口基线，不在此冒称已实现。

Corki两HTTP adapter解析四项usage，SQLite model_steps与TokenUsageUpdated持久/展示，
但ContextWindowManager.prepare只调用estimate_request_tokens，usage没有返回决策链。
实际影响：provider已报告达到阈值时仍继续采样，或本地过估时过早压缩；冷恢复同样
忽略已保存usage。拟用最近已提交模型结果的usage及持久历史边界作为活动窗口基线，
新增本地user/工具结果/context继续估算；不是累计各Step usage，也不扣cached、不重复
加reasoning输出子集。显式total_tokens优先，缺省可用input+output兼容；完全缺usage
回退本地估算。压缩越过基线后使其失效，旧数据库/checkpoint兼容不伪造精确基线。

先添加真实Runtime多Step/跨Turn/冷恢复和compact后重置RED场景，再接入存储与窗口；
补两种HTTP的total解析和协议/迁移验证。失败采样不成为有效usage，新schema不进入
模型历史或长期记忆档案。保留现有完整请求本地安全检查，provider合同和未知usage
不能以零成本解释；原生reasoning header与估算分支依源码进一步核对再实作。

## 第五十五批实施与验证：usage反馈、窗口边界与reasoning补计（C02/C03/A08）

ModelUsage新增可选total_tokens和非负整数校验；显式total（包括0）优先，否则非零
input+output作为兼容值，cached/reasoning是子集不再加一次。两HTTP adapter保存total，
非法total仍走既有协议/完成错误与有界retry合同。SQLite model_steps增加可空total与
usage_anchor_id，和模型结果/历史同一事务提交；输出末item是基线，空输出使用当前
历史末item，不新增模型可见伪消息。latest按提交rowid而非秒级时间戳或累计usage选取；
新load_context_usage端口供ContextWindowManager调用。旧行有输出可从原ID恢复边界；
旧空输出无法证明基线时回退本地估算。重复模型commit不改变基线/结果，新记录无usage
时使用本地完整估算，不将缺失误认0，也不把过往高usage永久绑定后续窗口。这是面向
可选usage兼容provider的回退，Codex record_token_usage_info(None)本身保留既有token_info，
不宣称这个缺字段分支逐字相同。

新增context/usage.py将最近有效usage加上锚点之后的user/context/tool等本地item成本。
按ID判定已计费内容，压缩marker越过锚点或锚点不存在时基线失效，替换窗口回到本地
重估；usage不写入conversation/checkpoint，避免旧checkpoint协议增加类型依赖。Runtime
真实prepare自动接入，包括tool结果回灌、后续Turn、重启。自动阈值用该active usage；
既有完整请求本地硬上限继续保留，且provider报告硬上限时即便没有可压缩旧历史也
停止，不继续空采样到max_steps。摘要后仅用新窗口估算，不用旧usage反复压缩。

进一步读取Codex history.rs::estimate_reasoning_length：encoded UTF-8 byte长度×3/4
减650 envelope，下限0，再按4 bytes/token上取整。Corki encrypted ReasoningItem改用
该估算，不把summary明文再加一次；仅旧指令边界前encrypted reasoning在server未计入
时补计。codex-api/src/sse/responses.rs以x-reasoning-included头存在为准，不解析值；
Responses adapter记录同样的sticky capability并写provider_metadata随usage落盘。
新Runtime可先读取已保存metadata；新HTTP会话仍以其自身响应头观测为准。普通文字、
附件和其他encrypted内容成本仍有待完整审计，不能由这一种reasoning估算关闭全C02。

最初8个集成测试均失败，其中一项夹具误把同次response的assistant/tool分配两个
step_id；先修正夹具后重新RED取得 **8 failed/1.07s**，均为高usage未触发压缩。实现后
首次7 passed/1 failed：空continuation尚无可压缩旧历史，已将该组合场景补真实warmup
Turn，另专门补无旧历史/报告硬上限的测试；后者RED **1 failed/1.25s**，原实现继续
到evaluation预算耗尽，修复后context_window立即失败且仅采样一次，未放松输入保护。

新增integration 21例、unit/context 15例，最终针对性 **36 passed/2.54s**。集成包含
有/无输出×continuation/tool/tool大结果跨阈值/跨Turn/cold，压缩后下一Turn不重压；
低usage覆盖本地过估以及无usage回退；Chat/Responses实际SSE→持久usage→下一Turn
压缩（high/low/missing/invalid四分支），明确total优先、header存在即true。HTTP invalid
Responses夹具第一次只让首响应非法，随后返回成功，既有retry正确恢复；测试为单次
非法不提交usage场景显式model_max_retries=0，未修改产品重试语义迎合失败断言。
unit覆盖tail只计一次、压缩/未知anchor失效、旧/当前reasoning与header双分支、usage
子集/非法值、最新commit非累计、真实旧表迁移（含空结果）、相同模型commit去重，
以及实际SQLite在append输出后注入错误时usage/模型结果/历史一起回滚。中间连同
既有context/storage/models回归 **228 passed/8.48s**。

第一轮全量在HTTP夹具修正前加载，得到 **1526 passed/1 failed/89.25s**，失败即上述
invalid Responses被合法retry恢复的错误测试预期。最终重新完整运行
**1532 passed/91.52s**（LANGGRAPH_STRICT_MSGPACK=true）。Ruff check/317文件format、
compileall、pip check、Node bootstrap、git diff --check通过；examples独立
**3 passed/2.70s**。README及THIRD_PARTY_NOTICES记录反馈合同/限制及源码适配出处。

wheel `/tmp/corki-context-usage-wheel.dPivUY/corki-0.1.0-py3-none-any.whl`，670742 bytes，
SHA-256 `5a504d799b0a31ac4c92f963e9f0f0c7835302ba6150810c71bbdb5c8a4b1683`。
同目录installed从/tmp使用python -I运行前批629选择+本批36：
**665 passed/28.04s**，148个Corki模块均来自安装包。Codex仍固定
ddf04ad26789d040f9ef6a96736f76602e35a6cc且干净；teach SHA-256仍为
816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a，用户docs/flow.md
暂存保留。未提交、未运行真实provider/Rust测试、未写用户记忆库；mock只证明Harness
链路，不证明真实模型选择或token估算精确率。

整体goal仍active，本轮与前轮均为progress。已接上真实usage反馈链，但BodyAfterPrefix、
fallback buffer、remote/server compact、完整动态schema/instructions增量成本、累计
usage/total事件诊断与其他provider能力、完整取消/恢复组合仍开放；其他A/B/D/E范围不变。
旧空结果没有可恢复锚点时的本地回退不是假定精确一致，也不将当前安全硬上限当成
Codex全部provider路径已对齐。

## 第五十六批实施前决策：BodyAfterPrefix窗口基线（C02/C03）

先完整读取Codex state/auto_compact_window.rs及其tracks_prefill_and_window_boundaries：
prefill分Estimated/ServerObserved，首次server input替代estimated，之后不再变化；output
属于body增长。state/session.rs替换/新窗口清prefill；session/mod.rs恢复历史时用完整
恢复历史估算前缀，下一次server usage才替代，不把旧会话第一次usage永远当新会话基线。
context_window.rs默认Total按model auto limit；BodyAfterPrefix用active减prefill，显式
scope limit不按90%截断，但full context hard cap独立生效。无fallback prompt时buffer0。
已读core/tests/suite/compact.rs三个集成场景：忽略起始prefix、压缩后重新计算增长、
body预算很大仍受完整窗口限制。TokenBudget/fallback提示与模型切换路径另有状态机，
不借scope枚举声称一起实现。

Corki当前仅Total，缺scope配置、首次input基线与reset；上一批usage只带total/anchor，
不能区分同anchor的空采样或取服务端input。拟为ContextUsage补原采样身份/input，仍
从既有model_steps读取，不新增模型历史；WindowManager持有与Codex相同的会话窗口
prefill状态。当前窗口首个新usage替代估算后冻结；压缩后重置，冷Runtime从恢复历史
估算起步并忽略旧会话usage作为新的server prefill。兼容缺usage provider以首次请求
本地估算起步，避免把无限增长都扣成prefix，此分支明确为本地兼容回退。

新增配置接入实际Runtime与默认配置模板，缺省Total不变；Body显式auto limit不套Total的
90%上限，但自动判断同时检查完整窗口。先用连续Turn/压缩两轮/冷恢复/完整窗口
上限的行为测试RED，再验证无usage估算、服务端替代及失败不重置，保留原始archive。

## 第五十六批实施与验证：BodyAfterPrefix实际窗口状态（C02/C03）

新增CorkiSettings.auto_compact_token_limit_scope，允许total/body_after_prefix，默认total；
读取[agent].auto_compact_token_limit_scope并严格拒绝其他值，默认config模板给出选项。
Runtime传入WindowManager，Body显式auto_compact_tokens不按Total的90%限制截断，同时
自动条件独立检查完整usable窗口。未把body预算与上下文硬上限混成一个阈值。

context/usage.py::BodyPrefixWindow持有会话内窗口ID、估算/服务端prefill状态和新窗口
建立时已存在的采样ID。ContextUsage从既有SQLite行增加input_tokens/sample_id（没有
新表/列/checkpoint字段），因此空输出共享同history anchor时仍可区分不同采样。
首个新采样input替代估算后冻结，output及之后的user/context/tool增长计入body。
冷Runtime以恢复历史估算为prefix，并暂用完整请求本地估算替代旧会话active usage；
否则旧会话高usage减去新会话估算prefix会立即误触发压缩。下一次新采样才建立新的
server prefill。缺usage provider使用固定的初始请求估算基线，之后不是每Step把整个
增长都重设为prefix；这是兼容回退，不伪称Codex未收到usage时一定执行同一算法。

自动替换成功后reset至新marker和替换窗口估算；manual compact也在append成功后reset。
摘要采样usage不属于新窗口第一条正常采样，不记录为server prefill；压缩失败不会
清除旧基线。冷恢复/已提交替换但尚未更新内存时，prepare看到不同marker也会重建
估算基线。所有prefill都是Runtime会话状态，未将旧server prefix永久跨会话保存，
符合本次读取的Codex恢复估算路径；原始conversation/archive不增加内部预算消息。

最初三个Runtime场景RED **3 failed/0.46s**（配置未实现）；接入后
**3 passed/0.80s**。随后扩展为ScriptedModel/Chat/Responses三种路径，各覆盖连续
两次窗口压缩、冷Runtime估算→首个新usage替代、body limit超过raw window时仍触发
full cap；实际HTTP输出经adapter/SQLite/window而非直接调用判断函数。unit按Codex
state测试映射estimated→first server input→后续不移动→替换reset，明确首次output
仍算增长，同anchor不同采样可区分；补缺usage固定估算及TOML有效/无效配置。
上述 **18 passed/1.57s**。

继续补manual成功（摘要报告高usage不污染后续窗口）、manual失败（次次auto仍按旧
body增长触发）和无usage真实Runtime→压缩；连同前批usage/config/context选择
**106 passed/4.76s**。再补Direct及实际QuickJS CodeMode工具结果增长→mid-turn compact
→后续模型完成，两个场景工具handler各只执行一次。最终新增14 integration+9 unit，
针对性 **23 passed/1.94s**。与源码三个场景的数字量级不同，以适应Corki完整内置
instructions成本；验证的是各请求/窗口之间的关系，不假装用小于真实固定prompt的
窗口也能采样。没有因夹具误差更改既有输入保护或工具副作用策略。

首次全量在最后两个工具组合加入前收集，**1553 passed/89.58s**；随后完整重跑取得
**1555 passed/94.14s**（LANGGRAPH_STRICT_MSGPACK=true）。Ruff check及319文件format、
compileall、pip check、Node bootstrap、git diff --check通过；examples独立
**3 passed/2.59s**。README及默认配置模板记录scope/冷恢复/缺usage兼容回退，
THIRD_PARTY_NOTICES补充Codex窗口状态与scope判断出处。

wheel `/tmp/corki-body-prefix-wheel.kjlYUc/corki-0.1.0-py3-none-any.whl`，671863 bytes，
SHA-256 `00da9b280c68d87b3bcdc46eb575c4fa446ac192feaeb21a4588bcbe983423f9`。
同目录installed从/tmp使用python -I运行前批665选择+本批23：
**688 passed/32.41s**，148个Corki模块均来自安装包。Codex固定
ddf04ad26789d040f9ef6a96736f76602e35a6cc且干净，teach SHA-256仍为
816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a；用户docs/flow.md
暂存保留。未提交、未运行真实模型/Rust测试、未写用户记忆库。真实HTTP在此指实际
adapter经MockTransport的离线请求，不是远程provider请求或真实模型工具选择验证。

整体goal仍active，前批与本批均为progress。此处关闭上述BodyAfterPrefix基本窗口
状态差异，不关闭TokenBudget/fallback prompt与buffer、model switch/downshift组合、
remote/server compact、完整provider usage诊断/硬上限兼容差异和其余A/B/C/D/E项。
特别是fallback buffer只在有fallback prompt时生效，不能下一批简单把阈值加一个
数字却不实现一次性提示、预算结束与新窗口状态转换。

## 第五十七批实施前决策：TokenBudget默认显式启用路径（A/B/C）

完整追踪compact_token_budget.rs→Session.start_new_context_window→replace_compacted_history/
recompute_token_usage：该feature不是摘要预算扩容，而是无模型/无远程compact请求的
新窗口替换，清除旧用户、助手、reasoning、call/result，只重建当前world state；环境
不重置，原rollout仍保留。manual与auto共享compaction生命周期。RetainClientDeveloper
另有条件分支，不能把当前本地摘要保留用户的合同照搬到此实验路径。

继续读取session/token_budget.rs/config.rs/turn.rs：TokenBudgetConfig默认notes=false、
reminder threshold=None、guidance=None、fallback=None，故显式feature enabled无附加
配置时不存在提醒/缓冲。自定义fallback仅有prompt时才启用buffer；普通模型采样后
先计算needs_follow_up及should_roll_over，再记录每窗口一次reminder/fallback。reminder
是developer，即便本次完成也可记录；fallback只在剩余0且尚不需rollover时发出，正常
工具面继续可用。超buffer/完整硬上限或new_context请求才reset，不能让fallback
强制本已完成的Turn续跑。自动资格还涉及账户/后端与模型默认配置，非所有API key
默认启用。已读suite/token_budget的手动/中途reset、fallback保留工作文件/耗尽buffer、
new_context跳过fallback、reminder跨阈值测试。

tools/spec_plan.rs1206注册new_context为DirectModelOnly，get_context_remaining普通Direct；
前者只请求后续窗口切换，不清环境；后者文本返回剩余tokens，CodeMode返回结构化
{"tokens_left":...}。context/token_budget_context.rs窗口身份独立developer消息，包含
agent/first/current/previous id；guidance应在其之前。不能把new_context暴露为JS嵌套工具。

Corki实际链为window.prepare/compact→_summarize→CompactionItem，始终保留用户；Graph
execute_tools只应用plan state_update，缺新窗口请求；无两专用工具/窗口developer标识。
默认实现先补明确的显式feature=true、TokenBudgetConfig默认值路径：无摘要reset、
稳定窗口身份、现有append-only历史与checkpoint兼容、两个工具接真实Runtime，body
基线随reset重置。随后再实现自定义reminder/fallback与模型默认/notes扩展路径，不
将这批默认配置当整个TokenBudget已经完成。该顺序是窗口状态机的依赖顺序，不改变
最终范围；不添加未接入的reminder/buffer枚举或让其落入旧摘要路径。

需要新增CompactionItem的显式reset标记（默认false保持旧JSON幂等）、ToolStateUpdate
的新窗口请求（默认false省略旧payload），模型视图不渲染空摘要占位；原档案保留旧
消息，side effect ledger/文件/进程状态不清空。new_context通过结果记录请求，恢复可
重用已提交结果并按active window判定一次消费，不能仅靠进程内bool。先以Runtime
manual、auto、工具请求、Direct/CodeMode曝光、原历史保留与新窗口身份编写RED。

## 第五十七批实施与验证：默认TokenBudget无摘要窗口重置（A/B/C）

新增token_budget_enabled=false，读取[features] token_budget布尔值并接入Runtime；
仅显式true启用，不自动猜账户资格或model-owned配置。默认模板给出关闭状态示例。
目前非布尔feature表明确报不支持，不会静默忽略提醒/buffer配置而启用不同行为。
README明确这是Codex显式feature enabled、TokenBudgetConfig默认无附加提示的路径。

CompactionItem新增context_reset=false，true只能带空summary；active_history重建完整
replacement后排除该内部marker，token成本0，legacy原始消息投影也不造空developer
摘要。false时payload省略新字段，旧JSON/MsgPack/default语义与既有幂等比较保留。
window._reset_context用marker+全量当前snapshot原子append替换窗口；manual捕获当前
world-state而非调用_summarize，auto在prepare达到窗口限制或看到成功new_context结果
时走同一reset。mid-turn不保留旧用户/工具消息；pre-turn新pending输入/附着信息随
新window保留。原始记录不删除，不清side-effect ledger或环境；完成替换后重算tools
表及BodyPrefix估算，避免旧deferred定义成为新窗口预算。manual不再发本地摘要警告。

context/token_budget.py追加独立developer context_window section，包含/root、first/
current/previous身份。初始ID使用thread派生的稳定UUID，后续ID复用持久CompactionItem
ID，故无新数据库列也能恢复身份；不是Codex的UUIDv7实现，也未接原生HTTP窗口metadata
header。该单Agent、模型可见身份合同与native请求诊断是不同验收项，后者仍开放。

新增NewContextTool（DirectModelOnly）和GetContextRemainingTool（Direct），只在feature
启用时注册。new_context返回ToolStateUpdate.new_context_requested=true，由持久工具
result而非临时bool承载；reset后的active历史不再含该结果，所以旧结果重放/冷启动
不会重复消费请求。字段贯穿executor规范化、ledger与ToolResultItem序列化，false
省略旧payload。get_context_remaining调用实际window usage/BodyPrefix，剩余值取body/
total limit与完整窗口limit中的较小者、下限0；CodeMode通过CodeModeOutput返回
{"tokens_left":...}，不是把文本当JSON猜测。读取当前状态时排除尚未落盘的合成aborted
输出，避免把正在执行的查询自身当成失败Observation计费。

最初6 Runtime场景RED **6 failed/0.39s**；首实现4 passed/2 failed/1.01s暴露executor
旧规范化仅复制plan、丢弃新的请求字段，已修复真实执行链而非放松断言；随后
**6 passed/0.94s**。既有protocol/storage/manual生命周期/CLI选择 **59 passed/0.92s**。
新增remaining曝光测试一度错误把nested_specs字典当spec列表迭代，修正夹具为查key；
随后Total/Body×Direct/CodeMode精确剩余数与ModelOnly隔离通过。实际HTTP测试中未知
Chat兼容provider按既有capability将developer映射system，改为断言这一兼容角色，
未修改adapter冒充具备developer能力；Responses保持developer。

最终新增16 integration+8 protocol/config，针对性 **24 passed/2.13s**。覆盖manual/
auto/new_context×Direct/CodeMode，模型请求数证明无摘要采样；新窗口first/previous链、
旧user/assistant退出active view而原记录和工作文件保留；get_remaining两scope下真实
预算及typed CodeMode输出、new_context不进入JS工具面；真实两HTTP manual→下一Turn
没有额外摘要请求，原archive仍可提取前后用户事实；reset append提交前/后注入OSError
再冷Runtime，新用户保留、工具call总数1且reset总数1。unit还覆盖JSON/严格MsgPack、
默认字段省略、legacy无空摘要、ledger冷读state_update、配置有效/无效值。

最终全量 **1579 passed/93.35s**（LANGGRAPH_STRICT_MSGPACK=true）。Ruff check/323文件
format、compileall、pip check、Node bootstrap、git diff --check通过；examples独立
**3 passed/2.57s**。README/default config与THIRD_PARTY_NOTICES记录启用方式、重要的
无摘要语义、适配源码和未实现范围；teach.md未改。

wheel `/tmp/corki-token-reset-wheel.9JuMXC/corki-0.1.0-py3-none-any.whl`，674857 bytes，
SHA-256 `f741dea7a3244f4cdb059fe663d9f960108cef5ae4c9262fcb8d86d9abab06ed`。
同目录installed从/tmp使用python -I运行前批688选择+本批24：
**712 passed/30.88s**，150个Corki模块均来自安装包。Codex固定
ddf04ad26789d040f9ef6a96736f76602e35a6cc且干净；teach SHA-256仍为
816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a，用户docs/flow.md
暂存保留。未提交、未运行真实provider/Rust测试、未写用户记忆库，HTTP是离线mock。

整体goal仍active，本批与上批均为progress。窗口reset及专用工具默认路径已接Runtime，
不是整个TokenBudget完成：custom reminder/guidance/fallback+buffer、post-sampling一次性
提示、自动资格/model默认覆盖、history-notes、retained client developer与hooks仍需
后续源码映射实现；未知/unbounded窗口、原生header/output schema与多Agent身份也未
由这些测试证明。文件保留不代替全部活跃process/cell组合的验证。其他A/B/C/D/E范围
不变；下一依赖项是让附加提醒/缓冲状态机建立在已验证的reset上，而不是退回摘要。

## 第五十八批实施前决策：TokenBudget提示与fallback窗口（C/E）

重新核对Codex session/token_budget.rs::maybe_record与turn.rs497–525：有效采样后、工具
收尾及pending状态确定后计算remaining，reminder达到阈值每窗口一次；即便本次终止
也记录，不强行续采样。fallback要求remaining=0且没有本次rollover/完整限制触发，
已请求new_context时不发fallback。remaining不含buffer，自动reset阈值才加buffer，
完整窗口独立硬上限不扩容。reminder和fallback都是developer历史，不是可替换的
world-state或真正用户请求；窗口替换后删除，不作为记忆原始用户事实。

config/mod.rs默认/validate/resolve_token_budget_config：各文本UTF-8上限2000 bytes，
reminder模板非空、阈值/buffer正整数；TOML fallback trim且空值变None，guidance过滤
纯空但不trim有效正文；只有存在fallback prompt才给buffer，单独buffer不延迟reset。
feature配置table含enabled，可显式关闭；unknown字段由源码deny_unknown_fields拒绝。
窗口guidance在独立context_window developer消息之前。保留use_history_notes_extension
未实现边界，不能吞掉true假装支持；账户/模型默认及hook仍按既有开放项处理。

Corki现有reset分支与本地summary分支并列，增加buffer时必须防止后者在未加buffer
阈值处抢先摘要；需同时接入post-sampling语义，而非仅prepare前插提示。拟新增
TokenBudgetConfig、typed BudgetNoticeItem与窗口内持久去重，接两个adapter/预算/serde；
在正常continuation、工具全部回灌后及finalize记录，steering半次采样不记录。记录失败
属于辅助提示故障，告警且不把已完成工作改成失败；CancelledError仍传播。成功持久
后按ID跳过，失败可再次尝试，不重放模型或工具。该持久去重是Corki恢复兼容实现，
不声称Codex claim内存flag提供跨进程恰好一次。

验收映射suite/token_budget中保留工具面/工作文件直至new_context、超buffer强制reset、
new_context跳过fallback、reminder跨Turn；另补Total/Body、硬上限、不强制已完成Turn
续跑、重置后新一轮提示、禁用/无prompt buffer、配置/角色/持久化及取消故障窗口。

## 第五十八批实现：显式TokenBudget提示与缓冲状态机

基准仍为固定Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc，源码路径均相对其
codex-rs：core/src/session/token_budget.rs156–213、session/turn.rs497–525、
session/context_window.rs、context/token_budget_context.rs；配置core/src/config/mod.rs
1130–1242和features/src/feature_configs.rs312–345；suite/token_budget.rs720–771、
1324–1486为reminder及保存文件/超buffer/reset验收映射。

新增config/TokenBudgetConfig，保留bool开关，table需显式enabled=true；未自动推导账户/
模型资格。支持默认提醒模板、阈值、guidance、fallback与buffer；按UTF-8字节与正i64
校验。unknown字段及disabled表中的错误字段类型也拒绝；disabled不检查正数/非空语义。
guidance仅过滤全空、有效正文不trim；fallback trim后全空转None。只有存在prompt才有
buffer；history-notes=true明确报尚未实现，不假装具备持久notes接口。

ContextWindowManager将budget_status与remaining共用计算；remaining仍使用unbuffered
阈值及完整hard cap，自动reset用base+buffer且不得落入旧摘要分支。prepare/reset注入
guidance到独立context_window之前。新增BudgetNoticeItem，区别于UserMessage与可替换
ContextItem，接JSON/严格MsgPack/SQLite/legacy view/token estimator与两个真实adapter；
Responses为developer，Chat沿用当前system兼容映射；memory archive不将其作为用户事实。

Graph普通CONTINUE、完整tools drain以及完成时pending判定后记录提示，steering半次
采样不提前记录。reminder达到阈值可在完成时记录，不为提示强制再采样；fallback仅在
remaining=0且没有rollover/hard limit时写入。new_context与超buffer都走既有无摘要reset。
每窗口notice ID按window/kind确定并持久去重，重启保留一次性状态，新窗口允许再次提示；
旧提示仅留raw history，不留在新active window。普通写入异常告警，不重放已完成模型/
工具；CancelledError不吞。这是Corki恢复兼容方案，Codex源码claim内存flag并不等价于
跨进程持久exactly-once，故明确记录实现差异。

最初6个新Runtime场景在实现前全部失败（缺少TokenBudgetConfig）；恢复执行后同6个
通过1.02s。新增cold/跨Turn/guidance/无prompt/hard limit/notice写入故障场景后13通过、
取消夹具1失败：原断言期望stream返回TurnCancelled，但Runtime契约会向调用方传播
CancelledError。改为断言传播且没有TurnCompleted，未修改Runtime掩盖取消。随后含真实
Chat/Responses请求体、JSON/MsgPack/legacy、UTF-8配置等37通过2.17s；再加无工具的
CONTINUE/正remaining模板重复替换，39通过2.32s。最后补disabled表类型校验5例，进入
全量与安装包验证，最终结果记录如下。

最终新增18 integration+26 protocol/config，针对性 **44 passed/2.00s**；全量
**1623 passed/96.40s**（LANGGRAPH_STRICT_MSGPACK=true）。Ruff check/326文件format、
compileall、pip check、Node bootstrap、git diff --check通过；examples独立
**3 passed/3.10s**。README/default config与THIRD_PARTY_NOTICES已说明自定义配置及
无摘要重置语义，保留未实现边界；teach.md SHA-256仍为
816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a。

wheel `/tmp/corki-budget-notices-wheel.KRVCNv/corki-0.1.0-py3-none-any.whl`，678022 bytes，
SHA-256 `119c7de0dec5b43d1d8dcb6acaa36a006a3d62128336ec57e073db383b36e79b`。
从/tmp使用python -I运行前批712选择+本批44，安装包 **756 passed/33.66s**；151个
Corki模块均来自上述installed目录。Codex固定commit且工作区干净；用户docs/flow.md
暂存保持。未提交、未运行真实模型或Rust测试，未写用户记忆库；HTTP为离线mock，预设
tool calls证明Harness状态流，不证明真实模型工具选择质量。

本批完成的是显式配置的reminder/guidance/fallback+buffer分支，不是整个TokenBudget或
整个Harness对齐。history-notes扩展、账户/模型默认激活、reset hooks与retained client
developer语义、原生context header/output schema、未知窗口仍需后续源码映射。完整
CodeMode活跃cell/compaction/取消组合没有由本批直接工具测试覆盖；其余A/B/C/D/E的
provider、动态schema、memory Phase2/来源/forget等开放项保持，整体goal仍active。

## 第五十九批实施前决策：history-notes依赖的加密工具结果（B/C/E）

上批属progress（实际状态机及测试改变）。本批先追踪ext/history-notes全部backend与
extension、tools与对应tests，发现它不是本地文件工具：extension::update_config要求
use_history_notes_extension、OpenAI provider及Codex backend认证；九个history/notes
namespaced工具均DirectModelOnly，只有append/write独占。backend::call覆写可信session/
agent context，35s请求、指定输出截断header；search/append/write另标encrypted arguments。
后端实现未包含在此仓库，客户端不能将opaque结果当作明文或宣称解密等价。thread_hint
为最多4000 UTF-8 bytes的ContextWindow fragment，失败不注入且不退回旧bridge；
session/session.rs::responses_metadata还要求history_ingest_requested与窗口元数据。

真实链路的先决缺口：HistoryNotesToolOutput把encrypted_output转成独立EncryptedContent，
图像另附；Corki ToolContent目前仅text/image/audio，executor拒绝加密块，estimator/CodeMode
部分分支会将未知非文字块当图片/音频。不能先注册九个名字而让真实结果丢失。先补typed
encrypted tool output完整通路，继续保留extension/backend/ingest与本地兼容方案为未完成。

参考protocol/models.rs::FunctionCallOutputContentItem、tools/tool_output.rs242–273、
utils/output-truncation/src/lib.rs：加密块原样保留，不消耗普通文本截断预算，不进入JS文本
结果。core/context_manager/history.rs834–836、1084–1123：FunctionCallOutput payload按
ceil(encoded UTF-8 bytes*9/16)估计解密后字节，不是reasoning的base64减650；custom output
尚未应用该discount，应保守计费。Corki新增provider显式能力门，Responses原生透传；
不具备能力/Chat仅向模型说明不可读取，不把密文伪装成文本/自动重试，原记录保持。完整
请求hard cap仍计费，超过本地32MB传输安全界限的结果报Observation而非截断密文。

验收需从实际Runtime handler→ledger→history→两HTTP/冷重启，验证精确opaque字节、
普通文本预算不破坏密文、能力降级、JS投影、MsgPack/JSON/旧数据兼容、token estimate及
大结果硬上限。此批不是history-notes扩展完成；不能以此关闭C05或长期记忆整体差异。

## 第五十九批实现：opaque工具结果的真实Harness通路

新增EncryptedContent字段类型（opaque字段不出现在repr）、payload kind与MsgPack allowlist；
旧text/image/audio及无content_items的编码不变。executor验证UTF-8文字与共享32MB binary/
opaque传输界限，不做base64猜解；错误按既有Observation预算处理，取消仍走原控制流。
普通text截断保持opaque整块原序，完整预算按ceil(encoded_bytes*9/16)再ceil(/4)计算
function输出，freeform保持保守原字节成本，不误用reasoning减650或固定image成本。

Responses读取provider显式supports_encrypted_tool_output能力并传native encrypted_content
块；CorkiSettings/TOML→Runtime组合已接入，默认false且Chat配置true拒绝。不支持的
Responses/Chat以明确不可读notice代替请求视图中的密文，不删除raw history/ledger，
不重采样或重跑工具以伪造降级成功；这不等于解密或完整兼容history恢复。ImagePreparation
保留opaque并继续处理图片；JS默认工具结果投影忽略opaque，cell不能自行emit这一内部
内容类型。UserMessage输入不接受该function-output专用variant。README/default config/
THIRD_PARTY_NOTICES明确能力条件、32MB保护、预算和未完成的history-notes边界。

RED最初三个实际HTTP Runtime场景全部ImportError（无EncryptedContent）。实现后
**3 passed/0.87s**。增加预算/JS/codec测试后20通过2失败，均为夹具：JS返回真实换行
而非字面反斜杠n；40字符错误预算会截断断言所需"byte limit"，该检查改1024预算。
随后加入pre/post history append失败时，Chat pre-commit场景暴露测试混淆：正常写入
FAILED终态的Turn不能用新stream代替resume_pending。改为同时注入terminal commit失败，
保留实际running记录，冷Runtime显式resume_pending再开启新Turn。既有Runtime会将终态
写入错误报告为storage TurnFailed，而非直接抛OSError，修正夹具断言，未改恢复代码。

最终针对性 **28 passed/1.64s**（11 integration+17 protocol/config）：真实三种HTTP能力
×无故障/pre/post提交9场景，恢复与下一Turn共3次采样且工具副作用1次，密文和有效PNG
有序透传、fallback不含密文、raw记录精确保留；实际600KB密文令direct TokenBudget重置
且无额外summary采样，真实QuickJS则只接收before/after、不因不可见nested ledger密文
虚耗active预算。JSON/严格MsgPack/SQLite ledger冷读、诊断投影、UTF-8 token边界、
freeform保守计费、零文本预算保留密文与图片、非法类型、32MB Observation错误及配置
能力边界均通过。全量和安装包验证继续记录如下。

最终全量 **1651 passed/91.84s**（LANGGRAPH_STRICT_MSGPACK=true）。Ruff check/328文件
format、compileall、pip check、Node bootstrap、git diff --check通过；examples独立
**3 passed/2.62s**。Codex固定ddf04ad26789d040f9ef6a96736f76602e35a6cc且干净；teach
SHA-256仍为816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a，
docs/flow.md用户暂存保持，未提交。

wheel `/tmp/corki-encrypted-output-wheel.jQzUXf/corki-0.1.0-py3-none-any.whl`，679227 bytes，
SHA-256 `b8496e9e9c3c12ffca2995e197d4158e62ee3dd1c1e4cbf18af9b1fc14823c63`。
从/tmp以python -I验证上批756选择+本批28，安装包 **784 passed/34.73s**，151个Corki
模块全部来自上述installed路径。未执行真实provider、Codex backend或Rust测试；HTTP
与密文均为确定性夹具，不声称密文真实可解密或真实模型已正确选择工具。

本批为progress，整体goal保持active。已补的是history-notes真实结果接入的必要依赖，
不是用一个更小功能替代按需历史召回。后续须继续完成服务端工具/namespace接入、可信
session-agent及window/ingest元数据、认证资格/失效、thread_hint生命周期与失败隔离，
并为不支持原生服务的provider实现明确的有效恢复方案；不可拿当前不可读notice算作
该方案。notes服务端存储/一致性实现不在本地Codex仓库，不能依据客户端schema臆造后端
已验证语义。TokenBudget hooks/激活、remote compact和其余A/B/C/D/E开放项范围不变。

## 第六十批实施前决策：工具namespace定义与调用身份（B/C/E）

上批为progress。继续追踪history-notes服务接入先决条件：Codex的九个工具使用history/
notes namespace，response ToolName保留namespace与name两字段；spec_plan.rs425–478先检查
namespace描述冲突，900–956 merge_into_namespaces按首出现顺序合并、组内工具名排序，
空描述补默认。Corki responses/tool_search.py仅把非functions调用转namespace::name，
却没有匹配的namespace schema输出；直接注册会未知工具，扁平化还会串到同名工具。
此次修复通用namespace真实链路，供后续服务端工具使用，不将服务/认证/ingest算完成。

保留Corki已有canonical name字符串，显式namespace用namespace::leaf；默认functions仍
用plain name。协议新增可选namespace_description并省略默认字段以兼容旧编码。Responses
native mode应按namespace输出合并定义、调用历史输出独立namespace；兼容Chat/Responses
使用确定性API安全名称，并在本Step已有工具/发现定义范围内反向映射，不能只按leaf路由。
名称映射碰撞/冲突描述须在采样前失败。定义、发现历史、恢复、freeform的namespace身份
不能随模式切换消失；CodeMode内部canonical身份不需要跟着HTTP别名改变。

显式tools.namespace_mode/native provider能力独立于tool_search/freeform模式。验收包含
同leaf不同namespace与default工具、native及兼容请求、搜索加载/跨Step/冷恢复、原生
freeform/JSON wrapper、未知namespace不越权命中、冲突和legacy字段兼容。哈希兼容别名
是Corki适配策略，非Codex原生命名；描述保留完整namespace语义，完整schema成本仍计费。

## 第六十批实现：native与compatible namespace工具身份

核心新增protocol/tool_names.py及models/namespaces.py：canonical namespace::leaf在registry/
ToolCall/ledger/checkpoint中保持；普通工具name不变，functions默认namespace仍归plain。
ToolSpec.namespace_description仅非None时序列化。Native Responses按首出现顺序分组、
组内排序、空description补Codex默认（functions为空）；兼容名称corki_ns_+48位SHA-256
hex，最长57ASCII字符，描述含完整canonical身份与namespace说明，碰撞预先拒绝。

Settings tools.namespace_mode→Runtime capability→ModelRequest接入prepare/model循环。
两个adapter先校验当前request+已发现定义的alias/description，兼容返回先还原canonical
再进行freeform wrapper解析；原生返回保留namespace，不按leaf猜测。CustomCalls同样
接alias映射。native_search_output分组独立于freeform与namespace开关，legacy默认输出
保持；回放call按当前传输模式输出，不改原记录。namespace description接搜索语料与
dynamic search-info缓存输出，初轮不曝光deferred schema，搜索namespace语义后可加载。

发现CodeMode原有normalized-name setdefault会静默隐藏别名冲突工具，已改显式拒绝；
同leaf不同namespace/default的无冲突JS名称经真实QuickJS分别调度canonical handler。
此处仍是兼容JS扁平面，不声称完整Codex原生CodeMode namespace协议。MCP namespace
owner、special-tool namespace碰撞与host来源映射继续开放，不能只拿source描述冒充owner。

RED最初3个真实HTTP场景全部失败（ToolSpec无namespace_description）；接通后
**3 passed/0.76s**。新unit与integration起初同名test_tool_namespaces.py导致pytest导入
冲突，unit改为test_namespace_identity.py；未删除用户文件或用importmode掩盖冲突。
随后namespace merge/配置/alias及8种search/freeform/namespace组合 **28 passed/1.70s**；
加真实CodeMode和拒绝静默冲突，并跑原deferred/CodeMode配置选择，**52 passed/3.29s**。
再补未知namespace与default functions分派、alias/description/capability三类故障零HTTP
请求，当前新增35测试 **35 passed/2.21s**，进入完整验证。

第一轮全量1686通过96.47s，examples独立3通过3.00s。最终复核增加native↔compatible
跨重启模式切换，原始canonical calls不变、回放精确投影为namespace/name或原alias，
不再只验证同模式恢复。另发现alias最多57字符可能长于原canonical name，补完整请求
ToolCall预算取两者较大估算（plain name不变），避免为兼容名称凭空少算tokens；新增
ASCII/Unicode/长canonical边界3例。新增最终40个测试，针对性40通过2.87s，再跑最后
状态的完整回归和安装包；该预算是保守兼容成本，不声称原生JSON序列化完全同字节。

最终新增19 integration+21 protocol/config；末次针对性+examples组合 **43 passed/5.17s**。
最终全量 **1691 passed/98.15s**（LANGGRAPH_STRICT_MSGPACK=true），Ruff check/332文件
format、compileall、pip check、Node bootstrap、git diff --check通过。README/default config/
THIRD_PARTY_NOTICES已更新。Codex固定ddf04ad26789d040f9ef6a96736f76602e35a6cc且干净；
teach SHA-256仍为816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a，
docs/flow.md暂存保留；未提交。

wheel `/tmp/corki-namespace-wheel.4irUYD/corki-0.1.0-py3-none-any.whl`，682049 bytes，
SHA-256 `eee7b4deb22d9014452108f8284d0a42a97f961a640b47b966d34db3f6bd8632`。
从/tmp以python -I执行上批784选择+本批40，安装包 **824 passed/35.85s**，153个Corki
模块全部来自上述installed目录。未运行真实provider、Codex backend/Rust测试；HTTP
mock和预设calls验证Harness分派与状态，不证明真实模型选择质量。

本批为progress，整体goal保持active。history-notes依赖的opaque输出和通用namespace
现在已接实际循环；服务端九工具、可信session/agent/window/ingest、认证能力与失效、
thread_hint生命周期及非原生provider有效历史恢复仍未实现，不以本批替代或关闭这些
工作。B02/B05的MCP namespace来源/owner与实验目录、B06完整JS namespace、安全隔离/
生命周期，TokenBudget hooks/激活/remote compact与其它A/B/C/D/E开放项维持完整范围。

## 第六十一批实施前决策：native history-notes服务端接入（B/C/E）

前批为progress。重新追踪ext/history-notes/backend.rs、extension.rs、tools.rs及tests：
九个DirectModelOnly工具、读并行/写独占；POST alpha/{history|notes}/v2/action，可信context
覆写模型输入；35s、不重试、输出预算header，search/append/write标加密参数；结果中
images独立处理，encrypted_output成为opaque块；非法图片/网络/JSON错误为Observation。
hint最多4000 UTF-8 bytes，失败不破坏Turn且不走旧MCP bridge。另追踪core/session/session.rs
responses_metadata与responses_metadata.rs：client_metadata里session/thread/turn/window，
x-codex-turn-metadata JSON含agent_name、context_window_id、从0开始window_number、
history_ingest_requested与request_kind=turn；兼容headers保留两个x-codex字段。

Corki目前能承载opaque和namespace，但notes配置仍拒绝、没有九工具/backend/hint/ingest。
拟添加实际异步HTTP客户端与Runtime持有的service；provider.codex_backend为显式已有
backend bearer认证声明，不读取Codex登录文件或假装实现OAuth。eligible仅OpenAI身份、
Responses、backend声明与已配置凭据、显式TokenBudget/notes均成立时启用；非资格不发
notes请求、不发布工具，不声称普通API key具备Codex backend能力。模型/notes使用同一
配置endpoint与bearer凭据；可注入借用HTTP client离线验证。工具原子发布，客户端延迟
创建并在Runtime关闭时回收，借用client不代关。

当前单root线程将session_id与thread_id同取Corki thread id，窗口UUID复用已持久恢复的
TokenBudget身份。这些是单Agent兼容映射，不声称覆盖多Agent/安装ID/源ordinal上传全部
语义。配置/协议变化接真实模型请求，禁止由模型工具参数冒充session身份。验收包含9
动作真实HTTP+ModelOnly、输出/错误/取消、保存→reset→读取、窗口metadata/hint生命周期、
无自动重放、冷恢复和资格门。非原生provider本地有效恢复方案仍保留完整待实现范围，
本批不会把不可读notice、JSON接口或mock服务端状态当成该方案完成。

实施中新增RED：notes.read_file的start_line="last"竟然进入HTTP并返回成功；Codex
tools.rs参数是anyOf(integer,null)，Corki executor._validate只识别顶层type，忽略
anyOf且未识别null。这是B07本地schema边界真实缺陷，拟在共用校验器补anyOf/null，
不单独给notes写旁路校验，也不声称已经实现完整JSON Schema。TOML测试另有fixture
关键字config_path写错，实际入口config_file；此为测试修正，不算产品修复。

## 第六十一批实现与验证：native history-notes实际Runtime链路（B/C/E）

本次重新完整读取目标及development.md、Codex AGENTS，核对固定Codex commit干净、
Corki HEAD 041bf05eb93d353e3a67a402086ebf8cf04b39f0与已有dirty工作区。上一条单纯拟定
goal没有工程进展；根据现有文件续接未完成客户端草稿，未重复创建goal或改teach。

源码依据重新读取ext/history-notes/src/backend.rs、extension.rs、tools.rs全部动作与
output路径；core/src/responses_metadata.rs::client_metadata/compatibility_headers/
turn_metadata_payload。utils/output-truncation重导出protocol::TruncationPolicy，
实际serde形状为{"mode":"bytes","limit":N}，纠正旧草稿误写的{"bytes":N}。

新增history_notes/{backend,schemas,service}.py，由Runtime在CodeMode创建前原子注册
九工具并持有service。backend延迟创建owned AsyncClient，borrowed client不代关；Runtime
关闭顺序加入service。普通执行、账本、schema、并发、媒体准备及结果持久化全部走原链路，
没有另建执行循环。notes开关不再统一拒绝；资格仍要求显式TokenBudget/notes、OpenAI身份、
Responses、codex_backend声明和非空credential。未满足任何一个条件均不注册、不请求hint、
不上传history metadata。已有用户的普通默认配置不激活本扩展。

ModelRequest新增可选client_metadata，默认None；Graph从已提交窗口档案取得身份，
Responses实际payload和两个兼容header发出同一身份。默认factory为eligible路径启用
encrypted tool output，不要求再重复打开其能力开关；namespace native/compatible独立。
WindowManager在旧窗口prepare及新reset snapshot接入hint；按当前窗口缓存成功或失败，
4000 UTF-8 bytes以内且非空才注入Developer notes.thread_hint；新窗口失败不会带旧hint。
metadata窗口身份来自持久marker，冷Runtime不会重置window_number或伪造旧turn。

backend每动作单POST，可信context覆写模型输入；四种加密参数动作发送对应header；
35秒HTTP设置之外，asyncio.timeout另限制完整调用总时长，慢滴响应不能无限续期。
错误不含后端body/auth，普通失败成为Observation，取消传播且关闭响应流；结果最多
32,000,000 bytes，encrypted_output完整保留，images从文本分离后经统一媒体准备。
再次RED发现Python json.loads(bytes)接受NaN/1e999及UTF-16，源码serde_json::from_slice
拒绝这些值。3项先失败，再改UTF-8解码和有限JSON数解析；不能把非法响应记录为成功。

RED首2例因TokenBudgetConfig拒绝notes=True失败；接入后通过。后续22例包括真实
Responses九动作/native与compatible、重置/冷启动、错误/资格/提示/取消全部通过。
测试编写中的type缺省、ToolResultItem字段及CancelledError消费约定错误已按实际协议
修正，不记为产品差异。扩展schema案例暴露anyOf/null共用校验缺口并修复，新增7例
handler准入测试；该修复仅覆盖此schema子集，不冒充完整JSON Schema实现。
写结果落账后历史提交前/后及终态提交故障，通过cold.resume_pending验证后台写只执行
一次、result仅保存一份，无重复采样旧step。真实HTTP测试使用实际默认model factory，
捕获backend及Responses请求，不以自定义ModelRequest检查冒充wire证据。

首次全量1726 passed/101.11s；examples独立3 passed/2.94s。再补超限响应、有效图片分离、
4000字节hint边界、非法JSON及完整deadline，最终新增34个integration、1个配置、7个
共用校验器测试；针对性（含既有相关测试）75 passed/3.49s。最终完整回归与wheel正在验证。

README/default config/THIRD_PARTY_NOTICES已更新。明确保留差异：现有credential显式声明
不等于OAuth登录/刷新或运行时失效；session=thread只为单root映射；installation_id、
source ordinal、native多Agent契约未齐。服务端存储/索引/即时读取/最终一致性/解密均
未连接验证，mock只证明客户端协议、调度与持久化。图片detail缺省仍用Corki high默认。
非原生provider需要有效本地history/notes恢复，不能用不可读notice顶替；本批不是该路径
完成，也未关闭C05、长期记忆整体或其它A/B/C/D/E开放项。goal维持active。

收尾继续追踪session/mod.rs::current_window（4180）：wire window_id实际是
"{thread_id}:{window_number}"，context_window_id才是UUID。旧草稿混为同一个UUID
不能作为可接受兼容差异保留；新增真实HTTP断言先2失败，随后将metadata/header改为
thread:number，提示里的context UUID仍保持已有持久身份。重新运行最终验证和构建。

最终wire身份修正后：针对性75 passed/3.59s，全量 **1733 passed/102.03s**，
examples独立 **3 passed/3.12s**；Ruff check/337文件format、compileall、pip check、
Node bootstrap和git diff --check通过。Codex固定commit仍干净，teach散列保持原值，
docs/flow.md用户暂存不变，未提交。

最终wheel `/tmp/corki-history-notes-final.pNnZyj/corki-0.1.0-py3-none-any.whl`，
689732 bytes，SHA-256 `c44290cbb85da293662ebfc334e6c236f40e26c5fc917ad13b751956ef168829`。
不再把前一次UUID混用版本的wheel验证作为最终产物证据。

从/tmp以python -I加载最终installed目录，上批824选择+本批34 integration/1配置+
tool executor完整14例，**873 passed/43.20s**；157个Corki模块全部确认来自该安装目录。
未运行真实模型、Codex backend或Rust测试；预设模型调用只证明Harness链路，不证明
真实模型选择质量。此批为progress，完整goal继续active。

下一优先项仍为C05的非原生有效历史/notes恢复：现有memory/backend.py操作跨Thread
长期记忆文件层级，read只支持正向line_offset，不提供窗口/历史item身份、负向notes行号
或rollout隔离，因此不能直接把它更名为history/notes来宣布等价。应继续追踪原生工具
契约，基于实际append-only archive及隔离持久notes实现可用兼容链路，并独立说明其
本地存储/明文结果与原生服务端索引/opaque输出之间的差异。其它A/B/C/D/E范围不缩减。

## 第六十二批实施前决策：非原生history/notes有效恢复（B/C/E）

前批progress。重读目标；Codex仍固定ddf04ad，继续以ext/history-notes/tools.rs九动作
schema/namespace语义、extension.rs资格及hint、session/mod.rs current_window为基准。
上游只有服务端客户端，仓库没有服务端索引/笔记存储实现，不能虚构服务端响应细节。
Corki现有native-only资格导致第三方provider notes配置启用后仍无工具，reset后没有可读
历史恢复；已有LocalMemoryBackend是跨Thread长期记忆，不是rollout工作笔记。

拟在显式TokenBudget+use_history_notes_extension时选择后端：满足既有native资格继续
native，否则用本地archive+SQLite notes；这是用户目标要求的兼容路径，不是宣称Codex
提供同一本地实现。不新增静默HTTP失败fallback，已准入native写的失败保持原结果未知。
共用九工具曝光/并发/ledger/上下文hint；local移除encrypted参数标记，metadata=None，
不触发网络history ingest。历史读取同Thread原始append-only记录并按compaction划分
thread:number窗口，返回稳定item身份、角色、工具过滤与可读文本；reasoning/opaque
不能伪装解密。Notes按Thread和虚拟/root/notes路径隔离，原子写/追加，1,000,000 UTF-8
字节上限、负向inclusive行范围；读写取消必须join真正的SQLite操作，避免释放执行门
后还有隐形副作用。输出保持有界且能继续按item/line范围读取，不依赖无效JSON截断。

验收先加入真实Runtime保存→reset→history search/read→notes read，以及Chat/Responses
wire、冷启动、不同Thread隔离、错误/取消/并发/账本故障。保留服务端响应形状、完整
跨Agent/native auth/ingest及其余A/B/C/D/E开放项，不用本批替代完整目标。

## 第六十二批实现与验证：本地可读历史与rollout笔记（B/C/E）

初始Runtime RED：TokenBudget/notes显式开启但仅有new_context/get_context_remaining，
无history搜索。新增local.py、archive.py、notes_store.py、output.py；Runtime在composition
选择native或local，共用HistoryNotesService/九个ModelOnly工具及标准executor/ledger。
RecoveryBackend port由两种真实实现使用；不是预留接口。native资格/加密输出cap保持
原规则；local不创建HTTP client、不带client_metadata，不将原生失败切换成本地副作用。
既有4个“native不合资格则完全无工具”的断言按新兼容合同改为“无native请求/ingest，
显式notes请求仍提供local”；两个feature关闭情形仍无工具。native失败还断言本地DB
没有创建，避免以后引入静默跨backend重试。

原始archive按append顺序和CompactionItem划分thread:number窗口，保留item ID、角色、
工具namespace/name、call_id与错误状态；匹配是case-sensitive literal substring。
复核RED暴露call_id与inline图片丢失，补协议字段与read_item独立images返回；read文本
保持媒体marker，base64不重复塞文本。Reasoning/静默snapshot不进入可读索引；本地不能
解密既有opaque，保留明确不可读标识而不是伪造原文。模型请求已丢弃旧窗口，但原archive
仍能list/search/read；当前用户角色包括真实user context，不能把role=user等同“真实提问”。

NotesStore只接虚拟路径，SQLite PK=(thread_id,path)，动态排序列枚举白名单、prefix
按substr比较（%/_不是通配符）；文件路径不会映射到宿主文件。相对/root/notes、literal~、
inclusive正负行号、创建/替换/追加、大小写敏感line search、created/updated/name排序
均接同一事务层。1,000,000 UTF-8 bytes上限在提交前校验；追加BEGIN IMMEDIATE避免
并发丢写。短连接由with/closing持有；_joined等待已开始的后台操作，重复取消不逃逸
写线程。文件新建0600，与用户会话库同目录独立命名，不改变已有business/checkpoint表。
I/O/SQLite错误有界且不泄漏host DB路径；hint失败省略、普通工具错误Observation、
取消保持控制流。跨Thread即使传入伪造context也不能选中其他Thread的笔记/历史。

output.py按完整UTF-8 JSON成本保留metadata与身份，read_item返回next_offset_chars；
列表在预算内保留完整record或可截短preview，不能把JSON截断后当有效结构使用。
新增16000窗口/12000阈值+80021字符工具输出实际自动reset场景：无需手动new_context，
下一Step无旧任务/巨量输出，仍可检索并精确读取尾部目标。最初RED发现preview固定取
开头导致命中不在预览，改为match_offset_chars/preview_offset_chars与命中附近片段。

已通过中间58、66、67项相关测试；首次完整回归1765 passed/103.62s。最后补实际Runtime
九动作direct/CodeMode-only组合，测试最初误选最旧user-role的environment片段，按源码
角色语义将fixture改recent_first，不改变历史投影来迎合断言。最终新增13 integration+
19 notes-store与3 archive tests，共35；包含既有native回归的针对性69 passed/7.08s。
当前最终全量、examples和隔离wheel验证进行中；以末次结果为准。

README/default config/THIRD_PARTY_NOTICES已更新。本地结果shape是明确兼容合同，不能
冒充未在源码中公开的server schema；立即可见不同于原生list/search eventual语义。
单root、4096 UTF-8字节路径、原生↔本地不迁移笔记、private reasoning/opaque不能解密、
音频与完整服务端历史规范化仍是差异；notes不是加密存储或OS权限边界，长单行超输出
预算仍须调高配置/拆分笔记。未运行真实provider/后端/Rust。完整A/B/C/D/E保持active。

最终回归意外捕获已有A/E恢复竞态：test_connection_backoff_survives_process_exit_before_checkpoint
出现delays=[20,40]而非[40]，同轮另2失败是已修正的九工具fixture。单独重跑通过不能
抹去该证据。检查_retry_model发现它无条件先发旧retry事件/等待，而_call_model
才读取已提交model_failure；旧checkpoint落后于journal时会重复过期等待。拟先添加
确定性“进入retry节点前下一attempt事实已经持久化”注入，再使retry节点核对该attempt
的failure/completed/partial事实，存在则跳过过期等待，仍沿既有model恢复节点消费事实。
这是Corki双存储恢复边界修复，不声称Codex有同一个LangGraph实现；不重采样已提交attempt。

补读Codex core/src/responses_retry.rs完整源码：Sampling连接重试独立5→60s、非internal/
非Bedrock资格、有界stream预算及WS fallback责任分离。Corki确定性RED分别复现已持久
失败仍多等5秒（[5,10]）与已完成仍多等5秒（[5]）；修复后再补partial事实场景，均不
重复过期等待/事件，继续原model节点恢复，而非跳过未记录attempt的正常退避。连接
模块11项通过；最终三种事实+真实process退出及native/local组合 **81 passed/8.47s**。
首次最终全量 **1771 passed/107.46s**。本批共新增38例，不将旧偶发失败删掉或放宽断言。

安装隔离复核发现connection crash fixture子进程原先会走venv editable来源，因此测试
显式将当前corki包父目录设为子进程PYTHONPATH，并在子进程校验真实包路径。新增检查
初稿受函数内import corki.core.graph的局部绑定影响出现UnboundLocalError，改唯一包别名，
此为测试夹具错误而非产品行为修复；修正后连接模块 **12 passed/1.86s**。

最终wheel `/tmp/corki-local-recovery-final.P0eNol/corki-0.1.0-py3-none-any.whl`，698275 bytes，
SHA-256 `78cae5b857c9d46a3b2ed73e59b21490d7ce38dbb56053b83a70ceba9df59cd5`。
从/tmp以python -I加载该installed目录，上批873选择+本批local35+connection完整12，
**920 passed/53.42s**，161个父进程Corki模块确认均来自installed，新增connection退出
子进程也校验同一包来源。未以第一份缺少retry修复的wheel代替最终产物验证。

仍需继续：native完整历史规范化/identity/auth生命周期；local archive畸形JSON调用的
raw_arguments/parse_error投影（当前JSON分支优先parsed arguments，不能据此声称原始
错误输入无损恢复）、音频/其它媒体、不同后端切换及完整日志/记忆污染边界；其余
A/B/C/D/E开放项不缩减。该记录不是目标完成声明。

最终夹具隔离修正后的完整回归 **1771 passed/117.59s**，examples独立
**3 passed/2.99s**；Ruff check/344文件format、compileall、pip check、Node bootstrap、
git diff --check通过。Codex工作区仍干净，teach原SHA-256不变，docs/flow.md用户暂存
保留，未提交。本轮为progress，所有测试进程已结束，完整goal保持active。

## 第六十三批：历史召回的原始工具输入（修改前审计）

基准仍为Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc。B/C/E交叉项：
protocol/src/models.rs ResponseItem::FunctionCall（1042起）明确将arguments保留为
原始String；core/src/tools/router.rs build_tool_call（246起）将同一个String交给
ToolPayload::Function，解析失败不应反向改写已产生的调用事实。
ext/history-notes/src/tools.rs HistoryNotesTool::handle_call（261起）将参数解析错误
转为RespondToModel，history read/search使用后端alpha/history/v2契约；此仓库不含
该服务端的完整normalized history实现，不能声称其返回parse_error等具体字段。

Corki models/responses.py _finish_function_call与openai_compatible.py _finish_tool_call
保存raw_arguments/parse_error，tools/executor.py validate在handler执行前生成非法JSON
Observation；持久化ToolCall仍含原始信息。但history_notes/archive.py project_history
仅freeform读raw，其余json.dumps(arguments)：非法JSON/非object会变成字符串null，
有效JSON也会丢空白、重复键等原始字面值。属于实际行为不一致，优先级P1；重置后
模型不能通过原始片段检索定位失败调用，无法可靠区分调用输入与错误结果。

实施前验收：先写RED覆盖非法JSON、非object、原始有效JSON、freeform和无raw旧调用；
投影以存在的原始输入为准，旧调用才序列化parsed arguments，保留call_id/input_kind
及已有parse_error诊断。read/list/search同源、Unicode分页不丢字。真实Runtime须验证
失败Observation→new_context→search→read→冷启动再读，错误调用不触发handler且不
被重放。字段是本地兼容投影，不冒充Codex未公开的服务端格式。媒体完整规范化、
原生鉴权/ingest、其它A/B/C/D/E开放项仍待后续，不缩减整体完成标准。

### 实现和验证记录

修改前新增6个投影参数场景全部RED：非法JSON/非object/空失败输入被投影成null，
重复key/空白原文被重写，已有freeform/旧parsed回退缺少input_kind元数据。另两个真实
HTTP适配器测试（Chat、Responses）在reset后search返回空数组而失败；不是只有人为
构造ToolCall的证明。随后修改project_history：有raw时保留raw，freeform/解析失败/
无parsed时也保留原始空输入；只有无raw且正常parsed的旧调用走JSON回退。附带已有
parse_error及input_kind，不修改工具执行参数、持久化格式或副作用判断。

HTTP测试从provider流输出畸形参数开始，经解析、持久化、executor错误Observation、
new_context、本地search/read，再关闭Runtime并新建同Thread Runtime重复reset/search/read。
检查实际线上格式请求：第一次错误反馈前仍回放原始arguments，reset后旧片段确实离开
模型输入，检索只命中原调用且两次item_id相同，read原文与parse_error到达模型；业务
archive只有一个原调用及一个error result，guarded handler计数始终0，2个HTTPclient均关闭。
这里的MockTransport验证协议/Harness链路，不证明真实模型选择或实际服务端能力。

额外参数化512-byte预算Unicode连续读取，确保增加调用元数据后仍可按字符offset完整
重组原始工具输入。native/local/archive/notes-store/连接恢复组合 **90 passed/8.43s**。
README已明确原始参数及旧记录回退。全量与隔离wheel验证进行中，最终结果后补。

最终完整回归 **1780 passed/107.71s**，examples独立 **3 passed/3.00s**；本批新增9例。
Ruff check、344文件format、compileall、pip check、Node bootstrap语法及git diff --check
通过。wheel `/tmp/corki-history-input-final.cI0nDj/corki-0.1.0-py3-none-any.whl`，
698525 bytes，SHA-256 `dbdc1e727d1f2b5d51d3c3facd810f4cd05ee631a18ec0b6d1649376f2597c2d`。
从/tmp通过python -I优先加载installed运行上批同一选择加本批9例，
**929 passed/57.53s**，161个父进程Corki模块全部验证来自该安装目录；连接恢复子进程
沿既有夹具校验相同包来源。没有依赖旧wheel冒充本轮验证。

Codex固定commit且工作区干净，teach.md SHA-256仍为
`816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a`，
docs/flow.md用户暂存保留，未提交。本轮只修改archive投影、对应测试、README和本审计；
没有新增存储字段或改变执行权限。本轮progress，所有验证进程已正常结束。
整体A/B/C/D/E仍未完成，C05的完整原生服务端/鉴权及媒体规范化、D记忆控制/污染、
其它矩阵开放项继续有效；未运行真实provider、服务端或Rust测试，goal保持active。

## 第六十四批：显式记忆请求的原文、目录边界与合并信任（修改前审计）

本轮D03/D04/E沿Codex ext/memories/src/tools/ad_hoc_note.rs（完整94行）→
local/ad_hoc_note.rs（完整147行）→memories/write/src/extensions/ad_hoc.rs（完整30行）
及templates/extensions/ad_hoc/instructions.md追踪。工具描述限制用户显式请求remember/
forget/update；schema声明文件名格式与长度；local create_new保证不覆盖，逐级校验目录
非symlink且必须directory，note以原始UTF-8字节write_all，不增删换行。tests.rs
add_ad_hoc_note_tool_creates_note_file精确断言无末尾换行原文。ad_hoc指令要求考虑新增/
编辑笔记、保留笔记源文件、合并派生信息标记[ad-hoc note]；内容是记忆数据，不是执行
行动的指令。完整control.rs另提供清空memories/memories_extensions根内容的管理操作，
不能把“忘记某事实”的ad-hoc笔记等同全盘擦除或从全部历史/备份移除。

Corki memory_add_note→LocalMemoryBackend.add_note→extensions/ad_hoc/notes已有append-only
文件与后续workspace digest合并，但存在：
- P1：note.rstrip()+换行改写原文；Python正则\\d接受非ASCII日期数字，而源backend只认ASCII。
- P0：仅backend构造时ensure_memory_layout；运行后root/managed ancestor换成symlink时，
  add_note直接沿路径写入，可写到外部目标。需每次写入前重新检查，不宣称抵抗并发目录替换。
- P1：model-visible filename字段没有源schema的格式/长度说明；模型直到调用失败才知道契约。
- P1：合并prompt只笼统提历史文本与更新遗忘，没有ad-hoc优先处理/仅数据/派生标记的明确
  合同；_consolidate确实把ad_hoc_notes作为JSON数据给模型，没有工具执行权限。

验收前先RED：原文字节/无换行/多换行/CRLF、Unicode日期、写前目录替换/移除/已有同名
文件不覆盖、模型可见schema。修复复用现有目录校验而不是扩大文件写入范围；保留当前
返回path的兼容shape，明确源backend接受leading-hyphen slug但源schema首字符要求alnum
的细微不一致，本轮遵循既有模型可见schema限制，不改变该边界。真实Runtime需验证
工具写入→冷启动后台合并→主模型摘要/按需读取更新内容，原请求笔记仍完整保留；
只证明数据与策略进入合并模型，不以脚本模型证明真实遗忘/抗注入质量。全局memory reset、
独立close及其它D/A/B/C/E开放项不在本批冒充完成。

### 实现与阶段验证

11个新增backend/schema场景先全部RED，实证无换行/多换行/CRLF/尾部空白被重写，
Unicode日期被接受，4级已替换目录全部可写到外部，以及目录缺失不重建、schema缺合同。
add_note现先UTF-8编码，使用共享ASCII文件名pattern，再ensure_memory_layout复核现存
根/祖先与重建缺失目录；O_CREAT|O_EXCL以0600创建，二进制原文写入。不覆盖同名文件，
不改旧笔记。schema增补格式/长度及verbatim说明；backend仍执行验证，未冒充公共
executor支持完整JSON Schema。目录并发替换/读路径与全部权限问题不据此宣布解决。

合并Runtime RED先在后台因prompt缺[ad-hoc note]与明确信任边界而失败；补对应规则后
到达主模型更新断言，发现测试误取最早memory.instructions而非append-only增量中的
最新同key记录。修改fixture为倒序查找，没有修改产品context来迎合测试。中间组合
151 passed/1 failed是该已定位fixture；最终针对新增Runtime **5 passed/1.10s**。

真实Runtime写入→关闭→同Thread冷启动后台合并→下一Turn最新summary→memory_read→
模型实际收到新版MEMORY，验证原笔记字节仍相同、合并模型无工具可执行、请求含更新/
遗忘数据与信任策略。预设合并输出从成品中移除旧偏好并携带派生标记，只证明链路，
不是实际模型自动遗忘/抵抗注入的证据。另4种运行期目录替换通过真实tool executor
返回error Observation、Turn正常完成、业务历史只有一个错误结果、外部目录无写入。

README和Apache-2.0适配声明已补，尚待末次完整回归和隔离wheel结果。本轮新增16例。

最终记忆相关组合 **152 passed/3.24s**，完整回归 **1796 passed/108.08s**，examples独立
**3 passed/2.73s**。Ruff check、344文件format、compileall、pip check、Node bootstrap
语法检查、git diff --check通过。Codex仍固定commit且干净，teach原SHA不变，docs/flow.md
用户暂存保留，未提交，也未删除用户笔记/数据库。

wheel `/tmp/corki-ad-hoc-memory-final.dKSCfW/corki-0.1.0-py3-none-any.whl`，699684 bytes，
SHA-256 `1df71d312e1d283105bfdc9fcbe74ddde0c151844147c2d03b59b32730240923`。
从/tmp以python -I加载installed，沿上批相同测试选择加本批16例，
**945 passed/47.43s**，161个父进程Corki模块校验均来自该安装目录；PromptStore优先
读取包内_prompt_templates，新增冷合并测试同时验证新策略随wheel发布。连接恢复
子进程仍沿既有隔离夹具检查包来源。所有验证进程已正常结束。

本轮progress，但D03/D04仅关闭本批有证据的具体差异。后续须继续核对memory read/
summary路径在运行期目录变化下的检查、external-context禁用生成的实际入口、全局
reset与后台任务收尾及原生Phase2 agent等；不能将写前目录检查扩大声称为整个记忆
读取/污染边界已安全。Prompt约束无主机侧语义授权判定，原始笔记和archive保留；未运行
真实模型/服务端/Rust验证。完整A/B/C/D/E目标保持active。

## 第六十五批：记忆读/搜索/摘要的实际文本合同（修改前审计）

完整读取Codex ext/memories/src/local.rs、local/{path,read,list,search}.rs及prompts.rs，
沿tools/read.rs、tools/search.rs→backend→local和utils/output-truncation/src/lib.rs→
utils/string/src/truncate.rs确认。源默认root list/search会lstat并拒绝symlink，目录遍历
跳过隐藏/链接/非普通文件；相对路径逐级lstat拒绝含dangling symlink的组件。注意源
resolve_scoped_path在相对非空path时未单独校验root，prompts直接read_to_string摘要，
因此不能把所有受信任root/summary替换问题误报为Corki独有差异，也不宣称Codex是沙箱。

Corki新增确认差异（D03/C03/E，P1，root默认search为P0）：
1. read_text默认通用换行转换CRLF，splitlines还将裸CR/Unicode分隔符当换行，且拒绝
   末尾LF后可读的空行；源read仅按LF计算字节边界并原样返回选中内容。
2. 源read和summary均使用4-byte/token近似的UTF-8首尾截断、marker额外预算；Corki两处
   独立使用估算token的头部截断，会丢失尾部索引/证据。stage-one已实现相同源策略，
   应共用而不改变常规工具输出的另一种预算语义。
3. 源normalized先按需to_lowercase，再只留Unicode alphanumeric；Corki只将部分分隔符
   改空格、casefold，并未检查归一化后为空。标点查询可能误匹配全部，常规路径式查询漏召回。
   Python Unicode表/Alphabetic与Rust版本的完整差分尚无证据，不可声明所有Unicode位级一致。
4. search读取行时Python splitlines与Rust str.lines的LF/CRLF语义不同；会改变行号/窗口。
5. 默认search没有对start symlink拒绝，root运行期替换后可递归读取外部；_resolve的
   exists前置也将dangling link当不存在，错误分类不一致。list已有start检查需保留。

先RED验证CRLF/裸CR/Unicode分隔符/末尾空行/空文件、首尾截断及marker、标点归一化/
大小写负例/空查询降级、默认root和dangling路径。真实Runtime按summary尾部索引→
normalized搜索→读取含原始换行片段→小预算尾部读取，另用错误查询和目录替换验证
Observation回灌。summary受信任读取保持源码路径，不因安全假设扩大为未经审计的全盘隔离。
原生工具命名/schema/cursor与Corki兼容接口、完整Unicode表差分及其它A/B/C/D/E开放项
继续保留，不以此批局部文本修复关闭整个记忆模块。

### 实现与阶段验证

新增22个文本/路径场景先 **16 failed/6 passed**，失败明确复现换行改写、末尾空行
拒绝、截断尾部丢失、normalized漏召回/全匹配、casefold多匹配、错误行号、默认root
链接绕过和dangling错误分类。修复后这些场景加原提取预算 **47 passed/0.26s**。
read改为UTF-8原始字节解码，只用LF计算范围；search独立按Rust str.lines移除LF/CRLF
终止符但保留裸CR、Unicode分隔符。共享truncate_memory_text沿原stage-one实现保留
首尾，read/summary复用；不改普通工具结果的预算函数。search先lower，再保留isalnum，
拒绝normalized空值，prepared queries移出文件循环；默认起点检查symlink，_resolve
无exists前置地识别dangling链接。完整Unicode仍开放：Python isalnum与Rust Alphabetic/
Number属性及其Unicode版本不能仅凭这些向量视为相同，本批不声明该部分位级等价。

真实Runtime 6次模型请求依次证明小summary窗口仍含尾部MEMORY.md索引、normalized
路径式查询命中正确行号、read返回原CRLF、极小read预算保留尾部、标点空查询成为error
Observation、运行期root替换后的search成为error且外部秘密未进入模型请求。最后Turn
完成，原MEMORY文件字节保留，持久化结果顺序为3个成功+2个错误。ScriptedModel证明
Harness链路而非真实模型的选择质量。记忆组合 **175 passed/3.36s**；本批共新增23例。
README/适配声明已更新。首次Ruff报共享helper注释超长102字符，已只换行修正；最终
全量/静态/隔离wheel结果待追加。

最终完整回归 **1819 passed/113.40s**；examples独立 **3 passed/3.16s**。Ruff check、
346文件format、compileall、pip check、Node bootstrap语法及git diff --check均通过。
wheel `/tmp/corki-memory-read-final.Idgrwy/corki-0.1.0-py3-none-any.whl`，699951 bytes，
SHA-256 `ea47de0348a4ba6cf4d9cd0c68186f874641c42644d9fe417180cad831356910`。
从/tmp以python -I加载installed，上批相同测试选择加本批unit22与Runtime1，
**968 passed/50.20s**，161个父进程Corki模块核对来自该安装目录。连接恢复fixture
子进程保留同包来源校验。全量与隔离进程都确认exit_code=0，非仅依据pytest末行。

Codex仍在固定commit且工作区干净，teach.md原SHA-256不变，docs/flow.md用户暂存保留，
未提交。本轮progress；仍需继续取得完整Unicode Alphabetic/Number及lowercase版本差分
证据（当前Python isalnum不能等价宣称Rust Alphabetic），核齐剩余read/search协议形状/
usage、external-context生成禁用、reset/后台收尾和其它A/B/C/D/E开放项。未执行真实模型、
服务端或Rust测试，未建立一般文件系统安全边界。整体goal保持active。

## 第六十六批：污染标记与已发布记忆清理（修改前审计）

本轮D01/D02/D04/E追踪core/src/stream_events_utils.rs（133–164）、对应tests
external_context_pollution_items_include_web_search_and_tool_search/exclude_local_tool_calls，
tools/registry.rs handle_any_tool、mcp_tool_call.rs prepared call准入→maybe_mark（877起），
以及state/src/runtime/memories.rs mark_thread_memory_mode_polluted（623–658）和两个
selected/already_polluted测试。源条件不只是工具名：ToolSearchCall/Output、WebSearchCall、
无call_id的外部通知、contains_external_context结果、PreparedMcpCall捕获的server metadata
等均有分支；MCP标记发生在准入之后且受server_pollutes_memory控制。

Corki graph._execute_one目前在claim前仅以mcp__前缀判断，缺工具搜索/外部结果事实、
按服务器实际资格与metadata决定的路径，且repo错误会影响主Turn；这些触发链差异继续
开放，不能因已有MCP测试就称污染控制完整。本批先修D02明确存储断链：
SQLiteMemoryRepository.mark_thread_mode只UPDATE threads，未在selected_for_phase2=1
时enqueue合并，而Codex即便已polluted仍重复enqueue。影响：旧合并输出仍含已失效来源，
任务watermark/待清理状态缺失；不是只阻止下一次stage1提取就足够。

验收先RED：选中过的enabled或已polluted线程标记后，watermark推进且任务pending；
未选中过/未知线程不制造合并任务；已有running owner/lease和成功cooldown不被破坏；
mode与enqueue异常必须在同一SQLite事务回滚，取消须等待已经开始的写入结束。
随后真实Runtime产生MCP调用标记→冷启动合并不再选污染源→原始工作输入移除→下一Turn
读取新摘要，保留原始档案/非安全擦除的既有边界。不改变本批未审计的工具触发分类，
更不以配置开关代替完整外部内容污染控制。

### 实现与阶段验证

unit10先 **8 failed/2 passed**，失败覆盖6种selected模式/job状态、enqueue错误未触发及
取消提前完成；未选过/未知Thread两例原已通过。真实Runtime先RED：实际MCP调用后的
global job仍succeeded而非pending，明确复现主循环到清理任务的断链。

mark_thread_mode改用已存在_joined_write；_mark_thread_mode以BEGIN IMMEDIATE将mode
更新与selected检查/enqueue放在同一事务。不只检查“mode是否变化”，已polluted仍会
在旧baseline选中时推进watermark；未选中不制造任务。复用现有enqueue保留running
owner/lease、completed_watermark及成功cooldown。此为Corki共享SQLite下的事务保证，
不声称Codex两存储之间有同样跨库原子性。未删除stage1原记录或原始会话档案。

Runtime测试从已完成合并且包含旧来源的baseline开始，实际MCP标记触发pending新水位；
下一eligible冷启动不提取污染源，清理raw/rollout工作输入，合并请求包含旧MEMORY但
新raw不含污染源；脚本合并输出到达下一Turn最新summary，selected_for_phase2清零。
这证明调度/数据/发布链路，不证明真实模型遗忘质量或所有外部内容触发已经覆盖。
针对组合 **15 passed/0.83s**；本批新增11例。首次Ruff报告5处测试SQL长行，已换行修正。

最终记忆组合 **188 passed/4.54s**，完整回归 **1830 passed/110.95s**，examples独立
**3 passed/3.36s**。Ruff check、347文件format、compileall、pip check、Node bootstrap
语法与git diff --check通过。wheel
`/tmp/corki-memory-pollution-final.pRJbTi/corki-0.1.0-py3-none-any.whl`，700313 bytes，
SHA-256 `601c213610c7a74e7494ebd1ea667d6d7581b2668293f2c6f298c7892db03ea5`。
从/tmp以python -I优先加载installed，上批同一选择加本批11例，
**979 passed/48.16s**；161个父进程Corki模块均校验来自安装目录。既有连接恢复子进程
包来源校验保留。所有测试/构建进程已确认结束且exit_code=0。

Codex HEAD仍为ddf04ad26789d040f9ef6a96736f76602e35a6cc且干净；teach原SHA不变，
docs/flow.md用户暂存保留，未提交。本轮progress，改动没有执行任何用户真实记忆清理。
下一优先项应沿真实typed工具/模型结果和MCP准入事实接污染触发与失败隔离，避免继续
用工具名称前缀代替能力/来源合同；这一点已记入D04，未被本批数据库修复覆盖。
原生Phase2 agent/diff、Unicode属性差分及其它A/B/C/D/E开放项也继续有效；未运行真实
模型、服务端或Rust验证，整体goal保持active。

## 第六十七批：typed外部工具结果与污染标记失败隔离（修改前审计）

完整追踪tools/src/tool_output.rs ToolOutput默认contains_external_context=false、
JsonToolOutput::with_external_context及Box转发；ext/web-search/src/output.rs明确返回true。
core/tools/registry.rs handle_any_tool在得到输出后依据该事实和配置标记，不要求工具名
含mcp。rollout/src/state_db.rs mark_thread_memory_mode_polluted是主循环best-effort边界，
失败日志而非工具/Turn失败。原生ToolSearchCall/Output另由stream_events_utils标记。

Corki ToolResult/ToolResultItem、ledger和executor规范化目前没有外部上下文字段，普通
扩展/Web结果无法表达；graph只在mcp__前缀调用前标记且repo异常传播到主任务。属于
D04/B07/E01缺失，不能继续靠名称扩充白名单。先新增布尔typed事实，跨规范化/媒体/
持久化/历史投影保留，旧记录默认false且不回填不可变JSON；ToolSearchTool的兼容结果
显式携带该事实，即使空命中也有效。graph消费已完成或cached结果时标记，嵌套CodeMode
同样沿真实_execute_one处理，不依赖外层exec的文本；既有MCP前置路径先复用失败隔离
helper，MCP精确准入/服务器资格另继续审计，不能因此声称前缀误判已解决。

先RED：非MCP flagged成功/错误结果、flag关闭/配置关闭负例、空搜索、ledger/history旧新
往返、错误flag校验；真实Runtime验证一般工具及CodeMode嵌套结果，mark失败仍继续、
取消仍传播，并检查恢复不重放副作用。原生call-level搜索/hosted通知在模型完成前后的
标记及MCP授权前误标仍开放，不使用结果flag冒充完整原生模型事件合同。

### 实现与阶段验证

初始RED：13个Runtime参数场景失败（12个缺typed flag导致工具失败，1个标记数据库异常
直接使TurnFailed），5个storage/validator场景因协议缺字段失败。增加
ToolResult/ToolResultItem.contains_external_context=false、executor布尔校验/复制、ledger
与item codec默认省略false；新true字段持久化，旧记录往返不回填破坏immutable payload。
媒体路径通过dataclasses.replace保留该字段。ToolSearchTool正常返回（含空命中）标true，
不通过名称解析决定一般结果来源。graph在complete_tool_call之后消费结果，包括cached，
嵌套_execute_one在返回JS之前完成处理；标记错误仅输出异常类型的warning，Cancel不捕获。
既有mcp前置检查复用同helper但尚未改精确准入，该差异仍保持开放。

普通/CodeMode×success/error×flag/config组合、schema错误、ledger/history冷往返先
**18 passed/1.54s**。追加空搜索、已提交模型step+工具ledger的resume_pending（不调用
handler、不重采样原步骤，模型收到cached fact且线程polluted），以及MCP前置/一般结果
后置标记取消。最初取消fixture只断言TurnCancelled而未接住Runtime既有“先yield终态再
raise CancelledError”合同，导致2失败；改测试验证两者，不改变Runtime取消行为。
本批共新增23例，最终针对结果随后记录。普通后置标记发生在ledger完成之后，取消不
回滚已完成副作用；best-effort失败期间不承诺记忆一定被禁用。

针对新增测试最终 **23 passed/1.88s**，收尾复验 **23 passed/1.90s**；examples独立
**3 passed/3.81s**。Ruff check、349文件format、compileall、pip check、Node bootstrap
语法与git diff --check通过。wheel
`/tmp/corki-external-result-final.IAh6Yv/corki-0.1.0-py3-none-any.whl`，700783 bytes，
SHA-256 `1ffbfc6d507b5db3c530fdd9b14f45b3f086f2c1391836d8144ecf04b2db582e`。
从/tmp以python -I优先加载installed，上批同一选择加本批23例，
**1002 passed/50.31s**；161个父进程Corki模块均校验来自安装目录，进程exit_code=0。
既有连接恢复子进程包来源校验保留。首次完整回归的最终输出在会话上下文切换时未
保留，原进程已结束且句柄不存在，不将预期数量当作通过证据；收尾重新运行完整回归。

收尾完整回归确认 **1853 passed/113.98s**，exit_code=0。Codex HEAD仍为
ddf04ad26789d040f9ef6a96736f76602e35a6cc且干净；teach原SHA不变，docs/flow.md用户
暂存保留，未提交。本批是progress，不代表A/B/C/D/E整体完成。MCP仍使用名称前缀
前置标记，尚未依据实际server metadata及准入事实；原生ToolSearchCall/Output与hosted
模型通知的触发/恢复时序也未由结果flag替代。下一优先项沿这两条真实调用链继续源码
审计和RED→GREEN，不以本批通过测试关闭剩余差异。Phase2 agent/diff、Unicode属性、
服务独立关闭等既有开放项仍有效；没有运行真实模型、远程服务或Rust验证，goal保持active。

## 第六十八批：MCP污染资格绑定实际调用准入（修改前审计）

基准Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc干净；Corki仍为
041bf05eb93d353e3a67a402086ebf8cf04b39f0叠加既有工作区修改，保留用户暂存docs/flow.md。
Codex core/src/mcp_tool_call.rs handle_mcp_tool_call先解析arguments、检查prepared call
可用性和metadata/approval，再进入handle_approved_mcp_tool_call；污染标记位于
PreparedMcpCall.call_with_preparation的prepare闭包。codex-mcp/src/binding.rs保存不可变
server_metadata，先校验catalog revision，再调用prepare，并持读锁直到调用结束。
server.rs McpServerMetadata.pollutes_memory默认true，不是远端Tool.annotations或_meta。
maybe_mark_thread_memory_mode_polluted检查配置和实际prepared_call.server_pollutes_memory。
binding_tests.rs stale_prepared_call_does_not_run_preparation及
preparation_holds_catalog_authority_until_it_finishes分别证明拒绝旧准入无准备副作用、
准备期间保持catalog权威；old_call metadata测试证明元数据随已准备调用保留。

Corki graph._execute_one在claim、曝光、参数校验和MCP路由之前按mcp__前缀标记，假名、
非法参数、未曝光、移除server/tool及执行前中断均可能误标。manager.call_tool虽刷新后
取实际连接并检查remote_tools，却未携带宿主污染资格；connection.lease只保活已准入
连接。D04/B07/E01部分一致、P0。修改方案：ToolContext传递按线程绑定的异步标记回调；
MCP实际handler在校验后进入router，router先刷新/检查存在性，在实际connection lease
内、远程call之前依据该连接的宿主metadata执行回调。独立MCPTool也按实际适配器身份
处理，不靠名称。metadata由宿主Python接口提供，默认true，不从模型、远端响应、
initialize或plugin manifest读取。已准入连接保留自己的metadata，未准入调用用刷新后的
值。删除graph前缀分支，保留第67批一般结果typed事实及best-effort/取消合同。

先RED真实Runtime：假mcp名、非法JSON/schema、未曝光、调用前移除server/tool不标记；
有效MCP在请求发送前已标记，包括远端error/timeout；宿主false不标记且远端伪造false
不得绕过true；CodeMode嵌套同路，连接刷新/取消验证回调时序。更新既有仅靠假名前缀的
fixture为真正MCP适配器，不能把误标当作验收前提。另验证执行前中断和metadata冻结。
本批不把连接保活lease宣称为Codex的catalog读写锁：Corki现有刷新可发布新代而旧准入
调用继续，精确catalog替换串行化/审批权威仍需后续独立对齐；原生模型事件覆盖亦开放。

### 实现与阶段验证

新增实际HTTP MCP/Runtime测试先 **6 failed/3 passed/1.36s**，失败均为假名、非法JSON、
schema不合法、未曝光、移除server/tool仍将memory_mode写成polluted；有效/远端error/
timeout三例原有前缀机制下已通过。删除graph前缀分支，将线程回调传入ToolContext；
MCPTool识别实际适配器并在executor校验后路由，manager在刷新和存在性检查后调用实际
connection，connection在lease内执行metadata控制的回调，随后发送远端请求。不把回调
存进checkpoint或模型schema，也不将任何远端字段解析为可信资格。

新增冻结MCPServerMetadata及严格boolean检查，默认pollutes_memory=true；Runtime.create
的mcp_server_metadata与request_mcp_refresh的server_metadata是宿主Python接口，复制输入
mapping并为每代connection保留metadata。刷新前已准入调用不改绑、未准入调用使用新代。
普通调用及CodeMode共用实际router；独立MCPTool仍在真实adapter边界标记。既有两组
仅用名字模拟MCP的memory fixture改为MCPTool加mock client；不是删除污染验收断言。

中间实现一次参数补丁错误地加在Runtime.__init__而非create，静态检查发现undefined并
导致31项NameError；已修正签名，未将该失败归因于测试。修正后相关组合
**57 passed/3.75s**。补充true/false跨代组合×普通/CodeMode、发送前状态检查、输入mapping
隔离、claim阶段取消、metadata非法值/冻结以及lease跨异步标记/retired拒绝，新增共23例，
**23 passed/1.84s**。这些离线mock只证明Harness与MCP链路，不证明真实模型选择质量。

完整回归 **1876 passed/118.68s**，examples独立 **3 passed/3.99s**，均exit_code=0。
Ruff check、351文件format、compileall、pip check、Node bootstrap语法及git diff --check
通过。首次--no-build-isolation构建因当前venv无hatchling失败；改用项目声明的标准隔离
构建成功，未修改项目依赖。wheel
`/tmp/corki-mcp-admission-final.zx3ZGS/corki-0.1.0-py3-none-any.whl`，701677 bytes，
SHA-256 `c8c3327084e384d5950b53494a9dd38bde3c7d984017dc10cffcca1b05519739`。

从/tmp以python -I优先加载installed，沿用第67批选择并加入全部unit/mcp与integration
test_mcp_*.py：**1057 passed/55.99s**，exit_code=0；161个父进程Corki模块均校验来自
安装目录。既有connection_retry子进程包来源校验保留，不外推所有子进程均从wheel导入。
所有测试/构建/安装句柄已确认结束。teach SHA仍为
816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a，用户暂存docs/flow.md
保留，未提交。未执行真实provider/server/Rust测试；goal保持active，本轮progress。
下一轮继续实际模型ToolSearchCall/Output/hosted通知触发及恢复合同；MCP catalog revision
更新与整套binding替换须区分追踪，不能把connection lease误当读写锁，也不能未经调用链
证明就把所有新连接发布都串行化到长工具之后。其余A/B/C/D/E开放项继续有效。

## 第六十九批：模型原生外部事件的持久化、污染与恢复（修改前审计）

Codex基准仍干净。core/src/stream_events_utils.rs record_completed_response_item先记录
完整单项，再依据response_item_may_include_external_context标记：ToolSearchCall、
ToolSearchOutput、WebSearchCall及FunctionCallOutput(call_id=None)。普通FunctionCall、
有call_id的FunctionCallOutput、CustomToolCall/Output、LocalShell及正文不标记。
对应stream_events_utils_tests.rs两组正/负分类覆盖。session/turn.rs drain_in_flight也
消费原生输出；session/mod.rs record_prepared_conversation_items对无call_id通知补标，
启动载入历史同样重检此类通知。协议models.rs保留原生execution/status/action/output等。
完整item与完整response不同；不能等response.completed才保留已完成的污染事实。

Corki Responses适配器已在output_item.done发ModelItemCompleted，但ToolSearchCall
转换为普通ToolCallItem后丢失类型，非法参数/未曝光或handler失败时不再有结果flag。
WebSearchCall、ToolSearchOutput及无call_id的FunctionCallOutput则被忽略，既丢上下文
也漏污染。graph允许的model item只有正文/reasoning/call，持久化后不消费原生事实。
A04/B03/C05/D04/E02部分一致，P0。先保存本审计，再补协议和实际适配器/Runtime链路。

实现方向：ToolCallItem增加默认false且旧JSON省略的typed外部事实，仅原生搜索wire设置；
托管事件使用独立HostedToolItem保存有界、不可变JSON，不伪装成assistant正文或有本地
call_id的ToolResult。支持web search、search output、server search call和无call_id通知，
不生成本地执行任务或无主工具结果。Responses按原生形状回放；Chat提供明确标注的外部
事件兼容消息，不提升到system/developer。接item codec/checkpoint/token成本/压缩输入/
原始archive和历史召回。单项payload设硬上限并计入现有response总量，拒绝超限而不截断
JSON；原生shape的完整媒体/能力和按模型token截断合同不能因此称完全一致。

Runtime在单项append成功后消费事实，在完整model step提交/缓存恢复和partial冷恢复时
补消费，维持best-effort且取消传播；启动仅对已保存无call_id通知重检（不能把所有普通
历史重新判为外部）。RED覆盖原生无效搜索仍污染、普通同名函数不凭名污染、hosted完成
及流失败保留、下一HTTP请求回放、冷恢复不重采样/执行、配置关闭、schema/旧JSON兼容。
完整宿主通知生产入口/CodeMode notify、多Agent fork和所有provider原生shape仍须继续
审计，不能将本批入站事件与存储回放等同所有通知来源完成。

### 实现与阶段验证

初始测试fixture忘记显式开启模型native-search能力，12失败不作为产品RED证据；修正
能力后11 failed/1 passed，其中正常终态下原生search/web/notification/search-output漏标
及server search被当client解析均暴露，普通同名function为通过负例。失败终态fixture
使用未知invalid_request_error，按既有Codex分类为可重试，Runtime继续新step而完成，
不能据此声称吞错误；改用已核实不可重试的invalid_prompt后正确验证TurnFailed。

ToolCallItem新增布尔事实及严格类型，旧false JSON省略；Responses在原生search完成时
设置，普通function同名不设置，并拒绝相同completed item身份改换种类。HostedToolItem
冻结payload_json，仅接收明确的四种原生外部事件；独立hosted解析/完成管理器去重、
拒绝修改已完成payload、不有限JSON/非法基础shape。完整响应数组补发与流式done共用
该管理器，计入response总字符预算。语义类型贯穿item codec/checkpoint allowlist/
token成本/Responses replay/Chat和legacy message兼容/原始提取archive/历史搜索读取。
没有本地call_id就不伪造result pair，hosted search定义仅回放，不冒充本地registry准入。

graph在append_partial_item之后标记，before live dispatch；完整提交或cached step与
partial冷恢复补消费。共享memory.pollution边界沿用warning-only和取消传播。Runtime
初始化在后台记忆启动前仅重检已存call-id-less通知；普通搜索/正文及其它历史不被启动
扫描重分类。取消在单项持久化后发生仍保留事实。既有MCP及一般typed结果路径复用同一
失败隔离helper。没有改动模型重试或把失败response写成成功model step。

P0上下文体量人工审查：新单项硬上限16,000 UTF-8 bytes，按当前保守token估算低于
10K tokens（可能超过1K）；内容全额计费，原生JSON不截断，超限显式协议失败。这是
当前有界实现的限制，不等价于Codex按模型TruncationPolicy对具体output截断；此差异
继续开放，不能以安全上限宣布原生上下文管理对齐。完整action/媒体字段和provider
能力转换、宿主/CodeMode通知生产入口、原生search输出的本地发现准入也仍开放。

第一组24例验证6种wire×终态成功/失败×配置开关，后续partial/completed冷恢复（含
native及普通同名call）不重采样原step、独立Chat HTTP回放和startup通知分类、写失败/
取消持久化、实际接收→manual compact→冷启动history search→read，另含codec/旧JSON/
checkpoint/预算/非法payload与去重。组合 **55 passed/3.37s**，本批新增55例。中间一版
完整回归 **1920 passed/109.70s**；后续补充legacy投影与测试后重新运行最终全量，
不以中间结果替代最终状态。全部为离线mock，不验证真实模型选择或远程服务兼容性。

最终完整回归 **1931 passed/112.52s**，examples独立 **3 passed/2.73s**，均exit_code=0。
Ruff check、356文件format、compileall、pip check、Node bootstrap语法与git diff --check
通过。B03保留原有未关闭项，并新增hosted search output目前只保留/回放定义，不将其
伪装成ToolResultItem.discovered_tools或绕过本地registry权限，完整发现准入需继续审计。

收尾继续定位到Codex core/src/context_manager/history.rs:340 record_items_with_metadata：
只有FunctionCallOutput/CustomToolCallOutput对模型可见副本执行fallback_token_limit_override
或model policy×1.2，再调用utils/output-truncation truncate_function_output_payload；
ToolSearchCall/Output和WebSearchCall不经过此分支。因此不能把目前统一16K字节拒绝
当作对齐后的最终策略。下一优先项应沿history副本/原始rollout区别和不同原生类型预算
实现该合同，替换本批暂时的统一入站上限；完整hosted媒体预算亦需同链处理。

标准隔离构建wheel成功：
`/tmp/corki-hosted-events-final.Br58Kw/corki-0.1.0-py3-none-any.whl`，705006 bytes，
SHA-256 `cde96f16fbacba3e8ae0f1cd0436ca79913c25fe996cd4b8c48955c7fc567b8e`。
从/tmp以python -I优先加载installed，沿用第68批选择加本批55例：
**1112 passed/54.03s**，exit_code=0；164个父进程Corki模块均来自安装目录。既有
connection_retry子进程包来源校验保留，不外推所有子进程。测试/构建/安装句柄均结束。
Codex HEAD仍ddf04ad26789d040f9ef6a96736f76602e35a6cc且干净；teach SHA仍
816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a，用户暂存docs/flow.md
保留，未提交。本轮progress，不把有界入站事件支持当作全套原生协议和通知来源完成。
未执行真实provider/server或Rust验证，其余A/B/C/D/E开放项有效，goal保持active。

## 第七十批：原始hosted档案与模型可见输出策略分离（修改前审计）

完整追踪Codex context_manager/history.rs record_items_with_metadata：保留原始item引用，
复制processed模型历史，仅FunctionCallOutput/CustomToolCallOutput调用truncate_function_output_payload，
使用可信fallback_token_limit_override或model.truncation_policy×1.2（ceil）。Session
record_prepared_conversation_items将原envelope持久化为rollout，模型历史不是原始档案。
protocol/protocol.rs TruncationPolicy有Bytes/Tokens；utils/string/truncate.rs按UTF-8均分
头尾，marker额外保留，0预算仍产生marker；字节模式报告实际丢弃字符、token模式ceil
((original_bytes-budget_bytes)/4)。utils/output-truncation/src/lib.rs按顺序消费文本和音频，
空文本删除、图片/密文保留不扣该局部预算，末尾计数省略text/audio。对应truncate_tests.rs
已有Unicode/tiny budget/多段文本和媒体顺序场景。音频估算utils/audio基于时长×10，无法
解码时UTF-8字节/4；Corki目前只实现WAV时长，其余codec能力差异仍在既有开放项。

models-manager/model_info.rs未知模型fallback Bytes(10000)，可由tool_output_token_limit
覆盖（Bytes模式转4×tokens）；bundled models.json中除gpt-5.2为Bytes(10000)，Corki已收录
的其余10个模型都是Tokens(10000)。自定义catalog优先，不能将模型策略混同旧
tools.output_char_budget（Python字符预算）。Corki目前model catalog只有window字段，
HostedToolItem统一16K字节拒绝所有事件且没有模型副本；新记录超过上限会错误TurnFailed，
也无法按模型策略保留原始通知。C02/B03/D04/E03部分一致、P0；本批明确修复此差异。

方案：补typed模型TruncationPolicy、静态catalog及token覆盖，独立hosted history projection
只修改函数输出模型副本；raw payload、item ID、存储archive和历史召回保持原文。新可见
投影随request checkpoint保留，不写回原始历史；旧Hosted字段缺省兼容。prepare/compact/
预算通知共同使用该投影，不在HTTP序列化后才截断以致token估算错位。完整响应仍受现有
4M字符预算约束，原始hosted envelope改用32MB存储/传输防护而非16K内容策略；search/web
不受函数输出策略裁切。一般本地ToolResult旧executor字符预算及全部媒体能力转换不在
本批宣称已解决，继续沿同策略审计，不把局部新policy当成全局一致。

先RED：>16K通知可完成并在后续HTTP/压缩输入中截断、raw/history完整；>16K search/web
不误截断；Bytes/Tokens/0/Unicode/marker额外/顺序text+image+audio+ciphertext；可信覆盖
不乘1.2且远端payload假metadata不能控制；自定义与bundled/fallback模型选择，旧JSON和
冷启动/partial恢复保持事实。接实际Runtime验证后运行完整回归和安装包验证。

### 实现与阶段验证

6个实际Responses/Runtime场景先 **6 failed/0.67s**，大于16K的通知、web及search output
都在首次Turn失败。补typed TruncationPolicy、ModelContextInfo政策字段、bundled已核齐
的10个Tokens/1个Bytes条目、未知Bytes fallback、自定义catalog以及tools.output_token_limit
解析/覆盖。其与旧字符预算区分，尚未宣称普通ToolResult路径已经切换该策略。

HostedToolItem新增可见model_payload_json及可信fallback_token_limit_override，可缺省且
旧JSON省略None；可见副本保持原ID/raw payload，普通archive写入不带该投影。checkpoint
可携带已冻结投影，不重复截断；提取与历史召回继续读raw。原生函数输出按policy×1.2或
精确token override处理，文本UTF-8头尾/marker额外、0预算marker、空text移除、音频整项
计费、图片/密文不扣局部输出预算、末尾省略计数均接入；raw内容数组基础schema现在在
入站验证。无截断/规范化变化时保留原对象与JSON拼写，避免仅空白变化破坏历史比较。

Window prepare/manual compact/budget notices共用模型历史投影；graph从旧request checkpoint
恢复也补投影。32MB仅作为原始envelope存储/传输防护，移除统一16K模型内容拒绝；原生
搜索/Web不按函数输出预算裁切。当前总请求对hosted媒体仍有保守JSON估算、媒体能力转换
和非WAV时长差异；不得把这批局部预算称作完整媒体合同。P0体量复核：可见函数输出按
模型policy有界（marker额外），可能超过旧16K字节/10K估算token；仍经过完整窗口检查，
不再为凑固定上限改变Codex分支语义。search定义须保留完整，预算不足由整体上下文处理。

中间4失败：search-output压缩请求采用已声明兼容消息而fixture只查native shape；另外
小通知无变化却被JSON重排造成旧fixture精确比较失败。前者改测试检验兼容消息内的同一
完整JSON，后者修生产代码保留不变对象。相关组合随后 **125 passed/3.97s**。
新unit/integration同basename导致pytest collection冲突，已将unit命名为hosted_truncation；
4个配置负例fixture漏working_directory也已修正。最终新增31例针对组合
**31 passed/1.53s**，包含源码golden、raw/visible/codec/checkpoint、预算估算、model政策/
覆盖、6个HTTP和4个小窗口partial/completed冷恢复场景。原始大通知不重采样、不回写截断。

### 最终门禁与边界

上一次全量进程终态输出在上下文交接时未保留；核实已无存活pytest后重新执行，不把
丢失的输出视为通过证据。本次最终全量 **1962 passed/124.00s**；示例包装执行
**3 passed/3.10s**。Ruff check通过、format check **360 files already formatted**，
compileall、pip check、Node bootstrap语法检查及git diff --check均通过。

标准隔离wheel构建与独立target安装成功：
`/tmp/corki-hosted-policy-final.SvSSLu/corki-0.1.0-py3-none-any.whl`，708324 bytes，
SHA-256 `3b64dc15872821ea558822c064d0a6b7bce9442c5719d507b719c75d9a0644ea`。
从/tmp以Python -I运行安装包选择集 **1143 passed/60.53s**，显式验证当前测试进程
加载的 **166个Corki模块** 均来自独立installed目录；connection retry专用子进程继续
核对安装包来源，不外推所有其他子进程fixture也已单独验证。该集合覆盖context、
protocol、storage、memory、MCP、skills、prompting、CLI及核心Runtime恢复/工具/
压缩/通知/历史召回等组合，新增31例在其中，不仅重复原有绿色测试。

README与第三方归属更新，B03/C02/D04摘要同步本批局部结论。普通ToolResult的旧
executor字符预算与原始/可见副本关系、完整工具结果metadata和媒体能力/非WAV codec
仍是下一条同源调用链的实际差异；Python整数与Rust usize/i64极值/饱和语义亦未声称
位级一致。本批不证明真实模型选择质量，不证明全部hosted生产链或远端服务端行为；
未执行真实provider/server或Rust验证，其余A/B/C/D/E开放项继续有效。

Codex仍为干净的ddf04ad26789d040f9ef6a96736f76602e35a6cc；teach.md SHA-256仍为
816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a；用户暂存
docs/flow.md保留，未提交。所有本批验证进程已收敛。本轮仅progress，goal保持active。

## 第七十一批：普通工具原始结果、执行账本与模型历史预算（修改前审计）

上轮为progress：第70批实现及31个新场景、全量1962和安装1143验证已落盘。本轮仍沿
同一输出调用链，不将hosted局部能力扩张为普通工具已对齐。Codex工作树仍干净，用户
暂存docs/flow.md保留，teach.md不修改。

源码链：tools/src/tool_output.rs ToolOutput将log_output、to_response_item与Code Mode
结果分开，fallback_token_limit_override为可信宿主元数据；core/tools/context.rs
FunctionToolOutput.to_response_item克隆原body，默认code_mode_result使用该原始输出。
core/tools/registry.rs AnyToolResult.into_response将override附入envelope；session/mod.rs
record_prepared_conversation_items将原envelope写rollout、processed副本进入history；
context_manager/history.rs record_items_with_metadata仅函数/custom输出按模型policy×1.2
或精确可信token override截断。utils/output-truncation完整顺序分支及已有golden继用。
ToolSearchOutput是独立原生输出，不套函数预算。McpToolOutput则先加wall-time并截断
response_payload，而code_mode_result保留raw；shell/exec又有自己的格式和预算。故不能
声称“所有工具rollout都是handler原文”，必须分别追踪这些具体输出类型。

Corki executor._normalize_result目前先用spec/global Python字符预算修改content与
ordered content，graph._execute_tool随后将同一结果写complete_tool_call账本，再构造
ToolResultItem；嵌套调用也返回已损坏的结果。error()同样提前丢失超长诊断。context
window只投影hosted，因此普通结果既不消费模型Bytes/Tokens策略，也无法通过原始
archive/历史检索恢复被executor删掉的文字。B07/C02/C04/D03/E03：行为不一致，P0。

修复方案：普通executor保留经过验证的结果与诊断，展示日志独立有界；可信token
override、明确search输出分类、显式旧spec字符预算随结果和账本/历史保存，缺省字段
兼容旧JSON。window在prepare/压缩/预算与旧request恢复之前对普通结果构造模型副本，
模型policy×1.2或精确override；已投影checkpoint不重复截断。显式旧spec字符预算仅作
额外可见限制，默认global字符预算不再替代模型policy；Code Mode嵌套消费原结果。
完整媒体转换、MCP/shell工具专属格式与原文边界、Code Mode自身formatted预算另行审计。

验收先RED：实际Responses/Chat多Step正常、freeform、错误与ordered输出，模型小预算
截断但账本/原始archive保持长Unicode正文；后续Turn/冷启动/手动压缩仍同一视图。
单项override含0、旧codec/checkpoint、真实nested调用、搜索定义不误截断及执行设置
跨代兼容继续验证；不以ScriptedModel预设调用证明真实模型选择质量。

### 实现、失败证据与回归修正

executor取消普通成功/错误结果的提前内容截断，保留独立有界display；增加原始body/
attachments的32MB防护，超限返回明确错误Observation，不将截断成功结果冒充完整值。
超长异常诊断按UTF-8字节上限保留首尾与marker；这是Corki存储防护，非声称Codex具有
相同原始体量上限。媒体预处理仍可能更改表示，所以本批“原始”指规范化结果，不是
所有handler输出bytes或MCP远端原始envelope的无损保存承诺。

ToolResult与ToolResultItem保存可信fallback_token_limit_override（含0）、明确search
分类及显式spec旧字符预算；完整ledger codec保留这些字段，缺省None不进入旧JSON。
executor只采纳可信handler的token override，search分类和legacy预算取实际绑定类型/
spec，不采纳handler伪造的这两项。旧分类None单独走旧名称/定义兼容分支；新false不
因名称恰好tool_search绕过预算。request-only model_output_projected在codec/checkpoint
恢复时保留；没有实际内容变化则返回原对象，避免无意义改写现有历史比较。

新增function_output调用与hosted相同的顺序截断实现，保留image/ciphertext顺序，传入
模型Bytes/Tokens×1.2或可信精确token override。window.prepare/manual/自动预算及
graph旧request恢复均接同一投影。显式spec字符预算作为额外兼容限制保留（ordered
继续旧近似算法），不会放宽模型预算；global output_char_budget仅约束展示，改用
output_token_limit配置模型预算。嵌套Code Mode得到账本原值，不再先套直接输出限制。

最初两次HTTP测试失败来自fixture漏capabilities及误写ledger表名，**不是功能RED**。
修正fixture且已移除executor截断、尚未接window投影时，8个实际两适配器请求场景
**8 failed/1.54s**，失败点为完整长正文直接进入模型、未消费小模型policy。这是分阶段
RED，不冒充原始未修改树的完整基线。接投影后相关37例中35通过、2个旧错误测试仍
要求丢弃诊断原文；已改为原诊断完整、模型copy<=80、display<=80三重断言。

首次宽回归 **26 failed, 1944 passed/119.69s**：18例Code Mode旧断言依赖nested/ledger
被提前截断；5例压缩fixture依赖旧global/更大spec字符预算越过模型policy；2例错误
诊断旧断言；1例未变化的结果仍加projected标志造成逐项比较不同。前23例更新明确
契约/显式token预算以继续原压缩触发目的，错误例保留原文与预算双重验收；最后1例修
生产代码保留不变对象。相关context/历史notes/skills/真实Code Mode/新unit组合随后
**67 passed/9.60s**。不是削弱当前输入保留、压缩、并发/跨代准入或冷恢复断言。

新增unit14例覆盖Bytes/Tokens ordered黄金值、三种override、原始/投影/JSON/checkpoint、
旧payload缺省、typed search与legacy分支、非法宿主override和原始UTF-8 guard。新增
integration12例：8个实际HTTP（文本/freeform兼容/错误/ordered×两adapter）经历多Step→
原始ledger/archive/提取→冷Turn→手动压缩；另4例在工具账本完成、历史未追加时故障，
实际Runtime冷恢复且handler只执行一次，覆盖直接/真实Code Mode及None/0覆盖。
Code Mode读取完整Unicode结果长度，而不是打印被截断的内层原文。直接旧字符兼容
marker是characters omitted而非原生tokens truncated，修正对应fixture分支后最终新增
组合 **26 passed/2.06s**。真实模型选择、完整媒体/MCP/shell格式等仍未执行/未关闭。

P0体量复核：普通文本模型项现在使用实际模型预算（例如Tokens10000的history allowance
可达约12000 token再加marker），不以旧固定字符上限冒充该源码分支；所有可见副本仍
经过完整请求窗口/硬上限检查。原始结果和nested值上限独立，不会因“原文保存”绕过
模型预算；媒体/ciphertext未被局部文本预算删除，完整媒体成本与Code Mode跨边界
极限仍需后续核齐。明确原始32MB guard、显式旧spec字符限制是兼容边界，不声明位级
复刻Rust所有类型/整数极值/媒体codec。B07/C02/D04总表已同步，其他开放项不缩减。

### 最终门禁

最终完整回归 **1988 passed/115.57s**，示例执行包装 **3 passed/2.77s**。Ruff check
通过，format check **363 files already formatted**；compileall、pip check、Node
bootstrap语法及git diff --check通过。未以中间失败运行代替最终门禁。

标准隔离wheel构建成功：
`/tmp/corki-function-policy-final.pf6wgf/corki-0.1.0-py3-none-any.whl`，710812 bytes，
SHA-256 `0cdf1cb62b33de6a682af237d722bc71e7c6249ea70b29e5f52f96c1dfc92f20`。
独立target安装、从/tmp以Python -I运行选集 **1207 passed/64.28s**，显式确认当前
测试进程的 **167个Corki模块** 均从该installed目录加载。选择集包含前批1143、
本批新增26、另补真实Code Mode step admission28/context journal6/discovery settings4，
覆盖所有本批修改的测试文件；connection retry专门子进程继续检查包来源，不外推
其他子进程也都逐一验证了导入来源。

Codex仍干净且HEAD为ddf04ad26789d040f9ef6a96736f76602e35a6cc；teach.md SHA-256
仍为816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a；用户暂存
docs/flow.md保留，未提交。本批验证进程均已终态。下一条同源差异为MCP/shell/Code Mode
各自的model/log/nested输出序列化、工具专属预算及metadata保留，不把普通函数路径
当作它们已对齐。A/B/C/D/E总目标继续active，本轮progress，未执行真实provider/server
或Rust验证，不宣称核心Harness整体完成。

## 第七十二批：MCP输出转换、结构化优先级与有效连接预算（修改前审计）

上一轮为progress：普通输出原文/模型副本与故障冷恢复落地，1988全量与1207安装回归
完成。继续MCP真实输出链，Codex工作树仍干净，用户暂存docs/flow.md保留。

源码：core/tools/handlers/mcp.rs先prepare_mcp_call，输出policy来自该PreparedMcpCall
的有效config，codex-mcp/src/binding.rs output_token_limit查**每工具**tools[name]的
NonZeroUsize，不是任意远端metadata或全server统一值；没有该值才用模型policy。
handle_mcp_tool_call最终CallToolResult::from_result把远端错误变为error-valued MCP
输出，不应把这些错误误作Code Mode host分发异常。mcp_tool_call.rs的model modality
sanitizer先替换不支持的image/audio块，nested公共结果也消费这个sanitized结果。

protocol/models.rs CallToolResult.as_function_call_output_payload先转换有序content：
text块_meta.codex/encryptedContent严格true才转opaque；存在该类型时不被structured
覆盖。否则非null structuredContent独占Text body，不能拼接旧text/媒体。image/audio
保留已有data:URL，否则使用mimeType/mime_type或application/octet-stream构造URL；
imageDetail接受auto/low/high/original，其他默认high。未知/畸形块（含非对象）序列化
为JSON文本，不静默丢弃。core/tools/context.rs McpToolOutput在Text/body数组前插入
Wall time四位小数/Output header，按policy×1.2截断，history override为该allowance
换算token预算（Bytes ceil/4），nested公共MCP结果保留未截断内容但剔除top-level_meta。
日志仅body文本片段（跳过blank/media/opaque），不退回序列化媒体或密文；日志有独立预算。
已有context_tests与models.rs测试覆盖wall-time、结构化大结果、媒体/密文及nested私有字段。

Corki MCPTool目前固定spec字符预算160000，拼接content与structuredContent，图片挪到
attachments末尾、音频仅JSON、加密text当普通明文、非mapping静默丢弃；不捕获实际
连接的每工具token配置，也缺wall-time包装。第71批通用投影不能替代这些工具专属
语义。B05/B07/C02/D04/E01部分一致/行为不一致，P0（密文暴露到文本/展示和媒体顺序）。

方案：独立typed MCP输出转换；source顺序/structured/opaque分支、modality替换、纯文本
日志、header与MCP专属预算，保留CodeMode公共原值。ToolContext传宿主model policy/
媒体能力，连接lease中捕获实际settings的tools[name].output_token_limit并随本次调用
使用，不能从旧schema实例拿新执行连接的预算。可信每工具配置严格正整数；原生
history override持久化继续第71批codec。保留取消、未知副作用不自动重试与原始体量
防护。完整MCP审批/catalog读写锁、media codec/image preparation逐字合同、shell/exec
预算仍另列，不能由本批替代。先source映射RED，再实际HTTP与冷恢复/刷新/nested验证。

### 实现与阶段验证

初始source映射 **4 failed/0.22s**：直接MCPTool在结构化、密文、图片、未知块场景均
缺history override，原内容也分别出现拼接、密文明文、媒体JSON和非对象丢失；测试
继续检验body/顺序/日志/nested结果，不是只补一个常量让断言通过。

新增mcp/output.py：以公开MCP协议字段构造严格有界JSON副本，先按宿主input能力
替换image/audio；block metadata保持，top-level_meta/未知transport字段不进入nested。
typed转换保留有序text/image/audio/opaque，处理data URL、mime alias/default、detail
和未知/畸形JSONfallback。非null structured独占输出，但opaque优先。model与日志加
四位wall-time header；日志仅未截断的文本片段，再由executor实施独立展示上限，绝不
先取已被model预算裁剪的正文。MCP model payload用policy×1.2，可信token fallback
进入既有ledger/history codec；Code Mode保留公共原值，真实媒体准备仍在其后的独立
边界。去掉固定MCP spec.output_char_budget=160000，不用旧字符语义替代source policy。

ToolContext在直接与nested实际graph路径传model policy及媒体能力。MCPServerSettings
解析tools[remote_name].output_token_limit为严格正整数（source NonZero，不接受0/
bool/float/string），不可由远端_meta覆盖。MCPManager先应用pending刷新/当前名称
检查，MCPConnection在实际lease内把该client settings的每工具预算交给调用局部capture；
已在执行的旧连接退休不改变本次policy。没有配置时使用Step宿主model policy。仅新增
该每工具输出字段，不声称其他同层MCP审批配置已支持，也未引入catalog读写锁。

MCP协议/HTTP/Timeout/OSError转为isError=true的公共MCP结果；Code Mode resolve该
错误值，不把远端失败误作dispatch_error拒绝Promise。超时保留结果未知/不得自动
重试有副作用操作的提醒；不捕获CancelledError，不重放远端调用。畸形handler结果仍
经普通executor规范化错误，未放宽代码执行/审批范围。

初步52例组合 **3 failed,49 passed/2.19s**；首次完整运行
**7 failed,1985 passed/115.98s**，均为旧fixture期待无header字符串、content+structured
拼接或attachments字段。更新这些语义断言及扩展示例，保留调用次数、进程退休顺序、
结构化优先和日志安全验证。相关MCP/示例组合随后 **63 passed/4.74s**。

8例实际HTTP MCP刷新+Runtime验证旧/新每工具预算的四种组合（含缺省）×direct/nested，
从旧Step工具实例调用时消费新admitted连接policy；冷重开不重执行，ledger保持完整
公共nested值和精确fallback。另3例实际Code Mode验证协议/async timeout/http timeout
是resolved error-valued MCP结果且只调用一次。连接退休unit再验证旧1→新80预算独立，
旧client在调用结束后关闭，不因为刷新改变已捕获输出限制。

模型HTTP媒体测试曾 **3 failed,23 passed/1.04s**，暴露fixture把转换层支持low当成
整个pipeline支持low。重新追踪session.prepare_conversation_items_for_history→
prepare_image_response_items→core/image_preparation.rs:294，确认默认DetailBased拒绝
low，而UnifiedBudget另有条件。Corki现有这条分支一致，保留生产行为，将集成测试
扩为high/low×媒体支持/不支持×Responses原生密文/Responses兼容/Chat共12例，
验证high图像及音频、low明确降级、opaque不落入display、结构化不覆盖opaque、
能力替换也影响nested公开结果。未将这些场景冒充全部图片/音频codec能力完成。

中间完整运行仍采集旧low fixture，**3 failed,2022 passed/120.49s**；修正后的
12例媒体HTTP **12 passed/1.20s**。补畸形type为list/dict时JSONfallback，避免Python
set membership对unhashable类型抛异常。最终新增21个unit+23个integration组合
**44 passed/2.67s**。本批没有真实provider/server/Rust验证，配置极值、完整授权/事件/
媒体准备/Code Mode桥体量及shell/exec专属输出边界仍保持开放。

P0复核：typed媒体/opaque不序列化回log文本；未知/畸形块按source成为JSON文本，
可能含任意字段，不能把该投影说成通用敏感信息过滤器。MCP nested保留公开content
及block metadata（包括opaque块），不会解密；它与普通FunctionToolOutput的默认JS
文本投影不同。源JSON/CodeMode/HTTP/模型窗口各自上限仍独立，MCP policy×1.2可能
产生超过1k token的单项，按实际source模型策略接受且仍经完整请求硬上限，不用旧
160000字符限制冒充该分支。其余未完成A/B/C/D/E项不缩减。

剩余错误边界明确记录：本批验证了MCP协议异常、HTTP异常、超时及畸形**content块**的
处理，不等于全部顶层MCP结果shape已对齐。当前content非数组/isError非法等在输出
转换校验时仍走普通executor dispatch error；还需沿Codex rmcp反序列化→
handle_mcp_tool_call::from_result追踪这些远端坏shape是否应统一为MCP error-valued
结果，补实际传输/Code Mode组合后才能关闭该子项。不会以现有成功/error测试替代。

### 最终门禁

最终完整回归 **2032 passed/122.29s**；扩展示例含MCP新header语义，示例执行包装
**3 passed/3.13s**。Ruff check通过、format check **367 files already formatted**；
compileall、pip check、Node bootstrap语法和git diff --check通过。

标准隔离wheel构建/独立target安装成功。README日志边界补充明确后重新构建最终包：
`/tmp/corki-mcp-output-final.hHkIO1/corki-0.1.0-py3-none-any.whl`，713831 bytes，
SHA-256 `2933ef3c6cdd522475dc5f60a2ad1f6db9446100b3956a221155c8f66a9d7e76`。
最终包从/tmp以Python -I运行选择集 **1273 passed/72.35s**，显式确认该测试进程
**168个Corki模块** 均来自独立installed目录。选择集为前批1207+本批44+另补普通/
MCP CodeModeOutput契约22例；涵盖本批所有变更的MCP单元/集成、实际stdio退休与
模型HTTP/Code Mode场景。connection retry专门子进程继续核对安装来源，不外推所有
其他子进程fixture也分别做过导入来源验证。初包另曾1273/61.59s通过，但最终证据
以上述重新构建后的SHA与72.35s回归为准。

Codex仍为干净的ddf04ad26789d040f9ef6a96736f76602e35a6cc；teach.md SHA-256仍为
816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a；用户暂存
docs/flow.md保留，未提交。本批验证进程均已终态。B05/C02/E01总表已同步；下一步
继续顶层MCP坏shape错误分层及shell/Code Mode专属输出链。所有A/B/C/D/E开放范围
仍有效，没有真实provider/server/Rust验证。本轮progress，goal保持active，不声明
核心Harness整体完成。

## Batch 73 — MCP malformed-response boundary (pre-edit audit)

Codex `rmcp-client/src/rmcp_client.rs::call_tool` (788–864) awaits a typed
CallToolResult (legacy also checks ServerResult variant); binding.rs::call_tool
propagates failures; core/src/mcp_tool_call.rs::handle_mcp_tool_call finally uses
protocol/src/models.rs::CallToolResult::from_result to return an isError value.
The local converted protocol type requires array content and optional boolean
is_error. External rmcp 3.2.0 dependency sources are not installed: absent-content
defaults and complete wire-schema equivalence are NOT established here.

Corki client.call_tool checks only Mapping; mcp_output rejects malformed content
and isError outside MCPTool's remote-error catch. Consequently nested Code Mode
rejects a host call instead of resolving an MCP error value (B05/E01, inconsistent,
P1). HTTP response.json also leaks decoding ValueError; Python bool/float equality
can accept a response id that is not the emitted integer request id.

Plan: centralize confirmed top-level result checks at the protocol boundary and
inside the adapter's remote-error catch (including custom clients). Normalize bad
HTTP JSON into MCPProtocolError; reject non-integer correlated ids. Preserve
missing content / null optional isError compatibility, malformed individual block
fallbacks, cancellation, and ordinary local handler contract failures. Strict JSON
decoding must reject non-finite numbers and invalid Unicode without reflecting raw
remote bodies. SSE/stdio retain skip-invalid-message behavior; do not claim their
timeout/timing parity with external rmcp.

Codex run_service_operation (1316+) retries transient errors only for tools/list;
expired-session 404 has a separate reinitialization/replay path. This batch must
not add tools/call replay for malformed results, nor claim the unimplemented 404,
authentication, modern-protocol or complete JSON-RPC schema branches are aligned.
Acceptance: RED→GREEN actual HTTP+Runtime direct/nested observations, persisted
non-dispatch MCP errors, cold-history reuse without extra calls, strict response
identity and streaming invalid-message tests; full/static/package gates follow.

### 实施与行为证据

`mcp/client.py::validate_tool_result` 在基础客户端返回前和MCPTool的远端catch内
统一校验顶层Mapping、content数组、optional isError布尔；输出投影复用该校验。
自定义MCP客户端也不会绕过错误值边界。缺省content、null isError、未知/畸形内容块
JSON回退保持兼容；不宣称已验证外部rmcp依赖的全部默认值。

HTTP/stdio/SSE共享严格JSON解析：拒绝非有限浮点（含指数溢出）和非法Unicode；错误
只给有界诊断，不回显响应body。解析后额外序列化校验仍受既有transport输入上限约束，
并非零拷贝流式解析。HTTP非法UTF-8直接协议失败；stdio和SSE非法JSON消息跳过，
无匹配响应最终报错/超时；这不等于已对齐rmcp所有transport时间行为。请求与stream
分派只接受严格int响应ID，防止Python bool/float相等性串用请求身份。

新测试修复前：实际HTTP+Runtime direct/nested content-null两例RED；单元身份、
非法JSON、SSE/stdio分派与自定义客户端错误层级RED。自定义客户端fixture初次缺少
executor预算参数，修正fixture后另跑三例确认真正的dispatch_error=True反例。
修复后新测试 **35 passed/2.68s**（19单元+16实际HTTP/Runtime集成）。覆盖坏字段、
非法JSON/UTF8/NaN/孤立代理项，真实QuickJS错误值resolve，完整Turn结束，ledger
is_error且非dispatch_error，冷打开历史复用与ledger字节不变、远端仅调用一次。
stdio测试用StreamReader验证分派，不冒充本批新增真实子进程实验；真实stdio既有
刷新/退休/示例测试继续纳入全回归。取消直达CancelledError，普通本地CodeModeOutput
非法JSON仍assert dispatch_error=True，不被本次远端协议降级吞并。

首轮全量 **2062 passed, 2 failed/124.19s**：两条旧MCP坏envelope测试要求无公共值，
与本次明确修正的语义冲突；已更新为isError公共值且非dispatch_error，保留原错误
诊断断言。首轮收集早于新增三条兼容性控制测试；最终完整回归须重新收集全部测试。
首轮安装包选择集同样在旧断言修正前收集，最终以重新执行的结果为准。

本批关闭B05/E01的已确认坏envelope错误层级及Python响应身份漏洞，不关闭完整MCP
wire校验、rmcp defaults、404/auth恢复、现代协议、所有超限分支。自定义Python客户端
产生不属于JSON域的深层对象仍可能触发本地输出契约错误，不能描述成完整远端wire
解析覆盖。后续仍按总目标继续shell/Code Mode输出链及其他A/B/C/D/E开放项。

### 最终门禁

最终重新收集全量 **2067 passed/135.90s**；新测试35例全部通过；普通公共值/错误
契约专项 **22 passed/0.18s**；三组合示例 **3 passed/3.58s**。Ruff check通过，
format check **369 files already formatted**；compileall、pip check、Node bootstrap
语法和git diff --check通过。首轮失败已经如实记录，不以首轮结果作为最终门禁。

标准隔离wheel构建并独立target安装：
`/tmp/corki-mcp-malformed-final.cnj0CX/corki-0.1.0-py3-none-any.whl`，714464 bytes，
SHA-256 `91a858c0f80add0253c337e3ce5b6213fe3d275d3039e9718d2da3fad84efe33`。
最终安装包选择集从/tmp以Python -I重跑 **1308 passed/75.17s**，确认进程内
**168个Corki模块**均来自installed目录；前批1273选择集+本批35例，完整覆盖本批
MCP与普通工具输出契约变更。首轮同包选择集1306 passed/2 failed/79.69s仍是更新
旧测试断言前收集导致的失败，最终独立重跑已通过；无安装来源混淆。

Codex保持干净的ddf04ad26789d040f9ef6a96736f76602e35a6cc；teach.md SHA保持
816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a；用户暂存
docs/flow.md保留，未提交。所有验证进程均已终态。本批为progress，goal保持active；
未执行真实provider/server或Rust测试。下一源码入口为core/src/tools/context.rs::
ExecCommandToolOutput (346+)、handlers/unified_exec/{exec_command,write_stdin}.rs，
再追踪Corki builtin/shell.py与CodeMode投影，不能以本批MCP边界代替其对齐证据。

## Batch 74 — unified exec output (pre-edit audit)

Codex pinned clean ddf04ad: handlers/unified_exec/exec_command.rs passes optional
max_output_tokens into manager; write_stdin.rs independently parses/passes its
own optional budget. unified_exec/process_manager.rs (610–809, 940–1042,
1478–1569) collects capped HeadTailBuffer, calculates per-observation byte-based
original_token_count, omitted bytes, chunk id and call wall time. mod.rs uses a
1 MiB collection cap independently of the default 10,000 model output tokens.
tools/context.rs::ExecCommandToolOutput (346–540) separates untruncated log,
model body min(request/default, model policy) with header allowance reservation,
and Code Mode raw output unless an explicit call budget was provided. Its tests
context_tests.rs (400–549) cover policy units, headers and omission metadata.
utils/output-truncation::formatted_truncate_text adds original count/line warning;
head_tail_buffer.rs preserves both ends and includes a middle omission marker.

Corki shell.py instead turns max_output_tokens into ProcessManager collection cap,
defaulting to 40k bytes; process.py preserves only tail. Thus raw/nested/log output
is prematurely lost, not just model-truncated (B07/C02 P1 inconsistent).
write_stdin schema cannot request a budget, inherits the initial collection cap;
output lacks chunk/count metadata and wall time is cumulative process age rather
than per-call wait. Existing general function output projection cannot recover
those lost bytes or recreate the source-specific warning/header contract.

Plan: independent host collection cap (default 1 MiB), bounded head/tail collection
and explicit omission/count metadata; per-call observation time/chunk identity;
dedicated shell projection matching source model/log/nested behavior. Add optional
nonnegative max_output_tokens to both tool schemas; do not feed it into collection.
Keep raw nested value in durable ledger and normal history allowance projection;
tiny model policy may still truncate metadata after source formatter exhausts body
budget, as in Codex. Tests must exercise real subprocesses plus Runtime/QuickJS,
per-poll budgets, cold durable result and bounded collection, not only fake output.

Not closed by this batch: numeric session IDs versus existing UUID compatibility,
non-TTY stdin policy, shell/approval/env selection, yield clamps, timeout modes,
process-store admission/cancellation/post-exit pipe drain/async watchers, remote
exec-server, and outer Code Mode cell formatting. No safety boundary expansion.

### 实施与行为证据

新增 `builtin/shell_output.py`，由exec_command/write_stdin正常handler调用。模型文本
保留Chunk ID、四位小数秒、退出/运行状态、原始token估计；按source的较小policy
选择和1.2序列化余量迭代预留header/warning/marker。保留bytes/chars与tokens单位，
不是把所有政策默改为字符数。嵌套公共值缺省不做模型预算截断；显式预算仅限制该次
公共output，且可大于模型预算；log从采集输出独立产生，executor继续独立display cap。
进程timeout保持现有is_error与显式诊断，未将未知/失败进程标成成功。

ProcessManager缺省采集上限改为source的1MiB（宿主可另设上限）；模型参数不再降低
采集上限。内部缓冲对称保留head/tail与中间省略marker，原始计数依据本次实际字节
含丢弃量，不把marker算进原始token。每次take清空当前采集，后续poll独立计算时间、
chunk/count。ProcessObservation新增字段有默认值；持久化仍复用既有content和
CodeModeOutput JSON，旧账本格式不重写。这里没有声称所有进程采集时序已与Codex
的两层异步drain、post-exit 50ms宽限、pause与remote executor等价。

修复前六条source-mapped测试RED：预算错误进入采集、缺少header、只有tail；修复后
本批 **30 passed/2.77s**（18单元+12集成）。原有tail-only单元断言按明确对齐目标
改为精确head/omission/tail字符串；未删除其超大chunk覆盖。新golden对照source
context_tests三组格式/单位/遗漏案例，额外覆盖nested显式预算大于模型预算、九组
不同chunk切分下缓冲等价、真实1100008字节子进程超1MiB、每call时间非进程年龄。

12集成是实际Python子进程→正常shell handler→真实Runtime/QuickJS→模型请求→
ledger→冷打开同Thread：direct/nested ×exec/poll ×缺省/4/0预算。poll首次执行用
1-token预算，后续仍可获取完整60008字节Unicode输出；不同预算均报告original
15002 tokens。核对终态、非dispatch_error、公共值、model文本大小、冷历史与ledger
字节不变且不重新发起工具调用。预设Model只证明harness链，不证明真实模型选择。
此交互fixture使用既有非TTY stdin兼容路径，不拿它证明Codex stdin policy对齐。

测试开发中修正了新文件与unit同名导致的pytest收集冲突（改名runtime）；修正手算
truncated-token期望9→10，以及fixture默认model是bytes政策、不能要求tokens marker。
源映射单位测试两种policy均保留。首轮全量 **2096 passed/133.71s**；最后补充一个
nested显式预算大于模型预算测试后已启动重新收集的最终全量，不能把首轮当作包含
该新增场景的证据。README明确有限采集/模型文本/公共值界限，THIRD_PARTY补来源。

### 最终门禁

最终全量重新收集 **2097 passed/128.24s**；本批30例全通过；三组合示例
**3 passed/3.18s**。Ruff check通过、format check **372 files already formatted**；
compileall、pip check、Node bootstrap语法及git diff --check通过。

标准隔离wheel构建/独立target安装：
`/tmp/corki-shell-output-final.4ATqp0/corki-0.1.0-py3-none-any.whl`，716100 bytes，
SHA-256 `8e88deed7fb02d63d6642431c26a7ff40caa66531a70549c7903efada7092770`。
从/tmp使用Python -I运行安装包选择集 **1364 passed/77.00s**，显式确认
**169个Corki模块**均来自独立installed目录。选择集为前批1308+本批30+
builtin_tools既有9+code_mode_results既有17，包含全部本批变更和实际进程/Runtime
输出及关闭路径。测试直接读取源fixture不是混用源实现；不外推全部子进程都有
各自的安装来源断言。

Codex仍干净且HEAD保持ddf04ad26789d040f9ef6a96736f76602e35a6cc；teach.md SHA
保持816f506cecbc07360e4472a16c7600da3e4098ef6ff99c48f427273429a6681a；用户
暂存docs/flow.md不变，未提交。所有验证进程已终态。本批progress，goal继续active；
没有真实模型/provider或Rust验证，不宣称整体Harness完成。

下一步明确源码入口：`core/src/tools/code_mode/mod.rs` 的结果构造(303+)，再追踪
code-mode-runtime/cell_actor/service；Corki `code_mode/cell.py::observe` 仍调用
旧 `context/tool_output.py::truncate_content`，其预算估计/格式与新的原生截断共享
函数是否一致尚须源码映射验证。Shell的其余生命周期/审批/环境/stdin差异及完整
A/B/C/D/E开放范围仍保留，不能被本批输出格式与预算修复替代。

## Batch 75 — outer Code Mode output (pre-edit audit)

Pinned Codex core/src/tools/code_mode/{execute_handler,wait_handler}.rs routes
initial/live wait/termination responses to mod.rs::handle_runtime_response
(249–348). It appends `Script error:\n...` for failed Result, truncates body using
explicit/default 10000 Tokens, then prepends status+wall time. Pure text keeps
original blocks unless joined UTF-8 bytes exceed budget; then uses shared formatted
head/tail truncation (utils/output-truncation/src/lib.rs 14–92). Mixed content uses
the same ordered function-output policy as hosted/MCP, preserving opaque blocks.
FunctionToolOutput then enters normal history model-policy serialization; no fixed
40000-character tool-spec cap. core/tests/suite/code_mode.rs 1883+ separates nested
variable output from emitted text and later configured/default history truncation.

Corki Cell.observe calls old context/tool_output::truncate_content: heuristic token
estimation, different omission marker/Unicode behavior and repeated halving of
snippets. Error prefix uses space instead of newline. exec/wait specs impose an
extra fixed 40000-character legacy cap, despite configurable model/output budgets.
Also observe clamps explicit budget to buffer/4 independently of admitted output.
B06/C02 inconsistent P1: output may be over-truncated and metadata/markers differ.

Plan: dedicated Code Mode output projection reusing native byte/token and ordered
content functions; correct error prefix before body budget, header afterward;
remove unsourced per-spec 40k cap and secondary buffer-derived output budget clamp.
Keep physical cell/bridge/node limits, old persisted legacy caps, cancellation,
dispatch error semantics, incremental draining and lifecycle ownership unchanged.
RED→GREEN real QuickJS initial/wait/failed/terminated output and actual Runtime
large emitted output→model policy→cold ledger/history evidence required.
Not closing full V8 parity, host duration accounting/rounding, wait-missing-cell
semantics, non-WAV audio codecs, original-image policy, cell buffer limits or all
runtime lifecycle branches. This is not permission to weaken transport hard caps.
