# C1/C2：按实际生产入口拆分 world-state section

## 2026-09-22：默认普通 Step 与条件能力重新分界

增量组合补证：`test_default_world_state_combination.py` 新增同线程插件与
deferred 工具目录的存在→同时撤销→同时恢复→冷 Runtime 未变化 Step。
Codex `WorldState::render_diff` 保留 section 插入链，
`updates::merge_contextual_fragments` 仅合并相邻同角色片段；Corki 每次
Step 增量也保留顺序。实际新片段在撤销与恢复时均为工具目录先于扩展插件
目录；插件通用指引只在首次注入，冷恢复不追加重复上下文事实。
首次测试错误地把已 sealed 的热 Runtime 工具注册表交给冷 Runtime，
构造前拒绝；改为冷 Runtime 自有注册表后两例通过，相关五文件
**45 passed / 56.04s**，4 workers/loadfile/禁重启。本批只有测试修改，
不把这项组合扩展为所有 section 状态转换的全称证明。

同一请求组合复核：`test_default_world_state_combination.py` 同时启用
项目 AGENTS、权限、plan 模式、本地环境、初始记忆、宿主技能、插件指引/目录、
延迟工具目录，并在冷启动同线程后再次采样。Codex 首窗将 world-state
片段先按 developer/user 分组，故不能把源码 section 插入链误当作跨角色的
最终线性顺序；Corki 的 `freeze_context_messages` 也按角色分组。对照 Codex
`session/world_state.rs` 的 plugins→tools→扩展及 `session/mod.rs` 的首窗分组，
修正 Corki deferred 目录被当作初始扩展提前的问题：它现为 WORLD_STATE，
插件指引在其前，插件目录在其后。组合及相关上下文测试 **496 passed /
12.56s**，4 workers/loadfile；冷恢复中历史前缀未重写，记忆与目录各仅一份。
此证据覆盖该组合，不等于所有条件能力已对齐。

重新按 Codex `session/world_state.rs::build_world_state_for_step` 的实际插入链核对：
model/personality→token budget/guidance→realtime→agents.md→permissions/
compact permissions→collaboration→persistent→environment→多环境指导→
Apps（用户排除）→plugins/tools→扩展→multi-agent→managed developer。
这些是 section 的比较次序，不等于全部都在默认配置生成正文。
Corki `ContextBuilder.build`、`Runtime` contributor/step contributor 与
`PromptSlot` 对应默认普通模型、项目规则、权限、协作模式、本地环境、
技能/记忆/插件、deferred 目录、managed 和扩展的生产入口；初始窗口
memory 指导与每 Step world-state 来源由 `InitialContextContributor` 明确分离。
本轮将 `tests/unit/context` 与七个角色、初始窗口、模型/风格、目录、环境、
managed 集成文件同批执行，**483 passed / 8.23s**，4 workers/loadfile/
禁重启，实际退出 0。首次命令误写 `test_runtime_environment.py` 导致
pytest 退出 5、零收集；已按实际文件名 `test_runtime_environment_context.py`
重跑完整预定范围，失败命令不计通过。

当前默认路径未由这次核查发现新的缺失 section，但这批测试并非所有 section
同一请求的全排列证明，C1/C2 不据此整体核销。Codex 的 Persistent 需有效
`ReasoningEffort::Persistent` 及宿主再次采样；Corki 当前只有普通 provider 参数，
没有本地续行调度。MultiAgent V2 需真正子 Agent 编排，Corki 现有来源标签/
Hook 名称/模型 metadata 均不能代替。DeferredExecutor 默认关闭，但跨机器
环境能力不是 MCP HTTP 绑定；尚未实现就不能只注入该指导。Codex Realtime
是 intermediary/转录角色，Corki 的直接文字 steer 不应复制其角色文案。
这些条件能力须按用户范围决定是否纳入本阶段；不因普通路径通过宣称它们
一致，也不借它们无限扩大已明确排除的官方产品/专有模型协议。

自定义扩展项已从差异诊断进入实现验证：新增 WorldStateContributor 接入
Runtime，生产者控制 diff 与 legacy/retained matcher；显式来源标记区分旧
PromptContribution，来源消失写静默比较状态而非通用撤销。冷恢复、取消、
自动/手动 compact、普通 HTTP 两模式已有证据，见 extension-world-state-contract.md。
608 定向通过不代替全量。33685 已失败，stdin 等待断言随后修订；51160 最终
结果未保留，新全量 25019 正在运行，详见 full-regression-25019.md。下方“没有对应接口”
是发现时记录，不再描述当前实现；其它条件 section 未据此宣布完成。

