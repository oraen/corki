# 记忆心跳故障测试的计时边界

修订后完整非CLI回归已通过：35013实际退出0（c86532），15504 passed、
1文件系统限制跳过，535.66秒，包含慢准备两类心跳失败。原88968失败日志保留，
定向测试与全量分别记录；未修改生产逻辑。日志full-regression-35013.txt。

64194已结束：心跳测试（含慢准备）通过，但全量另有子进程旧观察结果断言失败，
15499通过/1失败/1跳过，539.43秒，退出1（7ded6e）。不是全量全绿；详见
process-observation-test-boundary.md。修订子进程测试后五文件59通过，需刷新全量。

后续全量已启动：**64194运行中**。8 workers，范围及隔离参数同88968，启动前
无旧测试进程；12逻辑核/18GiB、报告43%空闲，历史compiler均存在（a444d8）。
本轮覆盖新增慢准备用例；没有缩减覆盖，不将定向339通过或运行中状态当全量通过。
先观察同一句柄至退出，运行时不改被测代码/测试。

全量88968已实际退出1（5bc64a）：15497 passed、1 failed、1 skipped，544.59秒。
完整原始输出见full-regression-88968.txt；唯一跳过为非UTF-8文件名文件系统限制。
失败：test_ownership.py::test_failed_heartbeat_stops_model_and_does_not_publish[error]，
总run_once超过1秒。不能把本次全量记为通过，也没有在全量运行时修改被测文件。

源码：memory/pipeline.py::_run_once 在真正启动 consolidation/heartbeat 前会
完成线程内目录准备、扩展指令、SQLite维护与claim；_run_owned_consolidation
独立创建work和heartbeat，心跳异常后取消并join，再进入失败记录。
旧测试的wait_for(run_once,1)把所有准备/收尾I/O算成了模型停止时限。
邻近test_complete_response_closes_stream已按模型开始事件隔离了同类计时边界。

确定性复现：给ensure_layout增加1.1秒阻塞（在既有owned线程里运行），不改
生产代码；lost/error两项均在旧wait_for处超时（283929，2 failed，3.44秒，
96592退出1）。它证明旧测试可在心跳尚未发生前失败；原全量没有分段时间埋点，
因此不声称已经精确量出原失败发生在哪一项准备I/O。

修订：准备阶段等待真实heartbeat_failed事件（10秒安全上限），随后仍用
**1秒**等待模型finally/closed；收尾报告另有10秒上限。保留失败计数、模型关闭、
MEMORY.md未发布和warning断言；finally显式取消/join pass并关闭service，不依赖
asyncio.run全局清理隐藏泄漏。新增慢准备参数不skip、不放宽心跳后停止检查。

tests/unit/memory + test_memory_direct_run_ownership + test_ordered_tool_publication
联合339 passed，15.50秒（46b415），77060退出0；4 workers/loadfile/禁重启，
null keyring、当前sandbox compiler。全局ruff check、1314文件格式与diff通过
（a869bf）。集合不是全量，旧全量失败仍保留；下一步刷新非CLI全量，生产代码
本轮未改。CLI对齐仍暂停。
