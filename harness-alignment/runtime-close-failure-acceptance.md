# E7 Runtime 关闭故障传播与后续资源清理

## 当前资源证据归并（覆盖下方历史计数与待验描述）

| 资源/状态边界 | 实际实现与验证入口 | 当前证据及限制 |
|---|---|---|
| 活动 Turn/模型/工具 | Runtime cancel→join；test_runtime_shutdown 的 active model/tool、初始写入、选定终态提交、暂停消费者 | 关闭不依赖事件消费；选定完成提交不会误改成取消 |
| 关闭编排/观察者 | Runtime 共享 shield close；14 入口故障矩阵 | RuntimeError/CancelledError 后仍关闭后续资源，重复观察不重复副作用；后置故障不证明任意内部永久挂死可回收 |
| reset/条件资源 | memory reset/service/history notes 实例及真实 unlink barrier | 92233 五文件 100 通过，含关闭观察者取消、资源依赖和 writer lease；没有修改生产逻辑 |
| MCP | manager shutdown task、transport owner 引用计数 | 本文后续节 58 联合：最后 view 关闭物理连接，外部取消不丢连接 owner；多 client 内部失败继续清理 |
| Code Mode/进程 | cell 回调与通知任务、真实子进程、Runtime terminate_all | 同 58 联合及 runtime_shutdown 真实 exec；与普通 Turn 中断保留终端的策略区别已测试 |
| 模型 HTTP | model_http_stream 的主错误/close 优先级 | model-stream-close-review.md：真实普通 HTTP 断流/取消/关闭失败，不增加危险重放 |
| 记忆工作/子 Runtime | pipeline owned jobs、ConsolidationShutdowns | memory-close-recheck.md：直接调用/后台任务、claim、等待者取消、关闭超时 owner 保留；不把超时宣称成功发布 |
| checkpoint | close_checkpoint context 清理及连接 fallback | test_checkpoint_cleanup：真实 SQLite 连接关闭前后故障/取消，primary 保留，fallback 失败可见，关闭一次 |
| terminal/write lease | Runtime 待写终态、storage-only retry | test_terminal_pending_recovery 的 shutdown retry：不重关 execution、不重放工作；test_pending_terminal_process_recovery 覆盖真实进程退出后持久恢复 |

上述源码/测试入口已重新核查。81536 全量实际退出 0（15532 passed/1 文件系统
限制跳过）晚于 session-start-gap 修复；不再说旧 15397 基线之后没有扩大验证。
新增条件资源/reset 测试只计入各自专项，不反写旧全量用例数。

仍独立开放：E4 的运行中 event loop 无 construction owner 同步 create 失败接管
需要兼容选择；A7/C8 的未关闭 Hook 恢复要求不能被关闭测试核销。下一步应推进
这些具体项及 C1/C2 条件来源，而非把上述已覆盖资源反复写成“全部未知”。
本归并没有将普通 close 的资源成功等同于任意外部副作用全局原子性。

## 条件资源覆盖核对（81536 全量运行期间）

### 在途 reset：纠正旧缺口描述并补齐观察者取消

重新检查完整文件后发现 `test_memory_reset_runtime.py` 原本已有
`test_cancelled_reset_and_runtime_close_join_actual_file_removal`：在实际 Path.unlink
线程中设 barrier，反复取消 reset 调用者并同时 close Runtime，确认两者都等到
文件删除完成。下文“在途 reset 待单独补测”过宽，不能抹去原有有效证据。

本轮扩展该测试为关闭观察者取消/不取消两种情况，并先完成真实 Runtime 准备：
删除 barrier 未释放时，模型/repository/writer 均不得关闭，writer lease 仍持有；
取消 close 观察者不取消共享 close task，新观察者继续等待同一任务。释放后真实
删除完成，reset 调用者仍收到 CancelledError，下游资源按顺序仅关闭一次，
writer 释放，后续 reset 拒绝。没有替换实际删除/关闭行为，也没有修改生产逻辑。

五文件联合（memory_reset_runtime、runtime_shutdown、memory_shutdown_deadline、
unit/memory/test_reset、local_history_notes），4 workers/loadfile/禁重启，
PYTHON_KEYRING_BACKEND=null、当前 sandbox compiler 显式配置：
**100 passed / 10.77s**，92233 实际退出 0（0bfb81）；ruff check/format 通过。
独立 tmp_path，先确认无其它 pytest/execnet（207f14）。无需仅因新增验证而
再次立即全量重跑；81536 的生产基线未变，新增测试结果独立计数。

81536 实际退出 0 后已扩展原矩阵：真实 Runtime 同时启用 memories 和本地
history notes，固定独立 home/memory/db 路径；在原 11 项基础上增加 reset、
memory service、history notes 三个入口，共 28 个错误/取消参数。原方法实际
执行后才注入错误，检查所有后续资源按顺序一次关闭、重复 close 保持失败、
checkpoint/writer 释放，并直接检查三项条件资源的关闭状态。
完整 test_runtime_shutdown.py **48 passed / 2.65s**，19118 实际退出 0
（8c59c0），4 workers/load，ruff check/format 通过。未改生产逻辑。
在途 reset 的 Runtime 关闭等待仍待单独补测；不将后置错误注入当作其证据。

当前 `_close_resources` 已接入 memory_resetter、code_mode、memory_service、
history_notes 四个条件资源；原 11 项参数矩阵不包含它们，不能由那个矩阵
直接推导所有配置组合均验收。Code Mode 已有下文内部故障证据。

