# 搜索 → 压缩/目录变化 → 重新发现（B/C组合验收）

## 冷恢复遇到目录变化（A/B/E，实施前证据缺口）

现有test_search_ledger_resume_restores_definitions_without_reexecution覆盖ledger已完成、
结果已追加及真实checkpoint三种窗口，但冷进程目录与旧目录完全相同。尚不能证明
恢复时旧定义不会授权已变化工具。Codex core/tools/handlers/tool_search.rs 从当前
runtime.search_info构建检索器；成功定义进入专用输出（用户排除该协议）。这里对齐
当前目录发现职责，不宣称Codex具有同一种SQLite/LangGraph恢复格式。
Corki current_discovery_history投影旧结果并提示重新搜索，build_tool_plan以当前spec
匹配，StepToolState.resolve冷恢复重新绑定当前handler。计划扩展上述真实恢复测试：
冷目录描述变化时旧定义不曝光，新搜索后才调用；持久旧结果不改写，旧搜索不重执行，
当前用户不重复，工具执行一次。无变化分支仍直接恢复定义。无需预设必须修改生产代码。

核对后的边界修正：不能把“已有request checkpoint也立刻替换请求工具定义”作为验收。
graph._call_model_owned保留该Step的request_tools；StepToolState.resolve仅重新绑定
当前handler；executor.execute比较保存spec与当前spec，不相等返回错误Observation。
下一prepare才投影失效定义并让模型重新搜索。新增测试最初对此做了错误假设，因此
checkpoint+目录变化1失败（29e347）；其余5通过。另一次夹具误用不存在的load_history
已修正为真实repository.load_items，不能把夹具失败描述为生产缺陷。

扩展现有恢复测试到三种窗口×目录未变/描述变化/schema新增required room：
未准备请求的恢复直接过滤失效定义；已保存请求先保留旧spec，尝试旧调用必须返回
definition changed且不执行handler，然后新搜索加载当前定义，按新schema调用。
全部分支检查旧搜索不会再执行、旧持久discovered_tools不改写、用户唯一、工具仅执行
一次、结果回灌后完成，再resume无事件。该批是新增组合证据，无生产代码修改；
Codex专用tool_search输出继续排除。预设模型调用不证明真实模型工具选择质量。

包含恢复/搜索错误/工具目录/普通端点隔离/压缩的扩大回归67通过（1e4434，15.22秒），
其采集早于新增schema变体；schema与其余恢复变体由后续专项补验。

最终专项9通过（c09b40，2.50秒），包括warm模型未重新采样已提交搜索Step的断言。
静态1092文件、compileall、77包/diff通过（05a25e）。Codex基准仍为
ddf04ad26789d040f9ef6a96736f76602e35a6cc且无改动。当前无活动测试；全目标未完成。

## 定义内容的序列化边界（实施前）

元素类型校验不等于可持久化：ToolSpec.parameters可以含object/NaN/无效Unicode。
当前ledger通过_result_to_json→tool_spec_to_payload→dumps_wire编码，普通历史后续再写SQLite。
计划复用相同payload/精确数字编码器在执行边界验证UTF-8可编码性，不能替换为字符串化
未知对象或静默删除schema字段。失败返回Observation而非ledger阶段Turn失败。
这不改变原生专用协议排除项，也不声称完成全部JSON Schema语义验证。

已实现：类型/快照之后复用tool_spec_to_payload和dumps_wire，并检查UTF-8及32MB
原始定义传输上限；与普通content独立检查，避免短content藏超大定义。没有新增任意
schema关键字白名单。3真实Runtime场景先红（1ee767），此前在ledger编码/SQLite
报内部TurnFailed；现在错误Observation可继续修正搜索。执行器/搜索专项50通过
（920132），静态1085文件77包通过（229fbb），扩充独立定义预算测试。
此处验证可编码数据，不声称修复既有任意自定义数值类型的schema语义或所有存储映射。

67880终态151通过6.68秒（dd35e0），覆盖搜索九场景、定义体积/快照、执行器、
目录缓存、模型搜索生命周期、自动压缩以及storage单位。当前无活动测试。

## 搜索返回定义的类型边界（实施前）

