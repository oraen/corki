# A4：UserPromptSubmit 主循环接入与剩余验收

采样checkpoint恢复缺口已修复：停在call_model前、随后异步结果提交、移除配置冷开，
原六例中已完成结果分支首请求缺context（22eae4，1失败/5通过），直到finalize才
投递并多采样。现Runtime恢复call_model/retry_model时接入prompt投影：按checkpoint
保存选定execution keys，再只投递该集合，校验源输入/上下文身份后更新request_items。
已投递receipt不删除选定集合，保证投递成功但checkpoint更新前中断仍可重新投影；
结果未知不重跑。源码依据仍为Codex hook_runtime.rs::drain_async_hook_results的
采样边界反馈语义，持久投影是Corki checkpoint恢复机制，不引入专有模型协议。
新增选择计划提交后、delivery receipt提交后两次故障/关闭/重新创建Runtime，再走
公开resume_pending，首请求有context、模型只请求一次、Hook只执行一次、历史context
一份且警告不重复。五文件联合 **90 passed / 9.36s**（df966d），Ruff通过（b70666）。
尚未覆盖投递receipt提交前取消、retry_model专门交错、压缩交错、损坏投影计划等边界；
不将此局部回归称为当前核心全量通过，A4/A7/C8仍未整体关闭。CLI继续暂停。
追加既有Pre/Post恢复、异步Compact/Stop四文件 **227 passed / 60.33s**（bf6cff），
修改范围Ruff、format检查及git diff --check均通过。与上方90项为两次独立运行。

异步实际进程补验：async_prompt_hook_recovery在原受控提交故障两例上新增真实
Python脚本完成/运行中关闭两例，四例通过（c66880）。脚本写PID及payload并等待
release，原Turn已完成且无同步Hook通知；完成分支释放后保存raw结果，关闭分支
不释放，由Runtime终止/join，os.kill(pid,0)确认不存在。两次冷开移除配置，完成
反馈一次context/首轮warning，未知不重启；started文件仍一份。
与async Compact/Stop、prompt基础/冷恢复/解析六文件联合
**103 passed / 12.06s**（4166a0），Ruff/format/diff通过（6f4d61）。本批仅补测试，
不将OS强杀、配置保留的新输入触发、采样checkpoint或投递中断视作已验证。

异步结果恢复已接入：实际Runtime完成原Turn后释放后台runner，complete_hook_execution
提交前/后注入异常，关闭后移除Hook配置并冷开新Turn。原unknown对照通过，但已
完成结果丢context的反例失败（22ac1d）。新async_prompt_hooks.drain从持久计划/
执行事实恢复完成结果，在prepare（用户输入前）和finalize边界投递；后台不再直接
append上下文，仅保存raw结果。delivery receipt防再次投递，未知/unclaimed不重跑，
上下文有原source_input_id及限额，警告归当前Turn，不发同步Hook通知。
finalize识别未被当前请求看到的prompt context后可继续采样；不以late continue=false
改写终态。此遵循hook_runtime.rs::drain_async_hook_results的边界语义，不是模型协议。
两次冷开验证脚本一次、context一份、警告仅首轮；相关六文件
**101 passed / 11.45s**（ee8df4），Ruff/format/diff通过（f369cd）。
受控runner/实际SQLite，不冒充真实脚本进程断电。call_model checkpoint跳过prepare、
投递receipt前后故障、压缩交错及后台运行中关闭仍需专门验收；本次不关闭A7/C8。

终态Hook取消修复：Stop期间steer的新输入在终态检查runner抛CancelledError，原
实现没有终态事件（最后为HookStarted），RUNNING未结束且aclose因未完成flush
再报错（02b94c）。现正常收尾flush专门捕获该取消，归还未提交输入到
TurnCancelled及take_unsubmitted_inputs，ack队列、清除flush义务，然后继续保存
CANCELLED和资源清理；不伪造Hook结果，不吞掉面向调用方的取消传播。
真实Runtime限时场景确认两个归还接口均有LATE_INPUT、无RUNNING残留、aclose成功；
结合prompt/恢复/输入边界/steer/解析 **104 passed / 10.01s**（b6add5），
Ruff/diff通过（be26d3）。这是受控runner取消，不是实际shell进程或MCP断连验证。
数据库读取/终态写入再次取消、未完成inspection的冷恢复及异步投递仍开放。

终态pending输入检查已接入：参考core/tasks/mod.rs::on_task_finished，取出pending
后仍run_hooks_and_record_inputs。新增Stop runner内steer新输入并continue=false
结束主循环，允许/拒绝两例原均只检查INITIAL（9bd573，2 failed）。Runtime正常
终态flush现使用旧Turn上下文逐条inspect，拒绝ack不入历史，允许user→context
一起落盘且不额外采样。失败后的下次准入flush仅复用已保存receipt，缺决定明确
拒绝继续，不在没有旧事件owner时绕过检查。prompt/冷恢复/steer及压缩输入窗口
联合45通过（33dc6d）。原始配置/模型均为离线替身，实际Runtime/SQLite，不是
远程验证。未完成inspection的自动重试/恢复策略、终态Hook取消、异步交错仍待
验收；不能据此称全部flush生命周期关闭。
追加turn_input_boundary（含既有终态输入持久化故障）与prompt/恢复/解析联合
**101 passed / 9.10s**（f54158）；Ruff/diff通过（89a76a），格式已规范化。

