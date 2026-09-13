# 协作模式与用户提问：实现前调用链审计

基准：Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc；范围为本地 Harness/CLI，
不复制官方模型目录拉取、官方账户或服务协议。本批仅审计，不能声称功能已实现。

## 原生已确认链路

- `session/turn_context.rs::mode/collaboration_mode` 读取冻结的 initial_settings。
  注释明确是 legacy Turn 访问器；Step 消费者应使用自己捕获的 StepContext.settings。
  因此模式更新不能简单修改全局变量让已开始工具读取最新值。
- `context/world_state/collaboration_mode.rs` 完整读取：模式、模型、指令 hash 构成
  snapshot；同值不重复注入；移除指令保留空快照以只清除一次；旧版只有 ModeKind
  的快照允许刷新。片段角色为 developer，kind 为 collaboration_mode.instructions。
  catalog 文本优先于 override，禁用 update_plan 时只剔除识别出的内置文本片段，
  不凭标题删除用户自定义文本。Corki 采用本地宿主配置，不因此接官方目录服务。
- `tools/handlers/plan.rs` 在 Turn 初始 Plan 模式拒绝 update_plan，返回 Observation。
- `request_user_input.rs` 拒绝 typed non-root，检查 available_modes，解析/规范化问题，
  将 is_blocking 设为 mode==Plan。该字段不等价于“handler 不等待”：两种模式都调用
  session.request_user_input 并等待它返回。
- `request_user_input_spec.rs` 已完整读取：模型 schema 含问题 id/header/question/
  options，normalize 要求每题非空 options，并把 is_other 强制设 true。1–3题、
  2–3选项等描述不应无依据全部变成额外 parser 硬限制。
- `session/mod.rs::request_user_input/notify_user_input_response` 登记 elicitation holder，
  在 active Turn 下登记 oneshot sender，再发 RequestUserInputEvent；响应按 sub_id
  取出 pending entry 并分配 acceptance_order，未知/迟到响应不创建新等待项。
  等待 receiver 被释放返回 None，由 handler 转成取消前未获回答的 Observation。

## Corki 当前状态与差异

`context/builder.py` 固定 mode.default；`protocol/settings.py` 完整读取，Thread 与
Turn 模型快照无 collaboration mode。`ToolContext` 无对应快照，CLI 无模式切换，
无 request_user_input 工具。现有 MCP elicitation/执行审批和 pending steer 提供
部分所有权/输入 UI 基础，但不是等价实现：不能把回答选项当授权 scope，也不能
把回答塞入普通输入历史、跳过独立问题 ID/结果契约或泄漏 MCP 服务身份。

状态：缺失。影响 A 的 Turn/Step 设置一致性、B 工具可用性、C 模式指令、F 模式与
提问交互。不是官方产品排除项，也不以无现有配置作为不适用依据。

## 下一实现门槛

1. 补齐原生模式修改的 admission 与持久恢复路径，以及 request_user_input 的取消
   清理和 available_modes 来源；目前未完整追踪，不能冒称全链已读。
2. 在已有模型设置/业务持久化结构上定义默认兼容的模式快照，验证旧记录仍按 Default
   恢复、只在正确边界发布，不串到已采样 Step 或执行中的工具。
3. 接本地模式指令增量与普通 function 工具门控，再接 CLI 显式切换和提问 UI。
4. 验收必须覆盖 Default→Plan→Default、相同模式不重复指令、冷恢复、并发/迟到回答、
   取消/关闭、非 root 拒绝、回答不授予执行权限、40/100列真实 PTY 与普通 HTTP 路径。

不能先加空枚举/拒绝开关就关闭缺口。本批无生产修改、无新测试运行、无活动测试；
上述路径和限制是实现依据，不是完成证据。整个 A–F 目标保持未完成。

## 注册门控与取消清理补充

`tools/spec_plan.rs` 仅在 experimental_request_user_input_enabled 时注册提问工具，
曝光为 DirectModelOnly：即使 Code Mode only 也给模型直接调用，不能开放嵌套入口。
`tools/src/tool_config.rs::request_user_input_available_modes` 默认仅 Plan；Default
需要 DefaultModeRequestUserInput，features 定义该特性默认关闭、UnderDevelopment。
因此不能把 Default 非阻塞提问当原生默认路径直接无条件启用。

