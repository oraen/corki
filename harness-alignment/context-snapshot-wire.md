# 上下文来源、快照与普通传输（C1/C2）

## 2026-09-22：world-state 默认 section 枚举与 realtime 结束过渡

固定 Codex `session/world_state.rs` 与 `context/world_state/{mod,realtime}.rs`
逐项核对当前默认/配置路径，而不是把源码目录里所有 section 都当默认缺失：
Model、AgentsMd、Permissions/CompactPermissions、Collaboration、Environment、
ContextWindowGuidance、PluginsInstructions、Tools 与宿主扩展分别对应 Corki
的 model.instructions、project.agents、permissions/approved_command_prefixes、
mode、environment、context_window_guidance、插件指导、deferred_tools 与
extension.world_state；各项细节仍按 C1/C2 专项证据验收。Codex 的 Apps
是用户排除的官方连接器产品路径；Persistent effort、MultiAgent 模式/
usage hint、DeferredExecutor 多环境指令是条件路径，范围选择见
`persistent-mode-decision.md`，不冒称当前已实现。Realtime 在两者均有
活跃路径，因此需要具体核对，不能归入这些条件分支。

Codex `RealtimeState::render_diff` 在 active→inactive 发专用结束片段；
Corki 的 `RealtimeContextContributor` 只在活跃时贡献 start，下一普通 Turn
原来由 `world_state._removal` 发通用 section 删除通知，缺少实时输入已结束的
明确语义。新增真实 Runtime/SQLite：实时 Turn→普通 Turn→关闭冷恢复→
普通 Turn→再次实时 Turn。修复前第二个请求没有专用 end（1 failed）；
现在只对 `realtime.active` 的撤销发本地 `realtime/end` 开发者片段，
其 `snapshot_content=""` 阻止冷开及后续普通 Turn 重发；再次进入时 start
正常出现。提示文本仅描述 Corki 自己的实时文字输入，不复制 Codex 的语音
转录专属说明，也不改变实时输入/取消所有权。

完整 unit/context 目录与实时、上下文更新/角色、默认 world-state、输入边界及
Turn 准入六个集成文件 **464 passed / 6.37s**，4 workers/loadfile/禁重启，
实际退出 0。此生产修改后，非 CLI 完整范围按互斥文件分组刷新：短期限
敏感六文件单 worker **459 passed / 55.89s**；其余 unit/integration 排除
CLI 与前六文件、4 workers **15279 passed、7 skipped / 738.24s**，两批
实际退出 0。独立收集 **15745 项**，合计 **15738 passed、7 skipped**；
七项跳过仍是六项缺历史编译器、一项文件系统限制。上节 15737 是修复前
基线，已被替代；
不因单个过渡修复宣称 C2 或全部实时行为完成。

## 2026-09-22：其余旧 typed 快照的过深 JSON 冷恢复降级

Codex `context/world_state/mod.rs::render_diff` 对无法反序列化的旧 section
快照统一降为 `Unknown`，不把旧比较元数据当执行授权。Corki 已对环境/
deferred 目录、模型指令及 personality 等路径补过此边界，但权限比较
`permissions._decode`、旧模型切换标记 `model_transition.previous_model` 和
扩展 section 的 `render_section` 仍漏接 Python `RecursionError`。

新增真实 Runtime/SQLite 冷恢复四组合：完整/精简权限、旧模型标记、
扩展 world-state。原行写入 16000 层旧 JSON 后关闭重开；修复前四例
均 `TurnFailed`，异常为 JSON 解码递归深度耗尽。现在只在旧比较快照
解析边界捕获该异常，按原有 Unknown/无旧模型身份路径刷新当前来源；
不改变权限编译器、审批、当前模型选择或扩展 renderer 的执行错误分类。
四例均转绿，旧 SQLite 前缀不变，权限两条路径连做两个 Turn 不重复
刷新，完整权限旧可见正文仍在请求，扩展 renderer 收到 `Unknown`。

连同完整 unit/context、权限、环境、模型快照及扩展 Runtime/wire 等关联
文件 **529 passed / 10.63s**，4 workers/loadfile/禁重启，实际退出 0；
修改文件 Ruff/format/diff 检查通过。生产修复后的非 CLI 完整范围已按
互斥文件分组刷新：短期限敏感六文件单 worker **459 passed / 55.16s**，
其余 unit/integration 排除 CLI 和前六文件、4 workers **15278 passed、
7 skipped / 732.12s**；两批实际退出 0。独立收集 **15744 项**，与
合计 **15737 passed、7 skipped** 一致；六项跳过缺历史编译器、一项
文件系统不接受非 UTF-8 文件名。上一轮 15733 是修复前基线，已被替代。
这组只证明具体旧快照损坏边界，不把任意执行账本损坏或所有第三方
扩展渲染失败统一吞为 Unknown。

