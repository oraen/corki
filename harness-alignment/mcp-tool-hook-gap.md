# B10/A4：MCP 工具型 Hook 的真实执行缺口

`Interrupt` MCP 结果提交边界补证：新增
`test_interrupt_mcp_recovery.py` 两例，以真实 Runtime/MCPManager/SQLite
在远端调用完成后分别于完成记录提交前、提交后注入故障，同时令取消终态
首次写入失败。冷 Runtime 恢复后均保持原始取消，不重新采样或重发已产生
的一次 MCP 副作用；unknown 仍是 unknown，已提交结果保持原账本。
首次扩大并行 6 文件回归 **93 passed、1 failed**：失败是已有异步 command
`Interrupt` 用例在 1 秒期限内等待测试释放，现场 SQLite 记录
`Interrupt failed: `（超时），串行单例通过。将该测试异步分支改用原生
允许的 3 秒上限，不弱化副作用/事件断言；修订后相同 6 文件
**94 passed / 44.94s**，4 workers/loadfile/禁重启，实际退出 0。
本批没有生产改动，也不把模拟提交故障等同于真实进程强杀。

`Interrupt` 真实 HTTP 故障补证：`test_interrupt_hooks.py` 用实际
`HttpMCPClient` 与 MockTransport 半截 JSON 响应触发 1 秒 Hook 超时；
响应体在唯一 `TurnCancelled` 前关闭，Hook 状态是 failed 而非伪成功，
取消不会升级为 `TurnFailed`，同线程冷 Runtime 不重发远端调用。
与普通 `Interrupt`、MCP Pre/PostToolUse、MCP 冷恢复、压缩 MCP HTTP
故障四文件联合 **74 passed / 13.57s**，4 workers/loadfile/禁重启，退出 0。
本批仅新增离线故障测试，没有改生产代码；不证明真实外网或 OS 强杀后的
运行中调用，也不覆盖远端已执行但完成事实未落盘的 crash 窗口。

2026-09-22 `Interrupt` 事件补证：Codex
`core/src/tasks/mod.rs` → `hook_runtime.rs::run_turn_interrupt_hooks` →
`hooks/src/events/interrupt.rs::run` → dispatcher 调用；
`hooks/src/engine/discovery.rs::append_matcher_groups` 接受 MCP 工具型
Interrupt Hook，并把默认/超长 timeout 限到 1/3 秒；Corki
`stop_hooks.command_identity` 与 `interrupt_hooks.run` 已有对应准入和执行。
新增真实 Runtime/MCPManager/SQLite、离线 client 的 root 中断与 replaced
两例：仅显式 root 中断远端调用一次，模板传入当前 Turn 身份、诊断在
TurnCancelled 前发出，后续重复取消及同线程冷恢复不重放；replaced 零调用。
`test_interrupt_hooks.py` 全文件 **14 passed**、实际退出 0；本批仅补测试，
未改生产。此前非 CLI 全量 15699 不包含这两例。此证据不覆盖 MCP
Interrupt 的半截 HTTP、执行提交前崩溃或其它事件来源；不因此核销整个 B10。

最新 HTTP 故障补证：实际 `HttpMCPClient`、半截响应体、Hook 截止时间与
活动 Turn 取消已覆盖 Pre/PostCompact × 自动/手动八组合，并在冷 Runtime
验证不重放；相关六文件 219 通过。见 `compact-hook-gap.md` 顶部。
因此下方“压缩 MCP HTTP 失败组合仍缺”也是历史记录；真实联网和
OS 强杀不可从 MockTransport 推出。

最新补证：Pre/PostCompact 的 MCP 工具型 Hook 已经加入真实 Runtime/SQLite
安装、unknown/completed 执行账本及冷恢复矩阵；新增 54 组合、相关联合
176 通过，具体范围和离线客户端限制见 `compact-hook-gap.md` 顶部。
因此下方“Pre/PostCompact MCP 自身冷恢复未验”属于旧状态；真实 HTTP
超时/取消与跨进程强杀仍需按各自入口单独验证。

当前状态：基础同步执行已接入，原始反例转绿；完整MCP故障/冷恢复验收仍开放。
不是官方服务排除项。CLI继续暂停；下方缺口记录保留修复前证据。

## 当前实现与验证

Stop/SubagentStop MCP冷恢复补验：复用真实Stop checkpoint故障夹具，新增两种
事件 × unknown/completed/feedback三窗口 × 原配置key/迁移key，共12场景通过
（915535）。unknown只执行首次active=false并失败终态，不重新采样或重发；
completed/feedback恢复已保存block反馈，第二次模型采样后仅发新一轮active=true，
不是重放第一次调用。已保存request保持原样、用户输入唯一、反馈唯一，再次resume
无事件。联合Stop恢复、MCP Pre/Post恢复及MCP Hook **123 passed / 31.70s**
（19d6e2）。仅新增离线MCP替身测试，没有生产修改；不是OS强杀证据。

