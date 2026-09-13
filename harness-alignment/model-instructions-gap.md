# 模型切换指令与会话基础指令：C1/C2 未闭合链路

## 基础来源回退的文本相等门槛：已修复并验证

Codex session/world_state.rs 58–74：previous_turn_settings 优先；只有缺失时才从
Model 基础来源回退，且要求基础正文 != 当前模型指令。Corki builder 的 base_model
此前只有来源判断，未比较正文。新增 accepted_turn × same_instructions 四例，
无已接受Turn且正文相等一例误发（508a16：1 failed/3 passed，1.22秒，退出1）。
修复仅收紧基础来源回退，不抑制真实历史已知模型不同的切换；基础正文保持不变。
builder 现仅在 Model 来源且基础正文不同于当前模型指令时提供 base_model 回退。
四例转绿，覆盖/生命周期/恢复/线程设置/context 共462 passed（618b64，8.21秒，
退出0），4workers/loadfile/禁自动worker重启；Ruff及格式检查通过。测试期间未改
生产或测试。全量尚未刷新；此门槛不证明任意缺失来源的旧历史都可恢复。


## 最新增量：旧模型标记恢复已验证（全量结果为此次变更前）

Codex 基准仍为 ddf04ad26789d040f9ef6a96736f76602e35a6cc；
core/src/session/rollout_reconstruction.rs 的 TurnContext 独立恢复 previous_turn_settings，
context/world_state/model.rs 在 section 缺失时根据 previous_model 判断切换。
Corki changed_context_items 现复用 harness.previous_model 的既有解析，读取可信内部
快照中的上一模型；该身份跨压缩保留，不新增持久化记录、不重写旧历史。
旧无 model.instructions 的冷恢复 × 压缩/未压缩 × 继续 small/切回 large 四例，
此前四失败（1162c8），现关联 452 passed（2c46ea，7.57秒；d2966a 确认退出0）。
原 session30239 已不存在且无活动 pytest；未将丢失结果视为通过，本次重新验证。
追加 accepted/user/input_attached/visible/malformed/other_key 六项来源边界，
只有可信内部快照可触发切换；专项共50 passed（2df2c0，7.59秒，退出0），Ruff及
三文件格式检查通过。两批4workers/loadfile/禁止worker自动重启，小范围不启96并发；
本轮测试执行期间未修改生产或测试。无官方服务调用，也未推进CLI对齐。

下方15214通过/1跳过属于此次生产增量之前的全量证据，不代表当前全量已刷新。
下一步仍需核对完全缺少可信上一模型信息的旧历史边界，并回到A–E开放清单；
本组只证明已有旧标记的恢复，不把未知历史猜测成可恢复，也不宣称整体对齐完成。


## 当前生产版本全量回归已刷新，目标仍未闭合

非CLI unit5894通过/1文件系统跳过（ef29c0），integration9320通过/无跳过
（587b02，491.82秒，session55650退出0）。合计15214通过、1跳过；完整集成
输出integration-regression-55650.txt。两批均8workers/loadfile/禁自动worker重启，
集成运行全过程未改生产或测试。下面各轮“未刷新全量”现已有这次后续证据。
旧无model section但有Turn设置的前态，以及旧缺失信息可恢复范围仍需源码与反例
核验，不能用全量绿色代替未覆盖行为；A–E其余开放项仍按acceptance-index推进。

## 恢复账本/checkpoint基础一致性校验与全单元刷新

新增账本None/相同/空串冲突/不同正文四例。原两冲突均被放行并采样（e12d77，
2 failed/2 passed，2.12秒）。Runtime.resume_pending现于执行恢复前比较账本、
checkpoint固定Turn基础、已有request_items对应的准备前缀；忽略初始未准备的
context_instructions占位，不忽略有效空串；字段类型非法或内容冲突报ValueError，
错误不含指令正文。旧账本缺字段仍可从有效checkpoint恢复，不把缺失当作冲突。
失败不采样且保留RUNNING记录，未自动修复/重写矛盾数据。

