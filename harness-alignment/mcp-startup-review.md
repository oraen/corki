# MCP 启动入场与部分注册清理

## 公开同步嵌入接口：待确认兼容迁移边界

2026-09-12再次核对construction.synchronous_rollback：检测到运行中loop且没有
显式_construction owner时直接调用factory，异常后局部closers没有接管。这是已知
公开入口缺口，不因正式CLI与memory使用异步owner而消失。当前src/examples/tests
共有693处直接LangGraphRuntime.create文本调用（不等于全部在async内）；src唯一
调用为cli/main.py::build_application，正式异步入口传入明确的_construction。

不采用嵌套事件循环、在另一线程关闭可能绑定当前loop的资源，或无所有者后台task
来声称失败返回前已回滚。直接禁止在运行中loop调用同步create可以在分配前失败，
但会改变现有公开宿主API，需要明确兼容迁移选择；相应项目内异步调用应迁移到
await acreate，无loop同步调用继续保留。此轮未做该破坏性API变更，等待用户确认。
这不影响已有正式异步构造路径，亦不将整个A–F目标标记完成或阻塞。

## 同步宿主无事件循环的构造失败（后续实施）

后续补证：新增同步模型关闭OSError/CancelledError组合，确认逆序清理仍到达最先
登记的ProcessManager，原异常保留或作为取消cause，借入模型不参与回滚。新增成功
同步构造→真实Turn→重复aclose，确认组装时仍无运行中loop，模型恰好关闭一次。
构造/关闭/CLI构造联合42通过（f50cf7，3.37秒），不是新的生产先红后绿缺陷。
调用点核验：src唯一直接create位于CLI build_application，异步正式入口传入
_construction，已由create_owned拥有。三个examples在async _run中直接create，
现迁移为await acreate，避免演示继续推广没有失败清理owner的调用方式。宿主自己
在运行中loop直接调用create的公开兼容入口仍存在，尚未宣称该整体边界已关闭。
迁移后三个真实示例E2E及构造测试20通过（3cbebc，5.16秒）；示例组合包含真实
工具/多Step/后续Turn，但ScriptedModel不证明真实模型质量。src/tests/examples的
ruff、1179文件格式及compileall通过（e71520），77依赖和diff检查也通过。

当前create的_construction缺省时仅创建局部closers，失败后没有调用者接管。
异步acreate/create_owned已等待回滚；本项补同步宿主没有运行中loop的调用场景。
沿用上文原生async Session初始化的失败等待要求，不复制官方认证/预热。
方案：同步包装器仅在无loop且无显式_construction时登记清理，失败后在临时loop
等待同一逆序清理逻辑；成功路径不运行临时loop，不改变资源初始化的事件循环。
有运行中loop的同步嵌入仍需使用acreate，不能用阻塞嵌套loop或无主task冒充已清理。
验收：真实Runtime后期构造普通错误/取消、自建与借入模型、registry回滚、恰好关闭。

已实施construction.synchronous_rollback包装Runtime.create；显式_construction仍由
已有异步/CLI所有者接管，不重复回滚。逆序等待收敛为共享_finish_rollback，保留
清理错误附注及取消优先级。真实CorkiGraph组装故障的四组合修复前2失败/2通过
（f95333），自建模型未关闭；修复后构造、CLI构造、初始化/关闭联合49通过
（f96e57，4.38秒）。测试同时断言成功进入组装前未偷偷启动事件循环、错误对象
保持、借入模型不关闭、宿主registry恢复。不是所有同步嵌入场景均已关闭。
全项目ruff/格式、compileall、77依赖及diff检查通过。一次回归命令误写不存在的
test_runtime_construction.py，未运行测试；已更正为当前存在的测试文件后取得上述结果。
扩大插件重载及真实CLI启动清理Ctrl+C PTY后77通过（208aaa，9.55秒），4条forkpty
多线程弃用警告；未跳过。此PTY检验共享异步清理未回归，不冒称新同步分支的物理
信号专项测试。下一步仍需解决/明确运行中loop同步嵌入的失败资源接管。

## CLI 启动清理期间物理 Ctrl+C（F/A/E，局部补证）

