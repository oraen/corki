# A4/A7/E7：Interrupt 生命周期缺失

## 有效取消意图与损坏计划交叉验证

test_interrupt_plan_integrity.py增加独立取消意图有/无参数，覆盖合法对照及四种
损坏计划，共10例。有效v1 interrupted意图存在时，损坏计划不会将取消改成失败：
公开resume只产生一次TurnCancelled，数据库为CANCELLED，意图及旧计划保持原样，
不采样、不执行命令，日志保留Interrupt hook cleanup failed和具体校验错误。
无独立意图的损坏计划仍明确TurnFailed；再次resume为空。

七文件联合8worker、禁用worker自动重启，106通过/4.84秒/退出0（cc34a5）；
修改文件Ruff与格式、git diff --check通过（b6807f）。本批只增加测试，无生产修改。
测试使用独立tmp_path数据库/home及存储API注入；当前配置无Hook，不将未执行结论
扩张为“所有授权配置已验证”。未覆盖并发关闭、MCP内部模板及执行结果损坏，
也不把旧14913通过视为最近生产修复后的全量结果。CLI对齐仍暂停。

## 旧Interrupt计划结构损坏的冷恢复修复

新增test_interrupt_plan_integrity.py：关闭展示marker、不写新取消意图，通过存储API
构造RUNNING及旧Interrupt计划后冷开公开resume。缺cwd、model为list、空命令对象、
重复command key四反例均误报TurnCancelled；空命令直到收尾才KeyError。
8worker首轮4失败/1合法对照通过（b5c034），不是并发时序失败。

参考hooks/events/interrupt.rs::InterruptRequest与schema.rs::InterruptCommandInput
的明确字段类型（09dee9），Corki新增restore_plan，在旧计划取消识别以及执行恢复前
共用：检查v1归属、payload字段集合/类型、environment成对字符串、命令基础类型/
1–3秒超时及唯一key。原当前授权匹配、unknown不重做、新取消意图优先级保持不变。
原始计划不修改，损坏旧证据明确TurnFailed，无模型请求或新执行记录；合法旧计划
仍取消且无配置时不执行。此校验不恢复官方metadata字段。

五文件79通过（e4571d，进程最终退出0为e1317a），扩大命令/MCP七文件101通过、
5.59秒（de98b4），均8worker、禁用自动worker重启。全src/tests Ruff、1255文件
format及diff检查通过（67f4dc）。未重新执行完整核心回归。

未验范围：新取消意图已存在但Interrupt计划损坏时的诊断/关闭，以及MCP内部模板
完整结构、执行结果损坏等。新测试通过存储API注入记录，不冒充真实OS强杀过程，
也不据此宣称完整A7/E7验收完成。

## 下一验证点：计划归属与完整数据有效性分离

完整回归96933运行期间只读复核：validate_plan_identity仅检查version、session/turn/
event及非空commands列表；has_durable_interrupt调用该函数后即认可取消证据。
run之后再用restore_command重建dataclass，当前授权匹配保护未执行命令，SQLite
claim拒绝重复unknown。因此不能仅由不完整校验推断任意命令可执行或会重复执行。
但合法归属下的缺失/非法payload字段、命令字段和重复key尚缺公开恢复反例；需在
本批回归结束后分别验证取消识别、执行准入和诊断/终态，不将dataclass类型注解
视作运行时校验。源码依据interrupt_hooks.py、stop_hooks.py::restore_command、
sqlite.py::_claim_hook_execution（d49ce9/9abc2c）；本段是待验证边界，不是已证实漏洞。

## 终态成功、意图失败时的并发关闭

