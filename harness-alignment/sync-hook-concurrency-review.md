# 同批同步 Hook 并发

基准：Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc，参考工作树干净。
Corki 保留既有未提交改动，本批仅涉及 Stop/SubagentStop 调度和对应测试。

原生 hooks/src/events/stop.rs::run 调用
engine/dispatcher.rs::execute_handlers_with_metadata：本地同步 handler 放入
FuturesUnordered 并发执行，全部完成后按 configured_order 排序；Stop 聚合和
hook_events 使用该顺序。异步 handler 单独交给会话 owner，不参与同步聚合。
这属于普通本地 Harness，不依赖官方模型或账户，也不要求引入 executor 产品。

Corki core/stop_hooks.py::StopHooks.run 已先启动异步命令，但同步命令逐条
claim → run → complete → 汇总。状态：行为不一致。实际影响：前一个命令
等待同批后一个命令时会超时；多个独立命令的耗时相加。

修复计划：同步任务并发执行并分别持久化，全部完成后按配置顺序发布结果和
反馈。保留完整批次恢复预校验；任何存储异常或取消必须取消并等待其他在途
任务清理，不允许孤儿进程或对未知效果自动重试。异步 owner 不改。

验收：真实 Runtime 下 Stop/SubagentStop 两命令互相启动可见、逆序完成但
反馈和生命周期结果仍按配置顺序；停止优先级、冷恢复及取消清理回归。

## 实施与验证

已将同步 claim/run/complete 拆成并发任务，gather 保留配置顺序，全部结果入账后
再发布 HookCompleted 和聚合控制。每条执行身份及原 v1 批次格式不变；恢复时仍
先校验整个批次。异常路径取消并 join 其他任务，重复取消不能打断 join 等待。
没有修改异步并发上限、信任指纹、provider 或模型协议。

真实 Runtime 新测试先出现脚本语法错误（夹具问题，已修正，不作为生产反例）；
修正脚本后，原串行实现两事件均失败：先声明命令超时，完成文件只有 `1` 而不是
`10`（6b07a4，2 failed，4.98 秒）。并发修复后，两命令按 `10` 实际完成，
模型反馈与 HookCompleted 仍按 `01` 顺序，第二步只运行允许结果，不重复反馈。

另验证真实两个在途子进程：关闭 Runtime，或第一条结果入账抛 OSError 时，
另一个等待命令被终止、pid 不再存在；两个 claim 保持未知，无成功通知、无副作用
重试、只一次模型采样，模型最终只关闭一次。关闭 stream 以 CancelledError 结束，
不是成功终态。未知结果的冷恢复仍由既有预校验保护。

旧控制聚合测试曾把实际进程完成顺序也写死（e22e1d：1 failed、50 passed），
改为每批两个命令各执行一次；原有展示、诊断、反馈顺序和 Stop 优先断言全部保留。
扩大同步/异步、来源/插件、冷恢复、Runtime关闭/构造、evaluation、CLI输出与手动
压缩组合 **236 passed，25.20 秒**（982f02）。这包含新并发/故障四项，不是全量。
静态检查通过（d45e9f）：ruff、1170 文件格式、compileall、77 包依赖、diff 检查。
同步 Hook 真实 CLI PTY **24 passed，45.12 秒**（abfe65）；存储批次/执行身份、
CLI活动状态、插件Hook来源等 **72 passed，0.53 秒**（79bd90）。PTY为既有交互
回归，不将其描述为新并发交错的终端专项验证。

## 后续：并发部分入账冷恢复

新增真实 graph/checkpoint → 关闭旧 Runtime → 新 Runtime.resume_pending 的组合：
Stop/SubagentStop × 全部完成/前一未知/后一未知 × 有无配置在首位的尚未 claim 命令，
共12项。两个实际子进程都已产生文件副作用，在 complete 边界挂起；按参数仅持久化
其中一个或两个结果，取消并 join 后冷重开。不伪造执行结果或修改数据库制造窗口。

部分未知时：失败关闭、无新模型采样、无命令重跑，尚未 claim 的首条命令也不启动。
全部已提交时：复用完成结果，仅执行尚未 claim 的命令一次，直接完成 Turn，不重新
采样。两分支原业务历史保持相等、用户输入只有一次、再次 resume 为空。
这补证了整批预校验，不只是单命令账本幂等性。本轮无生产代码修改。

恢复/同步并发/异步/存储组合 **64 passed，9.97 秒**（f5cb26）。随后补强未知分支
无Hook生命周期通知、账本逐行不变及成功分支全批结果入账的断言，专项再次
**12 passed，24 deselected，2.38 秒**（5880d2）；ruff/格式/diff检查通过（b55c79）。
转录、授权 UI、其他事件等完整 A–F 缺口仍开放，不能据此称整体完成。