新增 tests/e2e/test_cli_startup_cancel_pty.py 运行真实 cli.main、TerminalUI、
Runtime.create 和插件加载，替换模型构造为显式 ValueError/CancelledError。
插件 aclose 保持挂起，父进程用 PTY sendcontrol("c") 产生 SIGINT；子进程
确认 main task.cancelling 后发送确认标记，并继续等待独立释放文件。父进程
确认子进程仍活跃且关闭标记尚未落盘，再放行，最后严格验证插件已关闭且
进程退出码 130。没有用直接 task.cancel 冒充物理终端输入。

40/100 列 × 原错误/取消四例通过（daf5cf：4 passed，0 skipped，4.58 秒）。
本轮无需修改生产逻辑；这不是“修复前失败”的新生产缺陷。窗口只覆盖已进入
构造回滚后的一次物理 Ctrl+C，不承诺构造任意指令点或连续多次 SIGINT/强制
终止的清理。同步嵌入资源等待和完整 A–F 仍未完成。

CLI 单元、构造/内部末端/流清理、该新 PTY 与 Plan 提交取消 PTY 联合
403 passed，0 skipped，8 条 forkpty 警告，19.28 秒（e0ed63）；静态通过
（d61d43：1133 文件、77 包）。所有测试进程退出 0，无活动测试。

## Runtime.__init__ 当前资源分配补证（A/E）

完整追踪 Runtime.__init__ 到 CorkiGraph 及末尾 MCP reload 绑定。现有组件的
初始化状态如下，不因存在 aclose 方法就推断构造时已分配活跃外部资源：

- storage/thread_writer.py::ThreadWriterLease 只创建 coordinator/Lock；acquire
  才打开并持有锁文件。storage/thread_archive.py 的 archive store 只保存路径。
- history_notes/local.py → NotesStore.__init__ 只保存 repository/path/thread；
  NotesStore.call 才创建文件，用 closing(connection) 持有操作级连接。
- code_mode/service.py::__init__ 只创建空 cells/events 和检查包可用性，执行器
  不在构造时启动。core/step_settings.py 的 controller 只有空 tasks/Lock。
- MCPPrewarm.__init__ 只有 Event/弱引用，start 才创建后台任务；memory/pipeline.py
  的 LongTermMemoryService 构造也只保存状态，start/run 才开启任务。
- ContextWindowManager/CorkiGraph 只组装配置、引用和视图；未在构造末端开启
  checkpoint。真实活跃 RAM 连接是 create 在 __init__ 前创建的 volatile
  repository，现有 except 已调用 _close，不需要再重复登记同一连接关闭。

新增 test_runtime_constructor_rollback.py：在实际 __init__ 的 CorkiGraph
组装点注入普通异常/取消 × 持久/ephemeral，启用本地 history notes/token
budget，保留真实内部组件。验证无新增 asyncio 任务、无 Code Mode cell、
writer 未持锁、无 notes/checkpoint/锁文件，自建模型关闭、宿主注册表恢复；
ephemeral 连接再次 SELECT 明确报 closed，且没有数据库文件。四例通过
（293fe4），本轮没有为这些惰性组件增加多余关闭代码，也没有生产先红后绿记录。

该证据关闭“当前 __init__ 内部必然另有活跃资源未接管”的未证实推断；不承诺
恶意替换构造器、任意宿主副作用或之后新加入组件。同步嵌入资源等待及 CLI
新启动窗口的物理取消仍开放；完整 A–F 验收不因这四项而完成。

随后构造/初始化/关闭、CLI 构造、记忆 agent 和插件重载联合 86 passed，
0 skipped，15.13 秒（befeca）。静态全部通过（612d42：1132 文件、77 包），
所有测试进程退出 0，无活动测试。未改变普通模型或通用 MCP OAuth 路径。

## 宿主注册表构造失败回滚（A/B/E，已修复该边界）

原生 core/src/tools/registry.rs 的 ToolRegistry 持有按 Step 注册的 runtime，
不是向调用者借入一个 Python 可变注册表；Corki 的 host registry 是自身接口。
此前已跟踪的 prepare_external / PluginReload 只保证单次候选发布，无法撤销
Runtime.create 在插件发布成功后、后期构造失败的全部组成。因此即使 acreate
关闭模块，宿主仍可 get 到已关闭插件的 handler，且配置策略/新 owner 可残留。

