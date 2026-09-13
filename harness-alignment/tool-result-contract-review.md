# 工具结果状态与宿主元数据边界（B9/E2）

## 当前 B8/B9 验收归并

固定参考 ddf04ad26789d040f9ef6a96736f76602e35a6cc。以下结论覆盖本文件旧节中
“其它跨来源矩阵仍待收敛”的笼统占位，但不关闭 B10 或 A/C/E 的独立恢复要求。

| 要求 | 源码及验收证据 |
|---|---|
| 平铺身份无歧义 | protocol/tool_names.py 的确定性 alias、models/namespaces.py 的反向映射与碰撞拒绝；两普通适配器零请求拒绝碰撞、同 leaf 独立执行、跨接口冷历史、真实 plugin/MCP/dynamic 注册优先级 |
| 参数契约 | 原生 handlers/mod.rs typed serde 失败为 RespondToModel，MCP/dynamic 有独立解析；Corki 普通工具声明子集校验与自定义 parser 分离。required、基本/联合类型、嵌套、数组长度、enum JSON 身份及畸形声明均有 Runtime 零副作用与错误回灌证据 |
| 调用身份与缓存 | 原生 router.rs 保留 arguments 原文与 call_id；Corki 原文/decoded fingerprint、freeform 身份及 SQLite claim 冷恢复拒绝不同调用复用，包括舍入相等小数。handler 不得改写原始调用或结果身份 |
| 结果规范化 | 普通工具/Code Mode 非法结果形成原调用有界 dispatch_error；MCP 内容/结构化输出保持独立通道，远端 outputSchema 不当成本地结果授权。结果 JSON 数值往返、非法 Unicode/元数据、错身份均有验证 |
| 未知/失效/畸形 | graph 曝光和快照准入先于 handler；未知/未曝光回 Observation，定义变更拒绝，payload 种类错误为 Fatal。不同来源注册/刷新及下一 Step 处理已验证 |
| 超时/断连/超限 | HTTP/stdio MCP 真实调用链及冷历史不重发；32MB 原始上限拒绝，合法长内容原始账本保留与普通接口模型侧截断分离；取消不冒充成功。详见 E2 的 tool-failure-acceptance.md |

原生未知工具与 payload 不匹配分别由 core/src/tools/registry.rs 返回
RespondToModel/Fatal；枚举当前位于 tools/src/function_call_error.rs。
专用 namespace/tool search 分支只作参考，没有恢复到 Corki 请求中。
本地 schema 子集不是任意 JSON Schema 引擎，也不声称与每个 Rust 类型逐字节等价。
ScriptedModel/离线传输验证的是 Harness，不证明真实模型选择或纠错质量。

扩大回归首先发现旧 test_helper_response 的合法 permission-context 夹具缺少
已要求的 environment v1：2 failed / 1285 passed（39a9cd）。只更新该合法夹具，
保留混合 ok/error、重复字段拒绝及不缓存断言；没有放宽生产校验或改变 native
协议。重跑相同范围 **1287 passed / 74.16s，无跳过**（c12bb2，退出 0）。
范围为整个 unit/tools、namespace 身份、numeric/freeform storage 身份，以及
工具命名/碰撞/外部注册/冷身份、schema/失败、Code Mode error channels、MCP
schema/参数数值/结果数值、function output policy、MCP session recovery/response
routing 集成文件。修改文件 Ruff/format 通过（6757ef）。

B8/B9 当前普通工具路径已核验。完整 Hook 生命周期、准备/发布/取消交错属于
B10/A7/C8；副作用提交与内部重试属于 E5；全资源关闭属于 E7。上述故障后的
错误结果冷回放不等于任意外部操作 exactly-once 或 OS 强杀验证。

## B8/E2：本地参数校验与MCP参数通道分离

