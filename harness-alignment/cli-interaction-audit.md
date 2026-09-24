# CLI 交互对齐审计

## 2026-09-23：图片与正文的位置关系

本节取代下节的独立附件行和开头 Backspace 删除最后一张方案。
核对 Codex `chat_composer/attachment_state.rs::attach_image`、
`chatwidget/input_submission.rs` 和 `protocol/src/models.rs`：编辑器在光标处
插入原子 `[Image #N]` 元素；实际模型请求仍先发送编号图片块，再发送含占位符
的完整正文，并非将正文物理拆成 text/image/text。

Corki 改为跟踪真实标记的位置和附件身份，不将手输的同名文字识别为附件。
光标跳过标记，Backspace/Delete 原子删除，删除后仅重排真实标记的编号。
撤销同时恢复正文与附件，最多 64 条、累计 100 万文本字符及 4400 万字符
唯一图片 data URL；提交/清空/退出释放草稿撤销引用。粘贴进行中继续输入时
保留原插入锚点，读取期间禁止提交和取回队列，避免异步操作错配。
普通提交、实时 steer、排队/取回、未消费输入返回及会话持久化携带位置元数据。
按 Python Unicode 字符偏移保存，中文和前后空白不改写；不照搬 Rust UTF-8
字节偏移。模型内容采用同号 `<image name=[Image #N]>` 包装，不虚构文件路径。
文本压缩保留原正文并清除失效附件位置，不把图片包装标签混入用户文字。

验证：CLI/realtime/context/models/storage 单测、图片发送/准备/steering 集成及
40/100 列图片 PTY 合计 1758 passed，0 skipped；4 条既有 forkpty 警告。
含粘贴期间中文输入、原子删除/撤销、伪标记、编号顺序、两种模型协议请求、
执行中追加图片及冷恢复位置断言。未访问用户系统剪贴板或真实模型服务。

## 2026-09-23：Ctrl+V 图片输入全链路

参考 Codex `chatwidget/interaction.rs` 的 Ctrl+V 图片分支、`clipboard_paste.rs`
文件优先/像素回退/PNG 编码和 composer `attachment_state.rs` 的附件独立管理。
Corki 采用本地受管 Python/Pillow helper；macOS 先读 Finder 文件 URL，
再读取剪贴板像素。5 秒总超时、进程组回收、单图 20 MiB/3200 万像素限制，
最多 8 张，消息附件 data URL 总长度限制 4400 万字符。失败只显示安全提示。
不落临时图片文件：使用现有 ImageAttachment 数据合同与媒体校验/编码流程，
持久化不依赖临时路径，不新增官方服务或依赖包。远程 SSH 不读取服务器剪贴板。

