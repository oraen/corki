# 普通模型 HTTP 402 与恢复语义

## 摘要认证失败的独立重试边界复核

异步Pre压缩测试曾在未关闭摘要重试时收到最终成功，而不是第一次认证错误后
立即TurnFailed。本轮回到源码确认：Codex core/src/compact.rs约304–350行
先处理取消、会话预算和上下文超限，其余错误统一按max_retries/backoff重试。
Corki context/window.py::_summarize也不使用普通采样的is_retryable谓词；仅
status_code=402按用户明确要求立即失败。因此认证错误在摘要中重试不是新发现
的对齐缺陷，不能套用主循环策略擅自改掉，也不是官方登录/token刷新。

test_compaction_retry_http.py新增两普通接口×首次失败后成功/持续失败四个401
场景。真实MockTransport返回summary denied，Harness预算1、adapter预算7：
确认摘要只发两次相同请求、仅一条purpose=compaction的重试事件，错误详情保留；
持续失败不安装摘要，暂时失败后正常完成。既有402×自动/手动测试继续验证仅
一次请求、无重试、原归档保留与冷恢复不再采样。上述文件及摘要重试控制/集成
联合23通过（27cca1，2.53秒），退出0；ruff/format/diff通过（468b43）。
本批仅补四项行为验证，不修改生产、配置或凭据；不将401与402混称同一策略。

## 最新补齐：摘要所有者也停止402重试

目标文件明确要求402余额不足不得反复重试。此前普通采样/持久失败恢复已处理，
但window._summarize沿compact.rs独立重试循环，仍忽略适配器的不可重试标记。
本轮双普通HTTP适配器 × 自动/手动压缩复现4失败（c66315）：预算2时均发3次402
请求。现在仅为用户明确要求的status_code=402增加直接抛出边界，不改变其它摘要
重试与上下文超限裁剪策略，不新增官方账户查询、充值或备用地址。

参考compact.rs约304–345行：取消/预算/上下文超限有独立处理，其余错误进入通用
重试；本差异是用户范围要求覆盖参考默认行为，不宣称逐字复刻。失败仍保留真实
Insufficient Balance，TurnFailed不可重试，摘要未安装，原历史保留。真实Runtime
关闭重开后resume_pending为空且不再发请求；所有请求为MockTransport离线响应。
相关HTTP摘要、重试、Hook压缩与手动压缩联合34通过（34f04e，6.84秒），ruff、
1198文件格式、compileall、77包依赖与diff通过（7d6fc4）；固定参考commit干净。
CLI对齐仍暂停，未修改teach.md或用户凭据；不据此关闭全部HTTP/恢复故障范围。

## 已实施与验证

### 重试原因传递（已实施）

原生 `core/src/session/mod.rs::notify_stream_error` 将错误to_string放进
StreamError.additional_details，`tui/src/chatwidget/streaming.rs::on_stream_error`
再将状态标题和详情一起交给set_status。Corki graph发出的ModelRetryScheduled
已有error，包含普通采样及压缩，但应用消费时只显示计数/延迟，丢弃原因。
本批只补适配层错误传递，不对齐原生状态栏：在既有通知附加Reason，控制字符
转空格、最多4000字符，空原因不加空行；不修改原事件、数据库或模型历史。

已接入ModelRetryScheduled消费分支，普通采样和压缩共用；继续保留原来的
重试次数/延迟与部分正文收尾，不把重试描述为用户steering，不改变预算或请求。
四个非空原因反例修复前失败于丢失Reason（5a6003）；扩展有限/无限、采样/压缩、
控制字符/空白/5000字符原因后16通过（d66579）。真实HTTP重试层矩阵发出的
Runtime事件交给真实应用消费者，确认重试原因和最终失败均可见；包括402
终止不重试的对照，未发任何在线请求。

应用/HTTP重试/压缩HTTP联合137 passed / 7.09秒（f4b652，session82304终止0）。
静态初次仅测试预期字符串过长，拆分后ruff、1189文件格式与diff check通过
（591635），compileall和77包依赖检查通过（08843c）。未新增日志或发送凭据；
展示的是已有事件错误字段，不提供任意服务端错误正文的通用敏感信息识别保证。

### 正常输入取消清理误报（实施前补充）