test_interrupt_shutdown.py新增普通/realtime×意图提交前/后报错四场景，无Hook且
关闭展示marker。两个并发aclose均收到同一原始OSError，Turn只发一次取消终态，
模型关闭一次，重复close保留错误但不重关模型。冷开同Thread验证CANCELLED已落盘、
意图有无与实际提交一致、resume为空且无重新采样，冷Runtime正常释放。
该文件6通过（9596b2，1.18秒），仅新增测试，生产无需修改。
这是主动关闭期间的真实存储故障，不是将普通用户取消误报为清理失败。
不覆盖已消费完Turn之后关闭的错误保留语义，也不关闭全部E7。

## 取消意图写入前后故障：真实取消与冷恢复

test_interrupt_recovery.py新增12例：取消意图提交后报错（有/无Hook），以及
提交前报错（有Hook），各交叉展示marker开关与Interrupt结果提交前/后失败。
所有场景同时注入取消终态保存失败，明确检查warm仍为RUNNING、意图实际有无落盘、
错误日志保留；关闭后移除Hook配置冷开，唯一取消终态、模型只采样一次、Hook不重做、
原执行账本完整保留，重复resume为空。与记录损坏恢复联合34通过（65a493，4.57秒）。
随后与Interrupt命令/关闭四文件联合48通过（ddf574，6.00秒），修改文件Ruff检查、
格式检查及git diff --check通过（c62f6d）。未重新执行完整核心回归。
本批仅补测试，未改生产行为。Codex tasks/mod.rs取消路径的marker flush失败只警告并
继续Interrupt及TurnAborted；Corki对应runtime.py保留持久错误并继续取消收尾。
该证据覆盖已有持久取消意图或Interrupt计划的恢复，不覆盖所有取消证据都未落盘、
终态成功而意图失败、并发aclose错误传播以及Interrupt完整payload/commands校验。
这些边界与其它A–E开放项仍需分别处理，不能将本次通过作为整体验收。

## 持久取消记录的合法/损坏冷恢复边界

新增test_cancellation_intent_recovery.py，通过存储API构造RUNNING及取消记录，
关闭warm Runtime后由cold Runtime公开resume_pending恢复。三种合法原因
None/interrupted/replaced均取消且模型零请求；bool/未知版本、缺reason、非法reason、
额外字段及取消记录夹带执行claim均明确TurnFailed，不继续采样、不改写证据。
检查唯一终态、数据库状态、原计划/执行记录完全保留、重复resume为空。关闭展示marker，
无Interrupt配置，验证恢复控制不依赖展示或Hook存在。
专项10通过（625f3b），联合真实中断恢复/命令/关闭四文件36通过（2eb86a，4.57秒）。
生产实现无需修改。该测试为持久记录注入，不冒充真实崩溃或损坏写入的发生过程；
不覆盖取消意图写入本身失败及Interrupt完整payload/commands校验。其它A–E仍开放。

## 旧无marker会话兼容修复

为上一批新增取消意图之前的格式补验：真实中断/终态提交失败/Hook结果提交前后
故障，但拦截新意图写入以保留旧格式，不删除数据库数据。12场景中旧格式且marker
关闭的两例误续跑（913b94，2失败10通过）。旧Interrupt计划由取消收尾独占写入，
其存在可以提供此前未读取的取消证据，不需要根据当前配置或模型输出猜测。

恢复入口现在在没有新取消记录时检查当前Turn的interrupt_hook计划，并验证v1整数
版本、session/turn/event身份及非空commands列表，随后进入原中断收尾路径。
同一身份校验复用于执行恢复；后续仍按执行账本验证request、保留unknown/已提交结果、
核对未执行项的当前授权。旧展示marker路径继续保留。此处身份校验不等于已完成全部
command/payload损坏验收；该部分仍需继续，不宣称仅凭计划名就可执行任意历史命令。

修复后冷恢复12例与中断/旧历史/恢复并发四文件 **73 passed / 20.98s**（c292de）。
新增八例身份损坏拒绝测试，覆盖bool/未知版本、空/非法命令列表、payload缺失、
foreign session/turn及错误event。已知/未知Hook均不重复、模型仅初始采样、第二次
resume为空、新取消记录得到保存。测试使用受控Hook执行器，不冒充真实OS强杀。
解析/身份专项 **34 passed / 0.27s**（e6b3f1），修改范围Ruff/format/diff通过。