关联恢复/覆盖/生命周期/线程设置/手动恢复/storage：254 passed（80f192，7.35秒），
4workers。随后非CLI全部unit刷新：5894 passed、1 filesystem非UTF-8文件名限制
skipped（ef29c0，22.39秒，session33006终态退出0），8workers/loadfile/禁worker
重启。运行期间未改生产/测试。此单元全量包含近期基础指令全部生产变更；集成全量
尚未刷新，不把旧9261集成结果视为当前版本。旧无model section前态仍开放。

## 首checkpoint前账本恢复：普通/手动压缩基础已固定

新增真实stream/compact在首次ainvoke前故障，终态写故障保留RUNNING账本；
非空/空串×普通/手动压缩四例原均错误使用FUTURE BASE（21237b，4 failed，
2.11秒）。此处沿既有Corki已准入Turn恢复契约，不声称原生有相同checkpoint机制。

Runtime准入读取已初始化builder的有效基础，放入initial状态并随首次TurnRecord
提交。TurnRecord/turns新增可选base_instructions，旧表加nullable列，终态upsert
不重写准入字段；无checkpoint恢复从账本回填turn_base_instructions。手动摘要入口
同样取固定值，空串不丢失。fork历史复制保留新字段，旧快照缺字段仍为None。
没有重写原始线程基础，后续新Turn仍取新宿主配置。

四例转绿；相关覆盖/模型生命周期/线程设置/手动恢复/context共474 passed
（ebf769，7.23秒）。追加storage全单元、fork与恢复共182 passed（00a088，
7.27秒），均4workers/loadfile且退出0。最后补账本字段值断言，恢复文件串行
8 passed（bfd0f9，2.05秒），Ruff/格式检查通过。未刷新全量。

仍需核对旧无字段且无checkpoint历史的兼容界限，以及账本/已有checkpoint前缀
冲突的校验；旧无model section的前态尚未闭合。不用新字段证明旧缺失信息可恢复。

## 已准备Turn冷恢复：基础前缀不被未来宿主配置替换

本轮沿 Corki 已有 ModelSettingsSnapshot/checkpoint 已准入设置契约核验；这是
Python持久执行图的恢复边界，不宣称Codex具有相同LangGraph checkpoint机制。
首次夹具错误复用了已sealed registry（59dcdd），修正为每个Runtime独立注册工具
后才获得有效反例：采样前中断恢复通过，工具完成后恢复的下一prepare却把
ADMITTED BASE替换为FUTURE BASE（7186f1，1 failed/1 passed，1.95秒）。

Graph新增turn_base_instructions状态，首次prepare固定基础；以后prepare及压缩
使用的ContextSnapshot沿用该值。旧已有request_items的checkpoint从原
context_instructions取得前缀，保留空串，不使用truthiness决定是否覆盖。
新Turn没有此状态，正常取得新宿主覆盖。两个有效反例修复，关联覆盖/模型生命周期/
thread_settings_update/manual_compaction_recovery/unit/context共468 passed
（b04132，7.82秒，退出0），4workers/loadfile。再扩非空/空串×采样前/工具后四例
专项通过（202a77，2.34秒），工具执行次数不增加、未来Turn采用FUTURE BASE。
最终Ruff通过（e23f75），测试期间未改生产或测试。

仍开放：首prepare尚未形成checkpoint时的业务Turn账本基础选择、旧无model
section历史的前态、来源快照与失败/取消交错以及本轮生产后的全量刷新。

## 旧基础来源缺失：精确文本推断已补齐

session/session.rs 684–705：显式覆盖是Custom；继承来源缺失时，只有继承文本与
当前模型指令完全相等才推断当前Model，否则仍未知。已标记Custom即使文本相同
也不能转成Model。Corki此前遇NULL来源一律保留未知，漏掉可证明的模型来源。

新增NULL/Custom×文本相同/不同四个真实Runtime场景：预置无模型section的旧基础，
冷开small后在首次采样前切换large，断言model_switch及原基础记录不变。修复前
匹配文本且NULL来源的一例漏发（6fc105，1 failed/3 passed，2.07秒）。现只在
初始化、没有显式覆盖、来源NULL且文本精确匹配candidate时将运行内来源设为当前
模型；不沿用旧model标签、不更新存储行。修复后扩大覆盖、生命周期、两fork文件
与unit/context共442 passed（44b9ae，7.89秒，退出0），4workers/loadfile。

