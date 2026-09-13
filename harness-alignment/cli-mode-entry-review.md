# CLI 模式入口：实现前调用链审计

## Default/Plan 活跃请求的模式与停止矩阵

test_stopped_queue_pty.py 现扩展为两种初始模式。模型桩在每次实际请求中检查
mode.plan ContextItem 是否与宿主模式匹配；停止后手动重发的下一 Turn 也必须
保持相同上下文，不能只验证 UI 字符串。保留 Shift+Tab 对照、40/100 列、
Ctrl+C /stop、有/无待消费 steer，32 passed、0 skipped（871996，63.64 秒，
session 60424 退出 0）。无模式发布、无额外请求、队列文本完整及最终关闭
断言均通过。静态通过（305d07，1126 文件/77 包），本轮无生产修改。

源码接点：step_settings.py 保留已选择 collaboration_mode/instructions，
ContextBuilder.build 使用 mode.<kind> 稳定 key 注入 developer 指令。上述测试
证实两个真实主循环请求均到达该路径；不把 scripted model 当真实模型遵循
Plan 指令的质量证据。审批/补全/历史焦点与所有 footer 渲染组合仍未全覆盖。

## 设置提交窗口的物理 Ctrl+C

新增 test_cli_plan_cancel_pty.py：真实 TerminalUI / Application / Runtime，普通
模型桩一旦采样即失败；在 save_thread_model_settings 挂起提交，通过 PTY
sendcontrol("c") 发送真实 SIGINT，而不是在测试中调用 task.cancel。提交任务
观察到主任务 cancelling 后才发 SIGNAL_PENDING_COMMIT，父进程再释放提交，
避免把“提交前后偶然取消”冒充命中持久化窗口。

40/100 列各覆盖提交成功/失败：Runtime join 写入，CLI 根据实际快照同步为
Plan/Default，原多行 /plan 正文完整保留，queue_autosend=false，不显示错误
的 Plan enabled；返回输入区后显示正确模式，用户 Ctrl+D 可正常关闭模型并
以 0 退出。四项通过（0d4f55，5.76 秒），静态通过（1975b8，1126 文件/77 包）。
这是既有修复的额外真实交互证据，本轮未改生产代码。

原生 slash_dispatch.rs 的 Plan 与 inline Plan 分支仍以模式选择后处理正文为
基准；Corki 的异步持久化写入取消由 core/model_settings.py 的 shield/join 和
Application._set_collaboration_mode 的实际快照同步共同实现。该 Python SIGINT
时序测试不声称原生具有同一 SQLite 写入或 asyncio 行为。

最终 CLI 单元/模式集成/模式命令及提交取消 PTY 联合 373 passed、0 skipped，
12 条 forkpty 警告（54bbd1，26.62 秒，session 50063 退出 0）。无活动测试。
仍不以这组证据覆盖 Plan 活跃 Turn、全部焦点状态或整个 F/A–F 的完成。

## 运行中 Shift+Tab 与停止后队列恢复（本轮验证）

原生 chatwidget/interaction.rs 的 BackTab 分支要求 collaboration_modes_enabled、
!is_task_running、no_modal_or_popup_active；不符合时交给普通底部输入处理。
Corki Application 仅闲时 read_message 启用 mode cycle，_consume_realtime_turn
通过共享输入所有者收取运行中输入；TerminalUI 绑定受 _can_cycle_mode 控制。

扩展 test_stopped_queue_pty.py：真实模型保持未结束，在两条草稿末尾发送物理
Shift+Tab 再 Tab 入队；随后用 Ctrl+C 或 /stop 停止。覆盖 40/100 列、有/无
先前待消费 steer。UI 禁止任何模式发布，关闭后 Runtime 仍为 Default，模型
请求必须恰好为 initial 和用户停止后重新 Enter 的完整恢复文本，无额外采样。
保留原不按 Shift+Tab 的对照，共 16 项。此证据覆盖运行中模式键与排队/取消
组合，不替代模式设置提交窗口的物理 Ctrl+C 故障测试。本轮没有生产修改。

