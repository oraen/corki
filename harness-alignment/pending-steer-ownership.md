# 未提交steer的取消归属：A/C/F待修复差异

## 最新入口审计：运行中CLI压缩应拒绝，不应替换

完整入口追踪发现上一批CLI策略不一致：slash_command.rs::available_during_task把
Compact明确列为false；slash_dispatch.rs::slash_command_blocked_by_active_task检查task_running
和user_turn_pending_start，dispatch_command在到达Compact分支前报错并return。
只有空闲时才clear_token_usage→设置task running/pending→AppCommand::Compact→
app/thread_routing.rs::thread_compact_start。core/session/handlers.rs::compact再spawn
CompactTask，tasks/mod.rs::spawn_task的Replaced是核心入口语义，不能反推TUI允许忙时替换。
Corki当前_consume_realtime_turn却在忙时/compact取消并排优先队列，是行为不一致。
本批修复方案：忙时明确拒绝、不发模型输入、不取消、不排队；用户先停止或等待完成，
再空闲提交/compact。SDK层有任务的Replaced未提交输入交还修复继续保留。
验收：真实Runtime中忙时compact不改变第一请求，随后显式stop恢复Enter+Tab，空闲
compact只使用已提交历史。上一批“三组合通过”证明旧行为存在，不再作为CLI对齐证据。
原生tests/slash_commands.rs::queued_slash_compact_dispatches_after_active_turn还明确
Tab的ParseSlash队列在完成后执行；这与忙时Enter拒绝不冲突，Corki保留该队列入口。
三组修订测试先红（7b9840，40966终态3失败），生产修改后CLI/输入边界/所有权89通过
4.11秒（313c44）。去掉CLI主动Replaced分支及随附的特殊恢复例外；SDK层replaced
边界测试继续通过。93921已终态exit0：86通过8个forkpty警告19.57秒（fea20e），
覆盖新增Tab压缩及实际40/100列忙时拒绝+停止恢复PTY。静态1072文件77包通过
（583e92）。停止poll，本批无活动测试进程。压缩期间的活动composer/输入排队仍需
独立审计；本批不声称完整F完成。

以下为历史批次记录；其中忙时Enter替换及其恢复/Tab策略已被上述原生入口审计推翻，
不是当前行为，也不是保留理由。

状态：普通取消及有任务的replaced路径已接实现；冷恢复等仍开放，不是完整完成证明。

## 最新：替换取消与显式压缩的不同所有者

重新核对同一原生基线abort_all_tasks/abort_turn_if_active：有task的Replaced也清pending，
不是正常on_task_finished。新增replaced输入边界、真实Runtime+CLI压缩含pending两测试
先失败（a51308，36339终态exit1）：历史仍有one/two，CLI草稿未恢复。
Runtime现对所有TurnCancelled按持久化身份返回未提交输入，保留append成功项。
CLI将返回steer独立于待执行操作保存；join后替换取消只恢复steer，保留/compact和Tab
队列。普通停止或调用方取消则恢复steer+队列。恢复失败将返回项留在暂停的队列供编辑，
不覆盖原取消；事件与take仍按原item.id去重，不按文本去重。
专项输入边界/CLI/停止PTY91通过19.91秒（3e433a），覆盖此前两个失败；扩充压缩组合
3通过0.83秒（3ea418）：无steer、有steer、有steer+Tab，模型分别只见初始/摘要/可选
后续任务，未提交steer不进入这些请求。静态1072文件/77包通过（04f42c）。
广回归34950已终态exit0（69404d）：695通过、67个forkpty警告、119.00秒，覆盖
CLI/core/protocol/realtime单位测试、输入边界/CLI所有权集成、全部现有e2e；停止poll。
旧全项目20572已终态exit0，12779通过7跳过1863.41秒（b43d8c），停止poll；其采集
早于输入恢复和本批修改，仅为早期基线，不能替代本批验收。
仍需补替换的真实PTY、恢复失败的替换组合、冷启动草稿以及其他故障归属核对。
补充追踪protocol.rs：TUI对TurnStatus::Interrupted统一on_interrupted_turn并恢复Tab；
本批保留Corki已有“运行中/compact替换且保留Tab”路径，尚未完成原生Compact入口、
状态映射与Tab处理的整条对照，三组合通过不能作为这条CLI策略已完全对齐的证据。

## 本批实施与当前边界