修复方案：仅对传入的 registry，用同步 composition 事务包住 create 的整个
组装；成功保留发布，异常/控制流立即恢复工具映射、owner 能力、外部候选、
seal/策略/快照状态，随后才进入异步资源清理。不得等异步 close 后再全量恢复，
否则会覆盖宿主在清理挂起期间的新注册。本注册表本来要求单事件循环同步发布，
此事务同样不承诺跨线程变更或回滚第三方 handler 内部任意可变副作用。

已实施 ToolRegistry.composition 与 Runtime.create 的 rollback_registry 包装。
只对显式借入 registry 保留原始状态；正常返回不撤销，BaseException 同步恢复
后继续传播。新增 sync/async 两个真实 Runtime 后期故障场景修复前均暴露插件
残留（06f008）；修复后验证原 handler identity 与定义保留，异步关闭挂起时
宿主仍可用原 owner 追加 later，清理完成后 later 不丢失。同步测试显式接管
清理列表释放 fixture 模块，不能误称同步 create 已有自动异步资源释放。

四项注册表事务测试覆盖普通异常/取消 × 原未封存/已封存状态，检查工具映射、
owner 名称、策略 identity、封存状态及缓存 snapshot 恢复。插件/外部注册表/
Runtime 初始化关闭/CLI 构造和六个核心组合文件联合 380 passed、0 skipped，
24.42 秒（70d29b）。静态通过（92c6b1：1131 文件、77 包），无活动测试。
剩余构造工作聚焦 cls 内部资源分配、同步嵌入 API 的资源所有者和新 CLI 启动
物理取消；不再把本项注册残留当作尚未修复，也不据此宣称 A–F 全部完成。

## CLI 构造等待边界（已接入，仍有未验收边界）

原生 tui/src/lib.rs::run_main await startup_orchestration::run_main_inner，
将 StartupCancelled 映射为用户退出；不要求复制官方认证/prewarm 路径。
Corki cli/main.py 当前在 asyncio.run 之前同步 build_application：即使 Runtime
已有 acreate，CLI 构造失败仍无法等待插件清理。方案是在同一 asyncio.run 中
先 await 构造再运行 Application，用现有 create_owned 包裹 build_application，
将同一清理列表传递到 Runtime.create。CLI 自建 repository 也先登记；Runtime
完整返回后改由其 aclose 接管，避免 Application 构造失败时重复逐组件关闭。
保留同步 build_application 调用形式，明确它不是新的等待清理入口。

验收要求：真实 CLI main 在插件加载后模型构造错误/取消，异步插件关闭完成后
才返回错误/取消终态；正常启动和采样在同一 loop 中，成功后仅正常关闭一次。
配置 ValueError 仍只在启动阶段映射为参数错误，不改变运行中错误类型。

已实施 build_application_async → create_owned(build_application)；main 的单个
asyncio.run 先等待构造、再运行 Application。CLI 自建 repository 登记在同一
列表，Runtime 返回后列表转交 runtime.aclose，覆盖 Application 构造再失败。
startup CancelledError/KeyboardInterrupt 在清理完成后返回 130；运行中的异常
仍由原 Application 路径负责，不统一转换为 parser.error。

新增真实 main + Runtime 场景最初三项均失败（9e0f7b：无运行 loop、错误不关闭
插件、取消逃出入口）；修复后 CLI/恢复设置/managed MCP/流清理联合 437 passed，
0 skipped，15.15 秒（ddbc0c）。最后增加运行中 ValueError 边界后的新文件五项
通过（81851b），包括 Application 构造失败后完整 Runtime 恰好关闭一次。
静态通过（427688：1131 文件、77 包），所有进程退出 0。

新测试替换 TerminalUI 和交互 run，以真实主入口及 Runtime 验证所有权，未声称
本批是物理 PTY Ctrl+C 证据。同步 build_application/create 嵌入调用仍不具备
失败等待能力；宿主 registry 后期失败回滚及 cls 内部分配仍开放。生产 CLI
已不再属于“完全没有构造等待”的路径，不能重复按旧清单实现。

## 已接入：可等待构造入口及记忆子 Runtime（部分一致）

按上述原生 async Session 初始化/失败等待边界，新增 acreate，复用 create 的
真实组装而非复制第二套 Runtime。构造期间按分配顺序登记 Runtime 自建资源，
失败逆序清理并等待终态，再传播原错误；外部重复取消不能打断清理。宿主传入的
model/repository/memory_repository 在构造成功前不转移所有权。插件 manager
必须在 discover_and_load 执行代码前登记，否则插件控制流失败仍丢失 owner。

