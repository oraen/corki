# 宿主策略 developer 指令：C1/C2 实现缺口

状态：部分实现并验证。属于宿主来源、上下文角色/顺序与增量管理，不是官方账号或模型专属协议。

## 最新实现与验证

恢复无关贡献者故障两红例修复：在真实冷Runtime向builder注入contributions抛
OSError的贡献者，已prepared checkpoint的新政策/撤销两例均被阻断（bf2b39，
2 failed，0.87秒，退出1）。Codex session/world_state.rs:325–334政策直接来自
宿主已解析requirements；Corki此恢复扩展不需要重新遍历普通贡献者才能取政策。
ContextBuilder新增纯managed_instructions_snapshot，正常build也复用此片段生成；
graph恢复只读该宿主片段，保留冻结非政策内容及既有预算/压缩路径，不吞贡献者异常，
不改变后续新Step完整build。故障哨兵覆盖新政策/撤销、失败采样重试、刷新后再次
崩溃的既有组合，并断言贡献者从未调用；工具后正常prepare不安装该恢复哨兵。
相关四恢复/环境/搜索文件与unit/context共442 passed（858c46，25.32秒，退出0），
4workers/loadfile；Ruff通过（fffd66）。当前正在扩大单元与普通wire/记忆worker
入口回归，最终结果以core-regression-current.md最新记录为准。
先前15281全量属于这次生产修改前，不能直接继承为当前版本全量证据。

冷恢复可压缩预算成功分支已补验（本轮无生产修改）：旧assistant历史24000字符，
初始prepare无压缩，真实call_model checkpoint后冷开；新政策4000个界字符与
旧历史共同超限，但单独能容纳。resume先发普通摘要请求，再发一次正常请求，
恰好一个ContextCompacted事件并TurnCompleted。最终独立政策标签完整且仅一份，
当前用户输入恰好一次，旧assistant大段文本不再模型可见；原始历史前缀不变，
RUNNING账本终结。与不可容纳分支共同2 passed（ba560b，0.77秒）。首次失败
4a7187为新fixture漏传AssistantMessageItem.step_id，补齐后验证；不是生产缺陷。
来源仍为Codex session/turn.rs采样前world-state更新及compact.rs:355–388
摘要替换/当前上下文重注入；不声称Codex具有相同LangGraph恢复机制。

最新非CLI单元全量加该恢复文件14例：5925 passed、1 skipped（3766fb，30.54秒，
进程30797退出0），8workers/loadfile/禁worker重启，按12逻辑CPU/18GiB机器选择
低于96上限。唯一skip仍为文件系统拒绝非UTF-8文件名；Ruff/格式通过（a106cb）。
其中单元5911通过、恢复集成14通过；本结果覆盖当前恢复生产修改，但不是非CLI
集成全量，后者仍待刷新。下一批回到A–E优先级清单，同时补当前集成全量证据，
不因本政策专项通过宣称整个C1/C2或恢复链路完成。

恢复新增政策的硬窗口预算补验（本轮无生产修改）：新增真实RUNNING checkpoint
集成测试，初始政策可正常prepare，采样前中断并关闭Runtime；冷开宿主传入合法
小于40k UTF-8字节、但本身超过该Turn冻结12k窗口的政策。resume_pending必须
以CONTEXT_WINDOW、不可重试失败结束，不能产生TurnCompleted或普通最终采样。
若调用模型，只允许末项为实际tasks/compact提示的无工具普通摘要请求，且不发送
超大新政策；原始历史前缀保持不变、RUNNING账本正常终结。
专项1 passed（382917，0.59秒），增强错误类别与摘要身份断言后，关联恢复和完整
unit/context共405 passed（e991a7，25.15秒，退出0），4workers/loadfile、禁worker
重启；Ruff/格式通过（5276a0）。这是硬窗口拒绝边界，不证明可压缩政策的成功
恢复场景；下一步补“旧历史可压缩、新政策能容纳”的继续执行实证，并刷新变更后
全量回归证据。之前全量仍不覆盖最新恢复生产修改。

旧失败采样恢复新增实证（本轮无生产修改）：真实prepare/call_model checkpoint之后
向实际repository提交ModelFailure，模拟失败事实已提交但graph结果未提交。新政策/
撤销×刷新提交后是否再崩溃四例，恢复先消费旧失败，推进新attempt后采样；请求
包含更新政策且仅一次，无工具重跑。连同原8例，专项12 passed（818822，23.61秒），
相关基础恢复与并发恢复56 passed（6ad32c，24.99秒，退出0），4workers/loadfile，
Ruff/格式通过。连接重试使用实际延迟，没有用静默重跑掩盖失败。
本组验证上一轮“已失败采样保留刷新标记”的分支；剩余新增政策预算溢出及压缩
仍需直接反例，不用恢复绿色代替预算行为。当前全量仍为此恢复生产变更前证据。

