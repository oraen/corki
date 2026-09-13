# 记忆清理与新旧后台任务交接（D/E）

## 新owner空输出与失败终态补验

本批继续核对原生state/src/runtime/memories.rs：clear_memory_data_in_pool
事务删除两类jobs/outputs；mark_stage1_job_failed以running及ownership_token
为条件扣减重试。Corki sqlite._complete_extraction先查running/token，_fail_job
也以两者限制UPDATE，不能只按同名thread/job更新。

test_memory_reset_repository.py的新owner状态由running/success扩展为
running/success/empty/failure，保持重置事务正常/回滚后重试×旧success/empty/
failure交叉，共24例，比原来新增12例。通过重新打开的repository完成新owner，
旧回调前后比较完整jobs及outputs行；新empty/failure保持空输出，新failure的
retry_at/retry_remaining/error也不能被旧回调改写。会话和禁用mode仍保留。

与test_memory_reset_runtime及reset_inflight真实Runtime/执行租约场景联合
35通过、0跳过（2cad44，6.57秒），退出0；命令使用显式当前native compiler。
改动文件ruff/format通过（2f80db），diff检查通过。仅增加行为测试，无生产修改，
不声称这些repository矩阵自身调用了模型，也不把协作锁说成任意外部进程隔离。
本节补齐stage-one重置后新owner四种状态的晚回调证据，不据此关闭所有D3版本/
过期/水位或D6后台关闭项。CLI与teach.md未修改。

## Stage-one 重置后同名任务重新领取：补验计划

当前源码复核：原生 state/src/runtime/memories.rs 的 clear_memory_data_in_pool
在同一事务删除 outputs 与两类 jobs；mark_stage1_job_succeeded/no_output/failed
均先验证 running 和 ownership_token，再写输出、删除输出或扣重试预算。
Corki sqlite._complete_extraction/_fail_job 保持相同 owner 准入顺序。
现有 reset_repository 只证明删除 job 后旧 claim 无效，随后新 claim token 不同；
尚未在该组合中验证新 owner 已 running/succeeded 时，旧成功、空输出、失败回调
均不能覆盖新结果、删除新结果或改写水位/重试预算。计划扩展真实 SQLite 场景，
使用重新打开的 repository 完成新 owner，比较全部 jobs/outputs 行；不预设生产修复。
执行期的真实 Runtime 组合沿用下方 reset_inflight/reset_runtime 专项联合验证。

已补验：reset_repository 扩展为数据库重置正常/事务回滚后重试 × 新owner运行中/
已成功 × 旧成功/空输出/失败，共12例。新owner由重新打开的SQLite repository
完成，旧回调前后比较全部jobs及outputs行，证明不篡改状态/token、水位、lease、
重试预算和新结果；新owner最终仍可成功。保留会话及禁用mode不变。
与执行期reset、晚到输出、reset失败、跨进程租约、提取claim及取消handoff联合
58通过、0跳过（50f2b8，7.83秒，55539退出0）。没有生产修改，没有声称先红后绿。
静态检查207575通过：1190文件格式、ruff、compileall、77包依赖与diff。
参考commit再次核实39a5b6不变；本批仅在临时目录清理测试记忆。
另补跑过期输入清理、空输出与owned completion三文件23通过（ce490c，4.16秒），
覆盖清理持有SQLite写锁直到线程结束、旧claim禁止清理，以及当前owner的source
版本保护、空输出撤回和取消完成写入。两组结果分别记录，不冒充单次全量memory。

范围区分：显式记住/更新/遗忘走普通add_ad_hoc_note，记录权威信息供后续合并；
不是同步从全部历史中删除某个事实。Codex memories/write/templates/extensions/ad_hoc/
instructions.md与Corki prompts/memory/ad_hoc_instructions.md都要求合并新增/编辑笔记、
不删除笔记、把内容当信息而非动作指令。不得声称这是即时、永久屏蔽旧事实的系统。
reset_memory是宿主明确清空生成记忆的另一入口，保留会话及未来生成资格。