Runtime在普通TurnCancelled收尾读取已提交item身份，未提交的_unrecorded通过新增
TurnCancelled.unsubmitted_inputs交还宿主，不追加ConversationItem；append已成功但
ack未返回的身份继续保留。非取消失败/预算沿旧flush，未以本批替代其审计。
事件和进程内take_unsubmitted_inputs共享原UserMessageItem身份，后者在Turn join后
显式消费，供终态事件被取消打断的SDK/CLI取回，不是磁盘草稿存储。
CLI在事件与join后接收，按item.id去重（相同文本的不同提交保留），未提交Enter置于
Tab队列之前，沿已完成的停止恢复链返回草稿，不自动发下一Turn。取回未处理宿主输入
也会抑制自动出队，不能因为当前事件缺失而把它当普通后续任务。

旧stop/append_cancel两测试按新契约先红（5a8325），修复后连同控制80通过。
append_cancel保留已提交one、只返回two；普通stop返回one/two，下一无关请求不含
返回的item.id；take接口二次读取为空。真实PTY增加两次同文Enter+两Tab取消组合，
顺序完整且重提才采样，含输入边界18通过17.20秒（06c2e3）。另两条旧stop测试依赖
“必须入模型历史”，已改为验证取消的comprehension无法返回事件时take仍交还内容。
广回归60286已终态726通过2失败117.31秒（0fd082）；两失败正是旧stop落库断言，
已更新为验证宿主交还而不是删测试。最终输入边界/CLI/实际停止PTY89通过19.33秒
（34adf5），覆盖这两项。两组不是一次全绿结果，不将旧全项目20572作为本批验收。

限制：交还是进程内契约，宿主需要自行保存可跨进程恢复的草稿，README明确说明；
CLI冷启动草稿恢复未补齐。未修改用户旧ConversationItem，旧取消指令归档不擅自删除。
其他故障归属仍需继续参考原生，不能把取消修复称整个A/C/F闭环。

## 实际影响与复现

真实LangGraphRuntime临时库探针（29fc0d，27240已终态exit0）：第一次模型停在
ModelTextDelta之后，调用steer("UNSAMPLED_STEER")立即cancel_active；模型仅采样1次，
终态TurnCancelled，但load_display_history的用户记录为initial、UNSAMPLED_STEER。
再发unrelated next request，普通模型请求仍带initial、UNSAMPLED_STEER、新输入。
临时目录已清理，无用户会话修改，无外部模型请求。

因此这不是输入丢失，而是未提交的指令被无条件升级成模型历史：取消后用户未重新
确认，下一次无关请求仍接收它。若只在CLI复制恢复，还会同时留历史副本和新输入。

## 原生两条路径不可混淆

基线ddf04ad26789d040f9ef6a96736f76602e35a6cc。

- core/src/tasks/mod.rs::abort_all_tasks、abort_turn_if_active先取走active task，
  handle_task_abort通知取消、等清理、记录中断标记/发TurnAborted，随后
  session/input_queue.rs::clear_pending清waiters与pending_input.items。
- 同文件的正常on_task_finished会take_pending_input_for_turn_state，再
  run_hooks_and_record_inputs。这条正常收尾不能被当成外部abort也应落库的依据。
- session/tests.rs::abort_empty_active_turn_preserves_pending_input明确无task的空
  ActiveTurn例外，不可把“所有未处理输入取消就删除”套到此场景。
- tui/input_restore.rs::on_interrupted_turn恢复未ack steer→queued→当前草稿。
  tests/review_mode.rs::manual_interrupt_restores_pending_steers_to_composer及
  manual_interrupt_restores_pending_steers_before_queued_messages验证恢复文本、不发
  submit op、不插入该待处理输入的历史展示。

## Corki当前链路

RealtimeController.steer分配UserMessageItem后放入_queue和_unrecorded；dequeue不
等于ack。graph._persist_realtime_input通过window.prepare持久化后ack并发
RealtimeInputAccepted；因此ack是持久化边界，不是模型实际采样完成边界。
Runtime._execute_run所有终态都调用_flush_realtime_inputs，把剩余_unrecorded追加为
普通ConversationItem并ack，随后deactivate。这正是探针所见。
旧test_turn_input_boundary::test_accepted_input_survives_failure_or_cancel_without_duplicate_history
将budget、stop、model_failure、append_cancel放在同一“必须全部落库”断言下；它需要
按原生不同入口和已提交/未提交状态重新划分，不能用其全绿判定对齐，也不能直接删掉。
该旧四变体本轮实际重跑4通过0.73秒（f475b6），只证明当前保留行为仍存在，不是修复
证据。1254已终态，当前唯一长回归仍20572。

