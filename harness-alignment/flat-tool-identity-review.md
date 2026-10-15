# B8：普通函数平铺身份与碰撞

## 类型声明自身畸形：进一步核对

当前_validate仅在type为字符串或列表时执行检查；若工具明确声明type:null、
布尔、数字或对象，会被当成“未声明type”而直接放行。ToolSpec只复制parameters，
不会在注册时阻断这种声明，因而普通本地扩展handler可实际执行。
这与前节对未知类型名/非法列表成员的失败语义不一致，属于已有参数契约漏洞。
仍参考Codex PlanHandler的typed parse→RespondToModel→不发PlanUpdate边界，
不声称Codex存在同一通用schema实现，也不改变MCP自定义参数解析。

计划：区分type缺省与type存在但非法；后者返回错误Observation，前者仍允许
enum/anyOf等不单独声明type的有效schema。先扩展真实Runtime的非法声明测试，
验收包括零handler副作用、持久化错误及后续采样收到错误，不扩大支持的关键字集合。

已修复：仅在type键缺省时跳过类型约束，明确给出的值必须是合法字符串或列表。
修复前五个新Runtime反例失败、原41例通过（b62ce5，6.06秒）；失败由后续模型
收到成功ToolResult触发，工具本身已执行。修复后完整工具单位与schema/失败/
namespace集成联合994通过（0d76e2，20.80秒，65751退出0）。保留原enum无type
合法对照、nullable、列表及嵌套约束，不把声明错误变成Turn致命异常。
静态ruff（1a74d2）、1184文件格式（57aeb4）、compileall/77包依赖及diff
（acc548）通过。参考commit与干净状态再次核实（4aa23a）。本批只修改既有
普通工具校验器，不修改MCP parser、审批授权、官方协议或teach.md。

## 联合类型准入：已修复

参考同一干净Codex基线：PlanHandler.handle_call在发PlanUpdate前解析参数，
protocol/plan_tool.rs的UpdatePlanArgs.explanation为Option<String>；类型不符
返回RespondToModel。原生使用typed serde，不据此声称其运行通用schema校验器。
Corki ToolExecutor.execute在handler前调用_validate，但后者只检查字符串形式的
type；普通扩展工具声明type列表时直接跳过类型检查。nullable字符串因此可接收
整数、布尔、数组或对象，违反已暴露的参数契约。本项不涉及MCP自定义parser。

修复限定为已有基本类型的联合表示，沿用整数/数值不接受布尔的规则，仍继续检查
同层enum、数组长度和数值范围。验收在真实Runtime中验证合法值仅执行一次、
非法值零handler效果、错误Observation持久化并回灌。其他schema关键字不由此承诺。

修复前真实Runtime矩阵7 failed/30 passed（a0c009，5.11秒），包括nullable字符串
接收四类错误值、数值联合误接收布尔以及嵌套数组元素。_validate现在统一检查
字符串和列表形式的基本类型，并继续运行同层约束；空列表、未知类型名或非法
列表成员返回错误Observation，不静默取消类型约束。新增四例验证该失败边界。
初步schema/失败/executor/命名94通过，最终工具完整单位与schema/失败/命名
联合989 passed（a29037，19.66秒），无跳过；ruff、format（1183文件）、
compileall及依赖检查（77包）通过。所有测试已退出，无活动测试。
未改MCP自定义解析边界、普通传输或审批策略；这不代表所有schema关键字均已实现。

## 枚举中的布尔/数值身份

继续沿上述 Codex typed parse→RespondToModel 参考链核对 Corki 普通工具准入。
Corki enum 原先直接使用 Python `value in enum`，导致 True 与1、False与0
以及嵌套数组/对象中的这些值被视为相同。工具可只声明enum而不单独声明type，
此时错误值会进入handler。这是 Corki 既有枚举校验契约缺陷，不是要求复制原生
schema引擎，更不涉及模型专用协议。

方案：使用递归JSON值相等判断，布尔与数值严格区分；数值1与1.0保持相等，
对象键顺序不影响相等，数组逐元素比较。扩展原数组Runtime夹具并改名为
test_tool_schema_contract.py；保留原12个数组场景，增加10个枚举类型/嵌套/合法
对照。验收要求非法输入零handler效果，错误持久化并回灌，合法输入执行一次。

修复前4 failed/18 passed（cd9b32）：四个布尔/数值碰撞均错误执行并返回成功，
模型端Observation断言暴露问题。已增加_json_equal，仅替换既有enum成员比较；
数组、对象递归比较，不将数值统一转成字符串，因此保留1/1.0合法等价。
修复后schema/失败边界/executor/Code Mode命名/工具命名五文件86 passed
（7596f4，11.33秒），静态c17b55通过（1182文件、77包）。没有修改MCP解析器，
没有新增完整schema引擎，也不以本批证明真实模型选择质量或全部B8/E2完成。

## 参数数组边界：已确认的实现缺口与修复方案

原生 handlers/mod.rs::parse_arguments 与 handlers/plan.rs 的参数解析使用 typed
serde 解析，失败返回 RespondToModel，在发送计划更新等实际操作之前拒绝参数。
它不是通用 JSON Schema 引擎，不能声称 Codex 逐关键字执行 Corki 的校验器。
Corki 普通本地工具走 executor._validate，MCP/自定义 ToolCallArgumentParser
另有参数解析边界。本项只修 Corki 已声明支持的数组长度约束，不扩成 schema 引擎。

