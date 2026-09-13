# Stop hook：本地信任与执行接入方案

状态：本地 TOML 同步命令已接入，完整 hook 路径仍实施中。参考 commit
ddf04ad26789d040f9ef6a96736f76602e35a6cc。承接 turn-decision-acceptance.md。

## 最新：诊断与控制结果已分离

events/stop.rs::parse_completed 在处理 continue/decision 之前先把有效 JSON 的
systemMessage 加入 Warning entries；suppressOutput 不抑制该条目。即便 block
缺少有效 reason，systemMessage 也与失败诊断同时保留。Corki outcome 原先只
返回 decision/text，stop/block 分支直接返回而丢失 systemMessage。现已分离
诊断集合与控制文字，通过已有 WarningEvent 接入 CLI，不把诊断混入模型反馈。

真实 Runtime 的多 hook 控制测试携带 systemMessage 和 suppressOutput:true，
修复前全部缺少诊断（3 failed，906842），修复后按声明顺序收到两个 WarningEvent。
单元测试覆盖 allow/stop/block/无 reason 的语义失败 × suppressOutput，另验证结构
无效的输出不发布未校验消息。联合 hook、冷恢复、热刷新、evaluation、账本
99 passed（8877a5，11.88 秒），静态检查通过。未新增 HookCompleted 专属 CLI
展示，本项只证明通用 WarningEvent 交付，不作为专属 UI/PTY 完成证据。

## 前轮：多个 Stop 结果的停止优先级已修复

原生 events/stop.rs::aggregate_results 先汇总 should_stop，再计算
should_block = !should_stop && any(block)；停止会清空汇总 continuation fragments。
core/session/turn.rs 据此结束，而不会将某个 block 的提示回灌。Corki 原实现
逐 hook 立即追加反馈且 blocked 一旦为真不撤销，导致另一 hook 的 continue:false
无法阻止继续采样。现先保留各命令的持久化执行结果，再汇总控制结果，只有
没有 stop 时才提交反馈。不删除或改写已有历史/执行记录。

test_stop_hook_control.py 实际执行两条命令，覆盖 block→stop、stop→block、
block→block。前两者不追加反馈且不继续模型采样，后者按声明顺序回灌两个提示
并继续。修复前 2 failed/1 passed（c37dc8），修复后与本地/插件 hook、刷新、
冷恢复、evaluation 和账本联合 90 passed（29abac，12.04 秒）。静态检查通过。
此处仅核销多 hook 停止优先级，不宣称输出展示、所有通用诊断或新输入与 stop
并发的主循环边界已全部对齐。

## 前轮：hooks.json 配置来源已接入

discovery.rs::discover_handlers / load_hooks_json 在每个准入配置目录先加载
hooks.json 再加载 TOML，目录去重；两种定义共存时警告但都执行，JSON 的
hooks 字段提供事件定义，不提供用户授权。文件缺失忽略，读取/解析失败警告。
Corki 原先只保存 TOML 快照，因此此来源缺失。现已在配置加载时为
准入层捕获 JSON 文本快照，不在每个模型 Step 重读磁盘；discover 将 JSON
来源 pathname 与 TOML 来源分开，复用同一用户批准、命令规范化及执行记录。
新增 ConfigLayer.hooks_json 可选文本字段，旧调用默认 None；不改变数据库。
仅准入目录读取，读取错误归入配置警告；JSON 外层按 HooksFile 的已知字段
约束校验。非法 TOML hooks 表不丢独立 JSON，非法 JSON 不丢 TOML。

test_stop_hook_json.py 从真实文件经 CorkiSettings.for_directory 到 Runtime
执行，覆盖用户批准、JSON/项目不能自授、快照稳定、冷加载改动撤销旧批准、
解析错误、JSON→TOML 执行顺序，以及禁用项目中非法 TOML 的隔离。
test_stop_hook_sources.py 验证目录去重及坏 JSON 不丢 TOML。新场景修复前
4 failed/2 passed（988f42）；联合 hook、配置、项目信任 128 passed
（476440，14.45 秒）。后续收紧 JSON 外层约束再跑专项；静态检查已通过。
插件 hooks、SessionFlags、完整 hook 事件/CLI 审批不包含在本批，仍开放。

