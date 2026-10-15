# 终端主题采样与输入所有权

## 有界颜色采样已接入，显示消费者仍待接入

`cli/terminal_palette.py::TerminalPalette` 为每个 TerminalUI 保存一次采样状态，
composer 和表单共享该状态。两应用的 before_render 在 PTK 的 raw_mode 与
input.attach 作用域内触发；仅真实 VT 输入且输入/输出均为 TTY 时查询。
通过 `app.create_background_task` 发送 OSC10/11，100ms 共同截止；继续复用
现有 reader，回复通过临时 listener 通知任务，不增加终端读取者或轮询线程。
成功需要本次采样的两个槽，不采用此前 parser 缓存的零散/过期回复。

未知/缺一个槽/IO错误保持 None 与暗色默认，attempted 保证后续提示不会再次
查询。应用结束会取消并 join 后台任务；finally 移除 listener，不吞 CancelledError。
迟到回复仍由 framing 消费，但不改写已结束的采样快照。亮度判定使用原生公式。
本实现在第一个活跃输入应用首次绘制时启动，不另设阻塞的预启动 stdin reader；
若该应用提前退出，本次采样随其取消，不让可选显示探测延长输入任务生命周期。
因此不能将本批称为原生所有启动时序/终端探测项目均已复刻。

验证：完整CLI单位及新增实际TerminalUI启动PTY联合509 passed / 16.11秒
（2401eb，session 55639终止0）：浅色、深色、无回复、仅前景四种PTY场景；
真实终端收到查询后回复，与draft混排，超时后输入仍可提交，第二次提示不查询，
历史恰为两个用户提交。单位另覆盖迟到、写失败、取消和listener回收。
四条Python forkpty多线程弃用警告保留，本次没有死锁。ruff初次仅测试文件
导入分隔格式失败，机械修正后检查通过（760abe，1189文件）；compileall与
77包依赖检查通过（cce0ea）。新增主动查询后再跑实际审批/长详情PTY：
22 passed / 46.33秒（65b596，session 85462终止0），包括40/100列显式
决定、详情返回与恢复composer，不因新查询改变审批结果；最终diff check通过。

尚未将该快照接入syntax/diff/gutter和缓存，尚未验证主题逐格帧、Windows原生
输入/终端色深及自定义scope。下一步必须接入显示消费者；不将“成功采样颜色”
冒充已完成浅色显示。下方保留之前的分阶段记录，状态以上方最新结果为准。

## 输入 framing 已接入，主题采样尚未接入

新增 `cli/terminal_responses.py`，由 `TerminalUI` 的 composer/表单输入和独立
`choose_approval` 入口安装到现有 VT 输入解析器之前。重复安装返回同一对象，
不创建第二个 reader，不修改全局键表；非 VT 宿主输入不修改。颜色状态跟随
输入对象，仅保存 10/11 两槽，不更改草稿、历史、审批权限或模型请求。

实现支持 BEL/ST、RGB/RGBA、分片回复与有限长度 framing，OSC 外部控制文本
不成为审批快捷键；未完成回复不在 parser.flush 时变成 Esc。超过 1024 字符
的普通 OSC 保持丢弃状态直到终止符，正文中的 `1`/CR 不泄漏为批准；括号粘贴
内的无效/无关/超长 OSC 样文本保留，有效颜色回包过滤。新的 escape 序列可
重新同步，粘贴结束标记不被吞掉，未完成普通 OSC 后 Ctrl+C/Ctrl+D 仍可取消。
正常文本按块转交原解析器，避免将大段粘贴逐字符拼接。

验证证据：

- 真实 PromptSession 六个反例修复前全失败（4693cd）：BEL/ST 颜色回复单独
  使审批返回 cancel。修复后等待真正的批准/拒绝/Ctrl+C，再得对应结果。
- 完整 CLI 单元 498 passed / 8.99 秒（8c69ef），包括 framing 分块 1/2/7/4096、
  CPR/方向键/Unicode/粘贴/超长输入及安装幂等，原输入与审批测试继续通过。
- 真实 PTY 两宽度 40/100 列 × 七种批准/拒绝/取消按键，加入分片回包并断言
  无决策，再显式决定；恢复 composer 后正文中插入颜色回包，最终输入和历史
  仍仅为 ordinary followup。14 passed / 28.46 秒（0fd1bc，session 4222 终止0）。
- ruff check、1186 文件格式检查、compileall、77 包依赖检查和 diff check
  通过（aa1525）。

这一批关闭已复现的 VT 回包误取消边界，不关闭整个 F3/F6。仍未主动发送查询，
未接入100ms启动采样/回退缓存、浅色高亮与 diff/gutter、自定义主题和平台色深。
原生 Windows 输入、完整启动/关闭交错和主题截图仍需独立证据。下文保留实施前
调用链与复现记录；“没有回包边界”描述仅代表本批修复前的状态。

## 当前结论

F3/F6 **缺失**：补丁详情已有深色回退和有效色深桥接，但没有终端默认颜色采样，
没有浅色 diff/行号栏，也没有随背景选择语法主题。不能用设置一个 light 参数或
读取 COLORFGBG 替代原生调用链。参考 commit 为
`ddf04ad26789d040f9ef6a96736f76602e35a6cc`，本轮复核参考仓库无未提交变更。
本文不更改目标范围，不涉及模型协议、官方服务或通用 MCP OAuth。

## Codex 的实际路径

源码均相对于参考仓库 `codex-rs/tui/src/`：

- `tui.rs::init` 先启用终端模式，再在普通事件读取启动前调用
  `terminal_probe::startup(DEFAULT_TIMEOUT, ...)`，将结果写入
  `terminal_palette::set_default_colors_from_startup_probe`。默认预算 100 毫秒，
  不是每次绘制重新等待终端。失败有明确回退，初始化有恢复 guard。
