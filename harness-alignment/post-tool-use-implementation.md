# PostToolUse：执行事实、反馈与恢复的接入约束

状态：同步command基础链已接入Runtime；完整Post契约和冷恢复尚未验收。

## 异步 Post 的投影后冷恢复与压缩替换

本批重读 Codex hook_runtime.rs::drain_async_hook_results：Context 与 Warning
分流，Context 在新输入前记录或向活跃 Turn 注入；任务替换的 abort/join 顺序
见前批 async-pre-tool-review.md。Corki 的 Post 冷恢复并非 Pre owner 的
project_checkpoint：runtime._execute_run 调用 post_hook_recovery.recover_feedback，
由工具结果及已保存 Hook batch 重建反馈，先保存 post_feedback_projection 计划，
再幂等追加历史，最后由 Runtime 更新旧采样 checkpoint。

test_async_hook_checkpoint.py 现在真实覆盖 Post/True：调用 Post 恢复入口写入
投影与历史后关闭 Runtime，再冷开同一 checkpoint，模型收到恰好一份反馈；
旧版本此参数只是普通冷恢复的重复控制，不作为过去已有中断证据。
新增 Post/compact：实际恢复函数返回后、aupdate_state 前暂停；公开 compact()
取消旧 worker 后生成摘要。验证旧模型没有再次采样，摘要输入包含反馈，工具
及异步脚本各一次；再次冷开新输入仅见摘要，不复活原反馈，归档仍保留一份。
Post 此入口不写异步 owner receipt，幂等历史身份负责后续去重，不能套用 Pre
的“receipt 已提交”描述。未修改生产实现，不是新缺陷的修复前红绿证据。

checkpoint、Post 恢复、异步 Post、手动压缩恢复四文件最终 204 通过，50.89 秒，
退出 0（5b0594）；ruff/format/diff 通过（dafb68）。此批关闭的是投影函数完成后
的两个 Post 窗口，不替代投影中途、活跃新输入及全部 Hook 来源的验收。

2026-09-12 同步Post输出解析修复：完整追踪hooks/schema.rs →
engine/output_parser.rs::parse_post_tool_use → events/post_tool_use.rs::parse_completed
及common::join_text_chunks。未知字段、非bool suppressOutput、缺少hookEventName
等属于wire解析失败，不应用block/context/systemMessage；语义不支持字段则在
continue=false之后处理，停止反馈仍保留，但无效控制下context不应用。空stopReason
保留为空而不替换默认文本；实际HookCompleted输出stop条目（不是feedback），
停止理由与模型reason可独立。空字符串反馈仍形成普通结果投影，nested保留原值。

10个反例先失败（2d3f2f），修复后通过（47fc12）。Runtime新增invalid_wire、
stopped_control、stopped_empty direct/nested6项；Post解析/真实执行/冷恢复/
PrePost压缩联合160通过（660025，38.17秒）。修正冷恢复和压缩脚本夹具中漏写
必需hookEventName的问题，不能将此前宽松夹具当原生合法wire证据。
native实际字段为HookEventNameWire枚举，Post-only schemars注解不构成额外
运行时事件相等校验；补三项已知枚举行为测试，不扩大到未知事件名。
本批覆盖同步command；async控制效果不同仍未实现，旧宽松输出的完整迁移矩阵
未验收，不能据此关闭全部Hook或A–E。参考commit未变且干净（ba64d4）。

2026-09-12 工具结果/Post batch原子提交：post.run新增prepare_only，仅准备
匹配后的可信配置、实际执行载荷及归属，不执行Hook。graph在原handler返回后
准备批次，complete_tool_call在同一个SQLite BEGIN IMMEDIATE事务中保存原工具
结果和Post batch，随后才执行脚本及反馈投影。缓存路径不重新准备配置；无Hook
及正常错误路径仍使用原完成调用。批次必须属于同Thread/Turn/call_id，已有批次
内容不可变，已完成的旧工具不能事后新增批次。复用现有表，不引入官方协议。

