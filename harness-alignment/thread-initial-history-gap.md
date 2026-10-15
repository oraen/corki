# 初始历史入口：clear / fork 源码复核

## 当前实现进展

跨存储读取取消/不可用及持久冷恢复已补验（本批只改测试）：真实读取工作线程
通过事件屏障暂停后取消 Runtime 初始化，取消等待线程释放，目标不创建、writer
释放、源历史不变、未新增采样；恢复读取后可重试。OSError 不可用来源同样不发布
目标。持久分支关闭、源关闭后按目标 ID 冷开，恢复无采样且完整历史相等，再输入
仅包含分支历史，不混入源后来输入。8 个专项通过（83d1d8），所有 fork 集成、
ephemeral 集成及 unit/storage 联合 195 通过、8.56 秒/退出 0（6af130）。
Ruff 与 diff 检查通过（649a2d）。这些已明确窗口不再继续列为完全未验证；
下一步回到整体 A–E 验收收敛，不无限扩充分叉测试组合。

跨存储基本入口已实现：fork_source_repository 为宿主显式借用，读取来源的
ForkSnapshot（items/turns/usage）并在独立目标原子发布；不读取或复制执行账本，
不关闭借用源。SQLite 快照读取在一个事务内且取消等待线程收尾，默认同库仍保持
单事务读/写。Runtime 缓存首次成功读取的快照供初始化重试，成功后释放引用。
基准链为 Codex thread_manager::fork_thread_from_history → snapshot → spawn，
session::record_initial_history(Forked) 重建 context/usage；ephemeral 初始化跳过
持久线程和状态库。Corki 不依赖官方协议完成此路径。
test_thread_fork_external 两个真实反例先失败于缺少参数（f5e712），实现后通过；
扩展持久/临时 × 正常/事务回滚/提交后失败共 6 个场景，源变化后禁止重读、
分支输入/usage 映射、无执行记录复制、源关闭所有权及临时无磁盘状态已通过。
全部 fork 集成 + unit/storage 联合 182 通过，8.74 秒，静态格式通过（9a1542）。
跨存储取消、不可用来源及冷开仍需专项证据；整体 A–E 未宣称完成，CLI 暂停。

以下为先前实现与审计历史，跨存储“完全缺失”判断已被上段取代。

legacy fallback 已实施：没有对应 Turn 行、且最后原始用户消息之后没有中断标记
时，默认 fork 补标记，越界 cut 回到最后用户位置；不创建原历史没有的 TurnRecord。
已有显式 Turn 状态继续优先，retained 用户副本不计作新边界。原默认/越界两反例
转绿，基础文件 20 通过（156b2b）；新增对该已中断 legacy 分支再次越界分叉，
保留正文与单个中断标记、不虚构 Turn 行。联合全部 fork 专项与 storage 单测
8 worker/loadfile/禁重启 176 通过/8.58 秒/退出 0（5da0ac），Ruff/diff 通过
（b829a6）。本批改生产与测试，不是实际旧版本二进制迁移测试；来源 fixture 使用
当前仓库表示缺生命周期的旧历史。legacy 此具体分叉边界已不再完全缺失。

legacy 初始历史缺口已复现：Codex thread_manager.rs::snapshot_turn_state 在没有
显式生命周期时，按最后用户消息之后是否有终态判断 mid-turn。Corki 只查 turns
表，导致默认 fork 不补中断标记、越界截断保留未完成后缀。真实 Runtime 源仓库
无 Turn 行但有用户/未知工具历史的两个反例失败（805ff4），before=0 对照通过。
拟用最后原始用户消息作为 fallback 边界；只引用既有 item.turn_id，不伪造历史
TurnRecord，不重放工具，不覆盖显式终态，也不把压缩 retained 副本当作新输入。

