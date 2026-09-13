# 流式行队列：源码差异及接入约束

## PTY 发现的接入缺口：外层预览容器仍只检查正文

新增实际 Application.run + Runtime 的四例 PTY（40/100 列 × Ctrl+C 或 /stop）
让模型发出计划表格后无限等待，必须先看到 alpha/beta 才发取消。初跑四例
都在预览处超时（91d67c），不是取消等待失败。TerminalUI 的尾部渲染函数已
支持计划，但 HSplit 中 ConditionalContainer 仍以 _table_source.tail 为开关，
纯计划没有正文 table source，所以整个窗口隐藏。此前函数级测试及非尾部
PTY 不足以证明真实接入。本批将容器可见性改为实际可见 fragments，模型
等待期间无需正文事件或完成事件也能显示计划；继续验证取消及历史清理。

修复后新四例全部通过（56d879，6.27 秒），取消前模型无完成事件；取消后
验证模型流 finally 执行、计划 controller/preview 清空、40/100 Transcript
无 alpha/PARTIAL、SQLite 无 AssistantMessage，退出后模型关闭且只采样一次。
联合 CLI 单测、计划失败、计划和正文表格 PTY 共 401 passed、12 forkpty
警告（9f837d，27.50 秒），全部静态通过（263b2f，1148 文件/77 包）。
所有测试进程已终止。上述 PTY 证明终端确实输出了预览以及取消后内部显示/
模型历史清理；未用屏幕模拟器逐格断言擦除，也未覆盖历史面板尾部同步，
不能据此宣称完整 F/A–F 已完成。

## 最新接入：计划 mutable tail 的临时显示

原生 streaming/controller.rs::StreamCore::current_tail_lines 从 enqueued 边界
而非 emitted 边界取尾部，避免重复排队行；chatwidget::sync_active_stream_tail
优先正文控制器，再计划控制器。仅完整源行能更新可见尾部，终态丢弃临时 cell。
Corki 已有 TableStreamSource 和 StreamPlan.tail_lines，但 TerminalUI 的实际
composer 尾部入口只查看正文，计划表格一直不可见直至完成。这是 F 的缺失。
接入计划专用临时渲染，不记录 Transcript；保持正文优先级、队列不被尾部
越过，首段显示计划标题，已有提交正文则不重复标题。以表格有无前缀、取消
清理及不进入历史为本批验收，复杂交错和物理尾部 PTY 仍需另验。

PlanStreamUI._plan_tail_fragments 已接 composer 实际入口；按完整 source 渲染
并从稳定前缀的渲染边界切片，独立缓存包含 controller/源/宽度/提交进度。
新完整源行及 end 均 invalidate 输入应用，纯尾部没有动画积压也能刷新。
非交互/禁用 live input 不启用预览，正文 controller 存在时优先正文。
四例测试覆盖 40/100 列、有无已提交前缀、半行不显示、队列不越过、标题不
重复、预览不写 Transcript、取消撤销。初始两例失败（291a85），接入后
CLI/Runtime/既有计划 PTY 联合 409 passed、4 forkpty 警告（04fdb1，16.77 秒），
静态通过（a6e76a）。物理尾部 PTY、实际历史面板尾部同步、多 item 交错和
复杂 Markdown 仍待验证，不能由上述结果推断整个 F 或 A–F 完成。

## 最新修复：活动历史重放越过计划提交队列

源码对照：原生 PlanStreamController::on_commit_tick/batch 从 core.tick 取行，
current_tail_display_lines 单独提供 mutable tail；chatwidget::sync_active_stream_tail
维护独立 transient cell。队列中的稳定行不因浏览历史而全部提交。
Corki HistoryView.text → Transcript.render 重放 append_proposed_plan_delta，
后者在 replaying 时 drain 全队列，导致尚未显示的稳定行出现在历史/resize
重绘中。影响 F 的显示顺序及 A 的流式边界，属于行为不一致。
修复应只在活动计划重绘时使用已提交行快照，保留原始 calls 和真实队列；
历史缓存还必须跟踪提交进度，不能仅依赖 append 调用列表。mutable tail
仍需独立实现，不将全部队列冒充 tail。验收为 0/1/2 行提交、两种宽度、
HistoryView 缓存更新且队列/原始记录不变。