故障测试“batch”窗口现拦截真实complete_tool_call提交后、脚本claim之前；
Post108冷恢复与基础12联合120通过（28717a）。新增SQLite/volatile × 批次
INSERT失败/工具UPDATE失败/成功六项，触发器证明两个记录一起回滚，幂等重交及
配置碰撞不覆盖原事实。storage/Pre恢复/PrePost压缩/Post基础联合276通过
（bb0707，35.04秒）。静态1209文件/77包、compileall/diff通过（41e745）。

这关闭原工具结果提交后到batch保存前的独立提交窗口；准备批次本身发生在工具
副作用后、事务前，若该阶段或提交前失败，原有claim仍为未知且不重跑，不能
宣称任意外部副作用均可与本地数据库原子提交。历史旧工具无batch不猜测配置。
完整准备失败/取消矩阵、Post损坏快照、跨Turn终态及async/MCP型Hook仍开放。
下方“先单独提交工具再保存batch”的条目为历史实现，不代表当前提交顺序。

扩展主循环恢复/CodeMode检查首次1失败51通过（5d7000）：此前projection接入
假定存在checkpoint_id，而模型结果已提交、首checkpoint未创建的合法恢复没有
此ID。Runtime改为可选读取，无ID不建立checkpoint投影清单；已有主循环恢复
承担初始prepare。重跑52通过（0513ed，16.26秒），最终静态通过（61e11d）。

2026-09-12 Post压缩交叉验收：扩展原test_pre_hook_compaction.py为Pre/Post
共用真实Runtime场景，Post新增direct/nested × 短内容/溢出文件 × 手动/摘要失败
后重试/自动压缩12项。真实Hook脚本只执行一次，摘要请求包含原developer反馈与
工具结果；失败不安装摘要、不改历史。成功安装摘要后关闭并冷重开，Post恢复
返回空投影片段，后续普通请求只含摘要而不再含原Hook上下文；原记录与稳定ID
仍只保存一次。24交叉场景通过（af131b）；storage/executor/PrePost压缩/Post
基础链联合188通过（7a573a，11.75秒），静态1208文件/diff通过（383d91）。
本批仅新增行为证据，未修改生产代码，不宣称先红后绿；完整压缩安装中途崩溃、
旧projection清单与恢复、tool完成到Post batch前窗口仍未关闭。

2026-09-12 恢复自身幂等性实施：Runtime先读取checkpoint身份，再调用Post
恢复。复用hook_batches保存checkpoint-scoped projection清单（version与稳定
item_ids），先提交清单，再append历史，最后更新请求checkpoint。同一checkpoint
重试仍返回保存的ID集合，不以“历史已经有了”误判交付完成；新checkpoint只选择
新追加反馈，不无条件重放原历史。清单版本、重复ID和不属于当前候选的ID拒绝。
这是恢复记录，不运行Hook，不更改工具执行事实，也不涉及模型专属协议。

初版72真实冷恢复场景通过（17a445，18.04秒）：重复执行恢复但不更新checkpoint，
关闭旧Runtime再冷resume，原反馈仍到达模型且历史不重复。新增写历史前抛错、
写历史后再次恢复和新checkpoint不回填旧片段的测试继续扩展至108场景。
新checkpoint检查是投影边界测试，不冒充真实Post压缩安装/崩溃矩阵已经验收；
旧无恢复清单会话、正常Hook写入与采样并发、完整跨Turn/cell终态和batch损坏
矩阵仍需后续审计。下方append→checkpoint缺口为修复前记录。

最终本批Post108冷场景、Post基础链、Pre恢复及Pre压缩联合228通过（ac9f27，
58.89秒），无xfail；静态1208文件/77包、compileall/diff通过（8b85fa）。

