# A4/C8：压缩生命周期 Hook 缺口（未完成）

## 2026-09-22：冷恢复计划重复命令在副作用前拒绝

Codex `compact.rs::run_compact_task_inner`、`hook_runtime.rs::run_post_compact_hooks`
在当前 Turn 构造并运行 Hook 请求；Corki 为冷恢复另存命令计划。
真实 Runtime/SQLite 的 PostCompact 安装故障恢复中，将已保存计划的同一命令
重复一次，手动/自动 × command/MCP 四例修复前均先产生一次 Hook 副作用，
再因第二次 claim 的 unknown 账本失败。现于恢复执行前检查命令 key 唯一，
拒绝重复计划；四例先红后绿，均零新增 Hook 调用、TurnFailed、摘要与历史
前缀保留，二次恢复不重放。相关七文件 **224 passed / 26.62s**，
4 workers/loadfile/禁重启，实际退出 0。随后非 CLI 完整范围按短期限敏感
六文件与其余文件分组重跑，两批分别 **459 passed / 55.58s**、
**15307 passed、7 skipped / 736.34s**，均退出 0；独立收集
**15773 项**等于两批总数，合计 **15766 passed、7 skipped**。
七项跳过仍为六项缺失历史编译器和一项文件系统不接受非 UTF-8 文件名。
不据此证明其它任意计划损坏或外部副作用 exactly-once。

## 2026-09-22：已落盘计划 payload 的值身份及额外字段拒绝

Codex `compact.rs::run_compact_task_inner` 在同一 Turn/Step 捕获压缩来源与
Hook 生命周期；Corki 为冷恢复额外持久化 Pre/Post 计划，因此必须在重启后
重新绑定当时的身份，不能只检查字段类型。真实 Runtime/SQLite 的 PostCompact
安装后故障窗口中，对保存计划读取结果注入类型合法但值错配的 `model`、`cwd`、
`trigger`、`transcript_path`，或注入根会话不应有的 `agent_id/agent_type`、
额外 `scope` 字段；手动/自动 × command/MCP 共 24 例。修复前全部会继续执行
Hook（先 12 例、再 8 例、最后 4 例红），将错误来源传给本地命令或 MCP；
修复后均在 claim/远端调用前 `TurnFailed`，零新增 Hook 副作用，原摘要
及历史前缀保留，二次恢复不重复。

`compact_hooks.run` 现将保存 payload 的完整键集合、原 Turn 的模型与
触发来源、工作目录、已持久 session/thread 来源对应的 agent 身份，以及
从该 Thread 规范历史重新物化的 transcript 路径逐项核对；原有旧 v1
`session_id==thread_id` 兼容仍仅在此单字段允许，不放宽其余身份。
关联计划安装、保存边界、普通压缩、HTTP MCP、真实 shell 与异步 Hook
七文件 **220 passed / 23.92s**，4 workers/loadfile/禁重启，实际退出 0；
Ruff/format/diff 通过。随后非 CLI 完整范围按短期限敏感六文件与其余文件
分组重新运行，两批分别 **459 passed / 57.67s**、**15303 passed、
7 skipped / 734.48s**，均退出 0；独立收集 **15769 项**等于两批总数，
合计 **15762 passed、7 skipped**。七项跳过仍为六项缺失历史编译器和
一项文件系统不接受非 UTF-8 文件名。未据此宣称任意损坏命令数组、
执行账本迁移或远端副作用 exactly-once 均已验证。

## 2026-09-22：双同步 shell Hook 的进程清理与账本失败

