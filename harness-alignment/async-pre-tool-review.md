# 异步 PreToolUse：实现与剩余验收

## 恢复投影已落盘、尚未更新采样时被手动压缩替换

源码参考：Codex tasks/mod.rs::Session.spawn_task 先 await abort_all_tasks(Replaced)
再 start_task；tasks/compact.rs 的普通本地管理路径调用 compact::run_compact_task。
远程路径仅阅读，仍属用户排除项。Corki runtime.compact/_start_turn 取消并等待
当前 worker 后才进入新压缩 Turn；因此不应把同 Runtime 两个 worker 并发安装
压缩窗口当作正常可达情形。真实风险窗口在取消前的部分持久化状态。

新增 test_async_hook_checkpoint.py 的 PreToolUse/compact 场景：真实异步脚本
完成、冷恢复进入 call_model；实际 project_checkpoint 已提交投递计划、历史和
receipt 后暂停，尚未 aupdate_state。此时通过公开 compact() 取消恢复 worker。
验证旧观察流收到 TurnCancelled 并传播取消控制流，旧模型不重新采样，普通
摘要请求包含 CHECKPOINT_ASYNC；压缩完成后再次冷开，无待恢复 Turn，新用户
请求携带 CHECKPOINT_SUMMARY 压缩项，不再携带原始异步反馈。归档仍保留恰好
一份反馈，原工具和脚本各执行一次。

首次失败来自夹具未捕获观察流在 TurnCancelled 之后抛出的 CancelledError；
补强摘要断言时曾误读 CompactionItem.content，已按真实类型改查 summary。
两者均不是生产修复前红绿证据。最终 checkpoint、手动压缩恢复及投影边界
三个文件 41 通过，4.46 秒，退出 0；ruff/format 通过（80ee94）。
本批仅测试及审计更新；关闭该具体替换窗口，不据此关闭全部 A7/C8，投影中途
不同提交点、Post 的对应交错、新输入及其它安装取消组合仍按各自证据验收。

## 异步Pre压缩与冷恢复补验

本批对照原生core/hook_runtime.rs::drain_async_hook_results消费Context条目后
记录/注入采样，以及core/compact.rs的summary→build_compacted_history→
replace_compacted_history。Corki graph.prepare先recover/drain异步Pre；receipt
使冷恢复跳过已交付反馈，普通摘要与模型窗口替换由Harness负责。原生专属压缩
元数据不是本项目实现要求，未新增任何远程压缩协议。

test_async_pre_hooks.py增加direct/nested×手动压缩、自动压缩、摘要失败后显式
重试六组合。真实异步脚本完成并持久化，移除Hook配置、冷开并消费，再压缩并
第二次冷开：摘要请求恰含一份原反馈及工具结果；最终普通请求包含摘要marker而
不含原反馈；SQLite原始前缀和原反馈保留，工具参数仍original、效果一次、脚本
计数一次、无同步Hook通知。失败摘要不更改历史，后续重试成功。

十八项通过（4294c3，6.66秒）；联合Pre/Post、checkpoint、手动压缩事务恢复与
自动压缩安装116通过（270783，31.15秒），退出0。改动文件ruff/format及
git diff --check通过（bf5412）。本批仅补测试，不改生产代码；旧14337全量早于
这六项新增测试，不能把新项算入旧全量结果。

初次两次收集失败为ModelUsage导入路径写错；随后两项失败是夹具未按Post对照
固定摘要重试设置，不能算生产反例。初始及冷开统一显式窗口100000、自动阈值
50000、model_max_retries=0后，故障确实成为TurnFailed，手动重试独立验证。
本节仅补已交付反馈的压缩/冷开负断言，不关闭checkpoint投递计划跨压缩安装
取消、所有新输入交错或完整A7/C8。CLI与teach.md未修改。

最新损坏边界修复：投递计划同时包含有效ID和缺失ID时，原实现会先提交有效
context/receipt再失败；Pre/Post两组件反例先红（a3a177）。现投递前校验整份
选择，候选来自已验证队列的有效输出及历史中类型/kind/key/确定性ID匹配的
ContextItem；无效计划不写反馈、不确认队列。context_item_id抽取为共用
纯函数，保持原UUID算法。非dict计划改为显式ValueError。

组件+真实Runtime checkpoint/Pre/Post联合82通过（cdade1，25.25s）。全src/
tests的ruff、1220文件format、compileall与pip check通过（d016fb）。不是
最新全量pytest；不宣称覆盖所有损坏记录、跨checkpoint引用或恶意数据库
篡改。完整取消/压缩交错仍开放，CLI暂停。