结果：session 56854 已退出 0，3f90ec：16 passed、0 skipped，32.27 秒；
Ruff、1125 文件格式、compileall、77 包依赖及 diff 检查通过（82588c）。
无活动测试。这里只关闭上述具体 Default 运行/排队/停止矩阵，不泛称所有实时
输入、Plan 运行、审批焦点和模式提交取消的真实按键均已完成。

## 模式切换提示补齐

原生 chat_composer.rs 的 ActivePopup::None 分支通过 footer_props 的运行状态
及 mode indicator 决定 show_cycle_hint；footer.rs::single_line_footer_layout
按宽度优先保留 mode cycle，放不下时退为 mode-only。Corki 已有 Shift+Tab
绑定，但 _toolbar 未提示该操作，set_mode_cycle_enabled 也没有触发重绘。
方案：绑定和提示共用可用性判断；闲时展示模式及 shift+tab to cycle，宽度不足
退为模式标签，历史/表单/补全/运行中不展示可切换提示。只补此行为，不宣称已
复刻整个原生 footer 的右侧 context、队列和快捷键覆盖层布局。

已接入 _can_cycle_mode 供按键与提示共用；启用状态改变会重绘。20/40/100 列、
Default/Plan、闲时/运行/历史/表单/补全的 30 项直接渲染断言覆盖可见性和闲时
宽度收敛。首次测试六个真实闲时缺口失败，另六个历史 fixture 将实例字段误当
类属性而失败（1c17b7），已纠正 fixture，没有把后者算作生产缺陷。
CLI 单元、模式集成和现有 PTY 组合 369 通过（e6b00a，8 条 forkpty 警告），
全部静态通过（be922b）。独立真实 PTY 对提示文字的验证另行记录，不仅依赖
_toolbar 返回字符串。其他 footer 组合布局、物理取消及运行中键盘矩阵仍开放。

PTY 已补 expect_rendered("shift+tab to cycle")，真实 40/100 列在输入前等到
提示绘制后才执行原有成功/失败/循环/循环失败场景。8 passed（46f977，13.38
秒，session 29561 退出 0），无活动测试。这关闭本项实际提示绘制缺口，不代表
已匹配所有原生 footer 快照。

## 模式循环失败与补全焦点验证

新增 cycle_fail PTY（40/100 列）：成功进入 Plan 后，返回 Default 的设置更新
注入 OSError，终端必须显示 Default mode update failed；随后仍为 Plan、草稿
大小写/换行保留、输入历史为空。手动 Enter 后只采样一次，请求实际使用 medium
及 Plan context，而非按未提交的 Default 运行。UI 模式发布次数只为一次，最终
EOF/exitstatus=0。两种宽度加上既有场景共 8 通过（ccc962）。
tests/unit/cli/test_mode_cycle.py 新增真实 PromptSession completion state 的
Shift+Tab 序列，确认补全菜单下不返回模式控制信号、不提交草稿。五种输入状态
检查通过（bd4b7b）；不是把普通输入或表单同意路径替代成模式切换。
最终 CLI 单元/模式命令/问题集成/命令 PTY 343 通过（62a892，8 条 forkpty
警告），Ruff/1125 文件格式/compileall/77 包与 diff 检查通过（ed43c1）。
本轮仅补验证，无生产修改；物理 Ctrl+C 取消、运行中真实键盘组合和提示仍待验收。
本批属于 A/E/F 的输入/错误边界证据，不替代 B/C/D 与整个 A–F 最终验收。

## Shift+Tab 接入与首批验证

interaction.rs 的 BackTab 仅在 idle/no-modal/no-popup 生效，经 settings.rs 的
cycle_collaboration_mode 与 next_mask 选择下一模式并提交设置；不调用 composer
提交，不写输入历史，Default mask 不覆盖基础 model/effort。Corki 将使用独立的
输入控制信号退出当前 prompt，由 Application 串行等待设置提交，再恢复原草稿。
只在主循环闲时 read_message 期间启用绑定，实时输入、历史查看及表单不启用；
不能用假 `/plan off` 字符串、自动提交草稿或游离异步设置任务替代。
模式更新复用已验证的取消/join 后同步路径；Default 基础来自启动设置或同 UI
进入 Plan 前的快照，内置模式切换清除旧模式自定义指令。