新增测试在修复前失败（ed172c）：提交零行时 render 已含两行。现已接入
Transcript.render 的活动计划分支：仅该 run 使用 committed_rows fragment，
不重放其 begin/append/end，不改变真实 controller 或 calls；完整计划仍按
最终 source 重排。HistoryView 缓存增加 controller 身份和 emitted 进度，
无需等待模型再发 delta 才能显示新增提交行。两宽度及 0/1/2 行提交测试通过。
全部 CLI 单测、计划 Runtime/历史/失败矩阵和真实 PTY 联合 405 passed、
4 条 forkpty 警告（d6f55b，16.69 秒）；静态通过（33464b），无活动测试。
本批 PTY 覆盖既有计划流中可见及完成后 resize，不冒充历史面板中途切换的
物理输入测试。活动 fragment 保留提交时行布局；mutable tail、流中跨宽度
复杂 Markdown 重排及正文/工具交错仍未全部对齐，完整 A–F 保持开放。

## 最新验证：未完成计划的失败、取消与重试

重新核对原生 `tui/src/chatwidget/turn_runtime.rs::on_error → finalize_turn`：
错误路径清除预览并丢弃 plan controller，不把剩余计划草稿补成成功 item。
参考 commit 仍为 ddf04ad26789d040f9ef6a96736f76602e35a6cc，参考工作树干净。
Corki 的 Application 在终态/重试中断及 finally 中先 end_proposed_plan，
再结束动画；Runtime._run_graph 发出 TurnCancelled 后继续传播 CancelledError。

新增 test_proposed_plan_failures.py 六组合：失败/模型侧取消/一次重试，分别
发生于尚无已提交行和已有已提交行。真实 Runtime、SQLite、Application、
TerminalUI 联动；测试只暂停草稿的普通动画 tick，允许 finish drain，以便
抓出清理顺序错误造成的隐藏行补写。验证唯一终态、取消不被吞、模型迭代器
关闭、失败 step 无成功/partial item、重试请求不含未提交 AssistantMessage、
只有成功重试产生 PlanCompleted，以及 40/100 列重绘仅保留已提交 fragment。
模型原文历史不保存失败草稿；动画和 display-next-event 任务退出。

初跑失败来自夹具：零重试间隔违反配置约束，取消应断言抛出 CancelledError，
不能算生产代码红绿证据。修正夹具后相关联合 78 passed（1f9b2b，3.58 秒）；
静态检查通过（09e415，1147 文件/77 包）。原测试 handle 73532 已不存在，
本次新运行均取得终态；本批未修改生产代码。

这不是物理 Ctrl+C、消费端任务取消、真实终端背压或所有多 item 故障矩阵。
mutable tail、活动历史竞争、复杂交错及完整 A–F 仍开放。

## 最新实施：交互终端计划行队列已接入（完整矩阵仍开放）

StreamMarkdown 的行渲染成为可覆盖入口，StreamPlan 复用源缓存/队列/宽度边界，
独立持有计划正文行；标题及顶部 padding 跟第一批实际出队一起提交，不计入
正文积压行数。PlanStreamUI 接入 TerminalUI，完整源行经 TableStreamSource
分离稳定前缀，ProposedPlanDelta 实际驱动入队；完成事件排完并合并为原始
计划 source-backed cell。不是只新增离线策略模块。

TerminalUI 动画使用本次事件消费的稳定 owner 和共享 ChunkingPolicy：正文+
计划总深度、最大等待年龄决定压力；Smooth 每个现存控制器一行，CatchUp
按决策排队；出队复用已有 commit_delta 的提交/取消所有权。不因另一队列
出现而重置同一动画时钟。普通正文路径仍经原渲染器。

计划中断/错误/取消先撤销未提交队列，再清理正文和动画。为防止 Transcript
重绘重新执行旧 append 而显示隐藏队列，interrupted plan 转成仅包含已提交
行的 fragment；完成计划则仍保存原文，支持 resize。fragment 是显示历史，
不是成功 Plan item 或新增模型历史。正常计划完成不等于 Turn 成功。

管道不能擦除已写草稿，计划保持完成后输出一次；交互终端才流式展示。首轮
混合回归 f00a1a 的两个失败暴露了多计划块在管道重复标题，已通过该输出边界
修复，未放松既有“一次最终计划”的断言。原生 TUI 不等于非交互管道协议。