`tasks/mod.rs::abort_all_tasks/abort_turn_if_active` 先 handle_task_abort 等待取消被
观察，再发 abort lifecycle，最后 input_queue.clear_pending。后者在 Turn 锁内清除
pending waiters 与排队输入；`state/turn.rs::clear_pending_waiters` 包含独立的
pending_user_input。顺序目的明确：不要先释放等待者，使取消误表现为模型可见拒绝。

Corki `mcp/elicitation.py` 已完整读取：pending key 是 server+本地生成 request_id，
结果是 accept/decline/cancel 与可选 content/_meta；取消会 shield/join UI delivery
任务后清 pending。可参考其任务所有权模式，但该协议缺 Turn 回答顺序、问题答案
契约且携带服务/授权语义，不应将新工具伪装成 MCP server 或 shell_approval。
提问实现需要独立事件/响应契约，UI 焦点基础可复用，回答不触发授权发布。

本轮未改生产、未执行测试。下一步剩模式变更 admission/恢复与具体本地设置接线，
再实现；不重复把已确认的注册/清理路径列为未知，也不提前声明提问功能可用。

## 设置合并与启动初值补充

`session/step_settings.rs::StepSettings::apply` 与测试
`collaboration_replacement_wins_and_effort_clear_remains_sparse` 确认：完整
CollaborationMode 替换优先于同次 model/effort 字段；省略模式时才对现有模式中的
model/effort 作稀疏修改；显式 effort=None 清除，非省略。完整替换不能只保留模式名。
StepSettingsUpdate 注释要求针对各 owner 单独 merge，不把从未来 Thread 合并出来
的整份设置直接覆盖当前 Turn。

`session/mod.rs` 初始化 SessionConfiguration 前显式创建 ModeKind::Default，
包含当前 model/effort、developer_instructions=None。此证据只证明初始化，未证明
宿主恢复流程不再覆盖；因此撤回“冷启动应直接沿用上次模式”的潜在设计假定。
后续必须追踪 app-server/TUI 的模式重新提交与 rollout 参考快照用途，分别定义
新会话默认、旧历史上下文和正在恢复的执行快照，不凭字段可持久化就决定恢复语义。

Corki `core/model_settings.py` 和 `core/step_settings.py` 已完整读取：未来默认提交
在 lifecycle permit 内先落库后发布，取消仍 join；当前 Turn 更新有独立 owner/
done/current 身份检查，采样持有不可变快照。可沿用这些所有权边界，但新增模式
必须覆盖 capture/bind、恢复、上下文与实际工具，不只扩 dataclass。

本批是新证据收敛，未修改生产、未执行测试。下一步不再重读上述合并函数，而是
定位宿主恢复提交处，再形成端到端实现批次。核心目标仍未完成。

## 宿主同步/重新提交路径

已定位并读取 TUI `chatwidget/session_flow.rs`：收到 SessionConfigured 时先更新
model/effort；若返回 collaboration_mode，则 set_effective_collaboration_mode，
否则按 initial_collaboration_mask(config,catalog,model) 初始化。并非直接从历史中
最后一条模式指令推断当前模式。

`chatwidget/settings.rs::submit_collaboration_mode_settings_update` 通过
SubmitThreadOp/override_turn_context 显式提交完整 effective mode；
`input_submission.rs` 在模式启用且 active mask 存在时随输入再次携带 effective mode。
`session/session.rs` 的配置快照把当前 step_settings.collaboration_mode 放入
SessionConfigured；app-server thread_summary 也从 config_snapshot 返回它。
这将“复连已有 Session 当前设置”与“冷创建 Session 初始 Default”区分开。

实现约束由此明确：Corki 的历史回放不能自行改变宿主当前模式；显式选择通过设置
发布路径进入新输入/后续适用 Step；正在恢复的执行仍应使用其已持久化快照。
冷创建默认与复连现有 Runtime 不可合并成一种恢复。配置启动模式可由 CLI 宿主显式
选择，不把模型说出的 Plan 字样当切换命令。

本批无生产修改与新测试。剩余执行前核对：原生 override_turn_context 在活动 Turn
上的发布目标，以及 Corki snapshot/ledger 旧数据的迁移接点；不再重复定位 TUI 同步
和输入提交入口。模式、提问及整个 A–F 仍未实现完毕。

## 首批真实 Runtime 模式实现（尚未完整验收）

### 后续批次：普通 request_user_input 已接入 Runtime，CLI 待接入