历史全量证据（已由验收索引中较新全量替代）：74939实际退出0（92002f），
15490 passed、1文件系统限制跳过，
547.32秒；完整日志full-regression-74939.txt。包含下述近期模型与环境/目录修复，
各批次“生产后全量待刷新”为当时记录，已由本轮替代；其它未核验行为仍开放。

## 环境与工具目录旧比较状态：已有降级的冷启动实证

补充解析异常边界（已修复）：本机 Python 3.12.3 的 json.loads 可解析2000/
4000/8000层数组，16000层抛 RecursionError（1cc768）。因此前轮2000层通过
确实未覆盖异常捕获。扩展同一真实SQLite冷启动测试到16000层后四例均TurnFailed，
4失败4通过（30f73d，55214退出1），不存在通过无关重跑推定原因的情况。
现仅 environment/deferred_tools.decode_snapshot 增加捕获 RecursionError，沿
既有Unknown→当前完整快照路径恢复；未捕获MemoryError/任意Runtime错误，未改
权限、工具曝光判定或执行账本。原历史和可见正文保留，未搜索工具不被加载。
扩大完整unit/context与depth、runtime_environment、environment_permission_snapshot、
deferred_namespace_context、model_snapshot_recovery共447 passed，7.64秒
（d27ef6，53078实际退出0）；4 workers/loadfile/禁重启，null keyring/当前
sandbox compiler。Ruff/格式/diff check通过。此两处生产变化后仍需刷新全量。

原生 environment.rs:138–141 将 Unknown 当空基线；tools.rs:56–78 对 Unknown
渲染当前完整目录（当前为空则不发）。这里只参考内部目录生命周期，不恢复用户
排除的原生 namespace 协议。Corki environment/deferred_tools.decode_snapshot
验证版本及字段类型，render_update 对未知旧基线刷新当前值；未知旧可见正文保留。

新增 test_context_snapshot_depth 使用2000层数组作为不符合section结构的旧JSON，
覆盖环境/目录×静默/可见四个真实SQLite冷启动组合。检查连续两Turn完成、一次
有效新快照、持久前缀不变、每次请求仍保留旧正文；目录更新不加载 deferred 工具，
工具副作用次数为零。四例直接通过（3fc5c9，13647退出0），没有生产修复。
该用例验证具体嵌套数据与结构降级，并未证明触发解释器 RecursionError 后也能
恢复；现有 decoder 仅捕获 ValueError/TypeError，不能把这层尚未触发的边界记全。

扩大完整unit/context及depth/runtime_environment/environment_permission_snapshot/
deferred_namespace_context四集成文件共415 passed、5.19秒（c42afc，98805实际
退出0），4 workers/loadfile/禁重启，null keyring/当前 sandbox compiler；Ruff/
格式/diff check通过。无生产改动，执行账本与实际权限校验未修改。

## 旧模型指令比较快照损坏：已修复

后续回退边界补证：原生 session/world_state.rs:59–80 先取 previous_turn_settings，
缺失时仅在 Model provenance 且基础文本与当前模型指令不同的情况下用基础模型；
Custom provenance 不据旧模型标签推断切换。Corki builder.with_session_base 与
snapshot 的 base_model 条件对应此链，未引入新的官方服务或模型标签分支。

test_model_snapshot_recovery 增加无 accepted marker 的八个组合，以及 Model/
Custom 基础来源 × 有/无持久压缩 marker 四例：无任何旧身份不臆造切换；
Model 来源的旧基础触发一次新模型规则，Custom 来源不误触发；两者基础文本均
保持 FROZEN BASE。压缩恢复保留摘要、隐藏旧正文但不改原行；无压缩则保留正文。
压缩 marker 是明确的持久历史夹具，不冒充本轮真实生成摘要的证据。
初版四个失败来自夹具在 _ensure_ready 首次落盘后试图用 ensure 覆盖基础，
而 INSERT OR IGNORE 正确保留初始值（d83a28，16358退出1，4失败50通过）；
改为用真实旧模型/自定义基础配置创建源会话后验证，不修改生产以迎合错误夹具。
扩大461 passed、7.93秒（bb59f0，6201实际退出0），沿同样4worker环境；Ruff/
格式/diff check通过。本批仅测试修改，前轮生产修复仍待后续全量刷新。

原生 world_state/mod.rs:117–142 将反序列化失败转 Unknown；model.rs::render_diff
在 Unknown/Absent 时用已有 previous_model 判断是否切换，不把损坏值当模型名。
Corki world_state 原先在历史扫描、去重及渲染三处直接解析 model.instructions，
破损 JSON/数组/缺字段导致 TurnFailed，布尔 model 还会误触发同模型切换。
真实 SQLite 冷启动16组合（四种损坏×静默/可见×同/跨模型）先15失败1通过
（ee5d5d，39785退出1），不是仅从代码猜测的风险。

