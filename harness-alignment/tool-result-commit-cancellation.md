# 工具结果提交取消边界（A/B/E，进行中）

## E5 验收归并：已证实与剩余范围

Patch 内部重试本轮已核验：参考 runtimes/apply_patch.rs 的 run 从失败中取
committed_delta 并累积、拒绝分类后交给 orchestrator；wants_no_sandbox_approval
在 OnRequest 下也允许审批，不能复用普通命令的禁止规则。Corki backend 的
_file_operation 分 prepare/首次执行/审批/一次 retry，丢失或非法首次输出直接
PatchResult.unknown，不进入 retry。authorizations 的 patch retry 明确绕过
session cache，只接受 once，携带原始 stderr、执行证据和 committed_delta。
append_attempt 按执行顺序保留变化，exact 取两次的合取；超限降为 inexact，
不能把重试成功解释为首次副作用不存在或自动回滚。第二次拒绝也不循环。

真实本地编译器 patch_retry + patch_review 35通过（c83ceb）；扩大到
patch_retry/patch_deltas/patch_retry_contract/patch_delta_contract 为68通过、
1跳过，5.05秒，退出0（d7a2f8）。唯一跳过为需单独指定历史编译器的
test_old_approval_compiler_rejects_delta_contract_before_write，本轮未补跑，
不称全绿。当前原生证据包括先写第一个文件再拒绝第二个文件、重新批准/拒绝/
取消、拒绝读策略不可绕过、验证后软链接调换不跟随；真实写入后输出超限
返回 unknown，单元验证重试丢输出/取消/二次拒绝不第三次执行。

本轮仅审计，没有生产改动；审计保留当前普通宿主审批边界，不引入官方服务。
与 process startup 合并后，两个主要内部文件/命令重试链已具证据，后续应回到
主表跨模块剩余项，不再把这两条链路笼统列为“未检查”。这不扩大为任意第三方
handler 私有重试无副作用的保证，也不冒充远端 filesystem 后端实际运行验证。

执行工具内部 startup 重试本轮已核验：参考 tools/orchestrator.rs 只在沙箱
拒绝分支检查 escalate_on_failure、审批策略、无沙箱是否允许与重试审批；
sandboxing.rs::should_bypass_approval 允许复用 already_approved。Corki
execution/backend 在 authorize 成功后才设置 admission.approved；retry.parse_retry_plan
验证编译器能力、原命令、真实沙箱和策略；process_retry.finish_sandbox_startup
仅在早期已退出且非 timed_out 时分类，退役原进程后审批/复用批准，再执行至多一次。
已交付后台 session、普通退出、分类失败、取消和禁用策略均不进入再次执行。

实际本地编译器集成 sandbox_denial_retry 与单元 sandbox_retry_contract 合计
66 通过，10.48 秒，退出 0（9a2d2c），无跳过；包含 direct/Code Mode、TTY、
批准/拒绝/取消、启动/分类/审批阶段关闭、晚拒绝与第二次拒绝不循环。
首次未设置 CORKI_TEST_SANDBOX_COMPILER 为17通过/49跳过，不能作为集成证据；
最终使用项目已有 src/corki/_native/sandbox/corki-sandbox 明确补跑。本轮仅审计。

重要限制：拒绝分类是基于退出/输出的识别，不是“整个命令从未产生副作用”的证明。
批准后的第二次启动是宿主策略允许的重新执行，可能重复第一遍已发生的部分效果；
不能把 ledger 的同call-id保护夸大为命令内每个操作 exactly-once。已批准复用
符合参考普通审批分支；未引入官方 guardian 服务。E5 后续仍需核对 patch 等
其它内部重试编排，不重复将上述 process startup 路径列为未审计。