重新核对固定参考：tools/handlers/mod.rs::parse_arguments将typed serde解析失败
转为RespondToModel；dynamic.rs以Value解析后交宿主动态工具，不运行通用schema
校验；mcp.rs将Function参数交handle_mcp_tool_call。不能据此增加所有来源统一的
JSON Schema执行器，或将MCP描述schema误当本地调用授权。
Corki ToolExecutor对普通本地工具使用声明子集校验，错误生成dispatch_error；
MCPTool.parse_call_arguments将原始解析留给MCP execute，继续使用错误值通道。
本地JSON-Schema子集与Rust typed serde不是同一实现，不宣称任意关键字等价。

test_code_mode_error_channels新增required缺失、嵌套整数数组收到布尔值两个
参数错误，各覆盖catch/uncaught、batch/stream、相同call-id重放，共16场景。
验证handler执行次数为零、JS promise拒绝而非成功值、catch后可继续、未捕获时
脚本失败、错误Observation回到模型、完成事件及唯一completed错误账本保留
dispatch_error，重放不会执行handler。原有工具内部error_value仍是fulfilled。
与普通Runtime schema契约、MCP schema/数值参数联合139通过、0跳过
（da2d60，25.46秒）。本批无生产改动，不以本地校验通过代替MCP服务器校验，
也不将同Runtime重复身份当作进程强杀/冷checkpoint恢复证据。

## stdio 进程断开与半截输出补验

参考 rmcp-client/src/local_stdio_transport.rs：Legacy 分支使用 AsyncRwTransport，
close 先关闭协议传输，再等待子进程最多三秒，超时 kill；不把新协议分帧规则
混入当前普通初始化路径。Corki StdioMCPClient.request → _exchange →
_read_stdio：自然 EOF 先结束 inbound 工作，再 _finish_stdio_eof 关闭写端并回收
进程，最后将未完成 future 置为 MCPProtocolError；MCPTool 的错误值通道回灌模型。
无对应结果的半截 JSON 不得成为成功结果，取消与 EOF 错误也不能混为一个终态。

inbound_mcp_server.py 新增 disconnect/partial_exit，仅在实际收到 tools/call 后
以状态 23 退出，后一种先输出并 flush 半截 JSON。response_routing 的三入口
新增 6 例：确认模型收到 closed its output 错误、唯一错误完成事件/调用/账本、
非 TurnCancelled、进程确实退出 23；同 Thread 冷开不重发，历史前缀与结果账本
保持不变。全部 stdio 场景收尾增加进程已回收、reader/stderr task 已完成及 pending
为空的断言；15 秒 watchdog 限制整场景。未修改生产代码。

response_routing、unit/inbound_drain、integration/process_cleanup 共 61 通过，
34.78 秒，退出 0（b98839），无跳过；ruff check/format 与 diff 检查通过。
同时回归已有半关闭后 reverse RPC 排空、EOF 宽限与重复取消、进程组后代回收。
进程组测试为当前 POSIX 平台证据，不声明 Windows 验证；执行器断连及未入账
故障窗口仍独立验收，不把本批已落盘错误结果冷恢复等同于所有 exactly-once 保证。

## HTTP MCP 未知结果不重发与冷历史补验

后续实际期限耗尽补验：同一 Runtime 矩阵新增 deadline_send/deadline_body ×
三入口共 6 例。MockTransport 分别永久等待发送返回、返回部分 JSON 后永久等待读取，
不主动抛出超时异常，由配置 0.2 秒 active-time 预算取消等待。模型收到未知结果
超时警告前，必须已完成 send finally，或 reader finally 后 response.aclose；
错误事件、唯一调用/握手/账本、同 Thread 冷历史不重发和 client 关闭断言仍保留。
整场景另有 15 秒 watchdog，防止预算失效时测试无限悬挂。