新增 model_instructions.restored_model 验证旧模型身份，失败只记录不含原内容
的诊断，不覆盖可信 accepted-model marker。比较/渲染回退到已知 previous_model
或原有 base_model；旧可见行缺有效渲染状态则保留正文和身份。新状态仍按原生成
路径解析，不将所有解析/执行账本错误统一吞掉，不改数据库原行。
测试验证两次正常 Turn、只追加一次比较快照、同模型不发切换、跨模型新规则出现、
旧正文逐请求保留和历史前缀不变。

完整 unit/context、model_snapshot_recovery、model_instruction_lifecycle、
personality_legacy、personality_compaction 共449 passed、8.32秒（195009，
11534实际退出0），4 workers/loadfile/禁重启，null keyring/当前 sandbox compiler。
Ruff/格式/diff check通过。生产已变更，15450全量早于此修复，后续需刷新；
不能把本节推广为所有上下文贡献者或损坏持久字段均已处理。

最新状态：personality本地模板、宿主选择、Turn冻结恢复、Thread持久默认/更新和
独立增量section已接入；旧标签Known优先与Unknown模型回退、普通双接口、自动
压缩跨模型去重、旧列迁移及隐式冷恢复已补证。独立旧reference的实际行为映射和
未覆盖的跨模块故障仍需核定，不把全部旧标签恢复继续笼统列为缺失。原生公开Turn更新
接口不含personality，不新增此入口；已有模型切换须保留原选择，串用缺陷已修复。
原生完整分支、本地解析诊断与实施验收顺序见personality-context-gap.md。
宿主additional_developer_instructions已部分实现并验证，含纯政策恢复快照；
以managed-developer-instructions-gap.md当前顶部为准，不再沿用此前“完整链路缺失”。
C1/C2仍不能宣称全部一致。

## 2026-09-13：旧协作模式 Message 协调补齐

接续全量61920终态后的实现。固定参考 `collaboration_mode.rs` 对 Unknown 总是
发布当前片段，`WorldState::render_history_diff` 从保留的developer标签识别旧section。
Corki `changed_context_items` 现在识别独立collaboration_mode标签的
legacy.developer片段，登记临时Unknown比较状态，由后续typed模式快照覆盖；
不再把同一旧片段额外作为通用legacy.developer撤销。仅比较视图变化，不迁移/覆写旧行。

新增真实save_messages→关闭→冷Runtime场景：当前指令与旧文相同、不同、空串，
均只追加一个当前mode.plan片段；固定宿主配置再次冷开后不重复，原前缀不变。
初次3例失败（36bebc，1.38秒），修复后仍3例失败（4db233）：诊断证实额外项
来自夹具冷开重新传入default配置（06eb8c），不是剩余的通用撤销。Runtime初始化
明确将宿主settings写为未来Turn默认；故夹具改为冷开显式保持plan及相同指令，
不改生产默认覆盖规则、不删除唯一项断言。运行中的Turn恢复仍由其持久选择控制。

来源边界单测扩为两个section，用户role、前缀引用、自定义key、输入附着内容不接管。
最终完整unit/context加collaboration_mode/token_budget_notices/thread_settings_update
429通过（013989，8.23秒，退出0），4workers；Ruff/格式与diff检查通过（d487c0）。
本批新生产变更发生在15143全量通过之后；未重跑全量，不沿用旧结果宣称当前全绿。
已关闭本节旧模式入口缺口，C1/C2其它section及A–E其余差异仍独立验收。

## 2026-09-13：协作模式单 section 与模型身份快照

后续只读核对（全量集成61920运行期间未改代码/测试）：旧Message兼容入口仍将
带collaboration_mode标签的developer内容存为legacy.developer；当前专属Unknown
识别只有guidance。因此模式旧片段会走通用旧key清除，与Codex历史匹配后仅发布
当前模式片段不同。回归终态后应通过save_messages→关闭→新Runtime的真实入口
补反例，断言只追加当前模式片段、保留原前缀、再次Turn不重复；包含空指令状态，
不可把用户引用或输入附着片段纳入宿主状态。这里是源码确认的未闭合路径，
不是已经执行的故障测试，不计作修复完成，也不接入官方目录服务。

Codex `world_state/collaboration_mode.rs` 的 snapshot 包含 mode/model/instructions
哈希；非空指令在模式或模型改变时重新发出，空状态 hash 不变则跨模式/模型也静默。
`render_diff` 仅发布新的 `<collaboration_mode>` 片段，清除为成对空标签，
不生成旧模式的独立通用撤销项。此为本地上下文规则，不接入官方模型目录服务。

Corki 此前用 mode.default/mode.plan 两个比较 key，换模式新增后又撤销旧 key；
同模式同正文换模型没有快照身份变化。新增真实 Runtime 反例首先在初始片段缺少
标签处失败（cf87fa，1 failed）。现保留 durable key 兼容既有记录与消费者，
但 world_state 比较和历史投影视为同一 section；builder 的 admitted settings
捕获模型身份，snapshot_state 持久化 mode/model，正文仍作内容比较。
自定义指令保留宿主原文，不附加模板尾换行；空状态使用稳定空比较值。
模型可见模式片段采用原生标签，旧带 snapshot_content 的记录不重写。

