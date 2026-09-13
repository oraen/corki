# 工具就绪等待与执行锁（A5/B10/E7）

状态：已接入修复并通过下述分层验证；更大范围结果见后续记录，不代表全部 A–F 完成。
范围为普通本地/MCP 工具调度，不涉及官方账户、原生 namespace 或原生 tool search。
基准为 Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc。

## 当前实现与验证（优先于后面的实施前记录）

新增 tools/execution_gate.py：FIFO 并行读者/独占写者准入，等待中的写者不被后来
读者绕过，取消排队或已授予但尚未进入的 slot 都释放计数。LiveTools 先等待
readiness，再进入执行锁；发生致命/取消控制流时在重新准入前登记 failure，
拥有者仍取消并 join 兄弟任务，不能因为释放锁而执行后续排队副作用。
graph 非流式路径也使用同一 LiveTools，全部结果完成后按模型调用顺序追加历史；
逐调用 claim/complete 仍在 _execute_one，不随就绪预热提前移动。

StepToolReadiness 只对已曝光且与 Step 快照定义相等、实现可选 readiness 协议的
handler 等待。首次需要时读既有 Turn outcomes，只保存 call ID 集合（不修改账本），
对已完成/unknown 的调用跳过预热，真正执行仍以 claim 的身份/参数校验为准。
同一 readiness owner 只查询一次；不是跨 Step 的全局状态，也不持久化 handler。
MCPTool 的回调进入 manager 的 selected generation，仅等待启动，不请求工具授权、
不发 tools/call。启动失败仍由实际 dispatch 的 typed admission 生成错误 Observation；
取消不吞掉。等待后实际调用重新取得当前授权，预热本身不赋予缓存目录执行权限。

CodeModeService 已接入相同 ExecutionGate 与 StepToolReadiness，先按既有机制
取得 Step 资源 lease，再等待/排锁；已准入调用捕获旧 gate/dispatcher/readiness，
不会随 pause/下一 Step 重新绑定。相同 worker 重试保留 gate，新 Step 创建新 gate。
外层 exec 与嵌套调用保持独立 gate，避免非重入独占锁自死锁。

最初四反例与 task ownership 联合23通过（811354，3.53秒）。扩展为 direct/
Code Mode × batch/stream × read-only/exclusive × 完成/启动失败/取消/关闭32组合；
配合 Code Mode gate/生命周期48通过（a9e161，7.08秒）。另有 fatal exclusive
不执行后续 effect 两项通过（7363df）。冷恢复等最终本文件36通过（8fe050，4.09秒）。
gate取消及 live/code-mode admission/error 联合95通过（c8357d，21.07秒）；
调度、存储恢复、lazy/pending MCP、Code Mode lifecycle/cleanup等扩大232通过
（e066fc，47.30秒）。集合有重叠，不相加成唯一测试总量。

冷恢复首轮2失败、34通过（926e9a）：测试误把“此次 dispatch 不额外预热”写成
“整个冷启动不创建连接”。实际 Runtime._execute_run 在运行恢复节点前就
prepare_server(saved servers)，属于已有快照重建。修正夹具允许目录重建，但给
新 wait_until_ready 注入必失败哨兵；completed/unknown均不触发该哨兵，不重放
RPC、不重复输入/采样/调用记录。没有修改冷重建行为，也没有宣称所有恢复都离线。
初始审计中的“不因预热启动连接”仅指新的按调用 readiness，不包括目录重建。

静态首次发现 suppress 写法、随后测试导入排序，均已修正；最终结果另列。
原生 Rust 测试未运行；普通模型预设调用只证明 Harness 链路，不证明模型选择质量。
随后 MCP 全单位、gate公平性/取消、真实目录重载/资源Step/审批锁/记忆准入及
claim关闭联合2143通过（7496e3，33.38秒）。静态最终ruff、1181文件格式、
compileall、77依赖、diff检查通过（1c4449）。这组不包含整个项目所有集成/PTY。
最后普通搜索/定义加载、模型降窗压缩、摘要wire、记忆管道、错误恢复与并发恢复
六文件组合215通过（70c4d0，36.28秒）。基准仓库再次确认未修改；全部测试句柄
已终态。A–F整体仍未完成，不把这些重叠集合累加为全仓验收或真实模型质量证据。

## 源码链与差异