## 多 Agent 版本与名称边界（2026-09-14 只读复核）

完整读取 session/multi_agents.rs 及 world_state/multi_agent_mode.rs：角色正文
只在有效版本V2且根来源/ThreadSpawn子来源下生成；配置正文优先于目录正文，
显式空字符串阻止回退。策略自定义正文限制400 tokens；无自定义正文才由有效
effort推导Proactive/ExplicitRequestOnly。策略撤销不是通用撤销文案。
features/src/lib.rs:1252 的 Collab/multi_agent 默认 true，而紧邻的
MultiAgentV2 默认 false。因此“V2默认关闭”不能外推成整个多Agent默认关闭。
进一步核对 config/mod.rs:1540–1578：显式V2优先，其次agents_enabled=false
禁用；无覆盖时接受模型目录版本，最后按Collab选择V1/Disabled。
session/mod.rs:3868 的 resolve_multi_agent_version_for_model 用OnceLock固定
首次选择，后续仍应用显式配置覆盖。因此目录也可选择V2，并非必须显式开启V2。
实际工具注册/执行器链仍需继续追踪，不能仅凭功能表将整个能力排除。
已向用户询问本轮是否包括真正子Agent创建/消息/等待/取消，还是独立留后续；
回复前保持范围待确认，不把普通模型路径误判为不能支持子Agent。

Corki protocol/collaboration.py 的 CollaborationMode 只有 default/plan；
pre_tool_hooks/post_tool_hooks 中 spawn_agent 名称仅用于Hook匹配/转换，
model_context/bundled_models 的 multi_agent_reasoning_effort 仅是元数据。
这些均不是子线程所有权、消息投递或调度实现证据。本轮未新增编排能力，
未将占位字段标为一致；回归25019运行期间没有修改生产或测试。

## Realtime 的角色边界修正

完整读取原生 prompts/templates/realtime/realtime_start.md 后，不能将该指导
简单称为“语音提示”。关键是 backend executor 位于 intermediary 之后：输入
是转录，输出由中间层消费/可能摘要，并非用户直接对话。转录可能存在识别错误，
但“文字内容”本身不证明它就是 Corki 的直接文字 steer。原生
RealtimeConversationManager.mode_instructions 返回会话模式的独立指导快照，
world_state 使用它覆盖配置的 start/end 文本，active 转换控制注入。

Corki Runtime.stream(realtime=True) 激活 RealtimeController，steer 记录有身份的
UserMessageItem，在 sampling boundary 处理；prompts/realtime/start.md 明确
是用户直接 live text channel。没有中间层转录角色或反馈路由契约。当前直接
文字入口不应注入“用户不直接与你对话”的原生正文，也不应将现有 slot 当作
后台委托会话已实现。此结论仅说明当前入口角色不适用该段正文，不把新增通用
intermediary 宿主能力标成官方服务，也不声称整个 Realtime 能力已经对齐。
本轮未新增实时协议、供应商请求或生产/测试变更。

## Persistent 策略语义澄清

已完整读取 core/assets/persistent_mode.md 与 context/world_state/persistent_mode.rs。
原生资产要求任务结束后再次采样时处理原任务相关跟进，明确不得从历史指令
推导新授权；安全只读跟进也须在用户授权范围内。它不是无条件扩大自主权限。
正文不负责调度采样，宿主再次采样是独立条件；不能将 prompt 注入宣称为后台
调度器已实现。原生按 effective ReasoningEffort::Persistent 注入，basic guardian
不注入；模型目录正文可覆盖，显式空正文抑制默认值，hash 比较/替换/撤销。

Corki settings.reasoning_effort 是普通 provider 参数字符串，无对应本地策略
producer。差异是本地模式与注入生命周期，不能笼统标为官方协议或越权模式；
普通字符串兼容也不代表宿主启用了模式。后续实现应先确定显式本地模式入口与
宿主调度契约，保留“不扩大授权”边界，不靠伪造 openai 模型能力启用。

## 多环境能力追踪补充（全量 81536 期间只读审计）

