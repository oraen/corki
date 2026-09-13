# A5/A7：工具结果的有序增量发布

本次修复后的非CLI全量现已通过：35013实际退出0（c86532），15504 passed、
1非UTF-8文件名限制跳过，535.66秒；完整日志full-regression-35013.txt。
下方前缀冷恢复与全量待刷新均已有证据，A5调度需求按本文逐项表归并为已核验。
不以此关闭任意外部副作用原子性、其他Hook交错或E4/E7全部资源故障。
88968/64194失败与测试修订记录原样保留；没有用拼接两次失败冒充全绿。

最新全量88968已结束，退出1（5bc64a）：15497通过、1记忆心跳测试超时、
1文件系统限制跳过，544.59秒。日志full-regression-88968.txt。不是全量通过。
超时测试计时包含Git/SQLite准备；增加慢准备两例复现后，将1秒模型关闭检查
放到实际心跳故障之后，保留其它断言，未改生产代码。记忆/恢复339联合通过；
详见memory-heartbeat-test-timing.md。下一步刷新全量，不再观察已结束的88968。

## 最新：真实进程退出窗口已补证，全量运行中

新增 tests/fixtures/ordered_tool_crash.py：真实 Runtime、SQLite/checkpoint、
并行 head/tail。两项外部文件副作用均 flush/fsync；head 结果已进入业务历史后
直接 os._exit(29)，tail 仍在 handler 内等待，不执行正常取消或 Runtime 收尾。
test_ordered_tool_publication.py 新增 batch/streamed 两项冷恢复：
先确认旧 Turn RUNNING、模型提交存在、历史仅 head，再新建 Runtime resume。
断言历史前缀逐项不变、head/tail 结果各一个且顺序保持，head 成功/tail unknown，
零旧工具重放、后续一次模型采样、原输入一次、副作用文件不变，再 resume 为空。
不宣称未提交外部副作用可以 exactly-once，也不将 unknown 冒充成功。

与 live_tools/task_ownership 联合 50 passed，9.54 秒（df9384），68053 退出0。
本轮未修改生产代码。上轮全局格式提示用 formatter 修正了原文件的引号格式，
不改测试语义；全局 ruff check、1314文件格式、compileall、依赖检查及 diff
检查均通过（c01eab；该 shell 的退出1来自最后查无 pytest/execnet 进程）。

非 CLI 全量已启动，句柄 **88968**，不是已通过：8 workers/loadfile/禁重启，
unit+integration 排除 unit/cli 和 integration/test_cli*.py，addopts=''、-q
--tb=short -rs。资源复核12逻辑核/18GiB、memory_pressure报告39%空闲，启动前
无pytest/execnet；null keyring、当前sandbox及五个历史compiler同74939，
路径均已确认存在（6aa36f）。启动后冻结被测代码/测试；必须继续同一句柄观察，
不得据此重启或提前宣称全量通过。74939仍仅为本次生产修改前的全量基线。

基准：Codex `ddf04ad26789d040f9ef6a96736f76602e35a6cc`。
仅普通工具结果进入业务历史的时机，不涉及专用模型协议或 CLI 对齐。

## 源码差异与验收

Codex `core/src/session/turn.rs::drain_in_flight` 对 `FuturesOrdered` 每次取出
一个结果就调用 `record_annotated_conversation_items`，不等待整组全部完成。
`tools/parallel.rs` 的就绪等待和读写锁控制执行准入；执行顺序、完成顺序、
历史发布顺序是不同边界。

Corki `core/graph.py::_call_model` 和 `_execute_tools_owned` 原先均在
`LiveTools.finish()` 的 gather 完成后一次 append 全部结果。
虽然 `_execute_one` 已逐调用持久化执行账本，这不等于业务历史已经发布：
尾部工具等待期间，前缀结果无法由历史读取者看到。账本不是丢失，亦不能据此
声称副作用未提交；需要修正的是已完成有序前缀的可见时机。

`test_ordered_tool_publication.py` 使用真实 Runtime/SQLite、两项并行工具、
显式阻塞尾部。模型响应已结束，要求尾部释放前历史出现且只出现 head，
后续模型仍须收到 head/tail 顺序，工具各执行一次。batch/streamed 两项在
原实现中均失败于等待历史前缀（7888ae，2 failed，5.92 秒，退出 1）。
首次夹具错误使用不同 Step ID，工具未执行即超时（0edabc），已修正，
该次不作为生产行为反例。