真实测试覆盖 default→plan、同模式换模型、清空、空状态换模式/模型，
每步两 Turn、每阶段关闭后冷开，断言每次仅一片段（空→空零片段）、前缀不变及去重。
原测试的“no longer apply”断言改为精确空标签，这是参考契约修复，不是放宽断言。
完整 unit/context 与 collaboration_mode/thread_settings/context_journal/fragment_kinds
最终416通过（e3ec37，12.12秒，退出0），4workers；静态与diff检查通过。
全量unit5882通过发生在本次生产修改之前，不作为本次变更后全量通过证据。
未宣称其余 section、无类型旧模式片段识别及完整C1/C2已经验收。

## 2026-09-13：旧 Message guidance 的 Unknown 恢复已接通

补充上一批待核对项：该状态确实可达，不能仅因新 ContextItem 有 key 而标为不适用。
`SQLiteSessionRepository.save_messages` 及旧表迁移调用 `items_from_messages`，
developer 消息统一变为 `legacy.developer`；冷开 `load_items` 不会恢复专属 key。
Codex 的 `WorldState::render_history_diff` 在没有 section 快照但保留 developer
guidance 标签时使用 Unknown，替换/清除一次后发布已知快照。

真实兼容入口的冷恢复测试在修复前3失败、原有typed三例3通过（399456，1.96秒）：
替换缺专属提示，删除/空白没有 guidance 撤销项。现 `changed_context_items` 只将
独立标签包裹的 legacy.developer/developer 片段作为临时 Unknown 比较基线；
后续有 key 的追加更新覆盖它，原持久化 key、正文和历史前缀不修改。用户role、
带前缀引用、自定义key、输入附着片段不被接管。压缩仍清空当前窗口比较基线。

预算三文件与完整 unit/context 最终412通过（ec2d39，8.07秒，退出0），4workers；
Ruff check/format及定向diff检查通过。此前408通过未含新增四个来源边界断言，
不叠加计算。未运行全量或真实供应商请求。此证据关闭 guidance 的 legacy 入口缺口，
不等于所有世界状态 section 的旧历史匹配或 C1/C2 整体已经验收。

## 2026-09-13：context-window guidance 空白及冷恢复更新

固定参考 `context/world_state/context_window_guidance.rs` 的构造器将空白视为不存在，
保留非空原文；`render_diff` 在已有 guidance 时使用专属替换/撤销提示，并保留片段标签。
Corki 配置解析已过滤空白，但宿主直接构造 TokenBudgetConfig 的入口此前没有过滤。
现由 `context/token_budget.py::with_window_context` 统一过滤，真实 Runtime 连续两 Turn
验证 None、空串、空白不注入空标签，非空原文只提交一次。关联预算回归40通过
（42af09，6.23秒；原进程已结束，旧观察句柄随后不存在）。

进一步确认 `world_state._render_update` 曾对 guidance 使用通用 section 撤销提示。
新增同 Thread 冷开替换、删除、改为空白三例，修复前三例均在提示正文断言失败
（35b6ec，3 failed，1.37秒）。现使用独立 prompt 模板渲染专属提示，保留
snapshot_content 比较状态及原历史前缀；第二 Turn 不重复撤销。关联预算三文件及
world-state journal 合计53通过（ff3892，6.87秒，退出0），使用4个测试worker。
这不是官方协议或远程压缩接线，不修改 CLI。未重新运行全量回归。

C1/C2仍未整体关闭：Codex 对无类型快照、仅保留 legacy fragment 的 Unknown 状态
有专门识别/协调；本批证明的是 Corki 有 key 的 ContextItem 历史及冷恢复，不将其
等同于无类型历史识别。后续应核对该状态在 Corki 历史来源中是否可达及对应恢复路径。

## 当前实现：有效权限环境快照已接通并完成本批验收

2026-09-12：已修改生产代码，不再沿用下方“尚未实现”的历史状态。
原生 permission_context 在 requirements::apply 后调用固定参考的
environment_context.rs 渲染器，返回 version=1 的 filesystem/network 元数据；
workspace roots 来自已解析 ExecutionPermissions，而不是模型输入。
Python 校验响应版本、XML、合计30KB/9000估算token上限后接入真实 builder。
EnvironmentSnapshot v2 保留有效片段，权限独自变化会追加 user 增量；v1历史
仍可解码，但首次使用新实现会完整刷新一次，不改写原历史。
legacy 无沙箱模式使用已知 Disabled 的固定filesystem，不伪造managed权限。

