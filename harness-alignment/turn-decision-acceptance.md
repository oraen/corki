# A2/A4：主循环判断验收与剩余 Stop hook 差异

## 当前主循环边选择复核

下文“Stop hook 尚未实现”及早期“异步/non-root 未实现”均不是当前状态。
root Stop、SubagentStop、同步并发及异步会话owner已接入，分别见
stop-hook-execution-plan.md、subagent-stop-review.md、sync-hook-concurrency-review.md、
async-stop-review.md。完整A4的剩余来源、授权UI和资源专项仍以总索引为准，
不能为了保留“部分一致”标签而把已经实现的主路径继续列为缺失。

本轮逐边核对graph.compile：capture_step_settings→prepare_model_context→call_model；
call_model按steered/sampled/retry进入重新prepare/evaluate/retry_model；evaluate的
execute_tools/continue/finalize/fail四条边分别进入工具后prepare、直接prepare、
最终输入与Stop复核、失败终态。finalize只能结束、继续或失败，不因已有正文跳过工具。
guards按显式预算及工具调用优先级选择，缺失ModelCompleted的协议失败不进入空完成。
因此A2应按真实循环及路由证据核销，不再只以“三步搜索局部实证”描述整个实现。

另外核实最终答案的采样边界：Codex session/turn.rs每个采样请求初始化自己的
last_agent_message，仅在不再follow-up时把该请求结果交给Turn/Stop；
stream_events_utils.rs::finalize_non_tool_response_item忽略全空白消息，非空内容
依次覆盖同一采样请求的last_agent_message。它不是跨整个Turn查找最后一条正文。
Corki _finalize在last_model_items中逆序选择非空AssistantMessage，符合这条边界。
扩展实际Responses适配器的“工具→显式继续→完成”序列至空、仅reasoning、单条正文、
多条正文夹空白、仅空白五例，直接断言唯一TurnCompleted的final_answer。
空终态不得回填早先interim正文，多条终态不得拼接所有正文。这是补验，不是生产修复。

当前guards与default_loop_limits/model_continuation/turn_input_boundary/live_tools/
recovery_and_concurrency/stop_hook_control/stop_pending_recovery联合184通过
（7a96ad，44.10秒，64452退出0）；包含默认长循环、显式预算、工具/继续/完成、
采样和工具恢复、Stop优先于晚到输入及Stop阻止后的继续边。
静态ruff、1184文件格式、compileall、77包依赖通过（600a9f）。A2主循环路由
据上述源码与行为证据判定已核验；A4完整Hook来源、A5调度、A6/A7/A8及E7资源
故障矩阵各按专项独立验收，不从本批推断整个Runtime完成。

## 早期审计与证据（保留追溯）

2026-09-12 复核。参考 commit ddf04ad26789d040f9ef6a96736f76602e35a6cc。
本轮未改生产/测试。目标不是继续增加任意组合，而是核销已有证据并列出实际缺口。

## 已有判断路径

原生 core/src/session/turn.rs 采样后合并 model_needs_follow_up 与 pending input；
有后续工作时检查窗口并压缩/继续，没有后续工作才进入 Stop hook/结束分支。
TurnAborted 单独传播，其余错误发错误生命周期，不当成成功。
本目标用普通请求摘要代替原生专用压缩；不保留官方账户或远程压缩路径。

本地 evaluation/guards.py → core/graph.py::_evaluate 的边选择与
_finalize 的最终输入检查已接真实图；core/runtime.py 按 Completed/Cancelled/
Failed 保存 TurnRecord，turn_run.py 的独立 done/terminal 不依赖队列腾空。

| 判断 | 当前证据与准确边界 |
|---|---|
| 正常完成 | 无工具且 end_turn 非 False，合法空完成也可完成；test_model_continuation 的普通 HTTP 路径，不将缺流终态视为空完成 |
| 继续 | 工具执行回灌或 end_turn=False；continuation 的模型提交/checkpoint 冷恢复保持 False，不重采样已提交结果 |
| 默认预算 | 不设默认 Step/工具次数硬上限；test_default_loop_limits 实际超过旧 24/64，含 Code Mode 与普通自动摘要 |
| 显式预算 | 最后允许 Step 可终答，不可再调用工具/继续；batch 超工具数在执行前失败，streamed 每次接收完整调用先检查，之前合法执行的调用不承诺撤销 |
| 新输入 | _finalize 比对持久输入 ID 并检查 realtime；test_turn_input_boundary 在提交/evaluate/finalize 窗口验证继续或预算失败，不重复已接收输入 |
| 失败/取消 | live_tools 与 recovery_and_concurrency 覆盖截流、副作用后不重试旧请求、畸形调用、取消；资源所有权与存储故障详见 A6/E7 专项，不用此表外推全部关闭 |

本轮重新运行 evaluation 单元与 default_loop_limits、model_continuation、
turn_input_boundary、live_tools、recovery_and_concurrency 六个集成文件：
108 passed，30.70 秒（195f99），session 84840 退出 0。
因此总索引的“预算专项待收敛”已过时，应引用具体已验证路径。

## 确认缺失：配置的 Stop hook 尚不能控制结束

原生 features/src/lib.rs 的 CodexHooks 为 Stable/default_enabled=true；
session/mod.rs::build_hooks_config 合并 config/plugin hook sources 与信任配置；
session/session.rs 创建 Hooks。hook_runtime.rs::run_turn_stop_hooks 构造请求，
按 root/subagent/internal source 选 target，调用 Hooks::run_stop。
hooks/src/events/stop.rs 在无匹配 handler 时返回空 outcome；有配置时执行并
产生 should_block/should_stop/continuation_fragments。
session/turn.rs 在 should_block 且有 prompt 时写入片段、设置 stop_hook_active
并继续；无 prompt 的 block 发警告后忽略，should_stop 结束；记忆合并工作者
遇到管理拒绝走失败，而非无限继续。

Corki plugins/agent_hooks.py 明确只做 legacy metadata admission，唯一消费点
是 agent_overlay.py 的校验；core/graph.py::_finalize 没有对应执行/结果判断。
不能把这个文件的存在当成 hook 能力已实现。无配置默认路径的完成证据有效，
但配置 Stop hook 的路径缺失，A4 只能判“部分一致”。这不是官方服务专属功能。

影响：用户配置的完成检查无法阻止模型提前结束。优先级：主循环控制功能缺口。
下一批实施前须追踪本地 hook 配置来源/信任与执行所有权，形成执行方案；
不能把任意插件元数据直接升级为可执行命令，也不复制云端管理或官方配置分发。
验收须含无配置、允许结束、携带提示的阻止完成、无提示阻止、再次触发标志、
失败/超时/取消、持久历史与恢复、记忆来源隔离，接真实 Runtime，而非空回调接口。
