# B2/B10 工具曝光与 Code Mode 入口复核

当前B2验收已收敛：重读原生ToolExposure六种定义及is_direct/is_deferred/
is_available_in_code_mode，与本地CODE_MODE_EXPOSURES、nested_specs、build_tool_plan
和MCP mask投影对照；包含曝光矩阵与实际隐藏/恢复调用的联合502项通过
（33f2f1，41.59秒，范围详见tool-search-ranking-review.md）。因此B2标记为用户
普通路径下行为一致，不继续泛称曝光未实现。B10的cell取消、生命周期与恢复不是
B2曝光策略的一部分，仍由独立清单验收。无生产变更，原生Rust测试未执行。

Codex tools/src/tool_executor.rs::ToolExposure 定义六种曝光：Direct/Deferred 同时可
嵌套；DirectModelOnly/DeferredModelOnly 不可嵌套；CodeModeOnly 仅嵌套；Hidden
保留注册但不曝光。core/tools/spec_plan.rs 将 configured direct-only namespace
改为 DirectModelOnly，is_hidden_by_code_mode_only 隐藏可嵌套工具的直接模型入口，
register_code_mode_executors 仅为允许嵌套的 exposure 建立 code-mode names。
namespace 在这里是内部身份/分组，不要求 Corki 向模型发原生 namespace 协议。

Corki ToolExposure 与 CODE_MODE_EXPOSURES、code_mode/specs.py::nested_specs、
tools/discovery.py::build_tool_plan 对应这两种独立入口；router 根据当前捕获的
registry snapshot、MCP omit mask 与 mode 建立候选计划，不污染其他候选/旧 Step。
CodeModeOnly 会隐藏普通可嵌套工具的直接入口，但仍保留 DirectModelOnly；Deferred
可通过普通函数 tool_search 加载，也可在 Code Mode 内从 ALL_TOOLS 元数据发现。

本轮 test_mcp_exposure_surfaces、test_model_tool_router、test_namespace_policy
共378通过（122139，34.05秒），覆盖三模式、三搜索配置、8种MCP omit组合、前缀与
direct-only策略。矩阵部分只核对请求与nested定义，不冒称每个组合都执行了MCP工具。
另有实际四Turn reconcile：DirectModelOnly→Deferred→Hidden→CodeModeOnly，
HTTP MCP 仅初始化一次、目录拉取一次，三次合法调用，关闭时释放客户端。

本轮加强其中 Hidden 阶段：真实 cell 尝试调用已移除 tools 属性，捕获失败标记；
ALL_TOOLS 不包含该名字、MCP tools/call 计数不增长。之后重新设 CodeModeOnly，
真实调用恢复成功。加强后单项通过（8e9bd6，0.89秒），原始矩阵采集早于新增断言，
不把378称为已含新增断言。静态1110文件/compileall/77包/diff通过（919e75）。

本轮无生产修改。曝光不是安全执行授权：即使入口可见，执行审批仍独立验证。
原生 mode/namespace/Lite 的历史测试配置不意味着启用专属服务。B2主要入口与MCP
掩码已核验；B10 cell生命周期、调度/恢复全部细节不由这组曝光证据外推，完整A–F未完成。
