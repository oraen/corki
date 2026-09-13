# A2/A4 默认循环次数上限：实现前差异审计

## 最新实施（覆盖下方实现前状态）

pending input 与最后一步交叉补验：扩展 test_turn_input_boundary.py 的真实
Runtime 场景为 model_commit/after_evaluate/before_finalize 三窗口 × max_steps
为 None/1/4 九例（原三例保留为 4）。默认和足够预算下同 Turn 第二次采样
处理新输入；1 步预算下单采样失败，不错误完成。全部验证原输入和 late input
各一个独立 ID、单终态、realtime 关闭，正常收尾输入不作为取消草稿交还。
输入边界/pending sampling/steering/默认长任务联合 47 passed（3dbee1，10.61 秒）；
src/tests 静态和 src compileall、依赖检查、diff 检查通过（baa09a）。
只扩充测试，没有新生产修复。不是物理崩溃或输入落库失败的交叉证明。

源码依据：原生 core/src/tasks/mod.rs::on_task_finished 在普通错误路径仍取
pending input 并 run_hooks_and_record_inputs；Corki runtime.py::_execute_run
关闭准入后，非 TurnCancelled 才 _flush_realtime_inputs，取消则按已提交 ID
排除后交还。graph.py::_finalize 的 pending 检查先于完成，并复用显式预算。
此证据补充 pending-steer-ownership.md，不能外推 Codex 有同名 Step 预算。

显式最后一步边界补验：新 test_explicit_last_step_allows_final_but_not_followup
覆盖 batch/streamed × 正常终答/显式继续/工具调用，实际 Runtime 配置 max_steps=1。
终答允许完成；另外两种失败且仅一个失败终态、仅采样一次、工具零执行，历史
用户输入一份、无工具结果，关闭模型。模型宣称 end_turn=True 也不能让工具
调用绕过后续工作判断。六例及本文件长任务/既有 continuation/evaluator 联合
33 passed（a356a8，8.62 秒），没有生产修改，不声称发现或修复了新缺陷。
静态 src/tests ruff check/format（1136 文件）、src compileall、77 包依赖检查及
git diff --check 通过（17a37f）。此前全量 13574 passed/1 skipped 不包含这六例。

本次重读原生 session/turn.rs 采样后 needs_follow_up 合并、压缩、stop hooks
链及 Corki graph.py 三处 evaluator 入口与 finalize 的持久 pending-input 检查。
这组测试验证的是 Corki 显式宿主预算边界，不声称 Codex 原生也有同名预算；
默认普通循环对齐证据仍是下方长任务用例。pending input 恰好撞预算、重复调用
身份及所有错误终态仍应按各自专项核验，不能由此关闭整个 A4。

长任务普通压缩组合补验：batch/streamed 两场景在第 26 次常规采样返回高 usage，
触发一次无工具的普通 ModelRequest 摘要；随后继续至第 28 次采样结束。
验证所有常规请求的当前输入仅一份、可见 tool call/result 成对、27 次工具
按唯一 ID 只执行一次；原始归档保留全部调用和结果。归档中的输入保留原件
及 retained_from_id 指向原件的替换副本，active_history 仅有一份。
初跑两失败（40bc1b）是错误地要求 append-only 归档全文只有一份用户文本，
不是生产重复输入；核对 window.py 与 local_retention.retained_copy 后修正
断言，新增原件/副本来源检查，不删归档或放松模型请求唯一性。
与 body_compaction/compaction_wire_contract/model_search_compaction 联合
166 passed（505d7a，17.19 秒）；最后来源检查增强后本文件 6 passed
（7c3598），全部静态通过（ad06f3）。本批未改生产代码，无活动测试。
这是脚本模型 + 真实 Runtime 的普通摘要请求，不能证明摘要语义质量，亦不
声称同一长任务同时命中了压缩与崩溃窗口；完整 A–F 仍未完成。

执行中恢复补验：test_mixed_output_commit_recovers_without_repeating_tools 增加
26 次有效 end_turn=False 采样前缀，再到第 27 次混合正文/reasoning/并行
调用提交后暂停。沿既有实际 compiled graph + SQLite/checkpointer 故障注入
入口取消图任务，保留 RUNNING Turn，关闭 warm 并用新 Runtime/模型恢复。
这绕过正常 Turn 终态收尾以模拟提交窗口，不是物理 kill 或正常用户取消。
新增六组合：batch、streamed、streamed 结果提交挂起 × Runtime/CLI 恢复。
验证 warm 只采样 27 次，cold 只采样一次；26 个前缀消息按序且只出现一次，
混合消息和工具结果不重复、两工具最终各执行一次、再 resume 无待处理 Turn。
与现有恢复及默认长任务文件联合 32 passed（24cd8e，15.23 秒）；全部静态
通过（d47cbc，1149 文件/77 包），本批未改生产代码，测试进程已退出。
新长前缀不包含同轮压缩或 Code Mode，不能外推所有长任务故障窗口已覆盖。