彻底没有新意图、旧marker或Interrupt计划的旧RUNNING记录，无法据现有数据区分
“进程意外退出”与“取消的所有写入均失败”；不将这种不可区分状态自动判为取消，
也不声称能够还原未持久化事实。新记录及旧有证据路径的边界已说明，CLI仍暂停。

## 当前修复：取消意图不能依赖展示开关（A1/A7/E7）

真实公开中断后注入终态save_turn失败，并在Interrupt结果提交前/后注入失败：
关闭Runtime、移除Hook配置、重新打开同Thread调用resume_pending。四个初始反例
中两例失败（fb9b8b）：关闭agent_interrupt_message_enabled时没有TurnAbortedItem，
恢复继续模型采样并返回TurnCompleted；开启展示marker的两个对照正常取消。
这不是仅Hook问题，而是把可选历史文字当作唯一恢复控制意图的主循环缺口。

Runtime现在对TurnCancelled在终态保存前独立保存`turn_cancellation:{turn}` v1计划，
字段version/reason，使用已有持久批次存储，不添加模型可见消息，不要求Hook配置。
冷恢复在执行checkpoint/模型之前严格校验该记录、恢复原取消原因并进入取消收尾。
旧会话没有此记录时仍保留TurnAbortedItem的原兼容路径；没有记录且没有旧marker的
旧版本半提交会话仍不能一概判定已恢复正确，需要继续检查已有Interrupt计划的兼容
线索。任何时候都不把unknown执行记录改成成功。

新增`test_interrupt_recovery.py`：模型真实阻塞/公开取消，Hook执行器受控记录效果，
SQLite结果提交前后故障，旧Turn保持RUNNING后冷开；检查唯一初始采样、取消终态、
Hook仅一次、原执行事实不变、再次resume为空。扩展无Hook配置×marker开关×结果
窗口共8例（无Hook时结果窗口为阴性对照）。不冒充真实脚本或OS强杀测试。
初始四例及关闭/中断/checkpoint相关五文件 **72 passed / 7.79s**（5ba60a）；
扩展八例与中断历史/终端保留/恢复并发四文件 **66 passed / 25.41s**（db741b）。
修改范围Ruff/format/diff通过（607097/dbd095）。
仍开放：意图提交自身故障/损坏、旧无marker半提交迁移、替换原因交错以及全量核心
回归；本项不代表所有A–E已验收。CLI仍暂停。

## 范围纠正：不得复制专属 metadata 或官方插件信任例外

重新完整追踪了先前笼统列为待办的“最后Step来源/metadata”，结论如下，覆盖下方
将其整体列为必补缺口的表述；不是将通用插件或MCP支持排除。

- `core/src/hook_runtime.rs::build_request_metadata`从最后Step选择模型/effort等，
  但生成的唯一键是`X_CODEX_TURN_METADATA_HEADER`；`core/src/client.rs`定义其值为
  `x-codex-turn-metadata`。这是用户明确排除的专属metadata，不应补入普通MCP的_meta
  或HTTP请求头。Corki保留普通调用身份threadId，不以省略该官方字段算功能缺失。
- `run_turn_interrupt_hooks`的普通payload字段`model`另取`turn_context.model_info()`；
  `session/turn_context.rs`明确该方法返回冻结的initial_settings模型。不能为了所谓
  “最后Step对齐”把普通payload模型改为Step切换后的模型。Corki当前传入_turn_settings，
  与Step图的_settings分开；动态切换时的公开行为仍可补专项测试，未直接据源码宣布全验。
