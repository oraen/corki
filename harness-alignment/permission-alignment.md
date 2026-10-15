# 权限继承与执行：当前源码对照

状态：未实现完整对齐。这是原目标 A/B/D/E 的依赖审计，不是另一个缩小后的目标。
参考 Codex commit：`ddf04ad26789d040f9ef6a96736f76602e35a6cc`；参考仓库保持只读。

## 已确认的调用链

1. Codex `app-server/src/request_processors/turn_processor.rs:615–681` 先构造
   Thread settings overrides，再提交输入。仅当有输入、确实启动新 Turn 且主环境
   已配置时，启动记忆流水线。它分别传递 `thread.config().await` 和当前
   `config_snapshot.permission_profile`。不能据此将所有工作者设置都替换成 Step 设置，
   也不能把权限固定为初次创建 Runtime 时的配置。
2. `memories/write/src/start.rs:24–81` 完成 ephemeral、MemoryTool、非根来源及
   state DB 门控；维护和 quota 检查后执行 phase1，再把同一份父权限传给 phase2。
3. `memories/write/src/phase2.rs:49–103` 先 claim 全局任务、准备工作区，再建立工作者配置。
   配置失败会记录 `failed_sandbox_policy`，不会静默继续执行无约束工作者。
4. `phase2.rs:311–360` 的 `agent::get_config` 克隆基础配置，但强制内部工作者
   ephemeral、不生成/使用记忆、不通知用户、无 MCP、审批 Never，并关闭递归委派及
   Apps/Plugins/SkillMcpDependencyInstall 等功能。父权限的转换见下表。
5. 权限不是只进入提示词。`core/src/session/turn_context.rs:120–149` 将环境拥有的
   权限、cwd、workspace roots、home、临时目录及执行后端设置组成
   `FileSystemSandboxContext`；额外授权合并仍保留原 enforcement 类型。

| 父权限 | Codex 记忆工作者 | Corki 当前证据 |
| --- | --- | --- |
| Disabled | 保持 Disabled，不增加 Codex 管理的外层 sandbox | 无全局权限类型；现有 shell 启动没有 sandbox 转换，不能因此宣称整个权限实现一致 |
| External | 保持外部执行方的隔离责任和 network 值 | 没有可传递的外部权限身份/契约；不能自行改写为 Disabled 或 Managed |
| Managed | memory root 可写、网络关闭，禁用 TMPDIR 与 `/tmp` 的默认例外；审批 Never | 临时副本和固定 cwd 存在，但没有对应的 OS 执行约束 |

`memories/write/src/phase2_sandbox_tests.rs:14–70` 明确测试这三条分支。
这里只阅读了原生测试，没有运行 Codex Rust 测试。

## 不可混淆的执行层

- `protocol/src/models.rs:419` 的 PermissionProfile 是实际 enforcement 与具体权限；
  ActivePermissionProfile 只是配置身份，不可用名称替代实际权限。
- `protocol/src/permission_profile_snapshot.rs` 把具体权限、身份和 profile roots 原子绑定。
  profile roots 与本 Turn 的 runtime workspace roots 是不同数据。
- `sandboxing/src/manager.rs:317–354` 区分“请求需要 sandbox”与“选中哪个平台后端”。
  `SandboxType::None` 不足以证明没有约束，远端 executor 可以拥有 enforcement。
- `sandboxing/src/policy_transforms.rs:630–665` 合并额外权限时保留 enforcement；
  External + restricted network 不会仅因 network restricted 而自动重建本地 sandbox。
  managed-network requirements 是另一条需要单独处理的路径。
- `core/src/tools/runtimes/apply_patch.rs:83–113,169–205` 给所选环境的文件系统传递
  显式 sandbox context，而不是只包装 shell 命令。
- `exec-server/src/sandboxed_file_system.rs:45–165` 将 canonicalize/read/write 等操作
  交给受约束的文件系统 helper；因此只在 Python 路径字符串上检查前缀不等价。

## 接入前的 Corki 缺口（第178批修复前）

`core/runtime.py:1059–1065` 在新 Turn 持久化准入后调用
`LongTermMemoryService.start(thread_id)`；`memory/pipeline.py:145` 创建独立 pass，
`_consolidate` 调用 `memory/agent.py::run_agent`，目前未捕获或传递有效父权限快照。
工作者确实使用真实 LangGraphRuntime，且已有生命周期、输入副本和发布校验；这些机制
不能替代执行约束。

`config/settings.py:89` 明确说明 `mcp_approval_policy` 只负责 MCP 用户审查。
`tools/base.py::ToolContext` 没有权限快照；`core/graph.py` 的普通与 Code Mode 嵌套
dispatch 都只传 cwd、shell、上下文/媒体配置等信息。
`tools/builtin/process.py::_spawn` 直接以 shell argv 启动子进程；
`tools/builtin/patch.py` 在宿主线程中直接读写文件。
不能把已有 MCP 托管规则、目录来源准入或 patch 的 cwd 限制称为全局权限 enforcement。

## 修复顺序与验收合同

当前证据排除了“只给 memory settings 加一个 sandbox 布尔值”的方案。
下一批必须形成接入实际执行的纵向实现，不能只提交以下类型的空接口：

1. 在配置/宿主准入边界解析并验证权限，捕获不可变的有效权限快照，明确配置身份与 roots。
   实际默认配置选择、托管约束、已有 checkpoint 的兼容规则仍须继续追踪原生源码，
   不能从 PermissionProfile 枚举的 Default 推断完整产品默认行为。
2. 将同一快照送到实际 shell/文件系统执行边界；普通调用与 Code Mode 必须共享约束。
   需要同时覆盖后端不可用、远端拥有 sandbox、额外授权以及失败不自动提权的行为。
3. 新 Turn 接纳后捕获父权限，随该次后台 pass 传递，按三分支生成工作者权限。
   后续 Turn 更新不能偷换已启动 pass 的权限；不能用可变共享属性传递它。
4. 通过真实短命子进程和临时文件验证：允许范围内写入成功、越界写入/网络被实际阻止，
   Disabled/External 的路径不被无依据地重写。仅检查 argv、提示词或对象字段不算执行验证。
