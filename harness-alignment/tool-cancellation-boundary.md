# 工具取消被 handler 结果或异常掩盖（A4/E1/E6）

## 当前补验：媒体结果准备边界

重新核对 Codex `parallel.rs::handle_tool_call_with_source` 的 readiness → 执行锁 →
dispatch → 取消时 abort/join，并区分 terminal_outcome_reached。
Corki executor 在 handler 返回、结果规范化之后仍 await MediaPreparation.prepare_result，
因此该阶段也属于尚未完成的执行，不能用 handler 已返回作为成功依据。

已有直接调用测试改名为
`test_execution_cannot_replace_pending_cancellation_with_result`，扩展 stage=handler/media
与 outcome=return/error 四组合。媒体分支注入真实 Runtime 使用的 prepare_result 方法，
明确记录已进入该阶段，再对当前任务发起取消，捕获后返回或改抛 ValueError。
沿用已有 executor finally 保护，无需新增生产实现。

四组合均只执行 handler 一次、仅一次模型请求、不发布 ToolCallCompleted，消费者收到
CancelledError，最终事件为 TurnCancelled。补充直接调用的账本断言：同调用重新 claim
返回 unknown/not repeated 错误，不缓存伪结果。这是同 Runtime 的持久账本检查，
不是进程强杀或冷 Runtime 恢复；媒体故障注入也不是在原生解码器中重现取消。

失败边界与 executor 联合 37 passed（4c4f6a，3.40 秒）；图像、音频、媒体类型、
MCP wire、Code Mode 媒体和媒体预算六文件 137 passed（f838ec，28.13 秒），无跳过。
静态检查通过（70e716，1183 文件、77 包）。下文“媒体准备同故障未执行”是历史
状态，由本节替代；不据此宣称全部 E6/E7 资源与取消路径完成。

范围：普通工具边界与真实Runtime，不涉及官方协议。Codex基准为
ddf04ad26789d040f9ef6a96736f76602e35a6cc。

原生core/src/tools/parallel.rs::handle_tool_call_with_source选择dispatch完成与
cancellation token；在尚未到达终态时abort并await dispatch，发出aborted通知。
已完成结果另行保留，不能简单把所有取消竞态都覆盖成失败。

Corki tools/executor.py::execute只捕获Fatal/TimeoutError/Exception，通常能让
CancelledError传播。但handler在await收到取消后若捕获并返回ToolResult或改抛
ValueError，边界仍返回成功或错误Observation。graph._execute_bound_tool随后
complete_tool_call并发布完成事件，LiveTools也看不到取消。这违反“不吞取消”。

真实Runtime两反例由handler对自己的执行task发起cancel，在await处收到后分别
返回结果/改抛普通异常。两者均未向消费者传播CancelledError：修复前dd7f44，
2 failed、10 deselected。此为任务取消注入，不冒称物理Ctrl+C或进程强杀。

修复计划：执行边界记录进入时的task取消计数，离开时若本次执行新增未撤销取消，
优先恢复CancelledError，覆盖handler结果、普通异常和媒体准备阶段的掩盖。
不拦截正常的asyncio.timeout自行uncancel，不把过去已处理的取消误算为新取消。
不保存伪完成，不自动重放有未知副作用的调用；原始claim恢复仍保持unknown。

已实施executor边界finally检查；两真实反例通过，且不发布ToolCallCompleted、不
再次采样、消费者收到CancelledError并以TurnCancelled终止。工具失败边界、executor
单位、Code Mode错误通道及task ownership联合89通过12.50秒（f360c6，72208退出0）。
静态c3078c通过：ruff、1181文件格式、compileall、77依赖、diff。当前无运行中测试。
新增反例当前只覆盖普通直接工具；Code Mode的既有错误通道是回归证据，不冒称
相同吞取消故障已在嵌套cell中注入。工具ledger恢复、媒体准备吞取消等扩展仍需核验。

## 后续：嵌套cell与工具账本

新增真实Code Mode四组合：handler收到取消后返回/改抛异常 × 工具自行取消/
runtime.cancel_active取消Turn所有者。前者让JS Promise失败，cell可以捕获后继续；
后者必须传播CancelledError并以TurnCancelled结束，不因为JS catch变成成功。
本地service.invoke区分owned dispatch被取消与当前invocation task正在取消，不能
一律将工具自身取消升级为全Turn取消。原生code_mode/delegate.rs的dispatch worker
按cell token管理调用，parallel.rs仍拥有abort/await；没有引入新协议或改动这些参考文件。

断言工具执行一次、不发布该工具的ToolCallCompleted；真实repository重新claim
同一调用只获得unknown/not repeated错误，不得到false success。这是持久账本
API复用检查，不冒称跨进程Runtime冷恢复，也不是物理SIGINT。
首轮1ea2ab的2失败为测试误用event.call_id，改为真实tool_call_id；非生产反例。
失败边界/executor/Code Mode错误、嵌套取消、生命周期、Step准入、claim关闭及恢复
并发联合**162 passed，34.55秒**（e247f6，55381退出0）。静态e57f29全部通过，
无活动测试。此次只增加测试，沿用上一轮生产修复；媒体准备同故障注入仍未执行。
