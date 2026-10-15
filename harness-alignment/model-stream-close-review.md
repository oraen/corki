# 模型响应失败与关闭失败的优先级（A/E）

## 最新修复：非成功响应的正文/关闭错误不再覆盖状态或取消

非 2xx 的 aread 普通异常现在按诊断体不可用处理，保留已知 status；finally
关闭普通异常记 warning，不进入外层 TransportError 的连接重试分支。两处都不捕获
CancelledError。HTTPX 在 aread 内自动关闭产生的异常也覆盖，避免只修 finally。
成功读取的诊断体仍用于细分错误码；若 aread 因关闭失败而未完成，其正文按不可用
处理，不猜测未提交的部分 JSON，原始 HTTP 状态仍保留。

test_http_error_cleanup.py：真实 Runtime 自建两普通模型适配器 × 400/429/503 ×
正文完整/截断/取消 × 关闭普通异常/TransportError/取消，共 54 例。修复前
30 failed/24 passed（4e22b1）：包含 429 额外请求、503 状态丢失以及取消转失败。
修复后检查 400/429 单请求、503 原有 5×2 请求预算、关闭每个底层响应一次、
逻辑取消不保存 ModelFailure、普通失败保留 status/kind、不记录 ModelCompleted、
无 running turn 残留。此处是故障注入取消，不宣称已覆盖该组合的物理 PTY Ctrl+C。

联合既有 HTTP 边界/关闭、采样重试、部分工具结果、响应错误、Runtime 关闭和本地
TLS 取消等共 206 passed、0 skipped，23.62 秒（fd0c95）。补充诊断日志后新矩阵
再次 54 passed、0 skipped，5.57 秒（a06d5e）。所有测试进程已退出。
静态 f11f38：ruff check/format、compileall、
uv pip check、git diff --check 全通过（1135 文件、77 包）。完整 A–F 仍未完成。

### 修复前证据

http-client/src/transport.rs::ReqwestTransport::stream 在取得非成功状态后，
resp.text().await.ok() 使诊断体可缺失，但仍返回原 status/url/headers 的 Http 错误。
这是普通传输边界，不属于官方登录/配额查询。
Corki 非 2xx 的 aread/finally 与上次修复的成功响应 yield 分支不同：关闭异常可
覆盖已知状态，TransportError 还可能进入连接重试分支。HTTPX aread 在完整消费后
也会自动 aclose，故关闭异常既可能从 aread 抛出，也可能从 finally 抛出。
当时计划对非成功诊断体的普通异常保留原状态，诊断不可读时为空；关闭普通异常记日志，
取消仍传播。必须覆盖 429 不增加重试、503 仍按原预算、完整/截断正文与关闭取消，
以及 Runtime 失败日志和终态；不能只修 finally 而漏掉 aread 的自动关闭。

## 已修复：成功 HTTP 响应交接后的双重失败

model_http_stream 在 yield 消费者异常时保留 BaseException 主因，随后只将普通
关闭异常记录为 warning；没有主因时仍抛关闭错误，关闭本身的 CancelledError
继续传播。没有增加 HTTP 请求重试，采样重试仍由 graph 使用失败日志和最新历史决定。

test_history_retry.py 的实际 Responses stream 在发出普通 function call、确认
工具已执行之后 ReadError，再由 AsyncByteStream.aclose 抛 RuntimeError。新增
close_fails 两分支修复前 2 failed/2 passed（326cb9）：可恢复分支错误地 TurnFailed
internal，预算耗尽分支仅请求一次而非三次。修复后检查请求数、重试次数、每个响应
只关闭一次、工具只执行一次，以及每个重试请求已含 observed Observation。
进一步检查第一采样只保存原始 disconnect 失败、没有成功 ModelCompleted，canonical
用户输入和工具结果各一条。该证据不证明真实模型选择质量。

新增 6 例 model_http_stream 关闭优先级测试：消费者成功/read_error/cancel ×
关闭普通异常/关闭取消；检查异常实例、单请求、单关闭，避免把失败或取消静默成功。
相关重试/响应错误/Runtime 关闭/真实本地 TLS 握手取消联合首轮 148 passed
（bd7030）；后补持久化断言后整组再次 148 passed、0 skipped，18.02 秒
（750c88）。静态 2c4b36：ruff check/format、compileall、uv pip check、
git diff --check 全通过（1134 文件、77 包）。所有测试进程已退出，无活动测试。

该历史批次仅针对成功状态响应交接给 SSE 消费者后的关闭；非 2xx 后续修复与证据
见页首。所有重复取消或全部资源所有权仍不能由这些局部矩阵推定。

## 修复前源码对照与验收方案

参考 core/src/session/turn.rs::run_sampling_request → try_run_sampling_request：
读取错误成为 outcome，缺 response.completed 是 Stream 错误；离开接收循环后
drain_in_flight，已执行工具输出进入历史，再由上层按原错误判断是否重试并重建请求。
取消检查在 drain 后返回 TurnAborted。官方账户、原生输出项和专属模型能力不纳入移植。

Corki graph._call_model_owned → model_events → 普通适配器 → model_http_stream。
graph 对 ModelError 先等 live tools，再保存失败事实和决定重试；下一请求读持久历史。
model_events 已保护其迭代器关闭的原错误，但更内层 model_http_stream 的 yield/finally
直接 await response.aclose：当读取 ReadError 与关闭 RuntimeError 同时发生，后者
覆盖前者，上层无法再恢复原 ModelError/重试语义。状态：行为不一致，A/E3/E6，
中优先级；可能终止本可继续的 Turn，不据此声称必然发生工具重复副作用。

修复方案：成功 HTTP 响应交给 SSE 消费者后，保留消费者主异常；普通关闭异常在
已有主异常时作为诊断记录，无主异常时仍抛出；取消控制流不能被普通关闭异常替换。
仅修该所有权边界，不改变 HTTP 请求重试次数或原始状态码分类。
验收：实际 Responses HTTP 断流前已完成一个工具，注入响应关闭错误；原采样
重试预算、已更新历史和工具仅执行一次必须保持。补核成功/取消关闭路径与相关回归。