Pre/Post冷恢复补验：新增test_mcp_hook_recovery.py，Pre/Post × 普通/嵌套 ×
调用完成落盘前/后 × 保留/移除Hook及MCP配置，共16场景通过（0b0dff）。真实
Graph在complete_hook_execution提交边界取消，保留RUNNING checkpoint；关闭
原Runtime后，新Runtime经公开resume_pending恢复同Turn。远端替身只调用一次，
账本mcp目标保留且记录不变；仅completed恢复一份SAVED_MCP上下文，unknown不
伪造反馈。Pre普通工具未执行，Post既有副作用一次；未知外层脚本不伪装成功。
同一冷Runtime再次resume无事件且历史不变。联合原Post冷恢复与MCP Hook文件
**159 passed / 43.60s**（f1a67b），Ruff/format/diff通过（b3044c）。初轮修正
客户端替身settings及Post账本前缀夹具，未修改生产代码。
此为离线MCP替身、真实SQLite/checkpoint的新Runtime恢复，不是OS强杀或HTTP
跨进程验证；Stop/SubagentStop的MCP自身冷恢复尚未由本组覆盖。

真实HTTP补验：Pre/Post × 超时/取消四场景经实际HttpMCPClient与离线
httpx.MockTransport进入半截JSON响应体等待。Hook配置1秒覆盖客户端默认期限；
超时形成failed Hook但非block，普通工具一次执行且TurnCompleted；取消向stream
调用方传播CancelledError并只发布一个TurnCancelled，Pre取消无普通工具副作用，
Post取消保留此前一次副作用。各路径tools/call只发送一次，响应体在Turn终态前
关闭，Runtime关闭后客户端已关闭。联合HTTP清理与模板 **53 passed / 14.62s**
（1ec5fa），Ruff/format/diff通过（e7cec8）。测试初轮修正了指纹API夹具及
stream取消预期，没有修改生产逻辑；不把夹具失败称为生产反例。
此证据是实际客户端/模拟传输，不是真实远端联网，也不覆盖MCP Hook冷恢复。

后续Stop路径：新真实Runtime测试分别使用root、thread-spawn reviewer、内部
memory_consolidation来源。用户可信MCP Stop/SubagentStop第一次返回block，
第二次收到stop_hook_active=true并允许完成；下一次采样收到唯一反馈，事件顺序
blocked→completed。内部记忆worker不调用该用户Hook，只有一次模型采样。
新增3例与本文件Pre/Post共31通过（6b1dab）；与原Stop、异步Stop、模板联合
**77 passed / 16.24s**（10b7fb），Ruff通过（eee01d）。无生产修改。

来源限制必须准确：原生core/hook_runtime.rs::run_turn_stop_hooks对memory
consolidation使用专门target；hooks/events/stop.rs::select_handlers跳过user/
project/session flags/plugin，但保留system/托管/ExecutorScoped来源。当前测试
只证明用户Hook隔离，不能泛称原生内部worker完全不运行Hook。这些其它来源
对应的Corki加载/执行支持仍需按A4来源审计，不由本次三个测试关闭。

后续信任/就绪补验：test_mcp_tool_hooks的Pre/Post×direct/nested扩为7种策略，
共28场景。未信任hash、enabled=false撤销、目标修改但保留旧hash均不调用MCP；
模型采样期间禁用review或移除服务时，Hook使用最新目录拒绝旧授权，不发远端调用。
强制替换为一直等待的连接时，5秒watchdog内完成Turn，Hook报告failed且无review
调用，Runtime关闭后启动任务已退出。Hook失败告警与明确block不同：前者不自动
阻断普通工具，后者Pre在副作用前阻断；不是将基础设施故障冒称策略批准。

原生对应runtime.rs::latest_call_tool读取latest_connections，再传wait_for_server
参数；Hook executor固定false。配置拒绝由discover中的可信hash/enable检查负责。
本批为离线client和真实Runtime/manager验证，不冒充HTTP超时或跨进程冷恢复。
联合模板、MCP reconciliation与resource binding **69 passed / 9.54s**（c37cda）。
首轮联合收集暴露新增unit与integration文件同名冲突（991532），现将unit文件
改名test_mcp_hook_templates.py，未删除测试，也未改变全局pytest导入模式。
修改集成文件Ruff/format/diff通过（4d76f6）；本批未修改生产代码。

core/mcp_tool_hooks.py实现规范化、递归模板、MCP结果转Hook输出。StopCommand
旧字段不变，新增MCPHook独立目标和restore_command新旧快照恢复；账本请求使用
mcp字段绑定目标，不以空shell命令作为身份。graph/runtime把MCPManager传入
Pre/Post/Stop/SubagentStop同步分支，不进入异步command队列或递归模型工具路由。