新增真实 Runtime/SQLite、真实 POSIX 子进程的四个组合：Pre/PostCompact ×
Runtime 关闭/首个 Hook 执行账本提交失败。两条命令同时启动并等待各自释放；
关闭路径要求两个进程在 `aclose` 返回前退出，提交失败路径要求首个进程
释放后取消、join 另一个进程，且在 `TurnFailed` 发布前两个 PID 均不存活。
两种路径的执行结果账本均保持 unknown，不发成功 `HookCompleted`；Post
摘要已安装、Pre 未开始摘要。冷 Runtime 的 `resume_pending` 不重放进程。
新文件 **4 passed / 0.87s**；与普通压缩 Hook、MCP HTTP 半截故障、计划
安装/保存边界及 MCP 工具 Hook 六文件联合 **215 passed / 20.13s**，
4 workers/loadfile/禁重启，实际退出 0。只新增测试，不改生产；此证据仅
适用于受管 POSIX 进程，不保证强杀或远端副作用 exactly-once。新增四例及
上节八例后来已进入完整非 CLI 回归：两批合计 15733 passed、7 skipped，
详见 `context-budget-review.md` 顶部；单独故障窗口证据仍以上述六文件为准。

## 2026-09-22：并发 MCP 压缩 Hook 的响应清理所有权

将真实 `HttpMCPClient`／离线半截 JSON 响应体测试从单 Hook 扩展为
单个/两个同步 MCP Hook × Pre/PostCompact × 自动/手动 × 超时/取消。
两个 Hook 在同一事件中并发发起普通 `tools/call`，各自响应体停在半截；
取消路径要求两份 body 都关闭后才发布 Turn 终态，超时路径要求两个
`HookCompleted` 均为 failed，任一分支都不把半截输出当成功。冷 Runtime
同 Thread 的公开恢复不重发远端调用，也不重做自动路径的大工具副作用。
单文件 **16 passed / 11.37s**；与普通压缩 Hook、计划冷热恢复、计划
保存边界及 MCP 工具 Hook 五文件联合 **211 passed / 20.18s**，
4 workers/loadfile/禁重启、实际退出 0，Ruff/format/diff 通过。
只有测试/文档变更，不将 MockTransport 冒充真实外网或强杀后远端原子性。
本批新增 8 例晚于下方 15717 非 CLI 全范围基线；后续完整回归已刷新为
15733 passed、7 skipped，详见 `context-budget-review.md` 顶部。

联合批次命令：

```text
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring CORKI_TEST_SANDBOX_COMPILER=/Users/corki/PycharmProjects/corki/src/corki/_native/sandbox/corki-sandbox .venv/bin/pytest tests/integration/test_compact_mcp_http_failure.py tests/integration/test_compact_hooks.py tests/integration/test_compact_hook_install_recovery.py tests/integration/test_compact_plan_save_boundary.py tests/integration/test_mcp_tool_hooks.py -n 4 --dist=loadfile --max-worker-restart=0 -q -o addopts='' --tb=short
```

## 2026-09-22：计划保存写前／写后失败不激活孤立 Hook

Codex `core/src/compact.rs::run_compact_task_inner` 只在实际摘要成功后
进入 PostCompact；Corki 为冷恢复额外持久化 Pre/Post 执行计划，计划
保存失败不能单独产生可执行义务。新增真实 Runtime/SQLite 测试覆盖
Pre/Post × 自动/手动 × `save_hook_batch` 写前/写后报错八例：原 Turn
失败，Pre 不生成摘要、Post 摘要已生成但均未安装压缩 marker，Hook
零执行；写后允许留下孤立计划，冷 Runtime 的 `resume_pending` 和下个
普通 Turn 不执行它，原历史前缀及自动工具副作用不重放。冷对照使用较大
窗口，避免旧大结果在新 Turn 合法触发另一次自动压缩，测试精确针对旧计划。

新文件 **8 passed / 2.05s**。与计划安装恢复、SessionStart 来源安装、
普通压缩 Hook、真实 HTTP MCP 半截故障五文件联合 **176 passed /
19.71s**，4 workers/loadfile/禁重启，实际退出 0；Ruff/format/diff
通过。本轮只增测试，不改生产。首次扩大命令误写不存在的 MCP 恢复文件，
pytest 零收集退出 5，修正为 `test_compact_mcp_http_failure.py` 后运行上述
完整目标范围。不能以此证明任意损坏计划、进程强杀或外部副作用 exactly-once。

联合批次命令（在项目根目录）：