- `core-plugins/src/executor_hooks.rs::executor_plugin_hook_sources`读取executor插件
  manifests后，并不接受任意远端插件：最终经过临时未签名allowlist过滤。
  `plugin/src/bundled_hooks.rs`完整清单只匹配指定browser/chrome/computer-use等
  `@openai-bundled`身份及node_repl/cua_repl.turn_ended，另有
  `browser@openai-curated-remote`到codex_apps/connector_openai_browser的Stop路径。
  此官方插件产品例外和Apps路由无需复制，不能为本阶段完成重新引入官方连接器。
  通用用户可信配置/本地插件发现、MCP执行及来源变动后的授权校验仍在A/B/E范围内，
  不因为官方allowlist被排除就免除这些验收。

加强`test_interrupt_mcp.py`：所有实际HTTP客户端请求拒绝任何自动x-codex-*头，
tools/call的_meta仅允许threadId与客户端标准进度字段progressToken，不允许夹带专属
turn metadata。首次断言只允许threadId，八例因合法progressToken失败（2e06be），
已按通用MCP进度机制修正测试，不把标准进度字段误删为官方扩展。沿用十个离线
中断/关闭场景；本批没有添加生产协议或特权插件支持。下一步优先持久取消/Hook
计划提交交错与冷恢复，而不是实现以上排除项。CLI继续暂停。
修正后 **10 passed / 3.75s**（4ba7a8），Ruff/format/diff通过（0b2be1）。

## 当前补验：实际 HTTP MCP 中断调用

复核`hooks/src/engine/discovery.rs`的Interrupt MCP支持/限时测试与
`events/interrupt.rs::parse_completed`诊断语义；Corki对应
`interrupt_hooks.run → mcp_tool_hooks.run → MCPManager.call_hook → HttpMCPClient.request`。
调用要求已就绪连接/当前工具许可，租约在finally释放；超时使用客户端request_timeout，
isError转为Hook错误，非控制输出由Interrupt专用解析器判断。

新增`tests/integration/test_interrupt_mcp.py`：真实Runtime阻塞模型后调用公开cancel或
close，实际HttpMCPClient配httpx.MockTransport，仅允许fixture.invalid地址。
两种退出×warning/非法continue输出/isError/悬挂响应超时/untrusted共10场景，
检查MCP模板展开的event/原turn/session、threadId metadata、恰好一次tools/call、
唯一TurnCancelled、仅一次模型请求、Hook诊断状态及最终客户端关闭。超时的真实
AsyncByteStream必须在取消终态交付前aclose；未信任项无远程调用或Hook完成通知。
配置matcher=ignored证明Interrupt不按该值过滤。

专项 **10 passed / 3.70s**（dd2df3）；与命令中断、关闭、解析/指纹及MCP模板五文件
**60 passed / 5.08s**（b7fcea）；Ruff/format/diff通过（29543b）。仅新增测试，未修改
生产。这里“实际HTTP客户端”不表示真实联网服务或真实模型选工具；整个验证离线。
基本MCP正常/错误/超时路径不再列为完全未验；来源变化、冷恢复、最后Step元数据、
异步持久反馈等仍开放，不能视为完整A4/A7/E7通过。CLI对齐继续暂停。

## 当前关闭准入修复（A4/E7）

Codex基准仍为干净`ddf04ad26789d040f9ef6a96736f76602e35a6cc`。
`session/handlers.rs::shutdown_session_runtime`先`abort_all_tasks(Interrupted)`，
包含活动Turn的Interrupt分发，再`hooks().shutdown()`。Corki新增中断分发后，
`aclose`仍提前对共享Stop/start/Interrupt异步owner调用`close_admission`，导致
中断刚claim便无法调度，CancelledError成为cleanup_error并让正常close报错。

新增`tests/integration/test_interrupt_shutdown.py`普通/realtime两个公开关闭场景，
实际阻塞模型后调用aclose。修复前两例失败（c9e19e），日志明确为Interrupt hook
cleanup failed，异常从aclose返回。现移除这一owner的提前封闭：Runtime本身立即
closed并关闭输入，禁止新Turn；活动Turn完成中断处理后，现有资源关闭循环中的
owner.aclose负责封闭、取消及join。不修改其它owner或重新开放用户任务准入。