5. 验证权限建立失败的 job 状态、lease/claim 处理、取消、关闭、恢复以及副作用不重放。
   分平台记录真实执行与未验证项，不能把 macOS 结果外推 Linux/Windows。

这涉及用户目标明确包含的核心安全执行与后台工作者权限，不包含 UI、部署系统或
无关产品功能的重建。以上是修改前审计；后续实际接入和剩余缺口见本文件末尾。

## 执行后端方案验证（第178批，尚未接入）

后续阅读已确认：`core/src/config/permissions.rs:48–98` 的隐式内置 profile 选择
还依赖 project trust 和 Windows 后端级别；`config/mod.rs:4608–4758` 先合并托管
profile，验证持久化名称，再按显式覆盖/已持久化选择/配置默认值及托管 allowlist 解析。
因此不采用“枚举 Default 就是所有新会话默认权限”的推断。

当前正在验证直接复用固定版本 `codex-sandboxing` 的策略转换，而不是另外实现一套
路径/元数据/网络策略编译器。依赖从上述 commit 导出到独立临时目录，未在参考仓库内
构建或修改源码。转换探针只解析权限、选择后端并产生 argv，不自行执行模型命令。
macOS 的 `/usr/bin/sandbox-exec` 已通过 allow-default + `/usr/bin/true` 的最小运行检查；
这只证明后端可启动，不证明限制已生效。

原生 `seatbelt.rs:874–1078` 还处理路径别名、受保护元数据及祖先目录、拒绝读取规则、
网络限制和 Process/FileSystemHelper 两种 profile。平台 scratch 默认放行只在
`permissions.rs::include_platform_defaults` 成立时注入；不能把 Process 的平台默认
规则无条件套到 memory workspace-write（其默认全盘可读，不走该分支）。
文件系统 helper 使用更窄的平台 profile，见 `exec-server/src/fs_sandbox.rs:93–143`。

构建目录：`/tmp/corki-permission-probe.meQBl2`。`probe_contract.py` 在临时目录及
本机 loopback 上验证允许写入、越界写入、`.codex` 元数据保护和网络行为，并验证 External
保持调用者拥有的隔离。它是策略复用的可行性实验，不是 Corki Runtime 集成测试。
在编译、实际执行和宿主接入完成前，不把此探针计作权限功能实现或本批验收。

进度更新：探针已构建成功，647 个外部依赖的版本、来源及 checksum 全部匹配参考
Cargo.lock。五组真实子进程验证通过：workspace-write（网络关闭/开启）、read-only、
danger-full-access、External 嵌套于调用者拥有的 sandbox。受限路径下的越界写入、
`.codex` 写入和网络请求确实被 OS 阻止；不是只检查生成的 argv。
参考目录为只读 commit 导出副本，原仓库保持干净。尚未接入 Corki Runtime，未验证
Linux/Windows，也未实现配置 profile 选择、托管约束或审批编排；不能把策略转换库的
复用说成这些上层能力已经实现。

## 第178批：显式本地权限执行接入（候选，未整体验收）

本批已不再只是临时探针：`native/sandbox` 保存固定版本的真实策略编译器源码、
Cargo.lock 和可重复构建入口；构建在独立 commit 导出目录完成。647 个外部依赖的
版本/来源/checksum 与原生 lock 一致。macOS 发布版约15 MiB，仅依赖系统动态库。
当前以独立宿主可执行文件交付前置条件，不把它冒充已打包进 wheel 的原生资产。

`config/permissions.py` 提供不可变的显式配置值（编译器、宿主 policy cwd、原始 profile
JSON），`CorkiSettings.for_directory` 从 `[execution]` 加载。它不是完整的规范化
PermissionProfileSnapshot 或 Codex 命名 profile 配置解析器。未配置时保留原有宿主
执行行为；原生默认选择仍未对齐。

真实调用链现在是：普通/Code Mode 的 `graph._execute_one` → 同一 ToolContext 权限 →
`ExecCommandTool` → `ProcessManager._resolve_and_start` 编译策略 → 重验取消/关闭代次 →
`_start_session` → `_spawn`/PTY。模型 workdir 与宿主 policy cwd 分开传递给原生编译器，
不能通过修改 workdir 取得新目录的写权限。没有 sandbox 后端或策略不合法时返回工具错误，
不进入无约束重试。新的启动前异步编译窗口也加入取消重验，避免已取消命令仍被启动。

`ApplyPatchTool` 和 `ViewImageTool` 在有显式权限时通过固定操作文件 helper 执行，
使用原生 FileSystemHelper profile；读写及符号链接最终由 OS 检查，不只检查路径前缀。
helper 由独立任务持有，I/O/输出有界，失败/超时/重复取消均加入精确子进程清理；
不自动重放结果未知的文件操作。无权限配置的旧路径仍保留。

验证证据：

- 修复前真实 Runtime：10例中8失败/2通过（414a68）；失败直接观察到越界文件、
  `.codex/config.toml` 和后端不存在时的文件被写入，不是仅缺少新类型的导入错误。
- 新增策略准备取消测试先失败，实际观察到模型进程被启动（c4e48d）；修复后通过。
- 当前40例聚焦通过（ec65f4）：普通/Code Mode、PTY、workdir变化、patch权限、
  实际loopback网络开关、图片 deny-read/符号链接、非法策略与重复字段、配置及helper所有权。
- 初步相关旧测试整组通过（91e09d）；静态Ruff/722文件format、compile、pip check通过。
- 三个组合示例通过（867a95）。安装态233项相关回归通过，276个宿主模块来源均为
  隔离安装目录（38fa36）；源码全量仍在运行，不能记录为已通过。

重要未关闭项（仍属于原 A–E 目标）：

1. 完整默认/命名 profile 选择、托管配置约束、审批/额外授权与远端执行。
2. 持久化的具体权限/身份/roots 原子快照及恢复、当前 Turn 更新重验。
   当前新增值来自本次宿主 settings，不是已验证的冷恢复权限契约。
3. 记忆后台 pass 仍未接收独立有效父快照。工作者目前随 settings 继承显式值，
   但尚未按 Disabled/External/Managed 三分支派生；Managed 情况不能称为正确的
   memory-root-only 策略。尤其不得直接把所有父 deny-read 当作原生托管约束：
   `config/mod.rs:526–547` 明确只继承独立 managed_deny_read_policy，且保留 enforcement。
