# 近期核心改动后的扩大回归

## 当前全量已结束：15397 通过、1 文件系统跳过

74261 已实际退出 0（f57b1a）：15397 passed、1 skipped，539.86 秒。唯一跳过
为 tests/unit/config/test_project_trust_selection.py:164，文件系统拒绝非 UTF-8
文件名。完整累计输出已保存 full-regression-74261.txt；这是近期 Personality、
终态取消确认和待写终态不阻断新 Turn 等修改后的新全量，不再沿用 48797 旧基线。
运行期间未修改被测生产/测试，未重启 worker、未追加重复批次、未隐藏失败。
8 workers/loadfile 的全部非 CLI unit/integration 范围与六份 compiler 环境如下。
当前无活动测试；全量通过仅是回归证据，不核销 A–E 尚未验证的跨模块差异。

## 已结束批次启动记录：74261（Personality 及近期终态修复后）

已读取目标并确认启动前无 pytest/execnet/xdist 进程。机器 12 logical CPU、18 GiB
内存，沿用 8 workers/loadfile/禁 worker 重启；96 是总上限而非目标。五份历史
sandbox compiler SHA-256 均与 core-baseline-refresh.md 表一致（f4a89f）。
命令范围 tests/unit tests/integration，排除 tests/unit/cli 及 integration/test_cli*.py，
-o addopts='' -q --tb=short -rs；null keyring、当前及五份历史 compiler 环境。
启动 c5b644 返回 session 74261；跟进同句柄，不重复启动，不改被测生产或测试。
累计输出与最新结果在 functions stores personality_full_output/personality_full_result。
本节是运行记录而非通过声明，结束后须保留完整日志、退出码、失败和跳过原因。
CLI 仍暂停；48797 是旧版本已结束的基线，不能当作这轮最终结果。

## Personality功能开关：1348通过、1文件系统跳过

功能关闭使用默认模板并不生成独立section；明确none在开启时移除# Personality
首个H1段。开关纳入Turn/Step快照与冷绑定，不复制官方slug专属fallback。
四Runtime红例43ee1d转绿，扩大完整unit/config/context/core及personality_feature/
personality_updates/personality_selection_recovery/local_instruction_template/
active_step_settings/model_instruction_lifecycle/base_instruction_override/
base_instruction_recovery：1348通过、1文件系统限制跳过、9.82秒（a1fb11，67579
退出0），4workers/loadfile/禁重启，null keyring/当前sandbox；Ruff597fa6，diffb302f5。
当前无活动测试，全量未刷新；具体未闭合边界见personality专项顶部。

## 活跃模型切换保留personality：648通过

原生公开TurnSettingsUpdate没有personality，修正此前误列“缺少活跃风格更新API”。
实际新反例7c3d0d证实旧friendly任务切换模型时会带入后改Thread pragmatic；
解析宿主视图现在保留当前任务选择。初始/Step checkpoint和Thread各自值验证。
关联70通过7dc506；完整unit/core/context及active_step_settings/personality_updates/
personality_selection_recovery/local_instruction_template/thread_settings_update/
model_instruction_lifecycle共648通过、8.25秒（fb8c99，6454退出0），4workers/
loadfile/禁重启，null keyring/当前sandbox；Ruff3a1eb8，diff53dbc5。
当前无活动测试，生产改动未刷新全量；准确原生边界见personality专项顶部。

## Personality Thread更新与typed片段：1474通过

Thread默认SQLite迁移/读写、Runtime更新发布、builder状态与world_state渲染已
接入。两新Runtime用例覆盖baked/宿主自定义base、同模型变化仅一条独立developer
更新、基础指令不变、原历史前缀及稀疏更新不清选择。先红bdcdec，首轮接线签名
遗漏de76ae后修复，6通过140591；完整unit/config/context/core/storage及七个
personality/模板/基础指令/Thread更新集成文件1474通过、1文件系统跳过、9.12秒，
aff143确认94175退出0。4workers/loadfile/禁重启，null keyring/当前sandbox编译器；
Ruff354b43。当前无活动测试，本次生产修改未刷新全量；剩余边界见personality专项。

## Personality宿主选择与冻结恢复：1318通过

新增配置personality与ModelSettingsSnapshot选择，capture/bind及builder按准入值
渲染，旧快照缺字段兼容。四个真实prepare后冷恢复反例先红1c0b38，接入后四种
选择不被冷宿主none覆盖，原ledger/checkpoint选择保留，一次采样并终结。
完整unit/config、unit/context、unit/core及personality_selection_recovery/
local_instruction_template/model_instruction_lifecycle/base_instruction_override/
base_instruction_recovery/thread_settings_update共1318通过、1文件系统限制跳过，
8.98秒（272e70，36851退出0），4workers/loadfile/禁重启，null keyring/当前sandbox。
Ruffe9d3aa，diff03ccfa。当前无活动测试；本次生产改动未刷新全量。动态更新和
typed增量section仍待完成，不能将宿主初始选择当作完整personality支持。

## 本地模型指令模板：1284通过、1文件系统跳过

本地model_messages的template/variables进入有限ModelInstructionTemplate，默认
渲染经builder接入真实Runtime，模板进入ModelSettingsSnapshot JSON和checkpoint。
先4Runtime红例9b2802；模板渲染绿后checkpoint再4红e824b0，精确类型白名单修复。
完整unit/config、unit/context、unit/core及local_instruction_template/
model_instruction_lifecycle/base_instruction_override/base_instruction_recovery共1284
通过、1文件系统限制跳过、9.02秒（bd9b51，83182退出0），4workers/loadfile/
禁重启，null keyring；Ruff2ad447。当前无活动测试；本次生产改动未刷新全量。
仅模板默认选择已接入，动态personality与typed section仍待实施，见专项新顶部。

## 多待写真实进程退出：51关联通过

新增pending_terminal_process_recovery以独立进程执行三Turn，保留两条待写后
os._exit(73)绕过正常清理。父进程重开数据库/Thread，禁止采样仍恢复两原结果，
第三次resume为空，原始历史全等且无RUNNING。单项fecc4a通过；与待写准入/
恢复/确认/告警/准入六文件共51通过、4.77秒（d1f116，91198退出0），4workers/
loadfile/禁重启，null keyring；Ruff85e29b。本轮无生产修改、无活动测试。
上一轮1094扩大仍有效于生产代码，但不包含本新增用例；48797全量早于准入修复。

## 新Turn待写降级：1094通过

新双Turn/SQLite反例4c5a2a先复现旧终态补写阻止第二个Turn；_start_turn现在
仅捕获明确可恢复旧终态存储错误，保留pending并在新Turn Started后发Warning。
新任务自身提交、非可恢复故障、取消、resume与关闭屏障不降级。关联五文件50
通过9bc973；完整unit/storage、unit/core、integration/test_*recovery*.py以及
pending_terminal_admission/terminal_storage_warning/terminal_confirmation/turn_admission/
runtime_shutdown/interrupt_shutdown/interrupt_terminal_retention共1094通过、61.96秒，
4b3056确认52863退出0。4workers/loadfile/禁重启，null keyring/当前sandbox编译器。
Ruff e1c64e，diff c4e7f2。本批结束，无活动测试；48797全量仍是这两轮生产修改
前的绿色基线。跨进程多待写/压缩/Hook交错另核，详见partial-output-acceptance.md。

## 取消终态确认修复：442通过，全量基线在修改前

真实取消且CANCELLED实际提交后抛ack错误的新增反例先失败798b39，暴露取消
分支跳过精确确认、残留cleanup_error。Runtime现对取消也读回确认；保持取消事件
与CancelledError、确认Warning先于终态，无重复采样。关联114通过da9a3b；
完整unit/storage、unit/core及terminal_confirmation/terminal_storage_warning/
terminal_pending_recovery/turn_admission/interrupt_shutdown/runtime_shutdown/
cancellation_intent_recovery/interrupt_terminal_retention共442通过、10.65秒，
fcfa20输出汇总，07426a确认85612实际退出0；4workers/loadfile/禁重启，null
keyring/当前sandbox编译器。Ruff8bd701，diff检查a8c6ee。当前无活动测试。
下方15328全量通过为本次一行生产修复前基线，不称当前版本已重新跑过全量。

## 最新全量已结束：15328通过，1文件系统限制跳过

48797原进程实际退出0（b482fd），526.57秒，15328 passed / 1 skipped。
完整累计输出已保存full-regression-48797.txt；跳过项为
tests/unit/config/test_project_trust_selection.py:164，文件系统拒绝非UTF-8文件名。
范围与下方启动记录一致：非CLI的全部unit/integration，8workers/loadfile，
禁worker重启，六份sandbox编译器环境；本批运行期间未修改被测生产或测试。
64868的21失败、74206的34失败保留为历史证据，不改写旧日志；当前结果验证
终态告警实现及持续存储故障fixture修正后的完整回归，不等于A–E所有差异已关闭。
下一步回到活跃验收清单，先核对持续待写下的准入与原生继续执行语义。
原记忆数据库锁故障本批未复现，不宣称已定位或修复。

