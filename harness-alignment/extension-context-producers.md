# C1/C2：实际扩展来源与窗口生命周期

## 当前实现（覆盖下方原始发现）

### 全量结果及后续测试适配

1728 已实际退出 1（4254c5）：15481 passed、38 failed、1 skipped，556.78 秒；
完整日志 full-regression-1728.txt。37 个失败为记忆集成场景中的旧刷新/召回预期，
另 1 个 SSE resume GET 截止时间场景需独立诊断。运行期间未改生产/测试。
不把 912 定向结果、旧 15504 基线或后续局部重跑组合成全量通过。

本轮新增核对 Codex app-server/src/request_processors/thread_processor.rs::
memory_reset_response_inner（1850）：仅 clear_memory_data 和
clear_memory_roots_contents，不操作运行中 Thread 的历史/reference/window。
Corki reset_memory 同样只调用 resetter，不重写对话。因而旧 reset 测试要求下个
普通 Turn 追加 memory tombstone 是之前过度 world-state 化的预期，不应恢复。

已修订 memory_read_policy 中冷禁用和摘要预算更新两例、memory_reset_runtime
的 enabled 例：同窗口保持原 fragment；显式 compact 后下一 Turn 才读取当前
来源或不再注入。保留工具禁用、完整 POLICY、预算截断、原始历史前缀、reset
清目录/清旧 git 对象/废止旧 claim 等断言，不仅删除旧失败断言。与新增生命周期
测试联合 39 passed / 6.82 秒，6538 实际退出 0（d3e840）；ruff d53956 通过。

仍有 34 个原失败 memory 召回场景待逐文件接入真实新窗口验证，以及独立 MCP
30ms 截止时间失败待定因。本轮未修改生产代码，不宣称全部失败已修复。

记忆摘要的窗口生命周期已接入生产实现，仍待本批全量回归；不关闭整个 C1/C2。
`InitialContextContributor.initial_context_keys` 显式声明窗口级 developer 来源，
与普通 world-state、input-scoped 来源分离，不按 memory key 做执行分支。
MemoryContextContributor 采用该能力；禁用时仍声明其历史 key，但不输出正文。
多个来源可以声明同一 key（例如禁用的内置 memory 加宿主来源），真正同时输出
同一 key 时仍由 PromptAssembler 拒绝；与普通 world-state 的 key 冲突也拒绝。

Runtime 三处 ContextBuilder.build 传递 defer_initial_context=True。builder 冻结
本 Step 的加载回调，不在普通构建时读取窗口来源。WindowManager.prepare 在
已有窗口时省略这些 section 的 diff（不重读、不替换、不撤销），新窗口时加载；
自动压缩摘要完成后、原子替换写入前再次加载新窗口内容。手动压缩保留既有
“下次 prepare 重建”边界。回调不重新执行用户输入技能选择，也不重建其它
world-state 来源；加载错误沿原有 prepare 失败路径传播。

ContextSnapshot 只携带临时回调与声明 key，无数据库/checkpoint 字段或协议变化。
冷恢复根据业务历史 needs_initial_context 判定，不依赖存活 Runtime 的内存缓存。
直接使用 ContextBuilder 的宿主仍默认得到完整初始快照；Runtime 使用延迟路径。
初始正文的角色、输入归属和实际 key 均在加载时校验，section warning 保留且
新窗口刷新不累加旧 warning。显式记忆操作和后台提取/合并未修改。

新增测试覆盖完整权限开/关的技能+记忆组合，以及摘要初始存在/不存在 × 后续
删除/写入 × 自动/手动压缩八场景。记录真实 contributor 调用次数：首窗口一次，
跨 Turn 和关闭 Runtime 后冷恢复都不增加；新窗口才第二次读取。验证旧业务历史
前缀、旧记忆 item 身份、刷新后的正文/删除和技能输入身份。通用自定义 key 的
延迟 async 加载、warning 与非法角色/未声明 key/input/重复输出另有单元测试。

过程证据：修复前 87753 退出 1（645f72），两个真实 Runtime 用例因删除摘要后
错误追加撤销而失败；首实现 35657 为 715 通过/2 失败（d71036），暴露禁用来源
声明 key 冲突，已改为允许声明重叠、拒绝实际输出冲突。47205 为 717 通过
（5e4037）；扩展冷恢复/压缩后 93242 为 731 通过（063e50）。新增通用测试时
36980 为 911 通过/1 失败（db40c2），模板正文末尾换行漏写在新断言中；已按
真实模板补足预期，不修改生产文本、不放宽断言。另一次指定不存在测试文件的
61112 退出 5、未收集测试（8513e4），不计通过。以上失败记录不覆盖成绿色。