准入后终态保留修复：新增公开Runtime.stream在Hook完成、window.prepare尚未落盘
时普通OSError/CancelledError两反例，原终态清理只写ADMITTED_INPUT，丢掉已保存
ADMITTED_CONTEXT（10b01d，2 failed）。Runtime初始输入清理现复用ordered_inputs
和媒体处理，从receipt恢复user→context一并append；仍过滤拒绝项，不运行Hook，
保留TurnFailed/TurnCancelled真实终态。两场景验证输入/context/source身份与一次
执行；和基础prompt/冷恢复/steer/压缩输入交错/解析联合 **88 passed / 7.18s**
（061d35），Ruff/format/diff通过（e16a05）。此为受控runner/真实Runtime与SQLite；
不涵盖尚未检查的realtime leftovers或取消返回宿主的未提交steer恢复，异步也仍开放。

同步上下文顺序已修复：初始/steering × plain/结构化四场景新增精确顺序断言，
旧实现均失败（2b57fb，user位于context之后）。现在同步context先以普通item
payload存入准入receipt；获准输入由ordered_inputs展开为user→contexts，再交由
window统一媒体处理、预算、持久化和压缩。拒绝时只保存context，不保存用户输入。
冷恢复读取同一receipt，不重跑Hook；旧receipt无contexts字段仍可读取，既有已入
历史的旧context不重写。正常/恢复初轮26通过（88c4b7），冷恢复也追加顺序断言。
异步context仍走后台发布，不能把同步投递修复泛化成异步交错已验；准入receipt
之后的取消/终态flush、损坏投递数据及压缩交错仍需继续收敛。
七文件联合 **130 passed / 12.17s**（f9fe11），含运行中新输入、压缩steer提交窗口、
压缩Hook与解析；Ruff/format/diff通过（a5a694）。未执行最新全量核心回归。

冷恢复基础账本补验：新增test_prompt_hook_recovery.py，unknown/completed/receipt
三窗口 × stop/context × 保留/移除配置共12通过（f2f190）。实际Graph直接运行
RUNNING Turn，在结果落盘前/后或receipt落盘后抛错，关闭并新建Runtime后公开
resume_pending。unknown明确失败且不重跑，completed/receipt复用原结果，拒绝
无采样/无用户历史，context允许后仅一次采样、输入/context唯一，原执行事实不变，
再次resume空。受控runner+真实SQLite，不冒充实际进程/OS强杀；本批只补测试。
未知结果时失败清理的输入保留策略、本轮之外的context顺序及异步投递仍未验收，
不能将这12例泛化成全部准入恢复证明。下一步优先context顺序及终态flush交错。
与prompt实际执行、Pre/Post冷恢复、输出解析联合 **275 passed / 57.36s**（b758c0）；
Ruff/format/diff通过（d60dc7/de4834）。参考hook_runtime.rs::record_pending_input
先record_user_prompt再record_additional_contexts，Corki当前先append同步context，
顺序差异仍在，不能因上述context可见性测试通过而视为一致。

## 当前生产实现（以下历史未接入记录已被本节更新）

StopHooks.prepare/discover/command_identity加入UserPromptSubmit；新prompt_hooks.py
以输入item身份保存计划、执行claim/result和准入receipt，恢复时核对prompt/session/
turn与请求身份，未知效果拒绝重做、待执行授权变化拒绝恢复。普通command和MCP
使用已有runner，异步复用共享owner；context使用已有限额/spill及稳定ID。
Graph._prepare_model_context逐条检查初始输入；_persist_realtime_input检查steer，
拒绝项ack而不入历史，整批拒绝走prepare→END的成功终态，不误用取消或Stop Hook。
Runtime终态初始输入保留分支查询拒绝receipt，避免重新append拒绝项。

两个原始反例已转绿；扩大初始/steering × stop/context/plain/block/invalid_block/
exit2/allow共14场景通过（667ed8）。真实Python进程写payload，拒绝无额外模型请求
且历史无该用户输入；允许路径模型收到context。初轮修复中的异步generator误用
导致终态清理失败（4e0ab4），已改为可await列表推导；不是未处理遗留故障。
首轮相关八文件160通过（fc2254），最后追加context完成事件及14场景后再跑联合。
最终同八文件 **167 passed / 23.68s**（253ab1），改动文件Ruff/format/diff通过
（60d192）。仅此范围联合回归，不是最新全量核心回归。

这仅关闭基础接入缺口，下面事项必须继续处理/验证：