红绿及验证：新增真实 Runtime 两例修改前因计划从不提交而超时（d68a6f）；
现在模型等待期间 Smooth 多次单行、10 行突发 CatchUp 一次提交。单测验证
正文/计划各四行组合触发八行压力、owner 不变、只有已提交行能在中断后回放。
真实 PTY 新增 streamed 两例：模型发出文本后等待父进程输入，父进程必须先
看到计划末行才允许模型完成，随后测试 resize 和 composer 草稿保留。

CLI/core/parser/Runtime/history/新 PTY 联合 466 passed、0 skipped、4 条 forkpty
警告（29d4d0，20.50 秒）；额外既有表格及终态修复 PTY 18 passed（95135a，
25.92 秒）。静态 6a896d 全通过，1146 文件/77 包；所有测试进程已退出。

尚未完成：计划 mutable tail 的独立预览、活动历史视图与待提交队列竞争、
复杂正文/工具/计划交错及多 item、计划专门的故障/取消/重试/物理信号矩阵、
已知终端背景配色与复杂 Markdown 宽度。不能据上述通过声称完整 F 或 A–F 完成。

## 最新实施：完成计划独立渲染与物理 resize（流式队列仍开放）

以下为上一批状态，流式接入进展以上方章节为准。

完整读取原生 history_cell/plans.rs 及 plans_tests.rs，核对静态 PlanCell 与
PlanStreamController 的差别：静态正文使用 width-4、两列缩进，标题后有分隔
及顶部 padding，末尾有底部 padding；空正文显示 (empty)。原生计划背景依赖
default_bg，未知背景时为默认样式。Corki 当前尚无终端背景探测，本批采用这个
未知背景路径，不擅自硬编码深色底，也不声称已实现已知背景下的配色。

新增 cli/proposed_plan.py::ProposedPlanBlock，由 TerminalUI.show_proposed_plan
保存原始 source 到 Transcript。Application 的完成事件及历史回放共用该入口，
不再将计划标题拼进普通 AssistantBlock；空计划也不再被回放丢弃。正文按当前
宽度重新渲染，不把旧行宽快照当作永久内容。模型 wire/core 解析本批未变。

三例显示测试修改前缺少入口而失败（c79cad），新增 40/100 列明确文本快照、
空计划和 source 重排检查。真实 PTY 两方向 40→100/100→40：实际 Runtime
生成计划，等待完成后在 composer 输入 draft kept，再物理 resize，确认旧
计划被重绘，提交草稿未变且模型只采样一次，资源关闭后进程退出 0。此处验证
已完成计划，不冒充等待模型时的流式计划或中断计划 PTY。

最终 CLI/core/parser/Runtime/history/新 PTY 联合 444 passed、0 skipped、2 条
forkpty 多线程弃用警告，15.62 秒（071e2a）；进程终态另经 handle 确认。
全部静态通过（9a24a6，1144 文件/77 包）。原生独立 Plan 流式队列、总队列
策略、复杂表格/链接/代码/宽度、失败取消重试及完整 A–F 仍待处理。

## 最新实施：Runtime 计划解析与完成后展示（仍未完成独立流式显示）

protocol/proposed_plan.py 实现按行标签解析及最后计划块提取，core/plan_output.py
接入 ModelOutput，graph 用冻结的 _turn_settings 模式启用。不添加模型 wire
字段、不改原始 AssistantMessageItem 或 provider 请求；引用过滤后按 item
解析，计划完成状态只属于本次 sampling response。新增本地 ProposedPlanDelta
和 ProposedPlanCompleted 事件；空 delta 表示块开始，不把解析 End 当完成。
item.done/response.completed 由原 ModelOutput completed 集合去重。

CLI 暂以完成事件排入现有延后显示队列，正文完成后展示 Proposed Plan，纯
计划在完成事件展示；避免 TurnCompleted 的原始 final_answer 再回显标签。
历史回放已经消费 DisplayTurn.collaboration_mode，普通模式仍保留原文。
这只是完成后展示，不是原生独立 PlanStreamController：计划 delta 尚未
接显示队列，流中草稿不会显示，标题/背景/padding 及双队列策略仍需下一批。
暂不声称正文和计划交错时的所有顺序、同 response 多 item、多块修正屏幕
或异常终态已逐项对齐。新增事件在本地生成，不是 provider 原生 Plan 协议。

