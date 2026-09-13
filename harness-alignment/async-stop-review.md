# 异步 Stop / SubagentStop：会话所有权与恢复边界

## 后续：混合执行和独立压缩入口

重新追踪原生tasks/compact.rs::CompactTask::run的普通压缩分支 →
compact.rs::run_compact_task/inner；它运行自身Pre/PostCompact事件，但不调用
drain_async_hook_results。既有异步结果由session/turn.rs::run_turn的新prompt前或
采样/工具之后消费。故“手动压缩必须消费已有异步Stop结果”不是原生要求，不新增
这条消费路径；其他Hook事件是否实现仍另行审计，不能以此整体免除Pre/PostCompact。

test_async_stop_hooks把两事件的延迟Warning场景扩为普通继续/中间手动compact：
compact实际生成摘要且成功，未消费Warning，队列保留一项，下一普通Turn显示且不
注入停止/阻塞指令。另一个真实混合命令场景把同步命令放在前面，让它等待后面的
异步进程启动；最终同步命令完成、Turn结束而异步命令仍在等待释放，且只发一组
同步生命周期通知。该组合与manual_compaction_lifecycle共18通过（15d648）。
这是现有实现的行为补证，没有通过改生产调度或增加压缩消费入口来让测试通过。

新增后台结果入账失败注入：原Turn已完成，实际命令只执行一次；complete写入异常
进入日志、claim保持未知、不发布成功结果/诊断队列、不重试副作用。移除定义后新
Turn仍成功，关闭模型一次。上一轮测试会话句柄已失效，未据此推断结果；重新执行
同步/异步Stop、SubagentStop、恢复、插件刷新、evaluation、Runtime关闭/构造、
CLI Hook输出与手动压缩生命周期组合，**232 passed，25.36秒**（2e1e0e）。
该结果与下方历史集合重叠，不累加；本轮测试文件ruff检查、格式检查及git diff
--check通过（edcb6e）。这不是更新后的全量或异步PTY验收。

## 原生链路和原差异

参考ddf04ad26789d040f9ef6a96736f76602e35a6cc。
hooks/src/events/stop.rs → engine/dispatcher.rs将async handler交给
command_runner.rs::schedule_async_hook/schedule_async_task，不加入同步控制聚合。
CommandHookRuntime会话级持有任务集合及8个并发许可；配置刷新共享owner，关闭时
拒绝准入、取消并join。ConfiguredHandler::can_apply_control_effects仅对Sync为true。
events/stop.rs解析器对Async忽略控制，非JSON普通stdout不注入上下文；Stop和
SubagentStop本身不产生AdditionalContext。异步系统消息作为Warning在安全边界消费。
core/src/hook_runtime.rs::drain_async_hook_results处理完成结果，通知过滤器不发送
Async的同步HookStarted/HookCompleted。官方遥测和其他Hook类型不是本批新入口。

Corki此前在发现阶段拒绝async=true。新真实Runtime红例验证命令实际未运行，
Stop/SubagentStop两项均失败（a4e6f9，10.70秒），不是仅检查新接口存在。

## 已实施

- StopCommand保留asynchronous执行模式，已规范化指纹本来就包含async；执行request
  对async额外记录true。旧同步request不增加字段，旧v1批次缺模式时仍默认为同步。
- 完整批次授权/身份校验之后，先claim并安排全部async命令，再执行同步命令；不会
  因较早声明的同步命令尚未结束而阻止较晚async任务准入。同步命令调度本身未重写。
- core/async_hooks.py按会话拥有强引用任务集、8许可及结果队列，捕获当时command、
  shell/cwd/env/request。刷新定义不替换owner，也不撤销已经准入的命令。
- 后台效果使用同一真实POSIX进程组执行/清理和SQLite账本；后台存储失败只记诊断，
  不重试未知效果，不把已经完成的Turn改成同步失败。未完成claim冷恢复失败关闭。
- 队列只保留已解析error/warnings，原始stdout留在账本。Stop/Feedback不会成为
  后续控制、模型上下文或同步Hook生命周期通知。plain stdout也不冒充AdditionalContext。
- prepare（新用户输入持久化/模型请求前）和finalize（模型采样后）消费结果；工具
  运行中不插入消费，继续Step由下一prepare处理。Warning使用当前边界Turn的sink，
  后台任务不持有已经关闭的原Turn输出队列。错误保留日志。
- Runtime.aclose立即关闭异步准入，主执行停止后先await异步owner，再关闭模型、
  repository等依赖。owner独立close任务受shield保护，外层关闭等待者取消不丢清理。

## 行为验证

test_async_stop_hooks.py使用真实子进程、临时文件门闩和真实Runtime：

- 两事件均先TurnCompleted、后释放命令；配置刷新清空定义不取消旧任务，下一Turn
  显示原Warning，不再运行移除的命令、不注入Block/Stop、不发同步Hook通知。
- 9个命令只有8个先启动；释放后第9个执行且9份结果入账。关闭路径取消8个进程和
  等待任务，实查pid消失，9份未完成claim保持未知，无迟到输出。
- 两事件×unknown/completed恢复窗口：真实graph finalize前挂起并冷重开，unknown
  不重放，已提交结果复用；不重采样、用户输入不重复、原历史前缀保留、二次resume为空。
- 关闭期间挂住真实进程清理后的边界，取消外层close等待者；模型尚未关闭，释放后
  owner任务清空、模型恰好关闭一次、无晚到结果。

首轮新异步/child/旧恢复/evaluation95通过（932f8e）；并发/刷新/关闭4通过
（ff32f2）；异步冷恢复加Runtime构造/关闭48通过（9485ef）。最终扩大同步/异步、
来源/插件、冷恢复、Runtime关闭/构造及Hook输出**214通过，24.01秒**（3994b5）。
这些集合重叠，不能相加为唯一用例数量。另补8项异步输出分类与同步Hook真实PTY回归。
异步9项+输出分类8项+同步Hook PTY24项联合**41 passed，46.89秒**（5f7c9c），
24条forkpty弃用警告；没有把同步PTY称为异步终端验证。静态cadcf2通过（1169文件，
77包）；该批与214项仍存在重叠。

## 尚未完成及限制

本批支持已存在的本地命令来源的Stop/SubagentStop异步模式，不声称所有Hook事件
和MCP型Hook已实现，也不引入官方管理/执行器平台。转录文件和Hook授权UI仍开放。
只有POSIX实际进程组清理证据，Windows执行未完成；不是任意强杀下的exactly-once保证。
结果未知包含claim后尚未spawn的中断窗口，选择不自动重试，不猜测外部效果不存在。
更多混合失败/刷新故障组合仍需补验；手动压缩与一组同步在前/异步在后组合已按
本节上方源码和真实Runtime补证，不再将独立compact消费诊断误列为实现缺口。
完整A–F继续，之前全量基线早于本批生产变更。