参考 rmcp_client.rs::run_service_operation_once → active_time_timeout：计入活跃
时间、elicitation 暂停不消耗剩余预算；已有原生测试
active_time_timeout_pauses_while_elicitation_is_pending。Corki request 与
_http_request 使用 ActiveTime.timeout，buffered_request 的 owned_response
等待关闭工作完成并保留主异常；不是依靠 HTTPX MockTransport 自行执行超时。
当前 session_recovery、integration/http_cleanup、integration/mcp_elicitation、
unit/http_cleanup、unit/elicitation 五文件 102 通过，29.39 秒，退出 0（0926bc）；
涵盖原有 stdio/HTTP 人工等待暂停及响应关闭取消回归，ruff/format 通过。
这批新增覆盖 HTTP 实际期限耗尽；不将既有 stdio 人工等待测试称为 stdio 断连验收。

本轮重新确认参考 commit ddf04ad26789d040f9ef6a96736f76602e35a6cc，参考工作树干净。
参考 rmcp-client/src/rmcp_client.rs 的 call_tool → run_service_operation →
run_service_operation_with_transient_retries：只有 tools/list 可使用瞬时错误重试，
tools/call 的传输失败直接返回；明确会话过期 404 可重建一次，第二次失败不递归恢复。
connection_manager.rs::call_tool 先验证服务/环境/过滤，再取有效超时交给 client。
Corki manager.prepare_call 保留准入及连接借用；HttpRecovery._run 仅 tools/list
多次尝试，request 仅捕获带会话的 404 并在 catch 外再次调用；MCPTool.execute
将已列举的协议/传输/超时异常变为 MCP 错误值，CancelledError 不在此捕获范围。

test_mcp_session_recovery.py 将既有单/双 404 场景扩为四类故障，新增断连及
ReadTimeout × 三种入口共 6 例。故障在真实 HttpMCPClient 的 MockTransport
收到 tools/call 后注入，不是替换工具 handler；断言只有一次请求/一次握手、
错误完成事件、模型收到错误、唯一结果账本、关闭后同 Thread 原始历史前缀和
账本不变且无工具重发，全部 client 关闭。native 是旧配置兼容标签，不启用原生搜索。
超时同时断言未知执行结果警告；404 原有请求参数/新 progressToken 断言保留。

当前四文件 session_recovery/http_cleanup/output_policy/unavailable_error_value：
59 通过，13.19 秒，退出 0（5630ce）；ruff check/format 和 git diff --check 通过。
其中 http_cleanup 验证工具响应及恢复握手关闭被重复取消时，等待清理后才发布
唯一 TurnCancelled，冷开不重发。仅增补测试及审计，没有生产改动。
这证明上述 HTTP 失败及已落盘结果的冷历史边界，不证明远端操作没有副作用，
也不覆盖所有 stdio/执行器断连、未入账窗口或所有资源生命周期；实际期限耗尽
已由本节顶部的后续补验覆盖 HTTP 发送与响应体读取两个窗口。

## 嵌套非法结果与并行兄弟调用补验

本批参考同一 Codex commit：tools/router.rs 的
dispatch_tool_call_with_code_mode_result_inner 将调用交给 registry；parallel.rs
各调用独立持有执行任务与并行锁；code-mode-runtime/cell_actor/callbacks.rs
spawn_tool 区分 ToolResponse 与 ToolError。Corki graph 的嵌套 dispatch 将失败
持久化为 dispatch_error，service 在读取该结果后抛出 CodeModeToolError；
合法的 is_error 结果仍作为值返回，不能把外层脚本成功误判成内部失败被吞掉。

test_code_mode_error_channels.py 新增六种非法结果 × batch/stream 共 12 例：
错误 call_id、tool_name、含 NaN/Infinity 的 MCP 元数据、直接返回 dict/None。
真实 Runtime 中以 Promise.allSettled 同时调用两个可并行工具，坏调用等待好调用
进入 handler，再返回非法结果；验证 rejected/fulfilled、正常兄弟结果、两个独立
completed 账本结果、原始身份及错误原因。MCP 夹具保留合法 content 数组，避免
仅由缺少必填字段导致拒绝；明确检查校验错误而非把等待超时当作通过。
外层脚本正常完成、模型继续，内部错误仍持久存在且不携带非法 MCP 元数据。