## 已结束批次的启动记录：终态告警与持续故障fixture之后

session48797已启动（d3eb6a），实际45秒轮询c2f7d9到6%，仍活动、无退出码、
未见失败。继续同句柄，不重启或修改被测生产/测试。累计输出与最新结果保存在
functions stores warning_full_output / warning_full_result。64868/74206等旧批已结束。
启动前pgrep未发现pytest，当前及五份历史sandbox编译器逐路径检查存在（34478b）。
tests/unit tests/integration，排除tests/unit/cli和tests/integration/test_cli*.py；8workers/
loadfile/禁worker重启，-o addopts='' -q --tb=short -rs，null keyring，与上次全量
相同六份编译器环境。未另外启动测试批次，1092扩大通过不能替代本批最终结果。

## 终态告警扩大验证：1092通过；全量待刷新

首次扩大34失败/1058通过（095a3d，74206退出1，61.58秒），完整输出
warning-regression-74206.txt。失败三文件的注入仅拦save_turn，新的即时retry已
提交终态，导致原RUNNING断言失败。逐处让同一存储故障覆盖retry_turn_terminal，
同时核对并更新此前全量失败的另五份同类fixture。原冷恢复/取消/副作用/指令/
普通wire断言保留；encrypted_tool_output明确检查原internal历史提交错误，而非
被终态存储失败遮住。没有修改生产或跳过测试来取得绿色。
八文件137 passed/29.40秒（020468，97951退出0）。随后原扩大完整相同范围
1092 passed/62.69秒（6fb5ed，68161退出0）：完整unit/storage、unit/core、全部
integration/test_*recovery*.py，加terminal_storage_warning/terminal_confirmation/
turn_admission/runtime_shutdown/interrupt_shutdown/interrupt_terminal_retention。
4workers/loadfile/禁重启，null keyring/当前sandbox编译器；8文件Ruff/格式15a035。
当前无活动测试，下一步刷新非CLI全量；64868的15303通过/21失败仍是历史原结果。

## 终态告警首批：新四红转绿，关联40通过，扩大范围待执行

Runtime终态写入可恢复错误且已拥有record时，发终态前先有限排空；成功确认或
继续待写均单独Warning，保留原任务成功/失败，不把存储健康混作模型认证错误。
初始未准入/冲突/非可恢复错误排除；其他cleanup_error不清除，队列不是落盘证明。
新四组合（任务成功/认证失败×补写可用/不可用）先4失败8d2df3，接入后连既有
confirmation和存储22通过35efc3，bc2ee7确认56648退出0；静态f738fe。
旧pending/admission22失败14通过f59b5a；故障fixture扩到即时排空、预期改成原
任务结果，但后续恢复/冲突/取消/join/次数/副作用断言保留。三文件最终40 passed
5.36秒（12f86d，28379退出0）。本次未运行扩大恢复/全量，旧crash fixture仅拦
save_turn可能被新的即时retry补写，下一轮须核查并验证，不宣称回归全绿。

## 最新全量已结束：21失败/15303通过；五文件定向87通过

64868实际退出1（52c421），530.06秒，15303 passed/21 failed/1 skipped，完整
累计输出full-regression-64868.txt。唯一skip为非UTF-8文件名平台限制。禁止再按
下方活动状态轮询64868或重启同批。持续跟进原句柄至退出，期间未改生产/测试。
失败均为冷恢复返回空后的IndexError：thread_settings_update 1、encrypted_tool_output
12、interrupt_history 2、local_history_notes 2、function_output_policy 4。
逐处核对故障fixture：注入终态save失败后调用正常aclose，现在会被待写屏障补写，
原模拟不再留下RUNNING。五处显式清除进程内队列以模拟崩溃丢失内存，再关闭资源；
保留原指令、普通HTTP wire、取消标记、笔记副作用和工具结果恢复断言，未跳过测试。
这仍是内存丢失模拟，不宣称实际进程kill。未修改生产以绕过这些恢复测试。
五文件全部87通过（db2a32，9.27秒，3455退出0），4workers/loadfile/禁重启，null
keyring/当前sandbox编译器。本次修正后全量尚未重跑，不能把21失败原日志改称绿。
下一步终态告警语义；以后再刷新全量。原记忆锁错误本批未出现，不等于已定位修复。

## 当前活动全量：终态待写/有限重试/读回所有权之后

最近实际轮询98e03f到25%，64868仍活动、无退出码、未见失败。累计输出已更新
pending_full_output；继续同进程，不重启。只读原生通知边界与当前实现审计已更新
partial-output-acceptance.md新顶部；没有修改被测生产/测试。

session64868已启动（00154f），实际45秒轮询c06c62到7%，仍返回活动句柄，未有
退出码、未见失败。继续同句柄，不重复启动或修改生产/测试。累计输出与最新结果
存functions stores pending_full_output / pending_full_result。旧79636已退出1，勿轮询。
范围tests/unit tests/integration，排除tests/unit/cli和tests/integration/test_cli*.py；
8workers/loadfile/禁worker重启，-o addopts='' -q --tb=short -rs，null keyring；当前
及五份历史sandbox编译器均逐路径确认存在（be716d），与79636相同环境。
本批是这几轮终态生产改动后的全量刷新，437定向通过不能代替它的最终结果。

## 终态读回故障所有权：437通过

三红例0ac83a证明确认/状态双读失败会丢失运行期待写record。现在依据已成功准入
或已验证resume保留内存payload，后续事务重查身份/终态；不把保留当成已提交。
扩8组合含RUNNING、同终态、冲突、初始准入未成功，状态读成功/失败；原冲突不
覆盖，未准入不建待写、不采样，恢复不重复模型。关联44通过6dddc6；完整
unit/storage+unit/core及终态/admission/runtime与interrupt关闭/cancellation_intent/
interrupt_terminal_retention：437 passed/10.33秒（1c24f8，87594退出0），4workers/
loadfile/禁重启、null keyring。Ruff/格式d3aa8c。下一步全量刷新，宿主告警语义仍开。

## 待写有限重试：429通过

共用终态屏障现在仅对OSError和SQLite明确存储故障主码再试一次，保留未完成
队列；冲突/SQL错误/取消不重试，第二次错误直接传播。真实提交前/后错误10组合
先4失败6通过（b9c05c），实现后定向46通过（3e6f8c，5.03秒）。持续失败关闭
fixture新增严格2次尝试断言，不循环至成功。扩大完整unit/storage+unit/core及
terminal_pending_recovery、terminal_confirmation、turn_admission、runtime_shutdown、
interrupt_shutdown、cancellation_intent_recovery、interrupt_terminal_retention：
429 passed/10.45秒（75acb2，37047退出0），4workers/loadfile/禁重启、null keyring。
Ruff通过c58f78，格式已执行。全量尚未刷新，读回失败所有权与宿主告警映射仍开放。

## 待写终态准入/恢复屏障：1070通过

_flush_pending_terminals共用于_start_turn、resume_pending和关闭存储；新任务准入
或读取恢复账本之前先排空旧结果。两确定性反例063fee证实旧代码无此屏障。
真实线程门闩验证补写失败不产生新输入/采样，取消调用者仍join写线程；提交已完成
但确认被取消时队列保留，下一次原子幂等确认，不重复模型或旧Turn执行。
定向18通过bb904d，静态通过42e322。

扩大首次34失败/1036通过（fc898e，95757退出1，61.87秒），分布在interrupt_recovery
24项、base_instruction_recovery 4项、managed_policy_recovery 6项。旧崩溃fixture
正常aclose会排空队列，冷恢复因此为空；已显式模拟进程丢失内存待写队列，仅随后
释放fixture资源。原冷恢复终态、指令/政策、取消、Hook及不重复副作用断言未放宽。
这些是内存丢失模拟，不冒充实际kill进程验证。三文件50通过e924d5。
完整相同扩大范围重跑1070 passed/61.23秒（586982，67405退出0），4workers/loadfile/
禁重启，null keyring/当前sandbox编译器。6文件静态/格式通过3b8a79。
范围：完整unit/storage、unit/core、integration/test_*recovery*.py，加terminal_confirmation、
turn_admission、runtime_shutdown、interrupt_shutdown、interrupt_terminal_retention。
全量非CLI尚未刷新；读回失败时待写所有权、每屏障重试策略与宿主告警语义仍开放。

## 终态待写关闭首批实现：417项通过，全量待刷新

