# D2/D3：记忆来源与启动边界复核

## D3 合并选择、保留期与发布基线收敛

当前源码对照完成：state/runtime/memories.rs 的 get_phase2_input_selection 在
非空、使用时间或来源时间窗口内筛选 enabled 来源，按使用计数/时间/版本/id
降序取 Top-N，再按 id 升序提供稳定输入；Corki sqlite._load_consolidation_inputs
用 JOIN 在同一查询中完成资格过滤与对应排序，不照搬原生分页实现。
原生 prune_stage1_outputs_for_retention 与 Corki prune 均只清理非 selected
的过期记录，截止点相等保留，清理不删除成功提取水位。原生 phase2 成功事务
及 Corki complete_consolidation 均只把实际消费的精确来源版本标为 selected；
提取更新不能被旧合并任务误标为已消费。

memories/write/phase2.rs 的完成顺序为 worker 终态、关闭、验证、owner 确认、
Git 基线重置、数据库成功标记。Corki pipeline._run_owned_consolidation 先 join
work/heartbeat，再由 repository 的 owner 检查及 publish 回调发布基线和完成水位。
数据库与文件/Git 不构成跨系统事务；后半段失败不能虚报撤销前半段文件副作用。

本轮复核现有测试而非新增生产行为：

- retention：资格在 Top-N 前过滤、排序与稳定序列、source/last-use 而非生成时间、
  截止边界、bounded prune、selected 保护、成功水位保留与新提取保留旧基线。
- consolidation_policy/ownership：冷却与重试、单 owner、精确版本、合并期间输入
  水位推进不被旧 completed_watermark 吞掉、失去 token 后不能提交。
- expiry_ownership/owned_completion：过期清理的 owner、取消等待和事务失败边界。
- change_evidence/git_baseline/agent_runtime：真实子 Runtime 文件编辑、来源删除/修改、
  失去 owner、摘要/skill 写失败、Git 基线或数据库成功失败，以及后续冷恢复修复。

六文件 49 passed/19.58 秒（c26036）；agent_runtime 与 ownership 两文件
30 passed/14.41 秒（10fdb7），均退出 0、无跳过。本批只有审计改动，不把上述
分组数或上批 45 项相加冒称单次完整回归。

D3 的 claim/lease/owner/version/watermark/去重/失效在普通模型记忆路径下已核验，
结合下方 stage-one 与 memory-reset-handoff.md 的 reset 证据，不再保留笼统
“合并选择/保留期/发布基线待收敛”。限制仍为已声明的跨文件非原子发布、外部
不协作写入与模型事实质量；后台生命周期归 D6/E7，污染入口归 D5，不随 D3 关闭。

## D3 stage-one 版本与租约边界收敛

本批重读 state/src/runtime/memories.rs::try_claim_stage1_job 与
mark_stage1_job_succeeded：claim 检查成功水位、容量、有效租约、重试时间和版本
推进；completion 检查 running 与 ownership_token，不单独检查到期时间。
Corki extraction.claim_extraction_jobs 在 BEGIN IMMEDIATE 中建立来源快照及 token，
sqlite._complete_extraction 以同样的 token 检查提交，并保留 claim 时版本的成功水位。
新输入不抢占有效租约；旧快照完成不能把更新后的来源误标为已经处理。

新增真实 SQLite 两例：来源在租约期间更新，第二 repository 不可抢占；时间推进到
恰好到期。一例未接管，旧 owner 仍可完成旧版本，然后新版本可 claim；另一例先
接管，旧 owner 提交被拒绝。最终新 owner 成功、迟到旧提交不能覆盖输出，冷开后
同版本不再 claim，合并输入为新版。此为既有行为补证，未修改生产代码。