红绿证据：新增真实 Runtime mode × streamed 四例在修改前 Plan 两例失败、
Default 两例通过（da29bd）。扩为 mixed/only_plan/multiple_blocks 共十二例，
逐字符流和非流式完成、原文历史、一次完成、完成后 CLI 消费及 Default 当前
UI 下按保存模式回放均验证；CLI 消费使用实际 Runtime 事件的顺序重放，不
冒充真实 TTY 并发。解析单测三种分片覆盖围栏、Unicode 空白、额外文字、
孤立关闭、嵌套、残缺标签、无尾换行、未闭合块；保留匿名多 message delta
兼容性。最终 CLI/core/parser/Runtime/mode/history 联合 443 passed、0 skipped，
13.50 秒（8613ab）；全部静态通过（604968，1141 文件/77 包），无活动测试。

下一步仍需独立计划渲染/动画、历史分页实际 CLI 集成、40/100 列快照及 PTY，
补错误/取消/重试、多 item、引用与计划混合及模式所有权组合。完整 A–F 未完成。

## 后续明确缺口：proposed_plan 不是 update_plan 清单

以下为实现前审计；以顶部最新实施状态覆盖“尚无解析/事件”等历史描述。

全量基线等待期间进一步核对：prompts/modes/plan.md 明确要求最终计划使用独立行
`<proposed_plan>` 标签，以供客户端特殊渲染。Corki 当前协议/core/CLI 没有对应
proposed-plan 解析或 PlanDelta；Application 只把 PlanUpdated 交 TerminalUI.show_plan，
后者展示的是 update_plan 工具的 Updated Plan 清单，不能替代最终 Proposed Plan。
普通 assistant delta 仍经单一 StreamMarkdown/正文路径，不能据已有 Plan 清单测试
判定最终计划的独立展示已完成。

原生路径：utils/stream-parser/src/proposed_plan.rs::ProposedPlanParser 包装
TaggedLineParser；core/src/session/turn.rs::handle_plan_segments 区分 Normal 与
ProposedPlanStart/Delta/End，派发 PlanDelta；TUI PlanStreamController 独立持有
StreamCore，输出 Proposed Plan 标题、缩进、背景及首尾 padding。commit_tick 汇总
answer/plan 两队列的深度和最大等待年龄，不能简单给所有 Plan mode 正文统一换皮。

状态：F1/A3 部分缺失，待实现；下一步接入真实事件/显示/历史路径，补 40/100 列
快照和 PTY。以下解析规则已完整读取源码确认，不代表已实现特殊渲染。

### 最终计划解析及完成边界（基线等待期间补审）

参考 commit 再次确认 ddf04ad26789d040f9ef6a96736f76602e35a6cc，参考仓库
无未提交变更。完整读取 codex-rs/utils/stream-parser/src 下 proposed_plan.rs、
tagged_line_parser.rs、assistant_text.rs（含各自测试），并追踪 core/session/turn
中的按 item 解析、计划生命周期、完成消息提取及原始 response item 记录路径。

- 仅 ModeKind::Plan 开启计划解析，不按 provider 或地址启用。先剥离已有记忆
  引用，再解析计划；不是服务端 PlanDelta 协议，也不是原生 namespace。
- 标签必须独占一行，可带首尾空白。流中先缓存仍可能是标签的行前缀，一旦
  排除标签可能就立即输出，不等待整行；额外非空白文字让该行成为普通内容。
- 无代码围栏状态：围栏内独占行标签仍会被识别。不得新增 fence-aware 行为
  后声称与原生一致。嵌套开始标签作为计划正文；没有活动计划时的关闭标签
  作为普通正文；已活动计划只被对应关闭标签结束。
- finish 会判定无末尾换行的完整标签，将残缺标签作为正文，并为未闭合计划
  输出 End。这个 End 只是解析段结束，不能据此将失败的模型响应判成成功。
- 每个 assistant item 有独立 parser；整个 sampling response 共用一个计划
  item 状态。纯计划/前导空白不会制造空 assistant 开始事件。End 不完成计划；
  completed assistant message 的完整正文重新提取计划，再发完成事件。
- 完整提取遇到下一块 Start 会清空先前计划文本，因此使用最后一个计划块，
  不是把多个块简单拼接。单 response 的计划状态一旦 completed，后续计划
  delta 不再发布。完成事件正文与流中草稿可能不同；TUI 是否实际用最终正文
  修正显示不能由此推出，具体限制见下面的显示完成补审。