## 前轮：平台命令选择已规范化

原生 discovery.rs 的 Command 分支先按 cfg!(windows) 选择 commandWindows
或 command，再校验非空并计算规范化指纹（normalized.command_windows=None）。
Corki 原先已校验两个字段的类型和别名重复，但 discover 无条件拒绝它们；因此
合法跨平台配置在 macOS/Linux 上也不能运行。现按当前平台选定命令后
规范化，POSIX 忽略 Windows 字段的值但保留类型校验。Windows 进程 containment
仍未实现，不能把命令选择的模拟测试宣称为真实 Windows 执行验证。

真实 Runtime 测试携带 commandWindows 及 command_windows 两种别名，验证
普通命令的继续/反馈链路仍执行，未选字段不影响已授权的指纹；单元测试模拟
平台选择与非空校验顺序。修复前 5 failed/30 passed（85bfcf），修复后与
冷恢复、进程清理、执行账本、evaluation 联合 56 passed（837de8，6.04 秒）。
ruff check/format、compileall、uv pip check 通过。完整 hook 来源、CLI 审批、
异步及非 root 运行仍开放。

## 前轮：用户分层授权状态已接入

原生 config_rules.rs::hook_states_from_stack 从低到高读取用户/SessionFlags，
包括禁用层；有效状态逐字段合并、key 去首尾空白、无效条目忽略。项目层不能
写用户状态。Corki discover 原先提前跳过禁用层且 states.update 整条覆盖，
会在只更新 trusted_hash 时丢失 enabled=false，或只更新 enabled 时丢失哈希。
这是执行权限边界差异。现已分离定义准入与状态读取，逐字段校验合并，并用
真实 Runtime 命令日志验证启用、禁用保留、项目无权覆盖和禁用层的用户状态。
key 去首尾空白，无效类型条目不覆盖先前有效字段；未知字段忽略。额外保留
旧 key 兼容的安全边界：显式但无效的新 key 不从旧别名取得批准。
修复前 5 failed/13 passed（503324），修复后 hook/evaluation/存储 47 passed
（df78f5）；联合项目配置与信任测试 146 passed/1 skipped（4b0728，12.51 秒）。
ruff check/format、compileall、依赖检查通过。跳过项不计完成证据。
LocalConfigState 目前仅支持 user/project；SessionFlags 接入仍需单独处理，
不能把多用户层测试描述为已完成 CLI session override。

## 前轮：配置来源 key 对齐，保留执行身份

原生 hooks/src/lib.rs::hook_key 使用 pathname:event:group:handler，
engine/discovery.rs 从 source_path.display() 提供文件来源。Corki discover
现使用相同 pathname:stop:group:handler；只有新 key 完全不存在时才读取
旧 file:pathname:stop:group:handler 授权。新 key 禁用、空表或指纹变化
均不能回落到旧批准。未修改用户配置，也不自动批准旧版非规范化哈希。

执行记录仍保留 stop:turn:step:file:pathname:stop:group:handler，反馈 ID
也保持不变。这是持久化兼容边界，不是配置 key：直接重命名账本会把结果
未知的操作当作新操作执行。真实冷 Runtime 测试切换授权 key，并断言 SQLite
旧执行 key 保留，覆盖未知结果、已提交结果、已追加反馈三个窗口。

新增场景在修复前七项失败（dc0d88），修复后 hook 专项 40 项通过（3febeb）；
联合 evaluation、存储、模型继续、输入边界和恢复 122 passed in 22.12s
（7a0989）。ruff check/format、compileall、uv pip check、diff check 通过。
此项仅关闭本地 TOML 来源 key 差异；hooks.json、插件来源、分层状态合并、
异步/非 root hook 和 CLI 审批仍开放，不代表 A4 或完整 hook 已完成。