Runtime保留已独立确认仍RUNNING的失败终态，在关闭存储之前重试；SQLite
BEGIN IMMEDIATE核对身份/终态，拒绝缺失或冲突，幂等确认相同结果，不改准入字段。
失败保持_close_storage_pending和队列，重复close仅排空存储阶段。存储新2红例
4f7d68；接入后旧冷恢复正常close补写导致2失败fd641c，已区分内存队列丢失模拟
与真实正常关闭重试验证，未删除冷恢复结果/不重采样断言。
34通过e2a57a；完整unit/storage+unit/core及终态、admission、runtime/interrupt
shutdown、cancellation_intent、interrupt_terminal_retention：417 passed/8.94秒，
4workers/loadfile/禁重启，65731退出0（e30ef3），null keyring；静态/格式通过。
本次生产改动后全量未刷新。关闭之外排空、读回失败、取消及原生告警语义仍开放，
详见remaining-implementation-priorities.md新顶部。未宣称A–E完成，CLI暂停。

## 数据库锁诊断：同范围837通过，但原失败根因未确认

失败测试增加DiagnosticConnection.execute采集实际SQL、sqlite_errorname与异常栈，
主Turn断言失败时一并展示；使用原SQLite连接子类，不修改事务/超时/重试策略。
定向shutdown_deadline 11通过（f18916，4.69秒，99062退出0，4workers/load）。
随后与上一失败批相同的unit/memory+integration/test_memory*.py+long_term_memory
837通过（6c6008，66.77秒，71784退出0，4workers/loadfile，null keyring/当前
sandbox编译器）。两批严格先结束再执行，运行中无生产/测试编辑。

原277717的database is locked没有再次复现，不能称已修复。现有会话/记忆连接
busy_timeout均10000；checkpoints独立文件且setup已有仅bootstrap BUSY重试，
不足以把原错误归因于某个模块，不能盲加整Turn重试或自动重放工具副作用。
诊断目前捕获execute路径，不声称覆盖executemany/executescript/commit。
这次仅测试诊断修改；待后续出现具体SQL与错误码再做定向交错回归。保留开放锁风险，
不再以连续无差异重跑代替进展；接着推进已确认的终态待写所有权核心差异。

## 记忆诊断更新：流终态测试计时隔离，扩大回归暴露数据库锁失败

test_ownership的故障注入仅延迟ensure_layout 1.1秒，旧测试1秒whole-pass限制
确定性失败（a813b1：1失败/1通过），且model started=False/closed=False。
说明旧计时会在目标流尚未开始时失败；不能据此反推79636原超时的确切阶段。
现在保留模型启动后1秒关闭限制，准备和发布采用独立10秒看门狗，并在yield终态
之后直接AssertionError禁止多读，替代无限等待；成功报告和closed断言仍保留。
增加慢准备参数及finally取消/join/aclose。没有修改生产或放宽流关闭断言。
原文件14通过（e440bc，6.78秒；77c274确认session10556退出0），Ruff/格式通过。

扩大unit/memory、integration/test_memory*.py和test_long_term_memory.py，4workers/
loadfile/禁重启、null keyring/当前sandbox编译器：836 passed、1 failed、66.47秒，
session89191退出1（277717）。新增失败是test_memory_shutdown_deadline的
close-cancelled参数，在runtime.stream("main")预期TurnCompleted处实际TurnFailed
OperationalError: database is locked（internal），尚未进入目标关闭断言。
这不是原1秒流关闭失败，必须独立追踪主Runtime与后台记忆并发数据库操作，不能
通过串行绿色掩盖。当前无运行测试，下一步优先定位此锁故障；全量未宣称通过。

## 最新全量终态：15298通过、1失败、1跳过；先排查记忆超时

session79636已实际退出1（731230），534.03秒。8workers、非CLI单元与集成范围
及环境沿用下方启动记录；不能再按下方活动状态轮询。输出保存在
full-regression-79636.txt：包含完整失败栈和终态摘要，但13%–73%的进度点输出
在fb1937工具返回时被截断，日志已显式标记缺口，不称为完整原始输出。

唯一失败test_memory_completed_is_terminal_and_closes_stream：wait_for包住整个
service.run_once，timeout=1，最终TimeoutError；尚无证据断言是生产回归或仅资源争用。
全量结束后原文件未修改、串行诊断13 passed/3.67秒（b6dbdd，session2208退出0），
该例call耗时0.61秒。串行通过不消除原失败，不作为修复证明。现有源码pipeline.py
的提取流在ModelCompleted后break且使用aclosing；仍需定位本例走到的实际合并链路
及超时所处阶段，区分准备/模型终态/发布清理耗时。不得直接放宽超时或静默重跑。
下一步先完成这个失败的故障定位，再继续终态待写所有权；CLI暂停。

## 当前活动全量：政策纯读取与终态确认之后

最近实际45秒轮询eaf08a到12%，session79636仍活动，无退出码、未见失败，累计
输出已保存terminal_full_output。后续继续同句柄，不重启或修改被测代码。

session79636已启动（10fa35），实际轮询到2%（9c1c74），未终态、未见失败。
tests/unit tests/integration，排除tests/unit/cli与tests/integration/test_cli*.py，
8workers/loadfile/禁worker重启，-o addopts='' -q --tb=short -rs；null keyring，
当前及五份历史sandbox编译器逐路径存在已检查（b07721）。所有批次共享本批8worker，
未开额外测试进程。functions stores：terminal_full_result / terminal_full_output。
继续同句柄到实际终态；不在运行中修改生产/测试。27039等旧批已结束不要再轮询。

## 无关贡献者阻断恢复修复后的回归

ContextBuilder纯政策片段被正常build与冻结恢复共用，恢复不再执行全套贡献者。
原两故障注入红例修复，四文件+context442通过（858c46）。非CLI全单元加宿主政策
生命周期、普通HTTP wire和真实记忆worker上下文：5932 passed/1文件系统skip
（924fab，23.42秒），session54942在摘要输出后继续清理，最终3c34f5确认退出0；
未在清理期间修改生产/测试或重启。8workers/loadfile/禁worker重启，null keyring
和已验证sandbox编译器；Ruff/三文件格式及diff检查通过（fffd66、ab3b15）。
详见managed-developer-instructions-gap.md最新段。下方15281全量为这次生产改动前。
下一步回到其他section压缩保留与终态协调的开放需求，不再将无关贡献者故障
称为仅猜测或完全未验证；当前最新集成全量仍未刷新。

## 最新全量终态：15281通过、1文件系统跳过

session27039已退出0（755204），523.52秒，完整输出full-regression-27039.txt。
单元与集成非CLI同批8workers/loadfile/禁worker重启，范围及环境见下方启动记录。
唯一skip：test_project_trust_selection.py:164文件系统拒绝非UTF-8文件名。
此前90904五失败的冻结环境/Shell/工具结果恢复分支在本批完整覆盖后通过；其原
失败日志保留，不改写为历史全绿。本轮持续观察同句柄到真正退出，没有重启测试
或在运行期间修改生产/测试。下方27039活动记录现在仅为历史，禁止再轮询或重启。

本结果覆盖当前政策刷新门槛、非政策快照/搜索结果冻结及新增日期/catalog/Shell
组合，但不代表所有A–E缺口已关闭。下一步用确定性故障验证恢复完整builder是否
会被无关贡献者阻断；其他section压缩保留与A4/E6/E7终态协调依次继续，CLI暂停。

## 当前活动全量：冻结恢复修复后的单元+集成

实际轮询17a0b6到2%，再45秒dd2b78到8%，session27039仍返回活动句柄、没有
退出码，输出未见失败。累计输出已存frozen_full_output，下一轮继续同进程。

session27039已启动（438bc0）；tests/unit tests/integration，排除tests/unit/cli和
tests/integration/test_cli*.py，8workers/loadfile/禁worker重启，-o addopts='' -q
--tb=short -rs。六个当前/历史sandbox编译器路径均存在已确认（63a5be），null
keyring；无其他活动pytest。当前同一批次共享8workers，不把单元/集成各开一组。
functions stores：frozen_full_result / frozen_full_output。继续同进程至终态，不在
运行中改生产或测试。90904已退出1，不能再轮询；1089定向通过不能替代本次全量。

## 冻结恢复组合扩大：1089通过（本轮无生产修改）

test_deferred_tool_search原九种账本/checkpoint/catalog组合加入宿主新政策开关，
共18通过（276a91，6.64秒）。每个恢复请求政策恰一份；冻结首请求仍是旧描述/
schema/搜索结果，旧定义调用被拒后才重新搜索，旧搜索不执行，最终工具副作用
仅一次、canonical旧发现仍保留。test_runtime_environment_context的日期checkpoint
加入政策变化开关，两例通过（fd2ec9，0.95秒）：过滤仅新增政策后请求逐项仍与
原冻结请求相等，后续新Turn才刷新日期，未放宽原日期断言。