2026-09-12 yield/wait恢复实施：CellObservation/ToolResult新增可选内部
code_mode_lifecycle_json（version=1、cell_id、parent_call_id、status），真实
exec/wait观察结果携带，executor保留，SQLite保存并严格校验；旧JSON缺省None，
不增加模型ToolResultItem字段。code_mode_parent_finished用同Thread的cell
终态观察判断完成，原exec保存running时不会误判完成，后续wait终态可证明结束。
原生wait_handler.rs明确区分Yielded与Result/Terminated，仅非Yielded结束cell
dispatch（7a4345），此处对齐此状态区别，不引入官方服务协议。

冷测试扩展真实yield情形：第二次模型请求挂起时取消旧图，关闭并重建Runtime；
首次8失败28通过（49ea7c）暴露恢复反馈虽已进历史，call_model checkpoint仍用旧
request_items。现Runtime把本次恢复新追加的片段同步到待执行call_model/retry_model
请求快照。36冷场景通过（ffae98）；与Post/Pre恢复、CodeMode、元数据存储联合
193通过（5397bb，39.08秒）。包含真实yield→wait完成状态切换及11项状态JSON
往返/非法值拒绝。静态1208文件/77包、compileall及diff通过（8de702）。

仍有必须处理的恢复窗口：恢复context已append、更新checkpoint前再次崩溃时，
下次恢复当前“新追加片段”返回值为空，旧模型快照仍可能漏掉反馈。不能把本次
普通冷恢复通过当成恢复过程自身的原子性已验收。旧yield结果无状态字段仍缺
证明；完整跨Turn wait/终态交付、损坏归属及工具提交到batch前窗口仍开放。

2026-09-12 嵌套阻断恢复实施：Cell._invoke 使用宿主 ContextVar 绑定外层
call_id，admitted task继承身份，退出必定reset；Post batch保存parent_call_id。
独立恢复读取同Thread父调用的真实ledger完成状态，而非合成的unknown文本。
父调用未提交完成时，已保存block或未提交Hook结果另建带稳定ID的恢复上下文，
明确内层调用、父脚本及“脚本结果仍未知”；不改写外层ToolResult，不重跑JS。
父调用已完成则不重新注入block（脚本可能已处理异常）。parent可源于此前Turn，
查询仍限定同Thread，缺失/非法身份拒绝恢复；旧batch无parent不猜测归属。

原六项xfail已删除：冷Runtime24项与Post基础链12项全部正常通过（b61bb1）。
补充独立重复恢复不追加历史、已完成父脚本不重注入反馈；Post/Pre冷恢复与
CodeMode联合156通过（2dbd32，36.50秒），无xfail。静态1208文件、compileall、
77包依赖/diff检查通过（57d1e5）；Codex基准commit仍一致且干净（5be9c4）。
原生registry.rs的block拒绝结果而非撤销执行原则保持；冷恢复上下文是Corki
持久化链为保留此事实的实现，不宣称Codex有相同的数据库恢复协议。

仍开放：已yield的后台cell原始exec账本已完成但cell仍活跃，完整cell/wait交付
归属尚未覆盖；旧batch无parent时block恢复仍无证据。工具提交到batch前窗口、
损坏/跨Thread元数据故障注入、压缩与恢复完整矩阵、async/MCP型Hook亦未关闭。
下方“六项未修复”为历史状态，以本节普通未完成父脚本的验收范围为准。

2026-09-12 后续实施：新增 core/post_hook_recovery.py 并接入 Runtime resumed
分支，按工具账本定位保存的Post batch，恢复已提交结果中的additionalContext。
复用原稳定ID、保存预算和输入归属；先验证全部候选再写历史，不查当前Hook
配置、不执行命令、不重跑工具，也不把内层结果改造成外层JavaScript成功返回。
此前仅post.run缓存分支能恢复context，外层exec未知时不会进入此分支；现独立
恢复补齐该路径。原始历史中的已有片段复用，未另造ID重新注入压缩窗口。