`InputOwner._read` 将终端KeyboardInterrupt转为InputInterrupted；
`CorkiApplication._consume_interactive_events` 已将它与EOFError作为正常取消处理，
调用cancel_active后等待consumer完成。但finally的`_join_realtime_tasks`又把
这两种已经处理的控制异常作为任意Exception记录cleanup failed，造成用户看到的
误报。InputOwner自己的join已排除这两类，应用层却未保持一致。
参考原生`core/src/tasks/mod.rs`的中断/aborted-turn路径及
`protocol/src/error.rs::is_retryable`将TurnAborted/Interrupted视为非重试控制终态，
不把正常中断等同资源关闭失败。本批只修复应用层分类，不改UI外观或取消协议。
验收需真实Runtime仍结束模型流、所有owner结束；OSError等真实清理错误仍记录。

已修复：join显式接收input_task身份，仅对该任务的EOFError/InputInterrupted
跳过误报；其它owner即使返回同类异常仍记录，OSError等也保持类型日志，不输出
可能含私密信息的异常正文。取消、shield及等待所有任务结束的结构不变。

新增真实Runtime键盘中断/EOF反例最初夹具错误地期待正常返回（723ba5），
校正为现有CancelledError契约后，修复前两例明确失败于误报日志（8e98ee）；
没有为了让测试通过而吞掉取消。还覆盖非输入owner的OSError/EOFError/
InputInterrupted，以及原有重复取消、迟延清理、真实清理失败和渲染错误矩阵。
最终输入所有权/应用/Runtime关闭联合126 passed / 6.39秒（5e845b，session
53324终止0）；ruff、1189文件格式、compileall、77包依赖与diff check通过
（833c51）。该改动属于核心取消/资源错误语义所需的适配修复，不恢复CLI体验对齐。

### HTTP 402 修复记录

`http_error` 单独将402设为不可重试；保持protocol类别、HTTP状态和原始消息，
不依赖provider名称、域名或错误正文。`ModelFailure.error` 对旧402事实应用
当前不可重试策略，`Graph._retry_update` 根据该执行错误而非旧存储布尔值判断。
数据库归档不改写，不触发不相容迁移；其它HTTP/流错误策略保持原样。

两普通适配器实际Runtime反例先红：原来预期一次但实际两次请求（883e5e）。
修复后HTTP双层重试、错误响应清理、历史重试和失败存储联合114 passed / 14.05秒
（454649，session 84522终止0）。402只有一次请求、无重试事件、无成功step，
最终TurnFailed保留Insufficient Balance；已有5xx等对照保持通过。

额外扩展真实子进程失败提交后崩溃测试：在隔离测试数据库中模拟旧402
retryable=true记录，冷Runtime恢复时零模型请求、零重试事件，保留之前的工具
执行文件且不再次执行，最终失败保留原始原因、旧事实仍为true。与原连接故障
两种重试预算对照联合4 passed / 3.50秒（092594，session 63329终止0）。
静态ruff、1189文件格式、compileall、77包依赖、diff check通过（1c284d）。

未调用用户在线服务，未充值或改密钥；外部余额问题仍由账户方解决。
普通可重试故障的原因展示、InputInterrupted清理误报已由上方后续修复处理。
这些已确认问题已处理，但不能称为所有错误处理已完成；CLI视觉/主题对齐继续暂停。

## 实施前差异

用户实际会话数据库最近四次采样均记录 `model request failed (402): Insufficient Balance`，
kind=protocol、retryable=true；配置为普通 Chat Completions。未发起新的在线请求。

参考 `codex-rs/protocol/src/error.rs::CodexErr::is_retryable` 将 UnexpectedStatus
列为可重试；已知配额/用量错误另有不可重试分支。Corki 的
`models/http_stream.py::model_http_stream` 请求层仅自动重试5xx与TransportError，
但 `http_errors.http_error` 把其他未单独分类状态（包括402）映射为 retryable=true；
`core/graph.py::_retry_update` 在采样层重新调用模型。这解释了观察到的反复重连。

此项按用户最新目标明确调整，不把原生UnexpectedStatus一概重试当最终行为：
普通402无论provider、正文/code为何均不自动重试，保留状态和服务端原因；不调用
官方账户服务、不猜测账户余额、不改变凭据。其他HTTP状态本批不扩大重分类。

持久化兼容：已有失败事实不可改写（storage保存具有冲突检测）。旧402记录的
retryable=true保留作为历史事实，但构造执行错误时按当前策略不可重试；图的重试
决策使用该执行错误。验证新请求仅一次、失败终态和原始原因，旧记录读取仍不变。
CLI最终TurnFailed已有错误展示，本批402终止后应走该路径，不推进视觉对齐。
