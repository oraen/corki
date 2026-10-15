# B10/A6/E7：引擎进程创建交接中的重复取消

## 取消后创建失败：进一步故障窗口

原生 grpc_session/callbacks.rs 的调用完成采用biased取消优先，不把取消后的
callback结果提交为正常完成。Corki本地spawn等待同样需保留已接收的取消：
当前shield循环虽保持资源所有权，但随后spawn抛OSError会落入普通failed分支。
新增0/1/2次取消与创建失败对照，单次/重复取消均复现取消被吞，未取消对照正常。
修复限定spawn交接：等待中的普通异常若已有取消，作为CancelledError的cause
保留后传播取消；没有取消仍报告原始创建失败。不将创建错误误报为清理故障。

已实施：两取消反例先失败、其余7项通过（389ada）；修复后六文件71通过
（3fe69e，13.28秒），扩展实际Runtime成功创建/晚失败两分支后清理两文件16通过
（dc5dc4，1.51秒）。实际Runtime仍收到terminated Observation再继续，零store提交，
未把独立cell取消误认为整个Turn取消。静态770e11通过：ruff、1190文件格式、
compileall、77包依赖、diff；参考commit不变且干净。测试进程已全部终止。

参考 Codex code-mode/src/grpc_session/operations.rs 的 wait/terminate：观察者permit
与取消guard绑定，终止结果经 validate_wait_cell 后才报告；generation.rs 的delegate
与公开cell身份绑定host generation，旧generation不能接管新cell。原生通过独立
code-mode host管理引擎，不是Corki每cell启动Python/QuickJS进程；不照搬远程协议。

Corki Cell._run 创建spawn任务，第一次取消用shield保护，但异常分支直接await spawn。
第二次取消可取消spawn交接任务：已创建的进程尚未赋给self.process，finally清理
看不到它。这是Corki本地资源所有权缺陷，不能以原生实现不同判为不适用。

先加入实际引擎进程的交接故障注入：create_subprocess_exec已返回真实process，
但包装协程暂未交给Cell；单次/重复取消后都必须接管并reap进程、关闭stdin，不执行
store写入。初验重复取消失败、单次及既有清理对照通过，测试finally显式回收泄漏。
修复方案：始终shield等待spawn终止，记录取消；拿到结果并转移process所有权后
再传播取消，由既有清理路径kill/reap。不得把取消作为成功cell提交，也不重启采样。

已实施：真实进程反例修复前1失败/5通过（6af95c），重复取消时self.process为None；
修复后Cell清理、Runtime清理、嵌套取消、跨Turn/冷恢复、worker scope和Step准入
六文件67通过（7cab6e，13.56秒），静态65e5fb通过，参考commit不变且干净。
进一步增加实际Runtime交接测试：独立取消引擎任务两次，下一次模型采样必须已收到
Script terminated且进程被reap，store不提交、cell移除。第一次夹具误拦截初始化的
非引擎子进程且漏写Model.aclose（a62698），已限定只拦截worker.py并补协议方法；
这次夹具错误不作为生产红绿证据。
最终Runtime/Cell清理两文件12通过（67fcd0，1.38秒），ruff与1190文件格式、diff
检查通过；所有本批测试已退出。上述独立cell取消不会伪造Turn取消，整体Turn可在
收到终止Observation后正常完成；原有整Turn取消测试仍要求取消终态且不继续采样。