原生tools/context.rs::ToolSearchOutput的tools是Vec<LoadableToolSpec>，Rust类型边界
排除了任意null/map元素；此类型约束可参考，但其专用响应协议仍排除。
Corki ToolResult.__post_init__仅tuple化discovered_tools，ToolExecutor._normalize_result
对搜索直接保留元素；错误类型可逃出工具边界，直到历史序列化/下一请求才失败。
计划在普通执行器中验证ToolSpec元素，失败走既有错误Observation，不加载工具，
并延续五步纠正搜索测试。无需改变持久格式或引入原生协议。

已修复：执行器确认搜索定义元素均为ToolSpec，非搜索handler仍不能注入发现定义；
成功结果deepcopy定义快照，错误搜索不携带可加载定义。两真实Runtime故障注入先红
（e065f4）：null/map导致历史asdict失败并TurnFailed；修复后错误Observation→修正搜索
继续。新增持有原嵌套schema引用并在返回后修改的测试，确认结果快照不变。
此项是元素类型/所有权边界，不将其表述为任意schema语义都已验证。
静态1085文件77包通过（f8d173）。

30594终态42通过5.13秒（45b1b0），覆盖搜索故障六场景、执行器、搜索/快照、
model_search_lifecycle与deferred_namespace_compaction。当前无活动测试，完整goal未完成。

## 搜索错误与纠正（B/E，新增组合验证）

原生tools/handlers/tool_search.rs::handle_call对空query/零limit返回RespondToModel；
router.rs在专用call入口反序列化参数失败同样RespondToModel（专用结构不移植）。
Corki普通function调用经ToolExecutor schema校验/异常归一化→错误ToolResultItem；
discovery.build_tool_plan只从非错误搜索结果加载定义。未加载工具仍不在dispatch。

新test_search_error_recovery用真实Runtime走五步：错误搜索→未加载工具调用被拒绝→
修正搜索→调用→观察结果后完成。覆盖空query、参数类型、limit和一次index.rank异常；
前两次错误不加载工具/不执行handler，随后加载并仅执行一次，四个持久Observation
的错误标志严格为true/true/false/false且call_id不同。无需生产改动。
这补强执行器单位测试，未覆盖所有畸形handler结果；不冒称真实模型主动纠错质量。

11039终态43通过4.83秒（41b176），覆盖新增四场景、search/cache/snapshot单位、
model_search_lifecycle及普通inventory链路。静态1085文件77包通过（100b49）。
当前无活动测试，仍非完整A–F完成证明。

范围：Harness普通tool_search函数；内部namespace_description只作分组检索元数据，
不引入原生namespace/tool_search_output或远程压缩。

Codex固定基线工具调用入口 `tools/handlers/tool_search.rs::handle_call` 校验query/limit，
从当前search_infos检索；原生ToolSearchOutput及history normalize专用结构仅参考，
是用户排除的传输路径，不能声称Corki逐字复刻该协议或服务端定义缓存。

Corki真实链路：graph._prepare_model_context中的当前目录projection先于窗口预算；
window_manager.prepare执行普通摘要，graph._tool_plan → discovery.build_tool_plan从
有效ToolResultItem.discovered_tools匹配当前ToolSpec。current_discovery_history投影
过期定义，不改原始持久搜索记录。Step绑定的dispatch快照和下一Step新目录分离。

原测试 `test_automatic_compaction_reinjects_full_current_map_and_releases_loaded_tool`
只验证压缩后释放旧工具，本轮扩展至完整六次模型请求：搜索、旧快照调用、普通摘要、
重新搜索、新定义调用、收到FRESH_READ_PROOF后完成。执行计数严格为old/new各一次，
只压缩一次，当前用户输入保留，摘要无tools，原始长Observation和旧/新目录记录仍在。
这不是新生产功能：补强既有组合行为证据，未调整Runtime工具选择逻辑。

与冷checkpoint/模型切换/重试组合14通过3.59秒（bb204d）。模型调用为确定性脚本，
只证明Harness链路，不证明真实模型的检索或选择质量，也不是完整A–F验收。

扩大组合83068终态39通过7.46秒（c154f7），额外包含tool_inventory、uniform_model_capabilities、
no_default_model_endpoint和flat_search_request，确认普通工具/端点边界未回归。
静态1084文件77包通过（6b3f61），当前无活动测试。