## 前轮：支持的 Stop 命令指纹已按原生规范化

重读 config/src/hook_config.rs 的 MatcherGroup/HookHandlerConfig、
hooks/src/events/common.rs 的 Stop matcher=None、discovery.rs 的规范化以及
config/src/fingerprint.rs 的 version_for_toml。后者实际是 TOML Value 转
规范 JSON 后 SHA-256；旧文“JSON 对 TOML”的描述不够准确，核心差异是
缺少 event_name/hooks 包装、空白分隔和未执行类型/字段规范化。

command_identity 现生成 {event_name:"stop",hooks:[normalized_handler]}，
递归排序键、紧凑 UTF-8 JSON；timeout 缺省 600、0 归 1，async 缺省 false；
None 状态字段省略、空状态字符串保留，Stop 不使用 additionalContextLimit，
未知字段忽略但已知字段仍做类型校验。命令/timeout 改变继续撤销批准。

离线 Rust probe 使用相同 Serialize 数据结构、toml 0.9、serde_json、sha2，
路径 /private/tmp/corki-hook-hash.ATqWXT，cargo --offline 退出 0（251be7），
未修改参考源码、未访问网络。三个规范 JSON/hash 向量已写入
tests/unit/evaluation/test_stop_hook_identity.py，覆盖简单命令、中文/转义、
空 statusMessage。不是声称执行了整个 Codex 二进制。

新测试修复前 6 failed/2 passed（d48a11）；修复后与真实授权/继续/冷恢复、
进程及执行记录联合 34 passed（20d533，3.31 秒），静态通过
（5e1789，1146 文件、77 包）。真实 Runtime 额外证明显式默认/被忽略字段
不丢授权，旧版 Corki handler-only 指纹不能自动获得新版批准，不运行命令。

边界：仅已支持的本地 Stop 命令规范化，不宣称所有原生 handler 已实现。
本轮之后已处理 file: 来源 key 差异及执行身份兼容，见顶部更新。平台命令、
其他来源和 CLI 授权 UI 仍按下文开放；没有替用户修改任何批准记录。

## 最新：真实 graph / SQLite 三个冷恢复窗口

test_stop_hook_recovery.py 使用已授权本地命令和真实模型 Step，在命令已执行但
完成记录未提交、完成记录提交后、反馈 ContextItem 追加后三个窗口挂起。
取消底层 graph（保留 running Turn / checkpoint），关闭 warm Runtime，重新
构造 cold Runtime 并 resume_pending；不是只直接调用账本或手工伪造结果。

未知窗口只保留一次命令执行，恢复产生 TurnFailed/unknown，零额外模型采样；
完成/反馈窗口复用第一次结果，再采样一次并运行新的结束检查，命令记录严格为
False/True 两行。三者均保持旧归档前缀、一个用户输入、没有 ToolResult，反馈
恰好零/一条，再次 resume 无事件。专项 3 passed（a30d9b，1.03 秒），静态
通过（f06dc3，1145 文件、77 包）。本轮无生产修改。

这是取消 graph 的故障注入，不是 OS 强杀；也不证明配置变化、多个 hook、
进程仍运行时重启以及所有反馈/压缩/新输入交叉窗口。下方“实际冷恢复未验”
已由本节局部补齐，其他明确列出的功能缺口仍开放。

扩大为 hook 运行/进程/账本、结束输入窗口、模型继续、恢复与并发联合：
98 passed（21b5e7，20.02 秒），session 33038 已退出 0。

## 最新：同步 Stop 命令实际驱动 Runtime

core/stop_hooks.py 从 LocalConfigState 分层快照读取 Stop 命令，仅用户层
hooks.state 可提供批准。配置哈希/禁用检查通过后，使用会话 shell 和环境快照
执行 JSON stdin，分别读取 stdout/stderr；同步命令使用独立进程组，错误/超时/
取消 join 清理，成功不杀已脱离的 helper。当前每个输出流有 1 MiB 硬上限。
claim/完成结果走独立 hook_executions；已完成复用，未知抛错而不再次执行。

