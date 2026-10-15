# 异步 PostToolUse：明确缺口与接入边界

状态：部分一致；基础异步执行与暖Session投递已接入，完整恢复仍开放。
A/B/C/E范围，CLI暂停。下方初始缺失审计保留为修复前证据。

## 最新实施与验证

最新主循环事件复核纠错：固定基准core/src/hook_runtime.rs::
should_emit_hook_notification明确只允许非builtin同步Hook。drain_async_hook_results
虽调用emit_hook_completed_events，后者仍按该门控过滤；warning另用当前
turn_context发送。因此此前AsyncPost发送HookStarted/HookCompleted是实现错误，
不应以历史测试通过为依据保留。这是核心协议事件修复，不恢复CLI视觉工作。

两条真实direct/nested反例先失败（2f77eb），移除异步同步生命周期通知，context
继续按原来源持久化，warning改为当前活跃Turn的WarningEvent，error记录诊断，
不恢复官方metrics/analytics。所有原脚本/反馈/恢复断言保留；emit→receipt
窗口可能重复的是warning，而非应该发出的HookCompleted。历史完成通知描述
由本段明确推翻；receipt仍保护重复context与诊断交付。
异步60/同步Post18/异步Stop15/receipt6联合99通过（c78162，26.57s），静态
1213格式/ruff/compileall/diff/依赖通过（a50939）。新增warning实际Turn归属
断言随后专项60通过（32649e，19.41s），涵盖同Turn、跨Turn及冷恢复/投递故障。
不以99的旧采集冒充包含该新增断言。

最新补充自动/失败压缩：cold_auto_compacted通过真实模型usage越过已配置预算，
冷启动下一请求自动生成普通摘要；cold_compaction_retry首次摘要返回认证失败，
断言原历史完全不变，再次手动压缩成功。direct/nested×三类输出新增12项通过
（1eeb2a，4.80s）；两者成功后下一请求均只有摘要、不复活context，不重复
脚本或完成事件。自动场景覆盖跨Turn冷启动prepare，不是后台反馈到达任意
采样时刻的全部自动压缩组合。

首次12失败（0075d4）源于新测试夹具给ModelCompleted传usage=None，尚未进入
压缩；改为默认ModelUsage后通过，不能记作修复生产bug。完整异步Post60项与
Pre/Post压缩、异步Stop、receipt联合105通过（314ac5，30.17s）；静态1213格式/
ruff/compileall/diff/依赖通过（6973c5）。本批未修改生产代码。
摘要安装中途失败/取消、正在执行工具时反馈到达与压缩交错、完整旧批次兼容
仍开放；不要将上述局部证据升级为全部C8/E7验收。后续回到A–E差异优先级，
保留Hook剩余矩阵而非继续以易扩展的测试组合替代其他核心差异修复。

最新压缩证据：cold_compacted×direct/nested×三类控制输出6项，真实普通摘要
请求同时包含异步context和工具结果，摘要返回后原始历史前缀保持；关闭并重建
Runtime后请求包含CompactionItem和摘要，不复活ASYNC_POST_CONTEXT，不重复
脚本、工具或HookCompleted。六项通过（5eb957，2.57s）；异步Post48项与既有
Pre/Post压缩、异步Stop及receipt联合93通过（98318f，24.96s）。本批只新增测试，
生产代码未变；ruff/1213格式/compileall/diff和依赖检查通过（d4d2da、b9c34d）。

本证据限已投递反馈的手动普通压缩成功后冷启动；不是自动压缩所有时机、摘要
失败或安装中途崩溃的异步专属完整矩阵。不能把同步Hook既有压缩测试直接冒充
这些异步交叉场景。核心Hook阶段已具备执行/投递/冷恢复/故障/关闭/压缩实证，
剩余矩阵保留，后续应同时回到A–E其他未关闭核心项排序，CLI不恢复。

最新运行中关闭证据：cold_unknown×direct/nested×三类输出共6项，真实脚本
写PID后等待release；在原Turn已完成、脚本仍阻塞时Runtime.aclose（5秒界限），
检查Session任务清空且os.kill(pid,0)确认子进程不存在。保留原可信Hook配置、
放行release后重建Runtime开新Turn，再次冷重开，脚本计数仍一次、工具结果不
变、无虚构context/HookCompleted；持久化执行记录仍result=None，不篡改未知。
当前macOS实测6通过（d36b41），含新增账本断言的完整异步Post42项与Stop/
receipt联合63通过（16f42d，16.31s）。静态/1213格式/compileall/diff/依赖通过
（9c952c）。本批仅增加行为验证，未修改生产代码。

这关闭正常Runtime关闭对已启动脚本的回收/不重放组合，不等于所有取消或
OS强杀：关闭观察者反复取消、claim到任务归属前窗口、混合同步/异步拥塞、
压缩后的恢复与旧batch兼容仍需独立证据。下一批优先处理压缩后的反馈不复活，
随后回到A–E其他核心缺口，不把Hook单项局部绿当作整体完成。