```text
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring CORKI_TEST_SANDBOX_COMPILER=/Users/corki/PycharmProjects/corki/src/corki/_native/sandbox/corki-sandbox .venv/bin/pytest tests/integration/test_compact_plan_save_boundary.py tests/integration/test_compact_hook_install_recovery.py tests/integration/test_compact_start_install.py tests/integration/test_compact_hooks.py tests/integration/test_compact_mcp_http_failure.py -n 4 --dist=loadfile --max-worker-restart=0 -q -o addopts='' --tb=short
```

包含新增八例的后续非 CLI 完整范围已按短期限敏感组/其余组实际退出 0，
合计 **15717 passed、7 skipped**；独立收集 15724 项无分组遗漏，
跳过原因仍为历史编译器缺失及文件系统限制。完整两批命令及资源条件
见 `patch-worker-cancellation.md`，不以全量通过代替其余 C8 窗口验收。

## 2026-09-22：实际 HTTP MCP 压缩 Hook 的超时/取消与冷恢复

新增 `test_compact_mcp_http_failure.py`，用真实 `HttpMCPClient` 和离线
`httpx.MockTransport` 将 `tools/call` 的 JSON 响应体读至半截后永久等待。
Pre/PostCompact × 自动/手动 × 1 秒 Hook 超时/活动 Turn 取消八组合均通过。
每例断言 MCP 业务调用恰好一次、响应体在 Turn 终态前关闭、超时为失败 Hook
但不阻断压缩、取消传播且不伪装完成；Pre 取消不安装摘要，Post 取消保留
已安装摘要，自动压缩的大工具副作用仅一次。关闭 warm 后以同库同 Thread
重建 cold Runtime，`resume_pending` 为空、原历史逐项不变，调用/副作用不重放。
联合压缩、MCP 工具 Hook、HTTP 清理和上批冷恢复六文件
**219 passed / 19.65s**，4 workers/loadfile/禁重启，实际退出 0。
本批只新增离线故障注入测试，未修改生产；不冒充真实互联网故障或
操作系统强杀后的远端 exactly-once 保证。

## 2026-09-22：通用 MCP Pre/PostCompact 冷恢复补证

将既有真实 Runtime/SQLite 的压缩 Hook 安装与执行故障窗口扩为
`type=mcp_tool`，使用已信任的普通 MCP server/tool 身份、MCPManager 与离线
客户端，而不是把 MCP Hook 伪装成 shell command。新增 54 组合覆盖 Pre/Post ×
自动/手动 × 安装、unknown/completed 账本、回执及配置变更/损坏；记录远端
`tools/call` 的 session 身份和次数。unknown 不重发结果未知的调用，completed
复用已落盘输出，配置移除时不追溯授权；摘要/工具副作用及原始历史仍按原
测试断言保持一次。MCP 新组合单独 54 passed，和命令、异步压缩及 MCP
Pre/PostToolUse 恢复联合 **176 passed / 19.66s**，4 workers/loadfile/
禁重启，实际退出 0。无生产修改。

该证据使用真实主循环、管理器和持久账本，但 MCP 客户端是离线替身；不能
推出真实 HTTP 响应体超时、跨进程强杀或任意远端副作用 exactly-once。
下方“自身冷恢复尚缺”是此前状态，不再作为该窗口的当前结论。其余
压缩 Hook/可选来源仍按具体故障入口判断，不能用笼统“全部未验”替代。

自动异步实际进程补验：test_async_compact_hooks现覆盖自动/手动 × Pre/Post ×
后台完成/运行中关闭八场景（5d7d00）。自动路径为真实Runtime.stream→大工具结果
16000字符→普通摘要→安装→后续采样；脚本等待release时Turn已完成且PID仍活。
后台continue=false不改变终态、warning后续一次，关闭回收PID；冷开不重启旧脚本，
摘要一次、工具效果一次，账本trigger=auto/manual准确。Codex参考compact.rs
parse_handler_output的can_apply_control_effects边界；不使用远程压缩协议。
联合原异步Stop/压缩Hook/安装恢复/解析 **121 passed / 16.47s**（c738cf），
Ruff/format/diff通过（71949d）。本批只补测试；不是RUNNING checkpoint中途
冷恢复或OS强杀验证，不将自动异步全部交错关闭。后续优先其它未验生命周期与
MCP故障/恢复，不继续重复自动异步正常执行/关闭矩阵。