RUNNING采样前恢复两红例已修复：Runtime在即将恢复call_model/retry_model的checkpoint
设置refresh_recovered_context；Step绑定视图在真正采样前刷新builder世界状态，调用
现有window.prepare持久追加/预算/必要压缩，不重跑输入Hook、不重绑工具或改变
已保存request_tools，固定基础沿context_instructions。新请求视图随节点输出进checkpoint。
已完成/部分采样先协调原事实，不提前重建；已记录失败保持刷新标记至下一重试采样。

原四组合与基础恢复16通过（d47649）；关联432通过（4cf64e），扩大恢复文件群677
通过（ab3901，46.44秒），均终态。追加“上下文提交后崩溃且终态写失败”真实窗口，
再次冷开只保留一次政策更新，工具不重跑：8 passed（1d8d55，2.35秒）。随后修正
失败采样应保留刷新标记，关联52 passed（c0c393，16.61秒）；该命令格式检查退出1，
终态后仅格式化并重新通过Ruff/四文件格式检查。全量未刷新，677发生于最后标记细化前。

仍需专项预算溢出/自动压缩及失败采样重试的新增政策组合，不能仅由window.prepare
接线推断所有边界已验证。此次重建也涉及其他world-state贡献者，须继续复核恢复
中贡献者刷新与Hook投递交错，不把正常8例推广为所有崩溃窗口均安全。
以下两红例描述为修复前证据，现不再是当前失败状态。

当前新增真实RUNNING恢复反例仍有两失败（未修复）：test_managed_policy_recovery.py
在实际compiled graph采样前或工具提交后中断，关闭Runtime，再带新政策/空政策
冷开resume_pending。采样前两例恢复请求只有OLD POLICY；工具后两例经过prepare
正确追加替换/撤销且工具不重跑。结果186bd4：2 failed/2 passed，1.22秒，退出1。
Ruff通过（465070）。这不是全量回归失败汇总，而是全量之后新加入的确定性反例。

原生session/turn.rs在采样前record_step_world_state_if_changed，输入随后由历史生成；
world_state.rs从当前宿主requirements取得策略。Corki resume_pending在call_model
checkpoint恢复只补Hook反馈，不重建宿主策略；_call_model直接使用保存的request_items。
这是Corki持久图特有窗口，不能声称Codex有同样的LangGraph checkpoint实现。

下一批修复必须在未采样的恢复请求生效：新策略或撤销先持久追加，再更新请求视图；
保持固定Turn基础/Step模型及工具快照、不重复Hook/工具副作用，计入新增token预算。
不要简单重跑整个prepare或改变graph游标而忽略已提交副作用；也不要只改临时HTTP
正文。必须覆盖追加后再次中断的幂等性及下一次冷恢复。不跳过当前两红例。

工具后中途压缩补验（本轮无生产修改）：真实Runtime首采样ToolCall并提交Observation，
180k usage触发摘要，再采样完成；三次请求均有唯一完整标签/独立developer政策。
摘要没有工具定义但包含完整call/result，后续请求保留当前用户输入。关闭后冷开
同Thread继续，政策不重复、原前缀不变、工具执行计数及原始call/result各为1。
专项1 passed（622189，0.67秒），进程随后退出0（5bce1b），没有在清理时提前重启。
关联宿主策略/普通wire/模型生命周期/基础恢复与完整unit/context共434 passed
（1879cf，8.06秒，退出0），4workers/loadfile/禁worker重启；Ruff和格式通过
（b53eff）。本例是已完成Turn后的冷开，不冒充待恢复RUNNING Turn故障窗口测试。
下一步仍需核对RUNNING恢复时宿主策略变化与旧checkpoint请求的实际契约。

Guardian省略/普通撤销已补运行反例并修复：同一已有政策Thread由宿主冷开为普通或
Guardian，原前缀必须不变，普通缺省追加撤销，Guardian不追加任何该政策项。
原1 failed/1 passed（b6e688，0.86秒；后接sed导致shell退出0，不视为测试成功）。
ContextSnapshot新增仅宿主构建的omitted_sections默认空集合，builder依据已验证
SessionSource选择；window传给world-state比较，省略项不发布增量或撤销。
此标记不写入业务历史、不交给模型，不按旧历史自身字段决定权限，也不删除旧行。
普通缺省仍走既有撤销；基本Guardian不添加策略片段，不改历史投影视图。