MCPManager.call_hook非阻塞发布待更新目录，检查就绪连接、过滤和工具名，借用
连接生命周期；请求携带threadId和Hook超时，真实client复用active-time与传输
恢复通道。文本结果拼接、isError转Hook失败；1MiB文本上限是Corki自身防护。
不吞取消。未就绪和刷新等完整行为仍待专项证明，不能仅凭结构关闭验收。

初次接入原Pre两反例转绿，与原Pre/Post联合74通过（453765）。现新增Post普通/
嵌套验证：Pre副作用前阻断，Post一次副作用后反馈，结果进入下一模型请求。
与Stop、Pre/Post冷恢复、异步Pre/Post/Stop七文件联合 **322 passed / 84.78s，
无跳过**（b81950）。旧冷恢复文件主要证明command兼容，不冒充MCP自己的冷恢复。
模板/身份另10通过（41ba77）：保留JSON类型、嵌入串、递归不二次展开、缺路径、
不改源数据、目标/input/timeout/matcher指纹、非法配置拒绝。Ruff/diff通过
（4e33ae）。本批已包含生产修改，早前全量不代表当前版本。

下一步：原生其它Hook事件及来源按A4审计，优先Pre/PostCompact实际缺口。
上述已验的信任/就绪/Stop/HTTP故障不再列为完全未知；B10尚未完成。

## 固定参考实际调用链

Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc：

1. hooks/src/engine/discovery.rs 接受 McpTool handler，校验非空 server/tool，
   归一化 timeout，与 matcher/event 一起计算 trusted hash。SessionEnd 明确不支持
   MCP Hook，prompt/agent 类型仍跳过，不能据此要求实现原生也没有的行为。
2. engine/mcp_runner.rs 在事件 JSON 上递归展开 input 模板：完整占位符保留 JSON
   类型，嵌入字符串时序列化，缺路径失败，不把未展开值发给服务。
3. core/src/hook_mcp_executor.rs 通过 McpRuntime.latest_call_tool 调用已有连接，
   wait_for_server=false；不是经过模型工具搜索，不自动等待未就绪服务器。
   传入超时和宿主线程元数据，拼接文本内容；MCP is_error 变为 Hook 执行失败。
4. mcp_runner 把成功文本映射 stdout/exit_code=0，复用对应事件的 Hook 输出解析。
   普通/Code Mode 嵌套工具均须经过其已有 Pre/Post 控制边界。

## Corki 现状与影响

plugins/agent_hooks.py 可以验证 type=mcp_tool 的元数据，但实际
core/stop_hooks.py::command_identity 只接受 command；discover 捕获 ValueError
后告警并跳过。Pre/Post/Stop 的 runner 只有 shell run_command，没有 MCP runner。
因此已信任的 MCP 策略 Hook 不会运行，原工具可能执行，而不是收到配置中的阻断。
这不是“调用普通 MCP 工具时能触发 command Hook”的已有能力。

新 tests/integration/test_mcp_tool_hooks.py 使用真实 Runtime、真实 MCPManager
及离线 client：预先确认 policy 已就绪，按原生 NormalizedHookIdentity 和
config/src/fingerprint.rs 的 canonical JSON SHA256 设置可信状态；模板传入
tool_input.value，服务应返回 block。分别走普通 probe 与 Code Mode 嵌套 probe。
两个反例均失败：review 调用为零，probe 已产生一次夹具副作用（930f3e，0.78s）。
没有 xfail/跳过。Ruff/format 通过（622d34）；当前测试集合不能称全绿。

## 后续实现与验收边界

- 独立表示 MCP Hook kind/server/tool/input，不把远端调用伪装成 shell command。
  扩展可信 hash、快照、持久执行请求身份，保留既有 command 账本兼容与撤销语义。
- 在现有 MCPManager 上提供宿主 Hook 调用：绑定当前就绪连接，保留资源所有权、
  服务策略与超时，不通过模型曝光/递归 Hook 路由，也不引入专有模型协议。
- 将同步 MCP runner 接入已支持的 Pre/Post/Stop/SubagentStop 事件；复用事件输出
  契约、账本 claim/完成/未知不重做、取消与清理，不对 MCP 添加 command async 字段。
- 测试至少覆盖 direct/nested block/rewrite/context、未信任/修改/撤销、服务未就绪、
  模板类型与缺字段、MCP error/超时/取消、完成及未知结果冷恢复、旧 command 回归。
- 其它原生 Hook 事件的差异继续按 A4 主循环审计，不能以本次四类接入缩减目标。

以上为最初实现方案，基础接入现已完成；当前剩余边界以上方最新验证节为准。
现有 B8/B9 普通调用契约验收不代表全部 Hook 调用链已完成。
