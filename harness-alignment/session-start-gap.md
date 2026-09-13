# A4：SessionStart / SubagentStart 生命周期接入缺口

## 取消终态待写与旧消费义务交叉：已修复

新公开 Runtime 反例：manual compact 后 SessionStart claim 期间取消，同时让
CANCELLED save/retry 持续抛 OSError。Runtime 已持有取消结果并允许新 Turn，
但 SQLite 仍为 RUNNING；consume 只看数据库，将旧 unknown Hook 交给新 Turn，
导致新输入 TurnFailed。三组合先 1 失败/2 通过（99c77a），不是假设性缺口。
原生 state/session.rs::take_pending_session_start_source 为 pop_front，已取出来源
不因终态归档失败重新归属；tasks/mod.rs 归档失败后仍调度新工作，二者须同时保持。

GraphRunContext 新增不可变 terminal_pending_turns，由 Runtime 从自身待写终态
集合捕获，不来自模型历史或冷恢复推断。consume 遇到其他 Turn 已由当前 Runtime
持有终态时不转移义务；同 Turn 恢复仍走原账本，冷 Runtime 默认为空，仍检查
数据库/unknown。旧脚本未知账本不改、不补成功 receipt，待写终态仍由原 owner
补写。测试验证新输入正常、Hook 一次、摘要一次、数据库旧状态确实 RUNNING，
解除故障后 resume_pending 仅补终态、不重采样，最终 CANCELLED。

五文件138通过（c73319）；完整 unit/core/storage 加七个 compact/start/terminal
集成文件514 passed，18.17秒（73565d，19136实际退出0），4 workers/loadfile/
禁重启，null keyring/当前 sandbox compiler。Ruff cc8563 通过。无活动测试。
本次改变生产逻辑，15397全量是本修复前基线，不宣称已刷新；冷进程丢失内存终态
与其他取消/提交交错仍按各自恢复证据判断，不把本路径推广为任意 exactly-once。

## 自动压缩安装后 RUNNING 冷恢复：已补验

原生 session/turn.rs:540 在成功 mid-turn 压缩后消费 SessionStart 再继续采样。
Corki window.prepare 读取同 Turn 已安装 marker 调用 PostCompact/recover，graph
回调再消费 compact 来源；本轮核对这条实际恢复入口，不用手动失败后的新 Turn
代替。test_start_hook_recovery 新增 auto/installed × stop/allow × 保留/移除配置
四例，在 append marker 已提交后立即抛错，保留 RUNNING graph checkpoint。
故障后验证 marker 存在、consumer 未绑定、执行账本为空；冷 Runtime 通过公开
resume_pending 恢复，按当前配置选择 Hook，停止控制及输入身份正确、摘要一次、
原工具效果一次、反馈最多一份，二次恢复为空。
完整文件 92 passed，16.96 秒（2aedad，79017 实际退出 0），4 workers/loadfile/
禁重启，null keyring/当前 sandbox compiler；Ruff 361414 通过。仅测试修改，无
生产修改或活动测试。此证据与手动失败终态后的安装验证互补，不覆盖安装前重试
造成的重新摘要次数，也不声称强杀外部副作用具备 exactly-once。
下一步收敛取消/终态缺失下跨 Turn 的消费所有权，避免继续重复已证明的安装后
同 Turn 正常恢复排列；A–E 完整目标仍进行中，CLI 暂停。

## 来源登记与安装失败后的下一 Turn：已补验

对照 session/mod.rs:3821–3837：原生持久化压缩记录及基线后排队 Compact 来源。
Corki graph 的 PostCompact/prepare 先登记来源，window 随后 append marker；
compact_start_hooks.consume 只遍历已安装 marker，因此提前登记不能独自触发。
新增 test_compact_start_install：公开 manual compact 在 source 保存前/后、marker
安装前/后注入 OSError，均保留 TurnFailed；每窗口含 stop/allow 两种，共 8 例。
断言失败时一次摘要、零脚本，来源记录和 marker 的存在性分别符合提交窗口。
真实冷 Runtime 中原失败 Turn 不恢复，下一普通 Turn 仅对已安装 marker 执行一次
Hook；仅登记的孤立来源不执行、不影响输入，已安装 stop 生效；再下一 Turn 不重做，
原历史前缀不改写、摘要不重算。只修改临时测试库，不修改用户历史。