原生所有权参考：memories/write/src/phase2.rs完成worker并校验产物后再次heartbeat，
只有仍持claim才reset_memory_workspace_baseline并标记成功。Corki pipeline._run_owned_consolidation
完成后complete_consolidation在SQLite写事务内验证running状态/token后执行publish；重置删除
memory_jobs使旧claim失效。已有真实Runtime测试证明重置后旧采样不能发布到空目录，
但未在该测试中组合“新owner已经成功发布，再让旧worker返回”。状态：组合证据不足。

验收补充：同一数据库/记忆目录，旧worker采样阻塞→宿主reset→新Runtime成功合并→
旧worker返回。必须保留新MEMORY/summary和Git基线，新成功job不能被旧失败清理覆盖；
保留原无新owner场景。仅临时测试目录内重置，不触碰用户实际记忆。不预设修改生产实现。

深化故障注入发现真实缺口（0f7cb9）：旧模型在reset后返回apply_patch调用，即使最终
report.failed且publish拒绝，STALE_WRITE_PROOF.md已经写入共享根。无新owner/新owner
已成功两分支均失败；纯JSON结果两分支通过。BorrowedModel此前未检查claim，不能把
最终发布fence等同每个工具副作用fence。修复本批模型输出接纳边界：请求前及完成item
交给子Runtime前校验当前claim；失效则终止旧worker，不再接纳晚到工具调用。已经接纳
并运行的外部进程与reset交错仍需独立核验，不能用本测试证明其副作用已被完全隔离。

实现：PreparedAgent携带当前pass的check_owner，使用既有SQLite
write_consolidation_workspace的running/token事务检查（no-op文件回调）；BorrowedModel
在请求开始及ModelCompleted/ModelItemCompleted交给内部Runtime之前检查。失效时
ValueError终止子Turn，由既有memory失败隔离路径处理，模型stream通过aclosing关闭，
不重放被拒绝的调用、不关闭父模型。未增加官方服务或模型协议字段。
原生phase2周期heartbeat与结束前检查用于所有权参考，本次更早的工具接纳检查是为
Corki共享目录的已复现竞态补安全边界，并非声称原生逐事件进行相同数据库查询。

最初完整memory回归746通过26跳过（90cd0a，198.93秒）采集早于本生产修复，
不能作为修复验收。修复后原四组合4通过（a2aa39），再加入流式item完成入口补验。

流式/整响应/旧JSON×无新owner/新owner已发布六组合6通过（b45043，3.98秒）。
明确下一故障窗口：模型输出检查成功后到实际handler执行之间仍有调度间隙；已经启动
的shell/helper也可能跨过reset。需要结合共享workspace所有权、取消join及跨Runtime
访问核验，不能仅扩大模型回调来宣称工具执行全程原子。该缺口属于D/E未完成项，
不是官方服务排除项，也不能用“与原生同样可能”直接判为已完成。

修复后完整memory单位/集成748通过26跳过（11e793，194.36秒）；测试采集早于新增
流式item两分支，后者由上述六组合补验。26个跳过不计通过/不冒充已执行，包括需要
显式native sandbox配置的测试条件。静态1092文件、compileall、77包/diff通过
（83b2d9）。Codex参考commit仍ddf04ad26789d040f9ef6a96736f76602e35a6cc，无改动；
当前无活动测试。完整D/E及A–F目标继续开放。

执行期后续方案（实施前）：模型输出检查不是执行全程互斥。计划在共享worker首次接纳
工具调用时取得根目录外的OS租约，并在持锁后再次检查claim，持有到真实子Runtime关闭。
重置必须在清DB/文件前取得同一租约；正在执行或关闭未确认时明确报告busy、不清任何
状态，调用方可在worker结束后重试。不自动重放未知副作用、不把取消请求当关闭确认。
使用现有storage.file_lock的内核所有权，不根据PID/时间推测；外部非协作进程及父进程
崩溃后存活子进程仍需单独说明。此机制是Corki共享目录安全修复，不宣称Codex相同实现。