已实现 CycleModeInput 独立控制信号、TerminalUI 的 s-tab 绑定与 Application 的
闲时启用/读取结束 finally 禁用。该信号在通用 submission_text 中为空，实时路径
即使收到也不 steer 或排队。绑定还检查 history/modal/completion popup；退出
prompt 不调用 validate_and_handle，草稿保存且不进入 FileHistory。Application
串行切换 Plan/Default，共用 _set_collaboration_mode 的发布/失败/取消同步逻辑。
Default 选取本 UI 的基础模型与 effort，无新持久化字段、无模型服务发现请求。

实际 PTY 新增 40/100 列“粘贴草稿 → Shift+Tab Plan → Shift+Tab Default → Enter”
序列，通过中间零采样、草稿/换行保留、输入历史为空、最终仅一次采样且 effort
从 Plan medium 恢复 low 的检查（c40411：含原四例共 6 通过）。另有真实
PromptSession 按键测试覆盖 idle、未启用/运行中标记、history 和 modal 禁用。
首轮 history 夹具直接写 active 而没初始化关闭状态导致 1 失败；改为真实 open()
进入历史视图后再回归，未据此修改历史生产实现。
仍需补模式循环写入失败/取消的直接 PTY、补全 popup 与实时 Turn 的组合按键
矩阵及快捷键提示；不将首批循环通过声称为整个 CLI 或 A–F 已完成。
最终组合 CLI 单元/模式/冷恢复/问题/命令 PTY 365 通过（4c82a8，6 条 forkpty
警告），Ruff/1125 文件格式/compileall/77 包及 diff 检查通过（014710），进程已退出。

## 冷恢复来源审计：修正之前“需持久化旧 Default 基础值”的推断

同 TUI 缓存与冷 resume 不同：app/thread_settings.rs 的
apply_thread_settings_to_session 仅在 Default 时更新 session.model/effort，另存
当前 collaboration_mode；session_flow.rs 用这两组分别还原基础值和 active mask。
但 app_server_session.rs::started_thread_from_resume_response →
thread_session_state_from_thread_resume_response → thread_session_state_from_thread_response
将 resume 响应的 model/effort 作为基础值、collaboration_mode=None；session_flow
因此选 Default mask。core/src/session/mod.rs 构造新 SessionConfiguration 时也
显式创建 Default mode、developer_instructions=None。不能把同 UI 的基础缓存
误解为要求数据库永久保留切入 Plan 之前的旧 effort。

Corki config/settings.py 的 for_directory 目前将历史 ThreadModelSettings 的 Plan
及其自定义指令直接当作新会话默认值，这与上述冷启动路径不一致。修复应保留
恢复得到的 provider/model/effort，但新 CLI 会话选择 Default、无旧模式指令。
未完成 Turn 的 checkpoint 快照仍必须保留已入场 Plan，不得为重置新会话默认而
改写已有 Turn 或历史。验收用真实 build_application 冷启动及普通兼容请求，同时
回归既有 checkpoint Plan 恢复测试。无须新建永久 Default 基础字段。

已按此修正 for_directory 的 resume 配置入口。新增真实 build_application 两次
冷启动测试，覆盖普通 Responses/Chat Completions：先保存 Plan + 自定义指令，
改变当前宿主 provider/model 后恢复，仍使用恢复得到的 alpha/large/high，但新
Thread 默认 Default、无旧模式指令；下一 Turn 完成、旧历史保留，请求仅发往配置
的 alpha.invalid。两例先红（d7bd68：实际 Plan 而非 Default）后绿。
组合配置/冷恢复/模式/checkpoint/CLI/PTY 653 通过、1 跳过、4 forkpty 警告
（4d4b43），静态检查全部通过（c0fc07，1124 文件格式、77 包）。既有实际
checkpoint Plan 恢复场景同时通过，不将新默认重置套用到已入场 Turn。
仍未完成 Shift+Tab 循环绑定及其闲时/焦点矩阵；先前下文“冷恢复基础值未实现”
与永久保存旧 Default 的推断由本节证据替代。完整 A–F 仍未完成。

## 发布取消窗口：已修复已启动提交的成功/失败分支