fork 记忆 claim/source-version 专项已补：test_thread_fork_memory 从公开 Runtime
源 Turn 建历史，先领取父记忆任务并保持租约，再公开 fork。子线程起初无复制的
memory_jobs，独立仓库可领取子任务，token 不同；父 token 即便替换 thread_id 也
不能完成子任务。完成双方后，同版本跨仓库不再领取；子线程实际新 Turn 后仅子
版本推进并重新领取，旧完成回调拒绝，源完整历史不变。继承正文按 copied 路径保留，
内部 item 身份独立；不自创跨线程前缀删除策略。单例通过（34029c），联合全部
当前 fork 专项与 memory extraction_claims/repository 两文件共 48 通过/8.00 秒/
退出 0（bca7f6），8 worker/loadfile/禁重启；Ruff 通过（8152d4）。本批仅测试。
这证明分叉与实际 claim/完成存储链交接，不冒充本例运行了记忆模型提取和 Git 发布；
这些已有 D2/D3 专项证据仍独立保留。该明确窗口不再列为新缺口反复扩充。

计划历史专项已补：真实 UpdatePlanTool 生成源计划与状态 Observation，fork 后模型
看到旧调用参数、state_update 不变但 call_id 独立；新更新仅发布本次 PlanUpdated，
不冒充重做继承计划。冷开看见源/分支两次计划历史，不重新发布事件，源完整快照不变。
test_thread_fork_plan 1 通过（5265c5），联合 plan_contract、fork discovery/basic/
usage/compaction 六文件 8 worker/loadfile/禁重启共 39 通过/7.82 秒/退出 0
（100b30），Ruff 通过（57ca6f）。本批仅测试，未引入额外计划状态存储；不等于
所有嵌套 Code Mode、压缩后的计划场景或 UI 事件呈现均已验收。

记忆审计纠偏：不能先把“继承前缀应从记忆提取中剔除”当成 Codex 契约。
当前 pinned Codex memories/write/src/phase1.rs::process（约291行）读取 rollout，
serialize_filtered_rollout_response_items 保留经过内容过滤的 ResponseItem，
排除 SessionMeta/Compacted/TokenUsage 等非正文记录，没有按 fork 来源截掉 copied
前缀。state/src/runtime/memories.rs::claim_stage1_jobs_for_startup 按 source、
memory_mode、年龄/空闲及当前线程过滤，再按线程 ID/source 更新时间检查更新和
claim，不是跨线程内容哈希去重。Corki extraction.py 也按线程 claim 并读取该线程
原始历史；transcript.py 排除压缩保留副本，不等同于删除 fork copied 前缀。
此前笼统“继承历史记忆去重待修复”应改成来源/版本/claim 行为待验证，不能据此
擅自丢弃 copied fork 的记忆输入。下一步验证独立分支 claim、不复制父任务 owner/
watermark，以及相同分支版本不重复提取；跨线程前缀去重不是已确认必须新增功能。

fork 工具发现专项已验证：test_thread_fork_discovery 用实际普通 tool_search→
定义加载→工具执行→Observation 建源，关闭后 fork。目录未变时首请求加载已有
定义并执行新调用；描述变更时投影旧搜索结果为空、重新搜索才加载；移除时不曝光
旧工具且无新副作用。冷开沿用分支重新加载的定义，各阶段采样/副作用次数与调用
身份独立，源 DisplayHistory 不变，不重做源工具。三例通过（eef6df），联合 deferred
search、schema identity、fork/compaction/usage 六文件 8 worker/loadfile/禁重启：
41 通过/8.34 秒/退出 0（00c0a2）；Ruff 通过（df86ea）。本批仅测试。
证据支持当前普通函数兼容路径，不代表真实模型搜索质量或原生专属搜索协议。

计划状态审计补充：Codex core/src/tools/handlers/plan.rs::handle_call 解析
UpdatePlanArgs、发布 PlanUpdate，返回 Plan updated；并不在 handler 中存储独立
计划管理器。Forked 通过 session/mod.rs::apply_rollout_reconstruction 重建历史。
Corki 新 Turn 的 plan=() 不能单独证明分叉缺少长期工作计划，禁止因此自创不符基准
的永久计划存储。应继续验证调用参数、Observation.state_update 和历史事件的传承，
以及当前 Turn 更新行为；本轮没有宣称该计划链已验收。

继承 usage 已实现：新 inherited_context_usage 表存储预算事实（不是模型执行提交），
在 fork 同一事务内复制源继承事实与源本地采样事实，按保留 anchor 筛选并映射
anchor/sample 身份。保留历史序列，使分叉再截断能选择较早用量；旧数据库自动建
新表，但不会凭空回填旧 fork 的来源用量。无 anchor 的完整快照未知事实仍保留为
未知，不能伪造有效边界。load_context_usage 在一致性读事务里优先本地最新采样，
仅没有本地采样才读最新继承事实；新响应无 usage 不会复活旧继承值。

