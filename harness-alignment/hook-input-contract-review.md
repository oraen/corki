# Stop 输入身份与权限模式

基准 Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc。
core/src/hook_runtime.rs::run_turn_stop_hooks 对根/子线程均取 sess.session_id()，
hook_permission_mode 由本轮实际 approval policy 决定：Never 为 bypassPermissions，
其他为 default。hooks/src/events/stop.rs::run 序列化独立 session_id、turn_id、
transcript_path、permission_mode；子线程另有 agent_id 和 agent_transcript_path。
这些是本地命令输入，不是官方账户身份或网络协议。

Corki StopHooks.run 仅子线程读取 repository.load_thread_session_id，并携带权限模式；
根 Stop 使用 thread_id 冒充 session_id，且缺少权限模式和可空转录字段。宿主显式
传入不同 session_id 时会错误关联命令输入。状态：根输入契约不一致。

计划：新批次共用持久化会话身份和权限模式，区分子线程 agent_id。新快照版本与旧
v1 根 payload 分开；旧批次恢复必须保持原执行 request，不重写身份造成重复执行。
可读转录文件仍是独立未实现项，不能把 SQLite 路径写进 JSONL 字段或把 null 称为
转录能力已完成。权限字段描述已有策略，不授予额外权限，也不改变 Hook 独立授权。

## 实施

新 Stop/SubagentStop 批次为 v2：共用持久化 session_id、permission_mode、可空
transcript_path。子线程仍单独传 agent_id/agent_type/agent_transcript_path。
v1 子线程输入保持原契约；v1 根批次恢复保留 thread_id 形式的 session_id 并仍省略
旧版没有的两个字段，原请求字节不改。只对已存在 v1 快照兼容，新批次不使用旧契约。
未知版本继续失败，不修改授权指纹、执行 key 或允许重放未知效果。

新真实命令输入测试在修改前 **2 failed、2 passed，1.05 秒**（dcd0e4）：根路径
两种审批策略都把 thread_id 当成显式 session_id，子路径正确。修改后契约及
恢复/同步并发/异步/evaluation **127 passed，13.93 秒**（c391d2）。
再补旧 v1 显式不同会话 ID 的三窗口恢复，核对冷恢复旧 request_json 完全不变；
输入契约、来源/插件、控制、恢复及存储 **126 passed，20.47 秒**（ef97c4）。
静态通过（cf2464）：ruff、1171 文件格式、compileall、77 包依赖与 diff。

输入专项另扩为根/子事件 × Never/OnRequest × 同步/异步，验证后台实际命令读取
相同身份和策略，不将 TurnCompleted 当作后台命令已经完成。
扩展8项与既有异步13项联合 **21 passed，3.86 秒**（9fc54c），新增测试文件
ruff/格式通过（91c92c）。上述集合重叠，不累加为唯一测试数。
本批修正输入契约，不声称可读转录文件、完整生命周期事件或授权 UI 已完成。