payload类型审计：Codex hooks/events/compact.rs的请求为类型化字段，schema.rs的
Pre/PostCompactCommandInput要求字符串model/cwd/trigger、nullable transcript，
子代理字段由可选Subagent上下文成对提供。Corki恢复JSON未约束这些字段，新增
自动/手动×五字段损坏十例全部错误执行runner（54e2b4，10 failed）。拟在执行前
校验形状，保持历史model/cwd原值，不与当前Step配置强行相等，避免破坏合法恢复。
现已实现：恢复payload必须为对象，必需字符串字段/trigger标签/nullable transcript/
可选agent成对字符串先校验，失败在claim和runner前终止。六文件联合
**158 passed / 23.47s**（7c449f），含上述十反例、旧session兼容、动态Step/
previous-model、异步真实进程及输出解析；Ruff/format/diff通过（1ad8d5）。
不重写历史字段，不重采样已安装摘要。该测试通过repository读取拦截注入字段损坏，
不能证明任意合法字符串篡改可检测；command schema与MCP故障/冷恢复仍开放。

会话归属审计：参考hook_runtime.rs::run_pre_compact_hooks/run_post_compact_hooks，
session_id来自所属Session。Corki恢复计划目前只检查event/turn，未检查session。
自动/手动安装后冷恢复将payload.session_id改为foreign-session，两例均错误调用
runner（984aa1：2 failed、旧thread_id兼容对照2 passed）。修复要求：执行前校验
持久会话归属；仅兼容旧实现写入当前thread_id，不改写旧请求、保持账本去重身份。
此为Corki持久恢复边界，不是Codex官方服务鉴权功能。
修复已接入compact_hooks.run恢复分支：在恢复命令/claim前检查已保存session，
异会话明确TurnFailed，摘要已安装且不重采样、工具不重跑、Hook零调用；旧thread_id
计划保持原payload执行。与正常Hook、异步真实进程、动态Step/previous-model及
输出解析联合 **148 passed / 20.34s**（9bffc4）；Ruff/format/diff通过（cd31bb）。
测试通过拦截repository读取注入损坏，实际Graph/SQLite冷恢复；不称全部数据库
损坏已覆盖。其余payload字段、command schema与MCP自身故障组合仍需收敛。

异步实际进程补验：新增test_async_compact_hooks.py，Pre/Post × 正常后台完成/
运行中关闭四场景（866042）。真实Python脚本记录PID并等待release；手动压缩
在脚本未结束时已TurnCompleted、marker已安装。后台continue=false不控制终态，
systemMessage在下一普通Turn只出现一次Warning，无同步HookStarted/Completed。
关闭分支不释放脚本，Runtime负责终止并join，os.kill(pid,0)确认PID消失；账本
unknown保留。正常完成保留原始结果。两分支冷Runtime保留配置，resume无待续
Turn，下一普通Turn不重启旧脚本。与原async Stop、压缩Hook、安装/执行恢复及
解析联合 **103 passed / 12.74s**（227633），Ruff/format/diff通过（215a5b）。
本批仅补测试，无生产改动；覆盖手动压缩，不泛称自动/实时输入/并发限额/全部
冷checkpoint异步交错已验。MCP传输故障与剩余持久字段校验仍开放。

Pre账本恢复及HTTP MCP补验：原Post故障夹具新增Pre × unknown/completed ×
保留/移除配置 × 自动/手动八场景。Pre故障时尚无摘要/marker；unknown冷恢复在
摘要前失败且不重复Hook，completed复用结果后摘要仅一次。账本保持原样，自动
工具副作用一次；连原Post共24通过（23a5bf）。本组为受控runner/真实Graph与SQLite。

压缩Hook边界测试新增实际HttpMCPClient+MockTransport，自动/手动 × Pre/Post ×
停止/允许/非法输出/未信任共16场景。只向配置fixture.invalid发送普通MCP请求，
tools/call携带当前threadId，模板字段展开正确；未信任零调用，其余一次，Pre
停止无摘要、Post停止保留安装，非法block不变成停止。与原命令/身份/并发及冷恢复
联合 **68 passed / 9.23s**（357e71），Ruff/format/diff通过（cf0d37）。仅改测试。
这不是实际远端联网；MCP自身冷恢复/HTTP失败组合不能由command恢复或正常HTTP
替代，异步与剩余持久字段损坏也仍待验收。