test_thread_fork_usage 首先复现完整/截断分叉无 usage 两失败、空前缀对照一通过
（9def54）；修复后 3 通过（979a30）。加入实际冷 Runtime 的自动压缩消费：源两次
采样报告 10010/20010 tokens，冷分叉自动阈值 15000，首次新输入先普通摘要再采样，
模型请求包含新摘要；没有复制 model_steps、没有重复源采样。还验冷开事实相同、
anchor 在子历史中、sample 身份独立、二次分叉截断取首个事实、新未知响应覆盖。
扩大用例 3 通过（c80842）；联合所有 storage/context 单测与 fork/compaction 相关
集成共 566 通过/8.93 秒/退出 0（f6dd72），8 worker/loadfile/禁用重启。修改文件
Ruff、格式与 diff 通过（68c723、934b5a）。本批新增存储 schema 与实际读取链路。
这不代表完整所有 fork 状态均完成；工具发现/plan、继承历史记忆去重、legacy 与
跨仓库等缺口继续保留，最新全量核心回归仍早于本批生产改动。

真实普通模型摘要链路已补验：test_thread_fork_compaction 通过公开 stream/compact
生成一轮或连续两轮压缩，关闭源后分叉，检查原始条数、类型、独立身份、through/
retained 引用、实际 Context 的快照与分组映射，以及 active_history 的最新摘要。
分支普通采样保留用户输入；分支再次 compact，关闭冷开后沿公开 stream 使用新摘要，
摘要次数与普通采样次数精确，源完整 DisplayHistory 不变。两个初始场景通过
（5d853d），扩大至分支再压缩与 Context 断言后，联合 fork、local_compaction_inputs、
compact-start、全部 storage 单测 8 worker/loadfile/禁用重启：201 通过/7.85 秒/
退出 0（e4c769）；Ruff 通过（db0dc8）。本批只补测试，不是实际网络模型质量验证。
不再将这一、二轮手动压缩→fork→分支再压缩→冷开链路列作未验证；自动压缩、
截断与特殊内容仍按实际未覆盖范围保留，工具发现/plan 与记忆去重未由此关闭。

下一项已确认 usage 缺口：Codex record_initial_history(Forked) 明确恢复历史 token
usage；Corki _load_context_usage 只读 model_steps 最新行，而 copied fork 刻意不复制
该执行提交表，因此在首次新采样前得不到源 usage。context/window.py 的预算/压缩
路径实际调用该方法，不能仅视为 CLI 数字差异。后续应持久化独立的继承 usage 事实，
映射 anchor/sample 身份并尊重截断/压缩边界；不通过复制源采样账本恢复 usage。

截断修复已落地：按 Codex 的实际用户位置截断，不再删除同 Turn 的更早输入；
部分 Turn 不继承被删除的 final_answer/error 或成功终态，只保留截断前用户文本。
首用户之前的初始上下文继续保留；此前 before=0 三个夹具要求全部 Turn completed
与该契约冲突，已改成精确 cancelled/空 user_input/空 final_answer 断言，而非放宽。
修复后基础关联 287 通过（7802fc）。

随后发现本批初版的截断 Turn 判断会把后缀压缩保留副本的旧 turn_id 也当作未完成：
新增 ONE/TWO/摘要/保留 ONE 场景，before=1 应保留已完成 ONE，实际误标 cancelled，
反例失败（0c251f）。改为只检查实际切点所属 Turn 是否有继承前缀，不从全部后缀
引用推断取消。该存储夹具通过公开 Runtime 分叉消费，不采样；不冒充实际摘要生成。
最终与 fork、startup/clear 恢复、compact-start、writer、全部 storage 单测联合
8 worker/loadfile/禁用重启：288 通过/13.95 秒/退出 0（6a9583）；修改文件 Ruff
通过（7695cb）。同 Turn 反例与压缩引用反例均已转绿。
这批没有关闭完整压缩流程、工具发现/plan、usage 或长期记忆来源去重待办。