仍需区分本例与“完全没有基础表行/没有model section但有旧Turn设置”的迁移，
后者未由本组覆盖；待恢复Turn与覆盖交错、全量刷新继续开放。

## model_instructions_file 文件入口已接通

原生依据：config/mod.rs::try_read_non_empty_file 读取UTF-8、trim、拒绝空内容或
读取错误；加载顺序为显式宿主文本 → 文件 → TOML instructions。
config_loader_tests::project_paths_resolve_relative_to_dot_codex_and_override_in_order
证实配置层文件路径相对该层目录（不能只按邻近注释误用工作目录）。

Corki layers 在合并前规范化 model_instructions_file 路径；新 base_instructions
解析模块读取并固定去空白后的内容，文件优先于 instructions，拒绝非字符串、
缺失、空文件、非UTF-8，不静默回退。宿主直接传入/replace CorkiSettings 的
base_instructions 仍可显式选择文本；Runtime 不在每Step重新读文件。

六反例原全部失败（e0637f，1.55秒）：两路径类型读到TEXT FALLBACK，四非法情形
未报错。修复后相对/绝对路径均取FILE RULES，即使构造Runtime前修改文件，普通
采样与摘要仍使用已解析值。覆盖/生命周期及全部unit/config：687 passed、1因文件
系统拒绝非UTF-8文件名而skipped（834ebd，7.04秒，退出0），4workers/loadfile，
Ruff/diff-check通过（049e75）。未刷新全量；旧未知来源、恢复Turn及配置重载交错
继续开放。文件内容错误与文件名平台限制是不同场景，前者已有明确拒绝测试。

## 显式宿主覆盖已接通：四红例转绿，来源与恢复/fork 分开处理

补读 session/session.rs 的 thread_persistence 分支：New/Cleared/Forked 的
CreateThreadParams 带基础正文/provenance；Resumed 的 ResumeThreadParams 不覆盖
原基础元数据。因此本轮不把恢复时的宿主覆盖写回旧表，原历史仍可恢复。

CorkiSettings 新增可选 base_instructions，TOML instructions 映射至该字段；严格
拒绝非字符串，空串保留。Runtime 初始化的 ContextBuilder 选显式覆盖优先于持久
基础再优先模型目录；普通请求和手动摘要共用该前缀。SQLite 基础表新增 provenance，
既有本实现两字段模型基础迁移为 model；新 Custom 单独保存，None 可表达未知。
ensure 返回正文/来源后，恢复覆盖只更新 Runtime 视图。新建保存 Custom；fork 在
原发布事务前准备显式覆盖快照，或继承源 provenance，同库/跨库均不改源。
Custom 无历史模型时不推断 model_switch；有实际历史模型时仍按模型变化渲染。

原四红例转绿（237e57 所在34项联合）。测试追加移除覆盖后再次冷开：原恢复线程
回到 CATALOG BASE，新建线程保留最初 Custom；fork同库/跨库×继承/覆盖四例确认
持久来源为 Custom 且源基础未变，无凭空切换；另四非法类型校验。
配置/上下文全单元与覆盖、生命周期、fork、线程设置、手动压缩等扩大
1109 passed、1 skipped（7ee319，8.89 秒，退出0），4 workers/loadfile。
后续 project_trust_selection 定向 36通过/1跳过，-rs确认文件系统拒绝非UTF-8
文件名（af7654）；最终 Ruff/diff-check 通过（37053b）。

仍未闭合：model_instructions_file 路径入口、旧无来源基础的推断、旧无模型 section
历史、覆盖后待恢复 Turn checkpoint/取消与fork失败交错、全量回归。当前可用入口
为宿主 CorkiSettings(base_instructions=...) 或配置 instructions；不要把所有来源
及生命周期兼容宣称为已完成。下方“未修复反例”为修复前证据。

## 当前未修复反例：显式宿主 instructions 被忽略

本轮确认 CorkiSettings 无独立基础指令覆盖字段；from_directory 忽略未知的
instructions，ContextBuilder.with_session_base 无覆盖分支。PromptStore 自定义根
只是模板来源，不是独立的、可记录 Custom 来源的会话覆盖入口。