- 原始 response item 仍走 record_completed_response_item_with_finalized_facts
  进入历史；显示层拆出计划不等于删除模型历史中的原文。

Corki 接点再次实读确认：core/output.py::ModelOutput 目前仅做引用过滤；
graph.py 在 item.done 和最终 completed.items 两个入口调用 complete，靠 item
身份去重，输出对象每次 model step 新建。必须在这里考虑按 item 解析及最终
补全，而不是只在 TerminalUI 用正则替换标签。Application 的去重、重试中断、
显示队列及 core 输出事件需一起接入；cli/history.py 对 AssistantMessageItem
直接 show_assistant_message，冷回放也必须使用历史时的模式语义，不能用当前
CLI 模式解释旧消息。

### 模式所有权与历史入口补审

实施更新：DisplayTurn 已追加带 default 默认值的 collaboration_mode，并验证
模式。完整快照/分页共用 display_turn_from_row，以 ModelSettingsSnapshot
验证现有 JSON 后提取模式；无快照使用 default，无新数据库列或模型字段。
三位置参数兼容保留。并发快照测试更新 SELECT 识别条件，仍验证读事务不混入
并发写入。新两例已通过；联合 216 passed（9a8039），静态 bb04d8 通过。
此处只打通模式的历史读取，不声称 CLI 已消费该字段或最终计划功能已完成。

原生 core/session/turn.rs 使用 turn_context.mode()，而 turn_context.rs::mode
明确返回 initial_settings 中冻结的入场模式，不是当前 Step settings。因此
Corki 接入 ModelOutput 时应读取 _turn_settings.collaboration_mode；不能因为
_step_node 会按 step_model_settings 构建 view，就误用可更新的 _settings 模式。
现有 _step_node 已特意把 view._turn_settings 保留为入场设置，能够复用该边界。

历史来源已经存在：TurnRecord.model_settings / turns.model_settings_json 保存
ModelSettingsSnapshot，其中包含 collaboration_mode；旧快照缺字段时验证器
默认 default。现有 checkpoint 恢复测试也覆盖保存 Plan、宿主改 Default 后仍
恢复原入场模式，但尚未验证最终计划显示。

具体断点是 DisplayTurn 只有 id/status/error：storage/sqlite.py 的完整显示
快照，以及 storage/display_pages.py 的分页查询都只 SELECT 这三个字段。
CLI HistoryPager 虽补齐每条 item 对应的 Turn，却没有可传给回放的模式。
修复应同时扩展这两个显示读取入口，复用已保存且已验证的 Turn 快照，保持
原始 conversation item 和模型请求不变；不能只改完整快照而漏掉实际 CLI 分页。
旧会话无快照时用明确兼容默认，不从标签存在或当前 CLI 模式猜测历史模式。
新增显示字段须保留现有 DisplayTurn 三位置参数兼容。

这消除了为显示额外增加数据库模式列的必要，但仍需真实 Runtime 跨 Turn
模式切换、恢复和分页回放测试，不能把现有恢复提示词测试当成显示验证。

新增 test_proposed_plan_history_mode.py 的 snapshot/pages 两例已复现模式
丢失（fe010a，2 failed，0.88 秒）。真实 Runtime 执行 Plan/Default/Plan，
每次检查数据库入场快照正确；加入无快照旧 Turn、将当前模式改为 Default，
关闭并冷启动。显示读取不采样、不改变原始 item，最终返回模式却全部缺失。
这些是待修复红测试，不是已有能力验收；尚不覆盖终端渲染。初轮 c3e935
误用了不存在的 load_turn 方法，属于测试入口错误，不作为缺陷证据。
新增文件未被已启动的基线 session 1908 收集；不能将该基线结果称为包含这
两例的当前全量结果。上述实施更新在基线退出后完成，红测试现已转绿。

### 显示完成补审：不能把原生 TODO 当作已完成能力

进一步读取 tui/chatwidget/streaming.rs::on_plan_item_completed：非空完成文本
作为 copy source/latest proposed plan；空白完成文本回退到已缓存 delta。
controller.finalize 返回 streamed cell 的分支却用 controller 的源文本发送
ConsolidateProposedPlan，并明确保留最终正文/流式内容不一致时修正的 TODO。
没有该 cell 且最终文本非空时，才直接新增最终 plan cell。故“原生所有分支
都会按完成事件修正最终计划显示”不成立；上文多块规则是 core 事件契约，
不能据此声称原生 TUI 对多块草稿的屏幕替换也已完备。

