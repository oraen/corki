# E2：工具故障完整分类核验

## 2026-09-22：工具最终输入在副作用前通过持久化准入

继续对照 Codex `hooks/src/schema.rs::PreToolUseHookSpecificOutputWire` 的
`serde_json::Value` 改写输入，以及 `tools/src/function_call_error.rs` 的
RespondToModel/Fatal 区分。Corki 另允许注册工具实现
`ToolCallArgumentParser`，其 Python 返回值可在模型原始调用已经落账后
替换执行参数。原 `ToolExecutor.execute` 对最终 JSON 输入只生成了文本，
未在 handler 之前确认 UTF-8 可编码；畸形 Unicode 可让工具先产生副作用，
随后结果归一化/持久化才报错误 Observation。两个单位反例分别通过自带
解析器和执行前回调证明 handler 已运行，虽返回 error；真实 Runtime
会将该错误作为下一 Step 的 Observation。

现在最终输入 JSON 在调用 handler 前验证 UTF-8；freeform 校验使用改写后
的输入，而非原始调用。解析器和回调的两个反例修复后 handler 零执行，
另有 freeform 改写边界用例；真实 Runtime 中解析器坏值只产生唯一错误
Observation、模型继续、无工具副作用。七个工具/Hook/恢复相关文件
**150 passed**，Ruff 与格式检查通过。生产修改后非 CLI 主组
**15348 passed、7 skipped / 808.90s**，单进程敏感组 **459 passed /
61.94s**，两组退出 0，合计 **15807 passed、7 skipped**。七项跳过仍是
六项缺少历史原生编译器、一项文件系统不接受非 UTF-8 文件名。

此修复处理 Corki 动态 Python 工具的额外准入风险，不声称原生 Codex
存在相同的 Python 解析器；外部操作已执行但结果未知的其他窗口仍不保证
exactly-once。

基准为固定Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc。本项限定用户要求
的普通调用路径，不引入原生namespace/tool-search或官方服务。

参考tools/src/function_call_error.rs区分RespondToModel/Fatal；core/tools/registry.rs
未知工具生成RespondToModel，已注册工具的payload种类不匹配则Fatal。
typed handlers解析参数失败回模型；MCP处理保留错误值及传输错误区别，
mcp_tool_call.rs::notify_mcp_tool_call_completed将两者都标Failed而不冒充成功。
普通长输出通过tools/context.rs模型侧truncation投影，不把截断等同工具失败。
Corki graph先检查曝光/调用身份，ToolExecutor校验参数与结果，MCPTool保留独立
参数解析及错误值通道；所有已执行结果经账本完成后进入Observation/事件。

| E2要求 | 当前行为与本轮证据 |
|---|---|
| 未知/未曝光工具 | 真实Runtime未知名返回not advertised错误Observation，下一模型Step可处理；handler零执行 |
| 非法JSON/schema | 新Runtime非法JSON反例验证零handler效果；schema合同覆盖required、类型/nullable、anyOf、数组边界、JSON enum语义；Code Mode的catch/uncaught、批量/流式及同身份重放保留dispatch_error |
| handler异常 | 普通ValueError回灌模型，副作用不重试；显式FatalToolError停止Turn并join并行兄弟。工具自身返回is_error值与宿主dispatch错误在JS中保持不同通道 |
| MCP断连 | HTTP无响应/404恢复及stdio退出/半截JSON，均通过真实Runtime调用链；错误结果唯一落盘，同Thread冷开不重发。实际本地子进程证据仅为当前POSIX宿主 |
| 超时与取消 | HTTP发送/半截响应体由active-time期限取消，stdio/MCP工具等待超时；未知结果提示不能重发。用户取消及handler/media掩盖取消不变成成功完成 |
| 非法结果 | 错调用ID/工具名、非ToolResult、非法Unicode、NaN/Infinity及非法宿主元数据变为有界错误Observation，清除无效附件/状态，保留原调用身份 |
| 超长结果 | 新Runtime使用真实32,000,001字节输出验证原始传输上限拒绝，不伪造成功；低于上限的长文本/错误/有序内容，通过两种普通HTTP适配器验证原始账本/归档保留与模型侧截断分离 |

32MB是Corki的传输/存储防护，不声称Codex有完全相同的全局字节常量。
Python动态handler返回值需要运行时类型/身份校验，不能依赖Rust静态类型保证。
本地schema子集不等于任意JSON Schema关键字引擎；MCP schema仍不是本地授权依据。

## 验证

工具故障/schema/Code Mode错误、MCP HTTP/stdio、普通function输出、executor及
truncation八文件联合246通过、0跳过（f64f5e，43.78秒）。之后新增未知工具、
非法JSON、handler错误三项直接Runtime验证，工具故障文件最终33通过
（821b8f，4.49秒）；不把重叠测试相加。Ruff及格式检查通过，生产代码本轮未变。

E2上述普通路径已核验；模型HTTP错误完整分类归E3，模型/压缩/记忆所有资源的
故障关闭归E4/E7，工具提交故障和内部批准重试归E5。错误结果冷回放不是任意
外部副作用的exactly-once承诺，也不是OS断电/所有平台运行证明。