## 下一步修复与验收约束

1. 分开“controller仍持有、尚未持久化”和“append已成功、ack/checkpoint尚未完成”。
   后者不能删除或重提，尤其append_cancel窗口；已采样文本也不能再次恢复。
2. 显式外部取消应返回/保留可恢复的未提交输入身份，不将其放入下一请求的模型历史。
   UI按身份跟踪提交与持久化确认，处理相同文本多次提交；不可只用字符串匹配去重。
3. 显式Tab队列仍归CLI；未提交Enter归Runtime/CLI确认契约。合并恢复按steer、Tab、
   原草稿顺序，已提交消息不恢复第二份，关闭/取消均须join拥有者后处理。
4. 未提交恢复不能破坏非交互SDK调用方，也不能无声丢失已接受内容；先确定返回/事件
   契约及短期恢复存储，再清理无条件flush。禁止修改用户已有ConversationItem来补救。
5. 分别验证外部stop、replaced、正常失败、预算结束、append前/后取消、进程恢复，
   下一轮请求不得携带未经重提的取消steer；旧已提交历史和原item身份保持完整。
6. 用实际Runtime+PTY验证Enter未进入请求→取消→草稿恢复→重提才采样，并叠加Tab
   队列、重复文本、输入清理、重复取消。明确不引入官方协议或远程历史服务。

以上为最初审计的验收约束；当前生产实现与验证状态以本文顶部更新为准，
不能先靠展示层掩盖尚未关闭的所有权差异。
# 最新核心审计：模型失败/预算结束与外部取消不同

Codex固定基线 `tasks/regular.rs::RegularTask::run` 在run_turn设置terminal_error后
直接返回，不为pending input再启动失败回合；`tasks/mod.rs` spawn站点仅在取消token
未取消时调用on_task_finished。后者错误分支先发Error/记错误，仍take_pending_input
并run_hooks_and_record_inputs。外部abort走前文不同入口，不能据此恢复无条件flush。

Corki `_execute_run` 先选择TurnFailed并关闭输入，非取消才_flush_realtime_inputs；
取消按已提交身份过滤并交还。此项正常模型失败待处理输入的归属与原生一致，
无需生产改动。预算限制是Corki的本地终态条件，本次只验证其正常收尾契约，
不声称Codex具有相同max_steps策略。

新增真实Model.stream抛终态ModelError（正文输出前/后）测试：只发一个TurnFailed，
不自动重采样，pending输入落库且不作为取消草稿返回；半截正文不记成功AssistantMessage；
下一次显式请求只携带一次原item身份。扩充既有commit故障/预算测试下一请求身份断言。
13项通过2.03秒（416c54），此证据区别于原先只检查flush历史的测试。
尚未覆盖输入收尾持久化本身失败后的跨进程恢复；不得把本项扩大为所有故障窗口完成。

组合验收：输入边界、pending采样、realtime steering和CLI stream cleanup 27通过
4.58秒（49e27b），61646终态；全项目原22649仍live41%（35a0fc）。全项目静态
1084文件77包通过（c9b91f）。未修改正常失败flush或取消交还的生产行为。
# 收尾输入写入失败：实施前差异

原生session/mod.rs::record_prepared_conversation_items先更新内存history，再persist_rollout_items；
rollout/src/recorder.rs::flush及RolloutWriter保留pending_items，后续barrier重试未写后缀。
Corki RealtimeController.deactivate保留_unrecorded，activate拒绝未保存的旧输入，但
Runtime只在当前回合非取消收尾调用_flush_realtime_inputs，没有下次admission重试入口。
结果是一次输入保存故障后实时回合永久拒绝；非实时回合又可能绕过遗漏输入。
修复只重试已选择正常收尾的输入写入，不能把取消待交还输入静默转成历史；
SQLite按item身份幂等，验证append前失败和commit后报错均不重复，无模型/工具重放。
本批范围是同Runtime存活期间恢复，跨进程待写输入仍未有持久outbox。

已实施 `_input_flush_pending` 区分正常收尾的待重试写入与取消交还；只在正常flush
开始时设置，append+ack成功才清除。在_start_turn（普通/实时/compact共用入口）
和实际resume_pending启动前、持有admission/lifecycle锁时补保存；失败不创建新回合。
不增加模型重试，不回放工具。Runtime的thread_id构造后固定，不跨线程挪动待写输入。
SQLite._append_items_in_connection按既有id核对语义，commit后报错的重试不重复。