当前 extraction_claims、consolidation_policy、repository、ownership 及实际过期
清理集成五文件 45 passed，3.93 秒，退出 0（764bf3）；ruff/format/diff 通过
（db364b）。stage-one 已有证据涵盖时间窗口、全局容量、版本推进与重试恢复、
成功水位、空结果撤回、到期接管及 token 拒绝、旧库迁移。不要再笼统将这些列为
“来源版本/过期失效未实现”。D3 其余合并选择/保留期/发布基线仍需逐项收敛；
本批未将 repository 测试描述为真实模型提取质量或跨文件原子发布保证。

## 当前验收收敛：启动到发布不再列为无证据

本次重新追踪 Codex `phase1.rs::run/claim_startup_jobs`、
`phase2.rs::run` 与 completion handler，以及 Corki
`pipeline.py::_run_once/_consolidation_work/_run_owned_consolidation` 和
`sqlite.py::_complete_consolidation`。阶段链为来源 claim → 并行提取及持久化 →
全局合并 claim → 同步输入 → Git 差异/有效产物判断 → 内部 Agent → 关闭确认 →
产物验证与 owner 确认 → 成功基线与数据库完成标记。参考的官方账户检查仍排除。

现有实现与测试已覆盖以下可观察行为，本次没有新增生产逻辑：

| D2 子项 | 真实入口及当前证据 |
| --- | --- |
| 启动与来源 | session_source/startup_lifecycle/thread_memory_mode：新 Turn、steer、resume、compact、typed non-root、历史来源与 mode |
| 提取与存储 | pipeline_runtime/extraction_claims/owned_completion：完成、失败、取消来源；版本、空输出、持久化和取消写入所有权 |
| 合并与发布 | memory_agent_runtime：真实子 Runtime 读源、未知工具纠正、apply_patch、执行验证、direct/streaming/Code Mode、后续摘要召回 |
| 变更与去重 | no_output/git_baseline/change_evidence：空输出撤回、来源删除/修改、Git 基线、冷打开 no-diff 不再采样 |
| 失败不伪成功 | change_evidence/agent_runtime：模型、失去 owner、摘要/skill 写入、基线及数据库完成失败；保留实际副作用，后续修复 |

当前两组测试均终止成功：11 文件 123 passed（c69a58，33.76 秒），
3 文件 31 passed（34f718，30.15 秒），无跳过。第二组为
test_memory_agent_runtime、test_memory_git_baseline、test_memory_change_evidence；
第一组还包含 reset_inflight/reset_runtime/expiry_ownership/claim_handoff。
不把分组结果描述为单次完整 memory 回归。

D2 的“完整发布全链及启动组合待收敛”旧状态由上述明确证据替代。
限制仍保留：文件与 SQLite/Git 不是跨系统原子事务，失败可留下已写文件；
测试验证失败状态、基线/水位和后续修复，而不是虚报回滚。真实模型提取质量、
不协作外部写入以及强杀后逃逸进程不由这些测试保证。D3 的执行期租约保护
见 memory-reset-handoff.md；这不是把 D3/D6 或整个 A–F 一并判定完成。
下文运行中进程描述均为历史快照，不能用来推断当前存在活动测试。

参考 Codex commit `ddf04ad26789d040f9ef6a96736f76602e35a6cc`。
本批只读生产源码，未改认证、模型协议或记忆实现。

## 真实入口与筛选

Codex `app-server/src/request_processors/turn_processor.rs` 在
`start_or_steer_turn` 返回 Started、存在输入且主环境已配置时调用
`memories/write/src/start.rs::start_memories_startup_task`；Steered 不调用。
因此“startup”不是仅进程启动一次。后者排除 ephemeral、MemoryTool 关闭和
typed non-root source，准备目录、扩展与清理后运行 phase1/phase2。
其中 AuthManager 官方配额 guard 为用户明确排除项，不移植。

Corki `core/runtime.py` 在新 normal Turn、非 checkpoint resume、非 typed
non-root host 时调用 `memory/pipeline.py::start`；后者检查启用/后台开关及关闭
状态，拥有每次任务，数据库 claim 仲裁重叠。ephemeral 不创建该记忆服务。
本批确认新 Turn 启动语义；所有空输入、steer、恢复组合仍应结合启动生命周期专项
验收，不能单凭上述 if 判定整项已关闭。