4. Linux/Windows 的真实后端供应与验证、完整文件系统 helper 的宿主运行资源权限。
   当前只能在 macOS 记录已执行的 OS 效果，其他平台不能外推。
5. 原生可执行文件的分发、完整依赖许可集合和安装包平台覆盖；当前只提供源码构建。

因此本批是接入真实 Runtime 的增量候选，不是权限整体完成，更不是 Harness 对齐完成。

## 第179批：记忆任务的父权限捕获与三分支派生

重新逐段核对了原生 `turn_processor.rs:615–681`、`phase2.rs:49–103,311–360`
及完整 `phase2_sandbox_tests.rs`。父权限来自当前有效会话快照，独立于后台基础 Config；
配置失败记录 `failed_sandbox_policy`，不采样无约束工作者。原生保留 Disabled 和 External
的 enforcement，只为 Managed 创建 memory-root-write/no-network/no-default-temp 策略。

新增真实 Runtime 测试在修复前6例中2失败、4通过（48061e）：两个 Managed 例分别经过
普通/Code Mode，均实际写入了父工作区文件并连接了 loopback。不是只比较枚举或提示词。

现在 `Runtime._run_graph` 在准入处捕获独立 MemoryPermissionSnapshot，经 `_execute_run`
的新 Turn 启动门传给 `LongTermMemoryService.start`。start在排队前冻结本次调用值，
run_once → owned consolidation → run_agent 逐层传递，不使用可变 service 属性代替。
直接调用 run_once/run_agent 的旧宿主调用仍可从基础 settings 捕获默认值；显式的
MemoryPermissionSnapshot(None) 与未提供快照不同。

工作者准备私有输入副本后，先调用固定原生编译器派生/验证权限，成功后才构造子 Runtime
和采样；子 settings 使用派生结果并将 MCP 审批固定 Never。派生使用上游 PermissionProfile
及 SandboxPolicy 转换，不在 Python 中复制路径/临时目录/metadata 策略。
旧版本编译器会拒绝新 memory_root 字段，按策略失败关闭，不回退。

MemorySandboxPolicyError 在 job 边界写入精确 failed_sandbox_policy，并通过已有 owner
检查释放 claim/lease、保留重试时间；警告保留具体原因。CancelledError 不被策略异常吞掉，
准备中的后台工作被加入清理后再释放 job，临时副本最终删除。

本轮16个新增场景覆盖三分支×普通/嵌套×有效父值与基础值不同、后端缺失/非法配置的
采样前失败、准备期间关闭、排队前捕获不同 pass。相关426例通过18.90s（81c48c）。
Ruff/724文件format/compile/pip检查通过（3e7468），三组合示例通过（2abe1f,f789d5）。
安装态44778已终止：606 passed41.74s，277个宿主模块均来自隔离安装目录（b95a34）。
新的源码全量2832已终止：6743 passed515.36s，exit 0（d79b87）。
结合上述安装态与行为证据，第178–179批的显式执行/三分支继承范围阶段验收；
以下未关闭项不因此变成已完成。旧批日志中的运行状态仅表示当时观测。

原第178批全量57539已经终止：6727 passed515.25s（f108ae）。它始于第179批修改之前，
不将这次结果用于证明本轮新源码快照；当前已另起包含新用例的完整回归。

仍不能关闭的权限项：独立 managed_deny_read_policy 与完整配置约束尚无 Corki 表示，
不能把用户 profile 中全部 deny-read 当作它或宣称已继承。当前是显式权限路径，默认/命名
配置、持久化/恢复、实时权限更新以及非macOS实际执行仍未完成。另有执行次序差异：
Codex在加载phase2输入前建立agent Config；当前Corki在采样私有输入副本后派生权限。
策略失败先于子Runtime/模型/工具，但不是完整原生phase2步骤顺序的完成声明。

## 下一依赖：配置解析与恢复权威（源码证据，不是实现）

冷恢复不是把旧权限 JSON 原样信任并覆盖现有配置。原生
`app-server/.../persisted_resume_settings.rs` 从最新 TurnContext 或 ThreadSettingsApplied
倒序提取审批、reviewer及active profile；仅reviewer缺失时继续回查旧条目。
`thread_processor.rs:278–291,4124–4167` 在没有显式 sandbox/profile/default_permissions
覆盖时，只把持久化 active profile 的 id 送回 Config 解析。fork的对应逻辑位于4921–4990，
有已加载父线程及最新模型上下文的独立来源，不能把fork片段误称为cold resume。

`core/src/session/thread_settings.rs` 的更新与压缩共享 persistence semaphore；应用成功
之后才发布完整 ThreadSettingsApplied 快照。`config/resolved_permission_profile.rs`
内部持有 Constrained<PermissionProfileSnapshot>，检查候选的具体权限；legacy setter
重新建立无active身份的snapshot，不保留陈旧的profile身份/roots。

因此下一步要连同默认/命名profile、当前托管约束和配置身份解析一起补齐，再接入持久化与
恢复；不能先把当前 ExecutionPermissions 原始字段写进DB就称为权限恢复对齐。

## 第180批前置审计：托管约束不是用户 profile 的一部分

参考 commit 再验仍为 `ddf04ad26789d040f9ef6a96736f76602e35a6cc`，工作树为空。
以下为源码证据，不是已完成实现声明：

- `config/src/requirements_layers/permissions.rs`、`stack.rs`：托管
  `permissions.filesystem.deny_read` 是高优先级在前的去重并集；高层空数组/短数组
  不能清除低层拒读。普通 profile 表仍按常规 TOML 优先级递归合并。
- `requirements_layers/layer.rs`、`loader/mod.rs:695–743`：各层在自己的
  AbsolutePathBufGuard 下解析；系统文件使用文件父目录，不用模型 workdir。
  `core/src/config/config_loader_tests.rs:1655–1745` 明确测试相对路径和 glob 的基准。
- `config/src/config_requirements.rs:1810–1860,2062–2092`：模式白名单按具体文件
  权限分类；Managed 也可能是 DangerFullAccess。白名单必须包含 ReadOnly。
