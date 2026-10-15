# E3 普通模型故障链验收

本轮按当前源码和实际测试收敛 E3，不新增生产分支或重新引入官方服务。
参考固定 Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc；本地参考未改动。

参考调用链：model-provider-info/src/lib.rs 构造 ApiRetryConfig（429 不重试，
5xx/transport 重试，独立 request_max_retries）；core/src/session/turn.rs
run_sampling_request 使用 stream_max_retries，重试时重新 clone_history；
protocol/src/error.rs::is_retryable 区分终止与可恢复类别。官方账户更新与
专用响应路径不属于本项目验收要求。402 按用户要求覆盖参考通用状态重试策略。

Corki 调用链：普通适配器 → models/http_stream.py 请求层 → http_errors.py /
response_errors.py 分类 → graph._call_model_owned 持久化部分项和失败事实 →
_retry_update / _retry_model → 下一采样。成功响应 yield 位于请求重试 catch
之外，已交接响应的读取失败不会在旧 HTTP 请求层直接重放。

| 要求 | 当前行为与证明 |
|---|---|
| HTTP 错误及两层预算 | test_http_retry_layers：两接口覆盖 400/401/402/403/404/429/500/503 及具体错误类别，检查请求数、事件、失败账本和无成功采样；默认请求/采样预算组合、连接故障与请求层自行恢复另有测试 |
| 缺失终态与部分输出 | Responses 必须有终态事件；Chat 必须有完成标志，否则抛可恢复协议错误。test_partial_tool_transport_boundary 在两接口覆盖 EOF/ReadError、混合输出及冷恢复，区分已完成工具项与不完整参数预览 |
| 断流后重建历史 | test_history_retry 验证已有 Observation 进入下一请求、工具只执行一次、未知副作用不重放、逻辑 Step 与采样重试预算分离、失败提交后子进程崩溃恢复 |
| 流内失败分类 | test_response_errors 覆盖已支持普通错误类别、待决错误后的工具项、终态/EOF/取消与重试延迟；不将 HTTP 429 和流内 rate_limit_exceeded 混成同一策略 |
| 关闭与取消 | test_http_error_cleanup 两接口的状态/正文/关闭故障组合；test_http_stream_cleanup 验证主错误优先和取消不被普通关闭异常覆盖；backoff 测试验证取消结束、steer 排队而非重写正在重试的请求 |
| 持久化兼容 | test_model_failure 与 history_retry 保留失败事实；旧 402 retryable=true 只在执行时应用当前不可重试策略，不改写归档，不触发新模型请求 |

本轮运行上述七个文件：148 passed，20.03 秒，退出 0（c86ecb）。这是现有
生产实现的复核，不是本轮新增能力或红绿修复，也不累计进先前全量 14337。

结论：E3 所列普通内置传输的 HTTP/截流/缺终态/部分输出/重试限制已核验。
边界明确：脚本模型与 MockTransport 证明 Harness 行为，不证明在线服务可用性；
Chat 是目标指定的兼容映射，不宣称 Codex 默认使用 Chat；自定义 ModelPort 的
任意协议不属于内置传输保证。402 余额不足不会自动恢复外部余额。摘要有独立
重试所有者，401 与普通流策略不可混用，见 payment-error-review.md；其余工具、
记忆、扩展构造、资源生命周期与恢复交错仍由 E1/E2/E4–E7 及 A/C/D 验收。
