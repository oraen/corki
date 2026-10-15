# Hook 执行事件与 CLI 呈现：实施前对齐基准

状态：独立生命周期、完成文本、活动状态机与工具栏计时已接入；普通/resume 双模式 Hook PTY 已验证，完成样式已补齐。冷历史完成回放不属于原生已有行为，见下方更正；授权面板等仍待完成。
参考 commit ddf04ad26789d040f9ef6a96736f76602e35a6cc。

## 冷历史与内存回放边界更正

2026-09-12追踪原生持久化及TUI恢复：rollout/src/policy.rs明确不持久化
HookStarted/HookCompleted，app-server-protocol/src/protocol/thread_history.rs也不
把它们转成历史Turn item。TUI冷恢复读取turns并replay_thread_turns。
app/thread_events.rs保留的是既有内存buffer中的Hook通知，相关rebase测试不是
冷启动证据。因此下文旧阶段记录中的“冷历史完成回放缺失”不再作为实现任务。
“持久显示/Transcript”在本文应解释为当前进程可重排显示，不等同于落盘通知。
Corki cli/history.py不重建Hook通知与此边界一致；保留执行账本和真实恢复事件，
不增加新的冷历史显示协议。详细链路见remaining-implementation-priorities.md第3项。

## 当前实施进展

完成样式已实施：hook_output_text 复用既有文本投影，在 Rich Text 中只标记状态
圆点或来源前缀；TerminalUI.show_hook_output 通过 remember_display 保存结构化
run，重排时重建样式。Application 的真实 HookCompleted 分支调用该方法；旧宿主
UI 没有此可选渲染方法时仍输出原纯文本，不改变事件协议。quiet/context-only 仍
不会从此事件分支写入 transcript。没有改变 Hook 控制决策、计时和输入所有权。

新增完整 ANSI 文本断言覆盖四终态、40/100 列、字面 markup、多行、同宽回放；
成功 warning 只 dim 前缀，NO_COLOR 仍由终端配置决定。已有真实 Hook PTY 的
block 分支增加 ANSI 红色圆点断言，8 个 fresh/resume×实时/非实时×宽度场景
通过（776322）。测试早期继承 NO_COLOR 导致颜色断言失败，已在彩色测试中
隔离环境，而非在生产代码中强制忽略用户配置。生产前新增接口测试 9 failed
（bae7b5）只证明缺少专用 renderer；旧整块 dim 的差异由上述源码对照确认。
最终 CLI 单元与全部 24 个 Hook PTY 联合 456 passed（2eefbe，53.53 秒）；
24 条 Python forkpty 多线程弃用警告如实保留。静态检查通过。此批不关闭
冷历史完成投影、授权面板、任意长内容换行或完整 A–F 验收。

2026-09-12 样式复核：原生 history_cell/hook_cell.rs::output_lines 与
hook_completed_bullet 仅将完成圆点设 green+bold、failed/blocked/stopped 圆点
设 red+bold；标题和正文为默认前景，成功 warning 只把 `↳ Hook · ` 前缀变暗。
Corki Application 当前经 show_notice 将整块变暗，状态颜色与正文可读性不一致。
本轮计划接入独立 show_hook_output，保持当前文本/上下文隐藏规则与可重排 transcript，
不改 Hook 决策、计时或官方协议。用真实 TerminalUI ANSI/宽度/回放和现有 PTY 验证。

恢复入口已复用 _consume_interactive_events：普通运行/压缩选择原事件流，恢复
直接交接 resume_pending 的同一迭代器；_signal_turn_start 只观察真实开始事件，
没有额外 anext/replay/stream(user_input)。首 TurnStarted 前不读终端，空恢复和
错误不会消耗首条输入。消费者、开始等待者与输入 reader 一起取消/join；原始
迭代器由 wrapper 的 finally 关闭，外层 _consume_events 保持 shield 清理所有权。
恢复始终 steering_enabled=False，与 Runtime 的 realtime=False 一致；新输入
只排队。取消后恢复 composer，不将 Hook 取消伪装成完成。