进一步追踪：`features/src/lib.rs` 将 DeferredExecutor 标为 UnderDevelopment、
default_enabled=false。`session/session.rs` 构造 ThreadEnvironments，传入该
开关决定 non_blocking_snapshots，然后 update_selections → snapshot → skills/
plugins 预热。`environment_selection.rs::resolve_environment` 同时等待共享
executor 连接与宿主 attachment 配置；两者都就绪才解析 shell/platform 等信息。
snapshot 在非阻塞模式用 shared resolution 的当前状态区分 Starting/Ready/Failed；
子线程继承只保留 Ready，Thread-owned 配置重新推导，Owner 配置保持。
更新选择时先发布配置再唤醒等待者，移除时显式终止对应连接监听任务。

`exec-server/src/environment.rs::EnvironmentManager` 支持宿主提供 transport
snapshot（校验 ID、保留 local 身份、默认环境、重复项后才开始连接），不只
存在带官方账户参数的 Noise 注册路径。所以不能把通用多环境 Harness 能力
归类为官方服务排除项；也不应因当前缺少执行器而宣称一致。Corki
`Runtime.request_mcp_runtime_context` 只发布 MCP transport 选择并请求预热，
没有上述 shell/filesystem 与 attachment readiness 的组合契约。

已向用户提出非阻塞范围确认：本次是否包含宿主挂载的远程执行环境，或将该
默认关闭的可选能力留到后续。在回复前不新增全套执行器、不将本项标记完成
或不适用；继续全量验证及其它明确在范围内的工作。不会引入官方注册服务。

原生准确路径是 `core/src/context/world_state/environments_instructions.rs`，
不是 `session/world_state/`。`session/world_state.rs:248` 同时检查
include_environment_context 与 DeferredExecutor；`tools/spec_plan.rs:1146`
用相同功能开关注册 wait_for_environment。其 handler 在当前 step 的 ready
集合命中时直接返回；否则只允许选中的 starting 环境，等待该环境就绪，失败
作为 RespondToModel 要求继续使用其它环境。不存在的 ID 不会回退本地。
正文宣告的能力包括独立机器/工作区、文件、命令、规则、skills/plugins/MCP，
不是一个 HTTP 连接可用性提示。section 首次启用才注入，Unknown/已启用和
禁用均不产出 diff；不能照搬通用撤销逻辑。

Corki `mcp/runtime_environment.py` 明确只提供宿主固定的 HTTP transport
绑定：resolve 校验 environment_id、禁止远程 stdio 与本地 header helper，
缺失绑定不回退本地；MCPRuntimeContext 是不可变选择快照。该类型文档明确
不实现 executor discovery。`execution/terminal.py` 的审批 action 使用
local 环境身份；执行工具/context 中未找到 wait_for_environment 入口。
所以已有 MCP 绑定不能证明多执行环境的 starting→ready 调度已实现。

本项仍是条件能力差异，尚未判断整个 DeferredExecutor 能力是否属于本阶段
应补的宿主扩展；不能因为缺少开关就直接标不适用，也不能只注入正文并宣称
一致。下一步追踪原生 TurnEnvironmentSnapshot/starting 的宿主注册来源，
与 Corki Runtime 的实际宿主扩展契约比较，再确定实现范围。没有新增官方服务、
假工具或多 Agent。全量仍在运行，未修改生产或测试。

本轮为源码清单，不是全项验收证明。基准为Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc，
入口core/src/session/world_state.rs::build_world_state_for_step；Corki为
context/builder.py::build与world_state.py::changed_context_items/_render_update。
不得再把剩余工作只写为“其他typed section继续核验”，也不能拿全量通过证明缺失能力。

