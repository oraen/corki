# 子进程退出测试：旧观察结果不是实时状态

修订后非CLI全量35013已实际退出0（c86532）：15504 passed、1文件系统限制
跳过，535.66秒，含新增延迟leader组合。完整日志full-regression-35013.txt；
下面64194失败、确定性复现及定向验证均保留，不回写为历史全绿。

全量64194已实际退出1（7ded6e）：15499 passed、1 failed、1 skipped，539.43秒。
完整日志full-regression-64194.txt；跳过仍为文件系统不支持非UTF-8文件名。
旧记忆心跳失败未复现；本次失败为test_process_inherited_pipe.py的observe/tty场景。
运行期间未修改被测代码/测试，未重启worker，不将本次全量记为通过。

## 原因与确定性复现

ProcessManager.execute等待yield_seconds后调用_observe。若主进程仍在运行，
返回不可变ProcessObservation(exit_code=None, session_id=...)；之后主进程退出
不会更新这个旧对象。write_stdin(session_id,"",yield_seconds=0)才是新的观察。
_observe在确认主进程退出后有界等待继承stdout的reader，再retire所属进程组。

旧测试以0.05秒yield启动命令，随后轮询真实returncode，最后await原execute
task并把它的旧结果当成最终状态。全量中旧结果为空输出且exit_code=None，
与这个边界吻合，不是依据测试名推断manager挂死。
新增delayed_leader参数：fixture子进程准备完成后，主进程额外等待0.15秒才退出。
旧断言下两项observe（tty/pipe）均复现相同None退出码失败，原无延迟tty例通过：
2 failed、1 passed，2.00秒（b42c8b），22274退出1。未更改生产实现。

## 修订与验证

确认真实主进程退出后，在原1.5秒总收尾上限内读取execute结果；若有session_id，
只对同一会话空输入观察一次，合并两段输出。仍要求退出0、无session、无timeout、
保留PARENT-OUTPUT、manager目录为空、reader/timeout任务完成、PTY关闭。
close分支同样增加延迟组合；现有真实Runtime/cold-history断言不改。
不是放宽assert或忽略错误，也不延长生产超时、修改取消策略或重放命令。

五文件联合59 passed，13.38秒（7ab66d），80698退出0，4 workers/loadfile/禁重启；
覆盖inherited_pipe、process_session_scope、task_ownership、ordered_tool_publication、
memory/test_ownership。全局ruff/1314文件格式/diff通过（554b4d）。
原全量失败日志保留；下一步仍需修订后的非CLI全量验证，不能用59通过代替。