新 test_resume_input.py 四场景（空、错误、开始前取消、真实开始后入队）通过
（2d3483），断言 reader 不抢跑/终止、源流关闭、开始 waiter 无残留、无 steer。
Hook PTY 扩展 fresh/resume 两入口。resume 在已准备的存储中持久化 running Turn
及唯一用户输入，关闭 warm Runtime 后以同一 thread 创建 cold Runtime；这是
无 checkpoint 的恢复边界，不能代替 checkpoint 已提交副作用恢复证明。
首次夹具漏 _ensure_ready 导致 thread 外键失败（3cfc7c），已修夹具，不作为
生产缺陷先红证明。全组合与旧 checkpoint 故障窗口回归另记。
最终 Hook fresh/resume × true/false × quiet/block/cancel × 40/100 列共 24 PTY，
与所有 CLI 单元、原有 Stop checkpoint 冷恢复联合 458 passed（68038f，55.55 秒）。
断言 unique UserMessage、固定采样/命令次数、提示不留 Transcript、恢复不新增输入
历史及取消 pid 退出。已有 checkpoint 测试验证 unknown 不重放、已提交结果复用，
但尚无“已有 checkpoint 故障窗口 + PTY”联合场景，保留为验收边界而非宣称全链路
完全一致。静态 ruff/format/compileall/uv pip check/diff check 通过。
基础 CLI 与全部计划 PTY 再验 12 passed（9da659，19.95 秒），包括空恢复不影响
首次输入、模式切换/草稿与 Ctrl+C/Ctrl+D 退出。本轮测试句柄均已终态。

恢复入口实施前审计：runtime.resume_pending 在确认 running Turn/checkpoint 后调用
_run_graph(realtime=False)，首事件为 TurnStarted(resumed=True)。CLI 当前直接消费，
没有 composer。不能改成 stream(user_input) 或额外采样来激活界面；应将已有恢复
迭代器交给同一交互消费者，等首个 TurnStarted 才启动 reader，无待恢复 Turn 时
不抢读首条用户输入。恢复期间新输入只排队，因为后端明确禁用 realtime；原生
protocol.rs 保留 resume replay 边界并按实时事件分派 Hook 生命周期，不能将旧
history 中的 running 记录伪装成新的活跃执行。验收须覆盖空恢复、错误/取消关闭、
有恢复时队列/活动提示和不重复输入/副作用。原有冷账本测试仍需联合回归。

非实时普通 Turn 与 compact 已接入常驻 composer：TerminalUI.keep_composer_during_turn
将输入区渲染能力与 runtime realtime 输入策略分离。复用现有 InputOwner，不增加
第二个 reader/renderer；普通 stream 不携带 realtime=True，Enter/Tab 只入本地队列。
保留缺此能力的宿主 UI 旧消费路径。Hook PTY 扩到 12 项（两模式×三结果×两宽度），
与 CLI 单元共 428 passed（5e8bf7，30.58 秒）。修改前 false/quiet/40 真实 PTY
因无法出现 HOOK_CHECKING 失败（b74826）。新增两个普通/compact 单元断言：
stream 签名拒绝 realtime 参数、steer 直接失败，仍能 FIFO 保存两个后续输入并清理 reader。

旧计划 PTY 在生产修改后 6 failed/87 passed（d201d8）；原因是测试 UI 将新增加的
busy read 计入 idle-read 计数并提前要求模型已完成。夹具现按 mode-cycle-enabled
区分两种读取，保留模式持久化、草稿/历史/模型请求断言；不是关闭新 composer。
首批 success 与新增非实时单元复验 6 passed（bb30f2），完整计划与双模式问题/压缩
PTY 继续验证。resume_pending 仍直接消费事件，活动显示全覆盖尚未完成。
最终双模式提问/压缩与全部计划 PTY 24 passed（dcb4f0，50.67 秒）；另一审批、
提问、压缩、计划取消组合 58 passed（7c5f08，104.69 秒，较早加载旧单模式提问/
压缩夹具），不能将这两个重叠集合相加。修改后的提问测试走 _consume_turn 真实
模式分派并断言 modal 结束后原草稿、空 form history、modal_depth 均恢复；压缩
双模式验证 Enter/Tab 队列、失败继续与 /stop 草稿恢复。全目标依然未完成。
最终 CLI 单元 418 passed（88aa88，7.60 秒），ruff check/format、compileall、
uv pip check 和 diff check 通过；本轮所有测试句柄均已终态。

本轮非实时实现方案（实施前）：TerminalUI 声明常驻 composer 能力，复用同一个
InputOwner 和现有活动消费循环；realtime=False 仍调用普通 runtime.stream(message)，
不传 realtime=True，也不调用 steer。忙时普通 Enter 与 Tab 留待后续 Turn；
/stop、Ctrl+C 和 modal 优先级复用既有控制路径。普通运行与手动 compact 接入；
无交互能力的嵌入 UI 保持仅消费事件。resume 路径另行核对，不能据此宣称全覆盖。