新增 8 通过（257a75）；进程清理随后确认为退出 0（1cccb9），不以提前汇总替代
终态。五文件扩大（本文件、start_hook_recovery、compact_session_start、
compact_hook_install_recovery、async_compact_hooks）154 passed，15.39 秒
（23f654，98329 实际退出 0），4 workers/loadfile/禁重启，null keyring/当前
sandbox compiler。Ruff afbf31 通过。仅新增测试，无生产修改，无活动测试。
本批为失败终态后新 Turn 的手动路径，不等同自动路径 RUNNING checkpoint 恢复；
不要泛化核销全部安装/取消窗口。下一步据现有自动安装恢复证据核定真正剩余组合。

## 消费 Turn 绑定前后故障：已补冷恢复验证

对照 hook_runtime.rs:124–175 的来源出队后按当前配置选择逻辑，以及 Corki
compact_start_hooks.consume → start_hooks.run：consumer 绑定记录不是执行计划，
不能用它冻结当时的 handler，也不能把它当作脚本已执行。新增 manual/auto ×
consumer 保存前/保存后 × stop/allow × 保留/移除配置，共 16 个冷恢复场景。
故障时检查脚本及执行账本为空，绑定记录按窗口缺失或精确指向原 RUNNING Turn。
冷 Runtime 公开 resume_pending 按当前授权选择，配置移除时无脚本、无反馈但可
继续采样；保留时一次执行并遵守 stop。摘要、工具副作用、原输入身份及反馈不重复，
二次恢复为空。这不同于已冻结计划后撤销授权的拒绝路径，旧断言仍保留。

完整 test_start_hook_recovery 88 passed，16.25 秒（d835de，46325 实际退出 0），
4 workers/loadfile/禁重启，null keyring/当前 sandbox compiler；Ruff 4597c6 通过。
本轮仅测试修改，无生产修复，无活动测试；15397 全量不包含后续新增的这些测试。
source 登记/marker 安装窗口、终态缺失时的跨 Turn 及取消交错仍待独立验证，
本批不是任意进程强杀或外部副作用 exactly-once 的证明。

## 压缩来源计划提交后、执行前的冷恢复：已补验

重新核对原生 hook_runtime.rs:124–175：取出来源后构造当前请求并选择/执行 hooks，
同步反馈再进入上下文。Corki compact_start_hooks.consume 将 marker 与 consumer
绑定，start_hooks.run 先保存计划再 claim 执行；恢复时尚未执行的计划必须仍匹配
当前授权，不能因计划已落盘便绕过撤销。已完成/未知执行的恢复边界保持原语义。

test_start_hook_recovery 原有 initial/clear planned 窗口未覆盖 compact operation；
新增 manual/auto planned，各组合 stop/allow 与保留/移除配置，共 8 个场景。
在计划落盘后注入失败，断言脚本未执行；真实冷 Runtime 通过 resume_pending：
授权仍在则执行一次并遵守停止结果，授权撤销则失败且不执行；摘要一次、auto 工具
副作用一次，原输入身份/准入与反馈唯一性保持，第二次恢复为空。
完整文件 72 passed，13.29 秒（47e1b6，71551 实际退出 0），4 workers/loadfile/
禁重启，null keyring/当前 sandbox compiler；Ruff 与 diff check 678774 通过。
本轮仅增加测试，无生产修改；此前 15397 全量不包含新增 8 例，勿混写计数。

consumer 绑定前后、安装登记窗口及跨模块取消的验收仍独立开放，未用计划窗口
替代它们。E4 运行中 loop 的同步 create 无 owner 兼容选择仍未确认，本轮未改 API；
CLI 保持暂停，继续推进不依赖该选择的 A–E 核心项。

## 取消后的旧消费义务阻塞新Turn：已修复

新反例通过公开manual compact→stream消费Hook，runner在已claim后抛CancelledError。
原Turn正常保存CANCELLED、raw结果仍未知；同Runtime/关闭后冷开提交新输入，两者原先
均TurnFailed（Start hook outcome unknown），旧source永久阻塞后续输入。
**2 failed**（179432）确认，并非未知副作用被重做，而是错误地跨Turn继承了旧义务。
Codex `hook_runtime.rs::run_pending_session_start_hooks`在执行前take来源，
`state/session.rs::take_pending_session_start_source`为pop_front；取消不会把来源重新排队。