修复须保持：顺序发布不阻塞后续任务已报告的致命错误；取消与存储失败由
原有 owner 取消并 join 所有任务；恢复使用稳定结果身份，不重新执行已完成调用。
前缀发布不代表可以提前下一次模型采样或接入 steer。

## 当前实现与结果

`LiveTools.finish(on_result=...)` 按提交顺序逐项发布，同时等待共享 failure，
后面的 fatal 不会被前面的挂起任务遮住。原有无回调调用仍保留 gather 行为。
Graph 的流式正常完成、模型错误后的 drain、非流式工具节点均接入发布回调；
不重复整批 append，不变更执行 claim/complete 或稳定结果 ID。
写入回调失败仍进入既有异常/取消 owner，清理结束后再交付 Turn 终态。

新增测试共 6 项：前缀可见 batch/streamed 两项；前缀写入前失败、提交后应答
失败 × batch/streamed 四项。后四项的尾部工具一直阻塞，验证 failure 事件前
finally 已完成、一次采样、两项工具各执行一次、已发布 head 不重复。
streamed 异常清理可补写 head；batch 的提交前失败不会假称 head 已发布。

- 首组调度/任务所有权/就绪/gate/关闭联合 89 passed，9.71 秒（4bcc1c）；
  12730 最终退出 0（9d7fca），未将提前出现的 pytest summary 当进程结束。
- 加入新四项故障、Step admission/worker scope、新输入与恢复联合
  196 passed，16.88 秒（37f741），47687 退出 0。
- core/storage 单位测试和前后 Hook 恢复、普通传输、搜索错误、Code Mode
  错误/取消、计划与 diff 联合 686 passed，33.29 秒（75c048），2066 退出 0。
- 三组均 null keyring、当前 sandbox compiler、4 workers、loadfile、禁重启、
  addopts=''、-q --tb=short；组间串行，无失败静默重跑，集合重叠不相加。
  12 逻辑核/18 GiB，测试含真实子进程，采用 4 而非固定 96 workers。
  运行期间没有修改对应生产代码或测试。
- 修改文件 ruff check/format、src compileall、git diff --check 通过（4feb98）。
  全范围 ruff check 与依赖检查通过（bdacf0）；全范围 format --check 未通过：
  本轮未修改的 test_pending_terminal_process_recovery.py:75 字符串引号需格式化，
  其余 1312 文件格式通过。本轮保留该文件现有改动，不将全范围格式检查称为通过。

## A5 已核对边界与后续

| 边界 | 本轮核对的源码与行为证据 |
|---|---|
| 就绪后锁准入，不占锁等待 MCP | Codex parallel.rs 的 readiness → read/write → dispatch；Corki StepToolReadiness → ExecutionGate。test_mcp_readiness_gate 覆盖完成/启动失败/取消/关闭及冷账本不额外 dispatch 预热 |
| 并行/独占、完成序与历史序分离 | ExecutionGate FIFO，LiveTools 有序发布；test_live_tools/test_task_ownership 覆盖普通错误、fatal、取消与 exclusive 屏障。新增六项证明整批等待差异修复 |
| Code Mode 调用所属 Step | service._call 捕获当前 worker 的 gate/readiness/dispatcher，准入 lease 在等待前获取；test_code_mode_step_admission/worker_scope 验证旧模块新调用用当前 Step，已准入调用保留所属 worker |
| 原输入优先，新输入不抢占采样 | Codex turn.rs can_drain_pending_input；Graph prepare 的 step_count/compacted/model_needs_follow_up。test_pending_input_sampling_boundary、test_realtime_steering 验证首采样及压缩后续采样边界 |
| 工具完成后接入 steer 与退出时输入归属 | test_live_tools 的 steering 用真实历史位置断言 result 在新输入前；test_turn_input_boundary 覆盖关闭/取消/写入重试/终态拒收。细节见 pending-steer-ownership.md |

上述证据取代 A5 笼统的“readiness/Code Mode 继续收敛”，但不把新修复等同
整个 A5/A7/E7 验收完成。下一步需要针对**前缀已发布而尾部未完成时冷恢复**
补独立证明，并刷新修改后的非 CLI 全量回归。74939 的 15490 passed 是本次
生产修改前的全量基线，不涵盖此修复。其它 Hook/构造资源开放项仍在原清单。
原生 Rust 测试未运行，脚本模型不证明真实模型工具选择质量；CLI 对齐暂停。