原生 slash_dispatch 的 Plan inline 分支在不可提交时恢复或排队输入；Corki 的
run 在取消 `_enter_plan_mode` 时直接进入外层取消处理，尚未入场的正文丢失。
另一个边界是 ThreadSettingsController.update 会 shield 并 join 已启动的提交，
然后重抛取消：取消返回并不意味着设置回滚。当前 helper 未读取最终快照，可能
数据库/Runtime 已为 Plan 而 CLI 仍显示 Default。修复应在该窗口保留完整命令、
暂停自动重发并继续传播取消；join 结束后以实际快照同步 UI，不能假定提交成功或
失败。测试需覆盖取消期间提交成功和失败，并确认没有模型采样或后台写入遗留。

实现：Application.run 在进入模式未正常返回前保有完整命令；finally 将其放回
待处理队列并暂停自动重发，入场后的 Turn 取消不经过这段恢复。helper 在 Runtime
join 完成后读取实际 thread_settings，刷新命令状态和 UI；仅实际进入 Plan 才保存
Default 基础快照，随后重抛 CancelledError，不显示成功提示也不提交正文。
这保持原有 CLI 外层 Ctrl+C 中断/重新输入语义，不把取消当工具错误或成功。

tests/integration/test_cli_plan_command.py 新增两例先红（24468e，均为命令
丢失）后绿：真实 Runtime 设置保存被挂起，取消调用者时任务仍等待；释放后覆盖
成功发布和失败保持 Default，两条分支都零采样、命令原样保留、UI 与最终快照
一致、正常关闭。CLI/Thread 设置/PTY 组合 352 通过（2545d1，4 条 forkpty
警告）；Ruff、1124 文件格式、compileall、77 包与 diff 检查通过（084127）。
本批未执行物理 Ctrl+C PTY 故障注入；不声称取消所有指令时序均已穷尽。
模式循环与冷恢复基础设置仍待实现，完整 A–F 未完成。

## 直接终端验证（优先于下文历史待验收项）

新增 tests/e2e/test_cli_plan_pty.py：实际 TerminalUI composer → Application.run →
Runtime，在 40/100 列键入裸 `/plan`，再以 bracketed paste 输入多行正文。
裸命令后模型调用数为零，带正文只采样一次，模型收到保留大小写/换行的正文、
Medium effort 和 Plan context；成功发布后更新 indicator，并检查终端输出确有
Plan mode。设置写入失败两种宽度均不采样、不更新模式，保留两条原命令且不自动重发。
末尾 Ctrl+D 正常退出，所有子进程检查 EOF 和 exitstatus=0。

首轮 2 失败/2 通过（e5c0b5）暴露的是夹具禁用 CPR 导致 prompt-toolkit 隐藏
整个 toolbar；改为测试端响应光标位置查询后 4 通过（b50eb0），未修改生产逻辑。
这是真实 PTY 输入/输出验证，不是完整屏幕布局快照，也不证明真实模型选择质量。
CLI 单元、模式/问题集成及两类 PTY 组合 339 通过（26c43d，6 条 forkpty 警告）；
Ruff、1124 文件格式、compileall、77 包兼容和 diff 检查通过（224bab）。

下一步源码边界已确认：chatwidget/interaction.rs 的 BackTab 仅在 idle 且无 modal/
popup 时调用 cycle_collaboration_mode；blocks_direct_input 拒绝父级拥有输入的
场景。tests/plan_mode.rs::collab_mode_shift_tab_cycles_only_when_idle 验证循环
不改 Default 基础设置且运行中不切换。Corki 的该快捷键、冷恢复基础值和发布取消
窗口仍未完成，不能以本批直接 `/plan` PTY 通过宣称完整模式交互对齐。

## 首批入口实现（未完成整个模式交互）

已接入 CommandAction.PLAN 与保留原文的 input_text；裸 `/plan` 仅发布设置，
不采样模型。带参数的命令先等待 update_thread_settings 完整提交，再将正文作为
普通用户输入发送一次；`/plan off` 是正文 off，不自造退出开关。Plan 默认 Medium，
可通过本地 plan_mode_reasoning_effort 配置覆盖；保留当前模型，不按 provider 分支。
TerminalUI 增加 Plan indicator，/status 从成功发布的当前设置组刷新模式和模型。