执行期实现及初验：真实内部Runtime的ApplyPatchTool进入后挂起，同宿主/另一宿主reset
原先都错误成功（a4bf2a两失败）。WorkspaceLeases现在使用规范化root父目录的
`.ROOT.corki-memory.lock`（常规锁文件不在reset清理范围），首次工具完成item接纳前
取得租约并再检查claim；工具后续采样、后台terminal和Runtime关闭都保持租约。
确认关闭才释放；超时/失败关闭由ConsolidationShutdowns保留cleanup闭包与租约，
不因为父pass结束而解锁。取得锁的线程在返回前把所有权交给PreparedAgent，避免取消
丢失新descriptor；多根取得失败释放此前已取得的锁。reset取得同组租约后才清DB/文件。
busy及锁文件I/O失败明确返回MemoryResetError(database_cleared=False)。

25项reset/晚到输出/执行期专项通过（dcc914）；跨进程互斥、部分取得回滚及真实后台
terminal关闭超时/取消/heartbeat/晚失败12项通过（9c11a2）。后者验证未确认关闭仍busy，
关闭确认但临时副本清理失败可以释放执行锁，失败的关闭确认仍保留锁。
锁是协作协议，不是内核文件访问隔离：不协作的外部写入者、父进程崩溃后逃逸子进程
仍不受这个租约本身约束。也未将显式遗忘变成即时从原始会话永久删除。

原生清理调用链补查：app-server/request_processors/thread_processor.rs::
memory_reset_response_inner 与cli/main.rs::run_debug_clear_memories_command都是
先clear_memory_data后memories/write/src/control.rs::clear_memory_roots_contents，
后者拒绝symlink根、保留根目录并逐项清空。所查入口未提供本批OS执行租约。
因此busy是针对Corki已复现风险增加的失败保护，不能报告为两实现逐项完全一致。

扩大本批专项38通过（bc62ea，15.07秒），包括锁文件打开失败时DB/文件均不清理。
静态1095文件、compileall、77包/diff通过。完整memory回归752通过26跳过
（e0135a，195.65秒），采集早于新增跨进程/关闭租约断言及锁I/O失败断言，后者由
38项专项覆盖。无活动测试。未运行条件项不计验收通过，完整goal继续开放。

原生执行补验：定位上轮26个skip为24个父权限组合、1个invalid_profile、1个真实共享根
managed deny场景，均仅缺CORKI_TEST_SANDBOX_COMPILER。当前bundled_compiler经
verify_compiler验证macOS/arm64及二进制manifest，native/sandbox/receipt.py::source_digest
与当前源码一致（047ab9）：source 2438561fd3afbd02041affe1b345d98288bb697633bfd11d6af1fab7aa049860，
binary 966d3aaf44f77a78447d8c0758c0fe9594904733da52d8e49a0d2fcb89e4c3ba。
仅在测试进程设置显式后端，不改用户配置、不重新构建。

test_memory_execution_permissions、shared_workspace、reset_inflight、reset_runtime、
shutdown_deadline共52通过0跳过（8f43c1，31.42秒）。实际进程验证Managed只能写memory
且拒绝父目录/网络，Disabled/External保留调用方语义；External不代表本地强制隔离。
Direct/CodeMode、stale base、managed approval约束都走真实子Runtime；共享根deny
在采样前失败，不通过副本绕过。参考原生phase2_sandbox_tests.rs::
consolidation_uses_canonical_parent_enforcement以及Corki MemoryPermissionSnapshot。
本次关闭的是26个跳过的执行证据缺口，不是重跑整个memory集合的单次结果。
静态1095文件/compileall/77包/diff通过（e12fd7），参考commit不变且无改动，无活动测试。