调用账本的身份/复用/恢复部分已核验，不再笼统当成未知：
sqlite._claim_tool_call 在 BEGIN IMMEDIATE 内验证 thread/turn/name/参数指纹，
已完成结果只复用、未完成返回未知错误、旧指纹缺失不从后写历史猜测；完成结果
不可改写。graph 的普通与嵌套入口共享 _execute_bound_tool，cached 结果先于
handler 执行且不再 complete。JSON 键序与空白等价，但自由文本、解析错误和
精确数值变化不可误当同一调用。模型采样重试以已更新历史继续，不绕开账本。

本轮 tool_identity/freeform_identity/numeric_call_identity、真实参数冷恢复、
history_retry 与核心恢复六文件 58 通过、18 个 mixed_output 参数场景不选，
9.63 秒，退出 0（8357d2）。包含旧列迁移、冲突完成竞争、原始数值身份、
未知副作用穿过模型断流重试、当前 RUNNING checkpoint 的公开恢复。
结合前几轮 Code Mode 存储失败/冷复用、HTTP/stdio 不重发与本地清理证据，
不再要求重复扩展相同账本矩阵。

E5 整项仍开放：工具 handler 内部的再尝试不等同于 graph 的重新 claim；
例如执行权限/沙箱失败后的重试，应核对其触发是否为已知拒绝、是否经过正确
审批、是否可能已有副作用。这必须看对应执行编排源码，不能拿账本去重证明。
参考 rmcp_client 的瞬时重试仅 tools/list、会话404只重建一次，已在 MCP 专项
独立核验；同理，第三方工具私有重试不是 Harness 自动保证的 exactly-once。

## 当前嵌套提交失败与冷账本补验

RUNNING 工具节点现补公开恢复正向证据：
test_running_tool_checkpoint_resumes_without_repeating_effect 两例实际通过裸 graph
执行一次副作用，在 handler 返回前或 complete_tool_call 真提交后抛取消，保留真实
execute_tools checkpoint 与 RUNNING Turn；关闭后新建 Runtime，调用 resume_pending。
断言同一 Turn 发 resumed/Completed、仅一次恢复模型请求、未提交收到 unknown 错误、
已提交收到真实成功、用户及工具结果历史各一条、副作用总共一次，完成后再恢复为空。
这里注入节点取消而非操作系统强杀，不宣称覆盖 fsync/掉电或跨系统原子性。
初始夹具漏 _ensure_ready 触发外键失败，随后校正 LangGraph NodeCancelledError
预期；均不是生产反例，未修改实现。参考 commit 仍为 ddf04ad 且干净。
核心恢复/错误通道/存储恢复联合 76 通过、18 个混合 CLI 参数场景本轮不选，
13.03 秒（8db7b3）；ruff check/format、diff 检查通过。

后续公开恢复入口核验：实际方法名为 resume_pending()，不是 resume()。
runtime.resume_pending 在读取/执行 checkpoint 前调用 latest_running_turn；
sqlite 的 SQL 只选择 status='running'。上述四种已持久化 TurnFailed 的场景
现新增冷调用 resume_pending，断言无事件、无模型请求、无第二次副作用；再显式
新输入才有一次模型请求。这一终态分流不是漏恢复：不能为了“恢复”重置失败状态。
相对地，现有两个 RUNNING 验收分别证明业务模型提交先于 checkpoint 时不重采样，
以及真实 LangGraph SQLite checkpoint 可公开续跑完成。
错误通道全文件加上述两项及完成结果唯一历史项共 59 通过，11.48 秒，退出 0
（3870ea）；ruff check/format 与 diff 检查通过。无需生产修改。

参考 rollout_reconstruction.rs::reconstruct_history_from_rollout 重建历史/窗口/
Turn 元数据，不应被表述为 Corki 的 checkpoint 重执行机制；本处不宣称两项目
公开恢复 API 同名或底层事务相同。剩余未完成 RUNNING 工具节点的恢复窗口仍须
按其自身证据验收，而不是要求已终态 FAILED 的 Turn 自动复活。