扩大运行test_*recovery*.py（文件名检查无CLI文件）、selected_shell_runtime、
deferred_tool_search、runtime_environment_context与完整unit/context：1089 passed
（b2cd88，67.22秒，session90439退出0），4workers/loadfile/禁worker重启；
Ruff及格式通过（4a527a）。没有在进程仍活动时编辑生产/测试，未静默重跑失败。
本结果覆盖上述确定性恢复契约，不证明真实模型选择质量、所有section恢复/压缩
组合或全量已绿。下一步刷新非CLI全量；仍需审计恢复调用完整builder引入的
非政策贡献者读取/失败，以及冻结快照在压缩中对其他section的保留契约。

## 恢复冻结回归修复：定向52通过，全量待刷新

graph._refresh_recovered_context现对照冻结request_items判断宿主政策是否变化，
无变化原请求原样恢复；不是只对照journal，避免刷新已提交/checkpoint未提交时
漏掉政策更新。政策变化时使用原请求非input上下文的最后快照（模式按共享section
归并），仅替入新政策；预算/压缩仍调用window.prepare。已保存ToolResult按id保持
原冻结投影，不再按冷Runtime新catalog提前清空搜索发现结果。
原五红例及四文件50通过（7e773f）；追加政策变化×Shell变化两红例（0fd1c3）
后收窄快照/结果投影，最终四文件52 passed（724cd8，25.31秒，退出0），4workers。
中间误用ContextItem.is_input_context引发12失败（82efaa），进程13092退出1后
改用现有is_input_context函数；保留这一真实失败记录。Ruff/格式通过（32af6d）。
这只覆盖当前四文件，不把下方全量五失败直接改写为全量已绿。下一步补政策变化
与日期、搜索catalog变化组合，以及压缩/其他section快照交错，再扩大恢复回归。

## 最新终态：集成5失败/9353通过，须先修复恢复回归

session90904已退出1（461d1d），506.41秒；完整输出integration-regression-90904.txt。
禁止继续按下方活动状态轮询或重复启动。失败：test_selected_shell_runtime的
cold_checkpoint两种工具模式、test_runtime_environment_context的冻结日期恢复，
均出现额外environment.primary；test_deferred_tool_search的description/schema
×append_result=True×checkpoint=True两例，旧冻结搜索结果discovered_tools被清空。
这批全量不能称通过；既有unit5911通过/1跳过不抵消五个集成失败。

当前graph._refresh_recovered_context不区分宿主政策有无变化，统一build世界状态并
以当前specs重投影历史，直接改变已保存request_items。下一步优先约束刷新边界：
无政策变化保持原冻结请求；政策变化须增量更新政策并重新预算，但不能借此刷新
日期/Shell/旧工具定义或破坏原准入快照。应补政策变化与这三类冻结契约组合反例，
而不是只修改旧测试的expected值。终态写入协调优化暂后移。

## 当前活动批次：恢复刷新改动后的非CLI集成全量

续轮实际轮询11294f到30%，再45秒9d9289到43%，session90904仍活动，无退出码、
未见失败，累计输出已更新。继续此句柄，不修改被测生产/测试，不重复启动。
本轮只读核对终态写后报错分支，结论记录partial-output-acceptance.md对应段。

最近实际45秒轮询0bf4fd：session90904到23%，仍返回活动句柄、无退出码，未见
失败。累计输出已存recovery_full_output；下轮继续同进程，不将本轮等待视为终态。

session90904于本轮启动（893630），真实轮询至6%（eadaf6），尚未终态，不能计作
通过。继续此句柄，不启动重复批次、不在运行中修改生产或测试。
functions stores：recovery_full_result（最近返回）/recovery_full_output（累计输出）。
命令范围tests/integration，--ignore-glob='tests/integration/test_cli*.py'，-n 8，
--dist=loadfile --max-worker-restart=0 -o addopts='' -q --tb=short -rs。
六份当前/历史sandbox编译器均逐路径确认存在（2fa246），环境沿旧30649批次，
Keyring使用null后端；12逻辑CPU/18GiB按既有隔离分组选择8workers而非固定96。
最新单元5911通过/1文件系统跳过，连同恢复专项14例合计5925 passed（3766fb，
30.54秒，进程30797退出0）。旧30649、55650等已终态，禁止按下方旧文字再轮询。

最新生产增量：Guardian策略省略误发撤销的一红例已修复，普通缺省撤销对照保留。
宿主ContextSnapshot以omitted_sections传递不参与比较的section，不删除旧历史，
关联400 passed（e2ad91，7.10秒，退出0），Ruff通过。详见宿主策略专项最新段。
下方15252通过/1跳过的全量是此次变更前证据；不重启旧30649，后续按需刷新。
下一步中途工具压缩/恢复交错，随后回到A–E开放清单，不继续扩张Guardian产品范围。

## 当前全量终态：集成9341通过，单元5911通过/1跳过

session30649已退出0（3db7ca），9341 passed、506.75秒，无失败或跳过，完整输出
integration-regression-30649.txt。结合非CLI unit5911通过/1文件系统跳过（c4e45a），
合计15252通过、1跳过；两批8workers/loadfile/禁自动worker重启，运行期间未改
生产或测试。此前各处“活动/待刷新/未终态”现在是历史记录，不再轮询或重启30649。
当前全量覆盖近期模型来源与宿主developer策略改动，不证明A–E所有缺口已关闭。
下一步恢复Guardian section省略与普通政策撤销的差异反例，沿宿主策略专项继续。

## 最新全单元与活动集成（优先于下方历史记录）

续轮已确认原session30649仍活动：15%（b7e5cc），一次真实45秒等待到26%
（d1aea3），再45秒到34%（66afce），均仍返回session_id、无退出码，输出未见失败。
本轮为已验证等待，没有重启任务或修改生产/测试。继续同句柄，functions累计输出
已同步managed_integration_output；不能以观察超时或阶段进度当作终态。

非CLI全部unit：5911 passed、1 filesystem非UTF-8文件名限制skipped，22.01秒，
session95676终态退出0（c4e45a）。8workers/loadfile/禁worker重启，空keyring与
已验证当前sandbox编译器；覆盖近期模型和宿主developer策略生产增量。

非CLI集成已启动session30649（8c2453），排除test_cli*.py；当前真实轮询到5%
（afb645），无终态，不能视为通过。仍为8workers，全机12logical CPU/18GiB，
六份现行/历史sandbox编译器逐路径存在已确认（02ac80），环境同上次集成批次。
functions stores: managed_integration_current / managed_integration_output；后续继续
同句柄，不重启、不在其运行中修改生产或测试。没有使用96作为固定worker数。

最新宿主策略增量：旧developer政策四红例修复，Unknown协调及缺省撤销只追加一次；
自动/手动压缩的typed请求重建两例通过，无需额外压缩生产修改。关联450 passed
（d9b27a，7.82秒，退出0），4workers，静态/格式通过。详见managed-developer-instructions-gap.md。
全量仍待刷新；下一步内部已有历史/记忆worker传播及普通HTTP wire，A–E未闭合。

最新生产增量：基础模型来源回退补上“基础正文不同于当前模型指令”门槛，
与原生 session/world_state.rs 一致；没有已接受Turn且同正文时不误发切换，
真实上一模型不同仍发。四组合一红（508a16）转绿，关联462 passed（618b64，
8.21秒，退出0），4workers/loadfile，Ruff/格式通过；细节见model-instructions-gap.md。
本轮与旧标记修复后的全量尚未刷新。A–E仍未闭合，下一批应回到开放清单的
上下文 typed sections/恢复交错与终态故障语义，不继续扩张同正文配置排列。


## 最新增量：旧模型标记恢复已验证（全量结果为此次变更前）

Codex 基准仍为 ddf04ad26789d040f9ef6a96736f76602e35a6cc；
core/src/session/rollout_reconstruction.rs 的 TurnContext 独立恢复 previous_turn_settings，
context/world_state/model.rs 在 section 缺失时根据 previous_model 判断切换。
Corki changed_context_items 现复用 harness.previous_model 的既有解析，读取可信内部
快照中的上一模型；该身份跨压缩保留，不新增持久化记录、不重写旧历史。
旧无 model.instructions 的冷恢复 × 压缩/未压缩 × 继续 small/切回 large 四例，
此前四失败（1162c8），现关联 452 passed（2c46ea，7.57秒；d2966a 确认退出0）。
原 session30239 已不存在且无活动 pytest；未将丢失结果视为通过，本次重新验证。
追加 accepted/user/input_attached/visible/malformed/other_key 六项来源边界，
只有可信内部快照可触发切换；专项共50 passed（2df2c0，7.59秒，退出0），Ruff及
三文件格式检查通过。两批4workers/loadfile/禁止worker自动重启，小范围不启96并发；
本轮测试执行期间未修改生产或测试。无官方服务调用，也未推进CLI对齐。

下方15214通过/1跳过属于此次生产增量之前的全量证据，不代表当前全量已刷新。
下一步仍需核对完全缺少可信上一模型信息的旧历史边界，并回到A–E开放清单；
本组只证明已有旧标记的恢复，不把未知历史猜测成可恢复，也不宣称整体对齐完成。