后续基线暴露16项旧generation测试与新readiness边界冲突：原生parallel.rs先
wait_until_ready再dispatch，McpHandler.handle_call才prepare_mcp_call获取当前binding。
因此等待中显式刷新后，模型direct/nested应在实际准备时使用新配置；预热失败不应
覆盖新配置的成功。host直接call已选binding则仍使用旧配置并保留其失败，不自动重放。
已修正测试同时验证两种边界，保留ordinary/codex_apps仅作为任意服务器名字的组合，
不按名字激活产品功能。generation/readiness两文件84通过（72dece），生产未改。
完整基线被中断、失败及其他未验部分见core-baseline-refresh.md，不宣称全量通过。

Codex core/src/tools/parallel.rs::ToolCallRuntime::handle_tool_call_with_source：
从 Step router 取得精确 handler 与 supports_parallel；派生 dispatch task 内先 await
handler.wait_until_ready，然后才获取共享 RwLock 的 read/write guard，最后 dispatch。
取消分支 abort 并等待该 task。普通错误与 Fatal 仍分流。handlers/mcp.rs 的
McpHandler::wait_until_ready 调 session/mcp_runtime.rs::wait_for_mcp_server，
因此尚未完成 MCP 启动的工具不预占执行锁；最终结果仍由原生有序队列收集。

Corki core/live_tools.py::submit 在提交时就把 exclusive 任务设为后续 barrier，
run 等待前序任务后才调用 _execute_one。graph._execute_tools_owned 的非流式路径
按连续 parallel 批次 gather，遇到 exclusive 就等待其整个执行。MCPTool.execute
经 manager.call_tool → prepare_call → _wait_selected_client 才等待实际连接。
所以 pending MCP（无论 parallel/exclusive）都会挡住后续已就绪的 exclusive 工具。
code_mode/service.py::_call 同样在就绪之前构建 step_calls/barrier，尚无独立实测。

## 可重复证据

新增 tests/integration/test_mcp_readiness_gate.py 使用现有 lazy startup 的真实 Runtime
夹具：子任务缓存声明已经进入普通模型请求，但 MCP client.start 等待显式 release；
模型先调用 MCP lookup，再调用已经可用的本地 exclusive 工具。两工具均为普通函数。
要求 local 在 release 前执行，随后 MCP 才启动完成，模型结果仍按 remote/local 顺序。
批量/流式 × MCP readOnlyHint false/true 四组合均失败于 local_started 等待超时，
不是模型未到达或 MCP 没启动（3e115e，4 failed，4.95秒）。首次收集失败是夹具
模块导入路径不正确，已修正；不将导入失败当调度反例。

测试 finally 取消并等待消费者、关闭 Runtime，不留下在途连接。实施前四条回归
保留目标断言、未标 skip/xfail，当时相应测试不绿；现已按上方实现修复并重跑。
旧并行屏障测试只覆盖 handler 已可执行，不证明 readiness 前后锁顺序等价。

## 实现约束与验收

- 三条入口统一为就绪后加入公平并行/独占锁；普通已就绪工具仍保持先到的
  exclusive 不被后来读者饿死，结果顺序与实际开始顺序独立。
- 不直接把所有 MCP prepare_call 提到存储 claim 之前。已完成/unknown ledger
  恢复必须不因预热而触发新连接或重放；保存 Step handler/曝光校验与当前执行授权。
- 就绪等待不能执行 RPC，不能授予审批，也不能借“缓存已发现”绕过 live catalog。
  配置重载时选定 generation 的所有权和失败语义须沿用现有 MCPAdmission 边界。
- Code Mode 外层 cell 与嵌套工具不可竞争同一非重入独占锁造成自死锁；保持
  已准入 call 的 Step 快照和资源 lease，不绑定到后续 Step。
- 补 cancel/close 等待就绪、就绪失败降级、本地并行/独占屏障、结果顺序与冷恢复
  不重复执行的真实 Runtime 回归；新四反例通过只是必要条件，不是全部验收。

以下为实施前运行：当时生产代码未改，未执行原生 Rust 测试。
既有 lazy startup 与 task ownership 联合39通过（4d09d3，3.87秒），证明旧覆盖
确实没有这个组合；不把它与新4失败混写成全绿。ruff/1178文件格式/compileall/
77依赖/diff检查通过（23908c）。全部测试进程已终态，无待观察后台任务。