现`compact_start_hooks.consume`在consumer属于另一Turn时读取其权威生命周期事实：
已终态则不把已取出的来源转移给新Turn，原执行账本保留未知，不补成功receipt、不重跑。
同一个RUNNING Turn的冷恢复仍走原结果恢复/unknown失败语义；缺失consumer Turn报错，
不能用“查不到”推断完成。新增SessionRepository.load_turn_status为thread+turn精确
只读查询，SQLite及继承其实现的Volatile支持；自定义repository适配器需实现该端口。
未知status作为StorageIntegrityError拒绝，不使用展示历史猜测核心生命周期。

两反例转绿，原取消输入不出现在新请求，Hook一次、摘要一次、原unknown ledger不变。
相关compact/start恢复 **34 passed / 6.47s**（f2ad37）；新增持久/内存两种存储测试
覆盖各状态、缺失、跨thread隔离与非法status。五文件扩大回归 **91 passed / 16.11s**
（ed778f），修改范围Ruff/format/diff通过（7584ad）；不是完整核心全量验收。
本修复不等于所有取消窗口完成：终态尚未保存的中断、consumer绑定前后故障、失败终态
及实时输入交错仍需专门验收。CLI继续暂停，核心目标未完成。

## 压缩来源执行账本冷恢复补验

`test_start_hook_recovery.py`在原initial六例之外新增manual/auto×stop/allow×
unknown/completed/receipt/done共16例。manual先通过公开compact安装摘要，再启动
真实RUNNING graph消费；auto走模型工具调用→16KB工具结果→普通摘要→start消费。
在raw提交前/后、start receipt提交后、source done提交后注入OSError，保持RUNNING
检查点并关闭原Runtime；移除Hook配置后新Runtime用公开resume_pending恢复。

验证：unknown失败且不重跑；其余窗口保持原反馈及stop/allow决定；原执行账本完全
相等、脚本仅一次、摘要仅一次、auto工具效果仅一次，允许才增加一次后续模型采样。
原始用户贡献身份精确等于原item.id（压缩保留副本由retained_from_id区别），未准入
用户不出现；已知结果context仅一份；二次resume为空。受控runner/实际SQLite，不冒充
OS断电或真实外部服务幂等性证明。
新文件 **22 passed / 3.95s**（06cb78）；五文件联合 **92 passed / 17.07s**（f83814，
早于最后加强的原始输入身份断言，该断言随后随22例单独通过）。Ruff/format/diff通过
（b71fa2）。本批仅测试/文档，没有生产修复，不宣称新的完整核心全量通过。

下一步不重复扩大已验的这四类执行提交窗口；优先登记/安装/consumer绑定窗口、
取消后跨Turn消费义务、其它start来源与SessionEnd/Interrupt生命周期。A4/A7/C8继续
开放，异步投影及输入实时交错也未被上述同步冷恢复覆盖；CLI暂停。

## 压缩来源基础生产链已接入（覆盖下方未修复状态）

`compact_start_hooks.py`将每个marker的source登记与实际执行计划分离：安装前只写来源
登记，消费时按已安装历史顺序与marker匹配，未安装及旧marker不触发；实际消费时才
选择可信handlers。consumer持久绑定执行Turn，done记录消费结果；start_hooks.run
新增独立compact operation键，不覆盖initial receipt，也不消费startup/resume意图。
ThreadSpawn等SubAgent compact仍不执行root start。

graph在初始start之后、UserPromptSubmit之前消费已有来源；自动压缩PostCompact完成/
恢复后、普通采样前再消费新来源。手动compact仅登记，下一正常Turn消费。
start stop通过专门控制异常由Runtime映射正常TurnCompleted，不映射取消；终态输入
保留同时检查compact消费准入，未完成/停止不会在cleanup复活未准入输入。
window.prepare在恢复回调后刷新历史，在自动安装回调后重建active、工具定义及token
估算；body-prefix基线也使用包含反馈的新估算，不再返回旧compacted_active遗漏context。

原manual/auto×continue/stop四反例转绿（2fbf09）；已有compact Hook/安装恢复及initial
start/冷恢复四文件95通过（023c42）。新增manual冷开、同Turn两次自动压缩及下一Turn
不留旧来源，五文件联合 **103 passed / 15.18s**（345e5e）。最后新增排队后安装/移除
配置两个真实冷Runtime场景，专项 **10 passed / 2.25s**（176700），实际消费才应用
新配置，已安装摘要不重算。Ruff/format/diff检查通过（cab327）。