Codex config/mod.rs 3890–3920：显式 base_instructions → model_instructions_file
→ cfg.instructions，得到 Custom 来源；session/mod.rs 701：显式配置 → 历史基础
→ 当前模型。session/session.rs 684 起区分 Custom、继承 provenance，以及旧来源
未知时仅在基础文本等于当前模型文本才推断 Model，不能把未知自定义文本归给模型。
新线程持久初始化携带正文与 provenance（session/session.rs 约 860），需继续核对
resumed/fork persistence 分支对覆盖后继承的契约，不先猜测历史应被覆盖写回。

新增 test_base_instruction_override.py：instructions TOML 非空/空串 × 新建/恢复
线程四个真实 Runtime 请求，全部错误收到 CATALOG BASE（a1c9ee，4 failed，
2.50 秒，退出 1），不是构造器报错。后续摘要断言因首请求失败尚未执行。
本轮未修改生产代码、未 skip/xfail；当前测试树存在这四个已知失败，旧绿结果不能
称作当前全绿。CLI、teach.md 未动，无官方服务调用。

实现须作为完整一批：宿主配置值与严格校验、初始化优先级、Custom/Model/未知
来源表达及旧表兼容、fork 继承、普通请求与摘要统一基础。恢复覆盖应保留旧原始
记录，不把临时宿主文本误标为旧模型来源；空串应区别 None。仅让四例转绿而不处理
来源与恢复不构成本缺口完成。待实现后扩大配置/生命周期/fork/压缩与恢复验证。

## 工具后轮内压缩：模型身份、调用结果及当前输入已补验

进一步核对原生时序：session/turn.rs 在 hooks 接受输入后 set_previous_turn_settings
为当前模型（约 305 行）；每次采样前 record_step_world_state_if_changed 重新调用
build_world_state_for_step（session/mod.rs 3406），更新 live world state。工具后
run_auto_compact 的 BeforeLastUserMessage 使用该 world state；因此即使本轮刚从
large 切到 small，此时 Absent 渲染也不应重新报告切换，不是固定比较上一完整 Turn。

新增 test_mid_turn_compaction_keeps_model_identity_and_tool_commit，large/small
两参数：初始普通 Turn 后当前模型返回工具调用和 180000 tokens usage，工具真实
执行后触发摘要并继续采样。共四请求，基础指令稳定；工具仅执行一次、摘要输入
包含调用与 COMMITTED OBSERVATION、完整存储中各一条调用/结果，最终模型请求
保留当前用户输入且无 model_switch 片段。两例通过（c290ee，2.06 秒），无生产变更。

扩大生命周期、manual_compaction、manual_compaction_recovery、thread_settings_update
与 unit/context：452 passed（08ccbf，6.88 秒，退出 0），4 workers/loadfile。
本组补足正常工具后 mid-turn，不证明取消/未知提交恢复组合全部闭合；下一优先项
为显式宿主覆盖及旧无 model section/来源历史。当前模型 section 与原生 Turn 设置
来源差异仍需核验，不能用这组正常路径替代旧历史兼容。

## 自动压缩重注入：两反例修复

Codex compact.rs::build_compaction_initial_context 通过
Session::build_initial_context_with_world_state 渲染 world state，同时保留对应基线；
不是把模型目录正文无条件复制到替换历史。Corki ContextWindow.prepare 自动压缩
原先直接复制 snapshot.items 并清空 snapshot_content，模型 section 随后的历史投影
因此再次按初始 base_model 渲染，绕过最近模型身份回退。

新增 UsageModel 驱动真实阈值压缩：large → small（报告 180000 tokens）→
下一 Turn small/large，两例均失败（147f46，2.28 秒）：small 重发，large 是空片段。
现在新 marker 后计算模型 section 差异，重注入保留 content/snapshot_content 对；
不修改其余 section 的既有重注入语义，不调用专用压缩服务。
两例验证发生 ContextCompacted、共四次模型请求（含摘要），基础指令始终不变；
small 无切换片段，large 有正确 model_switch 包装及正文。

扩大生命周期、manual_compaction、manual_compaction_recovery、thread_settings_update
及 unit/context：450 passed（e2a47d，6.53 秒，退出 0），4 workers/loadfile。
本轮自动验证覆盖 pre-turn 阈值路径，未覆盖工具后 mid-turn、取消恢复等与本地
模型指令的全部组合；显式宿主覆盖、旧无来源历史仍开放。生产变更尚未全量刷新。