新确认的截断差异：Codex `core/src/thread_rollout_truncation.rs::
truncate_rollout_before_nth_user_message_from_start` 严格在用户消息位置截断。
Corki publish_fork 的同 Turn 向前回退会连同前面的用户输入/回答一起删除；新增
同 Turn FIRST→EARLIER→STEERED→LAST 反例在 before=1 时实际空历史，预期保留
前两条，已失败（66a61a）。修复应去掉整 Turn 回退，同时不把被截断 Turn 的源
final_answer/成功终态带入子线程，子线程也不接管该 Turn 的执行义务。

fork 基本实现已接入真实 Runtime：create/acreate 增加 fork_from_thread_id 与可选
fork_before_user_message，初始化 writer/锁内交给 SessionRepository.fork_thread。
SQLite 的新 thread_forks 表随原 schema 初始化兼容创建；单个 BEGIN IMMEDIATE
读源快照并发布目标线程、继承 Turn/items 与不可变请求回执。源 ID 作为来源记录保留，
不建立会限制源删除的外键；目标删除级联回执。相同 Runtime 提交后重试复用回执，
不会再次读取变化中的源历史。已有目标不接受新 fork，clear/startup 与 fork 互斥。

`storage/forks.py` 重映射 Item/Turn/Step/Call 身份与用户保留、上下文来源/分组、
压缩 through 引用；不复制执行账本。默认保留已提交快照并将活动源 Turn 在子历史
中标为 cancelled、附中断消息；索引截断在原始用户消息之前，越界时排除活动后缀。
截断不发布半个 compaction replacement，原全局身份冲突校验没有放宽。
此处“活动”当前依据显式持久 Turn；无 Turn 元数据的 legacy 源仍需补 Codex 的
合成历史判断。usage/work-state 以及记忆提取的继承来源去重也尚未据此关闭。

原四个缺接口反例已转绿（c4db28）。加入事务内追加后故障/提交后故障重试，
四边界共 12 通过（8c6ad3）；活动源未知工具结果三场景验证不采样/不重做源操作，
默认历史工具对补 aborted、截断省略活动后缀，源完整快照不变：全文件 15 通过
（03afe9）。与启动、clear 冷恢复、压缩启动、writer 及全部 storage 单测联合
8 worker/loadfile/禁用重启：286 通过/13.84 秒/退出 0（4cab5c）。修改文件 Ruff、
格式与 diff 检查通过（db6be0、0ec317）。没有全量核心回归刷新或真实服务调用。

后续优先：压缩与 Context 引用映射的模型可见验证、工具发现与计划状态、usage、
继承历史长期记忆去重；来源不存在/目标碰撞、取消发布、legacy 历史及分叉再分叉。
内置 Volatile 继承存储方法，但新 ephemeral Runtime 默认独立仓库，跨仓库分叉尚未
实现，不能宣称支持持久源到 ephemeral 分叉。自定义仓库需要实现新增 fork_thread
契约。基本 copied fork 已不再完全缺失，以上剩余项仍使其为“部分一致”。

fork 首批 Runtime 反例已落地：`tests/integration/test_thread_fork.py` 用公开 stream
建立 TWO-Turn 源并关闭，要求通过 acreate(fork_from_thread_id=...,
fork_before_user_message=...) 创建独立分叉；默认全历史、0/1 截断和越界四场景。
后续断言覆盖不采样源历史、独立 Item/Turn 身份、新线程模型可见前缀、独立追加、
冷恢复不重复采样/不丢分支历史、源完整 DisplayHistory 不变。
当前四例均失败于公开 fork 参数缺失（d1e5e4，4 failed/1.26 秒/退出 1），后续
断言尚未运行到，不是通过证据；未使用 skip/xfail。生产尚未实现 fork。
本轮已完成反例编写；下一批必须实现接口和原子发布，不再重复证明同一入口缺失。