test_code_mode_error_channels.py 的 broken_nested_commit 原有真实 Runtime 四组合
（提交前/提交后报错 × batch/stream）现在增加关闭后重新构造 Runtime：按原
thread/turn/call 身份再次 claim，未提交返回 dispatch_error 的未知结果且拒绝重做，
已提交返回原成功结果；再执行新用户 Turn，模型正常结束、Probe 副作用计数仍为一，
原账本 status/result_json 完全不变。冷实例使用 await acreate，未修改同步 API。
既有断言仍要求存储失败是 TurnFailed，不能被 JavaScript catch 转为正常续采样，
Code Mode cell 必须已关闭。四组合 4 通过（7116e5）；错误通道、嵌套取消、存储恢复
联合 74 通过，12.54 秒，退出 0（1385c2）；ruff check/format 及 diff 检查通过。

源码重新核对：graph._execute_bound_tool 先 claim 再决定执行，cached 路径先于
目录曝光与实际 handler；sqlite._claim_tool_call 在事务内检查完整身份，completed
返回原结果，其它已存在状态返回未知错误，不创建新执行。参考 parallel.rs 的
终态已达到时 join 真结果、未达到时取消并 join 的原则不变；SQLite 冷 claim 是
Corki 自身的持久化机制，不声称 Codex 使用同一账本。

本批冷重复 claim 是 repository 入口验证，随后新 Turn 验证冷 Runtime 可用及不
自发重放；不是把新 Turn 当作原失败 checkpoint 的 resume，也没有验证远端事务
可回滚。仅补测试和审计，没有生产修复。CLI 保持用户要求暂停，以下 A–F 为历史表述。

参考 Codex commit ddf04ad26789d040f9ef6a96736f76602e35a6cc（工作树干净）：
core/src/tools/parallel.rs::handle_tool_call_with_source 在取消时检查 terminal_outcome_reached
或 dispatch_handle.is_finished；已达到终态则 join 真实结果，不替换为 aborted。
未达到终态才 abort 并 join。这里对齐终态所有权，不复制原生 search/custom 响应协议。

Corki _call_model 的取消清理先 live.aclose，再读取 partial calls，通过 ledger 恢复
结果并追加稳定 ID 的 ToolResultItem。_execute_bound_tool 先 complete_tool_call 再构造
历史项；SQLite complete_tool_call 却直接 await asyncio.to_thread，取消不会停止工作线程。
因此 live.aclose 可能已返回，而数据库线程仍未提交；清理读到 running 写入 unknown
历史结果，随后工作线程提交成功结果，冷恢复构造相同 ID 的成功结果产生内容冲突。
这是由源码支持的竞态解释；前轮批量冲突尚未采到字段差异，不能直接当成已证明同一原因。

修改前验收：扩展现有真实 Runtime mixed-output 故障窗口，门控实际 SQLite 完成提交，
模型 commit 后取消 graph；在门控释放前 graph 必须仍持有完成写入，两次取消都不能
遗弃它。释放后冷恢复/CLI 恢复应成功，两个工具均只执行一次、结果唯一、reasoning 和
正文唯一。拟复用既有 _joined_write，不放宽历史内容校验，不改变未知结果禁止重试规则。

实际证据：门控完成提交的两个新组合在改生产代码前均失败，graph 取消先于后台写入
结束（dc2395，2失败4通过）。complete_tool_call 改为既有 _joined_write 后六组合通过
（6b7407，1.98秒），包括二次取消仍等待提交、释放后 SDK/CLI 冷恢复、工具执行计数
均一次、模型/用户/结果记录不重复。测试使用真实 SQLite 和 Runtime、脚本模型，
不访问模型服务，不改变 ledger 内容冲突检查，也不放宽未知副作用的重试边界。

扩大至核心/存储/CLI 单位及冷历史、显示读取、摘要、手动压缩恢复、来源、真实恢复、
live tools、按需搜索和中断历史，534通过（d96ba4，35.44秒）。静态1098文件、
compileall、77包和diff通过（6654a1）。本批修复了确定性提交取消竞态；前轮随机批量
冲突未采到字段差异，因此不声称已排除所有可能导致相同报错的原因。

