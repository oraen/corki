# 33685 stdin 等待断言诊断与修订

全量失败原始日志保留在 full-regression-33685.txt：15589 passed、3 failed、
1 skipped，实际退出 1。三个失败时观察值均有活跃 session_id，尚无预期输出。

源码契约：Codex unified_exec/process_manager.rs 初始执行按 yield 时间收集
输出快照（显式 completion timeout 为独立模式）；Corki ProcessManager.execute
在所有权交给 session 后 `_wait_session(yield_seconds)` 再 `_observe`，并不保证
该观察时刻进程已结束或打印 READY。write_stdin 空输入可继续观察同一 session。
原测试直接把 0.2 秒 yield 当作就绪/完成保证，缺少合法活跃返回的处理。

测试修订仅在 tests/integration/test_stdin_terminal_contract.py：有界轮询同一
session，累积输出，等 READY 后才发控制字符，终态才断言 exit_code。保留
EOF、closed stdin、raw BYTE:03、controlling tty、前台子进程 SIGINT/父进程存活
等原断言。生产代码、yield=0.2、timeout=3 均未提高或修改。

六个门控参数让子进程等待临时文件：初次 execute 必须返回空输出和活跃 ID，
测试再释放文件并轮询。这在无机器负载假设下检验上述合法时序。初批 33305
实际退出 0（3063dc）：22 passed / 5.61s。负向对照替换 observe_until 为不轮询，
三个 gated eof/raw/pipe interrupt 均被原成功断言拒绝（9461ac 输出）。该复现
证明测试的契约缺陷，不声称恢复了原全量运行中造成启动延迟的具体系统原因。

过程失败记录：初版两个长行 lint 未通过；拆行后 format --check 又提示排版，
format 修正后 lint 通过。一次错指定不存在的 test_process.py，10356 实际退出
5、no tests ran（51d2b2），不计入通过。

最终六文件联合：stdin_terminal_contract + unit/tools 的 process_io、
process_status、process_groups、process_cleanup_ownership、process_retention_policy。
4 workers/loadfile/禁重启，null keyring 与当前 sandbox compiler 显式配置，
**78 passed / 11.99s**；12980 在输出汇总后继续等待，最终实际退出 0（609ed7）。
生产未改。需要刷新非 CLI 全量，不以这 78 项覆盖掉 33685 的失败结果。

此项属于核心执行工具的 stdin/PTY 语义，不恢复暂停的 CLI 页面/交互对齐。