- `core/src/config/mod.rs:3990–4038`：非空托管拒读先给受约束值增加模式校验，
  再应用候选；不允许的候选可回退只读，不是将 Disabled 悄悄变成 Managed。
  原候选为 DangerFullAccess 且回退只读、审批 Never 时配置失败；回退清除旧身份和 roots。
- `core/src/config/mod.rs:4061–4148`：用户拒读在配置回退时保留；托管拒读另外保存，
  注入最终文件策略，并校验要求条目仍存在、显式可读 root 不落在拒读匹配范围内。
  `permission_profile_catalog.rs` 的可用性同时检查 id、实际模式和文件约束。
- 当前 `config/managed_mcp.py` 只接收 MCP/plugin 托管域，`mcp_shapes.py` 对其他域
  明确报错，不能误写为“已接受但没有执行”。`ExecutionPermissions` 只有显式 profile，
  无独立托管来源，因此记忆派生不能保留未被表示的约束。

修复验收必须包含：真实 Runtime 准入/执行、分层拒读不可被覆盖、相对路径不随工具 cwd
  改变、模式按具体权限分类、回退与失败分支、子记忆任务继承同一托管快照，以及旧编译器
  不支持新协议时失败关闭。不能只增加配置类型；完整默认/命名 profile、身份持久化和
  冷恢复仍需后续独立关闭。

### 第180批实现与当前验证

新增 `config/execution_requirements.py` 保存不可变来源/原始域值/独立base_dir，
`managed_mcp.py` 在保留原有MCP合并语义的同时拆出执行域；其他尚未实现域仍拒绝。
不能用普通MCP数组覆盖逻辑合并deny_read。旧snapshot API保留名称，新增独立execution层。

`native/sandbox` 新增codex-config依赖，直接使用上游 RequirementsLayerEntry、
compose_requirements_for_hostname、ConfigRequirements、实际模式分类器和ReadDenyMatcher。
原生core的私有配置接线由桥接代码实现：配置准入允许受约束只读回退；当前shell无审批
升级，FullAccess回退时报错。独立拒读插入最终策略并检查显式可读root冲突。
后续工具执行/记忆派生严格校验，不使用配置回退去掩盖运行时权限失配。

`Runtime.create` 在分配owner前将宿主约束绑定执行配置；缺少backend/profile时报错。
`_initialize_owned` 在Thread建立、MCP启动、采样之前异步解析，然后发布同一有效权限到
Runtime、Graph和未来Thread设置基准。回退警告进入首个Turn事件。编译器资源由原有
owned_process持有，返回后重验关闭/取消；这不是同步子进程阻塞事件循环。
这一步仍不是“所有配置在任何文件读取之前校验”的实现声明：factory的配置/插件发现
以及已有state adapter构造先于异步准入。

父Runtime捕获完整托管snapshot，后台service默认捕获也保留它。工作者同时继承独立
execution层和原始snapshot，不重新读取之后变化的系统requirements；父用户deny与
托管deny不混用。旧编译器缺少managed_requirements能力确认时拒绝执行，不回退宿主。

证据：

- 初始3个合法配置正向场景全部失败，错误确为非MCP托管域不支持（b720ea），不是导入失败。
- 当前22新增场景通过2.09s（605e99）：普通/Code Mode、精确/glob/符号链接拒读、
  高层空数组不清除低层规则、source-relative路径、有效模式分类、配置回退、
  采样/MCP启动前失败、显式可读root冲突、缺失backend、旧协议与非法域拒绝。
  两个真实后台工作者场景同时断言：托管秘密不可读、父用户deny对应文件对子任务可读，
  且不允许子Runtime重新读取系统配置。
- 先行执行/记忆相关47例通过8.61s（3fd114）；unit/config+unit/memory及记忆生命周期
  相关整组通过（eb6894）。三个组合示例通过（4bd8aa）。
- 静态Ruff、727文件format、compile、pip检查通过（2b3a79/77e771）；teach hash不变。
- 构建原生compiler终态94d649，36.38s；728个外部依赖name/version/source/checksum
  全部与固定Codex lock一致（0d80b8）。SHA256：
  `23234c406c70bd7ba96adffd4c80373aac658c30eaa64e33b0baef54f3cec855`。
- 包 `/tmp/corki-managed-artifact.AsvEO6`：wheel1157614字节，SHA256
  `1d608b5fd178ce367cc5f515fd6261d2935d1dc8de19507e592d4f500bfca64f`。
  308包文件/39prompts/88skills及README源码-wheel-install一致（3f4d16），
  888个sdist source条目一致（a52766）。
- 隔离安装654 passed43.26s，278个宿主模块来源均为安装目录（c50da3）。
  源码全量41801已终止：6765 passed511.52s，exit 0（163d51）。本批仅就上述
  托管模式/独立拒读及传递范围阶段验收，以下未关闭项继续保留。

未关闭：默认/命名profile和独立身份/roots、审批和managed network等其他域、冷恢复与
权限更新、完整helper宿主可读资源、phase2输入/配置顺序及私有副本与原生工作目录的
语义差异、非macOS真实backend、原生二进制分发与完整许可。A–E仍为部分一致。

### 后续命名配置源码证据（尚未实现）

`core/src/config/mod.rs:3370–3599,4577–4780`：用户与托管profile同名是冲突错误，
不是覆盖。恢复id先验证仍为已知builtin或能按当前配置编译；不合法的旧id不直接重放。
优先级为显式override/有效persisted id，再用户default，再托管allowlist/default回退。
托管default必须有allowlist且在其中；只有同时允许`:workspace`和`:read-only`时，
才可省略托管default并隐式选择`:workspace`。名单中未知id即使false也先做目录校验。

`config/src/permissions_toml.rs:20–218` 可复用公开resolve_profile：继承先父后子，
检测循环/未知父；description/extends是选中声明的元数据，不从父补齐；network domain
两边都有值时先normalize_host再合并。core只允许继承`:read-only`/`:workspace`，
不能继承`:danger-full-access`。workspace_roots按policy cwd解析，不随工具cwd变化。