先接已有异步 memory/agent.py 调用方，保留同步 create 的签名兼容。验收覆盖
插件首次取消、清理挂起时重复取消、后期构造失败关闭自建模型且不关闭借入模型、
成功构造正常运行关闭。此批不关闭全部差异：同步 CLI/嵌入入口迁移、宿主注册表
在后期构造失败的整体回滚及 cls 内部资源分配仍需检查，不把新入口当成全局修复。

实现位于 core/construction.py::create_owned → LangGraphRuntime.acreate →
原 create。登记自建 process manager、会话/记忆 repository、插件/MCP manager
和自建模型，既有 volatile 创建失败关闭保留。PluginManager 的 on_created
在执行插件前交出所有者。成功时不执行回滚；memory/agent.py 已真实使用 acreate。
失败清理逆序逐项尝试，错误类型附注到原异常；清理自身取消或父等待者取消均在
join 完成后传播取消，不把取消仅作为 warning。没有使用半初始化 Runtime.aclose。

新增真实 Runtime 测试覆盖首次插件 register 取消、关闭挂起时两次取消、后期
cls 构造失败的自建/借入模型 × 正常关闭/关闭错误/关闭取消，并确认后续插件
关闭未跳过。新入口不是既有 API 缺陷的原地回归，因此不宣称本批有生产先红
后绿记录。最终插件、初始化、关闭及 memory agent/context/pipeline 联合
286 passed，0 skipped，24.69 秒（e9402e），已覆盖实际记忆子 Runtime 正常
运行和关闭。静态全部通过（3bc121：1130 文件、77 包）；所有测试已退出 0。

## 构造前置校验：自定义存储与长期记忆（A/D/E）

当前 Runtime.create 同步执行插件后才验证 memories_enabled + 自定义非 SQLite
会话存储是否提供 memory_repository。这个纯配置错误不依赖插件，却已允许模块
执行和工具发布，随后抛 ValueError 且没有返回 Runtime 可供关闭。原生 Session
构造是 async，session_result Err 会 await live_thread_init.discard；该 guard
仅负责持久化清理，不应假定它清理所有连接或为 Corki 的同步 Python 钩子兜底。

本项先把可静态判断的上述存储约束移到任何资源/插件创建之前，保持错误类型和
信息不变，并用真实 create 与可执行插件验证无注册副作用。优先级高，状态待
修复。这只是避免一个确定的错误创建窗口，不替代完整构造所有权设计：create
还可能在插件自身、技能/MCP/上下文、模型/记忆服务及 cls 构造时失败；当前最后
的 try 仅关闭新建 VolatileSessionRepository。同步 API 不能在正在运行的事件
循环中直接 await 异步清理，不能以无所有者后台任务或吞取消来宣称整体已修复。

已实施前置校验：新增 test_invalid_memory_storage_is_rejected_before_executing_plugins
修复前失败于插件实际执行 register（131c88），修复后确认没有注册副作用或
工具发布。插件/Runtime 初始化关闭/记忆主链联合 257 passed，0 skipped，
10.55 秒（c2c8f9）；记忆读取策略与数据库所有权另 48 passed，0 skipped，
7.60 秒（4735cd）。后者包含合法自定义会话存储 + 显式记忆存储的真实 Turn，
证明前置校验未阻断支持的组合。静态通过（ac1715），无活动测试。

后续整体接管的实际调用边界已确认：CLI main.py::build_application 在同步
入口调用 create；memory/agent.py::_run_prepared_agent 在异步 try 中调用，
但 runtime 只有返回后才赋值，构造失败无法进入 runtime shutdown。生产源码
中的直接调用者为这两处；嵌入宿主与测试也使用公开 create，不能只为 CLI 添加
特殊清理就判完成。下一步需要同时安排可等待构造所有权和同步公开接口的兼容
边界，并逐个登记实际资源，不能复用未完整初始化对象的 aclose。

## 新确认：entrypoint 执行前的资源所有权缺口（E，高优先级）

重新追踪原生 core-plugins/src/manager.rs::plugins_for_config → loader.rs::
load_plugins_from_layer_stack_with_scope：逐包保留加载结果并记录错误。原生不执行
Corki 的可信 Python register 回调，官方身份/远程目录分支不在对齐范围。
Corki Runtime.create → discover_and_load → prepare_reload → _load_entrypoint
在模块 exec 成功且函数可调用之后才将 module 加入清理列表。若模块已建立资源、
定义 aclose 后抛错，或者注册函数缺失，_load_entrypoint 仅移除 sys.modules，
manager 最终关闭也找不到该模块。这是资源所有权丢失，不是正常包级降级。