关联宿主策略、两普通wire、实际memory上下文及完整unit/context共400 passed
（e2ad91，7.10秒，退出0），4workers/loadfile/禁worker重启，Ruff通过。
这是全量15252通过之后的新生产增量，尚未刷新全量，不能沿用旧全量宣称当前全绿。
下一步中途工具压缩/恢复交错及A–E其他开放项；完整Guardian产品实现不在此修复中。

后续全量已刷新：非CLI unit5911通过/1文件系统跳过（c4e45a），非CLI集成9341
全部通过（3db7ca，506.75秒，session30649退出0）。合计15252通过、1跳过；
完整集成输出integration-regression-30649.txt。没有在测试运行期间改代码。
仍不能据此关闭下述Guardian省略section的源码差异；下一批先补反例再修复。

Guardian历史边界源码复核（全量回归运行期间只读）：review_session.rs 915–953 的
新review初始历史是可选parent_compaction单项，忙碌时fork来自Guardian trunk，不是
无条件复制普通父会话完整历史。world_state.rs对basic source不添加managed section；
world_state/mod.rs::render_with仅遍历当前section，因此“该section不参与”不等于
“发送撤销旧政策”或“删除历史”。不能自行新增旧历史清除作为原生对齐要求。

Corki 当前builder对Guardian与普通缺省均不添加政策项，而changed_context_items
对所有消失key统一撤销。若宿主把已有政策历史交给Guardian，这两种情况可能混淆。
下一批在集成全量终态后补真实源切换/已有历史反例，明确section省略与普通政策缺省
撤销的区别；目前为源码差异，尚未以运行反例核销。不得在当前回归运行中改代码。

普通HTTP与实际记忆worker追加验证（本轮无生产修改）：新增两种普通适配器的
真实Runtime→OwnedHTTPClient MockTransport，按配置地址/端点断言，三次冷开分别
首次/替换/缺省撤销；独立消息角色为Responses developer、Chat兼容system，保留
原历史前缀，不混入固定基础，不泄漏managed_config内部content kind。两例通过
（c2766c，1.02秒），不是对真实供应商质量或外网服务的验证。

源码 memory/pipeline 捕获 MemoryPermissionSnapshot，memory/agent.py将
prepared.permissions.managed传给内部memory_consolidation Runtime。扩展实际
test_memory_agent_context 的四种skills场景：主模型及记忆worker每次真实请求均含
唯一HOST_POLICY_PROOF独立developer片段；记忆工具执行、污染隔离、最终发布断言
仍保留且通过。三文件联合18 passed（c89a77，6.77秒，退出0），4workers/loadfile，
Ruff/格式通过（3b3542）。未新建后台代理，运行的是隔离测试fixture。

下一步仍需核对Guardian已有历史投影语义与中途压缩/恢复交错，然后刷新全量。
当前不再把普通HTTP wire或上述实际记忆worker传递列为缺失；不由此关闭整个C1/C2。

后续增量：legacy developer旧政策四种冷恢复（相同/不同/空串/缺省）原全部失败
（1d6c70，1.23秒）：替换缺专属通知，撤销没有typed项。changed_context_items现对
独立legacy.developer标签登记临时Unknown，保留原行不改写；后续typed快照覆盖，
不再额外撤销legacy.developer键。两个真实冷Runtime续接只追加一次政策更新。
来源边界覆盖user角色、前缀引用、自定义key、输入附着，不接管这些片段。

普通自动及手动压缩真实Runtime两例通过（4f44af，0.86秒），最终请求有且仅有一个
完整标签/独立developer策略片段，原历史前缀不变。这两例无需额外生产修复：现有
压缩历史投影会调用typed renderer，不能仅因内部快照存原文就判定wire丢标签。
当前关联model lifecycle、managed policy/config及完整unit/context共450 passed
（d9b27a，7.82秒，退出0），4workers/loadfile/禁worker重启，静态及格式通过；
测试期间未改生产或测试。未刷新全量，未宣称mid-Turn工具后压缩或HTTP wire已核验。
下一步内部会话已有历史投影、记忆worker传播及两普通HTTP端到端；legacy Unknown
和普通自动/手动重建不再列为完全缺失。以下记录保留此前阶段的开放状态。