冷测试扩为block/context两类24项。context类12项包含嵌套冷恢复，现正常通过；
block类nested六项仍标记xfail，但标记之前会实际验证context和副作用去重。
与Post基础链及Pre冷恢复联合126通过、6预期失败（207907，32.41秒）。
静态ruff/format1207文件、compileall、77包依赖及diff检查通过（ae4e91/ec3bb6）。
这只关闭保存additionalContext的恢复缺口；阻断理由仍需父子调用/交付归属，
不能无条件把历次nested阻断重新注入，否则会把脚本已处理的异常或已压缩反馈
再次带回。tool提交到batch前窗口、损坏batch完整矩阵及完整A–E验收仍开放。

2026-09-12 冷 Runtime 故障注入：新增 test_post_hook_recovery.py，真实脚本及
工具副作用，关闭旧 Runtime、重新建 registry/Runtime 后 resume_pending。覆盖
batch 已保存未 claim、脚本已执行未提交结果、结果已提交未归档三个窗口，
direct/nested 与保留/删除配置共12项。首次运行6通过6失败（e77cdc）：direct
正确恢复 block 或未知反馈；nested 外层 exec 正确保守保持未知且不重跑，但
已保存的内层 Post 反馈没有独立恢复路径，POST_BLOCK 丢失。runtime 中
load_turn_tool_outcomes 此处仅用于 turn_diff，tool_readiness 仅用于避免唤醒
已执行工具；不能把这两处误判为模型反馈覆盖点。缺口是外层未知调用不再进入
内层 graph dispatch，因此内层 post.run 的缓存投影不会被调用。

现将 nested 六项明确标记已知缺口 xfail，标记前仍验证不重复模型采样、工具及
脚本副作用、原历史前缀不变和二次 resume 无任务。这不是修复或全绿验收。
后续需独立恢复内层已保存反馈及 additionalContext，并关联原输入和稳定ID；
不能重跑外层 JavaScript，也不能把内层工具结果冒充外层脚本的完成结果。

最新执行实施：StopHooks可信快照纳入Post事件及独立信任hash；graph捕获Step快照，
工具原结果先提交、外部污染先记录，再调用post_tool_hooks.run，Post事件/context
先于原ToolCallCompleted；完成事件、patch diff使用原结果，模型投影另算。同步
command并发运行并按配置序聚合，支持block、continue=false反馈、additionalContext、
warning与脚本错误诊断。非block反馈只改变直接模型内容，nested保留原结构化值；
block不覆盖工具ledger、不重跑handler。内部worker跳过，typed子Agent保留身份。

独立hook batch保存实际载荷、配置及输入归属；缓存路径只读取保存结果，不重跑
脚本或使用当前配置，未知结果保守返回错误。已有额外上下文按稳定ID去重。
尚有重要缺口：工具提交到batch保存之间的故障窗口未完成；完整Runtime冷恢复
与取消恢复可能通过其他ledger读取路径，必须逐条补齐，不能仅靠本函数缓存分支
宣称恢复正确。batch损坏/权限撤销/并发故障与跨Turn终态的完整矩阵仍开放。

tests/integration/test_post_tool_hooks.py用真实文件副作用和真实Hook进程验证
direct/nested × block/context/stopped/工具错误/脚本错误/不可信12项；原结果始终
留在ledger、模型投影不同、Hook输入/结果正确。随后直接调用已保存batch的恢复
组件，验证脚本一次、历史不重复、反馈不丢；此检查不是完整冷Runtime崩溃恢复。
初版12通过（574b75），与Pre/stdin/存储batch联合75通过（7b3b9e），Post/Stop
33通过（619a6f）；事件顺序断言后12通过（de977d）。本批未做修复前运行，
不把这些数值称为先红后绿。两处导入排序已修复；静态1205文件/77包通过（35c564）。