最终错误通道、普通失败边界及嵌套取消三文件 95 通过，15.64 秒，退出 0
（1537fd）；ruff、格式和 diff 检查通过（22f533）。仅测试与审计更新，未发现
需修改的生产差异。不声称这 12 例覆盖冷恢复重放或所有 MCP 生命周期，也不
将它们计入此前 14337 的全量结果。

## 原调用身份与非法返回值的Runtime补验

本批重读Codex core/src/tools/registry.rs::AnyToolResult.into_response：以包裹
结果的call_id及payload生成响应，调用身份由调度层绑定；payload种类不兼容仍
属于Fatal。Corki executor.execute捕获普通结果构造/规范化异常，_normalize_result
拒绝非ToolResult及call_id/tool_name不匹配；error使用原call构造Observation。
这是Python handler边界的运行时验证，不声称Rust也允许任意dict作为返回值。

test_tool_failure_boundary.py新增六例：错call_id、错tool_name、MCP元数据
JSON含NaN/Infinity、直接返回dict/None。均实际调用handler一次；持久结果和
第二次模型请求收到错误，结果仍绑定call-0及broken工具，终态TurnCompleted，
无计划/新上下文或非法MCP/patch元数据发布。既有非法结果矩阵也增加原身份断言。
本批29项通过（414dec，4.12秒），executor、精确数值、MCP结果字段及Code Mode
错误通道联合95通过（d32972，11.83秒），退出0。ruff/format通过（9484dc）。
没有新增生产修复；六例证明已有检查在真实Runtime生效，不冒充修复前红绿证据。
这些新增场景直接覆盖普通调用；Code Mode文件仅为相关回归，不称同六例嵌套
矩阵已验证。B8结果身份和B9非法结果已有此专项证据，其他生命周期项独立验收。

2026-09-12，范围为普通工具执行结果，不涉及官方服务。原生
core/src/tools/registry.rs在未知工具时返回RespondToModel，payload类型不匹配为
Fatal；parallel.rs保留Fatal与普通失败响应的区别。Corki executor对应区分这两类，
不能将全部错误无条件降级，也不能把Python扩展返回的非法对象直接交给持久层。

当前链路：ToolExecutor.execute调用handler → ToolResult/ToolStateUpdate构造校验
→ _normalize_result → graph._execute_bound_tool入账/发布事件。重读发现布尔状态字段
并非缺失校验：ToolStateUpdate.__post_init__已有new_context_requested和explanation
类型校验；ToolResult.__post_init__调用protocol/mcp.py的有界JSON/错误校验与
protocol/patches.py的版本、字段、路径、内容校验。无需重复增加同一层检查。

新增真实Runtime五例：非法new_context_requested、plan_explanation、mcp_result_json、
mcp_error、patch_delta_json，均在handler返回过程中生成，以验证构造异常仍穿过
实际执行边界，而不是在测试收集阶段提前报错。结果必须为持久错误Observation，
工具执行一次、模型两次采样后正常完成；无plan/新上下文状态变更，唯一工具完成
事件为错误且不携带MCP或patch元数据。没有通过放宽校验或删除断言使测试通过。

失败边界/executor/Code Mode错误通道联合79通过10.61秒（628890）；随后补强事件
与状态断言，失败边界文件21通过2.99秒（efd0e2），静态ruff、1181文件格式、
compileall、77依赖、diff均通过。全部进程已退出，无生产代码修改。
这是已有校验的真实Runtime补证，不是修复前失败的生产缺陷；E2中的断连、超时、
所有输出类型与Code Mode生命周期仍由各自专项验收，不从五例推导全部完成。