## 压缩后模型身份回退：四个反例已修复

Codex context/world_state/model_tests.rs 验证 Known/Unknown/Absent 模型变化会
渲染、相同模型不渲染；session/world_state.rs 从 previous_turn_settings 提供回退。
Corki ContextWindow.prepare 将完整 append-only 历史传给 changed_context_items，
但后者遇 CompactionItem 清空 latest 后仅用初始 base_model，丢失最近模型身份。

新增真实 Runtime：large → small → 连续两次普通手动压缩 → small/large，
分别热继续/冷开，共四例。修复前 4 failed（3208f5，2.79 秒）：继续 small 多发，
切回 large 漏发。现遍历原历史时单独保存最近 model.instructions 的模型身份，
清空可见 section baseline 不清除该身份，仅在没有保留 section 时传入 render_diff
回退；不保留旧模型指令正文到窗口、不修改原记录、不增加远程接口。

四例转绿，压缩与后续普通请求基础均保持 INITIAL RULES，原历史前缀不变。
扩大生命周期、manual_compaction、manual_compaction_recovery、thread_settings_update
及完整 unit/context：448 passed（50546d，6.27 秒，退出 0），4 workers/loadfile。
本轮生产改动需后续全量刷新。当前回退来源为已持久化模型 section；旧无该 section
但有 Turn 设置的历史、未到 prepare 的失败 Turn、自动压缩与 Step 中途切换组合
仍须核对，不能把这四例外推为完整 previous_turn_settings 契约已闭合。

## 外部重复取消：正在运行的基础写入所有权已验证

test_external_cancellation_joins_base_write_before_releasing_writer 使用线程事件
将同步写入停在提交前/提交后，外部连续两次 cancel 初始化等待者。释放测试闸门前，
等待者未结束、writer 仍持有、线程未结束、无采样；闸门释放后线程结束，等待者
传播 CancelledError，writer 释放且 compiled 未发布。未关闭旧 Runtime 即冷开同线程，
以更换后的同名模型目录运行，仍使用已提交 OWNED BASE，且仅采样一次。
线程事件决定写入阶段，sleep(0) 只让出循环递送取消，不以固定耗时猜测提交进度。
提交前取消也不假定能回滚正在运行的线程；必须等待其真实提交结果。

生命周期、runtime_initialization、runtime_shutdown 合计 72 passed（82a62e，
5.48 秒，退出 0），4 workers/loadfile；Ruff/格式检查通过（84da02），无生产变更，
未运行全量。下节“外部取消仍需证据”由本组两场景补足，不扩称所有取消组合穷尽。

下一主线仍是压缩前态：本轮重读 Codex session/world_state.rs::build_world_state_for_step
与 context/world_state/model.rs::render_diff，Absent/Unknown 优先比较 previous_turn_settings
模型，才回退基础指令来源；Corki world_state._render_update 的缺省目前只取 base_model。
应通过压缩前后及冷恢复真实请求确认并修复，不重新引入远程压缩协议。

## 初始化基础写入故障：提交事实与冷恢复已验证

参考基准仍为 ddf04ad26789d040f9ef6a96736f76602e35a6cc，参考工作树无改动。
session/mod.rs 的历史基础优先于当前模型目录，是本组恢复断言的依据；不引入
同段中的官方鉴权或专属能力分支。Corki 的 _initialize 异常回滚覆盖新增基础写入。

test_base_initialization_failure_recovers_actual_commit 新增四场景：同步写入函数
在实际提交前/后抛 OSError 或 CancelledError。均验证初始化未采样，数据库基础行
与真实提交一致；在失败 Runtime 尚未显式关闭时创建同线程 Runtime 并完成 Turn，
证明初始化回滚已释放 writer。冷开使用同名模型但更换目录基础文本：未提交取新值，
已提交保留原值，最终仅一行、一次采样。未修改生产代码。

