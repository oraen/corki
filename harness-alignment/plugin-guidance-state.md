# C2 插件通用说明与动态目录：实现前差异

## 最新：真实配置重载验证

test_plugin_config_reload.py 已在原 192 个组合的实际请求上增加指导检查：
本地插件可用时提供指导；同窗口最多一份，首次提供后 ID/正文/全部字段保持；
说明不含动态目录描述，目录变化不能产生指导撤销文本。原有绑定、技能、
MCP 调用、旧权限、注册/关闭计数和审批检查保留，没有为了新断言放松旧行为。
Runtime::_admit_skill_configuration 在下一 Turn 准入以 with_plugins 发布新视图，
活动 Turn 仍使用原视图；此入口不是宿主替身。fixture 文件均在临时目录。

实际重载文件与新/旧指导、自动压缩两接口联合 201 passed（0224e1，97.29 秒），
session 62132 退出 0；静态 src/tests 1139 文件及 compileall/77 包/diff 通过
（d0a16e）。既有测试的 native/codex_apps 名称用于旧配置/名称兼容输入，
不代表本批启用了原生协议或官方 Apps。

通用说明与动态目录混合这一实现差异现已修复，并具备新旧片段、正常重载、
手动/自动压缩和冷恢复证据。压缩提交中断、任意 provider 全矩阵等由整体
C/E 恢复与隔离验收继续核对，不据本项关闭完整 C2/A–F，也不把每个可能的
交叉组合都无依据升级成新的功能缺失。下一批回到其他核心差异清单。

## 最新：自动压缩 + 两普通 HTTP 适配器 + 冷恢复

test_plugin_guidance_compaction_wire.py 新增 Responses/Chat Completions ×
pre_turn/mid_turn 四场景。真实 Runtime/HTTP 适配器只访问 fixture.invalid 的
/v1/responses 或 /v1/chat/completions。首个普通响应 usage 60000 触发本地
50000 阈值；摘要同样发普通端点、tools 为空，之后普通请求恢复单份指导。
pre_turn 在下一 Turn 前触发；mid_turn 在 advance 工具真实执行并记录
OBSERVED_ONCE 后触发。每个普通/摘要请求的 function call/result ID 集合相等。
所有普通请求指导恰好一个，Responses 为 developer、Chat 为兼容 system。
SQLite 保留两个窗口的指导正文，关闭后新 Runtime 的 resume_pending 不采样，
后续冷请求不再摘要/执行工具、不重写旧归档前缀。

首轮 ea94bd 的两个 Responses 失败是 fixture 缺失 total_tokens，补齐后
新四例及手动/旧迁移/local inputs/role wire 联合 20 passed（12fcfd，3.17 秒）。
没有为该测试修改生产代码。静态 src/tests 1139 文件、src compileall、依赖
77 包/diff 通过（b33408）。该脚本 SSE 只证明 Harness/传输，不证明模型质量。

自动与手动、两普通传输及完成后冷恢复现在有实际组合证据；不是压缩提交
中断窗口或真实插件磁盘重载与摘要同场。剩余应核对这些边界及旧目录迁移
在普通 wire 上的表现，不再将“尚无自动压缩/两接口证据”作为当前状态。

## 最新：真实手动压缩及冷恢复补验

新片段 Runtime 测试扩展为不压缩/压缩时插件不可用/压缩时插件可用三场景。
后两者实际调用 runtime.compact，模型接收一次 tools=() 的普通摘要请求；
手动压缩结果保留旧归档前缀，模型可见窗口不立即包含 ContextItem。随后
普通 Turn 按当前可用性重建指导，冷打开同 Thread 后恢复插件，当前请求中
指导恰好一份；归档保留两个窗口的两份正文，不把归档重复误当请求重复。
无 compact 时仍保留原单窗口一次指导的基准。

初测 025c7a 一失败源于错误地要求手动压缩立即注入指导。重读原生
core/src/compact.rs::run_compact_task 的 InitialContextInjection::DoNotInject
及 Corki window.py::compact 后修正断言，并补普通下一 Turn。未因此改变生产。
与 local_compaction_inputs/world_state_journal 联合 24 passed（b19b77，2.15 秒）；
静态 src/tests 1138 文件、src compileall、77 包依赖/diff 通过（f561c7）。

本批仅手动压缩的实际普通 ModelRequest，不声称执行了专用远程压缩或真实
模型摘要质量验证。自动 pre/mid-turn 压缩与两种 wire、磁盘重载的同场组合
仍待补验；下方“实际压缩待补”的旧记录现仅剩这些未覆盖分支。

## 最新旧混合片段迁移修复

新增真实 Runtime + SQLite 旧片段 fixture：有效旧混合指导/已被旧通用 tombstone
撤销两种状态，连续两 Turn 校验指导是否补入、当前目录及旧记录前缀。
有效旧指导重复注入先红（a7bb06），修复后本文件 3 passed（e628d4）。
is_retained 现识别 developer 角色、catalog key 中旧插件指导标记及用法；
倒序遇旧整片撤销后不复用更早的指导，已撤销者需重新提供。新目录更新
采用独立 notice，只撤销旧能力/warnings，明确通用用法不变。
不改旧已持久化的文本，不重新启用 Apps 或官方协议。

插件单元/上下文日志/提示词/新迁移联合 259 passed（74427a，3.49 秒），
静态 src/tests 1138 文件、src compileall、依赖和 diff 检查通过（e4c506）。
本 fixture 注入兼容旧行，不代表运行旧版本二进制；连续请求均使用当前真实
Runtime。实际普通压缩后的重新注入、两接口 wire 及物理磁盘重载交叉仍待补验。
下方“只识别新 key/type”是上一实施阶段的历史状态，已由本批修复覆盖。