后续冷启动补验：四个长任务场景现在均在 warm Runtime 完成并关闭后，使用
新 Runtime/模型/registry 打开同一 SQLite Thread。resume_pending 返回空且
不调用模型；完整旧历史不变，后续 Turn 请求中的正文/调用/结果逐项等于
原历史，旧 81 次工具副作用不增加；新 Turn 仅采样一次。覆盖普通与 Code Mode、
batch 与 streamed，不把“完成后的重新打开”冒充执行中进程崩溃。
与既有 recovery_and_concurrency、model_search_compaction、memory_pipeline_runtime
联合 40 passed、0 skipped（5a0ce9，15.65 秒）；静态全部通过（3b8f52，
1149 文件/77 包）。本批只增强测试，未改生产逻辑，所有测试进程已退出。
已有中断/压缩/记忆测试与新长任务在同一回归组，不等于把这些故障都组合
进了同一个 28 Step 长任务；该更强交叉场景仍待补证。

默认 max_steps/max_tool_calls 已改为 None，仅显式正整数启用限制。新配置
只写注释示例，已有配置数值保留。验证仍拒绝零/负数/浮点/布尔。
evaluator 三调用入口共用可选检查；无 Step 上限时 graph 使用现有无界重试
路径的最大 recursion 值。CodeModeService.invoke 和 Cell 嵌套准入均支持
无次数上限；并发单元、内存、身份、取消、输出和上下文限制不变。

新增真实 Runtime 批量/流式长任务修改前两例被 64 调用上限拒绝（b73364）。
修复后四例覆盖 28 次采样/81 普通调用以及真实 Code Mode 单元内 81 嵌套
调用，检查完成、唯一执行及历史数量；与 Code Mode 生命周期联合 24 passed
（aebc41）。新模板断言另 3 passed，不仅验证属性默认值。

配置/evaluation/项目层/流式准入/继续/输入边界/重试初跑 707 passed、15 skipped
（0422f0，未传 compiler）；启用真实 compiler 后重跑该组 719 passed、
1 skipped（439cca，22.89 秒），仅文件系统非 UTF-8 路径 EILSEQ。
未放松原显式预算拒绝测试。development.md 已说明默认/显式预算和旧配置。
冷恢复/任意长程压缩/Code Mode 取消的完整组合仍需继续核验，A–F 未完成。

状态：行为不一致，待修复；不以已有显式预算测试通过视为默认路径对齐。

参考普通主循环 `codex-rs/core/src/session/turn.rs`：采样成功后合并
model_needs_follow_up 与 has_pending_input；需要继续且达到上下文阈值时
run_auto_compact，然后继续循环；否则在无后续工作时进入 stop hooks。
这里没有 Corki 的每 Turn 24 次采样/64 次工具调用默认硬上限。
本结论限定该普通循环；不等同于所有原生子代理、资源限制或产品配额无限制。
专用压缩只参考所有权/继续语义，仍不得复制为 Corki 的专用传输。

Corki 当前链路：

- `config/settings.py` 默认 max_steps=24、max_tool_calls=64，读取 [agent]
  覆盖值并强制正整数；未显式配置也会启用这两个限制。
- `evaluation/guards.py::evaluate_model_step`：需要继续且 step_count 已到
  上限则 FAIL；工具调用计数加当前批次超过上限也 FAIL。
- `core/graph.py` 流式 item 准入、evaluate、工具后续判断均使用该配置。
- `core/runtime.py::_graph_config` 又由 max_steps 推导 LangGraph 递归上限；
  单改 evaluator 会留下第二个硬停止点。
- Runtime 把 max_tool_calls 传给 CodeModeService，service.invoke 及 cell
  收到 tool 消息分别比较 max_calls；这不是单纯 UI 或文档默认值问题。

实际影响：正常长任务仍有待执行调用、上下文可继续压缩且无用户停止时，
默认 Corki 会因固定次数提前失败。不能用“教学简化”判定可接受，也不能
仅把次数调大来伪装成原生循环。显式宿主预算是有用的独立控制，应保留。

拟修复边界：默认无额外次数上限；显式正整数配置仍启用原有准入/终态检查。
需一起处理配置验证、graph recursion、流式与批量准入、Code Mode 两入口，
并核实旧会话/配置是否保存这些字段。不要放松上下文、取消、工具身份校验或
并发资源限制，也不要将次数上限和模型 HTTP 重试预算混为一谈。

验收要求：默认真实 Runtime 超过 24 个后续 Step、超过 64 个普通工具调用
仍能完成；显式小限制继续在副作用前拒绝；模型重试不消耗逻辑 Step；
Code Mode 嵌套调用、冷恢复与运行期新输入保持原有所有权。首先建立默认
长任务的失败测试，再实现可选限制，不能仅改设置单测。

本批只定位调用链及审计，不宣称已修复。已有 test_model_continuation、
test_live_tools、test_turn_input_boundary、test_history_retry 是显式边界
与恢复回归入口，不证明默认长期任务没有上述限制。

本批上述四个集成文件与 unit/evaluation/test_guards.py 联合 81 passed
（38b96d，16.73 秒），测试进程已退出；生产代码及测试未改动。