`core/src/config/permissions.rs:48–62,352–481`：默认builtin取决于项目trust状态和
Windows backend；命名profile的路径编译/平台glob规则仍在core，不在当前已引入的
codex-config公共解析器内。显式选择`:workspace`不应用legacy workspace自定义设置；
隐式workspace可保留legacy roots/network/tmp定制，但会清除不可重选的active身份。
下一步必须把这些选择、具体权限与身份一起实现，再做持久化，不能仅增加一个profile名字。

路径编译补充：已逐段读完core的`config/permissions.rs`。顶层filesystem路径必须绝对、
`~/...`或特殊token；scoped路径只允许后代，不允许`.`/`..`组件（单独`.`入口除外）。
`:project_roots`是`:workspace_roots`别名，未知special保留并警告；一般read/write glob
不能当成完整匹配支持，末尾`/**`才按子树解析。workspace deny glob保持symbolic，待
具体roots齐备后materialize。glob_scan_max_depth=0非法。不能用Python Path.resolve
或glob通配直接替代这些语义。

后续复用选择：核心路径编译器虽不在codex-config的公共API内，但该固定源码模块仅依赖
外部crate及`super::ProjectConfig`（实际为`codex_config::config_toml::ProjectConfig`）。
可以评估在现有固定git-export构建中编译该原文件，以免重新手写路径语义；此方案目前
只有源码依赖证据，尚未加入桥接器或编译验证，不能写作已复用。

额外待验证的控制目录映射：上游protocol/permissions.rs:27–36,831–833固定保护
`.git/.agents/.codex`；Corki的project plugin入口是`.corki/plugins`。当前桥接器未做名称
映射。CLI配置默认来自宿主CORKI_HOME/config.toml，并非自动读取项目`.corki/config.toml`，
不能据此直接宣称项目配置已可提权；但Corki控制目录在workspace-write下的写保护需加
真实Runtime回归并按来源权威审计，不能用`.codex`测试通过证明`.corki`也受保护。

## 第181批计划与验收范围（修改前记录）

在已有桥接器上先接入用户`default_permissions`/`[permissions.<id>]`与内置默认选择，
以固定上游core/config/permissions.rs原文件负责继承、路径编译和workspace roots。
保持显式原始profile路径兼容；编译结果需同时携带实际权限、active身份和profile roots，
托管模式约束导致回退时清空身份/roots。记忆子任务派生也不得保留父命名身份。

验收使用真实Runtime的OS写入/拒绝效果，覆盖命名继承、scoped workspace、未知/循环/
保留名、未知special、编译前拒绝、约束回退、后续模型设置变更不恢复旧权限。
目前Corki没有完整project trust配置解析，不凭空标记workspace受信；隐式builtin先按
unknown trust分支选择read-only。没有配置编译器仍是既有legacy路径，默认后端交付未关闭。
本批不以用户命名profile支持替代后续托管命名目录/allowlist、完整配置层选择和冷恢复；
这些仍按前述源码契约继续实现。审批/network proxy域未实现时必须显式报错而非忽略。

### 第181批实际实现与验证

`config/permissions.py` 在已有raw profile之外保存未解析selection；`settings.for_directory`
从宿主配置的default_permissions/permissions生成它，只有compiler时显式走unknown-trust
只读builtin。没有compiler的命名声明、raw与named混合来源明确报错，不静默忽略。
`ExecutionPermissions` 新增不可变ActivePermissionProfile和profile_workspace_roots。

`native/sandbox/src/selection.rs` 通过include!编译固定git-export中的原始
`core/src/config/permissions.rs`，不是复制到Corki后自行改写。上游ProjectConfig使用
codex-config公开类型，新增直接依赖的版本/来源仍与固定锁一致。原生checkout无修改。
因此继承/cycle/保留名/特殊路径/scoped glob/roots的核心编译均使用实际参考代码。
当前只支持named network.enabled；其余proxy字段因执行编排未接入而明确拒绝。

Runtime在采样前把selection解析成具体策略及其身份/roots；未来Thread模型设置更新
保留同一执行值。托管模式强制回退清空身份/roots，记忆派生也清空父命名身份。
startup在配置解析后遇到MCP失败可以重试；重新校验具体值不丢失未被回退的已有身份。
准备期间取消先join已有startup任务，不创建Thread或开始采样。

验证证据：

- 初始3例合法命名/隐式配置均失败（c277dd），原因为旧解析器强制compiler+raw profile。
- 初轮15例通过（af6795），扩大后的相关73例通过11.10s（67691d）。
- 发现Python身份检查额外禁止了native允许的空TOML profile key，真实Runtime先失败
  （fc7f5a；原生已返回合法id，Python拒绝）；移除额外限制后，当前52例通过2.95s
  （0486b5），包含全部24新增场景和28既有配置/owned-process回归。
- 三组合示例通过（94133a/d47c74）；Ruff/729文件format/compile/pip通过（a3d8a3）。
- 原生构建终态d9a39f，36.28s；compiler SHA256：
  `f83441d99c7f3a40b85941224f753d913f806e77338ea64cc7f5d96b83a56ccd`。
  728个外部锁记录仍与参考name/version/source/checksum一致（91e6ef）。
- 最终候选包 `/tmp/corki-named-final.d9pypn`：wheel1158648字节，SHA256
  `04385bf16dcf8d28cedb6f49e9f844a558df40cece5b54d3bd5a44ca325fd9f2`。
  308包文件/39prompts/88skills及README的源码-wheel-install一致（0ed21f）；
  891个sdist source条目一致（341284）。不要使用较早M98dk7候选代替这个快照。
- 当前隔离安装678 passed45.63s，278宿主模块来自安装目录（500025）。
- 源码全量68843已终态：6788 passed/515.90s（f0157b）。启动早于最后空名称兼容
  修正/新用例及第182批改动，只是该启动快照的回归证据，不是最新源码全量验收。

继续项：托管命名profile和allowlist/default、完整配置层与project trust、raw profile
无托管约束时的全配置准入（旧路径仍在tool边界验证）、审批与network proxy、profile
身份/roots持久化/当前配置重解析、Context中的权限说明、控制目录名称映射、默认backend。
本批是配置链路增量，不替代这些项目，也不代表A–E任何总项已全部完成。