4组合先红（dd4969）：非实时遗漏/残留输入，实时被activate拒绝；修复后增加第二次
保存仍失败的探针，确认无新采样、输入身份仍在，然后第三次恢复成功。输入边界/
采样/steering/CLI清理31通过（b73d0a），静态1084文件77包通过（882ad0）。
全项目原22649仍live50%（509530），采集早于本批，不能作为最新修复验收。
仍未处理进程退出前待写队列的持久outbox与最终关闭重试；不可称所有恢复问题解决。

扩大回归36146终态：core/realtime单位及上述输入/采样/steering/清理54通过4.86秒
（718951），停止poll。仅证明本批作用范围，不替代全A–F验收。
# 关闭待写输入（实施前）

原生rollout/recorder.rs::shutdown先drain pending，失败保留writer供下次flush/shutdown。
Corki _close_resources无输入flush且总会关闭repository/checkpointer/thread writer；
_close_task还永久缓存异常，无法后续重试。需先停止执行/后台资源，再保存正常收尾待写
输入，失败保留存储资源及独占thread writer，仅允许重复aclose重试，不重开Turn入口。
并发/取消关闭等待者不能打断共享清理，不能重复关闭已结束的模型/MCP。
取消待交还输入不落库；持久成功后冷读一次原id。硬退出/断电outbox仍不在本批完成声明中。

已实现：_close_resources在执行/后台服务关闭后调用_close_storage_resources，正常收尾
待写输入flush失败则保留repository/checkpointer/Thread writer；_closed维持True。
重复aclose仅在此存储barrier未完成时重启存储清理任务，模型/MCP不重复关闭；
其他原有资源关闭错误仍共享原close task。保存成功后才关闭存储并释放writer。
普通取消的_unsubmitted_inputs不由此flush。

2项先红（2b0e0c）：关闭无flush且错误未报告。修复后输入/关闭专项39通过（3d5ad0）。
扩充存储重试等待者取消、并发aclose，证明共享存储任务不受取消影响；使用全新Runtime
读取数据库，原待写item id恰好一次且模型只有原1次采样。静态1084文件77包通过
（b9da3b）；原全项目22649仍live54%（e63059），不作为本批覆盖证明。
永久存储失败会显式失败并保留存储资源供宿主重试；不宣称硬退出后内存队列可恢复。

本批最终扩大验证：2837终态76通过7.53秒（48ac8a），覆盖core/realtime单位、输入
边界/采样/steering/CLI清理与runtime_shutdown。停止poll2837，goal仍active。
# 输入追加的线程所有权（实施前）

原生rollout recorder将写命令交给独立writer，调用方等待ack取消不会销毁writer，shutdown
仍通过队列barrier等待。Corki SQLiteSessionRepository.append_items直接await to_thread，
取消等待者会让线程继续但释放admission/lifecycle锁，后续关闭可能与未完写入重叠。
同类save_turn/append_partial_item已使用本地_joined_write；普通append应复用相同契约。
验证必须阻塞真实同步_append_items线程，取消admission重试后关闭仍不能越过写入，
完成后传播CancelledError，存储按id去重，不重跑模型/工具。

已复用SQLite._joined_write用于append_items（不改变事务或身份规则）。真实线程gate
先红：取消两次后admission task已结束而写线程未结束（ef1eba，76197随后终态4d1832）。
修复后重复取消仍等待线程；并发close等待lifecycle锁，释放写线程后才传播取消，
关闭重试按原idack且模型只有初始请求。输入边界/关闭/storage单位128通过6.37秒
（353f09），静态1084文件77包通过（678932）。

原全项目22649已终态12851通过7跳过1891.18秒（cf5967），停止poll；采集早于近期
Markdown与输入/关闭修复，不作为这些改动的全项目验收。7跳过原因沿原测试门槛，
本轮未修改或绕过。此修改不等于所有SQLite方法均已完成取消所有权审计。

额外组合63726终态72通过13.22秒（27e0ba）：recovery_and_concurrency、手动压缩
恢复/生命周期、初始化、checkpoint清理、memory_pipeline_runtime及token预算恢复。
两批作用范围共200项通过；无新全项目启动。完整goal保持active。