本轮逐项只读确认：MemoryResetter.close 关闭准入并 shield 等待已持有的 reset，
结果由 reset 调用者观察；memory/pipeline.py 的共享 close task 先取消并 join
工作，再 settle 子 Runtime、关闭 owned 模型及 repository，捕获首个异常后
仍继续后续清理。LocalHistoryNotesBackend 不持有长连接，close 标记关闭；
其 `_joined` 在取消时等待线程操作终止，Runtime 先 join 活跃 Turn 再关 notes。
不能把 notes 服务关闭直接等同于取消未完成数据库写入。

已有 memory_shutdown_deadline 父 Runtime 测试检查 observer 取消和模型仅关闭
一次；test_reset 的队列串行/取消测试则在 reset 完成之后才 close，尚不能
证明 Runtime 关闭与在途 reset 的同一组合。下一步补真实启用条件资源的
Runtime 收尾矩阵（错误/取消仍关闭后续资源、重复 close 不重复副作用），
以及在途 reset 的关闭等待边界；不增加无界“所有永久挂死”验收要求。
这是明确的覆盖缺口，尚未证实生产泄漏；81536 运行中不修改测试或生产。

参考提交：ddf04ad26789d040f9ef6a96736f76602e35a6cc，参考工作树干净。
CLI 对齐继续暂停；本批仅补行为测试，没有修改生产实现。

## 源码对照

Codex `core/src/session/handlers.rs::shutdown_session_runtime` 先停止预热、
conversation 和活动任务，然后关闭 Hook、进程、Code Mode、MCP 等执行资源。
Code Mode shutdown 失败记录警告而不跳过后续资源。`shutdown` 随后执行
thread-stop 生命周期与持久化 shutdown；持久化失败报告 Error，仍发送
ShutdownComplete。`session/mod.rs::shutdown_and_wait` 等待共享的 session-loop
termination，支持多个观察者，而不是依赖某个事件消费者持续读取。

Corki `core/runtime.py::aclose` 关闭准入并建立共享、shield 保护的 close task。
`_close_resources` 等待活动任务后逐项清理，保存首个 BaseException，继续关闭
后续依赖；`_close_storage_resources` 处理输入落盘、repository、checkpoint、
writer，最后才传播错误。观察者取消不会取消共享 teardown。
两者都要求失败可见、后续清理继续；不声称 Python 的 close 抛错等同于 Rust
的事件 API，也不要求所有服务具有完全相同的关闭顺序。

## 本批验收

扩展真实 Runtime 的
`tests/integration/test_runtime_shutdown.py::test_close_failure_still_closes_other_resources_and_is_shared`：

- 入口：Stop/Post/Pre Hook owner、MCP prewarm、Thread memory、process manager、
  MCP manager、plugin manager、model、repository、Thread writer，共 11 个。
- 每个入口真实清理后注入 RuntimeError 或 CancelledError，共 22 例。
- 验证后续资源按实际依赖顺序全部调用一次；第二次 close 保留错误类型且不重新
  清理；普通错误保留原原因；checkpoint 引用清空、writer lease 已释放、准入关闭。
- CancelledError 不转成普通成功，也不会提前终止其余资源的清理。

专项 42 passed（867353）；保留普通错误原因断言后，与记忆 shutdown deadline、
task ownership、Thread writer、LiveTools close、Code Mode error channels、
MCP session recovery 七文件联合 **187 passed / 39.84s**（be752b，退出 0）。
Ruff、format、git diff --check 通过（0c34b2）。

## 未由本批证明

注入点在真实 close 返回之后，证明的是 Runtime 编排和错误传播，不能据此宣称
任意资源内部关闭前失败、永久挂起或 OS 强杀后均能清理成功。后台实际工作、
MCP 传输、Code Mode cell 和记忆子 Runtime 的内部所有权仍按各专项证据验收。
本批不关闭整个 E7，不解决 E4 的运行中 loop 无 owner 同步构造兼容选择；
也不把本次联合回归当成最近生产改动后的全量回归。

## 后续：MCP 内部所有权及 Code Mode 回调清理

原生 `codex-mcp/src/connection_manager.rs::shutdown` 在独立 spawn 中持有所有
连接并逐项 shutdown，明确保证发起清理的 refresh 被中断后清理仍存活。
`code-mode-runtime/src/service.rs::shutdown` 委托 runtime；对应
`service_contract_tests::shutdown_cancels_notifications_while_natural_completion_is_draining`
验证通知取消、等待释放，最后确认 cell closed，而非发出取消后即返回。

Corki MCPManager 的独立 shutdown task 持有连接；MCPConnection 的旧/新 policy
view 经 MCPTransportOwner 引用计数共享物理 client。最后一个 view 释放时才关闭
物理连接。真实 manager/owner 链测试现在包含两个 client：旧 docs view 有调用，
策略刷新后关闭并取消外部观察者，底层 docs close 在 barrier 后正常返回、抛
RuntimeError 或 CancelledError。三种情况均确认旧调用取消、notes 同样关闭、
每个物理 client 仅关闭一次；重复 manager.close 保留原错误类型，不再次执行。
这比 Runtime 的关闭方法包装测试深入一层，但底层 client 仍为离线替身。

联合已有 Code Mode 真实进程 wait/pipe 清理失败、取消和多 cell 回收，MCP 真实
进程 spawn handoff，以及 Step resource binding 测试：五文件 **58 passed /
27.26s**（be9ce3，退出 0），Ruff/format 通过（65b21b）。Code Mode 测试检查
回调/通知任务全部终止、进程退出及单一 Turn 终态；本轮没有修改生产代码。

该组已覆盖的 MCP 共享物理连接收尾与 Code Mode 清理故障不能再笼统列为完全
未知。它不证明每个平台实测、不可恢复挂死的清理或 OS 强杀后的内存回调执行，
也不代替 E4 构造失败问题、完整 A–E 验收和最终扩大回归。
