# D1：历史、模型窗口与运行中工作状态

本表归并已读源码与直接行为证据，不把所有状态都当作“长期记忆”，也不把归档
中的标识当作运行中资源授权。参考为本地固定 Codex commit ddf04ad26789d040f9ef6a96736f76602e35a6cc。

| 状态 | 同 Runtime 跨 Turn / 压缩 | fork / 冷启动 | 证据 |
|---|---|---|---|
| 计划调用及结果 | 原归档保留；压缩窗口只保留摘要等选中历史，不重发旧事件 | 复制历史不是重新执行计划；新分支更新不改源记录 | plan-event-boundary.md；test_thread_fork_plan |
| Code Mode store | 已提交值在运行中会话保留；压缩不清空 | 新 Runtime 空 store，不从旧脚本或结果重建 | code-mode-acceptance.md；test_code_mode_store_scope |
| 外部终端进程 | 存活进程属于原 ProcessManager，可跨 Turn 写入 | 复制旧 session ID 不接管进程；冷关闭后不重建进程 | 本节；test_process_session_scope |
| 已发现工具定义 | 从活动窗口的成功搜索结果加载；压缩释放后需重搜 | 历史中的定义须与当前目录匹配；变化/删除不能凭旧结果执行 | tool-search-ranking-review.md；test_thread_fork_discovery |

## 终端进程身份：本轮源码与实证

原生 session/session.rs:1415 每个 Session 构造 UnifiedExecProcessManager；
unified_exec/mod.rs:149–181 使用空 ProcessStore，write_stdin 在
process_manager.rs:813–829 查询本 manager 的 processes，缺失即 UnknownProcessId。
Corki runtime.py:906 构造 ProcessManager；process.py:506 起只查本实例 _sessions，
缺失返回普通工具错误，不根据持久历史生成进程或转发给别的 Runtime。

新增 test_process_session_scope：真实 exec_command 启动等待输入的终端，保留
结果及 session ID。fork 复制历史后 write_stdin 被拒绝、目标没有进程、源归档
不变且源进程仍活着，随后原 Runtime 写入可完成。另一路关闭源并冷启动，确认
旧子进程已退出、旧 ID 被拒绝、原历史前缀不变。两路均未重新执行 exec_command，
模型请求数精确为 6 / 4。只操作测试创建的进程，finally 关闭各自 owner。

首批8通过（4531fd，27497退出0）。扩大 process_session_scope、process_retention、
process_startup_ownership、thread_fork_discovery、code_mode_store_scope、
thread_fork_plan、thread_fork_compaction 共39 passed，12.66秒（d40b4d，59816
实际退出0）；4 workers/loadfile/禁重启，null keyring、当前 sandbox compiler。
Ruff/格式/diff check通过，无生产变更；15450全量早于最近三项新增组合测试。

## 验收边界

D1 以上可枚举工作状态的保存/隔离/冷启动规则已有直接证据，不再笼统列为未知。
不保证任意外部副作用 exactly-once，不让冷启动恢复活进程或未提交 cell。
模型/Step 配置快照归 A1/C2，Hook 控制与执行账本归 A7/C8/E7，长期记忆发布归
D2–D6；这些项目仍按主清单独立验收，本表不替代其开放项，也不恢复 CLI 对齐。
