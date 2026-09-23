# 记忆关闭与claim所有权复核（D/E）

## 2026-09-22：重置与 OAuth 写入的外层取消复核

检查实际调用链：`LangGraphRuntime.aclose` 创建并 shield 共享 `_close_task`；
`_close_resources` 先 join 活动 Turn，随后在同一关闭任务中等待
`MemoryResetter.aclose`，最后才关闭模型及存储。重置自身把 `_run` 作为
受锁保护的活动任务，`reset` 的取消等待者也必须 join 已开始的重置。
因此 `MemoryResetter.aclose` 单独被外部取消可能返回早，但公开 Runtime
关闭入口不会将该取消转发给共享清理任务；不能据内层单个 await 推断
Runtime 会提前关闭。已有真实 Runtime 文件删除门控测试覆盖关闭等待者
取消/不取消两个场景。

通用 MCP OAuth 的 File 凭据保存虽用 `asyncio.to_thread`，实际刷新入口
`refresh_oauth` 持有并 shield `_transaction` 至完成，且 token 请求限时块
结束后才进入保存；登录入口由 `join_oauth_task` 持有完成任务。未发现
模型服务认证或官方账户请求被该链路引入。本轮定向运行重置关闭双场景
与 `tests/unit/mcp/test_oauth_refresh.py`：**20 passed / 1.42s**，退出 0；
没有生产修改。不将此结果扩大为所有外部存储在进程强杀下的原子性保证。

## 最新：父关闭等待者取消不遗失超时子任务

2026-09-12 再读原生 memories/write/src/start.rs 的后台所有权与 phase2.rs
关闭后才发布的顺序：shutdown 未确认时直接返回并保留 lease，不让新 worker
与旧 worker 竞争。官方账户 rate_limits guard 仍是用户排除项，没有迁入。
Corki Runtime 先等待 memory service 关闭，再关闭主模型；服务先 join job、
settle retained worker，再关自有模型和仓库。aclose 的等待者取消不取消内部
close task；超时 worker 仍由 _RETAINED_OWNERS 持有，不因父服务关闭而丢失。

扩展 test_parent_shutdown_is_bounded_and_late_close_does_not_reclose_owned_model：
真实 parent/child Runtime，child close 挂起到 deadline 后保留，父 service 进入
settle 时暂停并取消外部 aclose 等待者。观察者收到 CancelledError，但主/记忆
模型尚未关闭，工作副本及强所有者仍存在；释放 host gate 后再次 aclose 完成，
两模型各关一次。随后 child 真正关闭，副本清理并移除 retained owner，无晚到
MEMORY.md 发布、不重复关闭借用模型。原不取消场景保留。

shutdown deadline 专项 11 passed（f1358b，8.72 秒），包括完成/取消/心跳失败
与晚到关闭/副本清理失败的原有组合。此轮无生产修改，不把等待者取消等同于
杀掉后台子任务，也不把测试强行释放的 gate 宣称为真实不可恢复挂死已解决。

### D6 所有权证据对应表

| 边界 | 当前接入点 | 行为证据 |
|---|---|---|
| 主循环与后台隔离 | Runtime 启动服务，pipeline 捕获后台错误 | test_memory_pipeline_runtime：生成/召回完整链、心跳故障不终止主 Turn |
| wait 观察者取消 | pipeline.wait 的 shield | 同文件：取消观察者后提取/合并继续，仅原有两次模型调用 |
| 直接运行与服务关闭 | run_once 的独立 job、_tasks、_join_owned | test_memory_direct_run_ownership：1/2 pass、关闭/重复取消/二者同时发生 |
| claim 与引用落库 | handoff_claim、仓库 owned worker、owner token | test_memory_database_ownership：claim 前后、等待并发槽、owner 替换、前台 citation 写入 |
| 子 Runtime 关闭超时 | ConsolidationShutdowns 保留所有者，禁止迟到发布 | test_memory_shutdown_deadline：完成/取消/心跳、晚到关闭或清理失败、父关闭等待者取消 |

该表核验合作式异步生命周期，不承诺进程被强杀后仍运行内存清理回调；崩溃后的
持久 lease/watermark 和发布恢复属于 D3 的独立状态契约。Windows 实际子进程
与终端行为未在本机执行，不用 Python 的可移植结构冒充平台实测。

本轮较大范围刷新：tests/unit/memory 与 tests/integration/test_memory*.py
联合 800 passed（eae122，218.00 秒）。该进程在新增取消参数前收集用例，
新增组合另由上述最终 11 项专项验证，不将两组简单相加为独立测试数。
相关 ruff/format/compileall/77 包依赖/diff 检查通过，测试句柄均已终态。

## 最新补验：直接调用并发与重复取消

test_memory_direct_run_ownership 扩为 1/2 个同时直接 pass × 服务 close/
调用方重复 cancel/两者同时发生，共六例。真实 SQLite claim 在统一 barrier
等待，全部进入后才发取消；barrier 释放前取消等待不返回、模型/数据库均
不关闭。释放后全部调用方收到 CancelledError，服务任务集合为空。
仅取消调用方不关闭服务依赖，后续显式 aclose 才按 model→repository 顺序
关闭；服务关闭后拒绝新 pass。没有新生产修改。

六例专项通过（859dd9，0.56 秒）；与 database ownership、生成/召回及
Runtime shutdown 联合 72 passed（460fb2，16.72 秒），进程 50406 已退出。
静态 src/tests 1140 文件、src compileall、77 包/diff 通过（98f0f8）。
这组是无候选来源的 claim handoff/资源生命周期反例，不能冒充有副作用的
phase-two 发布事务验证；后者仍沿既有 owner fencing/publish 专项核验。