活动输入循环明确拒绝即时 `/plan`，不调用 steer、不改已入场 Turn。显式排队输入
仍按已有下一 Turn 语义处理。提交异常时不改 indicator，原命令进入可编辑待处理
队列并暂停自动重发，不把失败文本当作模型消息；这是 Corki 当前草稿恢复接点，
并未声称等同原生所有 composer 回填细节。首次端到端测试暴露应用未保存配置引用，
已补保存，确保正确选取 Plan effort 而非所有进入操作失败。

从 Default 入场时保存基础快照供后续循环模式接入；**返回 Default 的快捷键与
冷恢复基础值仍未实现**，不要把这份保存值描述为已经完成模式循环。发布取消窗口
的输入恢复、真实键入 `/plan` 的 PTY 指示器与 mode-cycle 整体还需验证。整个 A–F
仍未完成。本批现有 PTY 是问题面板回归，不冒充 `/plan` 键入的直接证据。

参考本地 Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc；普通 Harness/CLI
范围，无需模型 catalog 服务或官方账户。

## 已确认的行为

- slash_dispatch.rs::apply_plan_slash_command 获取本地可用 plan_mask，经
  set_collaboration_mask_from_user_action → set_collaboration_mask →
  submit_collaboration_mode_settings_update 发布完整模式设置。裸 `/plan` 进入 Plan，
  不是 toggle；循环模式是另一个 cycle_collaboration_mode 入口。
- slash_command.rs::available_during_task 将 Plan 列为 false。Corki 不能把活动
  Turn 中的 `/plan` 当作普通 steering 发给模型，也不能仅改变未来默认后声称当前
  Turn 已切换。
- slash_dispatch.rs 的带参数 Plan 分支先设置模式，再 prepared_inline_user_message
  保留参数正文；配置就绪后使用 ShellEscapePolicy::Disallow 提交。不可用或尚未
  配置的路径保留/恢复文本，不吞掉用户输入。
- models-manager/collaboration_mode_presets.rs 已完整读取：Plan 内置 mask 保留
  model、设置 reasoning=Medium、携带 Plan 指令；Default mask 的 model/effort
  都不覆盖基础值。settings.rs::set_collaboration_mask 还支持本地
  plan_mode_reasoning_effort 覆盖。不能用“切回 Default 时保留 Plan Medium”代替。
- settings.rs::set_effective_collaboration_mode 仅在 Default 时更新
  current_collaboration_mode 基础值；active mask 是另一份状态。模式 indicator
  在 Plan 显示，Default 不显示。用户手动选择后显式提交 Thread 设置。

## Corki 当前差异及实现约束

Runtime 已有完整 CollaborationMode 替换和持久化快照，但 CommandDispatcher 没有
模式动作，当前只显示初始 settings.model；TerminalUI 尚无模式 indicator。
dispatch 会将整条 command lower()，若直接复用该变量作 inline 正文会破坏原文。
活动期间输入有独立分支；只添加闲时命令会漏掉运行中拒绝边界。

下一批必须同时接入：命令解析保留原文、闲时模式发布、带参数输入按新设置入场、
运行中拒绝、显示设置发布成功后的模式，以及独立 Default 基础值/Plan effort。
设置提交失败时不得更新指示器或丢弃 inline 输入。已有 Thread/Turn 快照职责不变，
不扩展原生未提供的当前 Turn 模式切换 API。

仍需落实的接点：Default 基础值在冷启动/恢复时的来源、CLI 循环模式快捷键的
原生可用条件及 Corki 绑定、设置等待期间输入的所有权；这些不能靠一个字符串
mode 或新增 `/plan off` 自造语义替代。通用 API 不需要官方 catalog 请求。

本轮为新源码证据收敛：尚未实现命令入口、未执行新的行为测试，不宣称该缺口
关闭。下一批验收应覆盖裸命令不采样、inline 正文原样且仅提交一次、运行中不
steer、不误改 active Turn、Plan/Default effort 恢复、提交失败以及真实 PTY 指示器。
