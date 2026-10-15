# E1 错误分类：普通调用与 Code Mode 的边界

基准：Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc。只核验本目标的普通
模型/工具调用，不引入官方认证恢复或专用协议。

## 源码与可观察语义

Codex tools/src/function_call_error.rs 定义 RespondToModel/Fatal；core/src/tools/
registry.rs 将未知工具归前者、已注册工具的 payload 类型不匹配归后者。
parallel.rs::handle_tool_call 将 Fatal 提升为 CodexErr::Fatal，其余错误生成
模型可见失败结果。Code Mode 不使用同一个外层分类出口：code_mode/delegate.rs
的内嵌 invocation 将错误转换为字符串交回脚本，不应据 Fatal 名称直接结束父 Turn。

| 类别 | Corki 实际出口 | 直接行为证据 |
|---|---|---|
| 可纠正的工具错误 | tools/executor.py 将参数/handler/超时/非法结果变为错误 ToolResult，graph 提交后回灌 | test_tool_failure_boundary：未知名/非法 JSON 零执行，handler 只执行一次，错误进入下一模型 Step |
| 普通工具致命错误 | FatalToolError 穿过 executor，runtime 的 Turn 异常分类产生 error_kind=tool、不可重试的 TurnFailed | 同文件：并行 sibling 已收尾，只有一个终态、一次模型采样，不回灌再执行 |
| 可重试模型错误 | ModelFailure 保留独立失败事实，重试节点重建历史并消费预算 | test_history_retry：旧 Observation/已发现定义保留，未知副作用不重放，失败提交后的冷恢复与旧 402 兼容 |
| 取消控制流 | executor 比较 task.cancelling；Runtime 单独处理 CancelledError/NodeCancelledError | test_tool_failure_boundary：handler 或 media 返回值/异常掩盖取消仍无成功工具完成事件，未知账本不重放；MCP 超时与用户取消区分 |
| Code Mode 内嵌调度错误 | graph 捕获嵌套 Fatal 并提交错误事实，脚本接收拒绝；工具自身 is_error 值保持值通道 | test_code_mode_error_channels：fatal/exception/timeout/schema/非法结果与 error_value，catch/uncaught、streamed/batch、身份重放 |

本轮三个完整文件联合 126 passed、16.15 秒（5722d6），随后同句柄 4494
确认退出 0（3f07cc）。4 worker/loadfile/禁止自动重启；没有改生产或测试。
这不是新增能力，而是消除 E1 旧“全分类待收敛”的笼统描述，明确可验证的分流契约。

## 保留边界

HTTP 状态与两层预算完整清单见 model-failure-acceptance.md（E3），压缩独立
策略归 C4；构造失败资源接管和关闭内部故障归 E4/E7。E1 分类已核验不表示
这些所有权缺口已修复，也不将半截输出所有路径（E6）或任意第三方副作用判为完成。
尤其 Code Mode 的 dispatch_error 不是普通模型请求中的原生专用错误协议。
