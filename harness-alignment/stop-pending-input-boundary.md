# A4/E：明确 Stop 与执行期间新输入

最新恢复修复扩大回归：存储全单元、evaluation、所有 Stop 来源及冷恢复联合
200 passed（9af1fd，15.30 秒）。ruff check/format、compileall、uv pip check、
diff check 通过；测试句柄均已终态。具体新增契约与剩余限制见文末。

参考 ddf04ad26789d040f9ef6a96736f76602e35a6cc，源码工作树干净。

原生 core/src/session/turn.rs:547–605：Stop outcomes 汇总后 should_block 可继续，
should_stop 直接 break，不因 hook 期间新输入再采样。tasks/mod.rs::on_task_finished
取走该 Turn pending input，经 run_hooks_and_record_inputs 持久化，再发终态。
因此“停止本次采样循环”和“保留到达的输入”必须分别成立；不能直接丢弃输入，
也不能擅自把它改成未提交草稿返还。

Corki core/stop_hooks.py 将 stopped 与正常允许都返回 False；graph._finalize
只拿 bool 判断继续，随后 pending/finish_if_idle 可重新设置 CONTINUE。
runtime._execute_run 对正常终态原本会 flush 未记录输入，与原生持久化方向一致。

真实 Runtime 探针（40dd68）：实际命令输出 continue:false，在其返回后、Hook
汇总前注入一次 steer；结果 TurnCompleted、模型请求 **2** 次、用户历史两项、
返还输入 0。故证实停止控制被覆盖，而不是仅猜测 bool 风险。

修复方案：服务返回明确 Allow/Block/Stop 三态；graph 遇 Stop 关闭准入边界，
跳过 pending 驱动的继续判断，仍走正常完成/持久化清理。保留真正取消控制流。
账本、执行 key、用户记录及旧 checkpoint 不引入新字段。无 stop 时保留既有
新输入继续行为。验收扩展真实多 handler 测试：两种 stop/block 顺序及全 block，
各有/无执行期间新输入；断言采样/命令次数、反馈、用户历史及清理。

属于 A4 主循环高优先级差异，不是 CLI 样式，不是官方专用功能。

## 实施与证据

服务现返回 StopDecision.ALLOW/BLOCK/STOP；graph._finalize 只对 BLOCK 走
继续判断，STOP 关闭输入准入、不用 pending 再开一轮，保留 stop_requested 的
取消语义。原有 Runtime 正常结束 flush 接收的用户输入，未修改持久化结构。

test_stop_hook_control 扩展真实命令返回之后注入 steer：两种 stop/block 顺序
在修改前 2 failed/4 passed（c2b09a）；修复后与 input boundary、Stop checkpoint
恢复、evaluation 联合 78 passed（9739c1，8.46 秒）。随后加入显式最后允许
Step 对照（stop 上限 1、全 block 上限 2）与无限制对照，来源/恢复组合另验。
这组证明配置 Stop 的当前边界，不关闭异步/non-root 等其他 A4 缺口。

最终所有 Stop 集成来源（TOML/JSON/plugin/reload/recovery/control）、evaluation
与 default_loop_limits 联合 114 passed（866169，18.66 秒），含 12 个控制矩阵
场景。ruff check/format、compileall、uv pip check、diff check 通过。
没有新增网络服务或官方协议依赖，未改 teach.md；本轮测试句柄均已终态。

## 新发现的恢复边界（实施前）

StopHooks.run 的 pending input 前置判断在读取账本前就返回 ALLOW，因此冷恢复
若含未被当前 request 消费的新输入，会绕过已存在的执行 claim。新增真实 checkpoint
测试命中两个窗口：已完成 Stop 被绕过导致第二次采样；未知结果被绕过，原本应
失败却完成（900e4a，2 failed/2 passed）。这不是上一批实时 Stop 三态修复已覆盖的路径。

修复需区分“尚未开始的本 Step hook”与“已 claim 的本 Step hook”：前者可让新
输入先执行；后者必须进入原身份校验/缓存复用/unknown 拒绝路径。增加按 thread、
turn、Step prefix 限定的只读存在检查，不通过再次 claim 来查询，不先运行命令；
授权与原请求一致性仍由既有 discover/claim 负责。本批先验证配置不变的恢复，
定义删除/更换、完整批次快照恢复等不能由此推断。

### 恢复修复进展

SessionRepositoryProtocol 新增 has_hook_executions(thread, turn, prefix)；SQLite
以参数化 thread/turn/字面前缀 SELECT 1 查询既有 hook_executions，Volatile 继承。
没有新表或数据迁移，也不改变旧执行 key；查询连接操作等待完成后才传播取消。
外部自定义 repository 若承载此 Hook 恢复路径，需要实现该只读契约。

StopHooks 仅在存在新输入时查询：没有本 Step claim 才 ALLOW 延后；已有 claim
进入原 claim 校验，completed 复用、unfinished 失败。并不绕过 hash 用户批准或
request identity 检查。两冷反例修复后与原 cold/control/input boundary 联合
54 passed（fd4560，8.92 秒）。存储测试补两 backend 的查询无副作用、不同
thread/turn/prefix 隔离、% 非通配及 completed/reopen 存在检查。

此修复只核销“配置不变 + 本 Step 已有 claim + 晚到持久输入”的恢复边界；
不能据此声称配置在崩溃间隙变化时整批 hook snapshot 已持久化，后者仍开放。