当前 _validate 把 minItems/maxItems 放在“items 是 Mapping”分支里面。因此
声明数组长度但不声明元素类型时，超长/过短数组直接进入 handler；会绕过工具自己
声明的操作批次边界。应将数组长度校验独立于元素 schema，保留原有元素递归。
优先级：B8/B9 参数契约；影响普通工具执行准入，不改变审批权限策略。
验收：真实 Runtime 中 absent/empty/typed items × 长度0/1/2/3，范围[1,2]；
非法输入零 handler 效果、错误 Observation 持久化并回灌，合法输入执行一次。

修复前真实Runtime矩阵2 failed/10 passed（e4876f）：未声明items的空数组与
三元素数组被执行并返回成功，模型侧契约断言暴露问题。已将长度检查移出元素
schema条件，递归元素校验保持不变。修复后与工具失败、executor、调用冷恢复、
Code Mode命名回归联合62 passed（fbf5a5，7.05秒）；静态a8814d通过（1182文件、
77包）。新增矩阵只直接验证普通工具入口，Code Mode文件是回归证据；不宣称
完整JSON Schema关键字支持或全部B8/B9已关闭。

## 冷恢复参数身份补验

重新核对上述 Codex commit（工作树干净）：
`core/src/tools/router.rs::build_tool_call` 将 FunctionCall 的 arguments 原文
放入 ToolPayload::Function，不在路由层以浮点解析结果替代参数。其 namespace
和 encrypted_function_args 不属于 Corki 的普通协议实现要求。
Corki `core/graph.py::_execute_bound_tool` 在使用缓存前调用
`storage/sqlite.py::_claim_tool_call`；后者比较 Thread、Turn、工具名和参数
fingerprint。`_call_arguments_fingerprint` 同时绑定 decoded cache 与精确
raw JSON，不让小数舍入后的相等值授权复用。这里的 SQLite 恢复机制是 Corki
自身实现，不能据此声称 Codex 也使用相同的持久化 ledger。

`test_tool_identity_resume` 从单一路径参数碰撞扩展到路径、舍入相等小数、
下溢相等小数，分别覆盖旧调用已完成和结果未知，共六例。测试写入已提交
Model Step 后重新创建 repository，以 `await LangGraphRuntime.acreate`
进入 `resume_pending`。数值两例显式确认 decoded arguments 相同。
恢复后只采样一次并将 collision Observation 回灌模型，handler 不执行；
旧调用的已完成结果或 unknown 状态保持不变。没有改变公共同步构造接口。

当前联合参数身份、freeform 身份、namespace 路由/响应边界、碰撞规划和精确
结果测试 98 passed（f4d7ef，10.50 秒），静态检查通过（0be028，1183 文件、
77 包）。这是落盘 Model Step 的冷恢复故障夹具，不是实际 HTTP→外部副作用
→杀进程的验证；不替代 schema/结果错误矩阵或其他 A–F 验收。

## 本轮审计范围

参考 Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc，原生
core/src/tools/registry.rs 在注册时把规范 ToolName 冲突记录到 first_collision；
core/src/tools/spec_plan.rs::finalize_tool_router 的严格分支读取此记录，并另行
检查 namespace description 冲突，由计划配置决定严格失败。
原生分组 wire 协议被用户明确排除，不复制；Corki 仍需独立保证普通函数名映射
不歧义，不能因为关闭严格目录冲突检查而允许 wire alias 覆盖。

当前路径：protocol/tool_names.py 将内部分组名稳定哈希为 corki_ns_ 前缀；
models/namespaces.py::request_tool_aliases 收集成功发现定义和当前请求定义，
发现不同规范名映射同一个 wire 名即报 ModelError。两适配器在提交网络请求前
建立这份映射；freeform 的兼容调用转换恢复规范名，不以 leaf 名猜测路由。
已有 test_tool_namespaces 覆盖同 leaf 分组独立执行、冷重开和传输切换。

当前差异清单：

- 同时加载的别名冲突：生产路径已有拒绝；原 Runtime 拒绝测试只覆盖 Responses，
  本轮补 Chat Completions，同样要求零 provider 请求、零工具执行。
- namespace description：只在显式 strict 下拒绝，与目录规划分开，不能混同
  wire 身份冲突；保留既有 source handler、不让 Harness 控制工具覆盖源注册。
- 存储数值身份和工具结果规范化：需复验对应专项，不用命名测试替代这些证据。

## 本轮验证

test_namespace_fault_fails_before_any_provider_request 新增 Chat Completions 两例，
保留 Responses description 案例的旧 native 配置输入（只验证兼容归一化，不启用
原生协议）。两接口的 alias 冲突均在 strict=false 下拒绝，description 冲突在
strict=true 下拒绝，零请求、零执行；没有为通过测试更改生产实现。

与同 leaf 独立路由/冷重开/传输切换、控制工具碰撞、外部注册、专用 namespace
响应拒绝、协议身份、数值 ledger 和精确结果专项联合 113 passed
（380406，10.48 秒）。ruff/format、compileall、77 包依赖和 diff 检查通过。

数值专项直接验证 SQLite claim 的 raw 参数精确身份：包括小数舍入相同但原值
不同、极小数、旧有损 fingerprint，均不能复用不同调用的缓存；结果专项验证
WireNumber 序列化往返及大小限制。这些是存储/规范化测试，不冒充此轮完成了
真实 HTTP 数值参数→外部副作用→进程崩溃恢复的组合。B8 完整 schema/非法结果
矩阵和其他 A–F 项仍需在总索引逐项收敛。