生产适配包含普通函数、MCP、patch及此前shell终态载荷，但本批新增全链测试
主要覆盖普通Probe；其他工具与Post交叉组合仍待验收。async/MCP型Hook仍未实现，
异步command明确警告，不把跳过算对齐。输出解析所有边界也仍需原生测试映射。
下文“未接入”的前置记录保留为历史状态，以本节为准；完整A–E目标不变。

最新来源修正及实施：进一步追踪ProcessManager._launch发现terminal_info已包含
BackgroundTerminalInfo(item_id, process_id, command, cwd)，前轮把“观察结果未传递”
扩大成“进程会话未保存”不准确。现复用此对象传到ProcessObservation，不新增
重复进程身份。shell_result只在终态生成version=1的post_tool_use_json（Bash、
原tool_use_id、实际command及模型策略下的输出），executor规范化保留该字段，
随原结果落入账本；初始yield没有该载荷。会话历史/模型声明不增加专属字段。
这是原生命令来源/终态载荷的前置实现，不意味着Post脚本已接入或已被执行。

四真实跨Turn direct/nested来源反例先失败（45ad4e）。首次实施10失败24通过
（c5934e）暴露executor重建结果时丢弃新载荷，以及旧SimpleNamespace夹具未带
可选terminal_info；补齐真实规范化链并更新夹具后，真实stdin/改写shell/输出/
存储联合70通过（a61d11，16.77秒）。另补非零退出仍具资格、运行中不具资格
及冷JSON往返，存储专项14通过（db1bf0）；静态1203文件/77包通过（ca3164）。
这些测试验证载荷落盘，不验证Post脚本调度/去重/反馈恢复，后者仍为下一阶段。

最新前置实施：executor在参数复验完成、handler调用前序列化version=1的执行
输入快照，规范化结果后由宿主填入ToolResult.execution_input_json，随原结果
一次性落入SQLite账本。快照包括input_kind、decoded arguments和raw_arguments，
handler修改执行对象不能改写已捕获快照；Post尚未消费它，不将前置实施当Post完成。
原ToolCall历史仍不改，ToolResultItem/普通模型请求不携带这个内部字段。旧结果
缺字段保持None且重编码不增加字段，不能猜测旧执行输入。返回合法结果（包括
handler错误值）可保存快照；尚未覆盖handler抛异常及所有Post资格/恢复分支。

真实Pre direct/nested的改写及handler mutation四反例先失败（e10d5f），实施后
完整Pre54通过（6a74fd）。加入旧JSON兼容、损坏元数据拒绝及模型窗口不暴露
检查后，storage/executor/Pre/MCP/参数恢复联合265通过（a9c942，25.86秒）。
此为新账本读取证据，不冒充Post脚本冷恢复。静态1203文件/77包通过（b86765）。
下方“执行副本只在局部可见”是实施前状态；新结果已保留快照，旧结果仍未知。
范围：A/B/C/E核心；CLI暂停，teach.md不修改，不涉及官方服务或原生模型协议。
参考固定commit ddf04ad26789d040f9ef6a96736f76602e35a6cc。

## 已追踪的原生链

tools/registry.rs::dispatch → handle_any_tool：handler先执行；原始输出含外部
内容时先标记memory污染；从实际执行后的invocation/output构造Post载荷。
dispatch用success_for_logging门控，再调用hook_runtime::run_post_tool_use_hooks。
后者preview/start → hooks/events/post_tool_use::run → completed事件；调用方再
record_additional_contexts。工具终态依据原执行结果，不因Post block改写副作用。

Post block只把结果转成RespondToModel；非block反馈包装为PostToolUseFeedbackOutput。
此包装保留original的log、success、fallback预算、sources与code_mode_result，仅
替换to_response_item。不能把直接模型反馈和嵌套结构化返回当同一投影。

hooks/events/post_tool_use.rs聚合配置顺序的context/feedback，任一有效block阻断
模型结果。exit 2必须有stderr反馈；其他非零退出及执行错误为失败诊断、不重跑工具。
output_parser::parse_post_tool_use与Pre不同：支持continue=false的stop反馈；reason
无decision且continue=true非法；block必须非空reason；suppressOutput与
updatedMCPToolOutput明确不支持。不可复用Pre解析器而悄悄改成另一份控制契约。