- 实际RUNNING冷恢复unknown/completed、receipt/context发布中断、配置变化与旧历史。
- async结果跨checkpoint恢复、压缩后投递去重、取消/关闭；当前后台直接append
  context的路径不能据已有异步Pre/Post证据宣称一致。
- 同步context目前先append再由window接受用户输入，需进一步核对参考的输入→context
  顺序、稳定source关联及拒绝时context保留，不把模型能看到context当完整顺序验收。
- Runtime正常终态对尚未检查的realtime leftovers仍有flush路径；已检查拒绝项ack，
  但未检查的新到达项与终态交错尚不能算已覆盖。
- 多输入混合批次、MCP实际执行/故障、来源策略、schema/计划损坏、通知与输出限额。

CLI仍暂停；本批不是官方服务功能，也不宣称A4或全部Harness完成。

当前实施阶段：新增core/prompt_hook_output.py，按源码独立实现输出语义，45项
单测通过（cb993e）。覆盖普通stdout注入、严格wire类型、同步/异步控制、无原因
block失败但continue=false优先停止、exit2非空stderr、warning及context保留规则。
reason无decision不是错误，suppressOutput忽略；hookEventName按参考共享枚举
解析，不把schema注解误当serde运行时约束。此模块尚未接入Runtime，两个真实
集成反例仍未修复，不能据此把功能标为实现；下一步必须用它接入输入准入链。
与压缩/Post/异步Pre解析联合99通过（db2440），Ruff/format/diff通过；不是Runtime回归。

输入路径追加核对：Graph._prepare_model_context直接传pending_input_items给window，
_persist_realtime_input独立准备并ack，Runtime._flush_realtime_inputs在正常终态
直接append未recorded队列。准入结果必须能被这些路径一致识别；否则主循环拒绝后
flush仍会复活输入。图prepare目前无条件进call_model，需明确的“全部输入被阻止”
成功终止路线，不借用取消异常或会运行Stop Hook的普通finalize来假装等价。

## 参考行为与边界

参考只读Codex commit ddf04ad26789d040f9ef6a96736f76602e35a6cc：

- core/src/session/turn.rs::run_hooks_and_record_inputs逐条检查输入；首次TurnStart
  和循环中pending input均调用。只在检查通过后record_pending_input；阻止的用户
  输入不进入会话历史。混合批次存在已接受非空用户输入时仍继续，不能一律取消全批。
- core/src/hook_runtime.rs::inspect_pending_input只处理TurnInput::UserInput；
  ResponseItem、FunctionCallOutput、InterAgentCommunication不运行此Hook。
  payload含session/turn、ThreadSpawn可选身份、cwd、transcript、model、permission_mode、prompt。
- hooks/src/events/user_prompt_submit.rs::run/parse_completed：同步continue=false，
  合法decision:block+reason，或exit 2且非空stderr可阻止；非法输出/一般执行错误
  形成失败Hook但不直接阻止。普通stdout可注入context，结构化additionalContext也支持。
  异步不能应用控制效果；并发执行、配置顺序汇总、输出限额/落盘复用现有Hook机制。
- 全批被阻止时run_turn返回Ok(None)，不是TurnAborted。tasks/regular.rs返回成功结果，
  tasks/mod.rs::on_task_finished按Ok发TurnComplete；不能把这种停止实现成取消异常。

本项是用户输入准入与上下文核心行为，不属于暂停的CLI风格，也不涉及官方服务。

## Corki实际缺口及反例

plugins/agent_hooks.py只列出事件名；StopHooks.prepare只加载Stop/SubagentStop、
Pre/PostToolUse、Pre/PostCompact。command_identity亦不接受UserPromptSubmit。
Graph._prepare把pending_input_items直接交给window.prepare，运行中新输入另走
_persist_realtime_input，_finalize依据已保存输入安排后续采样；没有该Hook检查链。

新增tests/integration/test_user_prompt_submit_hooks.py：真实Runtime.stream、真实
SQLite与实际Python脚本配置（独立按规范计算可信hash），stop/context两场景均
在marker不存在处失败（39b0d2，2 failed）；脚本完全未执行。未加xfail，未改生产。
最初测试曾预期取消，源码继续追踪后已纠正为TurnCompleted；失败根因仍是零执行。

## 实施与验收要求（仍待完成）

1. 接入可信配置/身份与MCP/command调度，复用输出限制和共享异步owner。
2. 建立按用户输入item身份绑定的持久计划、claim/result、context投递去重；
   未知副作用不重做，配置撤销不能绕过授权，完成结果可恢复。
3. 首次输入、运行中新输入、冷恢复分别在历史准入前检查；拒绝项不得由finalize或
   Runtime清理flush再录入、再次触发采样。已接受批次的次序与后续输入归还不能丢。
4. 控制和上下文解析按源码，不把普通stdout忽略，也不把正常阻止变成TurnCancelled。
5. 修复两个真实反例后补混合输入、重试/冷恢复、可信来源、取消、异步与MCP验证。

优先实施此实际功能缺失，不继续仅扩展已通过的压缩Hook正常路径矩阵。