最新投递快照竞态修复：计划save期间新完成结果此前被drain写历史/receipt，
却不在计划item_ids内。Pre/Post共享组件两反例先红（a12d5b），改为checkpoint
drain仅确认计划内context，其他结果留队列；普通drain按入口完成快照处理。
选择性ack不要求位于队首，冷恢复重排后也不吞掉尚未选中的结果。原生
core/hook_runtime.rs::drain_async_hook_results仍为反馈进入当前/后续采样的
语义依据；checkpoint计划是Corki存储实现，不宣称原生同样使用此算法。

共享组件并发/恢复重排四项通过；真实Runtime checkpoint及异步Pre/Post
联合78通过（d1d538，25.57s）。这是确定性repository故障注入加既有Runtime
回归，不冒充两脚本同时完成的真实时序证明。剩余投递取消/计划损坏身份/
压缩交错等继续开放，CLI暂停。

最新checkpoint修复：resumed的call_model/retry_model入口现调用异步Pre
project_checkpoint，先保存按checkpoint ID绑定的item_ids计划，再投递
历史/receipt，最后合入冻结request_items。再次重开可从计划补回已投递但
checkpoint未更新的context；默认不从完整原始历史重选已存在反馈，避免
直接复活压缩前内容。投递计划使用独立async_hook_projection前缀，不混入
Pre执行批次扫描。无新数据库表，未改同步Pre或Post的旧投递协议。

原Pre反例已绿、Post对照保持通过（73f8f8）。新增Pre投递后/检查点更新前
关闭重建通过；该文件4参数项通过（c5fc55，Post的中断参数目前是普通对照，
不计Post新增故障窗口）。Pre/Post冷恢复及同步Post恢复联合182通过
（b1a4a1，51.52s）。检查发现未使用变量，已修正，最终ruff及diff检查通过
（84f506），四文件格式检查通过。下段“生产未修复”由本段更新。

剩余必须补验：投递计划写入/历史/receipt/aupdate_state各取消窗口、计划损坏
和身份绑定、压缩后恢复负断言、计划构造期间新增异步完成的投递快照边界。
当前实现与局部通过不足以关闭完整A7/C8；CLI保持暂停。

新确认A7/C8缺口：test_async_hook_checkpoint.py通过真实脚本、SQLite及
compiled graph构造call_model检查点，脚本exit_code=0且结果已入账后取消
裸图任务（不伪造正常Runtime取消的恢复资格），关闭并移除配置，再调用
resume_pending。Pre恢复后的首次请求缺失context，Post对照通过（3f3bab，
1失败1通过）。首次两失败含脚本括号夹具错误，已修正并加成功退出前置断言，
不以该首次结果认定Post缺陷。

原因：runtime._execute_run的resumed分支只恢复同步Pre反馈；异步Pre在
Graph.prepare中恢复，而call_model检查点绕过prepare。Post已有独立的
post_feedback_projection持久投递计划覆盖此窗口。修复应为Pre补同等的
checkpoint投递身份与重入去重，不能简单拼接所有历史context（会复活压缩
前反馈），也不能仅drain后台队列后依赖下一Turn。当前生产尚未修复，新增
反例不skip/xfail，目标继续开放。

最新冷恢复证据：真实Runtime direct/nested各覆盖同Turn、已完成未投递冷
重开、未知结果关闭后重开、context提交后故障、receipt提交前/后故障六种
路径（12项）。已完成场景移除配置，仍从旧批次恢复；未知场景保留可信配置，
确认原脚本PID退出且结果仍未知，不产生虚假context、不重放。各冷场景再
重开一次，持久历史context至多一项，工具/脚本均仅执行一次。

Pre解析/共享并发/Post冷恢复联合99通过（a89361，24.53s）；ruff与diff
检查通过，格式检查前已无格式差异。本批未修改生产代码，不把已有实现的
新增验证写成新修复，也不把正常关闭重建和受控提交故障冒充OS强杀验证。
源码依据仍为core/hook_runtime.rs::drain_async_hook_results的采样反馈边界；
持久账本是Corki恢复设计，不宣称原生存在相同SQLite表/回执协议。

仍待验收：跳过prepare的checkpoint恢复、混合同步/异步真实脚本并发、压缩
安装交错、完整损坏/历史兼容矩阵。下方较早“Pre冷恢复未验收”由本段的具体
覆盖范围更新；A–E整体仍开放，CLI暂停。