Post执行账本补验及版本修复：安装冷恢复文件扩展自动/手动 × 调用完成落盘前/后 ×
保留/移除配置。真实Graph异常后保持RUNNING，关闭原Runtime再resume_pending；
unknown明确失败且Hook不重放，completed复用结果（即使配置已移除），摘要一次、
自动工具一次、原账本记录不变，再次resume无事件。原6安装场景与新增8执行场景
共14通过（7d739e）。受控runner/真实SQLite，不是实际进程或远端跨系统exactly-once。

另发现版本类型漏洞：Post计划version=true被Python视作1并继续执行，自动/手动
两反例都出现不应发生的新调用（aa39b4）。现snapshot与receipt版本都要求真正int
且等于1；布尔版本在执行前拒绝。联合四文件 **90 passed / 9.21s**（eaa185），
Ruff/format/diff通过。此负例直接覆盖snapshot，receipt其它损坏及全部payload/
command字段契约仍待核验；Pre账本恢复、异步/MCP执行也不由Post单独证明。

摘要模型owner修复：原生session/turn.rs::run_auto_compact的普通本地分支传
step_context.turn，compact.rs及Hook也使用该Turn的model_info。Corki实际摘要
已委托_local_compaction_owner，但新增Hook回调误用了当前Step的_model_name。
在真实update_turn_settings large→small场景加Pre/Post验证，两反例均出现
payload small而实际摘要large（c9e345）。现_summary_model_name沿同一摘要owner
获取模型，回调及持久Post计划均使用它；不改模型调用/重试路径或普通协议。

previous-model降档的真实HTTP离线测试也接入Pre/Post Hook验证：旧large模型摘要、
后续small请求，Hook model=large、trigger=auto；摘要失败只有Pre，无Post；
checkpoint冷恢复不重复通知。完整相关四文件 **90 passed / 14.83s**（d5b835），
Ruff/format/diff通过（b2fc60）。不将此证据泛化为全部模型切换/新输入交错。
下一步回到Hook执行账本unknown/completed、异步/MCP及计划损坏故障验收。

Session/ThreadSpawn身份修复：原生hook_runtime.rs::thread_spawn_subagent_hook_context
只给ThreadSpawn来源附agent_id=thread_id、agent_type=role或default；compact事件
session_id来自session而非thread。新增Pre/Post × root/具名child/default child/
internal八个真实Runtime反例，原先全部把显式session错填成thread而失败（5ceaa6）。
现从repository读取已保存session，只有ThreadSpawn添加agent字段，其它来源不
伪造agent身份。已有batch继续使用原payload，不重写已claim请求造成身份冲突。
与安装恢复/Stop恢复/解析联合 **122 passed / 20.37s**（3b8d93）。内部来源测试
是显式构造并配置Hook，不代表真实memory worker默认会加载用户Hook，二者不可混淆。
安装冷恢复测试另加入warm显式session、cold不传session仍保留原身份的断言。
最终与压缩Hook文件联合34通过（1cb9b2），修改文件Ruff/format/diff通过。

Post计划/安装关联修复：新增真实SQLite安装后抛错、关闭Runtime、公开resume_pending
三场景，原实现新增配置追溯执行/移除配置静默完成两个反例失败（f82e8f），保留
配置对照通过。现在自动prepare与manual compact均在append marker前调用prepare
阶段，保存以marker.id为身份的Post批次或空计划receipt；安装后仅执行该计划。
冷恢复recover阶段不从当前配置创建旧事件；旧版本没有计划的marker不追加新Hook。
计划中尚未claim的Hook授权移除/变化会明确失败，不用已保存计划绕过当前授权。
摘要已安装不回滚，不重新采样；未安装的预存计划不被历史marker引用，不执行。