专项 12 passed（dc385b，3.99 秒）；扩大到 runtime_shutdown、thread_settings_update
及完整 unit/context 后 446 passed（ef3a43，5.50 秒，退出 0）。4 workers/loadfile，
保守控制 Runtime/SQLite 资源；Ruff 与格式检查通过（db6fc7）。没有运行全量。
本组取消由写入函数抛出，不等同于外部 task.cancel 打断仍运行的线程写入；后者的
join/重复取消边界仍需专门证据。显式宿主覆盖、旧无来源历史和压缩前态仍未关闭。

## 后续：fork基础指令继承已接通

Codex fork_from_history沿InitialHistory::Forked传递保留的session metadata，
session初始化从历史基础指令优先于当前模型取值；按用户消息截断保留前置metadata。
Corki原ForkSnapshot只含items/turns/usage，新增基础表未继承，导致目标模型的
指令覆盖原前缀。新增同库/跨库/临时目标×完整/首用户前分叉六个Runtime反例，
修复前均收到TARGET BASE而不是SOURCE BASE（fa6bf1，6 failed，1.73秒）。

现ForkSnapshot增加可选、不可变的模型/正文二元组，旧调用的缺省None兼容；
read_fork_snapshot在原有读事务读取，publish_fork在目标线程、历史、回执同一
写事务插入基础指令。跨库仍借用源存储，初始化失败重试复用原快照，不重读变化的源。
新六例转绿，源历史不变；已有跨库存储故障测试补充新增表断言：事务中失败没有
遗留基础行，提交后报错保留精确基础，后续沿原回执恢复。最终五文件41通过
（eaa754，7.48秒，退出0），4workers，未运行全量。

这关闭了本轮新增表的fork遗漏；显式宿主覆盖、旧无来源历史、压缩前态与独立
初始化写入故障仍按下方清单推进，不从fork事务证据外推所有初始化/取消窗口。

## 当前实现：基础持久化、普通采样与切换已接通，完整生命周期仍开放

本轮新增本地catalog的base_instructions字段，ModelContextInfo保留并校验严格
字符串/None、最多30000 UTF-8字节，空串有独立含义；不获取官方模型目录或提示词。
SQLite新增thread_base_instructions表，ensure_base_instructions在同一事务中
insert-or-ignore并返回既存的模型来源与基础正文，加入现有取消后join的写入所有权。
Runtime在线程身份与writer就绪后加载/固定基础；ContextBuilder浅拷贝给每个已准入
设置视图，build及base_instructions()共同读取，分别服务普通请求/checkpoint和
手动压缩入口。旧数据库建表兼容，首次写入不覆盖原历史；当前第三方存储若没有该
可选端口而又配置模型指令，会明确拒绝，不伪造持久支持。

model.instructions独立section持久化模型身份，初始静默；换模型且指令非空时
发布独立developer/model_switch.instructions片段，重复Step/Turn不追加；空目标
不发片段。原始两参数红例现均通过（f17026，1.03秒），包括普通采样、切换及
冷开保留基础指令。与设置/模式/手动压缩/恢复/关闭和完整unit/context联合
483通过（ba87c3，6.69秒，退出0），4workers；另8个配置值边界通过（714370）。
Ruff、格式及定向diff通过。关联旧手动压缩用例通过不等于模型指令的新组合已验。

本批未关闭完整差异：fork目前复制历史但尚未将新增基础指令表纳入ForkSnapshot/
发布事务；显式宿主基础指令覆盖与旧无来源历史的优先级需要补齐；压缩重建时模型
section的前态来源还需与原生previous-turn推断核对；新增表写入前/后失败及取消、
在途Step模型切换与手动压缩的模型指令组合尚须专项测试。后续应优先实现fork及
恢复来源，不能仅因这两条红例转绿就宣称生命周期完成。下方“尚未修复”是历史记录。

2026-09-13，参考 ddf04ad26789d040f9ef6a96736f76602e35a6cc。
本批源码审计，不修改生产或测试，不将旧全量通过视为此能力已实现。

## 后续：真实Runtime红例已建立（尚未修复）

`tests/integration/test_model_instruction_lifecycle.py` 从现有本地catalog解析入口
传入两个模型的base_instructions，经真实Runtime首次采样、切换、后续Turn和
冷开流程，规定基础指令保持最初值，非空目标仅追加一个独立developer切换片段，
空目标不伪造片段且数据库原前缀不变。两参数均在首个真实ModelRequest的
instructions断言失败（859077，2 failed，0.73秒）：收到固定agent/base而不是
INITIAL MODEL RULES；不是初始化/收集失败。后续切换与冷开断言尚未执行，不能
算已验证。Ruff通过（47e2be），本批只新增测试，没有把红例skip/xfail。