clear 持久计划冷恢复已专门补验：`test_start_hook_recovery` 加入 clear 的 planned /
unknown / completed / receipt 四窗口，交叉停止/继续与配置保留/撤销，共 16 例。
首次通过公开 acreate 的 session_start_source=clear 建立来源，保存计划后注入
graph 提交故障，关闭 warm，再用未指定来源的冷 Runtime 公开 resume_pending。
检查原 payload/source 与完整计划不变、未 claim 的命令只有授权有效才执行、
unknown 不重做、已完成事实复用、输入身份/context/采样次数与唯一恢复终态。
16 通过（1f9800）；与首输入、compact-start、writer、volatile 五文件联合
8 worker/loadfile/禁止重启：126 通过/13.13 秒/退出 0（7af89e）。静态检查通过
（e5699a）。本批只改测试，不是 OS 强杀验证；不再把这四个 clear 窗口列为待办。

clear 已接入 `LangGraphRuntime.create/acreate`：新增可选 `session_start_source`
（startup/clear），省略仍自动创建或恢复。显式来源要求新线程；在初始化锁与 writer
内检查已有身份，拒绝覆盖已有线程。首次归类保持到同 Runtime 创建重试，已提交
Hook 计划继续沿原恢复链路处理。没有删除历史、清理长期记忆、增加 CLI 或模型协议。

新增 clear 首输入停止/继续反例先因入口缺失两例失败（832b1b），实施后启动文件
36 通过（44650c）。进一步补 clear matcher、真实 SEED 旧线程隔离、已有身份拒绝
与 writer 释放、完成后冷恢复沿用新线程历史但不重跑 clear；clear 六例通过
（b49ea2），包含创建提交前/后故障。五文件联合 8 worker/loadfile、禁止 worker
重启：110 通过/11.59 秒/退出 0（548b80）；包括启动计划恢复、压缩启动、线程
writer 和 volatile。修改文件 Ruff 与 diff 检查通过（f89d5f）。全部为离线测试。

此前批次完成 clear 的基本入口及已列窗口；其持久计划执行中冷恢复已由上方新增
专项覆盖，不再只用原 startup 的测试推断。fork 仍缺失，下面是实施前审计与后续
完整门槛，不能将基本 clear 实现冒称初始历史全部完成。

2026-09-13；Codex `ddf04ad26789d040f9ef6a96736f76602e35a6cc`，本轮确认参考仓库无未提交修改。
范围 A1/A4/A7、C6/C8、D1。CLI 对齐仍暂停；以下是核心生命周期缺口，不是界面工作。

## Codex 的实际契约

- clear：`app-server/src/request_processors/thread_processor.rs` 的 thread/start 将宿主
  `ThreadStartSource::Clear` 映射为 `InitialHistory::Cleared`，交给
  `ThreadManager::start_thread`。它不是在已有线程内删除历史的操作。
  `core/src/session/session.rs` 创建新身份并将 Cleared 映射为 SessionStart 的 clear；
  `core/src/session/mod.rs::record_initial_history` 对 New/Cleared 都不导入旧消息，
  初始上下文延至首个实际 Turn。无需官方模型协议或账户服务才能具有这些行为。
- fork：`core/src/thread_manager.rs::fork_thread` 从本地 thread store 读取源历史，
  经 `fork_thread_from_history` → `fork_thread_with_initial_history` →
  `fork_history_from_snapshot` → `spawn_thread`。新线程有独立 ID，保存直接来源身份，
  copied 路径持久化所继承的历史；referenced 路径是另一种存储实现，不是唯一方案。
- 快照不是无条件复制全部消息：`TruncateBeforeNthUserMessage` 在指定用户消息前截断；
  越界且源仍处于 Turn 内时，舍弃该未完成 Turn 的后缀。`Interrupted` 保留已有快照，
  源处于 Turn 内时补持久中断边界，不假造旧历史没有的 Turn ID。
- `session/session.rs` 将 New/Forked 归为 startup，Resumed 归为 resume；非根来源还要
  经过 SubagentStart 的独立分流。fork 不等于接管源线程待执行的副作用。

源码测试映射（本轮仅阅读定位，未执行 Rust 测试）：

- `core/tests/suite/fork_thread.rs::fork_thread_twice_drops_to_first_message`；
- `fork_thread_from_history_does_not_require_source_rollout_path`；
- `copied_paginated_fork_persists_inherited_history`；
- `core/src/thread_manager_tests.rs::interrupted_fork_snapshot_appends_interrupt_boundary`；
- `interrupted_fork_snapshot_does_not_synthesize_turn_id_for_legacy_history`；
- `interrupted_fork_snapshot_uses_persisted_mid_turn_history_without_live_source`。

