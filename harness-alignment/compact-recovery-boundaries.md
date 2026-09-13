# C8 压缩 Hook 恢复的具体边界

## 当前入口归并

| 要求窗口 | 当前证据入口 | 结论边界 |
|---|---|---|
| 摘要读取/关闭取消，工具执行后压缩 | test_local_compaction_inputs 的 cancellation_cannot_be_hidden、summary close failure | 普通模型吞掉取消后返回也不得安装摘要，保留已提交工具事实 |
| 当前与后续输入身份 | test_compaction_input_identity、test_local_compaction_inputs | 不合并内容相同的独立提交，摘要只纳入已接受输入 |
| Pre/PostCompact 安装/执行/receipt | test_compact_hook_install_recovery | install、unknown、completed、success/stopped/invalid receipt 已直接覆盖，最新联合 106 通过 |
| 异步 compact Hook 所有权 | test_async_compact_hooks | 实际进程启动/关闭，控制输出忽略，冷开不复活旧任务 |
| Pre/PostToolUse 投影与 compact 替换 | test_async_hook_checkpoint、test_pre_hook_compaction | 两个实际恢复入口分别在投影后中断/压缩，不重复反馈或副作用 |
| compact SessionStart 义务 | test_start_hook_recovery、test_compact_start_install、session-start-gap.md | 源计划/consumer 绑定、安装失败、新 Turn、待写旧取消终态与冷恢复分别有证据 |

以上归并明确已覆盖窗口；不再把无具体入口的“其余 Hook 交错”作为无限待办。
后续新缺口须指出真实入口与证据，不仅要求所有可能排列。总体 A–E 仍需完成
C1/C2 等独立要求。最新生产全量为 81536，新增测试独立记录，不累加历史数量。

参考 commit ddf04ad26789d040f9ef6a96736f76602e35a6cc。
Codex core/src/compact.rs 先运行 PreCompact，再执行压缩，只有压缩成功才调用
PostCompact；任一 Hook 的 stopped 均以 TurnAborted 控制流结束。此处只对照
核心顺序，不复制远程压缩端点或专属输出协议。

Corki context/window.py 经 graph 回调驱动 compact_hooks.run，按 operation
保存计划与完成回执；恢复先验证 receipt，再检查计划和 claim/result。
执行结果落盘与完成回执是不同窗口：receipt 保存之前需从结果恢复控制语义，
保存之后应复用 receipt，不重新执行命令或对已安装摘要重新采样。

## 本轮新增证据

test_compact_hook_install_recovery 在实际 save_hook_batch 写入 receipt 后注入
OSError，让 graph 尚未继续。PreCompact/PostCompact × 手动/自动 × 配置保持/
移除，共新增 8 例。关闭旧 Runtime 后通过 cold.resume_pending 验证 Hook 调用
一次、摘要一次、自动模式工具副作用一次、已有历史前缀不变、执行记录不变，
再次恢复无新任务。PreCompact 中断时尚无摘要，恢复只生成一次；PostCompact
中断时摘要已安装，恢复不重新生成。沿用离线 hook runner，不能冒充 OS 强杀
或真实命令输出的全链验证；其余真实进程场景由 async_compact_hooks 覆盖。

与 async_compact_hooks 联合 **54 passed / 4.76s**，15846 实际退出 0
（a98a22），4 workers/load/禁重启，当前 compiler 显式配置，ruff check/format
通过。生产未改。原 install/unknown/completed 窗口保持，不以 receipt 替代它们。

## 尚需核定而非笼统追加排列

停止与损坏回执现已补证：Pre/PostCompact × 手动/自动新增 8 例。
stopped 场景由实际 Hook 解析器将 continue:false 写入回执，在写入后注入异常，
冷恢复保持 TurnCancelled 并传播 CancelledError；不重复 Hook/工具，PreCompact
不产生摘要，PostCompact 保留已装摘要。损坏场景在真实持久 receipt 的读取边界
将 stopped 改成字符串，要求 Invalid compaction hook completion receipt，
同样不重复副作用或把坏值当 bool 使用。该损坏注入是读取替身，不声称改写了
磁盘记录；其它数据仍由真实仓库载入。

install-recovery、compact-hooks、async-compact-hooks 三文件 **106 passed /
6.31s**，7718 实际退出 0（257d9d），4 workers/load/禁重启，当前 compiler
显式配置，ruff check/format 通过；没有生产修改。下段“需要映射”保留为发现
时状态，不再把 stopped/布尔类型损坏列为未知，也不扩成无穷损坏值枚举。

本轮 receipt 验证的 stopped=false；stopped=true 回执能否保持取消、损坏回执
是否拒绝恢复，需要先映射现有测试，不能由成功回执推定。Pre/PostToolUse 的
异步投影→checkpoint 更新间被 compact 替换已有专项；SessionStart compact
义务的持久绑定和跨 Turn 消费见 session-start-gap.md，不重复称完全未知。
CLI/PTY 对齐仍暂停，任何平台或故障窗口的结论只覆盖实际执行证据。