最新投递故障注入：真实Runtime新增context append已提交后、receipt提交前、
receipt已提交后抛OSError三窗口，均验证失败Turn不采样，关闭后冷Runtime在
下一Turn只注入一份context、脚本/原工具只执行一次；再次冷启动不重复投递。
direct/nested×三类输出形成新增18项，与原18组合共36通过（a8537d，11.46s）。
这证明实际持久化API窗口恢复，不是OS强杀或任意取消窗口的完整证明。

完成事件不是exactly-once：emit之后receipt之前失败可使新Runtime再发一次
HookCompleted，测试明确允许该窗口两次事件，但模型context和脚本副作用仍
唯一。不把UI事件投递与SQLite提交声称为跨系统原子事务。

receipt损坏检查又发现Python数值等价问题：version=True或1.0被当作1接受。
6反例初次2失败4通过（539fa8），现在要求精确int且为1、字段集合精确、无
执行记录，避免错误跳过反馈。异步Post/Stop和receipt联合57通过（ae9d23，
14.31s）；ruff/1213文件格式/compileall/diff/依赖检查通过（41281b）。
此新增单元校验使用仓库替身，不能替代损坏SQLite后的完整Runtime验证。
完整取消/进程关闭、压缩后冷恢复、旧batch兼容和首次扫描成本仍开放。

2026-09-12续修冷启动跨Turn：新增cold_next_turn×direct/nested×三类输出6项，
先失败（065d3a，6失败2.17s，下一请求确实缺少context），再修复。
repository新增Thread内按插入序读取batch身份的只读入口，Volatile复用同契约。
AsyncPostHooks在首次prepare读取旧批次，只接受已保存的异步结果，检查原来源、
执行模式、预算和request身份；未知/未claim绝不重启脚本。验证全部候选后入队，
通过原drain投递，在context及事件之后保存稳定delivery receipt，再ack内存队列。
已投递receipt在下一次冷启动跳过，不能无条件复活已压缩的历史反馈。

新18异步组合通过（522cb9，4.51s）；cold六项进一步加入投递后再次关闭/重建
Runtime，无重复HookCompleted、无重复context、脚本计数仍一次，6通过（6acb03，
2.55s）。同步Post/冷恢复/压缩/Runtime恢复/storage联合183通过（a82d6c，
51.27s）。ruff、compileall、diff通过；格式修正后1212文件检查通过、依赖检查
通过（f88b53）。这覆盖正常关闭后重建Runtime，不冒充OS强杀实验。

仍开放：append/事件/receipt之间再中断的故障注入、receipt损坏/旧批次兼容、
异步反馈参与压缩后的真实冷重开、未知运行中claim的真实关闭矩阵。首次恢复
目前读取本Thread所有Post批次并逐批查询；大量历史的分页和成本尚未优化验收。
下文“冷启动跨旧Turn尚未实现”是本修复前状态，按本段更新为部分验证。

- 新增core/async_post_hooks.py，复用AsyncHooks任务所有者但独立于Stop诊断队列。
  Post batch现在原子保存同步/异步选择；异步claim后交给Session后台任务，工具
  不等待脚本，原工具结果不被block/stop改写。Runtime关闭先关准入再join所有者。
- outcome新增显式control=False：仍严格校验wire；合法context和systemMessage
  保留，语义控制不生效，exit2只是错误而非block。同步行为保持原路径。
- prepare在新用户输入落盘前drain；finalize在Stop决策前drain，并检测仍未进入
  request_items的异步context以继续正常预算受限采样。没有把context伪装成用户。
- 队列peek→持久化context→完成事件→ack，取消不提前丢队列；使用稳定async
  context ID去重。完成事件经当前活跃sink发送，保留原Turn身份。
- 同Turn冷恢复按保存的asynchronous解析：未知异步结果不重跑、不重建nested
  block；已完成context使用相同async ID。尚未覆盖所有异步冷恢复故障窗口。

原6反例由红变绿，联合同步Post/冷恢复 **132通过32.33s**（332542）。随后
扩展next_turn投递×配置移除，现12真实场景；与异步Stop、同步解析、Pre/Post
压缩联合 **64通过13.36s**（332209）。这两个集合重叠，不相加当独立用例数。
全src/tests ruff、1212文件格式、compileall、git diff --check通过（a4c539）。
旧14193基线早于本次生产修改，不能当作本次修改后的扩大回归。

仍需完成：冷启动新Turn发现旧Turn已保存但未投递的异步结果（当前仅原Turn
恢复会扫描）；取消/关闭/并发混合事件与投递再中断；压缩后的不复活及全预算/
显式Stop矩阵。当前暖Session队列不能替代持久化跨Turn交付索引，不能称完整
async验收。下一优先项是冷启动旧结果交付及对应真实Runtime故障测试。