下一步已读原生`config_loader_tests.rs:1839–2027`：托管profile与用户profile可以双向
继承；测试同时验证继承后的network proxy配置保留，不能只保留network.enabled位而
宣称完整managed profile支持。`ConfigRequirementsWithSources`可clone后into_toml，
便于在保留来源约束的同时解析托管目录。首次选择、startup重试和memory派生必须保留
足够的当前配置来源信息；不能在第一次canonicalize后丢掉user catalog，导致后续
allowlist引用无法校验。所有未执行的原生Rust测试/真实模型测试仍如实列为未执行。

## 第182批修改前确认

再次逐段核对core/config/mod.rs:4577–4774（参考checkout仍干净）：先合并独立用户/托管
目录并拒绝同名，再校验allowlist中的所有id（包括false项）和托管默认，最后选择/回退。
ID回退发生在具体权限编译之前，不等于模式约束在编译之后回退；后者才清空身份/roots。
只有同时允许`:read-only`和`:workspace`才能省略托管default，默认选workspace。

本轮要接入该完整目录/选择链，同时保留用户声明与独立托管层：重试配置准入可以重新
选择；普通工具和memory的具体权限override只能校验目录/模式，不能重新选择父profile
覆盖已派生的子权限。验收需覆盖双向继承、同名冲突、未知false条目、默认约束、ID回退、
保留当前配置来源后的重试/跨Turn，以及child override不被父named选择取代。

### 第182批实现与当前验证

`config/execution_requirements.py`、`managed_mcp.py`保留独立来源的命名profile、
allowed_permission_profiles和default_permissions。原生requirements层逐层校验再组合，
高层同名托管表保留低层未覆盖字段；用户/托管同名冲突不能以高层覆盖静默消除。
`native/sandbox/src/catalog.rs`实现上述目录校验与托管默认规则；selection在编译具体
策略前完成ID回退，继续使用固定Codex原始permissions.rs处理继承和文件权限。

`ExecutionPermissions.catalog_json`保留初始用户声明，`select_from_config`区分配置
重新准入与具体权限override。Runtime重试重新解析声明与当前传入的托管约束；普通工具
和memory子权限只携带目录校验上下文，不能重新套用父选择而覆盖派生权限。模式回退
清空身份但不销毁原始声明。此机制不是自动重读宿主配置、持久化profile或冷恢复实现。
编译器须显式确认managed_catalog能力，旧版本不得静默忽略新约束后进入工具执行。

原始3个场景先失败（e8c45d，旧实现拒绝合法托管命名域）；本批16个集成场景和5个新增
单元场景已加入。最新相关79 passed/6.62s（13f52c），包括普通/Code Mode双向继承、
多Turn、目录冲突/循环/未知false条目/默认校验、ID回退、托管高层表合并、配置重新准入，
以及真实run_agent子任务实际写入证明父readonly命名策略没有替代memory派生策略。
无效目录通过共享Runtime helper证明采样前失败；兼容能力校验证明旧编译器返回不能准入。

原生build44b04d终态35.22s，二进制
`/tmp/corki-catalog-permissions.bjTUGr/corki-sandbox-catalog` SHA256
`c678eb0036288501fa1335b6b420cd5446a0eaf637b676f143f1e37f92c9fc40`。
原生rustfmt检查通过（9e063a）；Python Ruff/730文件format/compile/pip检查通过
（f6af52）。参考checkout仍为固定commit且干净，teach哈希不变（76a817）。
本批最终源码全量、安装态验证及组合示例尚待终态，不能引用181的包作为182交付。

后续验收进展：三组合示例均成功（b67215），安装态699 passed/45.43s且278个宿主
模块来自隔离安装路径（1e2647）。最终候选`/tmp/corki-catalog-final.ChcFDJ`，wheel
1159131字节，SHA256 `8ce8bb13fae74f10af4529b5aa256c2ded487d2e240febba5e989ea91869c96a`。
308包文件/39prompts/88skills与README源码-wheel-install一致（05f0e5）；893个sdist
源条目一致（1bd01b）；git diff --check通过（1460e0）。源码全量92616已终态
6810 passed/514.35s（f3f85e），此终态观察完成后才开始183源码修改。本批目录/选择
范围阶段验收，但不代表A–E总项完成；183之后的源码不可继续引用此包/全量为最新验收。

仍未关闭：完整配置层/trust/legacy选择、审批/network proxy、当前配置重解析的持久化
身份、上下文权限说明、默认backend、非macOS执行后端和memory输入/配置顺序。
命名ID allowlist不是任意具体策略的能力白名单：原生显式PermissionProfile override
优先于命名选择，memory派生可在模式约束允许时写其工作目录。本批保留该区别，不能
将“只允许readonly名称”错误解释为所有宿主override都只能只读。A–E总项仍全部partial。

## 下一批：Phase2 配置准入顺序（修改前证据）

重新读取参考`memories/write/src/phase2.rs:48–203,312–379`（2ae6dc/88a2dc）：
claim → prepare_memory_workspace → agent::get_config → DB输入选择 → 同步输入 →
比较diff/合法产物 → no-change成功或启动worker。workspace准备仅布局/git基线/删除旧
diff，不等于提前读取并同步新输入（workspace.rs:15–21，8aafc6）。

Corki `memory/pipeline.py:_consolidation_work`目前先load_consolidation_inputs、
write_consolidation_workspace、capture和baseline_matches，仅非skip时进入_consolidate；
然后`memory/agent.py:run_agent`复制输入后才for_worker。因此不仅失败前输入已经被读取
和同步，no-change路径更会完全绕过worker权限校验并完成claim。

不改动正在验收的182源码快照，先在临时路径保存两例红测试：
`/tmp/corki-phase2-order.KdJ9lX/test_policy_order.py`。真实SQLite/MemoryService/owner
循环，缺失后端而非伪造异常：2 failed/0.66s（7c7861）。changed场景失败原因是
策略异常前已load输入；unchanged场景失败原因是没有抛策略错误并提前完成no-op。
这不是import/fixture失败，不是仍待复现的猜测。后续应纳入正式回归并接入完整phase2
准入顺序，而非只在agent._prepare附近交换两行；需覆盖skip、取消、lease失败、临时
目录所有权、配置一次解析后的worker复用。私有副本与原生memory_root差异也需明确。