扩大自动/手动 × 新增/移除/保留六场景通过（202bca），自动由一次大工具结果触发，
恢复不重复工具、摘要仅一次，保留历史前缀，未知授权不静默跳过。联合压缩Hook/
原手动恢复/自动恢复/previous-model/解析 **95 passed / 12.26s**（a2e057），
Ruff/format/diff通过（754a70）。受控Hook runner+真实Graph/SQLite，不冒充OS强杀。
仍需补计划自身保存失败、字段损坏、已claim unknown/completed、异步执行及原生
来源身份的专项；本批关闭的是安装到Post执行之间配置变更丢失的具体窗口。

同步并发事件修复：原生dispatcher.rs::execute_handlers_with_metadata并发执行后
按configured_order排序。新增真实Runtime Pre/Post两个反例，让第二个Hook结果
先提交、第一个随后返回；执行顺序second→first，旧实现完成事件同样反序导致
2 failed（1b91e0），不是串行夹具。已把HookStarted放在任务启动前按配置发布，
同步执行只返回结果，gather/join后按配置顺序发布HookCompleted；异步仍不发同步
通知。新增第二项结果提交后抛错验证：第一项等待被取消并进入finally，TurnFailed
保留真实错误，批次未发布伪成功完成事件。此为受控runner/真实Runtime与SQLite，
不是实际shell进程清理或OS强杀测试。相关三文件 **66 passed / 5.70s**（882415），
Ruff/format/diff通过（5929b6）。首轮hook_id字段夹具错误修正后才得到上述真实红测。
下一步仍优先Post计划与安装恢复关联；不以本批关闭全部取消/冷恢复边界。

当前生产进展：基础执行已实现，下面四项原始反例转绿（8838b0）。新增
core/compact_hooks.py，复用可信配置、command/MCP runner与持久执行账本；
Pre在摘要重试循环外，Post在安装后，continue=false进入取消控制流而不撤销
已安装摘要。graph三个prepare入口及手动节点均传操作回调，previous-model
compact标auto。加入完成receipt，后续prepare不重复已处理的Hook；保存结果
复用，已claim无结果拒绝重放。异步命令复用现有Stop owner及共享并发名额，
只回传诊断不控制压缩，但其真实Runtime故障验证仍开放。

真实Runtime新增允许、非法block、未信任三类，对照原stop共16通过（7946e5）。
receipt修改后，相关手动/自动恢复、previous-model、MCP Hook及context单测
**474 passed / 26.18s**（2c4cca）。随后按原生looks_like_json修正普通JSON标量
文本也应忽略，输出解析16通过（d35714）；小修复后真实Runtime与解析联合
**32 passed / 2.61s**（69c5e1），修改文件Ruff/format及diff检查通过。
未改CLI/teach.md，普通摘要传输不变；先前完整核心全量早于本次生产修改。

剩余具体边界，不可因原四项转绿而关闭本项：
- Post计划现已先保存、后安装并按marker关联；安装后配置新增/移除/保留的自动/
  手动冷恢复已验。计划保存写前/写后失败的八个冷热窗口已补验；其它计划
  字段损坏及未列明执行账本提交窗口仍需专项。
- session、ThreadSpawn agent及摘要owner模型字段已修复；Step激活和previous-model
  降档有真实Runtime/HTTP证据，其它计划字段损坏与执行恢复仍按下列项验收。
- 多同步Hook并发完成事件已按配置顺序聚合，受控runner的提交失败取消join已验；
  两个实际 HTTP MCP Hook 的超时/取消、响应关闭及冷恢复，以及两个实际 POSIX
  shell Hook 的关闭/提交失败、进程退出及冷恢复已补验；跨类型交错仍需补验。
- 异步命令、执行失败/摘要失败无Post，以及未列明的冷热恢复/新输入/取消窗口
  仍需专项测试；普通 mcp_tool 已有上述单/双 Hook 与执行账本证据。
- receipt与snapshot严格字段类型、完整payload身份校验及持久迁移要补验；当前
  只有基础版本、请求身份、未知拒绝和已保存命令恢复，不称完整损坏矩阵通过。

下文为修复前证据及实现依据，不能把“尚未修改生产”当作当前状态。