最新反例：tests/integration/test_async_post_hooks.py新增真实Runtime的
direct/nested × block/stop/unsupported共6项，首次运行6失败（fbf889，19.60s）。
当前失败在第二次模型采样等不到受控脚本的hook-started标记，与run跳过
asynchronous选择一致；尚未走到反馈第三次采样断言，不能据此证明投递行为。
测试以release文件放行实际脚本，并观察complete_hook_execution提交完成，
不使用任意固定延迟证明后台结束；预期工具结果保持原值、合法context进入
第三次采样、控制字段不生效且完成事件带warning。脚本会在finally放行并关闭
Runtime。新增测试ruff和格式检查通过；没有xfail或skip掩盖功能缺失。
本批尚未修改生产实现，旧扩大回归仅是这些反例加入前的基线，当前不宣称全绿。

Codex固定基准ddf04ad26789d040f9ef6a96736f76602e35a6cc：

- hooks/src/engine/dispatcher.rs::execute_handlers_with_metadata把Async handler交给
  command_runtime.schedule_async_hook，并从同步结果聚合中排除，不等待脚本结束。
- hooks/src/events/post_tool_use.rs::parse_completed：异步handler不能应用控制效果。
  合法wire的additionalContext仍可保留，包括携带不支持updatedMCPToolOutput的
  情形；block/continue=false不阻断主循环，exit2不是授权异步阻断的特殊出口。
- core/src/hook_runtime.rs::drain_async_hook_results提取Context条目：新Turn入场前
  直接记录历史，保证位于新用户输入之前；采样及工具完成后交给当前pending-input
  队列，供下一次采样。warning及完成事件独立处理。
- core/src/session/turn.rs::run_turn明确在首用户输入前、采样及其工具完成后两个
  安全边界drain，不在任意后台完成回调中直接改模型快照。

Corki当前：

- post_tool_hooks.run准备批次时跳过asynchronous并警告，尚未启动异步Post脚本。
- AsyncHooks已有session所有权、并发限额、完成队列、claim不重跑及关闭join基础。
- StopHooks._start_async仅存(error,warnings)，drain只发WarningEvent；它故意丢弃
  控制和原stdout。不能直接复用此结果类型来宣称异步Post上下文已对齐。
- graph._prepare_model_context与finalize已有Stop诊断drain，但仅增加context append
  还不能保证新用户输入前顺序或最终采样后pending-input使Turn继续执行。
- 现有post_hook_recovery按同步控制解析恢复；纳入异步配置前必须区分执行模式，
  否则未知异步claim会被错误重建为block反馈。

实施要求（尚未执行）：

1. 复用session任务所有者，扩展为带事件类型、原来源与context结果的明确队列；
   保留Stop现有诊断语义，不通过共享队列改变其控制边界。
2. Post batch同工具结果原子保存同步/异步选择；异步claim属于脚本副作用，不因
   Turn结束而重跑，关闭须join实际进程。同步脚本继续独立聚合。
3. 严格wire解析与control=False语义分离；不能把同步outcome的错误context过滤
   不加区分地套用到异步。完成事件不依赖已经关闭的原Turn输出队列。
4. 对接实际入场及采样后pending输入边界，验证新Turn用户输入前顺序、同Turn
   下一采样与完成后晚到结果；保存预算、溢出路径和稳定ID，不改普通模型协议。
5. 冷恢复区分未claim、结果未知、已保存未投递、已投递/已压缩；未知异步结果
   不能重跑或阻断。投递自身再中断须有幂等证据，不能只靠内存seen集合。

验收至少包含真实受控脚本：同步工具不被后台脚本等待；block/stop无控制作用；
context/警告能在上述两边界到达；跨Turn/配置移除/并发/重复取消/关闭及冷恢复。
扩大回归期间未修改生产代码；该批现已14193通过、7条件跳过且退出0，见
core-regression-2026-09-12.md。可以开始后续实现，应先新增真实Runtime反例。

## 续查：不能只在现有 drain 后追加上下文

当前 graph._finalize 的持久化待处理输入检查仅接受 UserMessageItem，且要求
同 turn_id、ID 尚未进入 request_items。异步 Post 的 developer 上下文即使已经
append 到历史，也不会被该条件识别为需要继续采样。只扩展 StopHooks.drain
写入 context 会造成“已保存但本 Turn 不再采样”的可观察缺口。

实现时需单独表达异步反馈的待投递状态，不将 Hook 反馈伪装成用户消息；同时
保持 StopDecision.STOP 与预算限制的既有优先级。至少新增“最后一次模型响应
期间后台 Post 完成，随后仍有一次包含反馈的采样”反例，以及显式 Stop/预算
耗尽不被反馈无限延长的对照。当前此项仅完成调用链定位，尚无实现或通过声明。