接线必须覆盖：builder.build返回的snapshot.instructions在graph.py直接保存为
checkpoint的context_instructions，普通ModelRequest读取该状态；独立compact_node
则调用context_builder.base_instructions()。只在window.prepare替换局部snapshot
不会改变graph保存的原snapshot；只改普通采样也不能修复独立手动压缩。
应建立共享的会话基础指令内容/来源所有权，在初始化与冷恢复时固定基础，供两条
入口读取；每次模型选择仍使用已准入的ModelSettingsSnapshot，旧Step不可被新
配置追溯改变。考虑显式覆盖、旧无来源记录、首次写入失败重试及fork来源，不能仅
缓存当前Runtime字符串来声称已经有持久恢复。

当前测试树包含上述2项已知红例；之前15143通过的全量不能描述为当前全绿。

## 两条不同的模型切换链路

Codex `core/src/session/mod.rs` 初始化基础指令的优先级是显式配置、历史
session_meta、当前模型渲染后的指令。`session/world_state.rs::build_world_state_for_step`
获取当前模型指令，结合 previous_turn_settings 或基础指令的模型来源，交给
`context/world_state/model.rs::ModelInstructionsState`。已知快照以模型身份比较；
Unknown/Absent 使用上一模型推断；模型改变且指令非空才产生片段。
`context/model_switch_instructions.rs` 指定 developer 角色、model_switch.instructions
类型及 separate message。未换模型不重发，空指令不伪造消息。

这与降低上下文窗口或 comp_hash 改变而触发压缩是不同职责。新模型的基础指令
不能直接替换会话原始前缀来冒充追加式切换；恢复时还需要原基础指令及其来源。

Corki `context/model_transition.py` 的 `model_snapshot` 仅静默保存 model/comp_hash，
`transition_target` 只决定 comp_hash_changed/model_downshift；`context/window.py`
使用它选择旧模型摘要，然后提交新窗口。它不包含模型指令，也不发布model_switch。
`config/model_context.py::parse_model_contexts` 不读取模型基础指令，
`protocol/context.py::ModelContextInfo` 没有对应字段；`context/builder.py` 每次用
agent/base组装基础指令，没有原生上述模型指令来源/会话保留链。因此不能把
已有降窗压缩、静默模型快照或协作模式刷新列为模型切换指令对齐的证据。

## 适用边界与实施顺序

本地、由用户/宿主显式提供的模型指令属于上下文构建能力，不要求官方模型目录、
官方账号或远程接口。不复制官方模型特定提示词；无模型指令时保持普通共享基础
指令路径。配置名称与序列化方案需在实施时统一设计，不能只给ModelContextInfo
增加字段而没有初始化、恢复、Step冻结和普通请求消费者。

1. 确认现有基础指令宿主覆盖入口、会话来源保存位置与冷恢复优先级；建立有界的
   通用本地模型指令契约，并兼容旧无来源会话，不从模型返回内容取得宿主权限。
2. 初始请求采用正确基础指令，保存其内容/来源；后续模型切换使用独立developer
   片段，保持旧前缀，不复用仅供压缩决策的comp_hash作为指令内容标识。
3. 接通已准入的Turn/Step模型快照，明确空指令、未改变模型、历史片段缺失、压缩
   重建与冷恢复行为。不能静默回退到官方目录或由provider标签改变路径。

验收须有实际Runtime和普通传输请求证据：首次基础指令；A→B仅一次新片段；
同模型不重发；空目标指令不伪造消息；再次冷开保持原始前缀与来源；在途旧Step
不被新宿主设置追溯修改；降窗摘要与切换片段各自正确；旧数据库缺字段兼容。
应先补能暴露完整缺口的反例，再实施；本文件不是这些测试已经运行的声明。

状态：缺失（可配置本地模型指令及对应会话生命周期链）；C1/C2仍部分一致。
默认共享指令路径继续有效，不据此说普通模型调用或降窗压缩整体不可用。