## 本轮确认的接入缺口

| 事实 | Corki当前来源 | 实际影响/优先级 |
|---|---|---|
| Post配置未进入可信快照 | StopHooks.prepare仅Stop/SubagentStop/Pre；discover/event key表同样如此 | 仅写执行函数不会生效；P1 |
| 执行副本只在executor局部可见 | ToolExecutor.execute的execution_call，graph仍持有原call | Post若取原call，会审计错Pre改写前的操作；P1 |
| 原结果只能提交一次 | SQLite._complete_tool_call对已completed且内容不同抛StorageIntegrityError | 不能先提交原结果，再用Post反馈覆盖同一ledger；P0 |
| 已提交结果跳过handler | graph._execute_bound_tool的cached分支 | 新Post阶段必须区分纯反馈恢复与脚本未知状态，不能为补Hook重跑handler；P0 |
| shell成功不能按exit_code==0判定 | 原生context.rs::ExecCommandToolOutput::success_for_logging恒true | 非零退出的正常进程结果仍可能有Post；不能简单过滤；P1 |
| shell终态由载荷资格决定 | 原生post_tool_use_response在process_id存在/缺hook_command时None | 初始yield不是终态；不能每次poll都Post；P1 |
| shell观察结果缺原调用身份（本批补传） | _ProcessSession.terminal_info原已保存，旧ProcessObservation未传递；前轮会话缺失判断已纠正 | write_stdin需关联原exec调用及改写后的命令，不能用当前poll ID/输入；P1 |
| 副作用记录和模型反馈共用result | graph complete、ToolCallCompleted、turn_diff.observe、返回投影均依赖同一结果 | Post block不能抹掉patch delta、外部内容污染或原始日志；P0 |

原生unified_exec/process_manager保存hook_command，后续write_stdin结果取原值；
context.rs::post_tool_use_id优先event_call_id。必须传递显式来源，禁止解析日志
或通过字符串输出反推原命令。Corki终端权限快照不是这组Hook来源信息的替代品。

## 实现次序与验收要求

1. 在执行副本及规范化结果仍同时存在的边界捕获不可变Post载荷，并保持成功/
   载荷资格分离；真实普通、MCP、patch、shell映射都要追踪，不只支持Probe。
2. 设计并实施独立执行事实与Post反馈阶段的持久化。原handler结果保持不可变；
   Post脚本claim/结果、反馈投影、additionalContext分别可验证。旧记录不能用
   当前配置/猜测输入补跑脚本。新增字段/记录必须覆盖旧数据库及checkpoint。
3. 接可信快照、脚本进程、事件和Post专用输出解析；异步/MCP型Hook仍为完整
   目标的一部分，分批实现不等于排除。配置来源和信任hash须绑定Post事件。
4. shell在启动时保存原call ID及实际command，终态观察才构造载荷；重复轮询、
   关闭、取消、跨Turn和未知进程恢复不能生成重复Post。
5. 真实Runtime验证副作用一次、Post block不回滚文件/远程效果、直接反馈与
   nested结构化值分离、Pre改写后Post输入一致、additionalContext进入下次请求。
6. 故障窗口至少包括handler完成/Post尚未claim、Post未知、Post结果已提交但
   反馈未归档、反馈已归档但checkpoint未提交；冷恢复和重复取消都不能重放副作用。

以下均不能替代上述验收：仅单测parser、仅配置字段存在、仅正常路径成功、
把is_error=false当所有工具统一Post资格、把unknown结果伪装成已完整恢复。
本轮不引入一个无法保持上述事实边界的临时Post回调；下一实施批次应从执行
事实/反馈持久化和实际执行副本捕获开始，完整A–E范围保持不变。
