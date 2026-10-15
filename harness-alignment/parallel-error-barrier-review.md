# 并行错误、独占屏障与结果顺序（A5/E1/E7）

## 关闭后准入疑点复核

参考Codex固定commit ddf04ad26789d040f9ef6a96736f76602e35a6cc，工作树干净
（949ac0）。parallel.rs::handle_tool_call_with_source将dispatch放入
AbortOnDropHandle，取消时abort并await；已达终态则保留结果。这不等同于
一个允许任意宿主持有并重复submit的永久队列。

Corki LiveTools.submit本身没有closed检查，但当前src全部调用点仅在graph：
流式入口先await _call_model_owned（内部消费流并submit），返回或异常退出后
才finish/aclose；批量入口同步for循环submit结束后才await finish/finally close。
owner没有交给handler或宿主，故当前调用链未发现关闭与新增submit并发的入口。
不把直接调用内部对象close后再submit的组件反例冒充真实Runtime可达缺陷，
本批不为此添加生产分支。将来若共享此owner或新增生产者，必须重新审计准入。

现有三文件test_live_tools_close、integration/test_live_tools、test_task_ownership
联合48通过，10.77秒（a4782c）。覆盖组件重复取消、真实Runtime batch/streamed
fatal sibling清理期间取消、挂起模型/工具关闭、流式提交及恢复；未增加新测试，
不声称完整E7或所有宿主恶意吞取消情形均已验证。首次误写不存在的测试文件名
导致pytest退出4且零测试（ccebaf），随后按rg定位实际文件运行，不计为生产失败。
CLI和teach.md均未改动。

后续关闭所有者补验：test_live_tools_close.py六组合（工具自行取消/由close取消
×等待者0/1/3次取消），双close观察者共享同一关闭task；工具清理屏障未放行时
两等待者及子task均未结束，子task取消计数始终1。放行后先完成清理，取消的
观察者得到CancelledError，另一个正常结束，第三次close复用已完成所有者。
六项通过（04f06b）；与执行gate、实际Runtime task ownership、MCP readiness、
Code Mode清理/嵌套取消联合83通过（4084f4，10.14s）。静态1214格式、ruff、
compileall、diff及依赖通过（16cd9d）。本批没有额外生产改动。
新增六项是组件所有权证据，真实Runtime组合由前节两反例及联合集成提供；不
把组件测试夸大为物理信号/所有进程恢复验证。完整A–E仍开放，CLI暂停。

上述取消清理缺口现已修复：LiveTools缓存独立关闭任务，首次close只取消未有
pending取消的工具，重复close共享任务；等待者的重复取消不打断子清理，join
完成后再传播CancelledError。两真实反例转绿，task ownership/live tools/恢复
联合70通过（f78544，22.25s）；随后新增最终仅一个TurnCancelled的断言，两项
通过（20c709，0.57s）。静态1213文件/ruff/compileall/diff/依赖通过（1b1500），
最终新增断言lint及diff通过（7b7abe）。没有修改CLI或模型协议。
证据限fatal sibling开始清理后外部取消的batch/streamed组合，不宣称全部
readiness、宿主恶意吞取消或跨进程资源恢复均已关闭。

最新已复现缺口：真实Runtime batch/streamed中，fatal sibling触发LiveTools.aclose
取消slow，slow正在finally等待清理屏障；此时cancel_active取消Turn所有者，
未shield的gather把第二次取消传入slow，导致清理被提前打断。两个反例失败
（82eb21），观察到corki-live-tool-slow已cancelled而release尚未放行。
原生parallel.rs取消后abort/await dispatch，收尾仍有明确任务所有者；Python
需要区别首次取消工作与等待清理时的重复取消，不能以asyncio.run兜底消除证据。
方案：LiveTools缓存独立关闭task，只发一次必要取消；所有close等待者shield/join，
等待结束后再传播其取消，重复close不重新取消正在清理的工具。实际回归尚待修复。
首次测试导入FatalToolError路径错误（bcde3e）为夹具错误，不计生产反例。

基线补正：整体运行 61fb44 暴露测试误要求 slow handler 先启动；并行 claim
及调度不保证这一顺序。已允许前两个 start 任意顺序，并额外强制 fast 先
启动，保留后续完成/独占屏障/模型与历史结果排序检查。四组合扩为八组合，
联合 storage/history 等 216 passed（9a8039），静态 bb04d8 通过。此次只
修正并加强测试，没有修改生产调度；不将旧基线的失败记成成功。

参考 core/src/tools/parallel.rs::ToolCallRuntime：保留宣布工具的 StepContext；
parallel 用 RwLock read，exclusive 用 write，取消先 abort 并等待 dispatch task。
普通 FunctionCallError 转失败 function output；Fatal 单独返回错误。session/turn.rs
使用 FuturesOrdered 收集并 drain 工具输出。原生 namespace/tool_search_output 等
排除路径只阅读，不实现。不能把 handler 普通错误等同于宿主存储/任务致命失败。

Corki ToolExecutor.execute 将普通 Exception 规范化为错误 Observation，FatalToolError
和取消不转普通结果。graph._execute_tools_owned 的 batch gather 保持输入顺序；
LiveTools.submit 的依赖屏障允许 parallel 重叠、exclusive 等前序任务，finish 按提交
顺序收集。宿主存储失败则由 graph 关闭/等待任务，禁止带着未完成 ledger 继续采样。
后一项是 Corki 持久化边界的证据，不据此声称与原生任意 fatal 错误逐项等价。

test_task_ownership.py 新增 4 例：batch/streamed × 显式错误结果/handler ValueError。
slow、fast 并行，fast 先失败，slow 被事件屏障保持，尾部 exclusive 此时不得开始；
release 后 slow 完成才轮到 tail。下一请求及 canonical 结果严格 slow/fast/tail，
中间一项 is_error=True，两次采样正常完成，结束前无 live-tool 任务残留。
这补齐已有“两并行成功”与“并行存储失败”之间的普通错误组合。

既有 claim/complete 存储故障再增加 streamed 两例：等 sibling 已运行才报存储错，
验证 TurnFailed 前 sibling 已取消且 task.done，ledger 后续给 unknown 结果且不重复执行。
代码只扩展测试，无生产实现修改。首轮 13 passed（c5a94d）；最终 task ownership、
recovery/concurrency、memory tool failures、tool claim shutdown 联合
89 passed、0 skipped，15.68 秒（30c848）。静态 b36d84 全通过：ruff check/format、
compileall、uv pip check、git diff --check，1136 文件、77 包；全部进程已退出。

预设模型调用仅证明调度与错误路径；不证明模型选择质量、全部执行器 readiness 排队、
Code Mode 内部调用或所有 fatal 错误与原生一致。A5/E1/E7 的其余范围仍开放。