仍需验证：登记/marker/consumer/执行提交/done各故障窗口、consumer跨Turn的取消恢复、
持续反馈超过窗口的处理、模型切换/realtime交错、异步compact start checkpoint反馈。
完整clear来源、分叉/显式新thread归类、初始resume义务关系及其它生命周期仍开放。
不将本批局部结果称为最新核心全量通过；A4/A7/C8仍未整体关闭，CLI继续暂停。

## 当前优先：压缩来源反例已确认

`test_compact_session_start.py`新增manual/auto×continue/stop四个公开Runtime场景：
manual先compact，再新用户Turn；auto先工具调用产生16KB结果，使真实普通模型摘要
触发。两者均设置可信SessionStart matcher=compact，受控runner记录payload。
**4 failed / 1.08s**（55ee69），均失败于摘要安装后Hook调用数仍为0；无xfail/skip。
本批只添加测试与审计，尚未修改生产。后续断言等待验证：一次摘要/一次工具效果、
source=compact、正常终态、stop阻止后续采样、上下文在即时继续请求可见且不进入之前摘要。

进一步源码证据：Codex `session/mod.rs`在持久化Compacted及相关基线后排队Compact来源；
`session/turn.rs`在每次成功mid-turn压缩后消费队列，stop返回Ok(None)。
`core/tests/suite/hooks.rs`的manual next-turn、mid-turn每次continuation及stop三个
测试分别约束下一Turn注入、每次压缩对应一次Hook且不留到后续Turn、禁止下一次采样。

Corki `context/window.py::prepare`先计算compacted_active/compacted_estimate，再保存
marker并调用PostCompact，最后仍返回原compacted_active；manual compact也从stored+
appended计算active。因此仅在PostCompact里执行start并append context，会形成“账本
有context但即时模型请求没有”的新缺陷，也会漏算上下文成本。
当前`start_hooks.run`使用每Turn initial键及单个start_source，只解决初始义务，不能
承载同Turn多次压缩，也不能通过设置start_source="compact"覆盖已经保存的initial receipt。

下一步实现要求：
- 建立每个压缩marker独立的待处理来源身份，区分已安装marker和安装前计划；不能
  给旧历史marker凭当前配置追补副作用，也不能让安装前失败触发compact start。
- 排队来源与实际执行计划分开；Codex排队的是source，实际消费时才选择handlers。
  不直接套用PostCompact的提前冻结commands逻辑，需保留配置变更/授权边界。
- 手动压缩只排队，下一正常Turn先消费；自动压缩在继续采样前消费。不能把start
  continue:false翻译成压缩取消，也不能撤销已经完成的工具或已安装摘要。
- 注入后重建并预算模型可见历史，包含新context；持续压缩须每次独立触发，不靠
  固定单次补丁绕过上下文窗口限制。来源队列及执行receipt需覆盖冷恢复。
- startup/resume与compact须有明确顺序、无覆盖/重放；ThreadSpawn仅startup走
  SubagentStart，其compact仍不触发root SessionStart。

下一轮优先修复本链，而非继续扩大已通过的初始startup矩阵。CLI仍暂停。

## 当前实现进展（覆盖下方历史缺失状态）

启动/resume基础路径已接入`core/start_hooks.py`和真实Runtime/graph：新增可信事件发现、
source/agent_type matcher、独立start输出控制、计划/claim/raw result/receipt及有界上下文。
graph在UserPromptSubmit前执行；同步上下文先入历史，continue:false正常结束且保留context。
Runtime终态初始输入保留须通过start准入，未完成或已停止检查不会被清理路径绕过。
同Runtime只消费一次初始义务；完成receipt用于同Turn重入。工厂会先生成thread_id，
已修正因此将全新会话误标resume的问题，保存生成前的启动意图。

ThreadSpawn startup走SubagentStart，payload含turn/agent身份，以角色匹配；review/compact
SubAgent不执行。start wire复用严格公共context字段校验，但先排除decision/reason，
非0（包括exit2）只失败不拒绝，SubagentStart/async不能停止Turn。
同步多脚本并发执行、按配置顺序发布结果；异步复用共享owner/并发限额，后台只提交raw，
prepare/finalize边界从账本投递，delivery receipt去重，未见反馈可触发后续采样。
异步基本生产路径已写入，但尚无其专项恢复/压缩/取消证据，不能据此称全部异步验收。