## 最新终态：unit5894通过/1跳过，integration9320全部通过

session55650已实际退出0（587b02）：9320 passed，491.82秒，无跳过或失败。
完整输出见integration-regression-55650.txt；functions store中的
base_integration_current为终态，不再轮询或重启该句柄。
tests/integration排除test_cli*.py，8workers/loadfile/禁自动worker重启，空keyring
及六份已验证本地sandbox编译器。全过程未修改生产/测试。历史混合测试可能含
CLI适配断言，不把文件排除误称为纯核心行为分类。

结合当前同一生产版本unit5894通过/1文件系统跳过（ef29c0），合计15214通过、
1跳过，覆盖近期基础指令来源、显式覆盖、压缩、fork、Turn账本与恢复冲突校验。
这证明当前回归通过，不证明旧无model section前态和A–E所有开放项已经实现。
下面“活动/待刷新”均为历史过程记录；CLI对齐仍按用户要求暂停。

## 当前活动集成回归：session55650，尚未终态

此前无活动pytest进程，当前及五份历史sandbox编译器均逐路径确认存在后，启动
tests/integration，排除test_cli*.py，8workers/loadfile/禁worker自动重启，空keyring
与既有六编译器环境。首call43dff2返回session55650；两次真实45秒等待推进至
12%（48c59e）、23%（71ec50），无退出码，目前未见失败，不能称为集成通过。
禁止重复启动，后续继续轮询55650；运行期间不改生产或测试。句柄/累计输出保存
functions store：base_integration_current / base_integration_output。
本次覆盖近期模型指令、来源、压缩、覆盖和恢复账本生产改动；全单元结果仍如下。

## 最新全单元：5894通过/1文件系统跳过；集成全量待刷新

session33006终态退出0（ef29c0）：tests/unit排除tests/unit/cli，5894 passed、
1 skipped，22.39秒。8workers/loadfile/禁自动worker重启，空keyring和当前本地
sandbox编译器；全过程未编辑生产或测试。-rs确认项目信任测试的非UTF-8文件名
文件系统限制。包含本轮账本/checkpoint前缀冲突校验及近期基础指令实现。

冲突校验两红例修复，关联254 passed（80f192，7.35秒）；细节见专项。
下方历史全单元/集成仅为旧证据，尤其旧9261集成不能证明当前新增生产变更全绿。

## 首checkpoint前基础指令账本：474/182两组通过

普通/手动压缩×空/非空四反例修复：TurnRecord与SQLite记录准入基础，无checkpoint
恢复沿账本，手动摘要也沿固定值。关联474 passed（ebf769，7.23秒）；storage/fork/
恢复182 passed（00a088，7.27秒），4workers/loadfile。新增账本值断言后专项串行
8 passed（bfd0f9，2.05秒），Ruff/格式通过。均退出0，未刷新全量；旧缺失字段与
已有checkpoint冲突校验仍开放，见model-instructions-gap.md。

## 工具后冷恢复基础指令：468项扩大通过

有效反例为工具完成后恢复的下一prepare错误改用未来宿主基础（7186f1）。
graph/state新增固定Turn前缀，兼容旧准备好的request checkpoint；关联六文件/目录
468 passed（b04132，7.82秒，退出0）。随后非空/空串×两个checkpoint位置四例
专项通过（202a77）；最终Ruff通过（e23f75）。均4workers/loadfile，未刷新全量。
首prepare前账本边界仍未由此验证，见model-instructions-gap.md。

## 旧来源精确推断：442项通过

NULL/Custom×匹配/不匹配四例原一失败（6fc105）。builder初始化现在仅对NULL来源
且文本与当前模型完全相同的情况推断当前Model，不改存储。关联覆盖/生命周期/
fork/context共442 passed（44b9ae，7.89秒，退出0），4workers/loadfile；未全量刷新。
无基础行、旧Turn缺section及待恢复Turn覆盖仍见model-instructions-gap.md。

## 基础指令文件入口：687通过/1文件系统跳过

相对/绝对路径优先级与四文件错误反例原六失败（e0637f）；接通配置层路径解析和
初始化前UTF-8内容读取后，覆盖/模型生命周期及全部unit/config共687 passed、
1 skipped（834ebd，7.04秒，退出0），4workers/loadfile，-rs确认文件名平台限制。
普通请求和摘要使用固定解析值，Ruff/diff-check通过（049e75）。本轮生产变更
未全量刷新，完整剩余来源与恢复契约见model-instructions-gap.md。

## 显式基础覆盖及来源修复：1109通过/1跳过

原四覆盖红例已转绿；新增恢复移除覆盖、新建持久 Custom、同库/跨库 fork 继承或
覆盖及非法类型断言。扩大 tests/unit/context、tests/unit/config 和相关七个集成文件：
1109 passed、1 skipped（7ee319，8.89 秒，退出0），4 workers/loadfile；未全量刷新。
后续定向 project_trust_selection：36 passed、1 skipped（af7654，0.11秒），
-rs 明确为文件系统拒绝非 UTF-8 文件名；最终 Ruff/diff-check 通过（37053b）。
生产改动含配置、SQLite来源、builder及fork；剩余来源/恢复边界见专项。
下方四失败段为修复前历史记录，不代表当前这四例仍失败。

## 当前已知失败：显式基础 instructions 覆盖未接入

test_base_instruction_override.py 新建/恢复 × 非空/空串四例均失败（a1c9ee，
2.50 秒，退出 1），实际普通请求仍为 CATALOG BASE。4 workers/loadfile；没有
修改生产代码或掩盖失败。下方452等通过是此前范围的结果，不是当前全量全绿。
下一步连同 Custom 来源和恢复/fork 契约实现，见 model-instructions-gap.md。

## 模型指令与工具后轮内压缩：452 项通过

新增未切换/本轮刚切换模型的真实工具后阈值压缩两例：无重复 model_switch，
摘要接收调用/结果，当前输入保留、工具仅执行一次。本轮只加测试，无生产变更。
关联生命周期、手动压缩/恢复、线程设置及 unit/context 共 452 passed（08ccbf，
6.88 秒，退出 0），4 workers/loadfile。此前自动压缩生产改动仍未全量刷新。

## 自动压缩模型差异重注入：450 项通过

UsageModel 真实阈值自动压缩两反例原失败（147f46），window.prepare 重注入现保留
模型 section 的渲染/快照对。关联生命周期、手动压缩/恢复、线程设置、unit/context
共 450 passed（e2a47d，6.53 秒，退出 0），4 workers/loadfile。生产改动未刷新全量；
仅新增 pre-turn 自动路径证据，mid-turn 等完整边界见 model-instructions-gap.md。

## 压缩后模型前态修复：448 项定向回归

连续普通压缩后继续 small/切回 large × 热/冷恢复四反例原均失败（3208f5）。
world_state 现从完整历史保留最近模型身份作无 section 时的比较回退；修复后
生命周期、手动压缩/恢复、线程设置和 unit/context 共 448 passed（50546d，
6.27 秒，退出 0），4 workers/loadfile。本轮有生产变更，尚未刷新全量。
旧无 section 历史及自动压缩等剩余边界见 model-instructions-gap.md。

## 外部重复取消基础写入：专项扩大回归

基础写入提交前/后由线程闸门暂停，重复取消初始化等待者，验证等待线程收尾、
writer 不提前释放、无采样及冷开继承真实提交结果。新增两场景，无生产变更。
model_instruction_lifecycle、runtime_initialization、runtime_shutdown 共 72 passed
（82a62e，5.48 秒，退出 0），4 workers/loadfile；Ruff/格式检查通过（84da02）。
完整目标仍未完成，压缩后模型前态继续见 model-instructions-gap.md；未刷新全量。

## 基础指令初始化故障定向验收

新增提交前/后 × OSError/CancelledError 四个真实 Runtime 恢复场景，无生产改动。
专项 12 通过（dc385b）；结合 runtime_shutdown、thread_settings_update、unit/context
共 446 通过（ef3a43，5.50 秒，退出 0），4 workers/loadfile；Ruff/格式检查通过。
验证无提前采样、失败初始化释放 writer、冷恢复使用实际提交的基础指令。
外部取消进行中的线程写入未由本组覆盖；完整缺口见 model-instructions-gap.md。
未刷新全量，以下历史全量结果不代表新增模型指令实现的当前全量覆盖。

## fork基础指令新修改后的定向回归

同库/跨库/临时目标与用户前截断六红例修复；ForkSnapshot及发布事务现在包含基础
指令来源，扩大五文件41通过（eaa754），含新增表在事务失败/提交后报错的状态断言。
未刷新全量；完整模型指令生命周期继续见model-instructions-gap.md。

## 最新定向：模型基础指令两红例转绿

