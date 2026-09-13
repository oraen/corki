# F1 计划文本换行

## 解释原文与显示的边界修复

原生 protocol/src/plan_tool.rs 的 UpdatePlanArgs.explanation 为 Option<String>，
handlers/plan.rs 将解析后的 args 原样发 PlanUpdate；仅 TUI display_lines 裁剪。
Corki 工具原先提前 strip 并把空串/纯空白变 None，导致事件和持久历史丢失数据。
现改为读取原始 explanation，显示层现有 strip 保持不变。本批不变更 schema 的
显式 null 接收边界，也不声称 Plan 模式已实现。

test_plan_contract 的 direct/Code Mode × 缺省/空串/纯空白/带空白文本，修复前
6 失败、2 通过（e4ed47），修复后与存储、渲染、真实 PTY、执行器组合 42 通过
（d3cd71，3 条 forkpty 警告）；direct 额外检查持久结果原文。首次组合命令误写
test_builtin.py，退出 4、未运行测试，随后以存在的路径重新运行，不计作通过。
静态 Ruff、1111 文件格式、compileall、77 包依赖检查通过（0c0c69）。

Codex pinned 源码 `tui/src/history_cell/plans.rs::PlanUpdateCell::display_lines`
把解释 trim 后放在步骤前；步骤原文不 trim，使用 adaptive wrapping。
`tui/src/wrapping.rs::adaptive_wrap_line_mixed_line_counts_leading_spaces_before_first_word`
明确测试含 URL 时前导空格占用宽度且保留。步骤状态样式与缩进和 Corki
`cli/terminal.py::show_plan` 对应。

确认差异（修复前）：`cli/wrapping.py::wrap_plan_text` 在 URL 混合分支使用
`separator = space if current else ""`，首词前的空格因此被删除。
影响：计划源文本缩进发生改变。优先级：F1 可见格式差异，不涉及权限或官方服务。
方案：保留首词前 whitespace，同时继续丢弃因折行而出现在新行首的词间分隔符。
验收：真实 TerminalUI.show_plan 的含 URL 步骤保留原始三个前导空格；已有 URL
完整性、长词折行、状态、解释和 markup 字面量场景仍通过。
本批不把单个场景外推为所有 Unicode/极窄列与原生快照完全一致。

已实现：URL 混合分支保留首词前空白，普通首词连同空白参与宽度分行；后续词间
分隔仍按原规则折行，URL 不拆开。改前新增 TerminalUI 测试失败（ceeffc，缺三个
空格），改后计划/转录/历史缓存联合 17 passed（ad78b1，0.66 秒）。修改文件 Ruff
通过（15aa79）、format 已运行（682752）、diff 通过（cf7e41）。
尚未为此新改动执行真实 PTY 或完整 CLI 联合回归；不以这 17 项关闭整个 F1/F6。

后续真实 Runtime PTY 先红：三项失败、306 CLI 单位通过（a98a5e）。定位第二处
`planning/models.py::validate_plan` 将 step.strip() 写入状态；原生
`core/src/tools/handlers/plan.rs` 的 serde 解析→PlanUpdate 与
`protocol/src/plan_tool.rs` 的 String 字段不改写步骤文本。方案：只做现有非空检查，
保存原文，验证实际事件/渲染/缩放链，不仅直接调用 UI。
另发现原生 Vec/String 无非空限制、single-active 是描述要求而非此 handler 检查；
Corki 当前加强了这些约束，需继续单独核对工具规范及故障行为，不据此宣称完全一致。

解析器已保留 step 原文。真实 Runtime→update_plan→PlanUpdated→Application→TerminalUI
在 40→100、100→40 PTY 验证缩进、URL 完整性与新输出宽度；另验证旧计划 resize
重绘时同样保留缩进且 composer 草稿不丢失。恢复缩进改变合法折行位置，因此结尾
短语断言改为合并 whitespace 后检查，仍严格检查 URL 一次完整出现及三个源空格。
最终整个 CLI 单位与计划 PTY：309 passed，3 forkpty 弃用警告，9.31 秒（f48065）。
工具 builtin/executor 另 23 passed（56c8d6）。无活动测试进程。
全项目 Ruff/1110 文件 format/compileall 通过（ad082e/9ebc6c/71097c），77 包依赖
检查通过（f21955）。本批闭合该缩进问题的真实链路验证，但不关闭全部计划契约差异。