第183批补充修改前证据：已完整读取参考phase2_sandbox_tests.rs及
phase2_workspace_roots_tests.rs（1e2fce/1b1cab），三分支以父canonical enforcement为准，
并将workspace roots绑定memory_root；并未执行原生Rust suite。
再用真实Runtime后台调度复现两例（临时test_runtime_policy_order.py，1f8d80）：
父TurnCompleted不受影响，changed输入已提前读取；unchanged报告failed=0、
consolidation_skipped=True，未发现缺失后端。两例0.70s真实失败，不是导入错误。

修复方向：在持有phase2 claim/heartbeat期间、DB输入选择前创建owned worker配置，
其工作目录/父策略/完整child settings作为同一准备结果传到run_agent复用；所有skip、
load/sync/diff失败及取消路径回收尚未交给Runtime的目录。Runtime关闭未确认时，沿用
ConsolidationShutdowns保留权，不能被外层准备结果的finally提前删除。直接run_agent
入口也必须在复制输入前建立相同配置。此修复不以调整_prepare/for_worker两行冒充
整个phase2顺序已对齐；原生共享memory_root与Corki私有副本仍为独立未关闭差异。

### 第183批当前实现与验证

`memory/agent.py:prepare_agent`是owned async context：先创建空工作目录、派生子权限、
构造完整child settings，再产出PreparedAgent。`pipeline._consolidation_work`在该
context内选择/同步输入和比较基线，非skip才把同一准备结果送入_consolidate/run_agent；
run_agent不重新派生或创建另一个目录。直接入口复用相同准备流程。PreparedAgent只能
启动一次；正常/skip/准备或输入异常清理目录，关闭失败设置retained，把清理权留给
已有ConsolidationShutdowns。原有cancel join/晚到close失败/延迟回收语义仍接受回归。

4个顺序红测试已纳入正式unit/integration；新增9例覆盖配置一次解析/同根复用、no-op
先校验且不创建Runtime、load/sync/capture/baseline/diff/copy失败清理、输入等待取消。
13个新增场景通过0.89s（ea85e4）；初步相关377项通过17.14s（4555bc，新增9例前）。
旧两例依赖_prepare已发生来观察目录，修正为观察更早的for_worker目录，并新增断言
配置失败时不复制输入；这不是移除故障验证，而是按新顺序加强预期。
Ruff/733文件format/compile/pip及diff检查通过（159a0d）。本批较宽memory回归、
最终全量/安装态尚待验证，不引用182全量当作当前源码结果。

最终候选检查：全部unit memory及integration test_memory*加托管命名场景，共737项
通过60.35s（56d82f）；三组合示例通过（8688a0）；最新静态检查733文件、compile/pip/
diff、teach哈希均通过（89b25c）。候选`/tmp/corki-phase2-config.WekiiJ`由sdist构建
wheel，1159768字节，SHA256
`d88eeebdbe48833d04dcc2a9a502e7581c1ad94ffe013090af5030cdb20b2292`。
308包文件/39prompts/88skills及README源码-wheel-install相等（c2a64e），896个sdist
源条目相等（97e9dc）。源码全量53843与隔离安装44794运行中，等待各自终态；
这两个进程都针对183最终源码，不要因观察超时重新启动。

安装态44794已终态712 passed/48.86s，278个Corki宿主模块路径验证来自独立安装目录
（31e320）。当前仅源码全量53843待终态，不能把安装态相关选择当作全量验收。

183最终源码全量53843已终态6823 passed/518.57s（1a77eb）。观察该终态后才开始184
源码迁移；183的配置准入顺序范围阶段验收，不包括下述共享目录与基线差异。

### 后续必须继续核对的共享工作目录差异

参考phase2.rs:385–491再次读到（04bbd4）：agent直接在memory_root执行，关闭确认后
检查该目录产物，heartbeat再确认owner，然后reset_memory_workspace_baseline并完成
DB job；agent失败时清除目录中的symlink并标失败，不存在“丢弃私有副本就回滚所有写入”
这一实现。Corki的agent_artifacts.collect/publish仍在私有目录比较输入、只接受输出
集合，并在owner事务里复制发布。其影响包括运行期间产物可见性、失败残留、实际cwd和
绝对路径权限约束的目标，以及输入变更能否被接受，不能把这些不同语义直接称为一致。

183只修复配置/输入处理先后及资源所有权，尚未关闭上述差异。下一步需跟踪原生
workspace基线/源目录读取/验证与lease失败窗口，并将这些场景映射到真实Runtime测试，
再决定相应实现迁移；不能因为私有副本更易测试或更保守就替代用户要求的实际行为。

## 第184批修改前审计：共享目录与路径权限

参考workspace.rs全文件（60bcd6）及workspace_tests.rs（b48425）已读：先准备布局/
git基线并删旧diff；最终验证先移除所有symlink，再检查MEMORY.md为文件和summary首行
v1。成功在确认关闭与owner后重建当前目录基线；失败不回滚已完成的普通文件写入。
state/runtime/memories.rs:1253–1307也确认DB只提交精确selected快照，不负责复制发布文件。

git-utils/src/baseline.rs完整756行及其测试已读（044600/d388e6/0f6a8f）：只排除名为
.git的元数据，包含其他隐藏文件/符号链接记录；diff仅hash当前文件而不写blob；reset
重建无parent的单提交并抛弃旧对象，read-tree禁用configured hooks。其reset明确仅供
内部目录，不能拿用户项目repo或整个home作目标。Corki的JSON sampled baseline及忽略
所有点文件的规则仍不同，后续不能将JSON快照直接宣传成原生Git基线完整等价。

当前P0路径差异已用真实Runtime、exec_command及原生权限编译器复现：
`/tmp/corki-shared-memory.jNjuoy/test_shared_workspace.py`包含3例。
- success/failure两路都证明worker cwd是临时目录，成功工具写入在完成前对实际root
  不可见（a4c8de，2 failed/1.04s；扩大后三例d0cc57）。
