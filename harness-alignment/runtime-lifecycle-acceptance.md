# A1：Thread、Turn、Model Step生命周期核验

## 2026-09-22：逐项完成顺序与模型终态一致性

继续追踪 Codex `core/src/session/turn.rs::try_run_sampling_request`：
`ResponseEvent::OutputItemDone` 按到达顺序处理并将工具 future 加入有序队列；
终态不能反过来改写已完成项的历史顺序。Corki
`core/graph.py::_call_model_owned` 原先逐项持久化并可能启动工具，随后却只
检查每个已完成项是否出现在 `ModelCompleted.items` 中，未检查顺序。
真实 Runtime 反例先红：逐项输出 first→second，终态返回 second→first，
Turn 被标成功且 final_answer 变成 first；已发布历史与模型 Step 顺序相悖。

现在终态必须以完全相同的已完成项序列为前缀，允许其后追加尚未逐项上报
的合法项；不一致归为模型协议失败，不提交成功模型 Step。工具组合用例
验证正文→工具逐项完成后若终态反序，已启动工具会被收束、结果唯一留账，
但 Turn 不会伪成功。模型继续/逐项输出/有序工具/截流/恢复七文件
**137 passed**；初次扩大命令误写不存在的 `test_model_failure.py`，零收集
退出 5，纠正文件范围后上述七文件实际退出 0。Ruff 与格式检查通过。
生产修改后非 CLI 主组 **15350 passed、7 skipped / 819.35s**，
单进程敏感组 **459 passed / 58.97s**，两组退出 0，合计
**15809 passed、7 skipped**。七项跳过仍是六项缺历史原生编译器、
一项文件系统不接受非 UTF-8 文件名。该补证不等于 OS 强杀或任意外部
副作用的 exactly-once 保证。

基准：本地Codex固定ddf04ad26789d040f9ef6a96736f76602e35a6cc，普通本地
任务/模型路径；不引入官方账户、远程压缩或专有模型协议。CLI对齐暂停。

参考Thread入口另已追踪thread_manager.rs的start_thread→start_thread_inner→
spawn_thread→finalize_thread_spawn：恢复已有运行Thread复用原对象，冲突rollout
路径拒绝；重复发布者shutdown后报错。Corki没有同一ThreadManager对象返回API，
以持久身份加写者锁拒绝第二个独立Runtime写入，保证同一Thread不出现两个活动
写者，而非宣称两个API返回值一致。参考StepContext明确冻结settings、MCP绑定及
tool_router；ActiveTurn/RunningTask分别持有turn_state、取消token、task handle
及完成通知，对应Corki的TurnRun与每Step快照所有权。

## 源码与行为映射

| 边界 | 参考与Corki真实路径 | 本轮验证 |
|---|---|---|
| Thread身份及单写者 | Corki持久ThreadRecord/session identity，Runtime初始化取得线程写锁，关闭依赖后释放；不同Thread不修改对方账本 | thread_session_identity覆盖旧库并发迁移、首次创建竞争、已有身份不覆盖、损坏身份拒绝；thread_writer_ownership覆盖双Runtime、初始化失败、清理及子进程死亡释放 |
| Turn准入 | 参考tasks/mod.rs由active_turn持有任务；Corki _start_turn以turn_lock串行准入，lifecycle_lock内注册TurnRun后才释放 | turn_admission验证旧观察者暂停不阻挡新任务、旧观察者关闭不取消新任务、仍等待旧任务持久化/清理 |
| 首次落盘 | Corki _execute_run拥有第一笔RUNNING写入及finally，不由观察者协程直接拥有 | turn_start_write正常/realtime/compact × 提交前后 × stop/consumer/close，单一取消终态、写入join、输入身份保留、冷resume不复活 |
| 正常/失败/取消终态 | 参考on_task_finished区分TurnAborted与TurnComplete.error，并清理active_turn；Corki将同一语义显式映射为TurnCancelled/TurnFailed/TurnCompleted | runtime_shutdown、turn_admission、recovery验证模型错误保留、取消传播、已选择终态不因关闭重分类、终态存储故障不重采样完成节点 |
| Step冻结与身份 | 参考session/turn.rs保留step_context并在循环中切换；Corki capture_step_settings同步checkpoint后prepare，工具快照和模型设置分别持有，模型结果验证Turn/Step/item/call身份 | active_step_settings覆盖准备中更新、重试不改当前Step、冷checkpoint恢复、无效快照拒绝；recovery新增同响应多个Step ID与重复item ID拒绝，连同跨Turn结果拒绝均无错误模型项落盘且不遗留RUNNING |
| 冷恢复 | Corki resume_pending仅选最新RUNNING，用业务历史/模型提交记录和LangGraph checkpoint决定恢复节点；完成/失败/取消不自动复活 | recovery验证已提交模型不重采样、工具结果复用、未知结果不重执行、真实RUNNING节点取消后恢复；turn_start_write验证终态取消后resume为空 |

TurnStatus.CREATED是兼容值对象，不表示每次Runtime准入必须单独写一笔CREATED；
实际先注册拥有者、写RUNNING，再写终态。Model Step不是第二套独立后台任务，
其请求快照、模型结果、计数和恢复位置由Turn的graph/checkpoint持有。Python的
独立TurnFailed事件对应参考TurnComplete带terminal_error，不能按名字判为失败被
标成功，也不复制官方事件封装。

## 本轮证据

七文件联合131通过、18 deselected、0失败（f65e7f，18.71秒）：
test_turn_admission、test_turn_start_write、test_runtime_shutdown、
test_active_step_settings、test_thread_writer_ownership、
unit/storage/test_thread_session_identity、test_recovery_and_concurrency。
其中排除mixed_output_commit的18项旧CLI混合场景，属于用户暂停范围，不计通过。
新增两个非法模型身份场景均在真实Runtime中检查协议失败、无助手项落盘及无
遗留RUNNING，不只单测_validate_model_items。Ruff及format检查通过。

A1本地Runtime核心生命周期按上述证据核验；不是宣称完整A–E完成，也不将
CREATED枚举、日志或“没有发现错误”当作证据。E4同步构造失败的资源清理选择
仍待用户决策；A7/C8的Hook投递与压缩剩余交错、E7各资源关闭故障仍独立开放。
子进程死亡及文件锁验证属于当前POSIX宿主，不冒充Windows运行证明、断电/fsync
证明或任意外部副作用的全局exactly-once。