app/event_dispatch.rs 的 ConsolidateProposedPlan 查找尾部连续的
ProposedPlanStreamCell 并替换，同时更新历史 overlay；不是把屏幕上的任意
计划文本全局替换。Corki 复用 transcript 修正机制时必须隔离正文、工具、
计划三者的显示身份，尤其不能改掉流中插入的工具记录。

turn_runtime.rs 在正常任务完成时仍 finalize 未结束的 plan controller，
在下个任务开始时丢弃残留 controller 并重置自适应队列策略。计划 item 完成
与 Turn 完成是两个边界：前者恢复运行状态栏，不停止仍运行的 Turn。原生
plan_mode 测试明确覆盖这一点，以及无换行增量不重绘未改变的 tail。
后续 Corki 测试需分别覆盖正常完成、重试/取消的清理和新 Turn 隔离；不能
把 finalize 的显示动作当成成功终态，也不能用普通正文完成测试代替它们。

错误路径另有明确差别：on_error 先 flush_answer_stream_with_separator，再调用
finalize_turn；后者清除 preview tail、重置队列策略并直接丢弃 answer/plan
controller，不调用计划完成事件。不能把普通正文的失败 flush 规则不加区分地
套到计划队列上。已经提交的显示与尚未提交的草稿尾部必须分开处理。

验收需包含拆分标签、普通模式原样保留、空白/额外文字/围栏/嵌套/未闭合块、
多块最终修正、item.done 与 response.completed 去重、断流重试、冷回放与
模式切换、引用和计划混合，以及正文/计划两队列的顺序和关闭。当前未修改
生产代码或测试；整体基线运行期间不改其已加载代码。

## 当前实施：主路径动画已接入，完整时序矩阵仍开放

Application._render_events 在 TerminalUI 的 TTY/live 路径启用 animated_events。
该异步迭代器由显示消费者持有，最多一个 anext 任务；等待模型事件时仍可按
8.333334ms deadline 提交行。连续 delta 不重置已有队列的 deadline；一次过期
提交后以当前时间安排下次，不补发一串旧 tick。无队列时不使用定时等待。

TerminalUI 只把完整稳定源入队；普通 tick 出一行，delta 入队后执行
CatchUpOnly。ChunkingPolicy 接入原生阈值、250ms 退出迟滞/重入冷却及 severe
绕过条件。实际输出 drain 继续经过 stream_commit 的 capture/write/flush，
不在空入队阶段暂停/恢复 composer。尾部暂不显示直到旧稳定队列排空，以保持
已验收的前缀在表格之前的显示顺序；未将排队行重复加入 mutable tail。

正常完成先立即排完稳定队列再作 authoritative transcript 修复；中断、失败、
取消和问题面板切换通过 _end_assistant_stream 排队列后结束显示。外层 finally
关闭事件包装器、取消并 join 未完成读取，再收回显示状态。无游离动画写入任务。
历史回放仍同步使用 StreamMarkdown.write，不启动该事件包装器。

验证和修正：初轮 044259 显示入队阶段多余 redraw 抢先触发双故障，已改为只
为实际出队包裹 in_terminal；PTY ef2968 暴露尾部先于前缀，已加顺序约束。
另两项旧 fixture 以 DELTA_RENDERED 标记当作实际屏幕提交，现保持模型阻塞，
明确等待 outer 真实出现后才允许最终完成，不用最终 repair 替代流中证据。

真实 Runtime 新测试分别验证：模型已处于等待时多个 tick 各出一行；10 行
突发在 CatchUpOnly 一次提交；关闭后无 corki-display-next-event 任务。策略
测试覆盖进入阈值、连续低压退出及严重积压绕过冷却。CLI/集成/流式 PTY 联合
402 passed、20 条 forkpty 警告（9a0a57，35.79 秒，早于新增 burst 参数）；
新增 burst 后专项 17 passed（50f9c2），全部静态通过（c254ad，1129 文件/77
包）。所有进程退出 0，无活动测试。