本轮新增 tests/e2e/test_hook_cli_pty.py：真实批准的本地 Python Stop 命令，真实
LangGraphRuntime、CorkiApplication、TerminalUI 与 pexpect PTY；40/100 列 ×
quiet/block/cancel。父进程等待工具栏 HOOK_CHECKING 实际输出后才释放命令，
不是直接调用 timer 或伪造 HookCompleted。block 第二次模型请求断言反馈已回灌；
终态断言 transient 不在 Transcript、quiet 不产生完成文本、输入历史仅有原输入，
并物理 Ctrl+D 退出。六项通过（e990b1，11.71 秒）；随后追加取消后真实 pid 已退出
断言并扩大回归。首次 cancel 失败是测试错误地只在正常 return 后执行断言，取消
传播后由应用外层处理，改为 finally 检查；没有为通过测试吞掉生产 CancelledError。
最终加上取消后 pid 消失断言，与 CLI 单元/evaluation/Stop 控制集成联合
459 passed（334337，21.31 秒）。ruff check/format 与 diff check 通过。
本轮仅新增行为证据，无生产代码变更，不将该组测试冒充全项目回归。

非实时缺口定位：application._consume_turn 的 realtime=False 分支只 await
_consume_events；compact 的 false 分支相同；terminal.read_message 才启动
PromptSession。invalidate 不会自行运行 renderer，所以该模式 hook 状态有值但
不可见。原生 bottom_pane/mod.rs::set_hook_status_message 则复用常驻 bottom pane，
任务中必要时 ensure_status_indicator。修复需协调 InputOwner/确认面板/运行输出，
不能简单再启动独立 Rich Live 或把 Running hook 写入历史；未将此缺口标不适用。
后续需验证普通运行、压缩、resume、modal 优先级、Ctrl+C 与关闭，再宣称此模式对齐。

新增 hook_activity.py：300ms reveal、实际 reveal 时刻起 600ms quiet bookkeeping、
按 id 重置、同消息合并、不同消息泛化、Context-only quiet、无 start 完成容错。
TerminalUI 使用一个 TimerHandle 驱动工具栏，无每 hook 后台任务，不写 Transcript。
应用在 Turn 终态立即清理，事件流 EOF/异常/取消/渲染异常 finally 再兜底清理；
clear/goodbye 也取消 timer。活动文本按 literal 单行处理，不解释 terminal markup。
CLI + evaluation + 全部 Stop 集成联合 509 passed（10aeea，19.44 秒）；随后补充
终态不等待 iterator EOF 的即时清理断言，另行复验。首轮测试失败含缺 pytest-asyncio
和旧 __new__ UI 测试夹具缺字段，修正测试调用及部分初始化清理；不作为行为先红证明。

最终应用/活动状态与基础 CLI PTY 复验 101 passed（2f74db，7.40 秒）。PTY 仅验证
40/100 列 /status 与 Ctrl+C/Ctrl+D 退出，不能冒充 Hook 活动显示验证；4 条警告来自
Python forkpty 多线程提示。ruff check/format、compileall、uv pip check、diff check
通过；参考 Codex commit 未变且工作树干净。

限制仍明确：非实时模式模型运行时没有 PromptSession，toolbar 不可见；本批没有
补齐此模式活动视图。600ms 已有状态计时，但 native exec/usage 插入顺序尚未等价
验证。尚无 Hook 专用真实 PTY、完整颜色、冷历史完成回放、/hooks 授权面板证据。
因此 F 为部分一致，不把该小批次视为 A–F 整体完成。

protocol/events.py 新增 HookRunSummary/HookOutputEntry 与 HookStarted/Completed，
不借用 ToolCall。StopHooks 对已捕获命令批次发送 started，按稳定执行 key 发送
完成状态与类型化输出；statusMessage 从规范化配置保留，缓存结果同样可报告而不
重跑。执行结果 WarningEvent 已改为 completed.entries，避免 CLI 重复显示；发现
和信任配置警告仍保持通用 WarningEvent。

CLI 在 started 时结束正文/reasoning 流并刷新工具显示；completed 走 hook_output.py，
安静成功和 Context-only 成功不写历史，警告/失败/阻塞/停止采用原生标题和缩进。
当前通过 show_notice 保存文本，颜色/专属活动行仍未接入，不能称视觉完全一致。
更没有用 started 提示永久写一行，避免偏离原生安静执行规则。

真实 Runtime 多 hook 测试断言 started/completed 身份一一对应、控制状态与诊断条目；
完成 renderer 测试覆盖 quiet/context 隐藏、Warning 多行、非成功标题。联合所有
hook 集成、evaluation、CLI 单元 482 passed（fd631b，18.90 秒）。应用层补充验证
正文先结束、恢复无 start 的 completed、非工具展示、诊断不重复。