验证：原startup/resume×context/stop四例通过（89a5af）；连同prompt正常/恢复/异步及
MCP Hook五文件 **78 passed / 21.16s**（599ab3）。新增unknown/completed/receipt×
stop/allow六个真实SQLite/RUNNING冷恢复场景通过：配置移除后未知不重跑、已知结果
保留原控制和context，脚本一次、模型最多一次、二次resume为空（c2a018）。
ThreadSpawn/review/compact三个Runtime场景及24个输出契约组合，联合新功能三文件
**37 passed / 2.61s**（d14f90）；Ruff/format通过（f35999），diff检查无输出。
共享发现/指纹、插件reload与异步Stop四文件追加 **37 passed / 5.15s**（76be01），
不是上方同为37项的新功能测试；检查未引入已验的共享发现/关闭回归。

仍未关闭：compact/clear来源队列、分叉/显式新thread_id启动归类、恢复既有义务与新resume
义务的完整关系、异步采样checkpoint投影、持久payload/receipt损坏校验、MCP专项执行、
启动取消和realtime leftovers。下一步优先压缩后compact来源的真实链，不能仅扩充
startup正常测试而跳过该行为。A4/A7/C8仍开放，CLI暂停，最新核心全量未重跑。

## 以下为实施前审计与反例

状态：缺失，已用实际Runtime反例确认；未修复，不是已通过的验收。
Codex基准HEAD仍为ddf04ad26789d040f9ef6a96736f76602e35a6cc，参考树干净。
Corki保留现有脏工作树，本批只添加测试与审计，不修改CLI/teach.md。

## 源码调用链与可观察契约

- `codex-rs/core/src/session/session.rs` 初始化完成后，根据InitialHistory排队来源：
  New/Forked→startup，Resumed→resume，Cleared→clear；不是每个普通Turn都触发。
- `core/src/session/turn.rs::run_turn` 在首次用户输入的
  `run_hooks_and_record_inputs`之前执行`run_pending_session_start_hooks`。
  start要求停止时返回Ok(None)，不继续准入用户或采样；属于正常结束而非取消。
- `core/src/hook_runtime.rs::run_pending_session_start_hooks`逐个消费来源：
  root走SessionStart；ThreadSpawn仅startup走SubagentStart，携带agent_id/agent_type/
  turn_id；其它SubAgent来源不执行start hooks。不能将所有内部工作误当root启动。
- `hooks/src/events/session_start.rs`：SessionStart matcher是source，SubagentStart matcher
  是agent_type。共享字段session_id/cwd/transcript_path/model/permission_mode；root输入
  没有SubagentStart的turn_id/agent字段。执行结果支持plain stdout和结构化additionalContext，
  每个上下文独立限额/溢出处理；非法JSON和非0退出失败但不阻断。
  仅同步SessionStart能以continue:false停止，SubagentStart及异步结果不控制Turn。
  即使SessionStart要求停止，已经产生的additionalContext仍先记录。
- `core/src/session/mod.rs`安装压缩历史后排队compact来源；`session/turn.rs`中途压缩后
  再执行pending start hooks，先于下一次采样。它不等于PreCompact/PostCompact，不能
  用已有两类Hook代替。手动压缩后的下一Turn也要考虑队列。
- 参考测试`core/tests/suite/hooks.rs`包括首次SessionStart→UserPromptSubmit顺序、
  上下文spill/独立limit、compact后上下文、mid-turn每次压缩后继续/停止，以及
  resumed_thread_runs_resume_then_compact_session_start_hooks。

## Corki当前证据

`plugins/agent_hooks.py`只承认事件元数据（模块明确不负责执行/授权）；
`core/stop_hooks.py::command_identity/discover/StopHooks.prepare`当前7类事件不含start。
`core/graph.py::_prepare_model_context`直接进入async prompt drain和UserPromptSubmit，
没有start准入或来源队列。`runtime.py::_ensure_thread`只创建/恢复线程身份，未建立
SessionStart义务。现有compact_hooks只处理Pre/PostCompact，不是缺失事件的替代实现。

`tests/integration/test_session_start_hooks.py`新增startup/resume×context/stop四场景：
真实Python脚本记录payload，独立计算可信指纹，通过公开acreate/stream驱动；resume
先用无Hook配置写入SEED再关闭重开。四例均失败于可信脚本从未运行：
**4 failed / 1.15s**（ceb885）。没有xfail/skip，当前新测试并非全绿。
之后断言用于验收source/session身份、正常终态、停止不准入用户/不采样但保留context、
允许时context在用户之前、同Runtime第二Turn不重复start；这些后续断言尚未跑到。