原生 protocol/request_user_input.rs、handlers/request_user_input_spec.rs 已完整读取；
config/mod.rs::resolve_experimental_request_user_input_enabled 默认 true，另一个
DefaultModeRequestUserInput feature 默认 false。question/options 数量限制为指导，
normalize 只要求每问题有非空 options 并强制 isOther=true；隐藏 isSecret 布尔值
通过 serde 接受。回应为 answers → question ID → answers 字符串数组。
handlers/request_user_input.rs 先禁止非 root，再检查 mode；两种 mode 都 await，
仅事件 isBlocking 在 Plan 为 true；不是 Default 下自动选第一项。
session::request_user_input 保存 Turn 所有的 waiter，发事件后等待真实回应；
notify_user_input_response 取走 waiter，迟到回应不会创造新请求。

已新增 protocol/user_input.py、core/user_input.py、tools/builtin/user_input.py，
接入默认注册表、GraphRunContext/ToolContext、TurnRun 所有权、RuntimeEvent 和
Runtime.respond_user_input(turn_id, call_id, response)。普通 DIRECT_MODEL_ONLY 曝光
使 code_mode_only 请求也保留直接调用入口，但 nested tools 不可调用；不启用原生
协议。回应只产生普通 ToolResult，不进入普通 UserMessage 或任何审批授权分支。
pending 同时绑定 Turn 实例与 call ID，过期/重复/错位回应返回 false，回应结构校验
失败不消费 waiter，接受时复制数据以防宿主之后修改对象。None 撤销单条问题时返回
Observation；Turn cancel/close 仍传播 CancelledError，并清除 waiter。

配置：tools.experimental_request_user_input.enabled 默认 true；
features.default_mode_request_user_input 默认 false。全部通过普通本地配置解析，
无账户、地址推断、服务请求或凭据。保留与原生一样的 root 限制和可选模式行为。

验收覆盖实际 model→函数→问题事件→宿主回应→下一请求 Observation；Plan/Default
feature × direct/code_mode_only；Default 禁用、缺选项、显式禁用工具、非 root；
nested Code Mode 负向调用；错误回应不消费、错位/重复/迟到回应、对象复制、取消/
关闭/撤销、阻塞事件发送取消清理；还覆盖原生宽松计数和 isOther/isSecret 规范化。
首次测试曾误期望 Runtime cancel 只返回终态、不抛取消，以及 QuickJS 错误必含
工具名；已依据既有真实契约修正，未因此改写取消控制流或开放 nested 工具。

未完成：CLI 对新问题事件的模态接入、选择/自由输入/显式提交/焦点恢复、问题取消
面板、真实 PTY；question/response 在故障窗口的冷恢复组合测试；原生 retained
verified-answer 扩展的适用范围还需独立核对，未以基本 ToolResult 持久化冒充它。
原生 acceptance_order 服务于后续 retained context 消费，本批不宣称已实现该扩展。
本批解决普通 Runtime 提问链路，不代表整个模式功能或 A–F 已完成。

最终组合回归：request_user_input、broker、模式、模型曝光、Code Mode/生命周期、
CLI input ownership，以及全部 unit tools/config/core：1591 passed、1 skipped，
23.14s，exit 0（9ca359）。跳过为真实文件系统不接受非 UTF-8 文件名。Ruff、1118
文件 format、compileall、77 包兼容检查通过（8868f7），所有测试进程已退出。
这是范围匹配回归，不是全仓最新源码全量验收，也没有执行真实模型工具选择评估。

本批修复前的审计已确认缺失 mode 状态、上下文切换和 PlanHandler 模式拒绝；
现在开始实现，不能继续把这些局部项笼统记成“完全缺失”，也不能把整个模式功能
改记为已完成。

新增源码结论：session/handlers.rs 的 Op::ThreadSettings 调 thread_settings::update；
Op::TurnSettings 则独立调 apply_turn_settings。step_activation.rs 的当前 Turn
更新仅接受 model/effort/summary/tier/reviewer，**不接受 collaboration_mode**。
因此 Corki 的完整模式替换只进入 update_thread_settings，既不新增运行中模式切换
API，也不把 Thread 修改回写已采样 Turn。原生完整模式替换优先于同次 model/effort。

已接入：protocol/collaboration.py 的完整替换值；现有 ThreadModelSettings、
ModelSettingsSnapshot、capture/bind、Runtime 提交/初始化保存；SQLite 原表增列
迁移和旧快照默认值；CLI 现有 for_directory 的显式恢复设置组读取新字段。
未添加地址、密钥、账户或 provider 特殊协议。完整替换后稀疏 model/effort 编辑
仍保留模式；Step 模型切换重新解析 metadata 时必须保留当前 Turn 的模式和指令，
不能从已经改变的未来 Thread 默认值重新带入。