剩余：非实时模式活动展示、专属颜色、真实 Runtime→CLI→PTY 全链路、历史恢复
持久输出及 exec/usage 插入顺序。unknown-result 终态清理已走通用失败路径，但还需
专门 Runtime→CLI 故障场景验证。下文保留原始差异审计，
其中无事件/无完成输出的描述已由本节更新。

## 原生调用链与行为

core/src/hook_runtime.rs::run_turn_stop_hooks 捕获 Hooks，对 preview_stop 的运行摘要
发送 started 事件，执行后发送 completed 事件。tui/chatwidget/protocol.rs 分派到
hook_lifecycle.rs::on_hook_started/on_hook_completed。UI 通过稳定 run.id 匹配，不
把 hook 冒充模型 ToolCall。开始时先刷新正文流，完成时可以处理未见过 start 的运行。

history_cell/hook_cell.rs 保存独立状态机：

- PendingReveal：开始后 300ms 内隐藏，避免快速命令闪烁。
- VisibleRunning：仅参与紧凑活动摘要，不写 transcript；相同 statusMessage 合并，
  否则按数量显示 Running hook / Running hooks。
- QuietLinger：安静成功立即退出活动摘要；保留 reveal 后 600ms 的清理计时，以维持
  exec flush/usage 输出顺序，最终不留下历史。
- Completed：仅成功且无用户输出以外的结果持久显示。Warning、Error、Feedback、
  Stop 可见；Context 不出现在 TUI。完成事件无对应 start 时也可持久显示。

重复 start 按 id 重置 reveal timer，不新增重复行。clear_active_hook_cell 清除
瞬态状态和底栏提示，不能把残留 running 状态刷入历史。

完成标题由状态决定：Hook failed、Blocked by hook、Hook stopped 等。普通成功且
有 Warning 使用“↳ Hook · 首行”，多行按 2/4 字符缩进。此信息来自 output_lines，
不是根据截图猜测。倒计时/延迟还需用可控时钟测试与实际 PTY 验证。

## Corki 现状与影响

- protocol/events.py 无 HookStarted/HookCompleted；StopHooks 仅产生 WarningEvent
  与持久化 ContextItem 反馈，CLI 不知道哪条 hook 正在运行或何时停止。
- cli/application.py 的工具事件队列只接 ToolCallStarted/Output/Completed/PlanUpdated；
  WarningEvent 立即调用 show_notice。不能将 hook 装入工具队列，破坏身份/输出语义。
- cli/terminal.py::show_notice 用 remember_display 持久记录，故不能拿它显示每个
  开始/安静成功，否则会把原生明确隐藏的状态永久写入历史。
- TerminalUI 已有 prompt-toolkit toolbar、流尾和 Transcript；应复用其刷新/清理
  所有权，避免每个 hook 单独创建不受控后台任务。
- StopCommand 当前未保存 statusMessage，且 outcome 仅提供控制/文字/诊断，没有
  完成状态的结构化区分。需在解析阶段携带失败与正常 allow 的区别，不按错误文字猜。

## 接入与验收顺序

1. 定义独立 run summary 与 Started/Completed 事件，包括稳定执行身份、事件名、
   来源、statusMessage、终态、类型化用户输出。保留账本不重复执行语义，恢复只
   展示缓存结果，绝不能为补齐 UI 重跑命令。取消不能伪装成功。
2. Runtime 在真实执行/缓存结果边界发送事件；完成状态来自解析结果，不解析展示
   文案。诊断不能同时以 WarningEvent 与 HookCompleted 双份刷入 CLI。
3. CLI 引入 hook 活动状态与 timer 所有者；按 run.id 配对、快速成功隐藏、较慢运行
   只显示瞬态摘要；结束/取消/关闭清理计时，不修改输入历史或权限确认状态。
4. 完成输出由独立 hook renderer 渲染，采用上述标题/缩进与 Context 过滤规则；
   Transcript 与历史回放只能记录应持久显示的部分。
5. 可控时钟测试边界 299/300ms、reveal 后 600ms、重复 start、无 start 完成、
   并发运行不同/相同 statusMessage、quiet success、各种失败/控制结果。
6. 真实 Runtime→CLI→PTY 验证常见宽度/长提示、正文流结束顺序、Ctrl+C/关闭、
   历史页与焦点；事件或 renderer 单元测试不替代该链路。

本批不新增官方服务、插件账户页面或云端入口，也不把 /hooks 授权面板的实现算入
仅生命周期显示的完成范围。授权面板仍为独立未完成项。