原生独立离线构建成功（bb06c7，3m17s），产物及source receipt验证通过。
新产物：/tmp/corki-environment-native.7UweDj/corki-sandbox。
旧安装已保留至同目录 previous-installation，新产物经install.py验证后安装
（df2ed9）；没有删除旧二进制、修改凭据或参考仓库。
首轮75项快照/元数据单测通过（ace0c1）。初始Runtime红测现已通过并新增
同Thread冷开不重复断言；首次联合30通过/2失败（0dadd4），失败为默认配置
尚加载旧安装而拒绝workspace_roots字段，不冒充全绿。现已更新安装并完成复测。
扩大复测420通过、0失败（8e27d2，21.88秒）：完整unit/context、环境权限Runtime、
执行权限上下文、context journal/fragment kinds、bundled执行。
另增同目录冷开切换managed/disabled的真实请求delta断言，最终定向86通过
（0c9cf1，7.32秒）；不把两次重叠测试相加为新全量。
这证明原始缺口已修复、约束合并后的deny_read进入user片段、旧v1刷新、冷开
不重复/权限变化追加以及手动压缩后重建。没有重新运行14337全量。

网络边界：当前宿主管理要求入口未开放顶层network域名代理配置；因此当前可用
配置不会产生该域名片段，不能声称域名执行策略已实现。本次只从有效constraints
读取，不把profile的network enabled布尔值伪装成域名约束，也不开放未执行的授权。
更多跨模块及完整C1/C2仍开放；以下保留修复前证据。

## 修复前确认：环境快照缺少有效 filesystem/network 状态

修复前 Runtime 红测已落地：test_environment_permission_snapshot.py 的 managed/
disabled 两例使用实际本地编译器和普通 Runtime，先断言 TurnCompleted，再在
捕获的 ModelRequest 的 user/environment.primary 中检查 filesystem。两例均在
filesystem 为 None 处失败（886ab1，2 failed，0.66秒）；不是启动失败或模拟
拼接片段。测试还规定 profile/file_system 类型、受限写路径及无域名约束时不
伪造 network。ruff check/format 和 diff 检查通过；生产缺口尚未修复，不能把
旧14337全量通过当作当前新增测试全绿。

已核对 permission_context.rs：requirements::compose → ConfigRequirements →
requirements::apply 的有效 profile 当前只用于 developer 文本，没有输出对应
环境元数据。构建需使用 native/sandbox/build.py 的新输出路径生成二进制及源码
摘要receipt，再验证接线；不能只更新Python并继续使用不支持新契约的旧编译器，
也不能伪造receipt或删除现有安装。本轮尚未修改原生/Python生产代码或构建产物。

状态：部分一致，C1/C2，需实现与红绿验证，不能以 developer 权限说明代替。
参考 context/world_state/environment.rs::from_turn_context_with_environments
从 primary 环境的 permission_profile/workspace_roots 构造 FileSystemContext；
network_from_turn_context 仅在宿主配置存在 network requirements 时构造域名约束。
snapshot/render_diff 比较 filesystem/network 字符串，并在变化时重发环境片段，
角色为 user、content kind 为 environments.environment_context。
FileSystemContext::from_permission_profile 先 materialize project roots 再渲染。
这不是官方服务依赖，属于本目标的有效执行环境及上下文增量语义。

Corki context/environment.py::EnvironmentSnapshot 当前只有 cwd/shell/current_date/
timezone；ContextBuilder 也仅传这四项。PermissionContext 的独立 developer 片段
有权限文本，但不提供对应 typed 环境字段及变化比较，不能据此称角色与 delta 一致。
当前环境 Runtime/日志回归16通过、2.01秒、退出0（ed7a0b），其通过不能反证
上述缺失：该组只检查现有四字段、日期变化、冷恢复与压缩，没有 filesystem/network
的预期断言。本轮发现来自两边真实构造/序列化源码，不是测试运行失败。

下一实现路径：复用 native/sandbox/permission_context.rs 已执行 requirements::apply
得到的有效 profile，新增有界、版本化环境快照元数据；不要直接复制原始配置，也
不要为取元数据启动模型命令。Python验证元数据后贯通 builder→EnvironmentSnapshot
→render_update→普通模型请求。覆盖初始完整、仅权限变化、未变不重复、禁用/重启/
压缩重建，旧快照缺字段须明确完整刷新，原历史不改写。网络只在有效约束存在时
加入；额外权限不是模型输入能自行授予的权限。先补真实 Runtime 失败断言再修复。
本轮未改生产实现，多环境选择等其它C1/C2边界仍独立核验。

## 项目不信任与全局指令保留的源码及运行对照

固定参考的 core/agents_md.rs::load_project_instructions 先从宿主 Instructions
构造 LoadedAgentsMd，再判断 active_project.is_untrusted 并直接返回已有全局
指令；只有非 untrusted 才遍历环境读取项目规则。agents_md_manager.rs 的缓存
按 selections/trust 失效，重新加载前清空旧值。world_state/agents_md.rs 则以
快照内容判断是否发 replacement/removal notice，不能把不信任理解成删除原归档。