## 最新修复：直接运行的记忆 pass 纳入关闭所有权

公开 run_once 现在在服务内创建独立任务并登记 _tasks；直接调用者取消时
取消并 join 本次工作，关闭服务也会先 cancel/join，再关闭模型/repository。
start 已登记的当前任务复用实际 _run_once，不创建第二层任务；保留 run_once
覆盖点、后台 warning 捕获和 wait 的观察语义。未把外部宿主任务本身登记为
服务子任务，避免等待整个调用方或关闭时相互等待。

新真实 SQLite claim 挂起反例，修复前出现 "model closed before direct claim
settled"（dee589，终态 8ce8d6）。修复后保持关闭 pending，释放 claim barrier
后 run 收到 CancelledError，模型→repository 顺序关闭、无模型采样；关闭后
新 run_once 拒绝。新专项与基础 pipeline 5 passed（810abe）；保留 start 覆盖
语义后的 startup/source/permissions/database ownership/完整生成召回联合
120 passed（aa1039，44.89 秒），全部测试进程已退出。
静态 src/tests 1140 文件、src compileall、77 包依赖/diff 通过（d594b4）。

本批修复直接服务入口的生命周期漏洞，真实主 Runtime 的 start 路径经联合
验证未改变权限、启动条件与 claim 语义。直接调用方重复取消、多次并发直接
运行等仍应按风险补验，不以本结果声称所有 D/E 或 A–F 已完成。
下方“实施前缺口”为本批修复之前的证据。

## 新确认：直接 run_once 的关闭所有权缺口（实施前）

LongTermMemoryService 由 memory.__init__ 公开导出，run_once 是现有可等待入口。
目前 start 将任务注册进 _tasks，但直接 run_once 不注册；aclose 只取消/join
_tasks。因此直接运行尚在 claim/提取时关闭服务，可能先关闭模型和 repository。
此处不是要求原生具有同名 Python 方法：原生 start.rs 后台闭包持有 Arc context，
phase1/phase2 持有对应依赖；Corki 可关闭资源不能早于已经接受的工作结束。
主 Runtime 的 start 路径已有所有权，不应为修复直接入口破坏后台 warning/wait。

修复方案：直接入口也以服务拥有的独立 job 执行并登记，调用方取消应取消并
等待本次工作；aclose 取消/join 所有已接受任务。不能登记整个外部调用方 task，
否则服务会等待与其无关的宿主生命周期；也不能让 close 与调用方相互等待。
保留 start 的非阻塞行为、wait 仅观察语义、关闭后拒绝新工作、claim owner fencing。
先补真实 SQLite claim 挂起反例，再实现并运行现有后台/数据库/关闭回归。

## 最新：等待者取消不取消后台提取，且仍完成真实召回链

重读原生 memories/write/src/start.rs 的后台启动、phase1/phase2 所有权入口，
及 Corki pipeline.py::start/wait/_capture_background_failure/aclose/_close_resources。
原生官方 quota guard 仍是明确排除项，不复制。Corki wait 使用 shield 观察
已接受任务；Runtime 关闭才取消并 join 后台任务，再关闭其依赖。

test_memory_pipeline_runtime 的完整生成/召回测试扩充 cancel_waiter 两值：
三种历史终态 × generate 两值新增六例。在真实提取模型等待期间取消独立
wait task，验证 CancelledError 到达等待者、后台 task 不完成/不处于 cancelling、
无第二次采样；释放后仍正常提取合并。保留原 summary→search→read→citation
全部断言、模型请求次数、使用反馈与持久引用检查。主 Turn 不受观察者取消影响。
这不是取消 memory job 或 Runtime 的测试，后两者由原 ownership/shutdown
专项覆盖；不以观察者的取消替代关闭期间的 claim fencing 证据。

pipeline Runtime、真实数据库 ownership、Runtime shutdown 联合 66 passed
（c8a117，16.42 秒），session 95410 已退出 0。静态 src/tests 1139 文件、
src compileall、77 包/diff 通过（6660e3）。本批无生产修改，新组合未暴露缺陷；
不由此关闭全部 D/E。下方为先前关闭/claim 专项证据。

Codex基线 memories/write/src/start.rs::start_memories_startup_task 对ephemeral/非root/
功能关闭做门控，tokio::spawn后台跑phase1/phase2。phase1.rs从state db claim，完成或
失败携带ownership_token。guard.rs的CodexBackend账户配额请求是用户明确排除项，
本轮只阅读以标清边界，不引入认证/配额或官方服务请求。

Corki Runtime._close_resources在storage关闭前等待MemoryService.aclose；pipeline
_close_resources取消并_join_owned后台任务，再settle worker、关闭memory model和repository。
SQLiteMemoryRepository写入本身已有join和owner fencing；本轮不改此生产实现。
独立Runtime的资源所有权不是把原生detached task逐行移植；验收是任务不越过其数据库
生命周期，外部等待者取消不能造成早关库或清除其他owner的claim。

扩展test_memory_database_ownership，叠加取消第一aclose等待者并再次aclose；真实
SQLite同步worker在claim前/后暂停，分别覆盖提取/合并、owner被替换、claim异常、
失败记录异常、commit成功但响应丢失。保留原有retry时间及warning断言，禁止取消
后尚未dispatch的claim触发模型采样。

此项不等于全部记忆生命周期完成，也不把永久未知commit写成成功释放。

32718终态60通过12.31秒（99d523），组合memory_database_ownership、
memory_pipeline_runtime和runtime_shutdown。静态1085文件77包通过（6f2455）。
本轮有新增取消组合证据，无生产变更；当前无活动测试。
