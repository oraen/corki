# B10：Code Mode 生命周期验收与剩余交叉项

## D1 已提交 store 的会话边界（本轮补证）

固定原生 session/session.rs:1491 每个 Session 创建 CodeModeService；
tools/code_mode/mod.rs:70、88 使用独立 OnceCell。code-mode-runtime 的
session_runtime/mod.rs:46、66 以空 HashMap 初始化 stored_values，:287–296
只在完成提交时合并 writes；service_tests.rs:411 直接验证同会话共享、不同会话
隔离。它不是随历史归档的长期记忆，也不要求 fork/冷启动恢复变量。
Corki runtime.py:470 创建独立 CodeModeService；service.py 的 stored 空字典、
commit 合并，以及 cell.py 的入场深拷贝/结果提交对应这层所有权。

新增 test_code_mode_store_scope.py，通过真实模型循环及 QuickJS 执行六次脚本：
先提交 store，再手动压缩并在下一 Turn 读取；fork 从空 store 开始，写不同值
后源会话仍读取原值；关闭源后同 Thread 冷启动读为空，不重放旧脚本。摘要仅
生成一次，分支写入不改变源归档，压缩和冷启动保留原历史前缀。
与 lifecycle、fork_plan、fork_compaction 联合 15 passed、5.74 秒（b59f68），
58439 经清理后实际退出 0（cf8c95）；4 workers/loadfile/禁重启，null keyring
与当前 sandbox compiler。Ruff/格式/diff check 通过，无生产改动。
这关闭 D1 已提交 store 的该组合证据缺口，不代表任意未提交副作用可恢复。

当前确认：command 类型同步/异步 Pre/Post 已实现，下方“完全没有前后Hook”
是历史记录。type=mcp_tool也已接入同步runner，原Pre两反例转绿，新增Post普通/
嵌套验证，与command异步和冷恢复联合322通过；详见mcp-tool-hook-gap.md当前节。
MCP自己的撤销、真实传输故障与冷恢复仍待验收，B10保持开放，非官方功能。

最新修复：graph._activate_code_mode不再用默认值重建嵌套GraphRunContext，
改为保留宿主上下文、仅替换cell事件出口。修复内部worker误执行用户Pre Hook
及子Agent身份丢失，同时保留审批回调、来源和已入场能力。Pre/冷恢复/完整
Code Mode/cell cleanup联合396通过（f12f9d）；源码依据、红绿过程及测试边界
见tool-hooks-review.md最新节。不据此关闭完整Hook或全部A–E。

当前用户路径：exec/wait 均通过普通函数传输，内部运行本地 QuickJS；不使用
原生 custom/namespace/tool search 或官方远程服务。原生参照为固定干净commit
ddf04ad26789d040f9ef6a96736f76602e35a6cc。

本批读到的原生链是 core/src/tools/code_mode/execute_handler.rs：解析pragma →
捕获nested definitions → service.execute → 标记cell可分发 → initial_response →
非yield才finish_cell_dispatch → 等待elicitation → handle_runtime_response。
wait_handler.rs解析JSON，选择wait/terminate，终态关闭dispatch，转换增量结果。
delegate.rs 的broker把新调用交给当前Step worker，已入场调用保留其host。
code-mode/grpc_session 的观察者互斥、取消guard与generation身份见spawn-review。

Corki graph._activate_code_mode（activate调用所在闭包）把嵌套调用接到普通
_execute_one和SQLite ledger；service.activate/pause控制当前worker，Cell负责引擎
进程、观察者及回调。Runtime终态完成只暂停，不杀yielded cell；失败/取消及关闭
调用deactivate(interrupt=True)后才释放依赖。worker stdin EOF终止引擎，store只在
收到完整模块结果时提交；重启不恢复活cell，不根据旧cell ID重放未知副作用。

| 生命周期要求 | 当前行为证据（tests中的实际实现已核对） |
|---|---|
| 引擎缺失与配置门控 | test_code_mode_config：真实启动降级/失败关闭；不能静默执行被隐藏工具 |
| 发现与嵌套调用 | test_code_mode：ALL_TOOLS过滤后调用普通ledger工具；namespaces/exposure另有覆盖 |
| yield/wait增量与单观察者 | test_code_mode_cells、test_code_mode_native_output：重复观察拒绝，取消观察不消费输出，下一次只读新输出 |
| 跨Step/Turn入场 | worker_scope、step_admission：prepare间隙等待，后续调用当前策略，已入场调用保持旧资源与事件寿命 |
| 并行/独占 | test_code_mode、lifecycle：嵌套屏障；新Step新gate，采样重试仍复用同gate |
| 模块结束与store | lifecycle、cells：正常/JS错误提交writes，终止不提交；未await工具被取消join；store按入场快照 |
| 冷恢复与副作用 | lifecycle、worker_scope：旧cell缺失不重放，已提交tools节点恢复不再采样，原调用只执行一次 |
| 媒体、压缩、历史 | media、output_wire：真实引擎输出/两普通接口/冷回放，媒体成本触发压缩仍保留当前输入 |
| 错误与取消 | error_channels、nested_cancellation：JS可捕获工具错误但不能吞ledger致命失败，整Turn取消不继续采样 |
| 进程与回调关闭 | cleanup、cell_cleanup、lifecycle：忙循环/父进程死亡/kill或reap故障/重复取消，终态和回调join；spawn两个缺陷已修复 |

当前联合命令为test_code_mode*.py（integration和unit/tools）及test_cell_cleanup.py，
显式使用本地sandbox编译器，256通过、0跳过（3645ec，39.66秒，61317退出0）。
这是当前目录集合回归，不是所有Harness测试；脚本模型不证明真实模型选择质量。

## 尚未关闭的交叉缺口：工具前后Hook

原生registry.rs在handler前run_pre_tool_use_hooks，可阻断/更新输入并注入context；
handler后run_post_tool_use_hooks，随后处理反馈。wait_handler明确禁用自身前后Hook，
但普通嵌套工具仍走该链。这是普通本地Harness功能，不是官方服务排除项。
Corki plugins/agent_hooks.py仅接受PreToolUse/PostToolUse元数据，core/stop_hooks.py
只构造Stop/SubagentStop快照，ToolExecutor尚无前后Hook执行链。元数据可解析不能
作为执行已实现的证据，Stop支持也不能代替工具级阻断/反馈。

状态：B10上述生命周期子项已核验，但与A/E共享的工具Hook行为仍缺失，不能把
整个工具执行链写成完全一致。下一步应先追踪原生Hook配置/信任/匹配/响应契约，
再复用Corki已有授权及Hook进程所有权，接入普通工具和嵌套工具；必须保证wait控制
不被Hook阻断、输入改写在副作用前验证、结果未知不重放、取消不吞。不要借此实现
官方远程Hook服务或恢复CLI视觉工作。