Corki InstructionManager.refresh 先加载宿主快照，再按 trust_level 门控项目读取；
加载后的 sources 同时区分宿主文件和项目文件，运行中缓存不按文件修改时间刷新。
本批增强 test_project_trust_runtime 的 None/trusted/untrusted × direct/CodeMode
六个场景：配置独立 host-home/AGENTS.md，确认不信任时项目正文被排除，但全局
正文和公开 instruction_sources() 的全局来源仍保留。并非仅断言“没有项目文本”。

项目 trust 全文件与 instruction_provider_lifecycle 联合 31 passed，4.72 秒，
退出 0（9cbf3d）；ruff/format 通过（e44181）。无生产修改，不宣称此小项关闭
全部 C1/C2；多环境来源、其它 typed section 及权限读取路径仍按主表验收。

## 最新 C2：项目规则创建快照与冷启动变更

重读原生 core/src/agents_md_manager.rs 全部实现及
core/src/context/world_state/agents_md.rs::render_diff：会话缓存按 selection/trust
失效，不按 mtime；同一已知状态无通知，替换/删除使用明确 AGENTS notice。
Corki InstructionManager.refresh 和 world_state.changed_context_items 的同类
路径已有实现，本批没有确认新的生产缺陷，不重复实现文件监控。

扩展 tests/integration/test_instruction_snapshots.py 为实际项目文件修改/删除
两场景：warm 两 Turn 继续使用创建快照；关闭后以同一数据库/thread 新建
Runtime，resume_pending 不采样，第三 Turn 追加替换或删除通知，旧历史前缀
逐项不变；第四 Turn 不重复追加通知。模型请求中仍保留旧规则及后续撤销事实，
不是抹掉旧正文。fixture 仅操作 tmp_path，不修改实际项目 AGENTS 或 teach.md。

与 provider 生命周期、两普通接口角色 wire、context update/world-state journal
联合 33 passed（4ebe1f，4.57 秒）；初专项 6 passed（fafe1b，终态 3a0fa3）。
静态初次发现测试字符串行长，拆分等值字符串后 src/tests ruff check/format、
src compileall、77 包依赖检查、diff 检查全部通过（e7482e）。
新场景使用实际 Runtime 的 ModelRequest，不声称新场景逐个运行两种 HTTP
序列化；两接口覆盖来自联合组中的已有独立用例。也不覆盖同一 Turn 压缩
与项目信任变化交叉、所有 typed section，完整 C2/A–F 仍未完成。

## 最新修复：内置来源显式分类及 host skills 条件插入

PromptContribution 增加可选 PromptPhase（EXTENSION/WORLD_STATE），内置 memory
声明 EXTENSION；skills、plugin inventory、realtime 声明 WORLD_STATE。未声明的
旧宿主贡献保留原插槽兼容规则；显式 phase 优先，非法类型立即失败，不按文本或 key
猜来源。该字段止于构建阶段，未新增 ContextItem/数据库/checkpoint 字段或供应商字段。

技能目录使用 HOST_SKILLS 插槽；ContextBuilder 有完整权限片段时位于权限前，
否则移到 WORLD_STATE_EXTENSIONS（普通工具目录之后、多 Agent 区之前）。这项
world-state 顺序同样用于新生成的 delta，但不对旧历史排序；initial_extension_keys
只在新窗口/压缩重建中使记忆和宿主 extension 前置。输入附件和独立消息仍隔离。
插件 inventory 标记仅明确它是当前目录状态，不声称其正文等价于原生插件使用提示。

test_context_message_groups.py 扩展成 18 例普通 Responses/Chat、权限开/关矩阵，
真实 Runtime 读取临时本地记忆和 SKILL.md，首个工具更新记忆，保留既有多 Step、
standalone、输入 skill、普通压缩、旧专有配置无效、冷恢复及完整前缀不重写断言。
新增请求检查 memory 在 skills 前、权限开时 skills→permissions→mode，关时
mode→skills，压缩后仍 memory→skills。修复前 18 failed（712c6c），首轮相关
418 passed（460d2c）。再补显式来源覆盖 legacy 插槽、工具目录相对位置、delta
原序和非法 phase 类型测试。

扩大回归初轮 1138 passed/2 failed（7e005e）：新单元用例绕过 Runtime 直接传入
未解析 AUTO 权限配置，编译器拒绝；改成明确的 legacy None 权限 fixture，不改变
生产权限解析。最终 context/prompting/memory/skills/plugins 单元及六文件集成
1140 passed、0 skipped，44.34 秒（b0017e，显式本机 sandbox compiler）。静态
dde162：ruff check/format、compileall、uv pip check、git diff --check 全通过，
1133 文件、77 包；测试进程已终止，无活动测试。

本项关闭记忆/技能来源分类及上述条件顺序缺口，不代表 C1、D4 或 A–F 整体完成。
未执行真实模型选择质量验证。以下为修复前源码追踪和诊断，保留历史措辞。

## 修复前发现：内置贡献项不能按插槽推断生命周期

