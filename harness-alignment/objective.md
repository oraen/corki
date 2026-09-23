/goal

当前目标（2026-09-23）：完善 CLI 的 Shift 等修饰键与终端转义处理，
修复 Shift+Enter 直接发送内容的问题。以本轮系统 goal 的新目标为准。

完成条件：
- 支持 CSI-u/Kitty 和 xterm modifyOtherKeys 的修饰 Enter 编码，Shift+Enter
  插入换行，普通 Enter 保持提交；Alt+Enter、Ctrl+J 的换行行为一致。
- 正确处理分片输入、松键事件、普通控制键和 Shift+Tab；括号粘贴内容
  不作为快捷键执行，原 CPR/颜色响应及常用方向键解析不回退。
- 只在交互输入期间启用键盘报告，正常完成、取消、EOF 后恢复终端模式；
  不修改全局解析表，不影响注入的非 VT 输入输出。
- 普通输入及问答备注支持换行；修饰 Enter 不自动确认审批。
- 用实际按键解析、PromptSession 和 PTY 验证，并运行相关 CLI 回归。
  对物理终端无法区分 Shift+Enter/Enter 的情况如实给出可用替代按键。

本轮集中修复输入问题；此前 A–E 核心目标和全面 CLI 视觉目标保留在
`objective-core-paused-2026-09-21.md`、`objective-cli-paused-2026-09-22.md`，
均不作为这次键盘修复的完成门槛。不回滚已有改动，不修改 teach.md，
不新增官方服务或斜杠命令功能。验收记录见 `cli-keyboard-review.md`。