共享并发修复已验证：Graph现在将Stop会话limiter注入Pre/Post owner，保留
各事件独立结果解析及投递队列。实际Runtime owner混合27任务时仅8项进入
执行，释放后27项全部完成；关闭时仅已启动8项执行finally，排队项不启动。
两项先红后绿，异步Pre/Post/Stop联合79通过（f23749，24.02s），本批四文件
ruff/format及git diff --check通过。该新增测试直接驱动Runtime持有的调度
owner，并非27个真实子进程或模型发起的混合Hook端到端测试；后者仍待补验。
下方“共享预算未验收”由本段更新，不关闭Pre冷恢复及其他完整故障矩阵。

共享并发差异已确认：原生engine/command_runner.rs的CommandRuntime共享
concurrency_limit（8），schedule_async_task统一领取permit，shutdown关闭
队列并回收任务；对应command_runner_tests验证第9项排队。Corki的三个
AsyncHooks独立构造Semaphore(8)，实际Runtime owner混合27项测试得到同时
24项启动，两反例失败（0441d1）。拟共享单一会话limiter，验证释放后全部
完成以及关闭时排队效果不启动。首次测试导入夹具错误已修正，不算生产反例。

最新实施：已接入Graph的prepare/finalize反馈投递、Pre工具准入以及Runtime
关闭时的任务接管/回收。异步Pre采用独立批次身份与解析，异步控制字段不会
阻断或改写工具；已有批次不按新配置重跑，未知/未claim效果不自动重放。
原有同步Pre记录格式保持不变。下方“尚未实现”段落保留为修复前证据。

真实Runtime direct/nested原两项反例已通过（d7ef86）；新增异步控制与严格
wire解析25项通过（8f50d3）。异步Pre/同步Pre/Pre恢复及压缩/同步Post/
异步Post/异步Stop联合269通过（bb8689，69.46s）；本批六文件ruff检查及
format检查通过（f7e97e），git diff --check通过。未调用真实模型服务。

尚未验收：异步Pre专属冷恢复、重复投递、关闭/配置变更交叉测试；从checkpoint
直接恢复至非prepare节点的反馈边界；Pre/Post/Stop共享会话并发预算；混合
同步/异步配置的完整故障矩阵。269通过不是这些边界已覆盖的证据，也不是
最新全量回归。A–E目标继续开放，CLI保持暂停。

范围A/B/C/E，CLI暂停；不使用官方模型协议。固定Codex基准
ddf04ad26789d040f9ef6a96736f76602e35a6cc。

原生hooks/src/events/pre_tool_use.rs::run经dispatcher::execute_handlers调度，
异步handler由Session command runtime持有，不进入同步聚合；parse_completed
检查can_apply_control_effects，异步不应用block、updated_input或exit2阻断，
合法wire的context/warning仍保留。该文件测试还明确验证异步allow控制无效时
context仍保留。core/hook_runtime.rs的drain与同步通知门控与Post共用。

Corki pre_tool_hooks.bind在匹配可信command后，把asynchronous配置跳过并警告；
未生成其执行记录，也未启动脚本。同步Pre仍走工具参数映射、实际审批和已存在
的反馈恢复；不能因为Post已支持异步就宣称Pre也已实现。

实施要求：在工具handler前完成异步claim与任务接管，而不等待脚本结束；不能
让异步deny/updatedInput绕过实际审批或影响本次工具参数。共享Session生命周期
和安全采样投递边界，但必须区分Pre/Post解析与持久化身份，保持已有同步Pre
执行key/反馈记录兼容，不把未知Pre脚本重放。同步Pre冷恢复不能误用异步控制。
源码wire严格性须另与schema/output_parser对照，不能直接套用Post字段。

先用真实Runtime direct/nested反例证明配置确实执行、工具不等脚本、后续采样
收到context、参数不被后台输出改写；随后冷恢复/关闭/混合匹配/信任变更验收。
目前仅开始该差异的实现前验证，不计为完成。

首次真实Runtime反例：test_async_pre_hooks.py direct/nested两项均失败
（274d39，6.82s），第二次采样等不到Pre脚本启动，符合当前跳过配置的源码。
尚未走到更新参数无效/第三次采样context的断言，不冒称后半链已验证。没有
使用xfail/skip，生产代码本批未改；下一批需实际实现，不能称当前测试全绿。
