# E6 半截正文与取消：当前直接证据

## 多条待写的真实进程退出：已补验证据

test_pending_terminal_process_recovery使用独立Python进程建立同Thread三个Turn：
前两条终态save/retry持续OSError，第三条正常提交；确认两条pending和三次采样后
os._exit(73)，不调用Runtime或asyncio正常关闭。父进程验证退出码，再以禁止采样
的ModelPort重开相同数据库与Thread，连续resume恢复answer 2和answer 1；第三次
resume为空，原始有序历史完整相等，数据库无RUNNING。退出释放真实Thread租约，
不是清空字典后正常close的模拟。该验证不含执行中工具副作用或摘要安装窗口。
单项1通过fecc4a；连待写准入/恢复/确认/告警/准入六文件51通过、4.77秒，
d1f116确认91198退出0；4workers/loadfile/禁重启，null keyring。Ruff85e29b。
本轮仅新增测试；不需因同类正常排列继续扩展，下一步回到C1/C2明确缺失的typed
上下文section（见context-snapshot-wire.md），压缩/Hook交错仍在原C8清单验收。

## 新Turn准入：可恢复旧终态故障不再阻断

再次读取原生tasks/mod.rs:846–865及rollout/src/recorder.rs:1700–1770：后台
writer保留失败数据，有限重试失败不阻止已完成任务之后调度pending work。
Corki_start_turn原先无条件要求旧终态补写成功，导致旧结果单独写失败时阻断新任务。
新双Turn反例4c5a2a证实该阻断；现仅对明确可恢复存储错误允许新Turn继续，
并以新Turn Warning报告旧结果待写；
原record保留、不重放旧任务，新Turn自身准入/模型/工具提交仍须成功。
冲突/非可恢复错误/取消不降级；resume和关闭仍须排空，避免把旧RUNNING误当
需要重执行的任务。新增真实双Turn/SQLite回归验证此界限及恢复后只补写原结果。
旧admission取消/join测试的新Turn分支改用非可恢复SQL错误检查拒绝，恢复分支仍
用OSError检查拒绝；保留取消join、历史前缀、原子确认及模型次数断言。
五文件50通过9bc973；完整unit/storage、unit/core、全部integration/*recovery*及
终态/准入/关闭关联扩大1094通过、61.96秒（4b3056，52863退出0），4workers/
loadfile/禁重启，null keyring/当前sandbox编译器；Ruff e1c64e，diff c4e7f2。
本批未跑新全量；多条待写跨进程丢失、与手动压缩/Hook恢复交错仍需单独核验，
不以两普通Turn场景宣称所有恢复映射完成。

## 取消终态已提交后的确认缺口：已修复

旧Runtime终态save_turn异常分支仅对非TurnCancelled调用confirm_turn_terminal。
若CANCELLED已实际提交但ack失败，load_turn_status返回CANCELLED而不会入待写，
因而残留cleanup_error且无确认告警。验收应使用真实cancel_active、真实SQLite
提交后抛OSError，要求原TurnCancelled和CancelledError保留、Warning先于终态、
无额外采样/待恢复任务/清理错误。不能把取消控制流本身catch成普通错误。
新真实取消反例798b39复现cleanup_error残留；现移除确认分支的取消排除条件，
SQLite精确确认CANCELLED后沿用确认告警，不改变CancelledError传播或其他清理错误。
关联114通过（da9a3b，59862退出0）；扩大完整unit/storage、unit/core及终态/
准入/取消/关闭八个集成文件442通过、10.65秒（fcfa20，07426a确认85612退出0）。
4workers/loadfile/禁重启，null keyring/当前sandbox编译器；Ruff通过8bd701。
本次生产改动之后未刷新非CLI全量，48797是修改前的绿色基线。
原生tasks/mod.rs:800–865按abort_reason发终态，清除active后flush失败仅日志，
仍进入maybe_start_turn_for_pending_work；session/mod.rs:4156的append错误也仅日志。
因此持续待写下Corki准入屏障仍是待收敛差异，不借执行账本不同直接核销，亦不
简单删屏障而让resume重放旧RUNNING。该较大恢复映射与本次ack确认遗漏分别处理。

转录告警收尾补验：查询端口自身抛 OSError 的成功/模型失败/取消三种原终态均
保持，取消仍传播、无重采样，诊断错误仅日志。已有故障副本冷补写后再开 Runtime
正常新 Turn，不再发旧副本警告，工具仍只执行一次。当前关联三个文件 56 通过、
5.41 秒、4 worker、退出 0（006c73），本批仅测试；随后非 CLI 全单元
5878 通过、1 文件系统限制跳过、22.19 秒、8 worker、退出 0（b55067）。
不将单元结果代替生产变更后的全集成；本组告警边界不再重复扩展正常排列。

## 转录副本告警已接入 Runtime

原反例的事件流只有模型元数据 warning，没有转录副本 warning（a87541）；日志
不能代替宿主事件。现 SQLite 提供可选 transcript_publication_pending(thread)，
只读当前线程 pending 状态，不触发重试；复用取消时等待线程结束的封装，闭包
避免触发写后发布。Runtime 收尾将一条 WarningEvent 加入 final_events，在原
终态前交给观察者，不等待有界数据队列、不改变成功/失败/取消、不重放效果。
查询普通异常仅日志告警，不让可选副本诊断覆盖原终态；第三方没有此端口时跳过。

原工具链反例转绿，明确过滤副本 warning 并要求恰好一条；当前线程 pending、
其他线程不串报、冷补写后清空已断言。初批 56 通过（0de550），补取消可等待的
只读实现与线程范围断言后四文件 66 通过、5.15 秒、退出 0（70828a），Ruff 通过。
本批修改生产，旧全量结果不能代表当前代码。未改 CLI；此为已注册转录副本的
降级告警，不将按需注册扩张为默认生成所有 transcript，也未改业务账本失败政策。

## 归档副本与业务账本：修正待写所有权的判断

已追踪 Corki sqlite.py 的 transcript_threads/transcript_pending 表和 item 插入
触发器、_joined_write → _after_durable_write → transcripts.flush_transcript。
归档副本确有持久待写状态；投影失败只记录 warning，不回滚业务提交；后续任意
业务写入可重试，副本同步/fsync 成功才删 pending。它不是原生的内存追加队列，
但“副本写失败不重放执行、保留待写数据”的功能已有实现，不能再列为完全缺失。
save_turn/commit_model_step/claim 是另一层恢复与副作用账本，失败语义不能直接
套用 JSONL 副本的降级处理。原生默认 rollout 与 Corki 按需 transcript 的触发/
告警出口差异仍需分别核对，不将这条映射解释为所有持久化行为完全相同。

新增真实 Runtime test_transcript_publication_runtime：先注册转录，持续注入副本
发布 OSError，完整模型→工具→Observation→模型→TurnCompleted 正常结束；仅两次
采样、一次工具副作用，副本不变、pending 留存。Runtime 关闭后新 SQLite 实例的
无关线程写入补齐原副本，逐项 payload 与原始有序记录一致、pending 清空，无再采样
或工具执行。与已有跨进程/损坏文件/fsync/迁移/取消单元组联合 14 通过、1.87 秒、
4 worker、退出 0（55a1d9）。本批仅测试，Ruff/格式检查另核。
这推翻上轮可能缺少归档待写所有权的推测，但不关闭业务终态政策及告警可见性差异。

## 模型提交确认先于消息完成事件

test_model_commit_event_boundary 新增四窗口：append_partial_item/commit_model_step
× 提交前失败/真实写入后抛 OSError。模型只采样一次，宿主收到 TurnFailed 和原始
错误，不收到 AssistantMessageCompleted/TurnCompleted；数据库按真实提交事实保留
或不保留该项，partial 与 completed step 不混用。Corki graph.py 的顺序确实是
先 await 写入，再 output.complete；不能用模型已经返回数据替代存储确认。
4 专项通过（18777e），关联写入取消/准入恢复三个文件 26 通过、4.45 秒、
4 worker、退出 0（c125b2）。本批仅测试，Ruff/格式另核。

进一步追踪原生 rollout/src/recorder.rs：RolloutWriterState.pending_items 由后台
writer 持有，成功项才从队列移除，失败保留未写后缀；write_pending_with_recovery
会重新打开并再试一次，后续 flush/shutdown 可继续重试。这是前述 warning 语义的
配套条件，不是吞掉存储错误。当前 Corki SQLite 失败传播及已提交模型复用不等于
这套后台待写所有权，不能只改 Runtime 的异常级别来冒充一致。
该差异仍开放：下一步先核对 Corki 现有归档写入层是否已有等价可重试队列，区分
归档副本和执行账本的必要提交屏障，避免破坏工具 claim 的副作用安全。

## Turn 终态提交失败：来源差异与提交前后窗口

### 当前实现与仍开放的通知边界（覆盖下方历史状态）

运行期待写所有权已经实现，不再是下方所述“仅输入屏障”：Runtime的
_pending_terminals保留失败终态，_flush_pending_terminals接入新Turn准入、resume
读取账本之前和_close_storage_resources。SQLite retry_turn_terminal在BEGIN IMMEDIATE
中验证准入身份，缺失/冲突拒绝，相同结果幂等，RUNNING才更新且保留准入字段。
可恢复I/O/明确SQLite存储主码每次排空最多尝试两次；错误/取消保留未确认记录。
关闭失败保留存储和Thread租约，再次aclose不重复已完成的执行资源清理。
确认/状态读失败时，成功准入或已验证resume所拥有的payload仍保留待核对；未准入
则不凭空新增。告警实现后的扩大验证1092通过（6fb5ed，68161退出0）；
非CLI全量48797现已退出0：15328通过、1文件系统限制跳过（b482fd）；
64868的失败保留为历史记录，见core-regression-current.md。

本轮只读重核tasks/mod.rs:373–389、800–863（f167da）：Codex任务run返回后
flush失败先发Warning，再on_task_finished按原terminal_error/abort_reason发终态；
终态事件后还有第二次flush，仅记录失败，不改变已发事件。当前Runtime已区分
任务结果与持久化健康：已准入、持有待写record且首次错误可恢复时，先有限排空；
补写确认或仍为可恢复存储故障均发Warning，再返回原任务终态。
test_terminal_storage_warning四组合验证原成功/认证失败×补写可用/持续失败，
恰好一条Warning先于终态、一次采样、补写尝试上限及关闭保留待写状态。
本轮重新读取该测试及Runtime真实路径，未把测试范围扩称覆盖所有取消交错。
冲突终态或根本未准入不适用“仅归档告警”；已有模型失败、输入/Hook/工具事实
提交失败也不得一概降级。队列保留本身不是已落盘证明，更不能消除真实任务错误。
仍需核对持续待写故障时新Turn准入屏障与原生继续执行语义，以及取消和关闭的
跨模块交错；不能因这四组合通过而关闭整体A4/E6/E7。本轮只修正审计状态，
未修改正在运行全量回归所覆盖的生产代码或测试。

待写关闭契约进一步核实：rollout/src/recorder.rs:1111的公开shutdown文档/实现
明确drain失败不终止writer；1850–1875命令循环在Shutdown成功时break，失败仅
ack Err而继续接收命令。flush:1009同样等待writer确认，不把消息入队当成落盘。
因此未来终态待写队列必须有可重试关闭屏障：失败保留队列和存储/写者所有权，
再次close仅重试未完成排空，不重复已经完成的其他资源清理。当前Corki只有
输入flush的_close_storage_pending特殊屏障，不能未经实现就声称覆盖终态待写。
此轮仅只读核查，未改生产；全量session79636运行中，详见回归记录。

终态未提交的冷恢复事实补验（本轮无生产修改）：新增terminal_pending_recovery，
真实模型成功/认证失败后持续拒绝所有非RUNNING的save_turn，首调用报告storage
failure；关闭原Runtime，重新打开同数据库/Thread，cold模型禁止任何采样。
resume_pending均还原原答案或原认证错误，采样总数1，原始历史前缀不变，RUNNING
索引终结，再resume为空。两例通过（df101a，0.69秒），关联确认/admission/取消
意图恢复与完整unit/storage共184 passed（b993d9，3.60秒，退出0），Ruff通过。

原生路径校正并重读：文件是codex-rs/rollout/src/recorder.rs，不是core/src/rollout。
1683–1760的pending_items由后台writer保管，persist/flush/shutdown均先写一次、
丢旧句柄再试一次，失败仍留未写后缀；1816–1844仅drain成功前缀。Corki的
transcript_pending及_after_durable_write负责已提交conversation_items的转录副本，
不是未提交TurnRecord队列；save_turn失败后依赖checkpoint/模型事实和显式resume。
因此不能以本次冷恢复通过核销“后台自动重试与告警而不改任务结果”的行为差异。
下一步需定义终态待写记录的运行期所有者、重试屏障与关闭失败契约，才能考虑
放宽当前未确认终态的storage failure。不得盲目重试并覆盖已存在的冲突终态；
confirm_turn_terminal的false包含缺失/RUNNING/冲突，不能当成可安全覆盖的证明。

真实确认worker与Runtime关闭补验（本轮无生产修改）：test_terminal_confirmation
扩为任务成功/认证失败×观察者取消/不取消×关闭/不关闭八组合。先异步门验证
8 passed（ed7152），再把门移入SQLite._confirm_turn_terminal的实际to_thread
worker，用threading.Event与loop.call_soon_threadsafe同步；放行后仍调用真实查询。
关闭任务在确认未完成时不结束，也不先关模型/存储；最终严格confirmed→model
closed→storage closed，各一次，重复aclose不重复清理。原错误/告警/单终态和
观察者取消传播断言全部保留。联合存储/core/关闭/取消/admission共411 passed
（fd6940，8.68秒，退出0），4workers/loadfile，Ruff/格式通过（71e844）。
这证明公共关闭等待finishing Turn中的真实确认worker，不宣称任意强制取消内部
worker都有相同语义。下一步回到提交前失败的待写所有权，不重复这八组合；
非CLI全量仍是新增终态确认生产逻辑之前的证据，后续需刷新。

原失败与观察者取消补验（本轮无生产修改）：新增test_terminal_confirmation，
实际Runtime模型成功/认证失败×确认读回期间取消/不取消观察者四组合。真实终态
写入后注入OSError，confirm以Event阻塞，再取消consumer并放行真实独立读回。
四例通过（989316，0.83秒）：仅一次采样、一次原终态；认证错误文字/kind保留，
不转storage failure、不误成功；存储Warning恰一次且先于终态。观察者取消继续
以CancelledError传播，run本身done且无error/cleanup_error，resume_pending为空。
这是终态finishing之后取消观察者的公共路径；不外推为任意内部任务强制取消安全。
联合admission、runtime/interrupt关闭、取消意图恢复、terminal retention及完整
unit/storage/core：407 passed（9543ec，8.82秒，退出0），4workers/loadfile/禁重启，
Ruff通过（0d17b6）。不再把原FAILED错误保留笼统列为完全未验证。
仍需确认读回本身失败/取消的资源所有权及未提交待写策略，当前全量未刷新。

最新实现：已提交终态确认丢失的一红例修复（0d6d44，1 failed/2 passed）。新增
SessionRepository.confirm_turn_terminal；SQLite/Volatile以独立连接精确查询
thread/id/status/user_input/operation/final_answer/error，不只看COMPLETED枚举，
不修改原准入model/base字段。RUNNING不得确认作终态，缺行/冲突返回false。
Runtime仅在非取消终态写失败时读回；确认为True则保留原终态、追加存储Warning，
不留下该错误作为cleanup_error，不重写记录或重跑模型。读回不可用/抛异常/不匹配
仍走原storage failure；不吞CancelledError，取消原分支不改。

SQLite与Volatile×三终态六例逐字段错配/缺行/RUNNING/冻结base保留，真实Runtime
提交前失败、提交后确认、读回失败、同status不同答案：联合20 passed（47a6c6）。
扩大unit/storage+全部recovery命名集成+admission：825 passed（d42df2，58.65秒，
退出0），4workers/loadfile。追加暂停观察者重新消费，确认存储Warning恰一次、
先于原完成且无失败事件；最初统计全部Warning导致一失败（f552d5，启动还有其它
告警），改为按故障消息确认，文件12 passed（aa77ad，2.18秒，退出0）。静态通过，
格式检查发现长断言后已仅格式化。以上不是当前全量通过证据。

剩余：原任务FAILED时是否完整保留错误、确认读回期间取消/资源关闭，以及提交前
失败的待写所有权策略仍须专项；不据已提交成功分支关闭A4/E6/E7，也不把原生
归档warning套到未确认的执行账本。下方旧“提交后仍报失败”是修复前证据。

最新只读复核（集成90904运行期间不改生产/测试）：Codex tasks/mod.rs:373–389
在前置flush失败时发Warning后仍on_task_finished；810–863发送终态后flush失败
仅日志。Corki runtime.py:2073–2084在save_turn异常时无条件把非取消终态改storage
failure；sqlite.py:670–683已提供joined写入与独立load_turn_status，resume_pending
runtime.py:1473则只选择RUNNING。既有test_turn_admission.py:220明确覆盖真实
提交后抛错：数据库COMPLETED、后续resume空，但当前调用者收到TurnFailed。
这不是“模型没完成”，也不是“仍需恢复”；现有断言证明差异存在而非证明已对齐。

下一步在全量退出后优先针对“提交后确认丢失”提出行为反例并修复协调：只能在
独立读回确认该Turn预期持久终态后保留原终态并发出存储告警；读回失败、仍RUNNING、
缺失或冲突不能当成功。需要核对状态枚举是否足以证明预期记录，必要时比较完整
终态payload，避免只凭COMPLETED字符串接受不同结果。取消控制意图和未知工具
副作用不受此变更削弱。提交前失败的待写所有权/重试契约仍另行审计，不能以此
局部分支关闭全部A4/E6/E7；也不简单复刻归档warning来丢弃执行账本屏障。

读回接口追加核验：SessionRepository当前仅load_turn_status返回枚举；完整TurnRecord
读取只存在latest_running_turn。SQLite._save_turn的冲突更新只写status/final_answer/
error/updated_at，保留原user_input/operation/model_settings/base_instructions。
因此不宜把原终态请求的dataclass与读回整行直接全等比较：终态请求未携带固定模型
和基础信息，落盘记录合法地仍保留它们。需要按thread+turn精确读取并核对实际更新
字段及执行身份；不能为了协调而清空原准入字段，也不能用全历史显示分页替代单行
确认。读回本身取消仍须传播，告警投递不能抹掉真实任务失败原因。

原生 tasks/mod.rs 在任务运行结束后 flush_rollout 失败时警告，继续
on_task_finished；终态事件后的 flush 失败同样警告。不能把这描述为原生在所有
持久化故障下都把 Turn 改为失败。Corki 的 save_turn 是业务恢复索引写入，
异常时保留 ModelCompleted 已有记录，但把宿主可见终态改为 storage TurnFailed
（取消另走独立控制意图），这是实际行为差异，尚不凭“更保守”判定完全一致。

test_turn_admission 的已有暂停观察者/恢复用例扩展为无故障、提交前失败、
提交后报错；直接断言原 owner 的终态为 storage failure，包含真实错误原因。
提交前失败仍有 running 记录，resume_pending 使用已提交模型结果完成，采样总数
仍为 1；提交后报错已有 durable completed，resume_pending 不执行工作，采样仍为 1。
模型成功事实、对当前调用者报告存储错误、恢复索引状态三者不能混为一件事。

首次新增断言漏导入 TurnFailed，2 失败/1 通过（38b783），仅修正导入；三个
关联完整文件 62 passed、5.27 秒、4 worker、退出 0（207266），Ruff 通过。
本批仅测试和审计，不更改终态政策；不是冷进程重启或所有持久化故障的证据。
下一步需核对模型项提交与事件先后、并给上述 rollout/业务写入差异明确结论，
不能继续把所有存储错误混为统一的 Codex 失败语义。

## 计划标记结束不等于模型项完成

上述 HTTP/Runtime 反例扩展为 text/open_plan/closed_plan：在 plan 模式分别发送
未闭合或已有结束标记的计划文本，但不发送 item/response 完成事实。两普通接口
× EOF/ReadError/取消覆盖计划路径的 12 个新增场景。所有场景明确接收预期正文/
ProposedPlanDelta，不产生 AssistantMessageCompleted/ProposedPlanCompleted，
仍只有失败或取消 Turn 终态，没有成功 model_step、额外采样或重复关闭。
源依据：Codex turn.rs 对 ProposedPlanSegment::ProposedPlanEnd 不作完成处理；
Corki PlanOutput.delta 只发增量，complete(item) 才能生成计划完成事件。

当前文件 26 通过（c5b25b）；关联 parser 与已有 plan runtime 文件共 75 通过、
5.87 秒、4 worker、退出 0（1bf0df）。关联 plan runtime 文件包含已有 CLI 消费/
重放断言，因此这 75 项不标为“纯非 CLI”；本次新增测试完全不依赖 CLI，未改
CLI、未推进视觉对齐。旧混合测试的通过不能代替其暂停项验收。
本批只补测试。正文/计划的缺终态边界不再笼统列为未验，存储提交与终态故障
仍需独立收敛，不将计划结束标记当模型权威终态。

Codex 固定基准 session/turn.rs 的采样流循环：取消映射 TurnAborted，流返回
错误则传播；未见 response.completed 就 EOF 产生 Stream 错误，不以已输出文字
认定成功。Corki core/model_stream.py 独立管理迭代器及关闭，graph.py 在没有
ModelCompleted 时生成可重试 ModelError，Runtime 将取消单独映射为 TurnCancelled。
可重试类别不意味着无条件继续：采样预算耗尽后仍失败。

新增 test_partial_text_without_terminal_cannot_complete_turn：普通 Responses/
Chat 两接口 × EOF/ReadError/CancelledError 共 6 例，均通过真实 HTTP 适配器与
Runtime。明确断言宿主确已收到完整的 unfinished 文本 delta，而后只有一个
失败/取消终态，没有重试事件，只有一次请求，响应流关闭一次，没有成功的
model_step 记录；取消还须向调用者传播 CancelledError。

原文件 8 个混合正文/reasoning/工具半提交场景覆盖重试成功和冷历史不重放。
这次补的是零重试预算下的失败/取消出口，不能用“最终重试成功”的旧覆盖代替。
初次 14 专项通过（6707e3），补强实际 delta 已送达断言后，三个关联文件
68 passed、8.09 秒、4 worker/loadfile、退出 0（776b5f）。仅新增测试，未改生产。

E6 尚不整体关闭：计划/其他输出投影、提交和终态故障需逐项核对；任意自定义
ModelPort 不遵守事件合同也不能以此宣称安全。当前不是新的全量测试结果。