## Corki 当前状态与影响

追踪 `core/runtime.py::AgentRuntime`、`LangGraphRuntime.create/acreate/__init__`、
`_ensure_thread` 与 `sessions/repository.py::SessionRepository`：目前公开构造只接受
线程身份、来源等参数，没有初始历史/clear/fork 请求，也没有历史快照复制事务契约。
`_ensure_thread` 根据存储存在性一次性选择 startup/resume，不能表达 clear。
`core/start_hooks.py::run` 支持已有来源的持久计划恢复，但不会创造缺失的会话入口。

全 src 的 fork 命中是 MCPConnection 的连接视图复制，不是会话历史分叉。
`ThreadSpawnSource` 是父子来源数据与 Hook 路由，不提供根线程分叉；
`context/user_instructions.py` 提到 root fork 的说明也不能证明实际入口已实现。
`reset_memory` 明确保留会话，只清理生成记忆，不能借用作 clear。

结论：两个入口均为**缺失**，不是“不适用”；既有显式新 ID 修复仅解决 startup/resume
误分类，不能关闭这两个缺口。直接向新线程 append 历史还会被当前存在性判定当成 resume，
所以后续分叉不能只在外部拼接存储方法或设置一个 Hook 字符串。

## 实现顺序与验收门槛

1. 先补宿主可调用的空新会话启动来源契约，默认兼容既有自动 startup/resume。
   clear 必须新身份、不删除或修改原历史；拒绝将已有线程冒充新 clear。
   贯通创建、首次准入、可信 matcher 与持久 Hook 计划，而不是仅新增枚举。
   验收新旧线程隔离、clear matcher、停止/继续、后续 Turn 不重触发、冷恢复及创建失败。
2. 再补核心分叉：定义快照选择与来源关系，在一致性快照上生成独立历史，持久发布，
   接入 Runtime 的初始历史状态。不复制源工具/Hook 的未完成执行义务，也不能重放未知
   副作用。处理压缩后的有效历史、原始记录、上下文来源与稳定工具关系。
3. 将上述 Codex 场景映射为公开 Runtime 测试，覆盖活动/终态源、冷源、截断边界、
   子线程后续写入隔离、失败回滚和冷恢复。实现不要求照搬 Rust 存储格式。

本轮完成差异确认，尚未实现这两个入口，也未新增/运行行为测试。不以此文档代替实现。
下一批先完成 clear 的核心闭环，不再重复扩充已通过的 startup/resume 来源矩阵。

以上末两句是首次审计时的状态，clear 后续实现与验证以上方当前实现节为准。

## fork 实施前存储约束

`storage/sqlite.py::_append_items_in_connection` 按全库 item.id 查重，已有同 ID 但
thread_id 不同会抛 StorageIntegrityError；turns.id 也为全库主键。不能把源 items
原样 append 到新线程，也不能复制 turns 行或执行账本来冒充分叉。
`_load_display_snapshot` 在一个 BEGIN 读事务中读取 items 与 turns，可复用一致性
快照思想，但 DisplayTurn 未包含全部恢复/工作状态，不能把该展示 DTO 直接当作
完整的分叉导出契约。`context/history.py::active_history` 重建压缩替代历史并补
缺失工具结果；截断必须保持 CompactionItem 的 replacement_item_count 完整性。
后续先确定新线程内 Item/Turn 及 retained_from_id 等引用的映射与来源记录，检查
持久调用身份和工具关系，再实现原子发布；禁止放宽现有全局碰撞校验来通过测试。

引用映射须覆盖 `ContextItem.source_input_id/message_group_id`、
`UserMessageItem.retained_from_id`、`CompactionItem.through_item_id`，不能只换 id。
源工具 call/result 必须维持配对，历史 Observation 的工具发现/plan 状态须保留，
但 model_steps、tool_executions、hook_batches/executions、pending checkpoint 不得
作为子线程待执行义务复制。Codex `session/mod.rs::record_initial_history(Forked)`
先 apply_rollout_reconstruction，恢复 token usage，再将继承历史与子配置一起持久化；
所以只有消息复制而不审查重建/配置覆盖/usage 的实现仍是部分一致。