当前状态为部分一致，而非完成：活动历史视图与队列竞争、复杂 resize backlog、
独立 Plan 视觉控制器和完整动画/审批
矩阵尚待核验。原生动画主路径不再属于完全缺失，但完整 F/A–F 未完成。

### 精确定时补证（2026-09-11）

重新读取原生 app/tests/stream_animation_tests.rs 及 event_dispatch.rs 的
Start/Stop 分支，参考 commit 仍为 ddf04ad26789d040f9ef6a96736f76602e35a6cc，
参考仓库无未提交变更。新增 test_event_consumer_deadlines_survive_stalls_and_discard_old_owners
直接驱动 Application 使用的 animated_events，不另写一个用于测试的调度器。
仅替换该模块的 monotonic 和 wait；事件任务本身仍由真实 asyncio 执行。

三个参数验证同一 owner 连续事件、过期后替换 owner、停止后重新启动：第二次
事件等待仅余半个 tick；消费者阻塞五个 tick 后，同一流只提交一次过期 tick，
下一次间隔完整 tick；替换/重启先等待完整新 tick，停止阶段没有定时等待。
EOF 后无残留事件读取任务。此处验证调度器，不冒充完整终端/历史焦点验证。

初次测试失败是误用未安装的 pytest-asyncio 标记，未执行测试主体；现已改成
项目现有 asyncio.run 入口，不作为生产缺陷先红后绿证据。新增三例加既有策略
及真实 Runtime 清理测试共 20 项通过（eecbd9）；本轮未修改生产定时逻辑。

扩大回归先启动的进程仍收集到修正前测试入口，最终仅这三例失败（b668ec），
五个 PTY 文件的 62 个场景均通过，无跳过，另有 62 条 forkpty 警告。随后
修正入口的完整 CLI 单元与 Runtime 流清理补跑 386 passed、0 skipped，9.12 秒
（f4baf0）。不能把这两次运行描述为一次 448 项全绿。PTY 包括表格 4、终态
修复 14、Plan 8、物理取消 4、停止后输入恢复 32，覆盖 40/100 列；本轮没有
修改这些 PTY 文件或生产代码。全部静态检查通过（4ea3d5）；进程均已终止。

## 当前实施：稳定行入队/出队已接原输出入口

StreamMarkdown.write 已改为 enqueue → drain，仍立即排空以维持现有 UI 时序。
队列持有不可变 Segment 行及单调时钟入队时间，已排队边界由 emitted + queue
length 计算；重复源快照不重复入队、不重置已有队首年龄。drain 按队首取指定
行数，一旦交付终端便不回队自动重试，避免写入结果未知时重复副作用。

resize 有 backlog 时重建队列，压缩到已显示行数以内仍保留至少一行供后续显示；
无 backlog 保留既有源快照重排计数。此规则和原生一样依赖终态规范渲染修正
结构变化，不承诺跨宽度的所有中间屏幕都无重复。tail 仍使用完整解析上下文的
稳定源边界，尚未启用独立定时显示或改写回放所有权。

三项新增队列测试修复前缺少入口（62b488），实现后通过；整组 CLI 单元及
真实 Runtime 流清理 379 passed、0 skipped（7dd269，8.73 秒）。这不是孤立
策略示例，现有 TerminalUI 的 StreamMarkdown.write 已实际使用队列；但不能
把该结构接入称为动画功能完成，定时任务、CatchUp 策略与异步终态 join 仍待接。

额外流式表格/交错工具/终态修复 PTY 20 passed、0 skipped（ccc138，28.25
秒）。所有测试进程已退出 0，静态检查通过（fc40a6，1127 文件/77 包），无
活动测试。完整 A–F 保持未完成。

状态：缺失，F1/F6；本次审计不视作实现完成。范围为本地 CLI 输出，不涉及官方
服务、模型传输协议或原生 namespace。既有 stream_commit 只负责提交所有权。

## 原生真实路径

以参考仓库 codex-rs/tui/src 为路径前缀：

- chatwidget/streaming.rs 的 answer/plan delta：controller.push 返回有新稳定行
  时发送 StartCommitAnimation，同时运行 CatchUpOnly 提交；有完整源行才更新尾部。
- streaming/controller.rs::push_delta：完整源行 → holdback → render.append →
  sync_stable_queue。队列单位是渲染行，不是模型 delta 或原文换行。