新增 ManagedDeveloperInstructions 不可变来源/正文值，经既有宿主 requirements
层合并，低到高标量覆盖（空串有效）；类型逐层校验，大小校验在最终有效值上执行。
原生 requirements_layers/stack_tests 明确高层空串覆盖低层；字节预算系数核实为4，
40KB含替换提示和标签，UTF-8计算，不截断。快照新增末尾可选字段保持既有位置参数。

Runtime构造将策略接到builder副本，普通Step将其放在宿主上下文末尾、独立developer
消息；world_state有专属替换/撤销。基础前缀不替换，不增加用户配置字段或官方入口。
沿用SessionSource.is_basic_guardian：内部Guardian及legacy reviewer省略，普通review
及Custom同名不省略。此处验证初始策略隔离，不声称已有历史的Guardian投影已核验。

集成反例最初在解析处失败（6930ab），后来同时修正测试夹具database参数名为真实API
database_path。真实跨Runtime注入/替换/清空、历史前缀不变及去重已通过；四来源实际
请求隔离、三种覆盖、三种非法类型与UTF-8预算边界亦通过。关联完整unit/context、
managed_mcp及新增文件414 passed（93794e，3.03秒）；命令随后静态检查因import排序
退出1，非测试失败。终态后仅自动修正import，Ruff及七文件格式检查重新通过。
4workers/loadfile/禁止worker重启，测试运行时未改生产或测试。未刷新全量。

仍需实现/核验：legacy标签Unknown识别、压缩后重建、缺省政策撤销、两普通HTTP
传输、内部会话的已有历史投影与记忆worker策略传播。以下缺失描述是修复前证据。

## 已确认的原生链路

固定参考 ddf04ad26789d040f9ef6a96736f76602e35a6cc：

- core/src/config/mod.rs 在配置加载时调用 validate_managed_developer_instructions，
  输入来自 config_layer_stack.requirements().additional_developer_instructions。
- core/src/session/world_state.rs::build_world_state_for_step 对非 basic session source
  添加 ManagedDeveloperInstructionsState；不能不加区分地向所有内部子会话传播。
- core/src/context/world_state/managed_developer_instructions.rs 保留来源 Sourced，
  校验正文加替换通知/标签的总预算（10000 estimated tokens），超限拒绝而不是截断。
- developer 独立消息，content kind managed_config.developer_instructions，标签
  managed_developer_instructions。非空（不是 trim 后非空）正文形成快照；相同静默，
  替换/撤销使用专属通知，Unknown 视为已有指令，支持旧 developer 标签识别。

## Corki 当前入口与实证

config/managed_mcp.py::load_mcp_requirements 从系统路径和宿主显式层读取，
compose_mcp_requirements 只分离 execution 域，剩余交给 mcp_shapes 校验。
MCPRequirementsSnapshot 只有 policy/sources/execution，没有此独立策略。
Runtime.create 已将快照传入 LongTermMemoryService 的 managed_requirements 并在
Runtime 保留；graph/builder 的该策略通道尚未接通。可复用已有宿主快照载体，
但不能将记忆服务已接收快照描述为主循环已接线，也不能另从普通 TOML、环境变量
或模型响应取得同等权限。

离线真实解析探针（5b427d）：传入 MCPRequirementsLayer("host-fixture",
'additional_developer_instructions = "HOST POLICY"')，实际抛出
ValueError: Failed to parse requirements layer host-fixture: Unsupported non-MCP managed requirement。
这是缺口的执行证据，不是通过测试，也没有向系统目录写入政策或发起模型请求。
检索 src 与 tests 未找到该字段或对应 managed_developer 实现；协作模式的
developer_instructions 是另一条用户/宿主模式契约，不是替代实现。

## 下一批实施与验收

1. 先确认 requirements 多来源标量优先级；guardian/review.rs 已确认 basic source
   仅为 Guardian 内部会话及对应 reviewer 标签，不是全部子会话。核对 Corki 对应入口，
   复用现有宿主不可变快照，加入带来源的可选指令与严格大小/类型校验。
2. 接通真实 Runtime→builder→world state，保持独立角色、顺序、标签及持久快照；
   不覆盖会话基础指令，不允许项目配置提升为系统政策，不引入官方服务。
3. 写真实普通请求反例：首次注入、未变去重、冷恢复替换/清空、原历史前缀不变；
   补旧 developer 标签 Unknown 与用户引用/输入附着不接管，压缩重建及内部会话隔离。
4. 覆盖来源优先级、非法类型、超限和失败前零采样；再跑关联回归和静态检查。

本审计不关闭 C1/C2，也不将其归为用户明确排除。CLI/teach.md 不改。