继续完整读取 core/src/session/world_state.rs、context/world_state/mod.rs 和
ext/memories/src/extension.rs，并追踪 ext/skills/src/extension.rs::contribute_world_state
→ world_state_catalogs.rs::build_world_state_section → world_state.rs：

- MemoriesExtension::contribute_thread_context 返回 DeveloperPolicy，初始聚合时
  在 world_state.render_full 之前；记忆不是 world-state feature。
- 技能目录经 contribute_world_state 返回 section；host_skills 在有完整 permissions
  section 时由 WorldState::add_extension_section 插入其前，否则依正常插入序列。
  不能把该条件省略成“所有技能总在权限前”。完整 world-state 保持 IndexMap 插入序。
- Corki Runtime.create 把 MemoryContextContributor、SkillContextContributor、
  PluginContextContributor 和宿主 context_contributors 放进同一个 contributors 列表。
  当前新增 initial_extension_keys 仅根据 EXTENSIONS/TURN 插槽，错误地把本地技能目录
  前移，而 SESSION 插槽的真实记忆贡献未纳入。宿主扩展/模式红绿证据仍成立，但
  不足以证明整个来源分类正确；上一节“SESSION 等 world-state”描述不能用于真实记忆。

当前真实 ContextBuilder + MemoryContextContributor + SkillContextContributor +
freeze_context_messages 的诊断（25a3ce，退出 0）输出：

```text
initial_extension_keys: ['extensions.skills.catalog']
initial developer order: ['extensions.skills.catalog', 'memory.instructions', 'mode.default']
```

诊断只替换内存中的记忆文件读取和技能 catalog 数据源，未创建/修改用户记忆、
未访问模型或外网；这是实际贡献/构建/分组链的证据，不冒充 Runtime HTTP 验收。
参考应是 thread memory 在 world-state host skills 之前。状态：行为不一致，C1/D4，
中优先级；先前局部修复暴露的内置组合缺口，不能因测试绿灯关闭。

下一实现应显式区分 thread/turn extension 与 world-state 来源，由内置生产者声明，
不再仅凭 slot 或 content 文本推断；同时保留宿主接口兼容，独立处理 host skills
有/无完整权限片段的插入位置。新增真实内置记忆+技能+权限+模式+宿主扩展组合请求，
覆盖初始/增量/压缩/冷恢复，确认现有组和输入附件不被重排，再关闭此项。
插件 inventory 与原生 PluginsInstructionsState 是否等价仍待追踪，不能直接套用。
本轮仅更新已证实的审计差异，未修改生产代码或声称此问题已修复。

## 最新修复：初始扩展与模式排序

本轮保持参考 commit ddf04ad26789d040f9ef6a96736f76602e35a6cc（工作树无改动）。
ContextBuilder 现在记录宿主普通 contributions 中 EXTENSIONS/TURN developer
片段的 initial_extension_keys；SESSION/REALTIME 等 world-state 插槽、input_scoped、
standalone 及 step_contributors 工具目录不属于这组。初始窗口和压缩重建在
freeze_context_messages 中稳定前移这些扩展，其余片段顺序不变；增量路径完全
不使用此排序信息。没有调低 PromptSlot，也没有增加数据库或 ContextItem 字段。
排序元数据仅在 ContextSnapshot 上，decorate/replace 保留，旧历史消息组不重写。

真实普通 Responses/Chat 请求的模式/扩展顺序断言，修复前 9 failed（dd2499），
修复后消息组组合 28 passed（a4193b）。新增来源边界测试区分 memory/world-state、
宿主 extension/turn、user、standalone、输入附件与工具目录，并检查 delta 原序。
最终 context/prompting 单元及六文件集成共 437 passed、0 skipped，11.17 秒
（09e8eb，显式本机 sandbox compiler）；初始、增量、普通压缩及冷恢复的既有
消息组/前缀不重写断言仍通过。这里关闭宿主扩展/模式的具体差异，不宣称 C1 所有
来源或 A–F 全部完成；原生各扩展内部的 thread/turn 回调分层并未据此证明一一对应。
最终静态检查 3fd814：ruff check/format、compileall、uv pip check、git diff --check
全部通过（1133 文件、77 包）。上述测试与检查进程均已退出，没有遗留活动测试。

以下保留修复前证据，文中的“当前/下一修复”描述该次诊断时状态。

## 修复前确认：初始扩展与模式排序

重新完整读取原生 session/mod.rs::build_initial_context_with_world_state：先把
extensions.contribute_thread_context 的 DeveloperPolicy/DeveloperCapabilities
及 contribute_turn_context 加入 developer_sections，再遍历 world_state.render_full。
最后输出合并 developer、独立 developer、contextual user 等消息；官方插件推荐、
远程 notes bridge、guardian 产品路径不据此纳入 Corki。context_manager/updates.rs
的增量合并只合并相邻、同 role、非 standalone，不能与初始聚合规则混为一谈。