当前行为反例：tests/integration/test_compact_hooks.py已接真实Runtime，自动由
16000字符工具结果触发，手动走runtime.compact。可信指纹独立按原生规范计算，
命令记录输入并输出continue=false。Pre/Post × manual/auto四场景全部失败，
失败均为命令未执行、输入记录文件不存在（5ec7c0，4 failed / 0.90s）。未xfail，
未改生产，不能称当前测试全绿。后续断言还要求Pre零摘要/零安装，Post一次摘要/
一次安装、停止终态及不再继续普通采样；这些断言尚未执行到，不能当成已验证。
Ruff检查及格式化通过（db649b）。优先实现此项，不继续扩大已通过的MCP矩阵。

范围：属于Harness本地生命周期，不是远程压缩/专有模型协议，不能按官方服务排除。
不修改CLI或teach.md，不引入任何专用压缩端点。

## 源码证据

固定Codex基准ddf04ad26789d040f9ef6a96736f76602e35a6cc：
- core/src/compact.rs::run_compact_task_inner，在摘要实现之前运行PreCompact；
  should_stop返回TurnAborted，不执行摘要。仅摘要成功后调用PostCompact；Post停止
  返回TurnAborted但不撤销已经完成的压缩。
- core/src/hook_runtime.rs::run_pre_compact_hooks / run_post_compact_hooks构造session、
  turn、subagent、cwd、transcript、model、trigger，发布HookStarted/Completed。
- hooks/src/events/compact.rs完整事件实现：按trigger匹配；continue=false停止，
  decision=block不是此事件支持的控制契约；普通stdout忽略，执行错误/非法JSON
  报failed而不自动阻止压缩。该文件测试分别覆盖前后停止、非法block、普通stdout。
- 异步handler能否控制取决于can_apply_control_effects；不能复用同步Stop解析器
  而把所有输出都当成阻断，也不能让后台Hook追溯修改已提交的历史。

Corki当前：core/compaction.py::compact_node直接调用window.compact，没有前后
生命周期Hook；plugins/agent_hooks.py声明事件名不代表执行已接入。自动触发还在
context/window.py::prepare内（包含模型切换及普通摘要路径），不能只修改手动节点
就宣称完整支持。已有Pre/PostToolUse不是Pre/PostCompact，二者不得混算证据。

## 下一步实施门槛

1. 完整追踪自动、手动、模型切换、失败重试的摘要调用与安装边界，确认Hook执行
   属于单次压缩操作还是摘要HTTP重试；不在每次HTTP重试重复有副作用Hook。
2. 对照discovery/dispatcher/output_parser的信任、matcher、同步/异步契约，再
   写真实Runtime反例：可信Pre continue=false应阻止摘要，Post只在安装成功后执行。
3. 复用本地配置与MCP runner、账本和资源owner；明确未知结果不重放及冷恢复边界。
4. 验收前后停止、摘要失败无Post、未信任不执行、取消清理、普通兼容模型请求及
   自动/手动路径。当前已有上方真实反例，尚无本项生产实现。

## 安装与调用所有权补充

- context/window.py::_summarize包含HTTP重试及超窗删旧成对历史后重试；这里不是
  Hook操作边界，不得每次_summary_attempt都启动Pre/Post。
- prepare常规自动路径在摘要和替代窗口构造完成、token校验通过后append_items，
  Post应在该安装之后。manual compact也在append后才发布成功，恢复时检测同Turn
  CompactionItem直接复用，因此必须考虑“安装已提交但Post未完成”的恢复窗口。
- 模型切换路径复制previous-model窗口并调用old.compact(phase=pre_turn)，这是
  自动trigger，不能因调用同一compact方法就误标为manual；payload模型也需对应
  实际摘要执行owner。
- graph.py有初始prepare、后续prepare及实时输入prepare三个调用入口，另有
  core/compaction.py手动节点。接入必须覆盖这些入口且保持输入取消/归还所有权。
- discovery.rs支持异步command（仅SessionEnd强制同步），事件输出以
  can_apply_control_effects约束。sync command与mcp_tool均应复用可信配置判断，
  async不能当成同步阻断；完整来源/事件审计仍独立开放。