graph.py::_finalize 接入：待处理输入优先，不先关闭 realtime 接收边界；
block 提示作为绑定当前输入的 ContextItem 写入历史，回 evaluate 的继续/预算
判断，下一 Step 重新采样。恢复身份使用 Turn/Step/来源 key，请求含配置指纹。
stop_hook_active 只参考之前 Step 的反馈，当前 Step 重读结果不会改变请求身份。
诊断走现有 WarningEvent/CLI 展示，不伪造工具 Observation。

初实现两授权场景发现反馈被 world_state 当快照撤销，导致多一条记录
（481a01）；绑定 source_input_id 后修复。真实 Runtime 六场景验证用户批准、
未批准、旧指纹、禁用、项目伪造批准、用户批准项目命令；授权场景执行
False/True 两次 hook，模型接收继续提示后结束，历史无 ToolResult。
另补进程超时/输出超限/重复取消的子进程退出及输出解析控制/诊断区分。
与存储、继续、预算、输入结束窗口联合 **79 passed（ffe292，14.25 秒）**，
静态通过（a87ef3，1144 文件、77 包），进程已退出 0。

仍未完成，不得由此关闭 A4：hooks.json/插件来源、MCP/async/非 root hook、
Windows 进程容器、CLI 显式信任界面、专门 HookStarted/Completed 呈现、输出
spilling、全部输出诊断字段、普通模型服务两协议的 wire 与实际冷恢复/执行中
输入交叉验证。未支持的 async/平台命令类型发警告，不偷偷同步执行。
当前批准指纹为 Corki 本地 JSON 规范化 SHA-256，不兼容原生 TOML version hash；
必须在后续配置契约对齐时处理，不能声称复制了原生批准记录。未读取/批准用户
现有 hook；测试只执行临时目录里的受控 Python 命令。完整目标范围不变。

## 实施中：独立执行记录已落地，Runtime hook 仍未接入

本地 storage/sqlite.py 新增 hook_executions 表及 claim_hook_execution /
complete_hook_execution，SessionRepository 声明对应契约，Volatile 存储继承
相同事务实现。初始化只新增表，不改旧工具或历史记录。请求、线程、Turn 与
execution_key 必须匹配，未完成 claim 报未知且不重跑，完成结果不可改写；
写操作复用 _joined_write，调用方取消不会遗弃后台数据库提交。

不能复用 tool_executions：load_turn_tool_outcomes 被 Runtime/恢复与工具清理
消费，hook 不是模型调用，混用会污染工具观察/副作用统计。新增记录与模型工具
账本、ConversationItem 分离。后续执行器仍需决定稳定 execution_key 和请求
快照，不能只靠内存里的“运行过”标志。

新增六个存储测试覆盖磁盘/RAM、完成复用、冷启动未知结果拒绝、四方并发仅一方
领取成功、线程/Turn/请求碰撞、不可改写及不进入工具结果/历史。联合存储与
真实模型继续/恢复集成 142 passed（f63f48，14.78 秒），静态通过
（237def，1141 文件、77 包）。无真实 hook 命令执行。

这是同一接入批次的持久化基础，不是完整功能交付：配置/信任、进程所有权、
outcome、_finalize 接入及 CLI 审核仍待实施，不能据此关闭 A4。下一步直接
使用此记录接执行器与真实 Runtime 测试，不继续把存储专项当成 hook 验收。

## 原生本地链路与不可省略的边界

1. session/mod.rs::build_hooks_config → Hooks::new → engine/discovery.rs::
   discover_handlers：逐层收集 config TOML/同层 hooks.json，再合并插件来源。
   同层两种表示同时存在会警告；不是从合并后的总字典随便取一个 hooks 字段。