| 原生section/来源 | Corki当前映射与证据入口 | 状态及下一步 |
|---|---|---|
| model / personality | model.instructions / personality，model_instructions.py、personality.py | 已有模型快照与风格专项；旧reference适用性已核定，见context-reference-compatibility.md |
| token budget / context_window_guidance | 本地预算、guidance片段和普通摘要路径 | 预算/旧标签/替换撤销已有专项；按context-budget-review.md核验精度限制，不恢复官方history服务 |
| agents_md | project.agents，InstructionManager | 角色、项目规则替换/撤销已有证据；仍需在最终来源总表确认与其他扩展的顺序 |
| permissions / compact_permissions | permissions.py的两个key | 已有原生约束合并与快照专项；不把文字描述当权限执行器 |
| collaboration_mode | mode.default/mode.plan归一比较key | 已有旧标签/切换专项；见context-snapshot-wire.md |
| environments | environment.py typed快照 | 已有权限字段、删除/变更与损坏旧元数据恢复；不同于下面通用多环境提示 |
| plugins_instructions / tools | plugin_guidance与deferred_tools | 指导保留/目录失效已有专项；tools仅普通平铺定义，本地分组不恢复namespace协议 |
| managed_developer_instructions | 受宿主验证的managed片段 | 已有替换/撤销/来源授权专项 |
| extension contributions | skills/memory/plugin/custom contributor，各自slot与input ownership | 不应套用所有section同一撤销规则；仍需逐生产者确认来源/删除/顺序，不能以通用接口存在视为完成 |
| apps_instructions | 不实现官方Apps指导 | 用户明确排除；普通同名MCP服务器不等于官方Apps |
| realtime | realtime.active文字追加输入提示 | 与原生实时会话提示不是同一能力；下述条件待独立核定，不复制语音提示来假装支持 |
| environments_instructions | 未发现对应多执行环境通用指导producer | 原生须include_environment_context且DeferredExecutor启用；需核对Corki执行环境能力范围，不能与已实现MCP readiness混为一谈 |
| persistent_mode | 未发现独立section/模板/撤销链 | 原生非basic guardian且effective reasoning effort为Persistent时生效；具体启用与续行授权边界待核定 |
| multi_agent_usage_hint / multi_agent_mode | 有MULTI_AGENT slot和模型effort元数据，但未发现对应section producer | 原生须MultiAgentVersion::V2及符合来源条件；占位slot不等于V2编排或动态策略已实现，需先核对能力范围 |

## 新明确的条件分支，不可当作既有功能完成

1. **Realtime**：原生world_state/realtime.rs比较active快照，false→true发start、
   true→false发end；Unknown在active时重新发start。其内容来自conversation
   mode instructions和codex-rs/prompts/templates/realtime/realtime_start.md。
   Corki realtime/context.py只在文字stream的realtime_active时贡献realtime.active，
   prompts/realtime/start.md明确是live text channel，撤销走通用section路径。
   这不是原生实时语音会话的等价实现，也不应为“对齐文案”引入供应商专用实时API。
   A5已核验的文字输入排队/取消行为不因此失效；两种能力须分别判断。

2. **多环境指导**：原生world_state/environments_instructions.rs只在启用且旧状态
   不是Known(true)/Unknown时发布，不在禁用时发通用撤销。正文明确涉及不同机器/
   工作区及starting环境的工具/规则/技能不可用。单纯给Corki插这段文字会宣称尚未
   证明存在的执行环境选择能力，故本轮未添加；后续先审执行环境生产链。

3. **Persistent**：原生persistent_mode.rs用非空正文hash作快照，变更时替换，
   禁用时撤销，Unknown视为旧正文存在；默认资产包含完成任务后自主跟进策略。
   Corki配置接受普通reasoning_effort='persistent'（496839），但只是允许供应商
   参数字符串，不足以证明用户授权了额外自主续行。不能从接受字符串直接推导
   应无条件启用新Harness模式，也不能仅因是原生effort名字就把本地策略算官方服务。
   本项保持待核定，不能通过新增空枚举关闭。

4. **MultiAgent V2**：session/multi_agents.rs在非V2时返回None，internal及非
   ThreadSpawn子来源不接受相同策略；V2按显式配置/目录提示和effort求出模式。
   world_state/multi_agent_mode.rs把退出Proactive/Unknown回退到ExplicitRequestOnly，
   自定义提示有400token截断，并将usage_hint hash纳入比较；usage_hint自身不采用
   通用旧指导撤销。Corki当前slot只是插入位置，模型multi_agent_reasoning_effort
   只是偏好元数据，不证明这些行为已接入。不得未经核定扩大成全套编排器实现。

本轮只读源码并记录具体缺口/适用性待核定项，未修改生产、测试或配置，未创建
子Agent，未执行供应商请求。15504全量基线仍有效；该清单使下一步可针对明确
入口而非无穷排列已有快照测试。C1/C2仍未整体关闭，CLI暂停与E4待确认均保持。