本地catalog字段、基础指令表、Runtime/builder和模型切换section已有生产变更。
原test_model_instruction_lifecycle两参数通过（f17026），扩大483通过（ba87c3），
另配置值8通过（714370），静态检查通过。未重跑全量；fork新表继承及完整模型
指令生命周期仍按model-instructions-gap.md推进。下方两红例状态保留作修复前记录。

## 当前已知红例：本地模型指令生命周期

新增test_model_instruction_lifecycle.py两参数真实Runtime均在首请求基础指令断言
失败（859077，2 failed，0.73秒），配置字段尚未接入。未改生产，未跳过或标记xfail；
后续切换/冷恢复断言还未执行。需继续model-instructions-gap.md中的初始化与
普通采样/手动压缩共同接线，不能将下方历史全量称作当前测试树全绿。

## 全量后的新修改：旧模式片段协调

world_state新增旧collaboration_mode片段的Unknown识别，专项及完整context联合
429通过（013989），静态检查通过。下方15143通过的全量早于这次生产变更；
未重新启动全量，不把旧回归冒充当前新修改的全量结果。详情见context-snapshot-wire.md。

## 当前终态：unit 5882通过，integration 9261通过

session 61920 已真实退出0（ff3f49）：9261 passed，515.72秒，无失败或跳过。
完整累计输出见 integration-regression-61920.txt，不再轮询该句柄。
本批覆盖近期转录告警、guidance及协作模式生产改动，也含76602中MCP夹具
定向修正；旧失败记录保留，但最新这批集成全绿。8workers/loadfile/禁止自动重启，
全过程未改生产代码或测试。排除 test_cli*.py，不声称其余历史混合文件完全无CLI断言。

与此前本次生产版本的unit 5882通过/1文件系统限制跳过（4655d9）合计15143通过、
1跳过。只读Ruff检查通过，1191文件格式检查通过（d89227），显式排除CLI目录和
test_cli*.py。这是当前回归证据，不证明尚无反例的核心差异已实现；C1/C2旧模式
历史入口、A7/C8交错与E4/E7等仍按acceptance-index逐项收敛，CLI对齐仍暂停。
下方“运行中”均为本次过程记录，不代表仍有活动测试进程。

## 最新刷新：协作模式修改后单元通过，集成运行中

续轮确认同一61920仍活动：17%（a2b016），随后等待45秒推进到29%（b6dd9f），
无退出码、无失败标记。并未中止或重启；当前仍不是集成全绿结论。

session 18819 已退出0（4655d9）：非CLI unit 5882 passed、1 skipped、22.53秒，
8workers/loadfile/禁自动重启；跳过仍为文件系统不支持非UTF-8文件名。
运行期间未修改生产或测试，此结果已覆盖近期guidance及协作模式生产变更。

全量integration（排除 test_cli*.py）已启动，session 61920（0e3df2），
8workers/loadfile/禁自动重启。当前及五份历史原生编译器已逐路径确认存在；
使用空keyring后端，保持既有离线夹具配置。只跟进该会话，不重复启动或在运行中
修改生产/测试。句柄和累计输出保存于functions store的core_integration_current/
core_integration_output；尚无最终结果，不能称全量通过。下方76602失败仍保留追溯。

## guidance 修复后的单元刷新；协作模式新修改另有定向验证

session 51642 已退出0（56d63a）：非CLI unit 5882 passed、1 skipped、23.25秒。
8workers/loadfile/禁worker重启；跳过仍是文件系统拒绝非UTF-8文件名。
此后本轮修改了协作模式 builder/world_state，最终关联416通过（e3ec37），
详见context-snapshot-wire.md。因此不能把5882称为协作模式修改后的全量结果，
也不能覆盖下方旧全量集成的失败记录。本轮未启动全量集成或恢复CLI对齐。

## 转录告警生产变更后：最新单元回归已完成

session 79298 已退出 0（b55067）：非 CLI unit 5878 passed、1 skipped、22.19 秒。
跳过为 test_project_trust_selection 的非 UTF-8 文件名文件系统限制；8 worker/
loadfile/禁止自动重启，运行期间未改生产或测试。转录诊断失败及恢复后不再警告的
关联集成 56 通过（006c73），不代表当前全量集成已刷新。下方 76602 集成结果早于
转录告警生产修改，仍保留原 1 夹具失败及定向修正记录，不改称全绿。

## 当前终态：unit 5878 通过；integration 9222 通过、1 夹具失败已定向修正

session 76602 已退出 1（23c187）：9222 passed、1 failed、493.01 秒，无跳过。
完整输出保存 integration-regression-76602.txt，不再轮询该句柄。唯一失败为
test_mcp_input_requirements::test_selected_plugin_uses_exact_host_identity[False]：
固定 sleep(0.12) 后 requests 仍为空。原两参数定向串行均通过（152240）。
不匹配身份的语义应为“不等待该 MCP 就可完成”，而非“必须在 120 ms 内采样”。
现仅该分支改为在 release 尚未设置时 wait_for(shield(task), 2)，再执行原请求/
曝光/终态断言；匹配身份分支不变，不改生产、不放宽身份匹配规则。
修正后两个完整关联文件 61 通过、12.24 秒、8 worker/loadfile、退出 0（3e4474）。
全量失败记录不能改称通过；修正后全量未重跑。下方运行中描述仅为历史记录。
下一步继续 A–E 剩余差异核验，E4 公开同步接口兼容选择仍待用户确认，CLI 暂停。

## 最新刷新：unit 5878 通过，集成已启动、尚未完成

续轮已跟进同一 session 76602，从 7% 到 12%、24%（81dd26、296e65、2a0eea），
最后一次等待 45 秒仍返回活动 session_id，无退出码、暂无失败标记；不是终态。
未重复启动，也未修改生产或测试。当前错误分类只读核对 executor.py 的
FatalToolError/Exception/取消保护，以及 ModelFailure.error 的旧 402 重试否决，
与既有 E2/E3 文档对应；不能仅据此关闭更广的 E1/E6。

跨存储 fork 与 managed Hook 近期改动后的非 CLI unit 已真实退出 0：
5878 passed、1 skipped、22.22 秒（20d695）；跳过仍是文件系统不支持非 UTF-8
文件名。8 worker/loadfile/禁自动重启，显式排除 tests/unit/cli。
当前非 CLI 集成 session 76602 已启动（3becd9），8 worker、相同隔离方案，五份历史原生编译器均确认
存在。活动句柄保存在 functions store 的 core_integration_current，输出累积于
core_integration_output；只跟进该句柄，不重复启动。运行中不改生产/测试。
下方 9177 集成结果是此前批次，不代表近期生产变更后的全量已通过。
E4 运行中同步 create 的公开兼容选择已再次询问用户；尚未擅自禁止该 API。

## 当前刷新已完成：unit 5859 通过，integration 9177 通过

session 25320 已真实退出 0：9177 passed，500.19 秒（7396ad），无失败或
跳过。完整累计输出见 integration-regression-25320.txt；不再轮询此句柄。
本次非 CLI unit + integration 共 15036 通过、1 文件系统限制跳过；运行期间
未修改生产或测试。它证明当前测试覆盖下未出现回归，不代表 A–E 全部已对齐。
下一批回到已核实的跨存储 fork 缺口；E4 兼容选择仍开放，CLI 仍暂停。

以下进度段为同批次的运行历史，均被上述终态取代。

续轮同句柄推进 74%→79%→83%（325093、1e073e、385475），仍返回活动
session_id，无终态、暂无失败标记。git diff --check 通过（cec63d）。
未改生产/测试；跨存储设计必须保留当前 fork 回执的提交后重试幂等语义，
不能因读取外部源引入重新取样历史或复制源执行义务。

同一句柄本轮已确认继续存活，输出由 11% 推进至 58%（00f269、902ae3），
暂未出现失败标记，尚未退出；这不是最终通过结论。未启动重复测试组，未修改
生产或测试文件。上一轮仅核对目标并行规则，未新增实现；本轮为真实进程跟进
及跨存储 fork 的只读源码核验。

clear/fork/usage/legacy 近期改动后的全部非 CLI 单元测试：5859 通过、1 跳过，
23.93 秒、退出 0（fdfd10）；跳过为文件系统拒绝非 UTF-8 文件名。8 worker、
loadfile、禁止 worker 自动重启，null keyring 与当前 sandbox compiler。
全 src/tests Ruff、1264 文件格式与 git diff --check 通过（c1c715）。

全部非 CLI 集成组已启动，session **25320**（48368a）；排除 test_cli*.py，
8 worker/loadfile/禁重启；配置当前编译器与五份仍存在的历史兼容编译器。
后续只跟进此句柄到实际退出，不因等待启动重复进程；运行期间不改生产/测试。
完整进程输出暂累积于 functions store 的 core_integration_output，最新句柄结果
为 core_integration_current。退出后记录全部失败/skip，不以单元全绿代替集成结果。
Codex HEAD 仍为 ddf04ad26789d040f9ef6a96736f76602e35a6cc（c7ea4d）。