2. hooks/src/config_rules.rs::hook_states_from_stack 只接受 User/SessionFlags
   的 enabled/trusted_hash，逐字段覆盖；Project/Plugin 不可写用户授权状态。
   engine/discovery.rs::hook_hash 对规范化事件、matcher、单 handler 配置生成
   版本哈希；无批准为 Untrusted，不匹配为 Modified，匹配才为 Trusted。
   enabled=false 即使 bypass 也不执行。项目信任只解决配置入场，不替代 hook 信任。
3. 普通本地 handler 仅 enabled 且 Trusted 执行；托管与 bundled cleanup 特例
   不是本目标可直接照搬的默认授权。官方云端管理和产品 cleanup allowlist 排除。
4. command_runner.rs::run_command 传 JSON stdin，使用会话环境快照、独立进程组，
   stdout/stderr 分开；timeout/异常退出清理进程组。正常完成允许明确脱离的 helper
   存活，不可把 timeout 清理误用于所有成功任务。后台任务由 session 持有并关闭，
   reload 复用其所有者，不能借重载遗弃已启动工作。
5. engine/mod.rs::ConfiguredHandler::can_apply_control_effects 只允许同步结果
   控制流程。stop.rs::parse_completed：空 stdout+exit0 允许结束；有效 JSON
   continue=false 请求停止；decision=block 必须有非空 reason；exit2 必须有非空
   stderr 才构成继续提示。非法 JSON、空 block、其他非零退出和执行错误记 hook
   failure，不伪造成功反馈，也不默认把根 Turn 判失败。
6. session/turn.rs 接 outcome：有提示的 block 写入上下文并继续，标记
   stop_hook_active；记忆合并的管理拒绝不进入无人值守继续循环。

## Corki 可复用入口及实际缺失

- config/layers.py 的 LocalConfigState 保留逐层 file/kind/disabled_reason/contents
  快照；项目信任在合并前冻结，项目不能批准自己。这是可复用的来源信息。
- 不能从 load_local_config 返回的合并 document 读 hook 授权：已信任项目仍可
  带 hooks.state，必须像原生一样从 user 层快照/明确宿主输入读取批准状态。
- plugins/agent_overlay.py::parse_overlay → agent_hooks.validate_hook_metadata
  仅是 legacy admission；没有 hook 状态存储、执行服务、结束 outcome，也没有
  CLI 审核入口。不得把既有有效 metadata 自动激活成命令。
- core/graph.py::_finalize 当前完成 pending input 检查后直接 completed；应在
  无待处理输入的结束候选点接入。预算、取消、恢复和新的 steering 输入仍须复核，
  不能绕过现有终态所有者或在 UI 回调里发起下一次模型请求。

## 实施顺序和验收门槛（不缩减最终要求）

第一批打通本地同步 Stop 命令的真实闭环：从独立配置快照发现 → 明确信任判定 →
持有执行/超时/取消清理 → outcome → graph 继续/完成 → 持久历史与 CLI 反馈。
同批必须证明无配置、未授权、项目伪造 state、配置变化后旧 hash、显式禁用均
不执行；已授权的 block/allow 实际驱动两次模型采样。不能只交付解析器/空接口。

随后补齐插件 hook 来源、MCP handler、后台无控制效果、子任务/记忆来源、
配置重载、CLI 显式审核与恢复窗口。保持这些路径在总审计中开放，不能因先完成
同步命令而宣布整个 Stop hook 或 A4 完成。

执行状态与模型采样 checkpoint 要区分：未知结果的 hook 命令不能静默重放；
需要在接入前确定已完成结果复用及不确定状态提示。测试使用临时目录脚本和
ScriptedModel/Mock MCP，不读取或批准真实用户机器上的 hook，不发官方请求。

## 本轮验证

现有 agent_hooks/local_config_layers/project_config_layers 三文件初跑
69 passed、14 skipped（68d33b，未配置测试 compiler）；随后设置当前真实
CORKI_TEST_SANDBOX_COMPILER 再跑 83 passed、0 skipped（681a76，3.99 秒），
session 63932 退出 0。该证据只验证可复用配置/元数据基础，不证明 hook 执行。
本轮没有修改生产代码/测试，也没有运行用户配置命令。
