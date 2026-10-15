# CLI 修饰键与转义输入修复（2026-09-23）

当前目标是 Shift+Enter 换行及相关键盘协议处理。之前的 A–E 核心工作和
完整 CLI 视觉对齐保留，不作为本次完成条件。

## 原因与实现

Corki 原只绑定普通 Enter 提交和 Escape+Enter 换行，没有启用键盘增强
报告，也没有在 prompt-toolkit 前解析 CSI-u/modifyOtherKeys。某些终端
因此只报告普通 CR，或把修饰键序列交给不认识该编码的解析器。

Codex 参考为本地 ddf04ad26789d040f9ef6a96736f76602e35a6cc 的
`codex-rs/tui/src/tui/keyboard_modes.rs`：增强报告有启用和恢复生命周期，
tmux xterm 格式存在修饰 Enter 与事件类型的兼容边界。
Corki 不启用全部事件/替代键报告，仅请求基础消歧，并支持 xterm 编码。

- `keyboard.py` 把 CSI-u 和 modifyOtherKeys 的修饰 Enter 解码为 Ctrl+J
  编辑事件；普通 Enter 仍提交。忽略 release，保留 Ctrl/Ctrl+Alt、Shift+Tab
  等可表达的控制键；不把不支持的系统修饰键当作普通提交键。
- `TerminalResponseParser` 以有界 CSI 缓冲处理分片，不更改全局 ANSI 表。
  原方向键、CPR、OSC 和 bracketed paste 继续交给已有解析路径；粘贴中的
  键盘序列保持文本，不触发提交。
- `enhanced_keyboard` 在 before_render 的 raw-input 所有权内启用，finally
  中撤销回调、pop Kitty 状态并关闭 xterm modifyOtherKeys。普通返回、
  EOF、Ctrl+C、异步取消均覆盖；非 VT 输出不写模式控制序列。
- composer、MCP 文本表单、用户问答备注有换行绑定；审批的 Ctrl+J 被忽略，
  不把修饰 Enter 变成接受。默认 Enter 提交、Alt+Enter 和 Ctrl+J 保持可用。

## 验证与边界

首批解析/真实 PromptSession 单位验证 23 passed。新增 PTY 在 40/100 列下
验证 CSI-u、xterm 和 Alt+Enter：Shift+Enter 后未提交、收到完整两行消息、
普通 Enter 才提交，并验证第二次输入的 Ctrl+C/EOF 都恢复键盘模式。
另有任务取消后草稿保留、模式成对恢复的真实 PromptSession 测试。

第一次 CLI+新 PTY 626 passed / 1 failed：旧草稿抢占测试注入的 Session
缺少 app；补齐夹具的非 VT app 后保留原草稿/取消断言。
第二次 CLI+新 PTY+审批+普通 CLI PTY 706 passed / 1 failed：一次旧
补全 Escape→等待→Enter 场景超时；相同四种宽度/退出组合定向复查全部
通过，尚未独立证明该单次时序失败的根因，没有改原超时或断言。
随后新增问答备注修饰 Enter 与取消清理验证，并将协议启用放到 raw-mode
后的 before_render。最终回归结果另记下方，不抹去此前失败。

所有验证离线，未调用模型服务。PTY 验证的是编码和程序行为，不冒充对
所有物理终端的实测。若终端对 Shift+Enter 和 Enter 始终发送相同 CR，
应用无法恢复被终端丢弃的 Shift 信息；README 提供 Alt+Enter、Ctrl+J
及显式 `ESC [ 13 ; 2 u` 映射作为替代。用户的具体终端尚未收到确认。

最终同范围回归：句柄 37857 实际退出 0（532bce），**710 passed、0 skipped**，
166.22 秒，4 workers/loadfile/禁 worker 重启。86 个已有 forkpty 多线程
DeprecationWarning 保留；九个修改 Python 文件 Ruff check/format 及
git diff --check 通过。原单次补全超时未复现，未宣称根因已修复。

```sh
TERM=xterm-256color PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring .venv/bin/pytest tests/unit/cli tests/e2e/test_modified_enter_pty.py tests/e2e/test_approval_pty.py tests/e2e/test_cli_pty.py -n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' -q --tb=short
```

本次修饰 Enter 与终端转义修复的实现、直接行为及相关回归已完成；不以
此结论关闭此前完整 CLI 视觉目标或 A–E 核心目标。