计划契约后续审计：`plan_spec.rs::create_update_plan_tool` 的 array/string 均无非空
约束，single-active 仅为 description；`plan.rs` handler 仅 serde 解析并发送事件。
Corki minItems=1 与 validate_plan 的非空/active_count 硬拒绝使清空计划等合法原生
输入变成错误 Observation。方案：保持数组、对象、字符串、状态枚举校验，删除
原生没有的语义硬限制，继续保留 single-active 模型指导。验收为真实 Runtime
输入空计划/空文本/双 active→结果回灌→后续空计划→持久历史冷读取，不能仅单测 helper。

已移除 minItems、非空字符串及 active_count 硬限制，继续保留类型/状态校验及说明
中的 single-active 指导。三项 Runtime 测试改前失败（b86af5）、改后通过；扩大全部
五文件（新 Runtime、builtin、executor、plan rendering、plan PTY）41 passed、
3 forkpty 警告（2a0129，6.04 秒）。旧 executor 测试把空数组当非法参数，已改为
真正非法的字符串 plan，继续断言错误 Observation；未删该故障测试。
新测试证明清空事件只发送一次、两次结果 state_update 精确保留、冷读取不改写历史，
不冒充断电 checkpoint 恢复或真实模型计划质量。Ruff/format 1111 文件通过
（8a3459/7ce355），compileall 通过（27d8a4）。计划其他结果协议/Plan 模式边界仍需
核对，不由此关闭完整 B/F；本批所有测试进程已结束。

结果契约差异：原生 PlanToolOutput::to_response_item 固定返回 `Plan updated`，
code_mode_result 返回空对象，解释仅由 PlanUpdate 事件携带。Corki 当前把解释
当作普通结果文本，且未设置 CodeModeOutput，导致嵌套调用收到字符串而非对象。
修复方案：普通确认与结构化空对象分离，继续保留计划解释事件和隐藏重复工具显示。
验收：direct/code_mode_only × 有无解释，真实模型下一 Step 检查返回值且事件恰好一次。

结果修复完成：四组合改前失败（1169ff），改后 plan contract、Code Mode lifecycle、
builtin/executor、计划 PTY 联合 43 passed、3 forkpty 警告（02e77e，9.59 秒），无跳过。
Code Mode 测试实际执行 `tools.update_plan` 并序列化返回对象，非模拟嵌套返回。
解释有无均不改变确认正文/对象；事件仍独立携带解释。Ruff 全项目、1111 文件 format、
compileall 通过（5e2efe/d53250/de1384）。仍未覆盖 Plan 模式拒绝边界和完整 B/F，
不改变普通模型传输或恢复官方协议；无活动测试进程。

## 模式边界：新确认的缺口，未实现

原生 `protocol/src/config_types.rs::ModeKind` 有 Default（默认）和 Plan，均可在 TUI
显示；`features/src/lib.rs` 的 collaboration_modes 标记 Removed/default_enabled=true，
不能误读为必须依赖官方账户的可选未启用功能。`plan.rs` 在 Turn 的 ModeKind::Plan
下返回错误 Observation，其他模式解析并发布事件。

Corki `context/builder.py::build` 固定贡献 `mode.default`/`modes/default`；
`cli/commands.py` 无模式切换入口，`tools/base.py::ToolContext` 没有协作模式快照。
源码搜索没有对应 plan_mode/collaboration_mode 状态实现。当前工具修复对齐的是
Default 路径，不能因此声明 Plan 分支一致。

状态：缺失/待完整追踪，不是“用户明确排除”。仅添加工具拒绝 if 或名称字符串判断
不能修复：需要追踪模式配置→Thread/Turn 快照→指令增量→工具边界→CLI 切换及恢复，
再实现宿主可用路径。下一步同时核对 request_user_input 的模式与阻塞语义，避免把
此缺口缩成单工具开关。此轮只读生产代码，没有新实现、没有新测试运行。

进一步模式快照/增量/用户提问等待与回答匹配调用链见 collaboration-mode-review.md，
已区分冻结 Turn 访问器与 Step 捕获，以及 is_blocking 标记与 handler 实际等待。