- `terminal_probe.rs::startup/read_startup_probe` 同组发出 CPR、OSC 10/11 和
  可选键盘探测，使用共同 deadline，启动读取上限 64 KiB。
  `parse_default_colors` 要求前景和背景都有效；RGB/RGBA 支持 1–4 位十六进制
  分量，按各自满量程缩放至 8 bit；接受 BEL 或 ESC-backslash 终止符。
- 启动读取后通过 `terminal_probe/startup_replay.rs::startup_replay_input`
  与 `crossterm::event::buffer_input` 回放用户输入，不把探测窗口读到的所有字节
  丢弃。普通 OSC、有效颜色回包、粘贴内容、未完成序列有不同处理；其中粘贴内
  有效颜色回包也会过滤。不能简化成正则删除所有粘贴内转义文本。
- `terminal_palette.rs` 缓存 attempted 与 value；无响应也记为已尝试。
  `diff_render.rs::current_diff_render_style_context` 每次渲染快照读取缓存与主题，
  不等于每帧进行 I/O。`color.rs::is_light` 使用
  `0.299*r + 0.587*g + 0.114*b > 128`。
- `render/highlight.rs::adaptive_default_theme_selection` 默认 Catppuccin Latte
  或 Mocha；自定义 `.tmTheme` 和主题 scope 背景有独立覆盖逻辑。
  `resolve_diff_backgrounds_for` 从回退色开始，再应用 inserted/deleted scope。
- 浅色 truecolor 行背景增/删为 `#dafbe1`/`#ffebe9`，行号背景
  `#aceebb`/`#ffcecb`，行号前景 `#1f2328`；256 色依次为
  194/224、157/217、236。ANSI16 不使用背景。Windows Terminal 色深提升
  还受 FORCE_COLOR 显式覆盖约束，不能简单无条件提升。

原生启动 probe 与独立 `default_colors` helper 的输入保留行为不同：后者
`read_until` 不回放输入，源码要求独占时段；Corki 不能在正在运行的 composer
或审批中照搬该 helper 另开 stdin 读取者。

## Corki 的实际路径与复现

`TerminalUI` 创建普通输入与表单 PromptSession；`read_elicitation` →
`choose_approval` → `ApprovalDetails` → `request_details/render_changes` →
`wrap_preview`。当前有效色深来自 `session.app.color_depth`，分页缓存仅含宽度/色深；
`syntax.highlight_code` 固定默认 monokai；`wrap_preview` 固定深色背景。

安装环境中的 prompt-toolkit `input/vt100.py::read_keys` 直接把读取文本交给
`input/vt100_parser.py::Vt100Parser.feed`。该解析器识别 CPR、鼠标、键盘和粘贴，
没有 OSC 10/11 回包边界。独立诊断实际输出（8cfead）：

```text
输入：draft + ESC ]11;rgb:ffff/ffff/ffff BEL
普通字符：draft]11;rgb:ffff/ffff/ffff
控制键：Escape, ControlG
```

ESC-backslash 终止形式同样产生 Escape 和普通字符。进一步用真实
`choose_approval(session, execution=True)`、`create_pipe_input`、DummyOutput，
只发送上述 BEL 终止回包，不发送任何用户决策，实际返回 **cancel**（d05af9）。
这不是当前 Corki 已主动查询终端的证据，而是直接添加查询会破坏审批的实证：
审批把 Escape 绑定为立即取消，颜色回包必须先在输入层消费。

本轮相关已有测试 39 passed / 0.87 秒（d8c83a，session 66486 终止、exit 0）：
`tests/unit/cli/test_patch_syntax.py` 与 `test_approval_details.py`。
这些测试只证明现有深色回退与分页仍可用，未覆盖自动主题探测；不能据此关闭 F。

## 实施顺序与验收门槛

1. 先接入 CLI 输入所有者的终端回包 framing，再发送任何颜色查询。必须只有一个
   活跃输入读取者，覆盖普通 composer 与表单切换。保持已有 CPR、Esc、多行、
   Unicode、括号粘贴及 typeahead；分片/延迟/畸形/超长回包有有界处理。
   不全局修改 prompt-toolkit 的键表，不在绘制函数读取 stdin，不把回包持久化。
2. 在真实交互启动路径做一次有界采样；缺一颜色、超时或不可用时缓存未知并走
   深色默认，不让每次审批重试采样。控制终端模式、取消、关闭和重定向输出的所有权。
   输入适配与色彩状态需要明确会话寿命，不用模块全局状态串到其他宿主会话。
3. 将同一颜色快照接入语法高亮、diff body/gutter 与分页缓存失效；只改 diff 背景
   会让深色语法前景落到浅色背景，仍不算完成。自定义主题/scope、主题切换与跨平台
   色深单独追踪，不能用 Pygments 任意浅色主题声称已逐格对齐。
4. 验证真实 PromptSession 中颜色回包不会提交/取消审批，不进入草稿/历史；随后
   显式批准、拒绝、取消仍各返回正确结果。覆盖回包与粘贴、输入、切换、关闭交错。
5. 真实 PTY 模拟 OSC 回复和无回复，验证启动等待有界、40/100 列浅/深色显示、
   续行/行号背景、色深回退及输入保留。不得只给 renderer 人工注入白色就声称
   端到端自动探测通过。

本轮完成调用链审计与风险复现，尚未修改生产实现；下一步是输入 framing 与
启动采样的联动实现，不是继续调整固定深色色值。A–F 总目标保持未完成。