## 最新实施：新片段链路已接入，旧混合历史仍待处理

PluginContextContributor 已分离 extensions.plugins.guidance 与原 catalog key。
固定说明使用独立模板/type，目录只含动态列表；仅本地启用且无错误插件触发
指导，不加入 Apps 或模型/provider 能力特判。显式输入片段保持不变。
world_state 在实际窗口内检查是否保留指导正文；不因最新比较状态而重复添加。
指导移除只写 silent snapshot，不向模型发撤销说明；目录仍走原撤销/替换。
新 context/plugin_guidance.py 的扫描在最近 CompactionItem 处停止，不读被替换窗口。

真实 Runtime 新测试修复前失败（d12783，缺独立类型），修复后通过（071cb7）。
覆盖目录首次/变化/移除及冷启动恢复：指导一次、动态目录正确更新/撤销、
无空 ContextItem 请求、旧历史前缀不变、不重复恢复采样。
该 fixture 调用真实 PluginContextContributor，由宿主提供动态清单；不是该
新用例实际触发磁盘重载。磁盘重载由联合组已有 test_plugin_config_reload 覆盖。
插件单元/重载/journal/新用例联合 421 passed（e029ba，102.32 秒），提示词/
更新日志/普通角色 wire 另 30 passed（add786，2.86 秒），进程均退出 0。
静态 src/tests 1138 文件、src compileall、77 包检查及 diff 通过（1f3415）。

尚未关闭：旧混合 catalog 的指导识别及目录撤销时的迁移语义；真正普通压缩
请求之后指导重新注入；新独立片段的两种普通 wire 与磁盘重载组合。
当前 is_retained 只识别新 key/type，不声称旧会话兼容已完成。下一批应先补
这两类反例再修复，不恢复旧混合模板，也不删旧历史。原实施前审计如下。

状态：部分一致；通用说明与动态目录的状态边界不一致，尚未修复。
范围仅本地插件及普通 MCP/skills；不引入 Apps、官方服务、模型/provider 特判。

## 原生调用链

固定参考 commit ddf04ad26789d040f9ef6a96736f76602e35a6cc：

- core/src/session/world_state.rs 构造 PluginsInstructionsState，原生以
  mcp.plugins_available 与 model include_plugin_usage_instructions 决定可用性。
  后者是参考条件，不复制成 Corki 的服务商专用能力分支。
- core/src/context/available_plugins_instructions.rs 固定 developer 角色、
  plugins.usage_instructions 类型，正文是通用用法，不列动态目录。
- core/src/context/world_state/plugins_instructions.rs 全部 render_diff：
  unavailable 不输出；previous 已为 true 或 Unknown 不重复输出；Absent/false
  到 available 才输出。retained matcher 识别仍在历史中的通用说明。
- 同目录 plugins_instructions_tests.rs 验证 legacy 不重复，以及有持久快照
  但正文未被保留时重新注入。不能只用当前 snapshot 的 bool 判断正文存在。

## Corki 当前调用链及影响

Runtime 构造 PluginContextContributor；配置重载以 with_plugins 替换其视图。
plugins/context.py::contributions 将固定说明和插件身份、版本、工具/MCP/skills
列表、warnings 合为 extensions.plugins.catalog 一个 WORLD_STATE 片段。
prompts/extensions/plugins/catalog.md 包含固定用法及动态变量。
ContextBuilder → window.prepare → world_state.changed_context_items 对该 key
走通用撤销/替换逻辑。因此目录或 warning 改变也会重发通用说明；空目录会
撤销整片段，不能表现原生独立的通用说明状态。

实际影响：多余重复上下文，通用指导与当前能力可用性混淆。动态本地目录本身
仍有用途，不能简单套用原生静态 bool 从而保留已经下线的工具或隐藏新能力。
显式选中插件片段 input_scoped=True 属于输入上下文，不随本修复改成动态撤销。

## 修复及验收要求

1. 分离固定通用说明与动态本地目录；保留目录撤销、warning 和能力变化，
   通用说明用独立 key/type/state。保留 Corki 身份和本地 Python 工具说明。
2. 以本地已加载插件状态判定，不引入官方 model metadata 或 Apps。
3. 通用说明不可用时仅记录比较状态，不撤销已保留的通用正文；后续恢复按
   实际历史判断是否需重新提供。复用已有 snapshot-only 持久字段，不加空消息。
4. 旧混合 catalog 会话识别已保留通用说明，不能改写旧记录或重复添加；目录
   更新仍必须完整生效。压缩丢弃正文后可以重新注入，不盲信旧快照。
5. 先红后绿覆盖实际 Runtime 插件重载/目录变化/禁用/恢复、普通请求与冷启动；
   检查指导次数、动态能力撤销、旧历史前缀、空消息缺席。另验压缩与旧混合历史。

本批仅完成源码差异确认；现有 plugin reload 测试主要检查目录/技能/工具绑定，
其通过不能证明独立通用说明状态已实现。C2 及完整 A–F 不据此关闭。

实施前基线：plugin_config_reload、selected_context、world_state_journal 联合
211 passed（3beb71，98.85 秒），session 41372 已退出 0。未修改生产/测试；
该结果只保护当前目录/绑定行为，不作为上述待修复状态语义的通过证据。