以下是旧批次历史，旧集成 74817 与旧单元 88800 均已终态，不再轮询。

## EOF修正后的关联复验已完成

response_routing、session_recovery、connection_handoff三个集成文件及stdio_ownership、
http_response_routing两个单位文件，8worker/loadfile/禁止worker重启，共94通过、
14.74秒（56f5f4），句柄61607最终退出0（c995a0）。涵盖真实EOF/reverse RPC、
连接交接取消、会话恢复与关闭观察者取消。期间未改生产或测试，不将原9075+1失败
改称全绿。下一步回到SessionStart显式thread_id的来源归类核验，而非继续重复此组。

## 集成组已结束：9075通过、1失败；失败夹具已定向修正

session74817退出1，9075 passed、1 failed、499.98秒（9b29f6），无skip。
最终输出保存于integration-regression-74817.txt；不要再轮询或重启该句柄。
唯一失败为test_mcp_response_routing的stdio-eof-code_mode：测试模型读取
clients[0]._process.wait时临时字段已为None，因此产生内部TurnFailed。

原例单独串行运行通过（18cfa6）；源码client.py::_close在join/reap后明确清空
_process（d0a09c），而夹具_spawn早已将同一个真实Process保存到processes。
改为processes[0].wait，保留原0.5秒期限、退出码0、错误Observation/唯一调用/
账本和冷历史全部断言。不改变生产关闭顺序，不把已有关闭对象的字段清空视作
资源泄漏。修改后整个response routing文件8worker共36通过/4.90秒（89cd70），
Ruff/格式/diff通过（89cd30）。原并行失败记录不改称全绿。

非CLI全单元组5859通过/1文件系统skip与本集成组构成当前分组回归；唯一失败
已定向修正，但修正后全部集成尚未重跑。下一步扩大EOF/刷新/关闭关联复验，
再收敛整体A–E验收，不重复轮询已完成句柄。CLI对齐继续暂停。

## 正在运行：非CLI集成组session74817

最新同句柄推进43%→49%→56%→67%（7714ea、6e21ab、40ea65、9ef631）。
约51%出现一个F标记（40ea65），尚无最终失败详情，不能判定为生产缺陷或并行
时序问题。继续原session74817至终态，保存最终报告后再定向诊断；不自动重跑、
不在运行期间修改代码/测试。此前“暂无失败”仅对应更早进度，不代表当前全绿。

本轮继续同句柄29%→33%→36%（2fb2b5、384368、754b78），未终态、暂无失败
标记。Codex基准再次确认为ddf04ad且工作树干净（359f35）。仅为current-scope.md
添加当前A–E/F暂停边界与旧进展历史标识，未改生产/测试，避免旧CLI记录误导续跑。

本轮同句柄续读13%→16%→20%→23%（73a927、b211b7、ca2624、94ccbe），
PID53598再次确认存活（d7c762），尚无失败标记，不是终态。期间仅阅读构造/关闭
已有证据，未修改生产/测试。E4同步create兼容选择已向用户发异步问题，等待答复，
不因此暂停此回归，也不擅自迁移公开入口。

进程PID53598已由ps确认（2598af），后续同句柄读到约7%（2f12cf），仍运行，
目前输出无失败/跳过标记；这只是运行中观察，不是最终通过结论。

在已完成单元组5859通过/1skip后启动全部tests/integration，仅排除test_cli*.py。
8worker、--dist=loadfile、--max-worker-restart=0、-o addopts='' -q --tb=short -rs。
命令显式设置null keyring、当前bundled CORKI_TEST_SANDBOX_COMPILER和前节已
核对SHA-256的全部五个CORKI_TEST_PRE_*历史路径。首次续读约1%（1d3cfc），
尚无终态。下一轮续读同一session74817，不重启或重复启动，不修改生产/测试。

并行检查：插件package/source/feature/config夹具写入tmp_path/home；metadata
build从固定Codex git show只读导出后在tmp_path应用补丁，未编译或覆盖共享产物；
HTTP/MCP/权限探针均绑定端口0；chdir/环境monkeypatch进程内隔离；记忆清理目标
为fixture所有的临时worker目录。loadfile保留文件内顺序；若暴露时序/资源失败，
保留原并行结果并在结束后定位，不静默重跑改称通过。CLI及e2e不属于此批。

## 分组刷新：当前非CLI单元组完成

当前tests/unit排除CLI，以8worker/loadfile/禁止worker重启执行，5859通过、
1文件系统限制跳过，23.00秒，退出0（e0fd66）。见parallel-test-readiness.md顶部
的完整命令和隔离检查。session88800已完成，不继续轮询；无生产或测试在运行中修改。
完整核心回归尚差非CLI integration组刷新，不能用该单元组或旧全量冒充已完成。

## 当前补验：六项历史编译器skip已实跑

重新核对core-baseline-refresh.md表格所列五份/private/tmp历史二进制，SHA-256
全部一致（2284cf）。仅对命令设置五个CORKI_TEST_PRE_*变量，未替换当前bundled
compiler或修改用户环境。以六个独立worker执行原六项skip，禁止worker自动重启：

- test_metadata_compiler_contract.py（runtime/context两项）
- test_native_read_helper.py::test_old_compiler_rejects_native_helper_contract
- test_native_patch.py::test_old_read_only_helper_cannot_silently_accept_patch
- test_patch_approvals.py::test_old_patch_compiler_rejects_approval_protocol
- test_patch_deltas.py::test_old_approval_compiler_rejects_delta_contract_before_write

结果6通过、0跳过、2.26秒、退出0（d7aaee）。各例独立tmp_path，仅共同读取历史
二进制，无共享写路径/固定端口；断言包含不采样/不发布上下文和拒绝旧写协议。
单独重验非UTF-8路径用例仍因当前文件系统EILSEQ跳过（fb0a48），未改测试绕过。
全src/tests Ruff通过、1256文件格式通过、离线锁检查与pip check通过（ea69df）。

这是当前代码上的专项补验，不修改原全量14913通过/7跳过的历史结果，也不把其与
新六项相加冒充一次完整回归。完整回归仍早于最近Interrupt生产修复，A–E其它验收
继续；CLI对齐暂停。下方“六项待补”与并行尚未接入的描述均为当时状态。

## 最新批次：夹具修正及取消持久化故障补验后（已结束，通过）

session96933已退出0：**14913 passed, 7 skipped in 2562.28s (42:42)**（846f9d）。
最终输出和原命令保存于core-regression-96933.txt。运行期间未修改生产代码、测试或
依赖环境；排除CLI及e2e，不宣称覆盖暂停范围或真实模型质量，也不替代A–E差异验收。
七项skip明确为：1项文件系统拒绝非UTF-8文件名；6项需显式历史原生编译器
（metadata contract两项、native patch/read helper/patch approvals/patch deltas各一项）。
这些skip不计通过，历史补验不能替代当前补验。下一步按用户授权落实隔离后的并行
测试，并继续Interrupt完整持久字段及A–E其它开放项。不要再轮询或重启96933。

以下是本批运行过程记录，不是当前仍在运行的状态。

最新续读到约95%（740ef6、b2c4bf、d6fa0c、d8a52b、dfea95），
PID28230及原命令再次确认仍运行（106c15）。累计七个skip，尚无失败标记，
未终态；继续session96933。
75%新增两个skip，77%新增三个，78%再新增一个；连同早期一个共七个，原因等
本批-rs终态报告确认，不能用历史相同数量直接判定原因或算作通过。
连续20秒等待均有新增输出。此前只读追踪Interrupt计划校验/命令重建/SQLite claim，
本轮仅等待和记录证据，未修改生产或测试。此百分比仅为回归执行进度。
用户新增并行授权已同步goal；后续准备见parallel-test-readiness.md。该规则不
中止本批；环境12逻辑CPU/18GiB，拟先8worker验证隔离，尚未安装插件或执行并行。

续读同一session96933已超过42%（37c96d、935180、4880a2、f75289），
ps再次确认PID28230及完整原命令仍存活（310a42）。累计一个skip，尚无失败标记，
未输出终态。多次20秒等待均持续输出；期间仅同步审计文档，未修改生产代码或测试，
下轮继续此handle。前次约39%的观察为4b66f9，不是最终结果。

全src/tests Ruff check及1254文件格式检查通过，git diff --check无输出；启动前
pgrep未找到pytest进程（fa2c8c最后退出1来自pgrep无匹配）。使用下方相同的核心回归
命令，排除CLI，启动session **96933**、PID **28230**（e9c527/2a4cf6）。
首次续读3198f4到约12%，一个skip、尚无失败标记；尚未终态，不称当前全绿。
下一轮续读此同一session，不因等待重启。测试运行期间不修改生产代码和测试。
上一批91344已结束；下方36失败及其定向修复是历史证据，不替代本批最终结果。