测试在真实已调度任务存在时暂缓owner清理，等待Python脚本写PID，然后仍调用真实
owner.aclose。验证脚本启动后PID被回收、唯一TurnCancelled、无遗留任务、重复关闭
不重跑。这个受控调度证明已启动进程的回收，不声称正常退出必须让异步脚本启动或
完成；未启动即被关闭回收也是异步语义允许的情况。
关闭新两例、Interrupt、Runtime shutdown、async Stop、SessionEnd、checkpoint六文件
**101 passed / 20.87s**（a68146）；修改范围Ruff/format/diff通过（a11db7）。
仍需最后Step来源、真实MCP中断、持久提交与冷恢复等验收；不关闭整个A4/A7/E7。
CLI对齐继续暂停。

## 当前实施进展（覆盖下面“尚未接入”）

`core/interrupt_hooks.py`已接入Runtime取消收尾：CodeMode/Step资源清理后，以原Turn
身份执行；通知追加到final_events，在TurnCancelled公开交付前输出，不依赖可能被
遗弃的有界队列。只处理interrupted，所有SubAgent跳过，replaced阴性对照保持。
取消终态仍按原逻辑持久化，Hook错误不改写成成功；执行/账本异常保留cleanup_error。
目前Hook在终态保存之后执行，关闭/冷恢复交错仍需专项验收，不据此声称完整恢复一致。

发现/信任身份新增Interrupt，matcher归一化为None；命令/MCP默认1秒、上限3秒，
async命令保持异步。专用输出只接受systemMessage，空输出成功，非JSON/额外字段/
不合法类型/非零exit都是诊断失败，无stop或context。使用独立interrupt_hook计划和
claim/raw记录，已知结果可读取，未知不重执行，未claim项需当前授权仍一致。
异步由现有共享AsyncHooks管理；MCP通过现有通用call_hook接入，不连接官方服务。

根反例已转绿，六个公开Runtime场景通过（733f35）；扩展async true/false共12场景，
异步实际Python脚本等待release文件，先观察取消终态再放行，等待raw结果提交后检查
单次效果，异步不发送同步Hook生命周期通知。解析14例、非零退出4例、命令/MCP
超时与matcher身份8例，合计 **38 passed / 1.75s**（fdb911）。
原六场景及相关关闭/初始化/checkpoint/SessionEnd/异步Stop/发现/身份九文件
**151 passed / 21.31s**（fb9423，早于新增异步六场景）；Ruff、format和diff检查通过。

仍开放：真实MCP中断场景、executor/最后Step来源与请求metadata、关闭已封闭异步准入
时的中断调度、异步反馈冷恢复、持久计划字段损坏/提交故障、未知结果冷恢复、真实超时
与放弃观察者回压。不能将本批基础接入称作完整A4/A7/E7关闭；CLI仍暂停。

## 实施前审计与反例

状态：已追踪源码并构造真实 Runtime 反例，尚未接入生产；不是 SessionEnd 的复用别名。
本项属于主循环取消与终态，不恢复 CLI 对齐，不需要官方服务或原生模型协议。

## Codex 调用链和契约

基准 `ddf04ad26789d040f9ef6a96736f76602e35a6cc`。

- `core/src/tasks/mod.rs`正常任务收尾的 abort_reason 分支及强制 abort_task 路径，
  仅 `TurnAbortReason::Interrupted` 调用 `run_turn_interrupt_hooks`，在 TurnAborted
  通知前执行；BudgetLimited/replaced 等不因同属取消而触发。
- 强制 abort 路径先取消/等待任务、调用任务 abort，再保存并刷新中断历史标记，
  最后触发 Hook。不能在模型/工具仍执行时并发发出中断完成通知。