Corki ContextBuilder.build 通过 PromptSlot 排序后给出 ContextItem；当前
COLLABORATION_MODE=500，EXTENSIONS=700，freeze_context_messages(initial=True)
按现有顺序聚合 developer。真实 Runtime + 宿主普通 developer contributor +
Plan 自定义规则的诊断（5ab400）打印：

```text
[('mode.plan', 'developer', 1), ('fixture.order', 'developer', 2)]
TurnCompleted
```

因此当前模式规则在扩展正文前，而参考的 thread/turn extension developer
片段在 world-state 模式规则前。状态：行为不一致，C1，中优先级；这是同权限
内容的顺序差异，不声称所有扩展都产生安全漏洞，也不能因角色相同直接判一致。
此前角色/tombstone/组边界测试没有组合模式与扩展，绿灯不能关闭这项差异。

下一修复须明确区分 thread/turn extension 与 world-state feature contributor，
在初始/压缩后重建时保持参考顺序，并保留增量更新的原始邻接、standalone 及
已保存消息组。不能直接调低全局 EXTENSIONS 枚举，因为它也影响 user 扩展、
MCP 目录和后续 Step。验收应加入普通 Responses/Chat 的真实请求，在初始、
增量、压缩后和冷恢复分别断言扩展/模式/项目/环境/用户输入顺序及旧前缀不重写。

本轮只确认差异及所需修复边界，未用记录当前错误顺序的测试将其锁定为期望。
诊断使用临时目录并已关闭 Runtime，不修改用户项目规则或 teach.md。

既有角色/消息组/更新日志/指令快照/provider 生命周期/环境六文件初轮
36 passed、1 skipped（5ff259）；确认本机编译器存在，显式配置
CORKI_TEST_SANDBOX_COMPILER 后整组 37 passed、0 skipped，10.00 秒（10cc67）。
静态通过（496d59：1133 文件、77 包），所有进程退出 0。上述通过只保留
原有行为证据，不证明新增确认的初始扩展/模式顺序差异已修复。

## 源码基准

Codex core/src/agents_md_manager.rs::AgentsMdManager 持有会话user instructions，按
环境selection与trust level缓存project discovery，不按文件mtime自动重读；失效先
清loaded再加载。core/src/context/user_instructions.rs固定user角色及AGENTS标记。
world_state/agents_md.rs::render_diff 对相同已知snapshot不输出，对替换/删除输出
显式notice，Unknown与Absent区分。context_manager/updates.rs只合并相邻、同role、
都允许合并的fragment；standalone边界必须保留。这里的原生内部metadata不照搬到普通
供应商请求，内部类型用途与普通role/content输出要分开判断。

Corki ContextBuilder→InstructionManager.refresh→ContextWindowManager.prepare→
world_state.changed_context_items→render_context_history→普通模型适配器组成实际链路。
InstructionManager固定用户快照，project以cwd/permissions/environment/trust为key；
不是“每 Step 必须重读AGENTS文件”。源策略收紧时先清旧loaded，失败不能回退旧授权。
world_state保存比较snapshot与模型可见notice，角色变化先在旧role撤销，再输出新role；
保留原历史，不让user角色直接撤销旧developer权威。该自定义contributor角色迁移是
Corki通用扩展边界，不宣称Codex的固定user-role AGENTS本身具有动态角色配置。

## 新增跨模块证据

test_context_role_wire.py 在真实Runtime中注入普通宿主contributor：第一Step developer
正文，第二Step相同key/text改user，第三Step删除，第四Step保持删除；前三次采样调用
真实fixture工具继续循环。然后关闭并按同thread冷启动，当前contributor仍为空。

- 两种实际适配器通过MockTransport发送请求，只访问配置的fixture地址。
- Responses按developer/developer撤销/user/user撤销顺序输出；Chat Completions的
  兼容实现把developer片段映射为system，撤销仍在同一高优先级role。
- 每个模型请求都逐消息检查顺序和删除notice，而不只查看内部枚举。
- canonical保存4个对应context事实，冷恢复不重复追加、不改写旧记录，工具只执行3次。
- 首个ROLE_PROOF不因角色改变被原地覆盖；standalone边界没有在序列化时被错误合并。

新增2项通过（ef4201，0.86秒）。连同实际全局override/项目创建快照/managed deny
read、宿主provider初始化取消与来源、输入附着skill及跨Turn回忆、world-state角色/
删除/提交边界、async contributors/fragment kinds/input context扩大55通过0跳过
（bf4e9f，2.91秒）。静态ruff/1109文件格式/compileall/77包/diff通过（b1a471）。

## 尚未覆盖的范围

本轮没有新增生产功能。55项不能代表所有world-state section的typed diff都与原生一致。
环境/权限/工具目录/扩展各有独立状态和顺序，尚须按总索引收敛；本测试的固定工具不是
deferred搜索验证。Token/附件预算、压缩期间各输入边界不由本批外推。完整A–F未完成。