追加 Runtime 关闭、checkpoint 清理与初始化争用37通过（32f1c5，4.36秒）。
本批测试均已结束，无遗留活动进程。历史分页及整体 A–F 验收仍未完成。

相邻 claim 边界审计：claim_tool_call 仍直接 to_thread，它不是纯读，会插入 running。
Runtime 取消/关闭应 join 这个实际写入再宣告终态或释放存储；否则关闭后仍可能有写入。
计划实际 Runtime 门控 claim 提交前/提交后返回前两个窗口，分别取消和关闭，确保门控
释放前操作不结束、工具不执行；释放后取消终态唯一且存储关闭没有活动 claim。
原生参考仍为 parallel.rs 中取消先 join dispatch 的所有权原则；SQLite ledger 是
Corki 的恢复机制，并非声称 Codex 使用相同数据库实现。拟复用同一个 _joined_write。

claim 修复与验证：claim_tool_call 已改为 _joined_write。首次新增测试错误地把
cancel_active 当作 join；实际接口只请求取消，已修正为等待 consumer/Turn，未改变
生产取消接口。关闭分支最初先红后绿；用当前正确测试在独立进程恢复旧 claim 方法，
整响应四组合全部复现早退（33b77c，4失败4通过），流式四组合受既有 partial cleanup
等待保护而通过。此差异记录为真实链路差别，不虚称八组合原先都失败。
当前测试覆盖 claim 写入前/写入后返回前 × cancel/close × 整响应/流式；断言工具没有
执行、取消终态唯一、存储关闭发生在 claim 工作线程退出后。没有取消后自动重试或
把 running ledger 当成功；没有修改官方服务、模型传输、通用 OAuth 或 teach.md。
静态1099文件/compileall/77包/diff通过（f4ab51）。

最终扩大回归：claim 八组合、结果提交恢复、Runtime shutdown、live tools、核心与存储
单位、deferred search、Code Mode、checkpoint 清理/初始化争用225通过
（455b7e，25.38秒）。无活动测试，整体 A–F 和持久历史分页仍需继续推进。

模型 Step 提交相邻审计：commit_model_step 同样直接 to_thread；该事务原子保存
model_steps、conversation_items 和 usage，但 awaiter 被取消后事务仍能继续运行。
Codex stream_events_utils::record_completed_response_item_with_finalized_facts →
Session.record_conversation_items 记录历史；tasks/mod.rs 正常结束前 flush_rollout，
中断 marker 也在 TurnAborted 前 flush。Session.flush_rollout 取得 live_thread 后
等待其 flush。这支持对齐持久化所有权/终态顺序，不代表 Codex 有 Corki 的 SQLite Step
事务或完全相同的错误策略（原生 flush 错误会告警）。

扩展现有 Runtime 门控测试到模型提交前/提交后返回前 × cancel/close；改生产前四项
全部复现后台写入未结束而 Turn/close 已返回（c3931b）。拟改用 _joined_write，保留
事务原子性和校验；取消仍不触发后续工具，已接收到的模型事实不因取消丢失。

模型提交已改为 _joined_write，四个新组合修复后通过；另外在关闭后新建 SQLite
repository，检查同一模型结果和 conversation item 各保留一份。与既有 claim 八组合、
结果提交恢复、Runtime shutdown、live tools、核心/存储、按需搜索、Code Mode、
checkpoint 清理和初始化争用合计229通过（cf698d，26.06秒）。静态1099文件、
compileall、77包、diff通过（11ae1f）。剩余 sqlite.py 直接 to_thread 调用为读取路径，
本结论仅覆盖此文件本轮检查的写入入口，不冒充所有资源生命周期已完成总审计。
参考 commit 未变且干净（8cde2d），无活动测试；下一步回到历史分页及 A–F 总清单。