Ctrl+V 后显示 [Image #N]，支持无文字提交、重复粘贴、光标在开头 Backspace
删除最后一张。读取期间阻止 Enter/Tab 提前提交；Ctrl+C 清草稿及图片，
模态抢占/任务结束会取消并等待读取子任务，已有附件随未提交草稿保留。
Slash 命令保留待发附件。普通提交、实时 steer、Tab 排队、关口关闭转下轮、
未消费输入返回及队列编辑传递同一附件对象，而不是丢掉附件的字符串。
会话关掉再打开仍从持久化用户内容中恢复图片；底层禁用图像时不允许粘贴。
本地 CLI 采用独立附件行而非 Codex 的行内原子占位符；不声称 UI 内部实现完全一致。

验证：CLI/realtime/models/image/owned-process 单测、图片持久化和 steering 集成，
40/100 列图片/队列/审批覆盖 PTY 联合最终 1217 passed，0 skipped。
测试不读取或修改用户系统剪贴板；另用独立 NSPasteboard 验证真实 macOS
Finder URL 桥接（中文/空格路径），并修正必须使用原生 NSArray 的细节。
helper 以 Python -I 启动，避免工作目录同名模块劫持；实际截图粘贴仍需用户体验确认。

## 2026-09-23：/model 分层选择交互

参考 Codex `chatwidget/model_popups.rs` 的模型/普通等级/高级等级分层，
当前值与默认值分开标记；新模型初始高亮自己的默认等级，不沿用旧模型等级。
模型页保留供应商配置列表及自定义输入，不把 bundled metadata 当可用模型目录。
等级页采用编号选择、方向键、Enter 确认，移除无意义的 Filter 输入框；
附通用等级说明（当前元数据没有描述字段，不伪造供应商描述）。Max/Ultra
只在 More reasoning… 子页选择；单一普通等级直接采用。
Esc 逐级返回，Ctrl+C/Ctrl+D 取消整个选择；只有最后接受才返回设置变更。
菜单使用瞬态擦除、窄屏单行截断和可视范围滚动。共享会话的只读状态、
擦除策略、文本监听在退出/取消/审批覆盖时恢复，保留已有输入所有权机制。
不复制官方 Auto 模型、账号套餐提示及账户服务入口。
CLI 全量及模型/审批覆盖/copy PTY 共 783 passed，0 skipped。
补充高级页 Esc 返回 PTY 4 passed；菜单快捷键/会话恢复/流程专项 15 passed。

## 2026-09-23：底栏模型名附带推理等级

参考 Codex `status_surfaces.rs::model_with_reasoning_display_name` 与
`status_controls.rs::status_line_reasoning_effort_label`，显示模型名和选中等级；
未选时使用模型元数据默认等级，无默认值或 none 显示 default。
复用已有设置快照刷新，不更改请求参数或新增官方账户逻辑。
保留黄色模型/等级、绿色路径；短路径让出可用宽度，避免还有空间却截断等级。
等级指会话选择，不声称每个第三方供应商都接受对应请求参数。

## 2026-09-23：输入区域背景与留白

参考 Codex `chat_composer.rs` 的整块背景和 `style.rs::user_message_bg_rgb`：
输入框上方一行无背景间距，上下各一行背景内边距；仅 buffer Window 铺底，
覆盖中文、显式换行、自动折行与行尾空白，不染色底栏和补全菜单。
复用现有终端色探测：深色向白色混合 12%，浅色向黑色混合 4%；
未知背景不猜测，NO_COLOR 继续由现有输出策略处理。仅补全打开时预留
菜单高度，避免普通输入框因原默认 8 行保留空间变得过厚。
新增深/浅色 × 40/100 列真实 PTY 测试，逐格验证背景、间距与多行提交。

## 2026-09-23：修正完成分隔线的显示条件

本节取代下节“每次成功完成都显示”的策略。重新参考 Codex
`chatwidget/replay.rs::prepare_assistant_message`、`turn_runtime.rs` 和
`history_cell/separators.rs`：纯聊天/思考不算工作；工具完成后挂起分隔线，
下一段回答开始前输出无耗时横线并消费标记，完成时不重复追加。
只有仍有未分隔的工作时，完成阶段才输出完成线；整数秒 > 60 才带
Worked for，60 秒及以下仅横线。计划更新、询问用户不计为实际工具工作。
流式正文、整段正文及 TurnCompleted 兜底答案使用相同条件。
沿用现有暂停感知计时与回放宽度适配，无新增后台任务。
边界：未移植 Codex 的 runtime_metrics 独立统计行，Corki 当前无对应的
完整指标事件；不伪造服务端计时或推理/WebSocket 统计。
验证：CLI 全量单测及 40/100 列三轮 composer PTY 756 passed；随后补充
流式正文路径，分隔线专项 78 passed。Ruff 与 git diff --check 通过。

## 2026-09-23：完成耗时分隔线

参考 Codex `history_cell/separators.rs` 的淡色横线及宽度处理、
`chatwidget/turn_runtime.rs` 的完成阶段输出。用户要求回答结束后显示，
Corki 每次成功 TurnCompleted 输出一次 Worked for；不照搬该版本 Codex
仅工作活动且超过 60 秒显示耗时文字的条件。失败/中断不冒充完成。
使用已有 WorkingStatus 暂停感知的单调耗时，无新增计时任务；显示历史
记录秒数，重放按当前宽度生成横线。极窄屏省略 Worked for，优先保留时长。
当前无协议 duration 字段，使用本次运行 UI 计时，不声称冷恢复累计耗时。

CLI 714 passed，完成/Working/copy PTY 8 passed。首次回归更新了原本只含
两条回答的历史断言（现在多一条耗时）；一项 Escape 时序测试重跑通过。
PTY 过滤 pyte 不支持的输入协议开关，防止模拟器将 CSI <u 误绘为字母 u；
保留屏幕行宽、三轮仅一条完成线/输入框及正文保留断言。Ruff/diff 检查通过。

## 2026-09-23：回答结束后的重复占位输入框

新增真实 Runtime/PTY 三轮测试，在 40/100 列均复现两个
`Ask Corki to do anything`。根因：实时 composer 在回合结束时被取消，
PromptSession 默认以 done 状态将未提交的输入框写进 scrollback。
改为 composer 默认 erase_when_done=True；仅在 Buffer 的验证通过接收回调
中为非空真实提交关闭擦除，每次 read_message 重置。取消、模式切换、空 Enter
不留下占位/草稿副本；提交消息仍只显示一次，草稿继续由原取消处理保存。
没有新增 reader、后台任务或定时器，也没有通过清空正文来掩盖残影。
专项三轮 PTY 同时检查屏幕及 scrollback 仅一个占位框；Working PTY 追加
取消旧 reader 后恢复草稿只显示一份的断言。
最终 CLI 单测及 composer/Working/copy/修饰 Enter/历史/执行/审批 PTY 联合
738 passed、0 skipped，84.28 秒；70 条既有 forkpty 弃用警告，无测试失败。

## 2026-09-23：底栏颜色改为 Codex 默认主题色

不再使用 ANSI yellow/green。核对 Codex `render/highlight.rs` 的 Mocha/Latte
默认主题、`status_line_style.rs` 的 85% 饱和度整数算法，并通过 agent-reach
读取 catppuccin/palette 与 catppuccin/bat 的原始 type/string 色值。
深色最终模型 #f6e2b7、路径 #abdfa7；浅色 #d59030、#489a36。
复用现有 TerminalPalette 背景探测，不新增查询或定时器。
iTerm composer 使用 RGB 色深，仍优先遵守 NO_COLOR 和显式色深设置。
CLI 单测和 Working PTY 684 passed；PTY 按实际终端字符验证 RGB 色值。

## 2026-09-23：底栏模型/目录与 reasoning effort 核查

参考 Codex `bottom_pane/status_line_style.rs`、`chatwidget/status_surfaces.rs`：
独立的模型与目录状态行，使用 ` · ` 分隔。Codex 按主题决定模型色，
Corki 按本次用户明确要求使用黄色模型名、绿色目录。底栏在工作中保留，
模型切换时读最新 settings；家目录缩写为 ~，窄屏按单元格裁剪并保留路径尾部。
原 Working 计时和模式/历史提示不被替换。遵循 NO_COLOR，不强制覆盖终端偏好。

reasoning_effort 不是空壳：Responses 适配器生成 reasoning.effort；
Chat Completions 适配器仅在 supports_reasoning_effort 为真时生成该字段。
默认通用 Chat Completions capability 为 false，不能宣称对所有供应商生效。
请求级覆盖、显式清空、不支持时省略均有单测；模型/effort 成对切换有 PTY 测试。
因此未触发用户“没有用途则删除”的条件，本轮不删除功能或配置。

首轮 759 passed / 1 failed：copy PTY 把回答后的活动 composer 当成回合完成，
现改为显式 TURN_SETTLED 信号后验证空闲 /copy；运行中本地命令路由仍单独覆盖。
底栏 PTY 追加屏幕位置及实际黄/绿字符色断言，测试显式移除继承的 NO_COLOR。
最终联合回归 760 passed、0 skipped，20.12 秒；Ruff 与 diff 检查通过。

## 2026-09-23：用户明确追加 /copy

参考 Codex `chatwidget/interaction.rs::show_copy_picker`、
`slash_dispatch.rs` 和 `clipboard_copy.rs`：提供最近已完成回答的全文、
代码块及引用选择面板。补全、帮助和空闲/运行中命令分派均已接入；
命令不发给模型，工具输出、错误提示、reasoning 不作为复制源。
复用 InputOwner 模态与审批覆盖机制；取消/失败释放面板、输入锁和暂停状态。

macOS 使用 pbcopy，Windows 使用固定 PowerShell 命令，内容走 UTF-8 stdin，
没有 shell 插值；子进程复用 run_owned 的超时、取消和 join 机制。
SSH 优先 OSC 52；其他平台或原生后端失败时可回退到终端协议，tmux 包装透传。
OSC 52 无确认回执，只提示已发送请求，不声称系统剪贴板已更新。
与 Codex 的差异：全文复制 Markdown 原文，不提供额外 HTML MIME；
Linux 当前使用终端协议而非持有原生剪贴板后台进程。

联合回归 687 passed、0 skipped（CLI 单测、owned helper、copy PTY、
model/approval PTY）；剪贴板外部写入全部替换为测试后端，没有改用户剪贴板。

## 2026-09-23：项目标题、工具预览、Ctrl 光标移动

逐项参考本地 Codex：

- `terminal_title.rs` 与 `chatwidget/status_surfaces.rs`：默认标题包含
  activity/thread-name/project-name。本轮按用户要求只设置项目目录名，
  TTY 才写 OSC 0；过滤控制/格式字符，折叠空白，最多 240 字符；生命周期
  finally 清空标题，不声称恢复无法可靠读取的原始 shell 标题。
- `exec_cell/render.rs`：TOOL_CALL_MAX_LINES=5，先按屏幕宽度折行，保留
  头尾并提示省略。Corki 工具标题限制两行、输出预览限制五行；流式数据按
  call_id 共用预算，只额外保留三行尾部，完成/中断时冲刷并删除该状态。
  Ctrl+T 完整历史单独启用 expand_tools，普通重绘继续使用紧凑预览；
  重放不会修改运行中的预览状态，也不修改模型工具结果或持久化内容。
  与 Codex 的差异：Corki 当前是追加式输出，尾部在调用结束/中断时提交，
  没有引入原地重绘的执行卡片。
- `keymap.rs` Editor 默认映射：明确绑定 Ctrl+A/E 行首/尾、Ctrl+B/F
  左右字符、Ctrl+左右及 Alt+B/F 按词移动；覆盖多行草稿及 CSI-u Ctrl+A。

验证：CLI 单测首轮 654 passed；专项追加并发工具正常/取消回收检查后
15 passed；Working/执行生命周期/历史/修饰 Enter/启动取消 PTY 50 passed。
标题另加真实 PTY OSC 设置/清空断言。Ruff 和 diff whitespace 检查通过。

## 2026-09-23：Esc 中断 Working

参照 Codex `bottom_pane/mod.rs::should_interrupt_running_task`：仅在工作进行中、
没有模态/补全/历史视图时中断；保留草稿，Esc 不退出整个应用。
Corki 通过现有 InputOwner → InputInterrupted → cancel_active → consumer join
流程实现，不创建脱离所有者的取消任务。非 eager 绑定保留 Alt+Enter/Alt+Up；
历史和补全仍优先消耗 Esc，空闲 Esc 不退出。Working 提示改为 `esc to interrupt`。

回收边界必须与后台会话所有权区分：Codex
`unified_exec/process_manager.rs` 在首次等待前 store_process，明确保留已登记
的后台进程；Corki 当前 ProcessManager 同样如此。本轮不修改这一核心语义。
旧 PTY 中“中断后立即杀进程”的断言与当前实现不符，Ctrl+C 和 Esc 均能复现；
改为检查受管理会话仍有所有者，并在应用退出后检查 PID 消失。
同时检查 reader、Working 刷新句柄、审批 pending、realtime tasks 在对应
生命周期边界清空，覆盖后续 Turn、冷启动历史、终端模式恢复。

验证：CLI 全单测及 execution lifecycle / Working / modified Enter / history /
startup cancel PTY 联合 **691 passed、0 skipped**（82.80 秒，4 workers/loadfile）；
Ruff check、format check 与 git diff --check 通过。PTY 测试产生 62 条 Python
forkpty 多线程弃用警告，无测试失败。本轮没有做长期内存压力测试，不把
所有权和退出检查表述为对所有泄漏的绝对保证。

## 2026-09-23：Working 状态、布局与耗时

用户再次明确要求参考 Codex 的执行页面，改善不显眼且无计时的 Working。
重新核对 `status_indicator_widget.rs`、`status_indicator_widget/timer.rs` 和
`bottom_pane/mod.rs` 的计时/模态暂停：状态行在 composer 上方，计时独立于
正文/工具详情，使用单调时钟并在审批等模态等待时暂停。

Corki 新增 `working_status.py`：加粗 cyan 的 Working 与活动指示、独立高亮
耗时 `0s / 1m 01s / 1h 00m 00s`，有可用宽度时显示 Ctrl+C 提示。非空草稿
时提示清除草稿，避免误称该键会立即中断。工具、reasoning、Hook 详情放在
下一行，过长或中文按终端单元格裁剪，优先保留计时；活动区域移至输入框
上方，空闲后恢复原底部提示。状态不写进对话历史。

同 Turn 重复激活或切换工具不会归零；`Transcript.modal_depth` 的嵌套变化
控制暂停/恢复，Turn 正常完成、异常、取消都由 `_render_events` 的 finally
结束计时和取消刷新句柄。真实终端四次/秒活动刷新，非动画路径每秒刷新。
这是本轮工作耗时，包含模型及工具执行，不声称能测量供应商内部纯推理时间。

专项 15 passed（c2a5b8），包含确定性时钟、分钟/小时及窄屏中文、工具切换、
嵌套模态暂停、新 Turn 归零、正常/失败/取消收束；40/100 列真实 Runtime PTY
验证安静模型/长工具中计时自行递增，屏幕上 Working 加粗且位于 composer
上方，完成后原屏幕不残留。首轮未设 TERM 的旧 reasoning 样式用例失败，
后续回归显式使用 TERM=xterm-256color；不将该批称为全绿。
扩大首轮 672 passed / 3 failed：一项只使用 TerminalUI 输出功能的旧夹具
没有计时器，已保留无交互 UI 的兼容；两个真实审批取消场景暴露普通 Enter
被变成 LF。单独串行同样失败，PTY 诊断确认下一草稿实际为 `wait\n`，
输入发生在旧 composer 退出与新 composer 接管之间的 cooked-mode 间隙。
CLI 现在从第一次读取开始持有 raw input 模式，跨面板/Turn 保持 CR 身份，
应用 finally 退出时恢复；首次读取之前的启动/历史加载仍保留信号处理。
原取消/授权断言全部保留，并加终态 ICRNL/ICANON 恢复检查。

最终 CLI 全单测及 Working、实际执行/审批取消/后续输入、历史、修饰 Enter、
启动取消 PTY 联合 **679 passed、0 skipped**，42.57 秒，4 workers/loadfile，
句柄 73207 实际退出 0（95e621）。原生编译器显式配置，50 个 forkpty 警告
保留；七个修改 Python 文件 Ruff/format 与 diff 检查通过。
本批已完成本次 Working 可见性和计时要求，不代表旧 F1–F6 全部细节已完成。

## 2026-09-22：高频交互细节重新纳入活跃验收

用户更新目标：Codex TUI 的基本页面交互是直接设计基准，不再只以 Corki
“能用、无明显 bug”为完成标准。斜杠命令、命令菜单及高级发现仍排除；
此前 F1–F6 的基础验收记录保留，但不能证明 Working/耗时等细节已对齐。

首项待核：Codex `tui/src/status_indicator_widget.rs` 的状态行位于输入区
上方，默认 `Working`，同一行显示递增耗时及中断提示，长详情/Hook 状态
有剩余宽度或换行规则；`fmt_elapsed_compact` 覆盖秒、分、小时。
下一轮应继续追踪 timer、bottom_pane、chatwidget 的创建/刷新/隐藏链，
对照 Corki 当前状态与真实 PTY 画面，形成先红后绿的执行中、工具、
审批、重连、取消/完成场景。此处是待办和源码定位，不宣称已实现。

## 范围与基线（2026-09-21）

当前目标以 objective.md 为准；A–E 核心进一步对齐按用户要求暂停，未宣称全部完成。
2026-09-21 再次收窄 CLI 范围：Codex 仅为适用参考，斜杠命令、skills/路径发现
和菜单扩建不再是本阶段门槛；重点是基本流程、清晰样式和实际 bug。
上一目标轮修改了实际目标和执行入口，属于进展；本轮开始时没有 pytest/execnet 进程。
Codex 参考：ddf04ad26789d040f9ef6a96736f76602e35a6cc，工作树干净。
Corki：e53a368705990cd025bdf2d404b88bb4197c5445，存在未提交核心构造迁移等修改，保留。
已读取 Codex 根 AGENTS、bottom_pane/AGENTS、tui/styles；Corki 未找到 AGENTS。
development.md 的旧里程碑排除项不覆盖当前用户目标。

资源：12 逻辑核、18 GiB 内存、memory_pressure 报空闲 29%。本批采用 4 workers，
文件级分组；PTY/历史均使用 tmp_path、隔离 CORKI_HOME，不需要真实模型或凭据。
96 是上限，不是必须占满；不创建子 Agent。

## 活跃清单

| 项 | 当前证据和差异 | 状态 / 下一步 |
| --- | --- | --- |
| F1 启动/布局 | 40/100 列启动、中文/长内容、双向 resize、浅色真彩色、无 CPR 状态栏与构造失败提示均有 PTY/屏幕证据，见 CLI-015–019、025–027、030 | 基础终端体验已验收；不声称所有终端主题/尺寸均无缺陷 |
| F2 输入 | 草稿取消/恢复、多行粘贴、历史导航、运行中排队与 steer、重连期间草稿、非空草稿 Ctrl+D、控制字符粘贴均有 PTY 证据，见 CLI-001、017、022–023、026 | 基础输入已验收；斜杠命令/补全扩建不在当前范围 |
| F3 输出 | 流尾/工具交错/长结果/错误重连、控制字符隔离、等待与工具状态、终端写入故障收束均已验证，见 CLI-012–015、020–021、024–025、027、029、031–032 | 常用显示已验收；完整 Markdown 控件与罕见组合不在门槛 |
| F4 审批 | 真实 shell 执行授权、patch 详情与拒绝、MCP 问答、Code Mode 相关授权链，以及并发表单、草稿/历史恢复均有集成或 PTY 证据，见 CLI-010–011、028、033 | 既有入口的基本安全/取消边界已验收；不扩建菜单或审批体系 |
| F5 取消/恢复 | 启动、采样、长工具、审批中断，冷恢复不重放副作用、连接失败后继续输入、历史/构造失败退出、流故障后清理均有证据，见 CLI-018–020、031–033 | 基本恢复闭环已验收；真实供应商和所有物理终端故障未验证 |
| F6 功能入口 | 启动状态、模型/effort 选择与实际请求生效、基本帮助/MCP 清单已有 CLI 测试和 PTY 证据 | 基本入口已验收；不新增 /skills 或 Codex 官方菜单 |

## CLI-001：Ctrl+C 草稿处理（P1）

复现：启动 Corki，键入非空草稿（不提交），按 Ctrl+C。
修复前 InputOwner.read_message 抛 InputInterrupted，应用空闲循环直接退出。
Codex 来源：tui/src/chatwidget/interaction.rs::on_ctrl_c 先调用 bottom_pane；
bottom_pane/mod.rs::on_ctrl_c 非空时 clear_composer_for_ctrl_c 并返回 Handled；
chat_composer.rs::clear_for_ctrl_c 保留本地草稿历史。默认
DOUBLE_PRESS_QUIT_SHORTCUT_ENABLED=false：空输入时取消工作/退出，不强行引入双击退出。

修复：TerminalUI 为非空 composer 单独绑定 Ctrl+C，保存输入历史并 reset buffer，
清空暂存 draft；不提交给模型，不写显示 transcript，不改变确认表单或历史视图的键路由。
空草稿仍走原取消/退出处理。历史 reset 在下一帧异步加载，单测等待加载后按 ↑。
快速同帧 Ctrl+C/↑ 的历史加载边界未作专项验收，不能宣称所有输入边界完成。

证据：新增 test_composer_cancel 修复前实际退出 1（277760），断言 composer 提前结束。
修复后首轮：6 个 PTY 用例通过，单测因未等待 reset 后历史异步加载失败（c762db）；
已明确同步加载条件，未删除草稿恢复断言。PTY 在 40/100 列验证 /status 草稿取消、
恢复提交、状态输出、再次输入及退出码 0，未调用模型。

## 验证命令与结果

基线命令：

```sh
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring .venv/bin/pytest tests/unit/cli tests/e2e/test_cli_pty.py tests/e2e/test_cli_startup_cancel_pty.py -n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' -q --tb=short
```

16470 实际退出 1，e111b7：542 passed / 2 failed，8.87s。
两项失败是 reasoning bold/dim/italic、history styled rows；宿主 TERM=dumb。
将终端配置显式设为 TERM=xterm-256color，未改生产样式和原断言，定向同两项
48339 实际退出 0（dd4f80）：2 passed / 0.51s。
这解释了测试环境依赖，不等于 dumb 终端样式已有完整验收。

本轮回归使用同基线命令加 TERM=xterm-256color，句柄 30712 实际退出 0
（830bb7）：547 passed、10 warnings、17.42s。CLI-001 的本批行为和 PTY 验证通过。
ruff check 和 format --check 对本轮三个代码/测试文件通过（fab330）。
PTY 存在 Python forkpty 多线程 DeprecationWarning，未隐藏，暂未观察到相关死锁。

下一优先：核对运行期间取消/草稿/历史视图的交互，再跑真实离线对话和审批闭环；
保留核心构造迁移旧失败，不恢复无关 A–E 全量对齐。

## CLI-002：历史浏览吞掉 Ctrl+C（2026-09-21，P2）

上一轮完成代码修复和 547 项回归，属于进展。本轮开始检查无 pytest/execnet 活进程。
复现：保留未提交草稿，Ctrl+T 打开历史，Ctrl+C 无反应，无法按惯常取消键返回。
Codex keymap.rs 的 pager.close 默认 q/Ctrl+C，close_transcript 为 Ctrl+T；
pager_overlay.rs::TranscriptOverlay::handle_event 先匹配关闭键，设 is_done，
不把该键交给 composer，也不提交草稿。Corki HistoryView 的 Keys.Any 吞掉 Ctrl+C，
只显式实现了 q/Esc/Ctrl+T。

修复：在 active 历史视图上为 Ctrl+C 注册 eager 关闭处理，复用原 close 的屏幕、
焦点、延迟输出恢复；非历史模式不生效，不改变审批表单或普通 composer 取消语义。

测试先红：为现有历史 PTY 的 close_key 添加 Ctrl+C，原草稿、历史、屏幕恢复断言
不变。58289 实际退出 1（c3a504）：40/100 列均等待退出 alternate screen 超时，
2 failed / 34.27s。测试清理了自己的子进程，随后才修改生产实现。

修复后验证命令（4 workers、loadfile；隔离 tmp_path，不联网）：

```sh
TERM=xterm-256color PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring .venv/bin/pytest tests/unit/cli/test_history_view.py tests/unit/cli/test_composer_cancel.py tests/unit/cli/test_approval.py tests/e2e/test_history_view_pty.py tests/e2e/test_approval_pty.py tests/integration/test_cli_input_ownership.py tests/integration/test_cli_user_input.py -n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' -q --tb=short
```

86869 实际退出 1（50a464）：121 passed / 12 failed / 80 warnings，213.53s。
历史 PTY 全部通过，包含新增 Ctrl+C 的两个宽度；12 个失败全部位于审批 resize
用例第 341 行，决定提交后等待 OLD_TRANSCRIPT 重绘超时，不能宣称关联回归全绿。
范围包括历史 resize/加载/流输出、审批详情、并发审批抢占历史、
草稿恢复和实际 runtime 的用户问答/输入清理；审批 PTY 本身是 UI fixture，不能
将其声称为整个 shell/patch 执行授权闭环。ruff check/format --check 两个本轮文件通过。
另已确认 Codex pager 支持 j/k、Ctrl+B/F/U/D 等导航，Corki 当前缺少这些别名，
记为下一批 F2/F5 待补齐，不以本条 Ctrl+C 修复代替完整历史交互对齐。

审批 resize 诊断：55372 实际退出 0（ae06e6），相同 12 用例串行、-s 并将
pexpect 日志输出到 stdout 全部完成。原始终端输出能看到 DECISION 后清屏、
OLD_TRANSCRIPT 和 composer 重绘。该检查同时改变了并行、捕获和日志时序，
因此只证明条件相关，尚不能区分时序缺陷与测试环境影响，不覆盖原失败。
下一优先为定位这 12 项失败（普通串行/同 worker 前序环境及 resize watcher），
不要仅重跑到绿或放宽超时；CLI-002 局部已验证，F4/F5 总体验收仍未完成。

## CLI-003：继承固定尺寸使 resize 监听失效（2026-09-21，P1）

上一轮修复 CLI-002 并暴露审批 resize 的新证据，属于进展；本轮开始无活测试进程。
进一步隔离：原 12 项普通串行（93915，49ec48）退出 0，不需要日志插桩也能通过。
仅一个 xdist worker、-x（38887，11027b）仍在同一重绘断言失败，实际退出 2；
故不能归因于并发资源压力。临时只读诊断在 modal 返回后报告：console.is_terminal=True、
console.size=80×24、物理 fd 1 大小=100×35、环境 COLUMNS=80/LINES=24、
Rich 的内部固定宽高=80/24（43848，635d40）。诊断代码已撤除，原审批测试无修改。

根因：Rich Console 构造器把 COLUMNS/LINES 保存为固定宽高，Transcript.watch
轮询 console.size 永远不变，无法积累 modal 期间的 resize 并在释放后重绘。
这是可由继承环境触发的实际 UI 缺陷，不仅是 worker 数问题。
Codex 依据：tui.rs 的 Resize(Size) 事件和 terminal.size() 使用实时终端尺寸，
本地重绘不以继承的固定行列环境作为权威。

修复：默认交互输出使用 TerminalConsole，从自身输出 fd 查询实际尺寸；
无法查询或重定向输出仍回退 Rich；显式注入的 Console 不改动。进程环境不修改。
新增单测固定 COLUMNS/LINES 下的动态尺寸与重定向降级；保留原 PTY resize 断言。
定向 68141 实际退出 0（7f2da1）：两项新单测和原 12 项审批 resize 全部通过。
ruff check/format --check 三个本轮代码文件通过（72ce2b）。

扩大验证：CLI 全部单测、历史/审批/启动 PTY、输入所有权/问答 integration，
4 workers/loadfile，命令同上扩大 tests/unit/cli 并加入 test_cli_pty.py、
test_cli_startup_cancel_pty.py；34758 实际退出 0（1cadfd）：650 passed、
90 个 forkpty DeprecationWarning，117.04s。原 12 项失败包含在内且全部通过，
未跳过、放宽超时或改动原审批断言。CLI-003 本批验收完成，历史原失败保留。
下一批回到 F2/F5 的历史导航缺口及真实离线对话/执行授权闭环，不扩展核心 A–E。

## CLI-004：历史导航快捷键缺失（2026-09-21，P2）

上一轮解决固定环境尺寸问题并完成 650 项回归，属于进展；本轮开始无活测试进程。
重新核对 Codex keymap.rs::pager 和 pager_overlay.rs::handle_key_event：j/k 单行，
空格/Ctrl+F 下一页、Ctrl+B 上一页，Ctrl+D/U 下/上半页（向上取整）。
Corki 之前只有方向键/PageUp/PageDown，新增七项真实输入解析测试全部先失败：
49822 实际退出 1（031cec），7 failed / 0.90s，滚动位置完全没动。

已为 HistoryView 活跃状态补齐上述键；复用原分页触发和边界夹取，不修改 composer
或审批键绑定。新增单测验证具体滚动量、草稿未污染、退出历史后只有原草稿提交。
普通 VT100 无法区分 Shift+Space 与 Space；Codex 的 Shift+Space 上翻快捷键尚未
接入扩展键盘协议，可用 Ctrl+B/PageUp，不宣称该组合已对齐。
目前沿用 Corki 原 page=终端高度-4 的计算；实际内容视口高度/顶部偏移与 Codex
滚动布局的进一步对照留在 F1/F5，不能以按键别名完成代替完整视口验收。

验证（TERM=xterm-256color，无真实模型/网络，按文件分组、临时目录隔离）：

```sh
.venv/bin/pytest tests/unit/cli/test_history_keys.py tests/unit/cli/test_history_view.py tests/e2e/test_history_view_pty.py -n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' -q --tb=short
.venv/bin/pytest tests/e2e/test_history_view_pty.py -k navigation -n 2 --dist=loadfile --max-worker-restart=0 -o addopts='' -q --tb=short
```

88112 实际退出 0（4d0d94）：26 passed、16 warnings、30.21s。
之后增加两个 40/100 列 PTY 导航用例，验证键序列后的确切滚动位置、alternate
screen 退出、原草稿提交和子进程退出码 0；44525 输出（6eface）2 passed、
2 warnings、4.80s。该组合 shell 命令最终退出 1 是随后 format --check 发现引号
格式差异，非 pytest 失败；已格式化并重新通过 lint/format 检查。forkpty 警告保留。
下一优先：实际 runtime 的离线 CLI 对话/执行审批闭环和运行中取消，不再重复扩展
本批快捷键排列；F1–F6 整体验收未完成，A–E 仍暂停。

## 实际执行链闭环验收（2026-09-21）

上一轮补齐导航并取得终端证据，属于进展；本轮无活测试进程后才启动新批次。
确认本地 native sandbox compiler 存在，使用显式路径运行，未因缺依赖跳过。
Codex 取消依据仍为 chatwidget/interaction.rs::on_ctrl_c：composer/modal 优先消费，
空草稿且存在活动工作时提交 interrupt；取消后仍可继续输入，不当成退出。

新增 tests/e2e/test_execution_lifecycle_pty.py，在 40/100 列分别验证接受/取消：
普通 CLI 输入 → 实际 runtime 请求 exec_command → 真实审批等待且目录尚不存在 →
显式按 y 或 Ctrl+C → 只有接受后实际 mkdir → 新一轮流式生成 → Ctrl+C 中断 →
后续普通消息完成 → Ctrl+T 历史/ Ctrl+C 返回 → Ctrl+D 退出。
子进程还验证模型关闭、无 shell session/待审批、无 modal、表单历史为空、普通
输入历史仅包含 execute/wait/followup。所有副作用位于隔离 tmp_path。
模型为离线 fixture，不冒称真实供应商质量；执行/授权链和 SQLite runtime 没有替换。

初次 12701 实际退出 1（a3ed9f）：2 passed / 2 failed / 23.41s。
失败是取消分支错误等待自定义正常返回标记 TURN_SETTLED；runtime 取消按契约传播
CancelledError，被应用消费并恢复 composer，因此不会执行正常返回后的 print。
将取消分支改为等 Turn interrupted，保留实际目录、后续操作和资源清理断言。
没有为迎合 fixture 修改生产取消逻辑。

```sh
TERM=xterm-256color CORKI_TEST_SANDBOX_COMPILER=/Users/corki/PycharmProjects/corki/src/corki/_native/sandbox/corki-sandbox PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring .venv/bin/pytest tests/e2e/test_execution_lifecycle_pty.py tests/integration/test_cli_execution_scope.py tests/e2e/test_user_input_pty.py -n 4 --dist=load --max-worker-restart=0 -o addopts='' -q --tb=short
```

65509 实际退出 0（71748d）：48 passed / 8 forkpty warnings / 16.49s，无 skip。
4 workers，独立临时目录/数据库，包含直接工具/Code Mode、shell/patch 审批详情、
once/session/rule 键盘授权及冷会话权限不继承、用户问答真实终端恢复。
新文件 ruff check/format --check 通过（008058）。本批只增验收，没有生产改动。
这不覆盖同一 PTY 的进程冷恢复、执行中长子进程取消、MCP 全闭环，也没有验证
取消提示恰好一次；这些仍在 F4/F5。下一批优先核对取消终态重复呈现及长工具取消。

## CLI-005：一次取消重复记录中断提示（2026-09-21，P2）

上一轮新增实际执行链验证，属于进展；本轮开始无活测试进程。
Codex input_restore.rs::on_interrupted_turn 在终态处理处 finalize、记录提示、
恢复 pending 输入。Corki 则在 TurnCancelled 消费后记录提示，随后流抛出的
CancelledError 又被 run 的兜底处理重复提示。同一次取消在 transcript 中有两条，
不是历史重绘造成同一条内容再显示。

为已有实际 shell/取消/后续输入 PTY 加入 transcript 精确次数断言：接受审批路径
只应有采样取消一次；取消审批路径加上采样取消共两次。61999 实际退出 1
（06c111），4 failed / 4.28s；接受分支实际记录两条，复现成立。

修复：CLI 层共享 _interruption_reported 和 _show_interruption；事件通知与
异常兜底共用去重状态。每个新操作和 TurnStarted 重置；没有终态事件的取消
仍输出兜底提示，后续操作也能再次提示。不改 runtime 取消、异常传播或资源清理。

```sh
TERM=xterm-256color CORKI_TEST_SANDBOX_COMPILER=/Users/corki/PycharmProjects/corki/src/corki/_native/sandbox/corki-sandbox .venv/bin/pytest tests/unit/cli/test_application.py tests/e2e/test_execution_lifecycle_pty.py tests/integration/test_cli_input_ownership.py -n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' -q --tb=short
```

67519 实际退出 0（667069）：120 passed / 4 warnings / 11.67s。
之后新增两项非实时应用循环测试：有/没有 TurnCancelled 事件时，连续两轮取消
每轮各提示一次，确保兜底不消失、不跨轮抑制。42649 实际退出 0（5a2b0c）：
2 passed / 99 deselected / 0.59s；三个本轮文件 lint/format 检查通过。
本批修改 CLI 适配层，不恢复 A–E。下一优先仍是长工具取消和冷恢复端到端，
并回到 F1–F6 检查输入补全、布局等尚未完成项。

## 跨进程冷恢复验收（2026-09-21）

上一轮修复重复取消提示并验证，属于进展；本轮开始无活测试进程。
Codex app/session_lifecycle.rs::replace_chat_widget_with_app_server_thread 明确区分
新建初始消息与 resume/fork（不自动提交新用户回合）；Corki history.py 的回放只渲染，
本批通过实际新解释器验证这条边界，不仅在同一个 Python 进程重建 runtime。

扩展 execution_lifecycle_pty：暖进程完成真实审批/取消/后续输入后退出，记录 thread id；
新 PTY/解释器使用同一个 SQLite thread，检查历史可见，/status 可用，正常退出。
冷模型显式统计调用次数，最终断言 0，避免仅抛异常后被应用兜底吞掉造成假通过；
还检查模型关闭、没有待审批或 shell session，实际目录状态不改变。
覆盖 40/100 列、接受和取消审批两种已持久化历史。未替换实际 runtime 或审批链。

17870 实际退出 0（c8c66e）：扩展 PTY 加原失败历史 PTY，6 passed / 6.76s。
补强模型零调用断言后的命令：

```sh
TERM=xterm-256color CORKI_TEST_SANDBOX_COMPILER=/Users/corki/PycharmProjects/corki/src/corki/_native/sandbox/corki-sandbox .venv/bin/pytest tests/e2e/test_execution_lifecycle_pty.py tests/integration/test_cli_history_replay.py tests/integration/test_cli_stream_cleanup.py -n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' -q --tb=short
```

52669 实际退出 0（8eb2d0）：27 passed / 8 forkpty warnings / 15.99s。
本批只增加验证，无生产功能改动；解决了跨进程冷恢复的证据缺口，不代表所有恢复
场景完成。长子进程执行中取消仍待独立 PTY 验收，未用采样取消冒充工具取消。

## 长 shell 工具取消验收（2026-09-21）

上一轮取得跨进程回放证据，属于进展；本轮开始无活测试进程。
扩展实际执行链 PTY 的 long 分支：批准 exec_command，启动写入自身 PID 后
sleep(300) 的真实子进程，yield_time_ms=30000。确认 PID 存活后按 Ctrl+C，
在 composer 恢复时、退出应用之前断言 PID 已不存在；随后继续采样取消、普通
对话、历史浏览、退出及新进程冷恢复。不是等待退出应用才清理工具的假通过。

首次 8010 退出 1（e81e63）：两个宽度等待工具 stdout 超时；串行日志诊断
43399 退出 1（1b1048）确认执行在等待，而 exec_command 在 yield 前缓冲输出。
因此改用工具自己写出的 PID 文件确认启动，不依赖审批中回显的命令文本。
初次失败时宿主被测试强制关闭，遗留三个 fixture 子进程（69900/69901/73228），
已按精确 PID 确认测试命令后 SIGTERM 清理；之后增加失败路径的 fixture PID 清理。
成功路径在该清理之前已断言 ProcessLookupError，不以测试清理替代产品验收。

1739 实际退出 0（b604f6）：两个 long 用例通过，4.84s。
加入失败清理后的完整文件回归：

```sh
TERM=xterm-256color CORKI_TEST_SANDBOX_COMPILER=/Users/corki/PycharmProjects/corki/src/corki/_native/sandbox/corki-sandbox .venv/bin/pytest tests/e2e/test_execution_lifecycle_pty.py -n 4 --dist=load --max-worker-restart=0 -o addopts='' -q --tb=short
```

72293 实际退出 0（1bbcfb）：6 passed / 12 forkpty warnings / 9.00s。
回归后进程检查没有该 sleep fixture 残留。没有生产代码修改。
本条验证活动、尚未 yield 的直接 shell 调用；不扩大为所有后台进程或平台的结论。
下一优先回到 F2/F6：输入补全、命令发现和真实菜单状态，避免围绕取消继续无界加例。

## CLI-006：命令候选与 Tab 补全（2026-09-21，进行中）

上一轮取得长工具取消证据，属于进展；本轮开始无活测试进程。
源码依据：Codex bottom_pane/chat_composer/slash_input.rs 的命令弹窗处理，
Up/Down 选择，Tab 补全（Skills 有独立立即分发例外），Enter 分发；
selected_command_completion 返回规范命令名及尾部空格。Corki 原先没有 completer，
Tab 无条件提交 QueuedInput，导致命令前缀不能通过键盘补全。

第一批实现：CommandCompleter 为现有顶级命令提供前缀候选和描述；composer
启用键入补全，Tab 接受候选但不提交，Enter 补全选项后提交原 dispatcher；普通
非命令输入维持原 Tab 排队路径。表单使用独立 session，不增加候选或污染审批。
未知命令仍由 dispatcher 本地报告。model 描述明确为配置说明，不伪装已有模型选择器。

6011 退出 0（969658）：新 Tab 行为、模式焦点和历史键共 43 passed / 2.71s。
扩大验证命令：

```sh
TERM=xterm-256color .venv/bin/pytest tests/unit/cli tests/e2e/test_cli_pty.py -n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' -q --tb=short
```

81663 退出 0（eda0f5）：555 passed / 6 warnings / 13.31s。真实 PTY 在 40/100
列检查候选可见、Tab 后没有 /status 执行结果、Enter 后有实际状态结果并正常退出。
新增行为本批未单独运行旧实现红测，不将源码差异冒充红测证据。

未完成：Esc 关闭候选后的输入语义、方向选择/快速输入、运行中队列完整 PTY、
模糊匹配与排序、子命令分组、skills/路径补全、候选布局快照。/memory 和 /mcp
顶级候选目前仍依赖用户补子命令；没有新增这些顶级裸命令的分发语义。
下一批先完善候选取消和参数输入边界，保持这一项进行中，不能宣称 F2/F6 完成。

### CLI-006 第二批：取消候选与换行边界

本轮已读取实际 goal；上一轮目标文件修订属于进展。源码基准仍为 Corki
e53a368705990cd025bdf2d404b88bb4197c5445、Codex
ddf04ad26789d040f9ef6a96736f76602e35a6cc。再次核对 slash_input.rs:211
之后的 Esc 逻辑：关闭弹窗、保留草稿、记录取消的命令 token。

完成行为：Esc 关闭候选后 Enter 提交原文而不是再次选择候选；修改输入后可重新
显示候选；新一轮输入清除取消状态。Tab 委托实际 DynamicCompleter 查询候选，
不因框架包装而误判无补全并排队提交。Esc 不能使用 eager 绑定抢走 Alt+Enter；
采用普通绑定，composer 多键等待 timeoutlen=0.1 秒，保留终端解码等待，避免默认
一秒多键等待让随后 Enter 被误解为换行。终端无法同时无延迟识别单独 Esc 与
所有 Alt 组合；此处保留组合键解析，不声称与原生事件输入时延完全相同。

红测及诊断：
- 21371 退出 1（181bcf）：1 failed / 1 passed，Tab 错误返回 QueuedInput('/sta')。
- 62935 退出 1（f41d12）：1 failed / 3 passed，eager Esc 使 Alt+Enter 提交原文。
- 53455 退出 1（173fb5）：去掉 eager 后两个单 Esc 检查未等到多键解析结束。
- 42188 退出 1（366d3d）：4 PTY failed / 6 passed，默认键序列等待使独立 Esc 后
  的 Enter 仍进入换行；调整生产 composer 等待时长，不跳过真实终端断言。
- 65241 退出 0（b43e03）：10 passed / 6 forkpty warnings / 13.47s，覆盖
  40/100 列、Esc 后未知命令反馈、重新补全执行、Ctrl+C/Ctrl+D 退出及草稿保留。

测试挂起记录：恢复时旧 pytest PID 657 及其四 worker 仍存活，超过九分钟；
采样显示 worker 等待，旧工具句柄已不可用，SIGINT 停止后确认五个进程都已退出，
旧批次最终退出码不可恢复，不计为通过。新广域回归 61563 的 faulthandler 明确
定位 test_terminal_ordinary_draft_survives_preemption；中断输出证明模拟 Session
缺失 completer 引起 AttributeError，而测试无限等待 entered。61563 最终退出 2，
555 passed 只是部分结果。补齐模拟器的 CommandCompleter，并对等待加三秒超时，
保留原草稿恢复和关闭次数断言；不是产品放宽空 completer 或取消安全检查。

最终回归（四个独立 worker，沿用本阶段保守资源配置，不启用 96 worker）：

```sh
TERM=xterm-256color .venv/bin/pytest tests/unit/cli tests/e2e/test_cli_pty.py -n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' -o faulthandler_timeout=30 -q --tb=short
```

18041 实际退出 0（9b095d）：558 passed / 6 forkpty warnings / 17.07s。
本批五个相关生产/测试文件 ruff check 与 format --check 均通过（135752）。
没有模型联网、核心层改动或官方服务入口。CLI-006 仍进行中：下一批核对候选导航、
选择时草稿和参数保留，再推进子命令发现与实际功能菜单；整体 F1–F6 未完成。

### CLI-006 第三批：候选导航不提前改写草稿

上一轮已修复取消和换行并完成回归，属于进展；本轮读取实际 goal，进程检查无
存活 pytest/execnet。参考 Codex slash_input.rs:225 的 Up/Ctrl+P、Down/Ctrl+N
以及 command_popup.rs:225 的循环选择；这些按键只改变弹窗选择，不改 composer。

红测 44991 退出 1（a5d950）：四种按键全部复现 Corki 把 /m 提前改成 /model
或 /mcp。现在默认高亮第一候选；导航只更新 CompletionState 的选择索引并重绘，
不使用会替换输入的 buffer.go_to_completion。Tab/Enter 才应用候选；普通输入
历史和历史浏览仍使用原按键逻辑，审批表单不安装这些绑定。

实现中 37403 退出 1：15 个初始化失败，原因是注册时 HistoryView 尚未创建；
已移到视图创建后，不用弱化 active 判断掩盖。32547 退出 0（d511a1）：15 passed，
覆盖四种导航按键、原文保持、选择后的 Tab 和参数提交，以及历史键隔离。
PTY 增加 / → Down → Enter 执行 /status，先断言导航没有执行命令；40/100 列
均实际运行，保留原有取消补全、草稿恢复和两种退出检查。

```sh
TERM=xterm-256color .venv/bin/pytest tests/unit/cli tests/e2e/test_cli_pty.py -n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' -o faulthandler_timeout=30 -q --tb=short
```

54083 实际退出 0（f07fcf）：562 passed / 6 forkpty warnings / 19.09s。
四个改动文件 ruff check、format --check 均通过（0aae74）。未改变核心和官方
服务边界。仍待：完整屏幕快照、模糊匹配/排序、技能和路径入口、已有参数时的
命令 token 补全、子命令发现及真正设置菜单；不能以本批绿色回归宣称 F2/F6 完成。
下一优先检查 /memory、/mcp 的裸命令发现与帮助反馈，避免菜单可见却直接报未知命令。

## CLI-007：运行期间本地命令误入模型输入（已修复本批场景）

本轮已读实际目标；上一轮候选导航修复和回归属于进展，开始无活测试进程。
核对入口时发现优先级更高的实际故障：_consume_interactive_events 仅拦截 plan、
memory、compact、stop，/help、/status、/clear、/mcp refresh 和未知斜杠命令均
可落入 runtime.steer。不是单纯缺少帮助文本，而是本地操作进入模型上下文。
Codex 依据：slash_command.rs::available_during_task 将 Status/Mcp 等与不可用的
Clear/Compact/Plan 分开；chatwidget/slash_dispatch.rs 的 MCP 入口请求实际工具
状态，不作为普通对话。本轮不复制官方 memory maintenance stub。

2502 退出 1（634a40）：新增五场景全部复现 runtime.steer 收到本地命令。
修复在 CLI 现有特殊命令路径后统一检查 dispatcher.handled：只读和未知命令输出
本地反馈；MCP_REFRESH 调实际 request_mcp_refresh；其余尚未支持的运行中状态
变更明确提示 unavailable，不误发模型或假称执行成功。显式 Tab 队列路径、原有
memory 确认、plan/compact 限制、普通 steer 和取消逻辑不变。运行期间 realtime
on/off 当前明确不可用，尚未实现运行中设置菜单，不冒称完整等价。

16904 退出 0（d55329）：五个定向场景通过。之后加强断言检查准确反馈、刷新调用
次数及 Clear 无副作用；真实 runtime + 离线模型的 queued_input PTY 加入运行中
/status、/missing、/clear，再继续排队、编辑、真实 steer、后续回合及退出。
fixture 明确断言只有普通 steer now 能进入 steer，最终模型请求和 Turn ID 不变。

```sh
TERM=xterm-256color .venv/bin/pytest tests/unit/cli/test_application.py tests/e2e/test_queued_input_pty.py -n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' -o faulthandler_timeout=30 -q --tb=short
```

86859 实际退出 0（338651）：110 passed / 4 forkpty warnings / 12.07s；PTY 覆盖
40/100 列及队列编辑分支。三个相关文件 ruff check 与 format --check 通过（048bef）。
本轮未运行全 CLI 或核心全量，不把专项证据扩大为全功能完成。没有核心代码变更。
下一步回到 MCP 实际工具状态入口：已确认 runtime.mcp_tool_catalog() 可用，应
接真实目录并处理加载失败/取消，不用仅显示帮助替代 Codex /mcp 功能；/memory
本地帮助和安全子命令发现仍待实现，F1–F6 整体继续进行。

## CLI-008：/mcp 实际目录入口（第一批）

上一轮运行中命令分流属于进展。本轮读取实际 goal，开始无活测试；核对 Codex
chatwidget.rs:1580 add_mcp_output：异步 FetchMcpInventory，加载区不锁住 composer。
Corki 现有 runtime.mcp_tool_catalog() 提供真实、独立于模型可调用集合的工具目录。
新增 MCP_LIST action，空闲和运行中均交给 CLI MCPInventory 管理一个有所有权的
后台任务；重复查询不叠加任务，完成后可再查，退出时先取消并 join，再关闭 runtime。
只在 AgentRuntime Protocol 补声明已有方法，没有改 MCP 核心查询/授权/联网策略。

展示按服务器分组的真实工具名称；空目录明确反馈。明确注明目录发现不代表执行授权
或连接健康，未提供鉴权信息的接口不伪造登录状态。失败仅显示异常类型及重试入口，
不把可能含凭据的异常原文输出；远端名称过滤终端控制字符。帮助和补全描述同步更新。
目录可能触发用户已配置 MCP 的正常发现，不调用模型、官方账户或官方专属服务。

28389 退出 2（24781d）：新模块尚不存在，属于收集失败，不冒充行为红测。
97339 退出 0（ff952f）：12 passed（目录任务及 dispatcher）。覆盖重复请求、失败
后重试、异常原文不泄漏、退出取消和 join；已有 refresh/memory 确认断言保留。

```sh
TERM=xterm-256color .venv/bin/pytest tests/unit/cli tests/e2e/test_cli_pty.py tests/e2e/test_queued_input_pty.py -n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' -o faulthandler_timeout=30 -q --tb=short
```

37707 实际退出 0（ddb52c）：574 passed / 10 forkpty warnings / 18.06s。
queued_input PTY 使用真实 runtime 和本地 mcp_number_server.py stdio 服务，40/100
列均在运行中查询到 mcp__numbers::read，随后继续真实队列、编辑、steer、后续 Turn
和退出；保留最终模型消息/Turn ID 断言，目录命令未发给模型。无外部模型调用。
随后补空闲 /mcp 空目录的 CLI PTY：31967 退出 0（f95ea4），9 passed / 6 warnings /
17.43s（test_cli_pty + test_mcp_inventory）。八个相关文件 lint/format 检查通过（65cc44）。

尚未完成完整 Codex MCP 交互：加载 spinner 替换、独立取消查询、verbose/鉴权和资源
信息、配置存在但工具不可见的详细原因、布局快照仍待；本批不可宣称完整 MCP 状态
等价。下一批先补异步查询可见状态/取消与错误恢复边界，再推进其余 F6 入口。

### CLI-008 第二批：加载提示生命周期

本轮读取实际 goal，开始无活测试；上一轮实际目录接入属于进展。重新核对 Codex
chatwidget.rs:1580–1618：加载单元是临时状态，清理只影响自身，不能清掉其他活动单元。
Corki 上批把 Loading MCP 写成永久 notice。本批 TerminalUI 改为 composer 上方的
独立临时一行，历史浏览时隐藏；不占用 reasoning/hook/mode toolbar，也不进入
Transcript.calls。完成、失败、取消均清除；任务尚未开始即被取消时，aclose 额外
清理（该路径不会进入协程 finally）。不支持该临时 UI 接口的宿主保留 notice 降级。

6721 退出 1（66c91a）：四种结局均因缺少临时状态失败，旧实现同时打印永久 Loading。
90513 退出 0（772835）：34 passed，包含清理、reasoning/hook 状态不受影响；修正
新测试误用的 transcript.entries 为现有 calls 属性，没有改已有产品断言。
新增 test_mcp_loading_pty.py 是慢查询/失败注入的 UI 组件 PTY，不冒称真实 MCP 服务；
40/100 列看见临时加载文本后仍能输入，完成/失败/宿主取消后可继续输入，断言状态
清除、查询 finally 执行且历史没有加载记录。23142 退出 0（24cc68）：13 passed。

```sh
TERM=xterm-256color .venv/bin/pytest tests/unit/cli tests/e2e/test_mcp_loading_pty.py tests/e2e/test_queued_input_pty.py -n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' -o faulthandler_timeout=30 -q --tb=short
```

44236 实际退出 0（561d3d）：578 passed / 10 forkpty warnings / 16.05s。
含上批真实 stdio MCP + runtime 运行中查询流程，四个相关文件 lint/format 均通过
（a2a3d2）。没有核心改动。本批提供临时文本而非动画 spinner，尚未提供用户独立
取消查询的按键；PTY 也不是完整屏幕快照，这些差异保留。下一步切回其余高频 F6
入口（模型配置选择等）及总体布局检查，避免只围绕 MCP 不断扩大测试排列。

## CLI-009：模型选择实际生效链路（第一批，菜单未完成）

上一轮临时加载状态修复属于进展；本轮读取 goal，开始无活测试。Codex
chatwidget/slash_dispatch.rs:303 的 /model 打开选择弹窗并等待设置应用后继续输入。
Corki 原先只提示编辑配置文件，本轮先接供后续菜单复用的实际提交链路：
/model <name> 保留大小写，调用现有 update_thread_settings(model=...)；成功后
同步 dispatcher 状态、UI 启动头模型及 default 模式的恢复基线，明确只影响后续
Turn，不改 provider 或全局配置。失败保留旧设置并输出无敏感原文的错误类型；
取消按 runtime 已发布 snapshot 同步后传播，不凭取消本身推断提交未发生。
支持空闲与运行中提交，不把命令发给模型；裸 /model 暂仍显示当前值和用法。

8735 退出 1（eeedf4）：新增保留 Vendor/Model-X 的命令测试原先得到 NONE。
真实 runtime 集成验证命令后的 ModelRequest、/status、UI 设置与持久化；失败注入
仍使用旧模型且可继续对话。queued_input PTY 增加运行中 /model Local/Other：
实际四个请求模型依次为旧、旧、新、新，原队列/steer/Turn ID 断言全部保留。

中间失败如实记录：86929 为 1 failed / 11 passed；52743 为 3 failed / 578 passed。
新冷恢复测试错误地把旧显式配置传给 SDK 构造器；/main.py 实际先读保存值并经
CorkiSettings.for_directory(resume_model_settings=...) 解析，再构造 runtime。
66135 为 1 failed / 580 passed，证明单纯 resume_pending 不会覆盖 SDK 显式配置；
最终测试按真实 CLI 顺序验证保存记录、设置解析和冷 runtime，不改核心语义。
40 列 PTY 另因长句被正常换行导致整句 expect_exact 超时，改为分别检查准确模型名
及句尾，保留底层四次请求模型断言。测试编写中 31947 存储类导入错误导致收集失败；
85452 虽 2 passed 但暴露 close 未 await 的警告，已修正，未把该轮当最终验收。

```sh
TERM=xterm-256color .venv/bin/pytest tests/unit/cli tests/integration/test_cli_model_command.py tests/integration/test_cli_plan_command.py tests/e2e/test_queued_input_pty.py -n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' -o faulthandler_timeout=30 -q --tb=short
```

72096 实际退出 0（73d124）：581 passed / 4 forkpty warnings / 15.57s，未再出现
未 await 警告。七个相关文件 lint/format 检查通过（b9a326，之后仅补 close 的 await）。
本批无核心实现改动、无模型联网。仍需真实选择弹窗、取消不提交、模型列表来源、
reasoning 选择及布局验证；不能以命令参数替代最终要求的 Codex 式模型选择界面。
下一批优先实现裸 /model 的选择交互，复用本批已验证的提交链路。

### CLI-009 第二批：裸 /model 选择界面

本轮已读实际 goal，开始无活测试；上一轮真实提交链路属于进展。参考 Codex
chatwidget/model_popups.rs 的 open_model_popup/current_model/selection actions：
打开当前候选，只有确认才分发设置变更。Corki 新增独立 model_picker 模块，
裸 /model 通过 InputOwner._modal 串行获取终端焦点，复用独立 DummyHistory 表单，
返回后才调用 _set_model。审批/问答仍走同一个模态锁，没有增加第二个并发 reader。

列表以当前模型和用户显式 models.catalog 为来源，去重；不拿内置上下文元数据
或 OpenAI 官方目录假装供应商可用列表。可键入筛选、上下/Ctrl+P/Ctrl+N 浏览，
无匹配时 Enter 接受自定义名称（大小写保留）；Esc/Ctrl+C/Ctrl+D 返回取消。
取消、确认、异常退出均清理表单缓冲和监听器，不记入普通输入历史。菜单标明
配置候选仍取决于供应商可用性；当前仅选择模型，不伪造 reasoning、订阅或配额信息。

81569 退出 2（64781b）：新模块未实现的收集失败，不记作行为红测。
89421 退出 0（bd0225）：4 个管道输入测试通过，覆盖方向选择确认、两种取消、
自定义模型及无历史残留。PTY 扩展现有真实 runtime 流程：任务运行中打开菜单，
输入 Discard/Model 后 Esc，再次打开输入 Local/Other 并确认；断言实际设置更新
恰好一次、表单名称不进历史、原回合旧模型/后续排队回合新模型、后续 MCP 查询与
队列编辑可继续，40/100 列均运行。未使用真实外部模型服务。

```sh
TERM=xterm-256color .venv/bin/pytest tests/unit/cli tests/integration/test_cli_model_command.py tests/e2e/test_queued_input_pty.py -n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' -o faulthandler_timeout=30 -q --tb=short
```

77569 实际退出 0（e9c217）：579 passed / 4 forkpty warnings / 18.88s。
七个相关文件 lint/format 通过（3e97a4）。没有核心实现修改。
仍需大目录/窄终端布局快照、reasoning 选择、菜单与后到审批的专门竞争验收；
没有实现远端模型目录请求，当前显式配置+自定义入口是独立 provider 的已接入来源。
下一批优先检查选择菜单的布局和错误反馈，再回到 F1–F6 总体差异，不能宣称 CLI 完成。

### CLI-009 第三批：无效名称反馈

本轮读取 goal，开始无活测试；上一轮菜单接入属于进展。复查 Codex
list_selection_view 的 no matches 呈现及 model picker 80 列快照；自定义名称是
Corki 普通 provider 入口，不能把“无候选”当作“无效名称可以提交”。
3005 退出 1（85633b）：1 failed / 4 passed，Invalid Model 按 Enter 后仍提示
可使用该名称但不提交。现显示明确红色错误，保持输入；编辑后清除错误，可修正
再提交。95039 退出 0（e2334f）：5 passed。真实 runtime 的 queued_input PTY
增加无效输入→报错→Ctrl+U 修正→确认，保留实际更新恰好一次、当前/后续回合模型
及队列/历史断言，40/100 列均覆盖。
39610 退出 0（75d4ef）：11 passed / 4 forkpty warnings / 16.33s，命令为
`TERM=xterm-256color .venv/bin/pytest tests/unit/cli/test_model_picker.py tests/integration/test_cli_model_command.py tests/e2e/test_queued_input_pty.py -n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' -o faulthandler_timeout=30 -q --tb=short`。
首次静态检查发现新测试长行，已格式化并重新 lint；本批未跑全 CLI，不扩大专项结论。
菜单全屏快照、reasoning 选择、审批竞争等原有剩余项不变。

### CLI-009 第四批：长名称窄窗口布局

本轮读取 goal，开始无活测试；上轮错误反馈修复属于进展。参考 Codex
selection_row_layout.rs 的显示宽度和换行处理。新测试复现 40×12 菜单按候选数
而非实际显示行数限高的问题；56054 退出 1（4c50f8）：1 failed / 1 passed。
现在按终端字符宽度折行，并扣除标题、错误、快捷键及输入占用后分配候选行数，
优先保留选中项，导航使选择保持可见；超长单项超过可用行数时显式省略，不截断
实际提交值。首版换行把选择箭头与名称分开，12889 退出 1（297360）；改为为
箭头/缩进预留两列后，33622 退出 0（788e5a）：7 passed。
人工检查 40 列长中文名称的实际渲染片段（62a0c8），存为 model_picker_40.txt
快照并纳入测试；这是菜单格式化文本快照，不是完整 VT 屏幕截图。测试还验证
滚动到第 11 个候选后 Enter 返回完整原始模型名。
68077 退出 0（0759ca）：11 passed / 4 forkpty warnings / 17.00s，运行
test_model_picker + test_queued_input_pty，4 workers/loadfile，真实终端完整切换
与错误修正流程仍通过。快照加入后再跑模型菜单单元及 lint/format，结果见本轮工具输出。
本批不宣称极小终端或任意超长输入均完整可见；reasoning 和后到审批竞争仍待。

### CLI-009 第五批：模型与 reasoning 原子选择

本轮读取 goal，开始无活测试；上轮布局修复属于进展。核对 Codex
model_popups.rs:439 的 supported reasoning popup 与 max/ultra 二次确认。
Corki 选择模型后从既有 ModelContextInfo.supported_reasoning_levels 读取可用档位，
无声明时不编造选项，保留原有自定义模型路径。模型+档位返回 ModelSelection 后
通过一次 update_thread_settings 发布，任一选择步骤取消都不发布半套设置。
max/ultra 另有默认取消的确认步骤，只提示时间/token 开销，不复制官方配额/套餐。
UI 与 /status 同步显示所选 effort；普通 /model <name> 保持原来的稀疏更新行为。
仅改 CLI，没有变更核心模型能力或传输语义。

新增 test_model_effort_pty.py 通过真实 runtime + 离线模型验证 40/100 列下第二步
取消后重新选择，high/max 最终确认仅产生一次更新，实际 ModelRequest 同时带正确
model/effort，关闭后模态深度归零。本功能本轮未单独运行旧版本行为红测。
49510 退出 1（467e40）：3 failed / 8 passed，失败发生在等待退出；测试把输出
DONE 后的运行中 composer 当作新空闲回合，Ctrl+D 消费为取消而非退出。改用应用
_consume_turn 返回后的明确 TURN_SETTLED 标记同步，保留实际请求与更新次数断言。
33931 退出 0（ae8ad6）：4 passed / 4 forkpty warnings / 11.38s。

```sh
TERM=xterm-256color .venv/bin/pytest tests/unit/cli tests/integration/test_cli_model_command.py tests/integration/test_cli_plan_command.py tests/e2e/test_model_effort_pty.py tests/e2e/test_queued_input_pty.py -n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' -o faulthandler_timeout=30 -q --tb=short
```

34257 实际退出 0（7f5425）：592 passed / 8 forkpty warnings / 18.90s。
五个相关文件 lint/format 通过（16ae1b，初次新 fixture 长行已修正）。
Plan 模式的选择作用域提示、ultra 特殊语义完整验收、后到审批竞争仍未完成；
不能把本批 high/max 证据扩成所有档位已验收。下一步处理模态竞争/安全输入边界，
之后回到整体 F1–F6 缺口，不恢复已暂停的核心对齐。

### F4/F6：设置菜单与后到执行审批（安全证据，调度差异仍开放）

新增 `tests/e2e/test_model_approval_pty.py`，真实 runtime、原生执行审批及物理
PTY，离线模型在模型菜单打开后才请求 mkdir。40/100 列、菜单确认/取消共四例：
菜单期间审批等待，菜单 Enter/Esc 不授权；独立按 y 后才创建临时目标。检查模型
设置结果、表单历史不污染、审批/进程/输入锁清理。未改生产实现，本批是当前行为
和安全边界的特征测试，不是 Codex 弹窗调度完全等价证明。

参考 `codex-rs/tui/src/bottom_pane/mod.rs:1635` 的 push_approval_request：
有最近 composer 活动或待显示审批时入延迟队列；输入空闲阈值为 1 秒（:194），
延迟审批仅在 view_stack 为空时展示（:677）。没有最近活动时则可直接 push_view
叠加审批。Corki InputOwner._modal 当前一律串行锁等待，既没有普通输入的空闲
保护，也没有菜单之上的可恢复审批栈。因此本项仅安全子项验证，**调度对齐未完成**。
下一批优先补普通输入期间的审批延后与草稿保护，再处理菜单暂停/恢复；不能把
本测试的“总是等待菜单”固定为最终 Codex 行为，调度升级时须同步调整特征断言，
保留不误授权、不污染历史和真实副作用的安全断言。

定向 13034 退出 0：4 passed / 4 forkpty warnings / 12.80s；ruff check/format 通过。
联合回归（独立 tmp_path，4 workers，避免 PTY/原生子进程过量并发）：

```sh
CORKI_TEST_SANDBOX_COMPILER=/Users/corki/PycharmProjects/corki/src/corki/_native/sandbox/corki-sandbox TERM=xterm-256color .venv/bin/pytest tests/integration/test_cli_input_ownership.py tests/integration/test_cli_model_command.py tests/e2e/test_model_approval_pty.py tests/e2e/test_model_effort_pty.py tests/e2e/test_execution_lifecycle_pty.py -n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' -o faulthandler_timeout=30 -q --tb=short
```

83036 实际退出 0：33 passed / 20 forkpty warnings / 24.54s。警告为 macOS
多线程宿主 forkpty 的既有弃用警告，不隐藏；没有真实供应商调用。核心 A–E 继续暂停。

### CLI-010：正在输入时审批抢焦点（已修复输入空闲保护）

依据 Codex bottom_pane/mod.rs:194、:659–705、:1635 的 1 秒 typing idle
保护，Corki TerminalUI 记录实际按键造成的编辑/提交时间；InputOwner.elicit 在
转移输入所有权前等待空闲，后续输入重置截止时间。独立 approval lock 保留审批
到达顺序；等待期间不取消 composer、不锁住普通输入，取消等待可直接清理。
恢复草稿/渲染/CPR 不算新的用户编辑。未改核心授权链或新增供应商服务。

新 test_approval_typing.py 在旧实现上 66020 退出 1：2 failed，明确失败于
“approval stole active typing”。修复后 83324 退出 0：45 passed / 2.95s
（含既有 elicitation 测试）。另覆盖 bracketed paste、继续输入延长等待、取消
待显示审批、恢复并提交完整草稿、表单不污染历史。

test_model_approval_pty.py 扩展 40/100 列真实 runtime/原生审批场景：输入 draft
触发工具请求，等待期间输入 y 仍属于草稿；独立批准前目录不存在，批准后才创建；
后续实际提交 drafty 并正常退出。95965 退出 1：2 failed / 605 passed，失败因
测试在非空草稿时等待空 placeholder，屏幕实际已显示 drafty；改成断言恢复草稿，
保留显式授权、文件副作用、历史及关闭断言，没有放宽生产安全行为。

```sh
CORKI_TEST_SANDBOX_COMPILER=/Users/corki/PycharmProjects/corki/src/corki/_native/sandbox/corki-sandbox TERM=xterm-256color .venv/bin/pytest tests/unit/cli tests/integration/test_cli_input_ownership.py tests/e2e/test_model_approval_pty.py tests/e2e/test_execution_lifecycle_pty.py -n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' -o faulthandler_timeout=30 -q --tb=short
```

66776 实际退出 0：609 passed / 18 forkpty warnings / 33.74s；四个相关文件
ruff check/format 通过。独立临时资源，4 workers 控制 PTY/原生子进程并发。
未完成：菜单上的审批栈暂停/恢复（仍为串行等待）；完整按键活动分类等价
（当前基于编辑结果，历史替换也会延迟，空文本上的无效果编辑键不会延迟）。
这两项保留为具体差异，不以本轮空闲保护证据宣称完整 F4 已对齐。

### CLI-011：空闲菜单挡住后到审批（已修复模型菜单叠加/恢复）

Codex bottom_pane/mod.rs:1635–1670 在没有最近 composer 活动时直接 push_view，
审批结束 pop 后恢复下面的菜单。Corki 原先一律串行等菜单退出；3841 红测退出 1，
2 failed，审批始终无法在菜单关闭前显示。

新增 menu_overlay.py：结束当前 menu prompt 的读取与终端清理后，再运行独立
审批 reader；保留菜单 Document（文字/光标）、高亮和错误状态，审批结束后恢复。
审批撤回取消并 join 其 reader；关闭整个菜单会取消未完成审批 Future。所有步骤
仍由 InputOwner 的同一输入所有权保护，不并发读取终端，不发布半套模型设置。
最近 composer 活动导致延后的请求仍按既有延迟路径等菜单关闭，未改为一律抢焦点。

单元/pipe 验证筛选文本和非首项选择恢复、独立拒绝、请求撤回、整个菜单取消；
真实 runtime/原生授权 PTY 的 40/100 列验证空闲菜单被审批覆盖、独立批准前没有
副作用、批准后真实执行、恢复菜单确认后才更改模型。70163：9 passed；2496：
22 passed / 12 forkpty warnings / 28.69s；六文件 ruff check/format 通过。

```sh
CORKI_TEST_SANDBOX_COMPILER=/Users/corki/PycharmProjects/corki/src/corki/_native/sandbox/corki-sandbox TERM=xterm-256color .venv/bin/pytest tests/unit/cli tests/integration/test_cli_input_ownership.py tests/integration/test_cli_model_command.py tests/integration/test_cli_plan_command.py tests/e2e/test_model_approval_pty.py tests/e2e/test_model_effort_pty.py tests/e2e/test_queued_input_pty.py tests/e2e/test_execution_lifecycle_pty.py -n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' -o faulthandler_timeout=30 -q --tb=short
```

17661 实际退出 0：630 passed / 28 forkpty warnings / 33.36s，4 workers，离线。
覆盖模型菜单叠加，不代表所有模态组合完成；连续多个后到审批、effort 子菜单中
叠加、运行中其他问答组合仍缺专项证据。F1–F6 活跃表已刷新。下一批回到 F3/F1
流式输出与实际屏幕验收，保留这些具体边界，不无限枚举菜单排列或恢复核心 A–E。

### CLI-012：流中断丢失未换行尾部（已修复）

取消/失败/retry 原来直接 end_assistant_message，清空 _assistant_pending；已收到
但未换行的中文尾部从输出与回放消失。89259 红测退出 1：3 failed，实际尾部计数
为 0。参考 Codex chatwidget/streaming.rs::flush_answer_stream、streaming/controller.rs
::finalize_remaining：清除 live tail 前从已收源文本完成显示收尾，不要求最终消息。

Transcript 提供当前流源文本；TerminalUI.interrupt_assistant_message 复用显示收尾，
应用在中断/失败/取消、流直接抛异常时调用。只保存已经收到的显示内容，不提交
runtime 成功状态；原失败/取消/retry 提示仍保留。直接异常清理追加红测 80709：
1 failed / 3 passed，随后统一 finally 的收尾路径，保留原始异常传播。
真实 runtime + PTY（40/100 列）验证无尾换行的中文/emoji、未闭合代码块，Ctrl+C
后尾部保留，后续对话可用，回放仅一份尾部和中断提示，正常退出。

另修正旧 test_stream_repair_pty 的 fixture：它显式注入 Rich Console，绕过当前
产品 TerminalConsole 的物理终端尺寸处理。11275：5 failed / 13 passed（窄屏代码
折行和 resize）；仅将 fixture 改为 TerminalConsole(color_system=None)，保持尺寸/
折行/缩放断言，54250：18 passed / 22.24s。不是放宽超时，也不是新生产尺寸修复。
仍保留完整 VT 屏幕快照与视觉验收待办；本批 PTY 字节/源回放不证明没有闪烁。

```sh
TERM=xterm-256color .venv/bin/pytest tests/unit/cli tests/integration/test_cli_input_ownership.py tests/e2e/test_stream_interruption_pty.py tests/e2e/test_stream_table_pty.py tests/e2e/test_stream_repair_pty.py -n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' -o faulthandler_timeout=30 -q --tb=short
```

18742 实际退出 0：626 passed / 22 forkpty warnings / 24.04s，4 workers，离线；
六文件 ruff check/format 通过。此前 86137 的 608 passed 是补直接异常路径前的结果。
下一步继续 F1/F3 终端最终画面和输出重绘检查；核心 A–E 继续暂停。

### CLI-013：相同内容完成时不必要的全屏清空（已修复可追加路径）

原 complete_assistant 对多行或 Markdown 无条件要求修复，正常完成也发送 3J/2J
并重绘整个 transcript。Codex chatwidget/streaming.rs:24–85 区分 Required 和
IfResizeReflowRan，streaming/controller.rs::finalize_remaining 只追加未输出行。
42555 红测：3 failed，中文两行、内联样式、代码块都不必要地调用 repair。

StreamMarkdown 记录真正提交成功的 styled rows；完成时重新渲染最终源，逐行比较
已输出的文本与样式，只在前缀完全相同时补剩余行。最终源变化、表格可变尾部、
缩放已重排、插入 notice、早期样式被后续 Markdown 改变等仍走完整修复。输出失败
不会登记成功行；队列/输出计数不一致时拒绝追加路径。只改 CLI 显示，不改模型协议。

新增正/反单测检查直接输出与源回放一致、样式变化/交错通知/resize 仍修复；物理
PTY 的相同内容完成用例改为明确禁止 3J/2J，同时保留文本仅一份、嵌套缩进和退出
断言。不是删除正确性断言来绕过失败；源变更及 resize 的原修复用例保持不变。

```sh
TERM=xterm-256color .venv/bin/pytest tests/unit/cli tests/e2e/test_stream_interruption_pty.py tests/e2e/test_stream_table_pty.py tests/e2e/test_stream_repair_pty.py -n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' -o faulthandler_timeout=30 -q --tb=short
```

35397 实际退出 0：615 passed / 22 forkpty warnings / 22.15s，4 workers；五文件
ruff check/format 通过。该证据证明消除这些场景的整屏清空，不冒称所有终端均无
闪烁；真实 VT 最终画面/交错工具输出仍需验收。下一批检查工具输出与 composer
同时活动时的呈现和错误收尾，避免只围绕同一完成路径继续枚举测试。

### CLI-014：工具分片被强制断行、产生额外空行（已修复）

Codex chatwidget/command_lifecycle.rs:54 将 delta 按 call_id 交给 ExecCell.append_output，
不为传输分片另造换行。Corki 原每个 ToolOutputDelta 直接 Console.print：hel/lo
变成两行，世界\n 多出空行。98266 红测 2 failed，输出明确显示分片被拆散。

应用显示状态现按 tool_call_id 保存未完成行，完整行及时输出；完成、失败、中断和
同工具的 PlanUpdated 边界排空尾部。TerminalUI 不再给已带换行的输出加第二个换行。
不改变 runtime 工具结果、授权或模型上下文。单测覆盖两个调用交错、空行保留、失败/
取消前的尾部及回放一致；47108：112 passed。真实 runtime/离线工具/PTY 在 40/100
列验证成功及失败结果显示时草稿仍可编辑、提交，执行一次、失败标识正确、正常退出。

95509 的既有 interleaved PTY 2 failed：仍等待 CLI-013 已消除的整屏清空。更新为
禁止 3J/2J，并保留正文完成前不能显示工具结果、输出顺序、各内容仅一份的断言；
fixture 改用产品 TerminalConsole。16272：6 passed / 6 forkpty warnings / 9.18s。
分片边界由事件测试覆盖；该真实 runtime fixture 返回完整工具结果，不冒称其生成
了传输分片。尚未实现 Codex 的所有并发工具分组/折叠呈现。

```sh
TERM=xterm-256color .venv/bin/pytest tests/unit/cli tests/integration/test_cli_input_ownership.py tests/e2e/test_tool_composer_pty.py tests/e2e/test_interleaved_tool_pty.py tests/e2e/test_stream_interruption_pty.py tests/e2e/test_stream_table_pty.py tests/e2e/test_stream_repair_pty.py -n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' -o faulthandler_timeout=30 -q --tb=short
```

7478 实际退出 0：640 passed / 28 forkpty warnings / 23.54s；五文件 lint/format
通过，4 workers、隔离临时资源。下一批补实际 VT 屏幕快照验收，不能一直以输出
字符串包含关系替代画面质量；保留 F1–F6 未完成项，核心 A–E 继续暂停。

### CLI-015：真实屏幕里工具结果落在回复之后（已修复）

给现有真实 runtime/PTY 测试加 dev 依赖 pyte==0.8.2 的 VT 屏幕重建。40/100 列、
工具成功/失败各一轮，在工具结束和双向 resize 后分别核对实际屏幕矩阵：工具
输出/中文/尾部各一份、失败标识与实际结果一致、非空输入草稿仍在、行宽受限。
初次 16271 4 failed 是测试把已提交草稿和恢复中的草稿都算重复；屏幕显示两处
各有意义，调整为检查最后的可编辑草稿，不丢弃内容断言。

随后增加顺序断言，73440 4 failed：最终屏幕把 Response complete. 放在
TOOL_TAIL 前，虽然内部 Transcript 回放顺序正确（40778 4 passed）。根因是
prompt-toolkit 的 StdoutProxy 异步刷写，应用继续消费后续助手事件时工具输出仍在
代理队列。借用已有 commit_delta 的终端所有权与原子写入路径，等待工具显示
提交完毕再处理下一事件；包括正常事件、关闭和异常清理。没有变更工具执行顺序
或模型协议。25971：133 passed / 4 forkpty warnings / 9.53s，最终与 resize 后的
真实屏幕均满足“工具结果在回复前”；内部回放断言同时保留。

```sh
CORKI_TEST_SANDBOX_COMPILER=/Users/corki/PycharmProjects/corki/src/corki/_native/sandbox/corki-sandbox TERM=xterm-256color .venv/bin/pytest tests/unit/cli tests/integration/test_cli_input_ownership.py tests/e2e/test_tool_composer_pty.py tests/e2e/test_interleaved_tool_pty.py tests/e2e/test_stream_interruption_pty.py tests/e2e/test_stream_table_pty.py tests/e2e/test_stream_repair_pty.py tests/e2e/test_queued_input_pty.py tests/e2e/test_model_approval_pty.py tests/e2e/test_execution_lifecycle_pty.py -n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' -o faulthandler_timeout=30 -q --tb=short
```

54385 实际退出 0：658 passed / 52 forkpty warnings / 41.29s；相关代码 lint/format、
diff check 通过。pyte 仅加入 dev extra，uv.lock 新增 14 行；恢复 all-extras 后
可选 code-mode 依赖仍在。当前只验证这一类终端画面，F1/F3 其他状态尚未完成。

### CLI-016：窄终端状态栏超宽（已修复）

空闲提示、Plan 模式、中文 reasoning 标题和 hook 状态可能超过终端列宽；原代码
仅有部分分支按字符数裁剪，中文的实际显示宽度更大。新增 12/20/40 列、四种
状态的失败回归测试后，统一按终端显示单元裁剪所有状态栏片段，并让空闲提示
和 Plan 状态按可用宽度选择完整短提示，避免显示半截快捷键。
没有扩建命令或新增服务入口。

验证：`TERM=xterm-256color .venv/bin/pytest tests/unit/cli tests/e2e/test_cli_plan_pty.py tests/e2e/test_cli_pty.py -n 4 --dist=loadfile --max-worker-restart=0 -q -o addopts='' --tb=short`
最终修改后实际退出 0，621 passed、14 条既有 forkpty 警告、20.58s；ruff check/format 与
diff check 通过。首次未设置 TERM 的相关样式测试 1 failed（ANSI 样式为空），
补全终端环境后通过；其失败与状态栏代码无关。下一项按基本使用优先级核对
长中文实际屏幕和普通输入/粘贴，不再追逐斜杠命令差异。

### CLI-017：多行中文粘贴、resize 与显式提交（已验证）

新增真实 PTY/VT 用例：40/100 列粘贴含中文、换行、emoji 的草稿，确认粘贴
本身不提交；双向 resize 后最终屏幕仍有完整两行且不越界；按 Enter 后只提交
一次，输入历史保存原文。`TERM=xterm-256color .venv/bin/pytest
tests/e2e/test_composer_paste_pty.py -q -o addopts='' --tb=short` 实际退出 0，
2 passed / 3.22s。未发现此路径的产品缺陷，因此未改生产代码。
下一项检查普通输入焦点和错误恢复，不扩建命令体系。

### CLI-018：历史加载失败时启动直接抛异常（已修复）

复现：会话历史读取失败时，`CorkiApplication.run()` 从加载阶段直接抛出底层异常，
用户没有稳定的错误提示和退出状态。修复后在恢复/读取/回放历史的入口捕获普通
异常，只记录异常类型；显示“无法加载会话历史、没有开始新 Turn”，返回 1，
不调用 `resume_pending`、不开放普通输入，原有 finally 仍关闭 pager、后台任务
和 runtime。历史不确定时不会静默打开新对话，异常私有内容不进入 UI。

先加入失败单测，原实现因 OSError 直接逃逸而 1 failed；修复后单测、历史分页、
回放及真实 PTY（40/100 列的分页读取失败）均通过。最终命令：
`TERM=xterm-256color .venv/bin/pytest tests/unit/cli/test_application.py tests/integration/test_cli_paged_history.py tests/integration/test_cli_history_replay.py tests/integration/test_cli_pending_history.py tests/e2e/test_cli_pty.py tests/e2e/test_history_view_pty.py -n 4 --dist=loadfile --max-worker-restart=0 -q -o addopts='' --tb=short`
实际退出 0：154 passed、26 条既有 forkpty 警告、31.06s。相关文件 ruff
check/format 与 diff check 通过。下一项检查构造阶段的可理解报错和退出资源边界。

### CLI-019：构造阶段 I/O 失败打印底层 traceback（已修复）

入口 `main()` 在 `build_application_async()` 抛出非 ValueError 的构造异常时，
原先直接传播，用户可能只看到 Python traceback 和私有底层异常内容。
新增 `OSError("PRIVATE_STORAGE_DETAIL")` 故障注入：修复前 1 failed，
修复后 stderr 仅显示 `Corki could not start (OSError)` 与配置/本地存储检查建议，
退出码 1，不输出私有异常字符串。构造器的异步回滚仍完成，原有参数错误、
启动取消和运行中异常语义不变。此次仅处理尚未进入应用主循环的构造阶段。

验证：`TERM=xterm-256color .venv/bin/pytest tests/integration/test_cli_construction.py tests/unit/cli/test_main.py tests/e2e/test_cli_pty.py -n 4 --dist=loadfile --max-worker-restart=0 -q -o addopts='' --tb=short`
实际退出 0：16 passed、8 条既有 forkpty 警告、20.39s；ruff check/format、
diff check 通过，测试进程已退出。下一项继续检查连接错误后的可用状态和
终端提示，不扩建命令菜单。

### CLI-020：无正文重试也提示“回答被打断”（已修复）

用离线真实 runtime 注入连接失败：立即失败或重连一次后仍失败，随后提交第二条
消息成功。修复前即使模型未输出正文，`AssistantMessageInterrupted` 也显示
`Response interrupted; retrying with updated history...`，与实际状态不符；
40/100 列 PTY 首次验证发现这一差异。新增先失败的行为单测后，只在本次重试
前确有可见 reasoning/正文/计划内容时显示“回答被打断”；每次中断后重置这份
可见状态，不影响原有“半截输出后重试”提示。重连次数、最终错误和后续输入保留。

验证：`TERM=xterm-256color .venv/bin/pytest tests/unit/cli/test_application.py tests/e2e/test_connection_recovery_pty.py tests/e2e/test_stream_interruption_pty.py tests/integration/test_connection_retry.py -n 4 --dist=loadfile --max-worker-restart=0 -q -o addopts='' --tb=short`
实际退出 0：156 passed、8 条既有 forkpty 警告、9.94s；ruff check/format、
diff check 通过，所有测试进程已退出。真实供应商连接未测，也不据此推断模型质量。

### CLI-021：模型错误正文在实时与冷恢复时可能显示凭据（已修复已知形式）

模型适配器可能将供应商错误正文或传输异常文字放入重连事件/失败 Turn；原 CLI
直接显示。用假的模型 key 与 Bearer token 做失败回归：实时重连原因和最终错误
会暴露它们；随后发现历史回放也直接显示持久化失败文本，重启后再次暴露。
新增共享的显示投影，对当前配置的模型 key 做精确替换，并遮蔽常见 Bearer 凭据；
同时剔除控制字符、保持错误说明和 4000 字符上限。实时通知、最终错误、外层
运行错误和失败历史回放统一使用；不改变模型请求、持久化失败事实或 provider 路由。
这不声称能识别所有任意格式的供应商秘密，范围是已配置 key 与常见 Bearer 形式。

单测先后复现实时和冷回放两个失败；扩展真实 runtime 冷失败 PTY，验证 40/100
列重启后终端原始输出及 transcript 都没有假凭据。最终命令：
`TERM=xterm-256color .venv/bin/pytest tests/unit/cli tests/integration/test_cli_history_replay.py tests/integration/test_cli_paged_history.py tests/e2e/test_failed_history_pty.py tests/e2e/test_connection_recovery_pty.py -n 4 --dist=loadfile --max-worker-restart=0 -q -o addopts='' --tb=short`
实际退出 0：630 passed、6 条既有 forkpty 警告、13.04s；ruff check/format、
diff check 通过，测试进程已退出。真实服务与真实凭据未使用。

### CLI-022：非空草稿 Ctrl+D 与新模型错误记录（已验证/修复）

先用 40/100 列真实 CLI 验证 Ctrl+D：草稿 `/status` 未提交时按键不退出，
草稿可继续提交；输入为空后再次 Ctrl+D 才正常退出。此处未发现产品缺陷。

CLI-021 的剩余边界是错误在模型适配器到达 CLI 前已持久化：换 key 后，旧记录
若未使用 Bearer 格式，显示层无法知道旧 key。新增离线 HTTP MockTransport
故障注入，两个普通兼容协议在 400 最终失败、500 重试失败时均先复现假 key
进入失败事实；随后在适配器发出重试事件或错误前按同一显示规则清理 key，
保留错误 kind、retryable、HTTP status 与 retry-after，不改变 provider 路由或请求。
新写入的 ModelFailure 与 TurnFailed、ModelRetryScheduled 不含假 key；既有存档
没有被改写，也不声称能识别任意无标识的历史秘密。

最终验证：`TERM=xterm-256color .venv/bin/pytest tests/unit/cli tests/integration/test_http_retry_layers.py tests/integration/test_response_errors.py tests/integration/test_connection_retry.py tests/e2e/test_cli_pty.py tests/e2e/test_failed_history_pty.py -n 4 --dist=loadfile --max-worker-restart=0 -q -o addopts='' --tb=short`
实际退出 0：712 passed、12 条既有 forkpty 警告、29.29s；相关 ruff
check/format、diff check 通过。真实服务、用户密钥均未使用。

### CLI-023：重连中输入下一条草稿（已验证）

扩展现有离线 runtime/PTY 故障注入：首条消息遇到连接重试，等待阶段让用户键入
`draft`，再释放第二次失败；确认失败 Turn 后实际 VT 屏幕仍有可编辑的
`› draft`，并未自动提交或清空。随后按 Enter，下一 Turn 成功，输入历史
恰有 `first`、`draft` 两条。40/100 列和立即失败、普通重试场景一起验证：
`TERM=xterm-256color .venv/bin/pytest tests/e2e/test_connection_recovery_pty.py -q -o addopts='' --tb=short`
实际退出 0：6 passed、12.04s；ruff check/format 通过，无测试进程残留。
此路径未发现产品缺陷，未改生产代码；真实供应商连接仍未验证。

### CLI-024：工具/模型输出可向终端注入控制序列（已修复）

复现：将 `ESC[2J`、单独回车和 BEL 放入假工具结果、调用预览及模型正文；
Rich 即使关闭 markup/highlight 也会原样输出 `ESC[2J`，可清屏或移动光标。
先加入失败测试确认原始终端字节含 ESC。修复在 CLI 显示层把 C0/C1 控制
字符变为可见的 `\\xNN`，保留普通换行、制表符和中文/emoji；CRLF 按普通
换行显示。覆盖工具结果/预览、Markdown 完整与流式缓存、reasoning、计划、
通知和 hook 输出，显示 transcript 回放走同一投影。原始工具结果与模型上下文
不修改；历史持久化未重写。工具输出原有 4000 字符显示预算保持不变。

验证：`env -u NO_COLOR TERM=xterm-256color .venv/bin/pytest -q tests/unit/cli
tests/integration/test_cli_history_replay.py tests/e2e/test_stream_repair_pty.py
-n 4 --dist=loadfile --max-worker-restart=0 -o addopts='' --tb=short` 实际退出 0：
635 passed、14 条既有 forkpty 警告、25.16s。相关 ruff check/format 和 diff
check 通过。首次在宿主 `TERM=dumb`、`NO_COLOR=1` 下运行时，既有历史样式
测试因终端禁用颜色而失败；换成可用色彩终端环境后同一测试通过。未调用真实模型。
下一项检查常见长工具结果的实际屏幕与继续输入体验。

### CLI-025：长工具结果后的回看和继续输入（已验证）

新增真实 runtime + PTY 用例：假工具一次返回约九千字符、300 行，包含首尾标记
和清屏序列；模型确实收到完整工具内容，CLI 只显示有省略提示的有界首尾片段。
40/100 列下确认终端原始输出没有工具注入的 `ESC[2J`，答复仍在最终屏幕；
之后键入未提交草稿，进入历史视图回看长结果开头，返回后草稿仍在，提交下一条
消息得到独立答复，最后 Ctrl+D 正常退出。原始模型上下文和输入历史分别验证。

首轮测试脚本误读 `ToolResultItem.result` 导致 fixture 自身报 AttributeError；
改为正确的 `ToolResultItem.content` 后通过，没有改动生产代码。
验证命令：`env -u NO_COLOR TERM=xterm-256color .venv/bin/pytest -q
tests/e2e/test_long_tool_output_pty.py tests/e2e/test_interleaved_tool_pty.py
tests/e2e/test_history_view_pty.py -n 4 --dist=loadfile --max-worker-restart=0
-o addopts='' --tb=short`，实际退出 0：22 passed、22 条既有 forkpty 警告、
31.21s。新测试 ruff check/format、diff check 通过；测试进程已退出。
此路径未发现产品缺陷。下一项优先核对常用终端布局/状态提示的小问题。

### CLI-026：会话标题和已提交输入仍能注入终端控制序列（已修复）

CLI-024 已保护工具/模型输出，但检查基本布局时发现启动标题直接显示配置中的
模型名/工作目录，已提交输入的历史回放也直接显示原文；假 `ESC[2J` 可进入
终端原始输出。先加入失败的显示测试，再把这两个入口、确认通知及工具失败标题
统一走现有显示层转义。用户输入、配置值、请求和持久化内容仍保留原文。

另加真实 PTY：40/100 列使用 bracketed paste 粘贴 `draft` + 清屏序列 + `text`，
实际提交内容保留原文，终端原始输出不含可执行的清屏序列，历史回放显示可见
转义文本。首次 PTY 断言误把 Rich 自身的合法样式 ESC 也禁止，已收窄为只禁止
注入的 `ESC[2J`，没有放宽对危险控制序列的检查。

验证：`env -u NO_COLOR TERM=xterm-256color .venv/bin/pytest -q tests/unit/cli
tests/integration/test_cli_history_replay.py tests/e2e/test_stream_repair_pty.py
tests/e2e/test_composer_paste_pty.py tests/e2e/test_cli_pty.py -n 4 --dist=loadfile
--max-worker-restart=0 -o addopts='' --tb=short` 实际退出 0：650 passed、28 条
既有 forkpty 警告、26.21s；相关 ruff check/format 与 diff check 通过，测试
进程已退出。未使用真实模型或用户密钥。下一项检查基本状态提示的清晰性。

### CLI-027：安静等待回复时没有忙碌提示（已修复）

正常输入已提交、模型尚未输出时，原底栏仍是空闲帮助文案；在不支持 CPR 的
终端里，prompt-toolkit 内建底栏还会完全隐藏。Codex 本地
`codex-rs/tui/src/chatwidget/status_state.rs` 的默认活动标题是 `Working`，
其状态行由 `status_indicator_widget.rs` 独立呈现。本次只采用轻量状态文本，
没有扩建动画/计时器或命令体系。

新增 Turn 活动标志，事件消费开始显示 `Working...`，reasoning/hook 的更具体
状态保持优先；任何完成、失败或取消路径均在 finally 恢复空闲文案。把状态行
放进 Corki 自有的 composer 布局，避免内建底栏在无 CPR 时消失；输入结束时
该行不继续占位。真实 PTY 用安静等待的离线模型验证 40/100 列状态出现、
回答后最终屏幕不残留 Working，草稿取消和正常退出仍可用。

首轮全回归暴露两个问题：部分单测用未构造 `_session` 的 UI 验证渲染，已让
状态更新在该场景安全跳过 invalidate；连接恢复 PTY 把有颜色的原始 ANSI
字节当纯文本比较，已改为解析 ANSI 后再核对相同文字与次数，没有删除错误
可见性断言。修正后命令：`env -u NO_COLOR TERM=xterm-256color .venv/bin/pytest
-q tests/unit/cli tests/integration/test_cli_history_replay.py
tests/e2e/test_working_status_pty.py tests/e2e/test_connection_recovery_pty.py
tests/e2e/test_stream_repair_pty.py tests/e2e/test_composer_paste_pty.py
tests/e2e/test_cli_pty.py tests/e2e/test_history_view_pty.py -n 4
--dist=loadfile --max-worker-restart=0 -o addopts='' --tb=short`，实际退出 0：
677 passed、54 条既有 forkpty 警告、33.15s。ruff check/format 与 diff check
通过，测试进程已退出。真实供应商/密钥未使用；下一项看工具与审批状态的
提示优先级，避免忙碌文本掩盖等待用户的状态。

### CLI-028：并发审批回归用例等待不可能出现的状态（已修正测试）

在 CLI-027 后专项运行审批/用户问答 PTY，普通审批均通过，并发审批的 12
个组合却一直等待。对单用例临时恢复旧底栏仍同样失败，排除了新状态行的
布局回归；终端记录显示两个审批表单已按顺序出现，第二次决定后恢复草稿。
根因是旧 fixture 等待 `InputOwner._modals == 2`，但当前 `InputOwner.elicit`
持有审批锁，活动表单最多为 1；主协程永远等不到 2，也就不会打印完成标记。
改为等待第一个活动表单，并断言第二请求尚未完成；保留“第二审批不得抢先”、
选择/拒绝/取消身份、表单历史为空、普通草稿恢复等原断言。另在并发审批
fixture 中模拟活动 Turn 状态，确认表单交接与忙碌状态撤销不冲突。没有修改审批
授权或生产逻辑。

验证：`env -u NO_COLOR TERM=xterm-256color .venv/bin/pytest -q
tests/e2e/test_approval_pty.py tests/e2e/test_user_input_pty.py -n 4 --dist=load
--max-worker-restart=0 -o addopts='' --tb=short`，实际退出 0：68 passed、68 条
既有 forkpty 警告、36.76s；ruff check/format 与 diff check 通过，测试进程已退出。
活动 Turn 状态下的 12 个并发审批组合单独重跑：12 passed、12 条既有警告、
10.04s。
这证明本批覆盖的表单交互，不代表所有真实外部权限请求已完整验收。

### CLI-029：工具执行期间缺少明确状态（已修复）

模型发出工具调用后，工具标题虽会写入历史，但输入区底栏仍可能只显示泛化的
`Working...`，用户无法判断当前是在等模型还是等工具。按工具调用 ID 跟踪开始和
完成事件：单工具显示 `Running <工具名>`，并行工具显示数量；Turn 完成、失败或取消
时统一清除。工具名仅作为显示文本，并经过终端控制字符转义；不更改实际工具调用。

先加单测覆盖单/多工具状态和 Turn 结束清理，初次因方法尚不存在而失败。再用
真实 runtime + PTY 的延迟假工具验证 40/100 列：工具未返回时屏幕确有状态，
返回后最终屏幕无残留，下一条草稿可取消并正常退出。首轮较大回归暴露一个
`TerminalUI.__new__` 轻量输出测试未初始化 `_turn_active`，状态清理已兼容该场景；
没有删除原测试或放宽断言。

最终验证：`env -u NO_COLOR TERM=xterm-256color .venv/bin/pytest -q
tests/unit/cli tests/integration/test_cli_history_replay.py
tests/e2e/test_working_status_pty.py tests/e2e/test_interleaved_tool_pty.py
tests/e2e/test_long_tool_output_pty.py tests/e2e/test_connection_recovery_pty.py
tests/e2e/test_approval_pty.py -n 4 --dist=load --max-worker-restart=0
-o addopts='' --tb=short`，实际退出 0：702 passed、78 条既有 forkpty 警告、
48.41s。相关四个文件 ruff check/format 通过；未使用真实模型或凭据。
下一项继续检查基础退出/错误状态与常见终端显示，不扩建斜杠命令。

### CLI-030：端到端终端配置与实际 CLI 不符（已修正测试并全量验证）

上一轮 702 项相关回归通过，本轮使用本地 native sandbox compiler 额外验证真实
执行审批链：允许、拒绝、长工具中断、后续输入、历史和退出，以及模型菜单与审批
交接，14 passed。随后首次运行全部 `tests/e2e`，实际退出 1：275 passed、9 failed。
失败集中于计划 resize 和浅色审批详情；未将整批误报为通过。

调查发现计划 PTY fixture 注入普通 Rich `Console`，其尺寸受继承终端提示影响，
与产品当前使用的 `TerminalConsole` 不一致，导致宽度断言与 resize watcher 失真。
改用产品使用的终端尺寸实现，保留无色输出、计划内容、重新绘制和草稿断言。
浅色审批 fixture 设置 `COLORTERM=truecolor`，但 prompt-toolkit 默认仍使用 256 色；
诊断输出证实实际浅色高亮为正确的 256 色序列，并非审批内容缺失。测试显式指定
`PROMPT_TOOLKIT_COLOR_DEPTH=DEPTH_24_BIT` 后继续严格核对真彩色 gutter/body
字节及拒绝语义。未修改生产代码，也未放宽内容或授权检查。

定向三文件 13 passed；最终执行 `env -u NO_COLOR TERM=xterm-256color
CORKI_TEST_SANDBOX_COMPILER=/Users/corki/PycharmProjects/corki/src/corki/_native/sandbox/corki-sandbox
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring .venv/bin/pytest -q tests/e2e
-n 4 --dist=load --max-worker-restart=0 -o addopts='' --tb=short -rs`，实际退出 0：
284 passed、287 条 Python forkpty DeprecationWarning、143.78s。三份测试文件的
ruff check/format 与 diff check 通过。未使用真实模型或用户凭据；CLI 全量端到端
通过不等于核心 A–E 全部完成，也不证明罕见终端和真供应商质量。

### CLI-031：普通模型 HTTP 401/403/404 被反复显示为重连（已修复）

用户此前报告“打不开 Corki”并看到多次 Reconnecting。源码确认普通模型 HTTP
401/403/404 经首次请求后仍标为 `retryable=True`，在模型采样层重复相同请求；
Corki 没有官方账户 token 刷新，凭据、权限或地址错误不会这样自行修复。现将
普通非 5xx 错误标为不可重试；5xx 保留原重试，402 既有拒绝重试边界保持不变。
这是为修复 CLI 可见故障所需的最小模型错误分类改动，不恢复 A–E 全面对齐。

先把现有双协议 HTTP→Runtime→CLI 事件集成测试的 401/403/404 预期改为
一次请求、无 Reconnecting；修复前六项实际失败（各发两次）。修复后增加
40/100 列真实 PTY 的 401 场景：错误可见、最终屏幕无 Reconnecting、底栏恢复、
下一条输入得到答复并可正常退出。40 列下错误按词换行，测试据实际屏幕核对
完整语义，不要求终端输出成为一段连续原始字节。

扩大回归发现旧历史测试曾要求 401/413 同样重发后成功，已按固定错误不可自愈
更新预期；另一测试将普通词 `fixture` 作为 API key，碰巧与错误文本重叠而被
既有安全脱敏，改用专用假 key，未削弱凭据保护。最终命令：
`env -u NO_COLOR TERM=xterm-256color .venv/bin/pytest -q tests/unit/models
tests/integration/test_http_retry_layers.py tests/integration/test_history_retry.py
tests/integration/test_compaction_retry_http.py tests/e2e/test_connection_recovery_pty.py
-n 4 --dist=load --max-worker-restart=0 -o addopts='' --tb=short`，实际退出 0：
432 passed、8 条既有 forkpty 警告、11.57s。未使用真实模型或用户密钥；
真实供应商的余额/账号状态未验证，也不查询官方配额服务。

### CLI-032：终端重绘异常后半截流留在显示状态（已修复）

上一轮普通 HTTP 错误处理已验证，属于进展。本轮用自带沙箱编译器运行全部
`tests/integration/test_cli*.py`，首次结果 128 passed、9 failed，失败均在
`test_cli_stream_cleanup.py`。现有流式实现会把已接收的半截回答整理为一条可回放
消息，旧测试却仍按 `append_assistant_delta`/`end_assistant_message` 的调用次数验收；
先改为核对显示源只有一份、无未结束流及 Runtime 终态后，仍有四个“重绘失败”
组合失败，确认了真实清理缺口，而非简单放宽断言。

修复：正常流式收尾若再次因终端写入/重绘失败，保留原异常，并从已收到的显示源
无写入地收束 transcript 与本地 stream 状态；不把半截回答伪装成完成的 Runtime
Turn，也不吞掉取消。故障注入的写入、flush、重绘和取消组合 10 项通过。

回归命令：`env -u NO_COLOR TERM=xterm-256color
CORKI_TEST_SANDBOX_COMPILER=/Users/corki/PycharmProjects/corki/src/corki/_native/sandbox/corki-sandbox
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring .venv/bin/pytest -q
tests/integration/test_cli*.py tests/e2e/test_stream_repair_pty.py
tests/e2e/test_stream_interruption_pty.py tests/e2e/test_interleaved_tool_pty.py
tests/e2e/test_working_status_pty.py -n 4 --dist=load --max-worker-restart=0
-o addopts='' --tb=short`，实际退出 0：161 passed、24 条既有 forkpty 警告、
21.94s。相关三文件 ruff check/format 与 diff check 通过，测试进程已退出。
另以 4 workers 运行 `tests/unit/cli`，实际退出 0：616 passed、7.54s。
未使用真实模型或用户密钥；物理终端永久写入失败时仍须由宿主终端恢复。

### CLI-033：当前 CLI 基本阶段的验收复核（2026-09-21）

上一轮 CLI-032 修复并通过相关回归，属于进展。最新生产改动后重跑整套
`tests/e2e`：`env -u NO_COLOR TERM=xterm-256color
CORKI_TEST_SANDBOX_COMPILER=/Users/corki/PycharmProjects/corki/src/corki/_native/sandbox/corki-sandbox
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring .venv/bin/pytest -q tests/e2e
-n 4 --dist=load --max-worker-restart=0 -o addopts='' --tb=short -rs`，实际退出 0：
286 passed、289 条 Python forkpty DeprecationWarning、147.66s。含真实 PTY 的
启动、普通/多行输入、流式回复、工具结果、后续输入、审批/拒绝、取消、历史回看、
冷恢复和退出；40/100 列及常见 resize/长内容场景均有覆盖。它不是对任意终端、
真供应商响应质量或绝对零 bug 的证明。

本阶段其余验收证据：`tests/unit/cli` 616 passed；全部 `test_cli*.py` 集成及
关联流式 PTY 161 passed；普通模型错误与历史重试范围 432 passed。
本轮另以 4 workers 跑 patch 详情/MCP 问答/Code Mode/原生 patch 的五个集成文件：
77 passed、1 skipped（仅旧版 native-read-only 编译器兼容性 fixture 未配置）；
实际执行审批和 Code Mode/patch 审批两文件 33 passed、1 skipped（仅旧版
batch214 编译器 fixture 未配置）。这些安全链路测试使用真实 Runtime/权限路由，
不是只点 UI 示范；shell 审批另有真实 PTY。`ruff check`、`ruff format --check`
覆盖 CLI 生产、CLI 单测、CLI 集成及全部 e2e 共 126 文件，全部通过；相关
`git diff --check` 通过。各批测试进程均已实际退出，未使用真实模型或用户凭据。

按当前目标的基本可用范围，F1–F6 的常用闭环与已登记 P0/P1 均有完成证据；
CLI-006 的斜杠候选扩建、CLI-008 的完整 MCP 菜单、CLI-009 的全套设置菜单等
旧计划未完成项，依用户最新指示不再是本阶段完成门槛，不改称为完整实现。
保留的限制：真实第三方模型连接/额度及供应商输出质量未验；旧版编译器兼容测试
两项跳过；forkpty 警告未消除；罕见终端特性、完整 Markdown/主题复刻未验。
A–E 核心进一步对齐按用户要求暂停，未宣称核心全部完成。