全项目 ruff check、format --check（1311 文件）、compileall 和 pip check 均通过
（4fbd75）。生产已变化，旧 15504 全量不再是本实现的全量验收证据。

最终同范围 7994 已实际退出 0（d26633）：912 passed / 16.24 秒，4 workers，
覆盖 unit/context、unit/core、unit/memory，以及四个 integration 文件：新增
extension_context_lifecycle、context_role_wire、personality_compaction、
plugin_guidance_compaction_wire。最终 ruff/format 与本轮 diff --check 再核通过。
全量前复核 12 逻辑核/18 GiB（2f481a）、35%系统空闲、无其它 pytest/execnet、
五个历史 compiler 存在（632539）。采用 8 workers，不固定使用 96；全量运行
期间冻结生产和测试，待实际退出才作验收结论。

## 原始发现与前一轮证据

基准：Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc。本轮未修改生产代码。

## 源码链与新确认差异

Codex `ext/memories/src/extension.rs::contribute_thread_context` 在读取配置后调用
`build_memory_tool_developer_instructions`，返回 `memories.instructions` DeveloperPolicy。
`core/src/session/mod.rs::build_initial_context_with_world_state` 先聚合 thread/turn
扩展，再聚合 world-state；`record_context_updates_and_set_reference_context_item`
只在无 reference 时走 full initial，已有 reference 时走 world-state diff 和必要的
turn contribution。`start_new_context_window` 再次构建 full initial。
记忆扩展没有 world-state 或 turn contributor 实现，因此不能每 Step 重读摘要。

Corki `memory/context.py::MemoryContextContributor.contributions` 每次读
`memory_summary.md`，不存在/为空返回空；`context/builder.py::build` 每次调用它。
`PromptPhase.EXTENSION` 当前只进入 initial_extension_keys，改变初始排序，
没有把调用/刷新生命周期限制在新窗口。`world_state.py` 因此能将摘要消失解释为
section 删除。这是**已确认的行为不一致**，不能因提示角色和初始排序正确就关闭。

本轮初版真实 Runtime 组合诊断（33413 实际退出 0，ef59f8，21 passed）在首 Turn
后删除临时摘要文件和技能文件：第二 Turn 的新增业务历史包含 memory.instructions
“no longer apply”记录。该断言用于识别当前差异，未作为最终回归保留，以免锁定
错误目标。最终测试只删技能目录文件，不改变摘要，验证已成立的来源/排序/输入身份。

下一步修复需先追踪普通 prepare、手动/自动压缩及冷恢复对新窗口的判定，再实现
thread/initial 扩展的窗口生命周期；不能只按 memory key 特判，也不能使显式记忆
更新/遗忘工具不再执行。测试须覆盖同窗口摘要变更/删除不重新注入，新窗口重读，
冷恢复保留既有窗口，以及输入附件不重读；不得添加专用远程压缩接口。

## 已补充的组合覆盖

`tests/integration/test_extension_context_lifecycle.py` 使用真实 SkillService、
MemoryContextContributor、Runtime 和 SQLite，参数覆盖完整 permissions 开/关：

- 初始 memory developer 指导在 skills catalog 之前；完整 permissions 时 host
  skills 在 permissions 前，关闭完整 permissions 时位于 mode 后。
- 显式技能正文是 user 角色，绑定原 UserMessageItem.id。
- 删除技能文件后刷新目录；不撤销、改写或再次注入先前输入的技能正文。
- 第二 Turn 保留此前完整业务历史前缀。

最终六文件联合回归：88124 实际退出 0（7c47b0），21 passed / 3.32 秒；
范围为新增组合文件、async_contributors、context_role_wire、skill_catalog_visibility、
plugin_guidance_state、plugin_guidance_compaction_wire。4 workers/loadfile/禁重启，
启动前无其它 pytest/execnet；使用已有 12 核/18GiB机器的小范围保守并发，不启用
96 个 worker。ruff check、format --check 与本轮文档 diff --check 通过（2ee233）。
这是定向验证，不是新增测试后的全量回归；此前 15504 全量仅为生产未变的基线。

原生对应：`context/world_state/mod.rs::add_extension_section` 将 host_skills
插在存在的完整 PermissionsState 前；`ext/skills/src/world_state.rs` 区分隐藏、
空目录、预算省略和已有快照。Corki `skills/context.py` 将目录与 input-scoped
正文分别贡献；`input_context.py::bind_input_context` 保存输入归属。

通用扩展尚不整体宣称一致：原生 WorldStateSectionContribution 由 producer 提供
render_diff/legacy/retained matcher，Corki 自定义 PromptContribution 主要采用
通用替换/删除。插件指导的专属保留规则另见既有 plugin_guidance 测试；不能由
本组合测试外推任意自定义 producer、编排器或多环境支持已完成。CLI 继续暂停。