修复方案：模块创建后、执行任何模块代码前即登记到本批次的模块所有权列表；
保持原异常/取消传播和不发布失败工具。验收采用真实 Runtime 首次加载普通失败
以及存活 Runtime 重载取消，关闭时逐代调用已定义的 aclose 恰好一次。
同步 Runtime.create 自身抛出控制流异常时整体资源接管仍独立开放；本项不声称
能够清理未提供钩子或在钩子定义前失败的任意可信 Python 副作用。

已实施：_load_entrypoint 接收调用批次的 modules 列表，在 exec 前登记；正常
注册不重复登记，模块 exec / 非 callable 失败仍撤出 sys.modules。普通错误
经候选发布后由 Runtime manager 持有；重载控制流失败由 prepare_reload 已有
回收分支接管。新增两例首次加载失败及原取消测试的 module 阶段参数，修复前
三例失败（efd98f：失败模块零次关闭、取消只关闭旧代）；修复后 plugins、
external registry、Runtime shutdown 共 250 passed，0 skipped，2.87 秒
（e5274f）。测试也确认健康兄弟保留、失败工具不发布、旧快照不变和重复关闭
不重复执行钩子。静态通过（12e996）；未复制任何原生账户或远程插件路径。

修复后另跑六文件组合（文件同下方并发 abort 节列出的核心组合）：95 passed，
0 skipped，18.43 秒（aee980），验证普通函数搜索/定义加载、降窗压缩、记忆及
恢复错误场景。与上述 250 项为两次运行，不计作一次全仓验收；所有进程退出 0。

## 并发 abort 的候选所有权补查

manager 已共享关闭任务，但 PluginReload.abort 每次创建不同临时 manager，
第二个调用拿到空 modules 后可在第一次关闭完成前返回。将候选持有的关闭
manager 缓存并复用，可令每个等待者都等待同一次真实清理。该要求来自 E 的
关闭所有权和终态约束；原生无 Python PluginReload 接口，不能宣称逐行对应。
新增 manager/candidate 两分支挂起 close 的并发测试，同时验证重复关闭只调用
一次钩子。此处修补上一轮关闭实现的调用者边界，不扩展官方插件功能。

已修复：PluginReload 保留 _abort_manager。两分支测试修复前 manager 通过、
candidate 提前返回失败（92e8a5），修复后通过。相关插件/注册/关闭与六文件
核心组合共 342 passed、0 skipped（1707d6，21.33 秒，session 31550 退出 0）。
组合文件为 test_deferred_tool_search、test_model_search_compaction、
test_compaction_wire_contract、test_memory_pipeline_runtime、test_search_error_recovery、
test_recovery_and_concurrency，覆盖普通搜索定义加载/回灌、降窗压缩、记忆及
恢复错误路径；不等于所有 A–F 或真实模型工具选择质量的验收。
全部静态通过（6fc3d5，1125 文件、77 包），无活动测试。

## 已修复：候选 abort 的外部取消打断关闭（E，局部验收）

真实 Runtime _prepare_configuration_reload 拒绝候选后 await plan.abort，后者
调用临时 PluginManager.aclose。当前 aclose 直接 await 模块 close；外部取消
会中止 close，捕获异常后丢掉模块列表并返回取消，不能再恢复尚未完成的清理。
新测试挂住候选模块关闭并取消父任务，2c5f00 直接证明父任务提前终止。

方案：manager 持有单一关闭任务，shield 并 join，记录外部取消但待所有关闭
结束再传播；并发/重复关闭复用同一任务。模块自身关闭错误仍逐项收集，不把
失败当成功。此修复遵循目标 E 的取消/所有权约束，不声称原生具有 Python
aclose 回调；原生插件加载路径及产品排除边界见下文。

已将 PluginManager.aclose 拆为持有任务的等待层和 _close_modules；关闭期间
禁止再发布，外部重复取消不进入模块钩子，收回任务结果后继续传播取消。新增
test_runtime_candidate_abort_joins_cleanup_after_repeated_cancel 在真实 Runtime
拒绝候选后挂起模块清理，连续两次取消父任务均不能提前结束，释放后确认清理
一次、旧 handler 未替换、取消终态保留。修复前 2c5f00 失败，修复后相关
245 passed、0 skipped（2d65c6，2.88 秒），静态全部通过（eb7e11）。
这里的“等待清理”不承诺恶意或永不返回的可信 Python 钩子可以被安全强制终止。