## 当前批次：中断/启动/关闭与取消恢复改动后（已结束，未通过）

补回原3000阈值的重复压缩场景，而非仅提高阈值规避：deferred测试现参数化
4000/一次与3000/两次。两次场景断言第二摘要请求实际收到FRESH_READ_PROOF工具结果，
最终普通请求收到包含该结果的CompactionItem且当前用户输入保留、定义再次释放；
两次工具副作用仍严格old/new各一次。单次场景仍精确断言最终ToolResultItem。
专项3通过（f3d578），联合unit/context、memory ownership与environment集成381通过
（94c248，4.85秒）。这证明普通摘要链路的输入/安装，不证明真实模型摘要质量。

六个失败文件现联合154通过、32.18秒（16ba71），原36失败均已定向复验通过，
不改写原全量统计、未重跑全量。最后两项：deferred明确第6请求为第二次摘要
（279969），原3000阈值会在fresh read后再压缩，旧脚本仅支持一次压缩；
单次压缩夹具阈值调4000，仍保留一次自动压缩、六请求、旧/新handler各一次、
重新搜索加载、当前输入与原始大结果保留及fresh result回灌全部断言（b58017）。
memory窗口测试扩为3000/4000×无覆盖/6000覆盖：3000无覆盖现在明确验证
超出有效窗口时失败且模型零请求，其余验证真实完成和一次模型请求；仍精确检查
worker解析的raw窗口/effective percent，不通过增大生产窗口掩盖预算不足。
生产代码未改。全src/tests Ruff check通过（5ee821），一处测试换行已格式化。
后续仍需全量回归及A–E开放项，不将夹具修复等同核心目标完成。

再定位环境两失败：Codex context/world_state/environment.rs::render_diff在日期等
Turn字段变化时携带当前network/filesystem，body按此渲染；Corki environment.render_update
与该行为一致。旧集成预期只包含日期/时区，现补齐默认managed/read-root权限的精确XML。
环境六项全部通过，与deferred两项联合7通过/1失败（540620）。生产未改。
另两项仍开放：memory worker上下文2931超过2850；deferred诊断已确认6次请求、
两次实际执行分别old/new metadata，末次请求断言失败（c2901f），需继续核对是否
再次触发压缩以及最终工具结果可见性，不能仅凭执行次数宣称工具结果正确回灌。

后续定位：三个Stop测试文件的32个失败由故障注入范围漂移造成。旧夹具拦截所有
save_hook_batch，新增UserPromptSubmit会先保存自己的准入记录，故障/挂起/旧格式改写
提前发生，尚未到达Stop批次。已将SQL触发器、挂起保存及legacy改写分别限定
stop:/subagent_stop:前缀；原终态、授权撤销、模型次数、真实副作用次数断言保留。
修前独立重现5失败（13f9ec）；修改后三文件136通过，29.89秒（3f3413），Ruff check通过。
format发现一处换行，已格式化。生产代码未改；这不是放宽unknown恢复规则。
原全量36失败记录不改写；另外4个上下文/环境/记忆失败仍待定位，全量未重跑。

session91344已退出1，最终输出1ce85a：**36 failed, 14848 passed, 7 skipped in
2599.51s (0:43:19)**。完整回溯保存在[core-regression-91344.txt](core-regression-91344.txt)。
不再轮询此进程，不以历史全绿替代此批结果。以下进度段落仅为运行历史。

失败分组（尚未完成根因确认或修复）：
- memory/test_agent_ownership：1项，准备上下文2931超过2850预算。
- test_deferred_namespace_compaction：1项，模型夹具内断言失败，需独立重现取得完整断言。
- test_runtime_environment_context：2项，环境增量比预期多filesystem权限快照。
- test_stop_batch_admission：4项，通用batch故障注入后模型请求数为0而非1；
  已读夹具，当前触发器/包装器未限定Stop批次，需核对是否提前命中新增准入批次。
- test_stop_hook_recovery：12项，版本True/2变体在warm阶段等待entered超时。
- test_stop_pending_recovery：16项，unknown恢复终态及副作用次数不符合预期，优先核对恢复身份。

七项skip已由最终-rs确认：1项文件系统拒绝非UTF-8文件名；6项需显式配置历史原生编译器。
历史批次补验不能替代当前批次补验。静态检查通过是本批启动前的结果，不覆盖后续修改。

最新续读228fe6、02c38b、b5443e到约95%，同一session91344仍存活；PID10468及原命令
已再次核实（3601e8）。此前累计八个失败标记，本轮又连续新增28个，累计36个，尚未输出
具体回溯。本轮两次30秒续读持续有新测试输出。只做只读核对及文档记录，不改生产/测试，
不重复启动。未终态属于已核实等待，不是任务阻塞，不据此前中间通过数宣称全绿。
约75%处新增两个skip标记（9d2938），77%～78%又新增四个（85ea65）；
连同早期一个，累计七个skip标记，原因等待最终-rs报告，不先认定为环境跳过。

续读确认同一session91344仍运行，PID10468亦存活（64f826）；已到约39%
（c1a8a9）。此前在约22%出现一个失败标记（e9b1b2），具体用例/回溯尚未汇总，
不能声称当前无失败或全绿。保留现场继续该进程，未更改生产/测试文件或重启。

本批启动前pgrep未发现pytest进程。全src/tests Ruff通过，1253文件format检查通过
（f58acd），git diff --check通过。当前重新启动核心回归session **91344**，PID **10468**，
进程命令已核对（70a02b）；首次续读8207dd运行至约13%，无失败标记、一个skip。
尚无终态，不能据此宣称全量通过。后续继续读取同一session，不因观察超时另起一批。

```sh
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring CORKI_TEST_SANDBOX_COMPILER=/Users/corki/PycharmProjects/corki/src/corki/_native/sandbox/corki-sandbox .venv/bin/pytest tests/unit tests/integration --ignore=tests/unit/cli --ignore-glob='tests/integration/test_cli*.py' -o addopts='' -q --tb=short -rs
```

范围排除已暂停CLI和e2e，覆盖其余unit/integration，包括新Interrupt/MCP/冷恢复测试。
本次添加-rs以保留最终skip原因。使用已存在的当前bundled compiler，未改用户凭据或
调用真实模型服务。测试运行期间不修改生产/测试文件；文档进度更新不作为通过证据。
即使本批全绿也仍需按A–E审计开放项验收，不能替代功能差异关闭。

## 以下为上一次已结束的批次，不是当前结果

session65002已正常退出0，最终输出1ab422：
14337 passed, 7 skipped in 2284.01s (0:38:04)。
本批运行期间未修改生产/测试文件。命令未设置-rs，终态没有逐项跳过原因；
七项不计作通过；以下定向补验独立记录，不改写原全量统计。

## 七个跳过项的定向补验

按同一基线环境运行native_patch、patch_deltas、native_read_helper、
metadata_compiler_contract、patch_approvals、bundled_execution六个集成文件及
unit/config/test_project_trust_selection.py，添加-rs：103通过、7跳过，
12.31秒，退出0（356f17）。本次明确重现六个缺旧编译器配置的跳过与一个
文件系统拒绝非UTF-8文件名的跳过，不单凭历史数量猜测原因。

core-baseline-refresh.md所列五份旧二进制SHA-256已重新核对一致（d1c0d7）。
仅为补验命令设置该表五个CORKI_TEST_PRE_*变量，再跑上述相同文件：
109通过、1跳过，12.21秒，退出0（ba3b8e）。metadata两入口、旧native FS、
patch、approval、delta六项现实际通过；唯一剩余跳过为
test_project_trust_selection.py:164的文件系统限制。未修改生产/测试代码、
未替换当前bundled compiler、未修改默认配置，也未放宽测试来消除跳过。

启动前pgrep未发现pytest进程。本轮新启动session65002，主PID62461，进程
命令已核对（f28423）。首次续读d62069到约12%，无失败标记、有一个skip；
上述为启动历史；本批现在已终态，不再轮询或重启同批。旧14193通过不是本批结果。

命令：

```sh
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring CORKI_TEST_SANDBOX_COMPILER=/Users/corki/PycharmProjects/corki/src/corki/_native/sandbox/corki-sandbox .venv/bin/pytest tests/unit tests/integration --ignore=tests/unit/cli --ignore-glob='tests/integration/test_cli*.py' -o addopts='' -q --tb=short
```

覆盖最近异步Pre、checkpoint投递、共享并发、关闭修复及压缩安装回归；不包含
CLI和e2e，不证明真实模型选择质量。测试运行期间本轮未改生产与测试代码。
基线仍为固定Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc。

同步构造缺口重新核对construction.synchronous_rollback：在运行loop且无
显式construction owner时仍直接factory；acreate使用create_owned接管回滚。
禁止旧公开调用方式属于未确认兼容迁移选择，本轮不擅自改变。此项与其他
A–E开放项均不因全量回归通过自动关闭，CLI继续暂停。
