# E4：运行中事件循环的同步构造兼容决策

本轮重新检查当前源代码并独立复现，不靠历史审计推断。
core/construction.py::synchronous_rollback 在无loop时收集closers并asyncio.run回滚；
在已有loop且无_construction owner时直接调用factory。runtime.py在Graph构造前
已创建自有模型并登记aclose，但该分支没有可等待的回滚owner。
create_owned/acreate会捕获构造异常并await _finish_rollback。
Codex session/session.rs::Session::new为异步初始化；行为对齐要求资源有owner，
并不要求Python同步API逐字模仿Rust。

只读故障诊断（c82560，退出0）：临时目录、实际Runtime构造、注入最后Graph
构造ValueError、自建模型aclose记录。sync_in_loop在异常返回时closed=False，
async_owned同样故障closed=True。诊断结束显式关闭模型并清理临时目录，
不创建真实供应商连接、不改变生产代码。该证据仅针对明确的自建模型路径。

保证“异常返回前异步回滚完成”不能用同步函数阻塞已有loop实现；任意插件/宿主
closer可能依赖该loop，不可擅自移到另一个loop。无owner后台cleanup也不满足
交付前清理完成。延迟初始化整套资源会改变更大的启动/错误边界，不能当零影响修复。

建议需用户确认的兼容调整：已有运行中loop、未提供construction owner时，
同步create在分配资源前报明确错误，要求await acreate；无loop同步create不变，
已有owner/acreate路径不变。须同步迁移受影响调用和补准入/零资源分配测试，
不能仅改抛错而留下仓内调用失败。用户确认前不实施此API变更。

本轮仅诊断与记录；15504通过全量基线没有因生产修改失效。该项仍开放，
不能把其它A–E可推进工作一概标阻塞，也不能在用户未同意时宣称已修复。