Codex `phase1.rs::claim_startup_jobs` 使用 interactive 来源的 Display 字符串，
`state/src/runtime/memories.rs::claim_stage1_jobs_for_startup` 筛选 active、
memory enabled、非当前 Thread、毫秒时间窗口，先限制扫描再探测旧记忆。
Corki `memory/extraction.py` 对应筛选并在同一事务中读取历史、建立 owner claim。
SQL 中 atlas/chatgpt 是历史元数据标签，不是官方服务入口；从启动参数构造的同名
Custom source 以 JSON 存储，并不因此获得可提取身份。

必须区分当前 host 能否启动任务和历史 source 能否被提取：root exec/MCP 可启动，
其历史不进入 interactive 提取；typed internal/subagent 不启动，类似名称的 Custom
字符串不能冒充 typed non-root。重新打开 Thread 保留历史来源，但按当前 host 判断
后台启动资格。

## 当前实证

执行：`PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring .venv/bin/pytest
-o addopts='' -q --tb=short tests/integration/test_memory_session_source.py
tests/integration/test_thread_memory_mode.py tests/unit/memory/test_extraction_claims.py`。
终态 `8b9492`：50 passed，10.28 秒，无跳过。

- 真实 Runtime 来源矩阵、typed 子代理禁启动、root 非交互来源可启动、冷打开来源不改写。
- 目录失败只记录后台失败，不建立 claim；关闭及重复取消等待真实目录线程后才关存储。
- memory_mode 不进入模型输入、不改写历史/版本；禁用阻止提取与合并选择，启用恢复选择。
- claim 专项覆盖时间边界、跨 repository 容量、三次重试预算、新版本重置、成功水位
  冷打开去重、无输出清理并触发合并、先扫描上限再探测、旧数据库迁移。

这是 Harness 确定性测试，不证明真实模型记忆质量。完整 D2 发布链、D3 全部失效路径、
D5 污染入口与 D6 所有关闭组合仍需总清单收敛，不以本批 50 项替代。
原全量 session 34854 poll `fd4eb5` 仍活动，约 41% 测试计数，并有 skip；尚无最终
退出与 skip 原因汇总。该进程采集早于后续 CLI 改动，不作为最新快照完整验收。

## 启动连续状态补充

继续读取原生 turn_processor：判定是输入数组非空，而非文本 trim 非空；不能将
Corki 的空字符串直接等同于原生空输入数组。未据此添加无依据的拒绝或启停分支。

读取并执行 `test_memory_startup_lifecycle.py` 与 `test_ephemeral_runtime.py`，
初次 27 passed（623318）。前者通过真实 Runtime、计数替身验证每个新 Turn 启动、
steer 不重复启动、compact/resume/入库失败不启动、后台失败不阻止下一 Turn，
以及等待者取消不取消后台任务、多次任务关闭和真实 SQLite 合并 claim 所有权。
后者包含临时会话跨 Turn 保留上下文、搜索/执行/压缩链，及已有记忆可读取但不启动
后台生成/不创建持久历史的行为。计数替身仅证明启动边界，不冒充完整提取模型调用。

扩展已有三分支测试：在 compact/resume/入库失败后继续同 Runtime 的正常 Turn，
证明跳过不会永久停用后台任务（失败分支先恢复存储写入）。本批无生产修改。
上述两文件联合来源/mode/claim 三文件：77 passed，17.70 秒（02d463），无跳过。
修改文件 Ruff 与 format 检查通过（34ae98/ab0098），diff 检查通过（ea4913）。
全量 34854 仍活动（30b510，约 47% 测试计数），并非最新源快照完整验收。
这补齐上一节点名的启动组合证据；D2 完整发布、D3 失效、D5/D6 总矩阵仍未关闭。