额外 Runtime 配置重载 code_failure / slow_close 两场景跨三种工具模式和两种
服务器名称，12 passed、180 deselected（029562，4.57 秒）。两批进程均退出
0，无活动测试；没有通过这项局部修复关闭初始同步构造资源接管或整个 A–F。

## 已修复：重载 register 取消丢失模块所有权（E，局部验收）

真实 Runtime 的 _prepare_configuration_reload 在调用 prepare_reload 返回前还
没有 PluginReload 可 abort。manager 的 register BaseException 分支仅从
sys.modules 删除当前批次模块并传播异常，没有把模块交还 manager；因此 Runtime
关闭也无法调用新模块及已准备兄弟模块的 aclose。已有工具快照虽未变，资源
所有权却已丢失。新增真实 Runtime 回归要求原取消继续传播、旧快照不变，并在
Runtime 关闭时恰好关闭旧模块、新失败模块及先前新兄弟模块。

修复方案：与 prepare_external 失败路径保持同样所有权规则，在传播注册控制流
异常前将本批次模块交回现有 manager。此处只修复已存在 Runtime 的重载路径；
同步 discover_and_load 本身构造失败时异步资源的接管仍需独立处理，不据此
宣称全部初始化取消完成。原生没有该 Python 回调接口，参考的核心约束是上文
已追踪的失败不发布和所有者负责清理，不引入官方插件身份或服务路径。

已实施上述所有权移交。新增 test_runtime_owns_new_modules_when_registration_propagates_cancellation
直接调用实际 Runtime 配置重载入口，验证取消传播和完整关闭。初版断言错误地
要求未 seal 注册表的 snapshot 对象 identity 相同（cb1019），改为定义及 handler
身份后，修复前仍失败于只有一次 close 而非两次（fab8fb），确认不是测试假阳性。
修复后 plugins 全单元、external registry 与 Runtime shutdown 联合 244 passed，
0 skipped，2.85 秒（17b62f，session 1708 退出 0）。全部静态检查通过
（4575ff：1125 文件、77 包）；无活动测试。没有改变普通异常降级或候选发布规则。

本轮覆盖 A/B/E 的 required MCP 启动失败、取消和重试，不代表所有扩展初始化
已验收。仅增强测试，无生产代码变更；不引入官方认证或服务调用。

## 原生调用链

参考仓库 codex-rs/core/src/session/session.rs 的 Session 初始化：
install_initial_mcp_runtime().await? 成功后才启动 prewarm、记录初始历史并返回
Session；失败进入 live_thread_init.discard().await。这里是会话初始化失败，
不是一个已入场 Turn 的成功终态。

core/src/session/mcp_runtime.rs 的 install_initial_mcp_runtime 先发布 MCP runtime，
再调用 codex-mcp/src/runtime.rs::validate_required_servers，最终到
connection_manager/required.rs::McpConnectionSet::validate_required_servers。
先发布是为了等待期间能处理 startup elicitation，不是保证所有连接均成功。
校验聚合 required 服务器失败；有缓存工具的 dormant 连接免于立即启动。
上述路径中的官方 auth、prewarm 产品依赖不是 Corki 要复制的功能。

## Corki 对应语义及差异

core/runtime.py::_ensure_ready / _initialize 在入场前初始化。异常或取消时，
调用 MCP rollback_startup，撤销指令启动快照并关闭写入器，再传播原异常。
mcp/manager.py::start_session 在捕获连接后验证 required failures。
_publish_preparation 通过 replace_owned 完成注册验证后同步发布 registry 与
manager 快照；失败服务器没有可调用工具定义。聚合 resource helper 仍可能
存在，不能把它误认为失败服务器自己的工具泄漏。

rollback_startup → _dispose_preparation → _close_preparation 取消并 join 未完成
任务，关闭 generation 拥有但未发布的连接；已绑定旧 Step 的 generation 由
最后一个 binding 清理。成功发布的健康连接可留给重试复用。