- app/event_dispatch.rs 的 StartCommitAnimation 只在无定时器时创建 interval；
  首次截止时间为 now + tick，重复 start 不延期。MissedTickBehavior::Delay
  禁止积压的旧 tick 一起补发。Stop 删除定时器，替换 widget 也停止旧定时器。
- app.rs → tui.rs → tui/frame_rate_limiter.rs：tick 为 8,333,334 纳秒。
- chatwidget.on_commit_tick → streaming/commit_tick.rs::run_commit_tick：汇总
  answer/plan 队列深度与最大队首年龄 → chunking policy → 按队首取行 → 插入
  history cell → 同步尾部；两队列空时停止动画，必要时恢复 working 状态。
- flush_answer_stream：take controller、清活动尾部、finalize 完整原文、合并
  authoritative completed message、reset policy，再按其他 controller 是否空停止。
  完成路径不等待逐行动画排完；不能把已完成内容留给失去所有者的后台任务。

## 确认的策略参数

chunking.rs 的策略不分 provider 或吞吐来源。Smooth 每 tick 一行；CatchUp
在当前 tick 排空 backlog。进入条件为深度 >= 8 或队首年龄 >= 120ms；退出
要求深度 <= 2 且年龄 <= 40ms 连续保持 250ms。退出后的 250ms 抑制重新进入，
除非深度 >= 64 或年龄 >= 300ms。队列空立即恢复 Smooth，仍记录 CatchUp
退出时间；reset 才清掉全部迟滞状态。CatchUpOnly 也更新策略，但 Smooth 不出行。

对应原生测试 app/tests/stream_animation_tests.rs 使用暂停时钟验证重复 start、
阻塞后仅一次 overdue tick、stop/restart 只保留新 deadline、widget replacement
取消旧定时器。不能仅以 sleep 后总输出相同证明这些时序。

## Corki 当前状态与影响

TerminalUI.append_assistant_delta_live → commit_delta → append_assistant_delta →
StreamMarkdown.write：完整源渲染后将 remaining 全部立即 print。没有稳定行队列、
入队时间、策略迟滞或独立 tick。已有渲染缓存和串行 flush 不关闭该差异。

StreamMarkdown.emitted 只记已输出行数；table.emitted 是原文边界，两者不能
共用。原生 current_tail_lines 从 enqueued_stable_len 开始而非 emitted：
排队未显示的行不能再出现在尾部。直接在每 delta 后 sleep 会阻塞事件消费、
无法测量真实 backlog，且把字符批次误当行批次，因此不是对齐方案。

Transcript.render 会同步保存/恢复活动 StreamMarkdown，再把全部调用回放到
临时 console。若动画从同步 append 自动启动，历史回放也可能创建定时任务并
污染真正活动流。必须限定动画在活跃输出入口，回放/pipe 保持同步完整渲染。

## 实现所需边界及验收

1. 将稳定渲染行准备与输出拆开；分别记录 enqueued/emitted 和队首入队时间，
   不复制原生原文 byte offsets 为 Python 字符索引。排队时保留不可变行快照。
2. 将定时/策略接到实际 TerminalUI 活跃流；单一任务拥有生命周期。每 tick
   复用现有串行终端提交，不通过异步 StdoutProxy 留下与尾部竞争的输出。
3. delta 到达可触发 CatchUpOnly，但不重置已有正常 tick。UI 忙时使用延后
   deadline，不用连续零等待把过期 tick 全补发。
4. 完成、失败、取消、切换历史、关闭和源替换必须处理旧队列及任务；不得重放
   结果未知的终端写入。规范化最终正文仍由现有 Transcript 完成。
5. resize 必须按渲染行重建未显示部分。原生 set_width 保留至少一个待显示行，
   避免宽度增大使未显示行数缩小后内容永久丢失；无 backlog/tail 时不重放旧文。
6. 真正模型消费不得因 Smooth 节奏逐行等待。通过持有模型未完成的 Runtime
   测试及 40/100 列 PTY 证明慢速单行、突发追赶、尾部/表格不重复、工具事件
   FIFO、取消/关闭任务终态及最终文本相同。策略和时钟边界用确定性时钟测试。

本轮完整追踪以上原生生产分支及对应 Corki 接点，未新增孤立策略模块或修改
生产行为。下一实现应围绕这些所有权边界推进，不能以新增枚举或策略单测结案。