- managed deny_read拒绝实际memory_root，但临时副本仍能启动worker。直接用同一个
  native compiler为实际root派生策略会报readable-root冲突（4ed062）；扩大为真实
  exec读取测试文件后，ToolResult包含合成标记MANAGED_PRIVATE_MEMORY（e853b6），
  证明不是只缺初始化错误，而是复制输入改变了独立托管路径约束的目标。

迁移要求：主pipeline默认使用实际memory_root作为child cwd和权限派生根；临时目录
仅承载临时Runtime数据库/运行资源，清理绝不可递归删除共享root。工具完成的写入即时
可见，agent失败保留普通文件但不推进基线/DB成功；确认关闭后验证当前共享产物，owner
fence只管理基线/状态提交，不再二次复制/覆盖worker输出。原有JSON最终输出可作为
明确兼容路径，但不能替代默认文件编辑路径。关闭未确认时仍保留owner，不得因共享root
迁移提前释放lease。需要对应更新只适用于private-copy的旧测试预期，并保留取消/故障
与权限真实行为的断言。此处仍是修改前记录，尚未声称已修复。

### 第184批当前实现与验证

`prepare_agent(..., root=memory_root)`为主pipeline创建共享准备结果；temporary只拥有
运行资源，settings.working_directory和for_worker都绑定实际root。直接run_agent不传
共享准备结果时保留独立隔离兼容分支。主流程不复制sampled输入，生成diff后直接让正常
Runtime工具操作root。SharedAgentArtifacts明确表示已编辑当前目录，无回拷文件集合。
完成时owner事务只更新当前目录基线和DB；外部重建的摘要不会被旧删除清单再次删除。

关闭确认后，shared validation清除symlink并只检查原生要求的MEMORY文件/summary首行。
失败保留普通写入、不推进成功基线；关闭未确认仍保留原有lease/状态目录owner。测试
观察临时状态改为独立fixture，不再把child cwd.parent误当作可递归清理的临时目录。
文件路径不再进行私有产物的源快照拒绝或输出字节改写；JSON输出的序列化/脱敏兼容逻辑
仍明确单列。不能因此声称所有Corki sanitizer/Memory契约已与原生相同。

三例原始红测试转绿（011538，1.40s）。首轮较宽回归29 failed/711 passed（9c21ef）
逐项对应私有cwd/写入不可见/回滚/输入变更拒绝等旧契约。调整为真实共享行为后，第二轮
746 passed/60.24s（a72a9b）；后续新增FIFO原生validator边界，当前新增总数10。
额外验证symlink文件/目录及隐藏位置的清理不改外部目标，失败清理保留普通写入，diff
清理不删除memory或.git。当前最终全量/安装态尚未运行，184不能引用183包作为当前交付。

仍开放：JSON baseline捕获/排除规则与原生Git不同（包括FIFO与隐藏文件、初始基线恢复、
旧对象删除、hooks禁用）；初始布局与claim顺序；完整权限配置/default/recovery/context。
shared validator接受FIFO不代表当前JSON baseline也支持它，该残留已单独记录，不能
用更严格的快照规则替代原生最终行为。所有A–E总项仍partial。

184最终候选：新增10场景及受影响生命周期聚焦69项通过12.03s（785ffc），最新静态
Ruff/736文件format/compile/pip/diff通过且teach哈希不变（5fc39c）；三组合示例成功
（ac00e2）。候选`/tmp/corki-shared-final.HzTzxu`由sdist构建，wheel1161745字节，
SHA256 `7573c99d4de64f32db6f73e2562b78174c7be72935c1c21dc5df5909c62c4539`。
308包文件/40prompts/88skills及README源码-wheel-install一致（2e9b97），900个sdist
源条目一致（cba821）。当前源码全量51067与扩展安装态95735仍运行，未阶段验收。

扩展安装态95735已终态1011 passed/90.50s（9c24e3），278个Corki宿主模块均来自
隔离安装路径。源码全量51067随后终态13252f：6833 passed / 520.17s；第184批阶段验收，不能
将已通过的安装态相关选择替代全量证据。第184批所有验证进程现已终态。

下一步已完整读Corki memory/reset.py（e08ac8）：显式reset验证bounded roots/受保护
路径与重叠，再清DB、清根目录下全部条目，因此未来.git必须纳入同一清理语义，不能
另留Git对象保留已遗忘内容；当前实现并非只删除几个Markdown文件。原生Git基线迁移
还需核对reset/forget入口调用链、旧JSON基线迁移和已有内部目录元数据，不得将用户项目
Git仓库或home作为reset_git_repository目标。此处为后续审计，尚未新增Git写操作。

第185批已按上述审计接入实际Git基线：准备早于子配置，默认共享路径和JSON输出
兼容路径均在成功后记录当前树；隐藏文件、FIFO及链接启动清理接通，v3内容迁移、
只读diff、单提交reset/旧对象清除与显式reset包含Git对象均有行为验证。
相关749/安装1029通过，源码全量67857已终态6851通过/607.55s（fd4ee1）。完整实现、包散列和剩余兼容差异见
audit.md的185节；本页此前“Git尚未新增”保留为修改前历史，不是当前实现状态。

第186批指令来源/创建快照/受限读取已阶段验收：源码6873项（9773b0）、隔离安装
1463项（cb97cd）通过。第187批正在接入执行规则：Never命令准入、home启动加载、
坏规则整组回退与告警、不可变规则快照、配置目录匹配的显式父继承，均接入真实Runtime。
配置复制不自动继承父规则；后台记忆新内部会话重新加载。具体源码、红绿证据、当前
全量进程和包状态集中记录在audit.md第187批，避免本页历史统计被误读为最新验收。
默认无编译器、完整项目trust/规则层、托管exec规则与审批、跨平台和恢复仍未关闭。

187已在冻结包范围通过源码6937/安装1527（a57a77/9917d9）。188随后接通托管
requirements [rules]、原生逐层追加/最严格命令决策、坏用户规则保留托管overlay，
以及原生规则指纹+来源约束下的显式父快照复用。上段“托管exec规则未关闭”是188前状态；
当前实现与专项/全量/包证据见audit.md的188节。交互审批、默认路径和完整trust仍开放。