状态：入场阻断和失败工具隔离有直接证据；对象生命周期不是逐项相同。
原生 Session 构造失败，Corki 同一未关闭 Runtime 可以重试。当前测试证明此
差异不会把失败输入送入模型或重放 tools/call；没有声称原生也复用同一对象。
原生 MCP 的全部资源关闭链及动态扩展注册失败仍需后续逐项核验。

### 后续：discard 的准确边界与 Python 注册隔离

继续追踪 thread-store/src/live_thread.rs::LiveThreadInitGuard::discard →
LiveThread::discard → local/live_writer.rs::discard_thread：删除 pending metadata
及 live recorder，不强制把惰性内存历史落盘。Guard Drop 会在 Tokio runtime 上
安排同一清理。这是持久化清理，不是 MCP 连接或所有 Session 资源的总关闭入口；
不能用此调用证明 MCP 已全部 join。

Corki plugins/manager.py::prepare_reload 为新代码创建隔离 ToolRegistry，
普通 register 异常撤销 registrar 并生成 warning；候选只发布成功组件。
prepare_external 先准备注册快照，PluginReload.publish 验证 generation 后
发布；旧 Step 的 registry snapshot 仍持有旧 handler。失败模块保留在候选的
模块所有权列表中，abort 或 manager.aclose 负责关闭，并非立即释放一切资源。
该 Python 回调扩展是 Corki 自身接口，不把原生插件的官方 auth 或产品接口复制
过来，也不凭这个测试宣称原生存在相同回调生命周期。

原生插件加载另由 core/src/plugins/mod.rs 接入 core-plugins/manager.rs 的
plugins_for_config → loader.rs::load_plugins_from_layer_stack →
load_plugins_from_layer_stack_with_scope：按配置名排序逐包加载，将加载结果
保留并记录包级 error；不能把 Corki 的可信 Python register 回调当成该原生
路径的直接复刻。原生缓存中的官方身份刷新及远程安装目录分支属于排除范围。

新增 tests/unit/plugins/test_reload.py 的失败替换测试覆盖：准备期间旧快照不变，
发布后失败包没有半成品工具，健康兄弟 handler 身份不变；旧快照仍返回旧结果；
修复后可调用新结果，健康包只注册一次，最终三个失败/成功代码 generation 均
关闭一次。实际 Runtime 的配置重载集成另验证 code_failure 不产生新版本结果，
并保留已入场旧版本调用和关闭所有模块；两类证据不混为同一个测试。

本轮验证：`PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring .venv/bin/pytest
-o addopts='' -q -ra tests/unit/plugins/test_reload.py
tests/unit/tools/test_external_registry.py tests/integration/test_plugin_config_reload.py
-k 'code_failure or reload or registry' --tb=short`。筛选也匹配文件名，因此实际
运行完整这三个文件，而非只有 code_failure。session 76328 已终态退出 0，
ffc247：221 passed，0 skipped，98.10 秒。静态 3dd021 全部通过（1125 文件、
77 包）。无生产修改，无活动测试；尚未把所有扩展取消/关闭边界判为完成。

## 行为证据

tests/integration/test_mcp_required_startup.py 中真实 Runtime + HTTP MCP mock：
required 失败两次均无模型请求、无用户历史入场；增强断言确认健康工具存在、
失败服务器工具不存在、第二次失败注册集合不变且健康连接尚未关闭。移除
故障后仅 accepted 输入采样一次，健康 initialize 仅一次，失败服务器三次；
最终关闭全部客户端。另覆盖 optional 降级与 disabled 不连接。

联合 test_runtime_initialization.py、test_mcp_parallel_startup.py、
test_search_cache_runtime.py 覆盖初始化挂起的取消/重复取消/stop/close，
迟到依赖完成后的清理，以及并行启动、缓存恢复路径。

验证命令：

```sh
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring .venv/bin/pytest -o addopts='' -q -ra tests/integration/test_mcp_required_startup.py tests/integration/test_runtime_initialization.py tests/integration/test_mcp_parallel_startup.py tests/integration/test_search_cache_runtime.py --tb=short
```

上轮 session 37501 本轮返回 Unknown process id，无可回收的终态输出，未计作
通过。确认句柄缺失后启动新批次 86884，终态 0a0b9e：45 passed，0 skipped，
3.50 秒，退出 0。静态 161667：Ruff、1125 文件格式、compileall、77 包依赖及
git diff --check 均通过。没有活动测试。完整 A–F 目标仍未完成。