ContextBuilder 从当前 Step 设置生成模式贡献，None 使用本地内置模板，非空自定义
指令使用本地 raw 模板，空串撤销原指令；沿用现有 world-state 增量替换/撤销机制，
原始历史并不删除。Plan 模板来自 pinned 本地源码并已补第三方声明。
ToolContext 则使用原生 handler 对应的已入场 Turn 模式；direct 和 Code Mode 中
update_plan 都返回 Observation 错误，不提交 PlanUpdated，切回 Default 新 Turn 恢复。

验证过程：真实模式测试在接入上下文/handler 前失败（89d156，错误地产生计划事件），
接入后通过；Code Mode 也走实际嵌套调用。真实 checkpoint 停在 call_model 之前，
新宿主 Default 下恢复仍使用旧 Plan 快照。旧表去除新列后重新打开自动迁移，旧 payload
去除模式字段后按 Default 读取。自定义指令测试最初误要求抹除历史，已依既有增量
契约改为检查最新 mode.plan 的撤销/替换记录，而非弱化为只看最终回答。

仍缺：request_user_input 普通工具、回应归属与取消、CLI /plan/模式呈现/问题面板、
提案块呈现、模式上下文针对连续压缩的组合验证。Plan 规则是模型指导，不把它声称
为新增文件系统安全沙箱。所有 A–F 未完成，本批模式能力仍标为部分一致。

本批终态验证：组合 Runtime 模式/Thread 设置/活动 Step/计划契约/上下文 kind/
手动压缩生命周期，以及全部 unit context/config/storage/prompting、工具执行器、
计划渲染和真实 PTY：1238 passed、1 skipped、3 warnings，28.11s，exit 0（3a5533）。
跳过为真实文件系统不支持非 UTF-8 文件名；警告为 Python forkpty 多线程提示。
Ruff、1113 文件 format、compileall、77 包兼容检查通过（28c1e7）。这是范围匹配
的组合回归，不是全仓最新源码全量验收；所有测试进程均已退出。

## 可恢复设置与初始化结论修正

补充源码证据：rollout/src/policy.rs 将 ThreadSettingsApplied 纳入持久化；
state/src/extract.rs::apply_event_msg 仅将其 model/provider/effort/cwd/权限提取到
元数据，没有恢复 collaboration_mode。session/mod.rs::update_settings_if 尾部仅
通知 contributor、刷新权限网络代理并安排 MCP prewarm，再返回 commit。因此
“事件可持久化”不能推出“历史加载自动恢复模式”；实际消费者仍需分层追踪。

`protocol.rs::ThreadSettingsSnapshot` **包含完整 collaboration_mode**；先前仅检查
Session 初始化 Default 不足以判定最终冷恢复模式。上述“初始 Default”只保留为
构造初值事实，不得被实现者解释为忽略已保存的显式 Thread 设置。

`app-server/thread_processor.rs::thread_revert_response` 在卸载前捕获 config、
restorable_thread_settings 和 MCP extension handles；恢复历史创建替代 Runtime 后
调用 restore_thread_settings(settings)。`codex_thread.rs` 将这份 overrides 转成
SessionSettingsUpdate→update_settings，说明该路径是从仍加载的 Runtime 保留当前
设置，而非从被回退的历史片段猜测模式。此 Runtime replacement 路径与普通冷启动
仍须区分，不引入官方认证参数作为 Corki 依赖。

`session/thread_settings.rs` 已完整读取：普通设置更新获取与压缩 checkpoint 共用的
persistence permit，构造 StepSettingsUpdate、update_settings 后发 ThreadSettingsApplied；
fork/compaction 使用 applied_event 构造当前完整快照。`session/mod.rs::update_settings_if`
在同一 state 锁内校验候选和 admission predicate 后发布 session_configuration，
commit 持有对应 snapshot；不是直接改已存在 TurnContext.initial_settings。

下一实现依据：显式 Thread 设置与已采样 Turn/Step 快照分开；模式随完整设置记录，
旧记录缺字段采用兼容初值，恢复中若有显式设置必须应用。原生历史重建对
ThreadSettingsApplied 的具体消费仍需确认，不能把本批 replacement 实证外推成
所有重建路径。此轮无生产修改、无测试运行；diff 检查通过，完整目标未完成。