- `core/src/hook_runtime.rs::run_turn_interrupt_hooks`跳过所有 SubAgent；从已脱离
  active turn 的最后执行 Step 取 executor hooks/request metadata，不重新发现新 Step。
  使用原 Turn 身份，脚本前 flush_rollout 失败只告警。
- `hooks/src/events/interrupt.rs`不按 matcher 过滤。payload 为 session_id、turn_id、
  transcript_path、cwd、hook_event_name、model、permission_mode；请求 metadata 另行传输。
  支持本地命令与 MCP。Executor-scoped 走异步且不发公共 Hook 生命周期通知。
- `hooks/src/engine/discovery.rs`默认 1 秒，上限 3 秒，命令 async 保留，MCP 同样限时。
  本地配置的 matcher 被归一化为 None，不能照搬 SessionEnd 的 reason=other。
  相关原生测试 `interrupt_normalizes_timeout_and_supports_async_execution`、
  `interrupt_mcp_tool_hooks_are_supported_and_timeout_is_clamped`。
- `hooks/src/schema.rs::InterruptCommandOutputWire`只接收可空字符串 systemMessage，
  deny_unknown_fields。`events/interrupt.rs::parse_completed`：空 stdout 成功；合法 JSON
  可生成 warning；非 JSON/非法 JSON/非零 exit/无 exit/runner error 为 failed 诊断。
  不接受 Stop 的 continue/decision/context，不控制取消终态，不注入模型上下文。

## Corki 对应链路与影响

`plugins/agent_hooks.py`承认事件名，部分解析器允许识别该 hookEventName，但
`core/stop_hooks.py`没有 Interrupt 发现、指纹或 prepare 快照入口。
`runtime.cancel_active`通过 TurnRun.cancel 保存 interrupted/replaced 原因；
`_execute_run`取消清理、输入归还、中断 marker、终态落盘链均没有分发 Interrupt。
所以配置可信 Interrupt 脚本不会执行。`_run_graph`先交付 TurnCancelled 再抛
CancelledError 属于现有接口契约，测试必须保留该控制流。

## 行为反例

`tests/integration/test_interrupt_hooks.py`实际阻塞模型 stream，以公开 cancel_active
触发取消。root/ThreadSpawn/review × interrupted/replaced 六场景，可信脚本读取
transcript 并写日志，预期仅 interrupted root 执行、Hook 通知先于唯一终态、保持原
Turn 身份、模型已结束、重复 cancel/close 不重执行。
校正测试对公开流 CancelledError 的接收后，得到 **1 failed / 5 passed / 1.06s**
（a5cfdc）：root interrupted 没有脚本效果；五个阴性对照通过不证明根路径支持。
最初六例因 fixture 未接收公开取消异常而失败（8c3bfd），不把它称生产故障。
信任摘要使用独立规范化配置，未设置的 matcher 不进入身份；不通过 monkeypatch
执行器、xfail 或 skip 掩盖缺口。生产尚未修改，不称测试全绿。

## 实施顺序与验收

1. 增加真实发现与规范化：忽略 matcher、命令/MCP 默认及上限、保留 async，
   对齐既有 trust/source/环境，不为关闭场景错误拒绝 MCP。
2. 专门解析 Interrupt 输出；复用安全执行和账本，但不复用 Stop 控制/context。
3. 接入已结束执行工作的取消收尾、TurnCancelled 通知之前。用 final_events 等不依赖
   已放弃的有界队列的出口，防止关闭/观察者退出死锁；保留原取消异常及持久终态。
4. 执行未知/已提交结果不能因恢复中断 marker 再次运行。评估独立持久计划/claim 与
   terminal save 失败的窗口，并使用最后 Step 的已确认来源，而非当前全局配置误归属。
5. 根反例转绿后验证同步输出、异步和 MCP、子 Agent/替换隔离、取消中再次关闭、
   超时真实进程回收、故障/冷恢复，以及现有关闭输入与 checkpoint 回归。

整个 A–E 仍开放；本项不能只实现同步正常脚本就标记完整生命周期对齐。