## 实施顺序与完成门槛

1. 接入可信发现、source/agent matcher与专门输出解释；不要借用UserPromptSubmit的
   decision:block或exit2拒绝语义，也不要借用compact Hook的取消语义。
2. 明确Runtime实例启动与持久Turn恢复的关系，保存待处理来源及每次触发身份；
   使用已有plan/claim/result/receipt机制，未知副作用不能重放。旧记录不可凭空推断
   过去已经运行或给旧压缩安装补做新配置效果。
3. 在用户准入前及压缩后采样前消费；start停止需让Runtime最终清理路径也不复活
   尚未准入的用户，不能只让graph返回空pending。
4. 覆盖ThreadSpawn/internal隔离、resume/compact顺序、matcher/trust、MCP及异步
   反馈、取消和冷恢复。先使上述四个真实入口反例转绿，再补跨模块交错。
5. A4/A7/C8仍开放。CLI对齐暂停，普通模型协议与官方服务隔离边界不变。
# 显式新线程ID来源误分类（当前修复）

持久义务交接补验：test_start_hook_recovery初始Hook改为明确startup matcher，
冷配置增加保留/撤销对照，并加入计划已提交但尚无claim窗口。冷Runtime虽然因
线程存在归为resume，保存的startup计划仍是实际匹配/恢复依据：保留授权才执行
未claim命令一次，撤销时报authorization changed；已有completed/receipt复用，
unknown不重做，原payload/commands不改写。保留手动/自动compact的全部原提交
窗口，输入身份、context反馈、摘要/工具副作用次数断言继续检查。该文件48通过
（1616d6），三个启动文件联合90通过/10.44秒/退出0（87234d），8worker/loadfile、
禁用worker重启；修改文件Ruff/格式/diff通过（5d0049）。本批只改测试。
受控graph提交故障后公开resume，不冒充OS强杀；该组合不要求再发一个新的resume
Hook覆盖原计划，不用新的来源猜测旧副作用。仍不关闭clear/分叉等其余生命周期。

补验：SessionStart首输入测试扩为四来源（自动新ID/显式新ID/已有历史/仅有线程
无历史）×停止/继续×正常/创建提交前故障/提交后故障，24例。故障后原异常对象
保留、writer释放、实际线程存在性与提交窗口一致，无Hook或采样；同Runtime恢复
FIRST仍用初始来源，SECOND不重复start。仅线程元数据冷开走resume而非按空消息
判断startup。新增显式新ID的ThreadSpawn/review/compact子Agent对照：只有
ThreadSpawn触发SubagentStart，continue:false不控制子Turn，身份与上下文保持。
六文件8worker/loadfile共91通过/12.78秒/退出0（f8ca98），修改文件静态与diff通过
（f54a48）。本批只补测试，不改生产；不是进程强杀或跨宿主并发创建保证。

Codex session/session.rs按InitialHistory::New/Forked→startup、Resumed→resume，
不以线程ID是否由调用方提供来判定。Corki runtime.py构造阶段仅用thread_id是否None，
显式新ID首次stream实际创建新线程却发resume。test_session_start_hooks新增
explicit_new×stop/continue两例均失败，四原对照通过（f53661）。
拟在持有线程writer与初始化锁后以存储存在性解析一次来源；正常创建失败后同Runtime
重试不重新归类已提交线程，不覆盖恢复中的持久Hook计划。不复制官方会话服务。

已实施：SessionRepository新增只读thread_exists契约，SQLite以SELECT 1查询，
Volatile沿用同一实现；Runtime在writer/初始化锁内、create_thread之前解析一次来源。
_start_source_resolved保留本Runtime的首次归类，避免部分初始化后重试把新建变成恢复。
新增显式新ID两例随六文件70通过/12.98秒/退出0（410e9f），包括start/compact-start、
writer所有权、memory来源、volatile及执行身份。原新ID两反例转绿，既有恢复对照保持。
首次命令误含不存在的test_session_start_recovery.py导致零测试/退出5（eba417），
已改用实际文件，不把该次算作回归。运行结束后仅格式化SQL查询表达式。

边界：宿主自定义SessionRepository需实现新增只读方法；内置持久/内存仓库已接入，
未新增schema或改写旧历史。创建提交前后故障、仅创建未产生Turn的冷恢复及子Agent
显式新ID应继续做针对性验证；本批不宣称所有启动/分叉/clear来源完成。CLI仍暂停。
