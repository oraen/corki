# 通用 CLI 对齐进展

目标及八项独立清单见 `cli-universal-goal.md`。本轮八项实现及相关联合验收已完成。

当前状态：1/2/3/4/5/6/7/9 均已完成独立实现、专项及最终相关联合验收；
下文按时间追加，较早段落的缺口以之后的补充为准，不代表已勾选完成。

## 4 图片路径粘贴（实施中）

参考 Codex `tui/src/bottom_pane/chat_composer.rs::handle_paste`、
`handle_paste_image_path` 与 `tui/src/clipboard_paste.rs::normalize_pasted_path`、
同文件 `pasted_paths_tests`。Codex 对不超过 1000 字符的粘贴先尝试单一路径，
识别图片后插入图片标记及空格，识别失败保留普通文字；支持 shell 引号/转义和 file URL。

Corki 原先只有 Ctrl+V 剪贴板图片读取，没有 bracketed-paste 路径转换。
现增加显式路径的受管 helper（`image_clipboard.py --file`），复用 PNG 编码、
20 MiB/3200 万像素、5 秒超时和进程回收，绝不回退到系统剪贴板。
`terminal.py` 注册 `Keys.BracketedPaste`，保留原文字直到成功解码，
失败/取消不丢文字，图像禁用及数量上限时保持文本。复用消息总量上限。

必要差异：Codex 同步探测图片，本实现异步隔离解码；解码期间若草稿发生编辑，
保守保留文字而不把过期图片插入新草稿。后续统一草稿状态设计时评估更细锚点跟踪。
Windows/WSL 路径解析有兼容分支，但当前 macOS 环境未做真实 Windows 验证。
不把该分支宣称为已跨平台验收。

首轮专项：`tests/unit/cli/test_image_path_paste.py` 与 `test_image_paste.py`，
26 passed。含真实 helper 对测试生成图片的读取、无效文件、路径标准化及
真实 PromptSession 按键序列的成功/失败/编辑过期/取消回收，不使用用户剪贴板。
后续 CLI 全量单测、图片发送集成、原图片 PTY 联合 827 passed，4 条既有 forkpty 警告。
新增中文/空格相对路径的真实 helper → PTY → Runtime → 模型请求断言，
40/100 列及既有 idle/queued 共 6 passed，6 条既有 forkpty 警告。
进一步补充畸形 file URL 及 NUL 路径拒绝。未完成共享草稿/长粘贴整合及
所有超限边界验收，暂不勾选第 4 项完成。

## 后续顺序

1/3 共享输入状态：已初读 Codex chat_composer 的 Large Paste Placeholders
及 paste_burst.rs 模块文档、chat_composer_history.rs 的 HistoryEntry。
开始实施前还需完整核对对应状态机及测试，不能把初读计为实现。
其余 2/5/6/7/9 尚未实施。

## 3 同次运行历史草稿（实施中）

参考 Codex `chat_composer_history.rs::HistoryEntry`，明确持久化输入历史不存
图片或原始元素元数据，本地历史保存完整草稿；读取 `chat_composer.rs` 的
`history_navigation_restores_remote_and_local_image_attachments`、
`history_navigation_restores_remote_only_submissions`、
`history_navigation_leaves_cursor_at_end_of_line` 测试，验证恢复附件与光标边界。

新增 `draft_history.py`：真实图片身份/位置快照，不按标记文字推测附件；
相同正文但不同图片不去重。最多 128 项、100 万正文字符、4400 万唯一图片
data URL 字符，按完整条目淘汰而不是只删图片留下假标记。退出释放引用。
terminal 上下键/Ctrl+P/N 在首末行浏览，菜单优先，搜索框不被此键绑定抢占；
Down 返回最初未发送草稿，Ctrl+C 先保存本地完整草稿。跨进程从 FileHistory
加载的条目仍是纯文本，不从手输/磁盘标记补造图片。

验证：新增同名标记不同图片不去重、容量与释放、真实 PromptSession
粘贴→提交→Up→Down→Up→再提交测试；已有 Ctrl+C、多行和抢占回归。
CLI 全量、图片集成及当时 6 个图片 PTY 联合 833 passed、6 条 forkpty 警告。
后续新增 recall 模式 PTY 验证窄/宽屏找回再提交：8 passed、8 条既有 forkpty
警告，实际退出 0；ruff check 与 git diff --check 通过。

未完成部分：与第 1 项长粘贴元素的统一快照；原生 Ctrl+R 目前仍是文本历史，
不能误称完整富草稿搜索；检查复杂连续编辑导航语义和更多恢复场景。
因此第 3 项尚不勾选完成。

## 1 长文本折叠（实施中）

再次读取 Codex `chat_composer.rs::handle_paste`、`next_large_paste_placeholder`，
及 `handle_paste_large_uses_placeholder_and_replaces_on_submit`、
`large_paste_numbering_continues_with_same_length_placeholder`、
`large_paste_numbering_reuses_after_all_deleted` 测试。
阈值为超过 1000 Unicode 字符；同尺寸占位符使用现存最大序号加一，
全部删除后复用无后缀名称，不能删除第一个后立即复用 base。

ImageDraft 引入有真实身份的 Paste 元素，与图片共享原子编辑、光标跳过、撤销。
只展开跟踪到的粘贴标记，不匹配手输同名文字，展开时重算后方图片偏移。
bracketed-paste 超过阈值折叠，提交展开；DraftEntry 记录折叠正文及 payload，
同次运行 Up 恢复折叠状态。ExpandedFileHistory 保存展开后的原文，跨进程
不会只找回无法展开的私有标记。草稿合并携带尚未提交的粘贴元素。

验证：纯状态测试覆盖假标记、图片偏移、删除/撤销、序号持续与复位；
PromptSession 中文多行粘贴→折叠→提交→Up→再次提交→检查磁盘全文。
CLI/图片集成/图片 PTY 联合 837 passed，8 条既有 forkpty 警告；ruff/diff 检查通过。
随后新增序号测试暴露相同标记公共前缀导致删除身份错判，已用删除后的光标限制
差分前缀修正；最新 CLI 全量（含该新增测试）824 passed，实际退出 0。

明确未完成：非 bracketed paste-burst；排队提交目前携带展开原文，取回后
尚未恢复折叠占位符；同尺寸粘贴跨草稿合并的编号冲突处理；更完整的容量边界
及长粘贴专项 PTY。不能以当前绿色测试宣称第 1/3 项完整完成。

### 队列草稿补充（后续进展，取代上段对应两项缺口）

核对 Codex `chatwidget/input_queue.rs` 的消息/历史并行保存、
`input_restore.rs::drain_pending_messages_for_restore` 和
`user_messages.rs::remap_colliding_paste_placeholders`：合并时对冲突标记
从 #2 起寻找可用编号，只改真实 text elements，并重算附件偏移。

Corki `DraftText`/`ImageInput.draft` 携带仅 CLI 可见的草稿快照；
`submission_value` 保留快照，`submission_text` 只返回展开正文给运行时，
队列预览用折叠正文，Alt+Up 和中断后的 restore_queued_inputs 恢复完整快照。
ImageDraft.append 对冲突 Paste 元素重命名并重定位后方图片，不改手输文字。
新增包含不同 payload、同名粘贴标记、各自图片的队列与当前草稿合并测试。
最新联合回归 839 passed、8 条既有 forkpty 警告，实际退出 0；ruff/diff 通过。

仍待验收：富草稿经已接纳 steer 再从运行时退回的快照关联、复杂队列 PTY、
非 bracketed paste-burst 及其余容量/生命周期边界。第 1/3 项继续未完成。
已继续阅读 paste_burst.rs 的字符判别/flush/Enter 方法：8ms 字符间隔，
120ms Enter 抑制窗口，非 ASCII 不暂存首字符；尚未实施此状态机。

### paste-burst 状态机（尚未接入 UI）

读取 Codex paste_burst.rs 常量、字符判别、定时 flush、Enter、修饰键 drain、
retro-grab 与五个专项测试。新增 `cli/paste_burst.py` 纯状态机及 5 项测试，
实际 5 passed。采用显式 monotonic 时间输入，不拥有后台任务或定时器；
与 Codex 一致：8ms 字符间隔、Windows 60ms 活跃缓冲空闲超时（其他 8ms）、
120ms Enter 抑制、ASCII 暂存首字符、非 ASCII 直接显示后按条件 retro-grab。
修饰键前 drain，取消/显式粘贴 clear；Unicode 偏移使用 Python 字符数。

这只是可测试的状态机，尚无生产调用，不能宣称快速粘贴功能已经生效。
下一步必须接入 composer 字符/Enter/其他按键、flush 调度与取消清理；
同步调整测试的人类键入时序，不能用快速注入的字符串模拟慢速手打来否定
正确的防误提交行为，也不能以只测纯状态机替代真实终端验收。

### paste-burst 输入框集成

核对 Codex `chat_composer.rs::handle_non_ascii_char`、
`flush_paste_burst_if_due` 及 paste_burst 的 drain/clear 边界后，新增
`composer_paste.py` 接入每个 composer 自己的按键分发器（不改全局解析表）。
普通字符经状态机暂存/retro-grab，Enter 在抑制窗口内换行；修饰键先 drain，
再委托原 handler。真实 image/paste 原子元素不可被 retro-grab 当纯文本回收。
CPR 回复不改变 burst 时序。单个 call_later 句柄刷新并随 prompt 退出取消；
取消先同步 drain 到草稿，不在退出路径启动图片读取 worker。
所有暂存字符也更新输入活跃时间，防止审批在暂存窗口抢走输入。
普通 burst 路径先做本地 is_file 探测（类似 Codex 的路径探测），实际图片解码
仍由受管 worker 完成；修饰键 drain 不做路径转换，避免阻塞紧接着的 Ctrl+V。

生产接入初轮 CLI 回归发现 25 个失败，分别排查而非直接忽略：修正输入活跃
时间未刷新和普通单词启动 helper 的真实问题；编辑/Shift+Enter 等测试将
“文字与提交回车同批发送”改成输入后跨越 120ms 窗口再有意提交，保留原断言。
新增 PromptSession 原始 ASCII/中文多行 burst、防误提交/折叠及暂存字符取消恢复
测试，专项 28 passed；修正后 CLI 全量 833 passed，实际退出 0。
长粘贴+图片真实 PTY 已加入 40/100 列场景，模型断言展开正文和图片偏移。
最终该文件 10 passed、10 条既有 forkpty 警告，实际退出 0；ruff/diff 通过。
尚需完整 e2e 集合回归及其余容量/富草稿边界，不能把本段计为整个目标完成。

## 6 本地会话选择（实施中）

已阅读 Codex `resume_picker.rs` 的查询、Esc 清查询/退出、Ctrl+T 预览、
Tab 工具栏及左右切换过滤器、Ctrl+A 归档，以及 archive.rs 的归档状态与
禁止归档当前活跃会话边界。Corki 复用 SQLiteThreadArchiveStore 的写租约
和归档元数据，不删除 conversation_items，不新建官方服务客户端。

新增 session_catalog.py：每页最多 100 行、默认 50，目录/归档状态/查询过滤，
SQL 参数化且返回标题与预览有长度上限；读取以 mode=ro 独立连接完成并关闭。
借用 joined-worker 机制等待取消的读取结束，避免未回收的后台 SQLite 线程。
session_picker.py 接入本地异步列表，过期加载/预览结果不覆盖新选择；
预览只取近期消息，Ctrl+A 归档，归档集合 Enter 先取消归档再返回 ID。
按键处理和后台任务由 picker Application 管理并在退出时等待清理。

启动入口：`corki resume` 打开选择器，`corki resume --last` 恢复最近会话，
指定 ID 及原有 `--resume` 保留。无 TTY 不隐式选取，提示使用 --last/ID。
取消选择器直接退出且不创建模型；这与 Codex 某些入口 Esc 开新会话的行为
尚有差异，后续核对启动上下文再统一，不能宣称完全一致。

专项测试包括列表分页/中文搜索/目录过滤/SQL 字符按字面处理、预览、归档/恢复
且原历史不变，真实 prompt-toolkit Application 归档→切换集合→恢复选择和取消。
最新 CLI 全量及归档存储回归 837 passed，实际退出 0。
新增 40/100 列查询选择/取消真实 PTY，与专项单测合计 7 passed，4 条既有
forkpty 警告，实际退出 0；ruff/diff 通过。
尚需：完整预览交互、跨目录恢复语义、失效/占用会话错误体验、更多键盘与
resize 场景，启动恢复到真实 Runtime 的专项验证。第 6 项仍未完成。

## 7 回溯旧问题并分支（实施中）

核对 Codex `app_backtrack.rs`：空 composer 首次 Esc prime，第二次打开历史选择，
确认携带 source thread id 与 nth_user_message，在该问题之前分支；陈旧 source
不可应用，不修改原会话或回滚工作区。检查 Corki `storage/forks.py` 的原始
UserMessageItem（retained_from_id 为空）计数和 Runtime 的 fork 初始化接口。

新增 BacktrackInput 控制信号及 backtrack.py，闲置空输入框双 Esc 返回控制信号，
不写输入历史、不提交模型；运行中 Esc 与补全弹窗 Esc 保持已有路由。
Application 加载显示快照，按原始用户消息选择，验证当前 source 未变化后调用
branch factory。生产 factory 创建独立 Runtime/会话，先发布 fork 再恢复选中
问题草稿，旧 Application 完成清理后 main 才切换；构造失败关闭新 Runtime，
旧会话仍可继续。图片从持久化附件或内容块恢复。排队输入未清空时暂不允许回溯，
避免无提示丢掉队列。

当前选项页复用 compact list，尚不是 Codex 的完整 transcript overlay；后续
需要对齐预览/左右或 Esc 步进、首次 Esc 提示和失败后的草稿恢复。
第 2 项引用绑定还没实现，不能声称分支已保全未来的 CLI mention 元数据。
切换时当前配置/协作模式继承、复杂压缩历史和更多取消边界仍需专项验证。

集成测试：真实 Runtime 两轮→CLI 选择第 2 问→独立 fork；分支只有第 1 问，
第 2 问放回草稿，模型调用数不增加，resume_pending 不执行旧任务，源快照不变。
该测试通过；相关 CLI 初步 125 passed；CLI 全量与该集成联合 838 passed，
实际退出 0。另新增真实 PromptSession 双 Esc 不提交/不写历史测试。
第 7 项仍未完成，尤其尚缺完整生产启动切换 PTY。

## 9 终端通知（实施中）

参考 Codex notifications/{mod,osc9,bel}.rs 的终端识别、OSC9/BEL 和 tmux DCS，
tui.rs 初始 focused=true 与 unfocused/always 条件，chatwidget/notifications.rs
的事件白名单与待操作优先于完成通知的合并逻辑。

新增 TerminalNotifications，配置 `[tui] notifications = true|false|[事件名]`、
`notification_method = "auto"|"osc9"|"bel"`、
`notification_condition = "unfocused"|"always"`；默认 true/auto/unfocused。
支持 agent-turn-complete、approval-requested、plan-mode-prompt 的触发入口；
terminal_responses 在 bracketed paste 外识别分片 CSI I/O，不将焦点报告当按键，
粘贴内同样的字节仍保留为正文。input_mode 启用并最终关闭 1004 焦点报告，
移除监听器、取消通知 call_soon 句柄、清空有界去重队列。
自动终端选择、tmux 包装、非 TTY/关闭时不输出、输出失败不传播至任务。

隐私差异：Codex 可带响应/命令摘要，本目标要求不泄露敏感正文，因此 Corki
只发固定“task complete/needs your input”文字，不发送回答、命令、路径或密钥。
同一轮事件合并时待操作提醒覆盖完成提醒；重复身份有界去重。

专项初轮通知/解析/Application 139 passed；CLI+config 联合 1518 passed、1 skipped。
真实 PTY 失焦→BEL/OSC9→退出关闭焦点报告 2 passed，2 条既有 forkpty 警告。
配置加载新增验证后通知专项 6 passed。单独重验跳过项所在范围：48 passed、
1 skipped，原因为当前文件系统拒绝非 UTF-8 文件名；并非通知功能测试跳过。
尚需真实审批/提问事件全链路测试、运行时重放去重边界及全量终端回归，
不能以当前通知输出测试证明整个第 9 项已完全验收。

## 2 文件、技能和插件引用（实施中）

本轮重新读取 Codex：
- `bottom_pane/mentions_v2/search_catalog.rs::build_search_catalog`、
  `skill_candidate`、`plugin_candidate` 及插件名称测试。
- `mentions_v2/filter.rs::filtered_candidates/sort_rows`，插件/技能/文件类型优先级。
- `chat_composer.rs::take_mention_bindings`、`insert_selected_path`、
  `insert_selected_mention`；文件前后分隔、图片文件、提交保留绑定相关测试。
- `chatwidget/input_submission.rs` 中 binding 到 `UserInput::Skill/Mention` 的
  路径匹配、去重和同名选择规则。

Codex 文件候选插入路径（含空白且不含双引号时加双引号）；技能/插件不仅插入
名字，还由真实元素保留路径绑定，提交后生成结构化选择器。同名技能不能按名字
任取第一个。统一 @ 搜索隐藏已有所属插件候选的技能，$ 仍可选择技能。

Corki 原本仅有斜杠补全，但运行时已有 InputMention 及技能/插件依赖解析。
新增 `reference_completion.py`，接入 CommandCompleter 的异步候选、Enter/Tab
只选择不提交、Esc 既有取消；`LangGraphRuntime.input_reference_catalog` 仅读取
本地技能及已加载插件。技能文件读取用现有取消后 join 的 run_skill_io；文件
列表用受管 rg 子进程，3 秒、2 MB 输出、1 万条路径、100 条展示上限，取消回收。
异步候选选择检查原正文与光标，过期候选不写入、不提交。文件带空白路径加引号，
不从损坏 UTF-8 或控制字符路径构造另一个错误路径。

ImageDraft 增加真实 Mention 元素；图片重新编号不会修改它，粘贴折叠和队列
合并会移动其位置；删除原子移除绑定、撤销恢复。DraftEntry 保存绑定，
DraftText/ImageInput 经普通提交、排队、steer 共同的参数入口传递 mentions。
持久化旧问题/退回输入通过已保存的 host selector 重绑现有 canonical marker，
不会凭普通文字生成新的 selector。同名候选显示来源路径以便区分。

当前差异/待办：文件仅枚举 rg 返回的文件，目录候选、无 rg/超限反馈和图片文件
选中后的附件转换尚未实现；模糊匹配为确定性的 Python 子序列评分，不声称等同
Codex 的评分；插件显示暂用 canonical name，展示别名/更多搜索词待补；异步
失败/关闭、resize/审批覆盖、排队/运行中搜索还需专项验收。手动构造的不可见
selector 无对应文本元素时的编辑恢复边界尚未完整定义。第 2 项仍未完成。

首轮 CLI 全量与技能运行时/分支集成联合 858 passed；随后新增持久化引用恢复
专项后 7 passed。真实 PromptSession 已验证选中不提交、再次 Enter 提交绑定，
上下键恢复并再次提交相同绑定；引用+图片+长粘贴+原子删除/撤销覆盖。

新增 40/100 列真实 PTY → CorkiApplication → LangGraphRuntime → 本地假模型
测试：技能 selector 的绝对路径和实际注入的技能正文、中文空格文件路径的引用。
首轮文件断言误保留了普通文本提交会 trim 的末尾空格，修正测试预期后 4 passed；
随后所有 CLI 单测、技能/分支集成及该 PTY 联合 **863 passed、4 warnings**，
实际退出 0；警告均为 Python 3.12 的既有多线程 forkpty 弃用提示。
ruff 与 git diff --check 通过。未据此宣称其余 PTY 或全部 8 项已完成。

### 图片文件候选补充

重新核对 Codex `chat_composer.rs::is_image_path` 和
`insert_selected_file_path`：仅 png/jpg/jpeg/gif/webp 进入图片探测，成功插入
图片及分隔空格，失败按普通文件路径插入。Corki 在真实选择后复用现有受管
`_paste_image_path`；不调用剪贴板。选择生成的路径和空格保留到解码成功，
替换范围包含选择追加的空格，避免图片后出现双空格。保留供应商不支持图片、
8 张附件及 4400 万 data URL 字符限制。取消受管任务仍 join，编辑时 revision
校验拒绝旧结果。与 Codex 的差异仍为异步解码，不阻塞输入事件循环。

扩展真实 PromptSession 测试覆盖 candidate/paste 两条入口的成功、坏图片、
期间编辑和取消，共 20 passed；图片候选新增 40/100 列 PTY，实际 helper 解码
测试生成的中文空格路径图片，再传到真实 Runtime 的本地假模型，确认位置及
附件不重复。CLI 全量及引用 PTY 联合 **860 passed、6 条既有 forkpty 警告**，
实际退出 0；ruff/diff 检查通过。该组合未包含上一轮的两个集成测试文件，
计数不同不代表测试减少或失败。第 2 项的目录候选/搜索失败反馈等缺口仍在。

## 5 工具语义展示（实施中，尚未完成聚合状态机）

参考源码：Codex `shell-command/src/parse_command.rs` 的 parse_command、
summarize_main_tokens 及 git_status_is_unknown、grep/ls-files 等测试；
`tui/src/exec_cell/model.rs::is_exploring_call/is_active/add_call`；
`exec_cell/render.rs::exploring_display_lines`；`history_cell/tests.rs` 中
coalesces_reads_across_multiple_calls、coalesces_sequential_reads_within_one_call
以及对应 Explored 快照。明确全部解析项均为 Read/ListFiles/Search 才归为探索，
任何 Unknown 使整条脚本回退普通执行。连续 Read 去重合并，不跨 Search 合并。

Corki 原先只显示工具名与原始 JSON，FIFO 生命周期事件由 application 的
_flush_tool_displays 处理，输出/原始标题通过 Transcript 保留，可 Ctrl+T 展开。
本轮新增展示专用 `tool_activity.py`，基础 cat/head/tail/sed -n、ls/tree/eza、
rg/grep 分类，畸形或截断 JSON 回退，混合未知命令整条回退，不影响任何工具
执行、授权或错误判断。TerminalUI 折叠头显示 Exploring + Read/List/Search，
连续读取合并；宽度自适应，最多 5 行并给展开提示；展开仍显示原始工具参数。
失败文案/输出路径维持既有行为，分类不能把错误变为成功。

当前仍非完整 Codex 行为：只完成基础展示解析，尚未使用等价 shell AST，
管道/cd 等复合常见形式尚未分类；跨调用聚合、活跃区域刷新及最终 Explored
转换仍未实施，不能把每次调用的 Exploring 标题当成完成状态。后续须贯穿
事件 call_id、完成/中断/重放、历史和异步输出顺序，增加真实 PTY 验收。

新增 23 个展示/回退/同调用读取合并/展开测试；CLI 全量 **877 passed**，
实际退出 0；ruff 和 git diff --check 通过。未进行本项真实 PTY 验收，清单不勾选。

### 探索聚合状态机、真实退出状态与 PTY 补充

再次对照 Codex ExecCell::add_call/complete_call/should_flush：用 call_id 路由，
不把孤立结束事件归给另一个调用；探索调用可跨工具返回持续合并，失败结束后刷出。
新增 Exploration 有界状态（最多 64 调用，每个错误预览只保留末尾 2048 字符），
Application 传递 call_id 给 TerminalUI，原始事件仍保留在 Transcript。
普通命令/回答/通知/退出等显示边界刷出探索；成功摘要显示 Explored，未确认的
调用仍显示 Exploring，已有中断提示明确“completion not confirmed”。
实时状态栏显示 Exploring，完成摘要合并连续 Read，并保留 Search 顺序。
展开历史输出原始命令和全文，重绘重建独立探索对象并恢复原实时对象，不修改源记录。

检查真实 shell_result 发现 is_error 仅表示工具级故障/超时，非零进程退出码可能
仍是 false。新增 core/tool_display.py 从 exec_command/write_stdin 的明确
CodeModeOutput 投影可选 exit_code/session_id 至 ToolCallCompleted；不解析输出
正文、不改变结果/权限/工具语义。展示层非零退出显示 Command failed (exit N)，
后台进程仍显示 Exploring 和真实 session ID，不误标成完成。

首轮联合测试 1324 passed、4 PTY failed；PTY 暴露摘要经 StdoutProxy 延后到回答
之后的真实顺序问题。_prepare_work_separator 现通过 commit_tool_display 串行
提交摘要/分隔线；错误文案使用 Text 避免自动数字高亮切割。重跑真实 PTY 4 passed；
CLI、protocol、shell_output_runtime 集成与 PTY 联合 **1328 passed、4 条既有
forkpty 警告**，退出 0。另补中断、孤立完成、容量测试；本轮未读取用户剪贴板，
模型为本地 stub，cat 仅访问测试临时文件。

剩余缺口：shell AST/复合命令分类尚不完整；实时 Exploring 目前利用已有工作状态
区，而非完整 Codex 可变 exec cell；持久化冷会话 replay_history 还未统一到新的
call_id 聚合入口；并行/审批覆盖/resize/恢复专项需扩充。第 5 项仍不勾选完成。

### 持久化工具展示恢复补充

核对 Codex `chatwidget/tests/exec_flow.rs` 的
replayed_command_completion_preserves_tracking_without_duplicate_starts、
replayed_completion_preserves_unrelated_running_command 和失败重叠调用测试，
明确回放使用身份匹配，不误完成实时的其他调用。本轮检查 Corki history.py
发现冷恢复仍走无 call_id 展示入口，并且 display_content 缺省时先裁掉正文
4000 字符；这与“完整历史可展开”不符，现移除预裁剪，继续由 renderer 折叠。

ToolResultItem 增加可选 exit_code/session_id（默认 None，旧 JSON 省略以保持
兼容），graph 从相同权威结构化结果写入；字段只控制显示，不改变 is_error。
history.py 复用 identified started/completed 及 chunk 输出，回放隔离探索状态，
不修改此前活跃的组。旧记录若无权威退出/后台状态，则保留原始观察输出可见，
不从任意工具正文推断成功，不把未知失败的旧输出折叠隐藏。

真实 PTY 测试扩展为：运行两条真实 cat → 正常/非零结束 → CLI 退出 →
重建 Runtime 读取原数据库 → 校验持久化退出码 → 回放相同聚合/失败提示和
完整输出。恢复使用禁止模型采样的 stub，确认无启动子进程。40/100 列共
4 passed；新增单测验证超过 4000 字符尾部可展开、隔离实时活动、旧记录不
掩盖退出码、可选字段往返和 bool 拒绝。CLI+protocol+该 PTY 联合
**1322 passed、4 条既有 forkpty 警告**，退出 0；ruff/diff 通过。
本组合未包含上一轮 shell_output_runtime 集成，计数差异不代表失败。
第 5 项复杂 shell 分类、完整实时 exec-cell 布局和其他专项仍待继续验收。

### 常见复合命令补充

读取 Codex parse_shell_script、is_small_formatting_command 与
supports_rg_files_then_head、keeps_mutating_xargs_pipeline、
collapses_pipeline_with_helper_when_later_stage_is_unknown、cd_then_cat_is_single_read、
cd_with_double_dash_then_cat_is_read 测试后，补入 `cd path &&`、`cd -- -path &&`、
`rg --files | head -n N`、`cat file | sed -n '1,20p'`、只读 sort/uniq/wc 管道，
及 git grep/git ls-files 分类。任意未知阶段仍令整条命令回退，不忽略后续未知
动作；修复缺少右操作数的 `&&` 误分类。测试发现 `cd a | cat b` 不能沿用顺序
切换目录的推断，已明确回退。正文中的原始命令及输出仍完整保留。

必要差异：当前使用受限词法分类，不是等价 shell AST；动态展开、控制结构和
复杂脚本保留原始展示。Codex 的小格式化列表包含 tee 等，本实现不隐藏 tee
写文件、sort -o、sed 写入或未知 xargs，以免把实际操作藏进只读摘要；该差异
只影响标题分类，不改变执行权限或工具语义。

展示分类专项新增 16 个例子；首轮 1 failed/38 passed 暴露上述 cd 管道问题，
修复后 CLI+既有探索 PTY 联合 **908 passed、4 条既有 forkpty 警告**。
随后将 PTY 扩展 simple/compound 两种真实 shell 命令，含正常/非零退出和
40/100 列、数据库冷恢复，共 **8 passed、8 条既有 forkpty 警告**，退出 0。
ruff/diff 检查通过；仍不把受限词法分类等同完整 Codex parser，八项验收未完成。

## 9 通知事件与降级补充

再次读取 Codex `notifications/mod.rs` 的 supports_osc9 及支持/不支持终端测试，
`chatwidget/notifications.rs` 的事件名称白名单、优先级和待发通知覆盖。
Corki 新增 TERM=dumb 保护，不发送通知及焦点控制码；空通知列表也不注册监听
或启用 1004。可选输出后端的普通异常在同步/异步回调和退出恢复处隔离，失败
禁用后续发送，不让 NotImplementedError 等逃逸到事件循环；仍不捕获进程退出
类 BaseException。已有固定通知文字策略继续避免正文、命令或路径泄露。

发现 Application 对旧 TurnCompleted 回放仍可能提醒，现按已回放 turn 身份与
新事件活动判断：纯旧回放不提示；恢复后的新回答/工具/提问活动完成仍提示，
同次终端会话同身份最终由有界去重处理。TurnStarted 重置本轮通知活动标记。

新增测试覆盖空列表/TERM=dumb、发出前焦点返回、退出取消 call_soon、后端
NotImplementedError、普通/重复/回放/恢复后新回答的完成事件，以及 shell/patch/
MCP 审批通知到决定回传、提问通知失败但回答照常交付。通知专项+PTY 初轮
19 passed；CLI 全量+BEL/OSC9 PTY **917 passed、2 条既有 forkpty 警告**。
ruff/diff 通过。此处应用层审批事件测试不等同真实终端审批通知端到端；仍需
完整任务各项联合回归和第 9 项逐条最终审计，暂不据此勾选完成。

## 6 最近对话展开及预览取消补充

重新核对 Codex resume_picker.rs 的 Ctrl+E expansion 测试和 Ctrl+T transcript
测试，以及 resume_picker_transcript_preview.rs 的 MAX_TRANSCRIPT_PREVIEW_LINES=6。
两种交互不同：Ctrl+E 展开最近对话，Ctrl+T 是完整 transcript overlay。
Corki 原先只在 Ctrl+T 显示最后 4 项摘要，本轮补 Ctrl+E 开关与最近 6 项查询，
有明确 Loading/No messages 状态。Esc 在加载中先关闭预览，不退出 picker；
再次 Ctrl+E 收起。切换选择或筛选取消预览请求，独立 preview_generation 防止
晚到的成功/异常结果重新打开已关闭的预览。Application 仍拥有并 join 后台任务。

新增真实 PromptSession 管线测试：打开预览→Esc 取消并确认读取已回收、picker
仍运行→再次打开→Ctrl+E 收起→Ctrl+C 退出；断言最多请求 6 项。该专项与原
会话列表/归档/恢复/40/100 列 PTY 联合 **8 passed、4 条既有 forkpty 警告**，
退出 0；ruff/diff 通过。Ctrl+T 暂仍为旧预览入口的兼容别名，完整 transcript
滚动窗口、目录恢复选择等此前缺口尚未完成，不能据此声称第 6 项完全对齐。

### Ctrl+T 独立只读记录窗口

核对 Codex resume_picker.rs 的独立 Ctrl+T/ Ctrl+E 分派、
loaded_transcript_waits_for_loading_frame_before_opening_overlay 及
escape_cancels_transcript_loading_and_restores_picker_navigation 后，Ctrl+T
不再复用最近对话预览，而进入独立全屏只读窗口。父 picker 在 in_terminal
期间暂停终端输入，关闭后恢复原列表/选择；不创建 Runtime，不采样模型、不执行工具。

新增 session_transcript.py：上下滚动，Home/End 到当前页首尾，在边界用 PgUp/
PgDn 浏览较旧/较新页；Esc/Ctrl+T/q 返回。读取复用 read_display_items_page
的 compaction replacement 边界及身份游标，每页 20 条，只保留当前页正文，
导航游标最多 4096 项。每个 SQLite 值读取前限制 8 MB，页面文本最多 800 万
字符，超限显示错误而不是静默裁掉正文。连接使用 closing，异步 worker 用
_joined_write 取消后 join；进入立即显示 Loading，失败可取消返回。
工具正文不再按最近摘要的 4000 字符限制裁剪，页面采用明确角色/工具名的纯文本
展示；没有恢复内部 context 指令。Ctrl+E 和 Ctrl+T 提示分行，40 列可见。

必要差异/尚待补齐：本窗口为分页纯文本，尚不是 Codex 的完整 styled cells，
独立失败 Turn 元数据、部分非标准/远程历史类型还未加入；超大单项目前明确拒绝
浏览，需后续评估分块读取。窗口容量限制不是对“全部数据已展示”的声明。
目录选择、更多取消/resize/分页交互验收仍待完成。

测试验证 45 条历史遍历无缺失、工具正文 5000 字符后尾部保留、加载取消后
读取任务结束。新增 40/100 列真实 PTY：Ctrl+T→读取正文→q 返回原列表→
Enter 选择原会话。CLI 全量与会话 picker PTY 联合 **924 passed、6 条既有
forkpty 警告**，退出 0；新增 SQLite 长度保护后重跑目录专项。ruff/diff 通过。

## 3 普通历史导航边界修正

本轮逐段核对 Codex chat_composer_history.rs::should_handle_navigation、
navigate_up/down、should_handle_navigation_when_cursor_is_at_line_boundaries，
以及 chat_composer.rs 中该判断的调用链和 history_navigation_cursor。
此前 Corki 在首/末行就切换历史并保存任意 scratch；Codex 实际要求：空文本，
或仍与刚找回的历史完全相同且光标处于全文首/尾。修改后的历史文本、普通未提交
草稿及内部位置应交给普通光标移动，不应该被 Up/Down 替换。

新增 DraftHistory.should_navigate，TerminalUI 先按该规则分派，未满足时仅移动
光标。保留已经验证的图片/粘贴/绑定历史元数据，最新历史再向下回到空草稿。
修正此前真实 PromptSession 测试的错误期待：输入“草稿”后 Up 必须保持原文，
清空后 Up 才恢复历史附件；Down 清空、再次 Up 恢复并提交同一图片。

专项初轮 13 passed；新增全文边界/内部位置/编辑后保护测试后，CLI 全量及
图片路径/附件/排队/历史/paste-burst 的 10 个真实 PTY 联合 **929 passed、
10 条既有 forkpty 警告**，退出 0。ruff/diff 通过。此次更正以实际 Codex 源码
为准，替代前文“任意草稿 Up 保存 scratch 并切换”的交互说明；不宣称全部目标完成。

## 7 分支失败后的选中问题恢复

核对 Codex app_backtrack.rs::restore_backtrack_prompt_after_branch_error、
app/tests.rs::backtrack_branch_failure_restores_selected_prompt_snapshot 及对应
快照：创建分支失败后恢复选中问题到 composer，再显示错误。Corki 此前只报告失败。
现 _edit_previous_prompt 保存选择，在非取消异常且来源会话未变化时，通过
prompt_input / restore_queued_inputs 恢复文字、图片位置及显式引用绑定。
不提交、不写输入历史、不改变原会话。错误仅展示异常类型，避免泄露私密路径。
加载失败/未选择不恢复；取消继续向上传播；会话变化不污染新会话。

新增 5 个参数化异常边界用例，连同真实 runtime 分支和双 Esc 专项 7 passed。
CLI 全量联合分支测试首次 924 passed、1 failed：长中文 raw paste 用例在并行
运行中偶发失败，原输出因超长参数 ID 截断；单独重跑 3 passed。为该用例加上
短 ID 便于后续定位，未修改生产粘贴阈值或弱化断言。相同 8 worker 全量重跑
925 passed，退出 0；ruff/diff 通过。该偶发时序问题仍待复现分析，不能按重跑
通过视为已修复。首次 Esc 提示、完整 transcript 回溯选中交互仍待继续对齐。

## 7 从模型菜单改为历史回溯选择窗口

参考 app_backtrack/legacy_input.rs::handle_backtrack_preview_event、
app_backtrack.rs::step_backtrack_and_highlight / step_forward_backtrack_and_highlight、
pager_overlay.rs::render_hints / handle_event，以及 edit_next_hint_is_visible_when_highlighted
测试。Codex 在完整历史 overlay 中高亮问题，Esc/Left 向旧、Right 向新，边界不循环，
Enter 确认；普通上下键滚动，不替代选择，q/Ctrl+T 关闭。

Corki 原来复用模型下拉菜单，隐藏上下文且 Esc 直接取消。新增 backtrack_picker.py，
显示来源快照中问题/回答/工具正文，高亮选中问题全文；使用原问题 ordinal 和 ID
映射，过滤 compaction 替换副本，不把滚动位置当作分支边界。Application 在同一个
快照上构建候选和正文，再交给现有 source-preserving branch factory。
窗口为独立 full-screen prompt_toolkit Application，退出恢复屏幕；TerminalUI 在
finally 回收 modal_depth，取消不产生分支。最大正文 800 万字符，超限明确失败，
不截断后假装完整。键位提示分三行，40 列可以读取选择/退出方式。

新增 9 个单元测试：中文多行/完整工具输出/retained 排除/ID 不匹配、左右边界、
Esc 向旧、三种退出键及容量保护。新增 4 个真实 PTY：40/100 列并 resize，运行真实
本地 runtime 和假模型，确认选择首问题后分支不含旧问答、原会话快照不变，且取消
不创建分支；resume_pending 不重跑工具或调用模型。CLI 全量、分支集成和该 PTY 联合
938 passed、4 条既有 forkpty 警告，退出 0；ruff/diff 通过。

仍有必要继续的差异：正文暂为纯文本，不是 Codex styled history cells；超大历史
应改用分页而不是当前快照窗口；首次 Esc hint 尚待接入。此次额外读到 Codex 的
backtrack_fork_before_turn_id_rejects_mid_turn_steers / rejects_in_progress_and_missing_prompts
测试，下一轮需核实 Corki 对同 turn 内 steer 和未结束 turn 的分支边界，不能据本轮
普通已完成 turn 测试推断这些场景也已对齐。全目标仍未完成。

## 7 持久化分支边界校验

逐段核对 Codex app_backtrack.rs::backtrack_fork_before_turn_id 及其 rejects_mid_turn_steers、
rejects_in_progress_and_missing_prompts 测试。Corki storage/forks.py 的通用 fork 支持
消息位置截断并把截断 turn 标为 cancelled，但 CLI prompt edit 不应因此允许独立
编辑 turn 中途的 steer。本次仅约束 CLI，不改变底层通用 fork 语义。

backtrack.validate_selection 在创建分支前拒绝同 turn 后续原始问题、created/running
turn、缺失或变化的选中记录。Application 在交互结束后重新读取快照，并再检查来源
thread；失败沿用恢复选中草稿的逻辑，只有本地固定安全错误文案允许展示详细原因。
历史无 turn 状态的旧记录明确拒绝，而非猜测已完成（相对 Codex 完整 Turn 数据模型
的必要兼容边界）。completed/failed/cancelled 的首问题仍可编辑。

新增状态枚举、steer、过期/非法序号/缺失状态单元测试；应用级新增交互期间历史
消失和运行中 turn 场景，断言 branch factory 从未调用且图片/引用草稿恢复。
CLI 全量、分支集成及真实 PTY 合计 947 passed、4 条既有 forkpty 警告，退出 0；
ruff/diff 通过。全目标仍在进行，未以本轮边界通过代替其他未完成的验收项。

## 7 首次 Esc 提示

参考 Codex app_backtrack.rs::prime_backtrack / open_backtrack_preview、
chat_composer.rs::set_esc_backtrack_hint、esc_hint_stays_hidden_with_draft_content 测试
和 footer_mode_esc_hint_backtrack 快照：存在历史目标时首次 Esc 显示
“esc again to edit previous message”，正文输入后清除，进入回溯窗口后隐藏。
TerminalUI 现在跟踪已显示的历史问题及非命令提交，首次 Esc 后在底部状态行显示
相同提示并 invalidate；空会话、带草稿/附件和运行中不显示。普通 transcript
重绘不重新修改目标标记；排队输入、斜杠命令不单独制造目标。原有文本变动回调
清除 primed 状态，第二次 Esc 仍进入既有回溯流程。此提示不代替持久化分支校验。

新增真实 PromptSession 用例覆盖有/无历史首次提示、中文输入清除、输入未误提交；
专项 25 passed，CLI 全量、分支集成及回溯 PTY 949 passed、4 条既有 forkpty 警告。
ruff/diff 通过。分页历史、样式及其他编号项的剩余差距仍按原 goal 继续，不宣称完成。

## 2 文件引用补充父目录候选（尚非完整目录索引）

阅读 Codex file-search/src/lib.rs::walker_worker、run_returns_directory_matches_for_query、
run_classifies_followed_directory_symlink_as_directory：实际 walker 返回文件和目录，
允许隐藏条目、跟随链接，并按 git 上下文处理 ignore。Corki 当前 rg --files 路径
只有文件。本轮先将已经索引的文件父目录去重加入候选，以 Directory 标注，仍走
原路径匹配和带空格路径引用选择链。最多 10000 个派生目录、总计 200 万字符；
没有新建线程、进程或监听器，沿用已具取消回收的 owned rg 子进程。

新增中文空格嵌套目录去重/补全单元用例，扩展真实 PTY 40/100 列目录选择→再次
Enter 提交→假模型检查精确路径正文及无错误附件/技能绑定。专项 16 passed、8 条
既有 forkpty 警告；ruff/diff 通过。本轮没有重跑所有 CLI 或全项目测试。

剩余差异明确保留：空目录、只含被忽略文件的目录及跟随目录链接未完整对齐；
rg 不可用/超限反馈和大项目搜索仍需改进。父目录补全只是增量实现，不作为第 2 项
已完成或缩减最终目录搜索要求的依据。

## 2 搜索错误和异步过期结果边界

参考 Codex tui/src/file_search.rs 的 session_token 与启动失败日志、
file_search_popup.rs 的 loading/no matches 状态。Corki 依赖外部 rg，与 Codex
内置搜索器不同；因此增加固定的不可用/失败提示是必要差异，而不是照搬其仅日志
处理。files 不再将缺少 rg、超时/超限/进程错误吞成空列表；rg 正常无结果退出码 1
明确接受。owned_process 新参数默认仍只接受 0，不改变其他现有调用的失败语义。

CommandCompleter 保存单个与文档文本/光标绑定的错误，异步 generation 保证旧结果
不能覆盖新状态；输入变化释放错误并作废旧请求。失败不构造可被 Enter 选中的假
候选，不影响其他成功加载的候选；状态行显示固定文案，无异常路径/token/输出。
取消继续传播给已拥有的搜索子进程生命周期，不新建后台任务。

CLI 全量加所选执行集成测试 942 passed、53 skipped（环境条件，不能当成执行层
全量通过）；文件/目录/技能/图片 40/100 列真实 PTY 8 passed、8 条既有 forkpty
警告。新增错误/空目录/缺失 rg/过期/取消及默认非零退出保护专项最终 5 passed。
ruff/diff 通过。大项目索引和完整空目录/链接目录搜索仍待完成。

## 跨功能 PTY 回归：审批、停止队列、Stop hook

尝试全 e2e，4 worker、loadfile、maxfail=5，实际因各 worker 已分配批次而在
15 failed / 32 passed 后退出 2，未完成全量。曾尝试对确认的 pytest PID 发 SIGINT，
进程已自行结束，命令返回 no such process，未实际中止任何进程。
失败集中在旧测试把正文和 Enter 合并发送，paste-burst 正确将其视为换行。
重新核对 Codex paste_burst.rs::newline_suppression_window_outlives_buffer_flush：
flush 后仍存在 Enter 抑制窗口，不能为旧测试取消生产防误提交行为。

修正 test_approval_pty、test_stopped_queue_pty、test_hook_cli_pty 中确实意在提交的
普通文本输入，分开正文和 Enter，间隔 150ms；未修改审批详情中的控制字符攻击输入、
队列 Tab 操作、真正粘贴用例或原有结果断言。审批 64 passed / 64 forkpty warnings。
第二轮 32 passed / 8 failed，失败是 hook 仍等待用户此前明确移除的
shift+tab to cycle footer；改为检查 composer 恢复，保留 subprocess 回收、历史数量、
调用次数和 hook 状态清理断言。最终 hook+stopped_queue+raw paste 联合
59 passed / 56 forkpty warnings，退出 0。ruff/diff 通过。

此次恢复了这些测试对当前交互的有效覆盖，不宣称所有 e2e 已通过；其他旧 PTY 中仍
有合并文本/Enter 及过期文案，需继续逐个核实并完整回归。

## 跨功能 PTY 回归：压缩、Working、普通命令和工具输出草稿

继续依据 Codex paste_burst.rs::newline_should_insert_instead_of_submit 与
direct_insert_newline_should_insert 的 active/window 判定，检查旧 PTY 的输入意图。
修正 compaction_input、working_status、cli、tool_composer 四个文件中意在普通提交
的合并文本+Enter，改为独立 Enter（150ms），不改产品逻辑及结果断言。

压缩完成/失败/取消 × realtime 开关 × 40/100 列，以及安静模型/慢工具计时和草稿
恢复联合 16 passed、16 条既有 forkpty 警告。普通命令菜单、Ctrl+C 草稿历史恢复、
非空 Ctrl+D、不完整历史加载退出，以及工具成功/失败输出不消费草稿、resize 后
顺序和不重复展示联合 14 passed、14 条既有 forkpty 警告。均退出 0，ruff/diff 通过。
这是四个文件的完整专项结果，非全 e2e 或八项最终验收完成声明。

## 2 插件显示名、别名搜索和稳定绑定

阅读 mentions_v2/search_catalog.rs::plugin_candidate / plugin_mention_name 及两个
命名测试、filter.rs::best_tool_match / sort_rows、plugin_mention_popup 快照。
Codex 分离显示名、搜索词和插入标记：显示名优先匹配，再用内部名/完整 config name/
marketplace 匹配；标记沿用显示段大小写（MCP-Search、Google_Calendar），不匹配时
按内部名分段首字母大写。最终绑定 plugin://config_name，不按显示名选插件。

Reference 新增 display_name/search_terms，候选渲染使用显示名，搜索优先显示命中，
再匹配别名，保留插件→技能→文件/目录排序。插件插入标记按上述算法生成，selector
保存标记名和完整稳定路径，使历史恢复仍能重新绑定。描述不再重复展示插件名。
技能目前没有独立 display_name 元数据，保留现有 qualified name；模糊算法仍为
本地 subsequence，尚未声称等同 Codex 的全部评分细节。

新增 8 个用例覆盖 Codex 命名示例、显示名/内部名/来源搜索、同名不同来源插件选择、
实际 accept_reference→explicit_plugin_ids→持久化问题恢复的身份保持。
初轮测试调用参数及小写 display name 的预期有误，按真实函数签名和 Codex 原规则
修正后，CLI 全量与引用候选真实 PTY 联合 959 passed、8 forkpty warnings，退出 0；
ruff/diff 通过。没有新增官方目录/服务依赖，其他第 2 项差距仍继续保留。

## 2 工具候选模糊评分与 Unicode

完整阅读 codex-rs/utils/fuzzy-match/src/lib.rs 及其全部测试。原 Corki 的 casefold
和累计跳字符评分不同：Codex 逐字符 lower 并映射原始字符索引，needle 用 lower，
评分为首末命中窗口额外跨度，前缀命中减 100，空 needle utility 返回 i32::MAX；
调用层空查询按 0 分字典序排列。现 fuzzy_match/match_score/reference_match_score
按该算法实现；保留显示名命中优先于别名命中的排序层。

新增 12 个测试覆盖源码 ASCII/İ 展开/ß 不展开/大小写/前缀/间隔/空查询示例、中文
原字符索引，以及显示命中与别名命中优先级。工具/插件候选和真实文件/图片/目录
PTY 联合 36 passed、8 条既有 forkpty 警告，退出 0；ruff/diff 通过。
这仅证明工具候选算法对齐：Codex 文件候选另用 nucleo，Corki 当前仍复用本地评分，
完整文件索引和排序仍是待办，不据此勾选整个第 2 项。

## 6 会话分页加载期间的越界修复

参考 resume_picker.rs::maybe_load_more_for_scroll / ensure_minimum_rows_for_view，
其 loading 防重入和 footer 进度冻结，以及 picker_footer_progress_label_freezes_percent_
while_loading、transcript_loading_consumes_picker_input 测试。检查 Corki 时发现实际
越界路径：第 2 页只有一行，Up 回第 1 页先置 selected=49，但 rows 仍是旧页；
在新页返回前 Ctrl+E 会读取 rows[49]。重复 Down 也可能在旧 more 状态上继续跳页。

修复：listing loading 或 busy 时不处理列表导航和新预览；Enter/归档已有相同保护。
加载期间显示 Loading sessions，而不是把未完成查询当作 No matching sessions。
取消仍有效，沿用 Application 的后台任务取消/join。Codex 采用累加分页，Corki
目前切页，因此加载期间暂停列表选择是当前结构下的必要保护，不宣称分页交互完全
一致。新增真实 PromptSession 重现 0→50→0 三次请求，冻结第三次加载后依次发送
预览/Down/Enter，断言没有越界、无额外查询、无预览和误选择，退出等待加载回收。

会话 picker 单元与 40/100 列真实 PTY 专项 11 passed、6 条既有 forkpty 警告，退出 0；
ruff/diff 通过。其他原目标和第 6 项剩余差距仍未标记完成。

## 6 归档状态提示与过期搜索验证

阅读 resume_picker/archive.rs 的 Pending/Restoring 状态和 result 身份判断，及
archive_tests.rs::archive_shortcut_archives_selected_session_once 等测试。现 Corki
在归档/恢复/打开的异步操作期间显示对应状态，归档列表隐藏 Ctrl+A archive 提示，
Enter 标为 restore；操作开始清除旧错误，finally 清除状态，不改变归档存储语义。

新增真实 PromptSession 异步竞态测试：旧查询被取消后仍延迟返回，确保新查询 ab 的
结果继续被选中而非被 a 覆盖；连续 Ctrl+A/Enter 不重复归档，退出后归档 coroutine
已回收。该测试验证任务 ownership，不声称已提交的数据库归档可以被取消回滚。
会话 picker 专项与窄宽屏 PTY 共 13 passed、6 条既有 forkpty 警告，退出 0；
ruff/diff 通过。整体目标和其他未完成验收仍保持进行中。

## 6 跨目录 picker 恢复接入工作目录选择

阅读 session_resume.rs::resolve_cwd_for_resume_or_fork 和 cwd_prompt.rs 的状态/按键及
cwd_prompt_selects_session_by_default、can_select_current、ctrl_c_exits_instead_of_selecting
测试。Codex 默认 Session，Esc 同样选择 Session，Ctrl+C/D 才退出。Corki 此前从其他
目录筛出的会话仍总用进程启动 cwd 构造 runtime，本轮新增 resume_directory 选择屏：
默认/1/Esc 会话目录，Down/2 当前目录，Enter 确认，Ctrl+C/D 退出；路径 resolve 后
相同则不询问，不存在的选项目录不接受并提示。路径显示清理控制字符、自动换行。

main 的 picker 返回后读取线程记录，再将选择传给 build_application 的 working_directory，
配置、UI、runtime 使用同一选中目录，不做全局 chdir。路径有效性在分配 repository
之前检查；回溯分支 factory 继承当前 UI 的工作目录，避免又退回启动 cwd。
新增 9 项按键/取消/失效目录/构造链传值测试。测试初版误从 package 导入同名函数而
非模块，修正后完整重跑：CLI 全量加现有 picker PTY 981 passed、6 forkpty warnings，
退出 0；ruff/diff 通过。现有 PTY 只覆盖 picker 本身，不证明新跨目录流程已做真实
工具 cwd 端到端验证，下一步必须补充。

尚未完成的差异：Codex 的记住目录偏好选项未接入，显式 ID/legacy resume 仍保持旧
行为，本次只接入交互 picker；完整跨目录 PTY 与实际工具工作目录验收仍待补齐。
不以这部分实现代替第 6 项或整体目标完成。

## 6 跨目录恢复真实工具验证

新增 test_resume_directory_pty：真实 main(['resume'])、SQLite 会话、picker 目录筛选、
目录选择、TerminalUI 和 LangGraphRuntime；仅模型与配置来源替换为本地测试替身，
禁止加载真实用户凭据。分别在启动目录和中文空格会话目录创建同名 cwd-marker，
选择目录后让假模型调用真正 exec_command('cat cwd-marker', login=False)，检查结果
来自选中的目录，且仅调用两次模型，不重跑原会话。取消流程断言未调用模型。
覆盖 Session/Esc、Current/2、Ctrl+C 取消 × 40/100 列，并在选择屏 resize。
全局进程 cwd 保持不变，验证依赖 runtime 的 working_directory 而非 chdir 偶然效果。

首轮六项卡在无匹配列表的原始字节字符串匹配；终端使用增量绘制拆分字符，改用
pyte 检查还原后的画面后，6 passed、6 条既有 forkpty 警告，退出 0；ruff/diff 通过。
补齐上一节新目录流程的真实工具验证，不代表记住偏好或显式 ID 流程已实现。

## 6 显式 ID / legacy resume 的目录确认

再次核对 session_resume.rs 的公共 resolve_cwd_for_resume_or_fork：跨目录询问并不只
属于 picker。main 现在对所有交互式 resume 共用目录解析与确认，显式 ID 和 legacy
--resume ID 先 resolve_thread，finally 关闭临时 repository，再读取记录和询问目录；
picker 仍先选会话。latest 仍按启动目录解析，不扩大到其他项目。非 TTY 保持现有
启动目录行为，不尝试读取交互输入；此兼容边界与记住偏好尚未完成的差异明确保留。

扩展真实 main + runtime + exec_command 目录验证矩阵为 picker/id/legacy ×
session/current/cancel × 40/100 列，共 18 PTY；联合目录单元与 parser 测试
29 passed、18 条既有 forkpty 警告，退出 0；ruff/diff 通过。默认会话目录/Esc、当前
目录/2、取消零模型调用、实际相对文件读取和全局 cwd 不变均保留验证。
其余原始八项及第 6 项偏好持久化仍待完成，不作整体完成声明。

## 6 恢复目录的记住偏好

对照 Codex `codex-rs/tui/src/cwd_prompt.rs` 的 CwdSelection、
persist_remembered_cwd_selection 和对应记住选择/保存失败测试：四个选项依次是
会话目录、当前目录、会话目录并记住、当前目录并记住，1–4 直接确认，上下/j/k
循环选择，Esc 仍采用会话目录，Ctrl+C/D 退出。保存失败不取消当前选择。

Corki resume_directory 现已接入同样四选项，main 传入用户 config_file，保存
`tui.resume_cwd = "session" | "current"`。复用 toml_edits 的保留注释、进程内锁、
临时文件原子替换；读写由取消后等待结束的 joined worker 执行。普通选择和取消
不写配置，保存失败只输出固定提示不泄露异常内容，仍返回选中目录。读取上限
1 MB；错误配置和已失效的记住目录明确失败，不静默覆盖或在错误目录运行工具。

单元覆盖两种记住模式、再次读取跳过询问、保留已有配置和注释、保存失败、取消
不写、错误配置及失效目录。PTY 扩为 picker/id/legacy × 五种选择 × 40/100 列，
记住模式再次调用真实 main 恢复会话，确认无目录询问，真实 exec_command 仍读到
正确相对路径文件。最初二次执行测试失败源于假模型重用工具调用 ID，改为按 turn
生成唯一 ID 后完整重跑，没有绕过生产去重保护。

验证：目录单元 + PTY 50 passed、30 条既有 forkpty 警告；CLI 单元全量 986 passed；
ruff 与 git diff --check 通过。无真实模型、剪贴板或用户凭据调用。
边界：偏好目前来自用户配置文件，未对齐项目层级覆盖；非 TTY 保留旧启动目录行为。
二次启动 PTY 是同进程再次 main，不宣称已验证独立进程重启。原始八项完整回归及
其他记录中的剩余差异尚未完成，goal 保持进行中。

## 2 搜索容量反馈与 1/4 普通粘贴后立即提交回归

重新阅读 Codex file-search/src/lib.rs 的 walker_worker、
run_returns_directory_matches_for_query、run_classifies_followed_directory_symlink_as_directory：
它直接索引目录，hidden(false)、follow_links(true)、require_git(true)。Corki 的 rg
文件列表加父目录推导仍缺空目录和跟随目录链接，且文件排序仍非 nucleo，这些没有
完成；本轮不将其标为已对齐。修复已有 10000 文件/10000 推导目录及目录字符容量
上限的静默截断，超过边界明确报告 candidate limit，不再把不完整候选伪装成完整
结果。新增文件阈值两侧、推导目录超限测试，引用相关 16 passed。

全 E2E 尝试（4 workers，maxfail=3）实际 6 failed、102 passed、12 skipped，退出 2，
106 条既有 forkpty 警告，不代表全量通过。其中四项旧 plan cancel 测试仍等待已删除
的 shift+tab 页脚，改为等待真实输入框，运行时模式状态断言保持不变；随后暴露
WRITE_HELD 后 Ctrl+C 等不到 SIGNAL_PENDING_COMMIT，四项仍失败，必须继续排查。
另外两项控制字符粘贴测试是真实输入回归：普通单词被当成候选图片路径启动异步
解码，解码未结束时 Enter 被忽略。单独重跑两项同样失败，排除仅并发时序现象。

对照 Codex bottom_pane/chat_composer.rs 的 handle_paste/handle_paste_image_path，
它同步 image_dimensions 成功才接受为附件，显式粘贴清理 burst 的 Enter 抑制。
Corki 继续使用有界独立图片解码进程，但现对所有粘贴路径先检查 is_file，而非仅
非 bracketed burst 做此检查，普通文字及缺失路径不分配 decoder，不吞紧接的 Enter。
图片路径单元测试改用确实存在的临时文件，解码成功/失败/编辑/取消仍由替身控制。
新增英文、中文、缺失图片路径、含 ESC 控制文本的显式粘贴 + 同批 Enter 单元测试，
断言无 decoder 任务且输入完整提交。原 PTY 控制字符测试不加延时，仍验证安全显示。

最终 CLI 单元全量 + composer paste/image paste PTY：1007 passed、14 条既有 forkpty
警告，退出 0；ruff/diff 通过。剩余明确问题包括 plan commit Ctrl+C 四项失败，以及
上述文件索引/排序差异；全套 PTY 和原始八项总验收尚未完成。无真实剪贴板/模型调用。

## 输入读取间隙的 Ctrl+C 与终端恢复

继续追踪上一节 plan commit Ctrl+C 四项失败。Codex 的
chatwidget/interaction.rs::handle_key_event/on_ctrl_c 按模态处理优先、活动任务中断
的顺序处理 Ctrl+C；composer_submission.rs 的
output_free_ctrl_c_interrupt_keeps_prompt_and_opens_blank_composer 验证中断不会重发
原问题。Corki 的故障不在数据库提交：TerminalUI.input_mode 为避免读者切换期间
CR 被 cooked mode 转为 LF，在首次 read_message 后持续 raw mode，因而关闭了 ISIG。
模式提交等待时没有 composer 读取 Ctrl+C 字节，也不能产生 asyncio 所需 SIGINT。

新增 terminal_input_mode.between_readers_mode：保留 raw 输入的 CR/LF 区别和关闭回显，
POSIX 读者间隙开启 ISIG/NOFLSH；每个 prompt-toolkit reader 嵌套自己的 raw_mode，
此时仍关闭 ISIG，由既有按键处理器决定清空草稿/取消弹窗/中断。退出由外层 raw_mode
恢复原始终端设置，不新增监听器、计时器或后台任务。非 TTY/DummyInput 不访问 termios，
Windows 维持原来的输入适配，本次不宣称验证 Windows 间隙 Ctrl+C。

四项原失败 PTY 在不改变 sendcontrol('c') 或状态断言的情况下通过。新增真实 openpty
单元验证间隙信号开、reader 信号关、CR/LF 字节不转换、嵌套退出/异常退出恢复设置。
初始全量联跑结果 2 failed、1093 passed（100 项 PTY 全通过），两项失败都是 macOS
恢复 canonical 模式时内核设置的 PENDIN 临时标志；测试排除这一非配置状态后，
CLI 单元完整重跑 996 passed。模式单元 + 通知 PTY 5 passed、2 forkpty 警告。
审批/排队/提交取消 PTY 与模式单元再次联合复测 103 passed、100 条既有 forkpty 警告，
退出 0（8 workers，load 调度，35.76 秒）。ruff/diff 通过。整体目标仍未完成。

## 9 审批与完成通知的应用链路 PTY

再次对照 Codex chatwidget/notifications.rs::notify/maybe_post_pending_notification、
Notification::priority/allowed_for/display，以及 tui.rs::notify：待操作通知优先于完成，
发送时检查焦点，后端异常关闭后续通知而不打断任务。Codex 通知预览会带命令等信息，
Corki 按本目标隐私要求维持固定通用文案，这是刻意保留的差异。

扩展 test_notifications_pty，使用真实 CorkiApplication._handle_elicitation、InputOwner、
TerminalUI、shell approval 和 _render_events_owned(TurnCompleted)；审批请求/完成事件
由本地 fixture 注入，运行时回应端只记录决策，不启动真实工具或模型。覆盖
聚焦/失焦 × 接受/Esc 取消 × 40/100 列与 resize，验证审批决策一次、通知身份去重、
重复完成事件去重、原输入草稿完整恢复后提交，以及通知 OSC 内容精确为通用文本。
退出断言计时 handle/pending/seen 清空、两个输入 parser 的 focus listener 注销、
composer reader 与两种 prompt 应用后台任务结束，并观察 focus reporting 关闭序列。

首轮 fixture 等待带 once 的审批标签，但该请求未声明 scopes，实际为 Yes, proceed；
校正 fixture 等待文本，未更改生产行为。最终通知单元/事件路由/PTY 联跑
27 passed、10 条既有 forkpty 警告，退出 0；ruff/diff 通过。本轮证明本地应用到终端
控制序列的链路，不声称证明操作系统实际弹出通知，也不代替真实模型/工具审批触发
全链路验收。原始八项及完整 PTY 回归仍有未完成工作，goal 保持进行中。

## 全套 PTY 推进：模式切换、失败恢复、残影与真实工具生命周期

使用 8 workers/load 调度再次尝试全套 tests/e2e，并显式提供本地已存在的 sandbox
compiler，避免实际工具生命周期用例因缺 compiler 跳过；本次全套仍失败退出 2，
不作为通过证据。定位到 test_cli_plan_pty 等待用户已要求移除的页脚，及 connection
recovery、composer residue、execution lifecycle 把文字与 Enter 同批发送。
再次核对 Codex bottom_pane/paste_burst.rs 的
newline_suppression_window_outlives_buffer_flush：burst 刷新后仍保留短 Enter 抑制窗口，
不应为这些普通提交测试关闭生产粘贴保护。

仅修正上述四个测试文件的输入驱动：普通输入分开发送文本/明确 Enter；初始等待
使用实际输入框，移除等待不可见 Plan mode 页脚，原有运行时模式/配置发布断言保留。
专门的 bracketed paste 同批 Enter 和换行测试没有改成延时。第一轮 26 passed、
4 failed，剩余全部为工具审批取消后接续输入；增加捕获终端输出后确认文字仍在，
但 Enter 插入了换行。取消时 reader 被更换，发送后计时不等于字符已被新 reader
处理。改为等待 wait 文本实际显示，再间隔 150ms 明确提交，生产逻辑未变。

最终四文件全矩阵 30 passed、42 条既有 forkpty 警告，退出 0。验证含真实 exec_command
审批接受/取消、Ctrl+C/Esc 中断、长进程所有权/退出回收、后续提问、冷启动历史回放
不重跑工具、终端模式恢复；另覆盖连接失败/重试时保留草稿、三轮输入框无残影、
模式发布失败与模式切换。所有模型为本地替身，无用户密钥或付费调用。ruff/diff 通过。
全套 PTY 尚须继续跑完，文件索引、排序及其他已记录差异仍未完成。

## 扩大菜单/流式回归，发现完整工具历史缺口

继续检查 Codex paste_burst::newline_suppression_window_outlives_buffer_flush 和
chat_composer::handle_paste，并检查 Corki ComposerPaste、model_picker 与现有 PTY。
八组测试中仅将主 composer 的普通文字提交拆开为文字和明确 Enter；模型菜单输入
没有粘贴 burst 拦截，菜单内确认/取消按键保持原样。涉及 copy、model_effort、
mcp_loading、stream_interruption、stream_table、long_tool_output、terminal_palette、
model_approval。真实模型审批覆盖确认/取消/审批覆盖/继续打字，配置仍只发布最终选择。

颜色测试的失败确认来自宿主 NO_COLOR=1：只在明确要求测试 24-bit 颜色的 PTY fixture
环境移除此变量，不改变生产对 NO_COLOR 的尊重。长输出旧测试检查 characters omitted，
与当前按终端行显示的截断方式不符，改为检查 lines (ctrl+t to expand)，并增加展开后
300 行工具结果全部可见、安全转义控制字符的要求，未删减完整性断言。

最终本轮八文件 30 passed、6 failed、36 条既有 forkpty 警告，退出 1；ruff/diff 通过。
未通过的两项 long_tool_output 是实际缺口：tools/executor.py::_normalize_result（返回
ToolResult 的归一化路径）先把 display_content 截到 min(output_budget,4000)，graph.py
发 ToolOutputDelta 时使用该预览，故同次运行 transcript 展开也缺中间内容。模型/ledger
原始 content 仍完整，问题在 UI 展示源，需补全原始输出到展开历史的链路，不能简单取消
所有显示上限或改变工具/模型语义。graph.py 的 Code Mode notify 还单独 text[:4000]，
修复时应一起核对。新增失败断言保留，用于验证真正的修复。

剩余四项 stream_table（普通/markdown 包装 × 40/100）在退出历史查看、resize、提交
continue 后未到 TABLE_TURN_FINISHED，增加等待 alternate screen 退出和文字出现后
依然失败，不能继续当作单纯输入间隔问题，尚需检查真实输入/steer/流式显示状态。
其他菜单、复制、MCP 加载、流式中断、颜色与模型审批用例本轮通过。整体 goal 未完成。

## 5 修复展开历史的原始输出链路

核对 Codex exec_cell/model.rs::CommandOutput：aggregated_output 保留最终完整输出，
lines 提供预览渲染所需行，transcript_lines 提供展开历史；live_output 单独负责有界
实时预览。结合 exec_cell/render.rs 的长行预览测试，不能把终端折叠等同于提前销毁
显示源。Corki 此前 tools/executor.py::_normalize_result 和 error 把 display_content
再按模型预算/4000 截断，导致 UI 自身展开无数据可用。

执行器现在保留经原有原始传输上限校验的完整 display_content；显式空显示和自定义
显示文本优先级不变，未设置时仍采用原始 result.content。独立自定义显示文本也计入
32 MB 原始传输上限，避免新增无界入口。错误结果仍先按原有原始错误上限约束，再
完整交给显示层。模型的 legacy_output_char_budget、上下文投影和真实工具内容不变；
terminal 的按行预览仍收起长输出，只有展开时显示完整内容。

graph.py Code Mode notify 去掉独立 text[:4000]，保留 code_mode/cell.py 的通知总量
MAX_BUFFER=4,000,000、64 项上限与 service.notify 的已有边界。新增真实 Code Mode
本地引擎集成：6000 中文字符通知在 ToolOutputDelta 与持久化显示快照中均完整。
新增执行器完整默认显示/显式空显示/显式自定义显示、额外显示超限拒绝测试；错误
结果测试继续验证模型投影 <=80 字符，同时显示源保留完整错误。

联合 CLI 单元全量、执行器单元、Code Mode 集成全文件、长工具结果 PTY：
1045 passed、2 条既有 forkpty 警告，退出 0（8 workers/load，11.37 秒）；ruff/diff
通过。此前两项 long_tool_output 失败已修复，展开验证全部 300 行且控制字符不执行。
这不解决尚待定位的四项流式表格接续输入失败，也不代表八项全部完成。

## 流式表格接续输入 PTY 定位

逐层诊断确认第二次 Enter 仍是 ControlM，submit_composer 绑定正确；不是 CR 被转为
LF，也不是 runtime.steer 死锁。缩放后的重绘输出在测试端未持续消费，PTY 输出缓冲
满导致子进程阻塞，分别发送的 continue 和 Enter 积压成同批输入，被 paste-burst
正确识别为粘贴并保留换行。测试改为逐键输入期间有界读取重绘输出，模拟真实终端
消费行为；保留防误提交生产逻辑，没有增加生产延时或关闭 paste-burst。

移除临时键盘诊断钩子，保留失败时捕获输出。普通/markdown 包装 × 40/100 四项
全部通过：4 passed，4 条既有 forkpty 警告，4 workers，4.76 秒。ruff 通过。
本次无需生产修改。继续全套 E2E，尚未达到整个 goal 的完成条件。

## 继续全套回归（尚未全绿）

CLI 单元全量再次 996 passed（8 workers/loadfile，10.71 秒）。全 E2E 本次提前停止：
281 passed、16 failed、306 条 forkpty 警告，退出 2，171.78 秒，不能算全量通过。
其中 queued_input/proposed_plan_tail 的 worker 在修改前已导入旧测试；修订后的两文件
独立验证 12 passed、12 warnings（4 workers，13.76 秒），保留真实队列/steer/历史/
模型关闭及预览取消断言，只把普通键入和提交分离，未改粘贴测试。

plan_pty/proposed_plan_pty 的 composer 提交原先用 sendline("") 发 LF（现在明确对应
Ctrl+J 换行），改发 CR；模型 fixture 的 input() 同步仍保留 sendline。两文件全 7 项
通过。与 interleaved_tool 合跑结果 7 passed、2 failed，9 warnings，5.66 秒；剩余两项
interleaved_tool 在模型/工具次数断言失败，已增加诊断，仍待定位。所有八项验收未完成。

随后定位 interleaved_tool：假模型先发送 call 的 ModelItemCompleted，最终却返回
(message, call)，违反 graph.py 已完成项必须是最终 items 前缀的既有校验。改假模型
为 (call, message)，不修改生产核心或放宽校验；仍验证正文完成后才显示工具结果、
只执行一次、下一采样有结果，以及实时/回放不重复和显示顺序。
上述六文件（interleaved、plan、proposed_plan、plan_tail、queued、stream_table）联合
25 passed、25 条 forkpty 警告，8 workers/load，11.22 秒；ruff/diff 通过。
此结果解决该次全套运行暴露的这些用例，但还需重新跑完整 E2E 及八项最终差距审计。

## 全 E2E 首次全绿与继续补齐源码差距

重新跑 tests/e2e 全目录：388 passed、397 条既有 forkpty 警告，8 workers/load，
121.45 秒，退出 0。使用本地假模型与测试权限编译器，未调用付费供应商或真实剪贴板。
这是本轮后续目录跟随/提交长度改动之前的全量基线，不代替后续变更回归。

### 2 跟随目录链接

再读 Codex tui/src/file_search.rs::FileSearchManager 的会话更新/取消链路和
file-search/src/lib.rs::walker_worker：hidden(false)、follow_links(true)、require_git(true)。
其 run_returns_directory_matches_for_query 和
run_classifies_followed_directory_symlink_as_directory 测试要求目录可被引用。
Corki rg 搜索补 --follow，中文/空格链接路径使用链接本身的相对拼写，遵守忽略规则，
不替换成外部目标绝对路径；现有取消、3 秒/2 MB/候选上限不变。
新增外部目录链接及 ignore 测试和循环链接有界失败测试；引用/错误单元 18 passed。
必要差异：循环/损坏链接使 rg 非零退出时显示固定失败提示，不冒充空列表；Codex
walker 可跳过单个错误继续。空目录候选和大项目/文件排序差距仍待补齐，不能算本项完成。

### 1 展开后提交长度门槛

阅读 Codex chat_composer.rs 提交验证、oversized_submit_reports_error_and_restores_draft、
oversized_queued_submission_reports_error_and_restores_draft 和上限等值提交测试；
protocol/src/user_input.rs 上限 1<<20 Unicode 字符。Corki 新 ComposerValidator 在
PromptSession 验证阶段展开真实粘贴后检查，不以短占位符逃逸；失败保留原草稿和附件，
不进入 accept handler、历史或队列。错误文案含上限及实际字符数，与 Codex 相同；
当前呈现在输入验证栏而非新增 transcript 错误 cell，是 UI 架构差异。
新增普通/折叠 × 等值/超限、Enter/Tab 超限后纠正再次提交测试。测试暴露 Tab 校验
失败后 queue 标记残留，修复为普通 Enter 明确清除该标记。该门槛限制提交，不等同于
已解决持续输入/连续粘贴的全部内存容量边界，资源终审仍待进行。

变更后 CLI 全单元 + 排队/粘贴/图片 PTY 联合：1021 passed、1 failed、18 warnings，
8 workers，17.29 秒。唯一失败为既有 long-cjk 非 bracketed burst 未折叠（内容完整且
未误提交的前置断言已过），需继续定位高并发下按键时间分类；不能将联合结果写成全绿。
定向串行重跑提交验证/paste adapter/burst/引用及错误五文件：32 passed，1.77 秒；
ruff、diff 检查通过。新增超限拒绝/纠正提交及目录链接测试通过。

## 中文 burst 时间分类与第 9 项独立验收

再读 Codex chat_composer.rs::handle_input_basic_with_time 与 paste_burst.rs 的
on_plain_char_no_hold、note_plain_char、flush_if_due：每事件先按实际时钟 flush，超过
8 ms 即可拆分非 bracketed burst；不存在“单次 pipe.write 必须视作单个 paste”的保证。
原 Corki 测试在并发调度时仍要求一次写入的 1100 中文字符必然折成一个 >1000 块，
该时间前提不成立。测试改按 Codex with_time 方式控制 adapter 时钟，等待全部按键
处理后推进到 300 ms 再 flush；仍断言内容完整、不误提交、长块折叠和计时器回收。
生产阈值/IME 路径不变，实际 PTY 多行中文/resize/控制字符粘贴测试保留真实时钟。
全 CLI 单元 + 真实粘贴 PTY：1008 passed、4 forkpty warnings，8 workers，10.19 秒。

第 9 项逐链复核：Codex notifications/mod.rs::for_method/supports_osc9 及其后端测试，
chatwidget/notifications.rs 的 allowed_for/priority/notify/maybe_post_pending_notification，
tui.rs::notify 的焦点门槛和失败后禁用。Corki notifications.py、application.py 的
TurnCompleted/执行及 MCP 审批/用户问题入口、配置校验和 terminal 输入域退出均已检查。
27 项通知单元、事件及真实 PTY 全过（10 forkpty warnings，4 workers，8.62 秒）；
补自动后端五种支持终端/未知/AppleTerminal 降级与 256 项去重容量测试。
通知文案刻意仅固定通用提示，不复制 Codex 的响应/命令/文件预览，满足用户隐私要求；
未知终端自动 BEL，dumb/重定向不输出控制序列，发送异常禁用后续通知但不影响任务。
焦点恢复会撤销待发通知，关闭输入域取消回调、移除监听器、清空去重并关闭 1004 模式。
真实 PTY 覆盖 40/100 列、resize、聚焦/失焦、审批确认/取消、草稿恢复与重复完成事件。
第 9 项据此单独勾选；OS 桌面弹窗与 Windows 真实终端未人工验证，不承诺系统提示外观。
其他七项及全目标仍未完成，不以通知验收代替其他项。

## 第 4 项图片路径独立验收及资源释放修复

本轮重新对照 Codex chat_composer.rs::handle_paste/handle_paste_image_path：>1000
先折叠，短粘贴且图片输入启用时尝试路径图片，成功附加并补空格，否则保留文字；
clipboard_paste.rs::normalize_pasted_path 及 pasted_paths_tests 对引号、shell 转义、
file URL、Windows 路径和多词拒绝的规则。Corki 对应 image_clipboard.py 路径规范化/
read_path_image、terminal.py paste_text/_paste_image_path、input_owner 的 rich input
和 runtime 图片提交链路已复核。图片只从显式路径读取，不回退到系统剪贴板。

异步解码保留原文字，完成后校验草稿 revision 和原范围，编辑/取消不误绑；无效/
非图片/超限保持普通文字，不自动发送。子进程 5 秒/20 MiB 输出、32 MP 解码上限，
每消息 8 图/44 MB data URL 边界保留。退出/重复取消/晚生成进程通过 run_owned 回收。
相对 Codex 同步探测并保存路径，Corki 异步解码为 PNG data URL，避免 UI 阻塞与稍后
文件变化；编码格式归一化和保守的过期结果丢弃是独立架构差异。

补无效文件/目录/非图片/源字节超限/像素超限测试，以及失败时图像对象关闭测试。
后者先失败 2 项，确认 PIL.Image.Image.__exit__ 并不释放 pixel core；encode_png
改用 closing 显式关闭原图、RGBA 转换图，并 context-close BytesIO，成功/异常一致。
未仅靠进程退出或 GC 代替显式释放。最终路径/剪贴板适配单元、owned process 生命周期、
真实运行时图片集成及 40/100 列 PTY 联合：70 passed、10 forkpty warnings，4 workers，
9.71 秒；ruff/diff 通过。PTY 包含空闲、排队、历史恢复、长粘贴加图片、真实图片路径。

第 4 项据此勾选。测试仅用临时生成的图片、模拟 clipboard reader 和独立命名 pasteboard，
不读取真实用户剪贴板或调用真实模型。macOS PTY 和本地解码已验证，Windows/WSL/Linux
实际剪贴板与真实终端未人工验证；供应商图片能力仍由普通供应商配置决定。
其他六项及整体目标仍需独立验收，不能以本项测试代替。

## 第 3 项历史图片/粘贴独立验收

重新阅读 Codex chat_composer_history.rs::HistoryEntry/new/record_local_submission_inner/
should_handle_navigation 及 duplicate/startup/persistent restore 测试：同次运行存完整
rich entry，重复比较整个 entry；纯文本磁盘历史不包含原图片/粘贴元素，仅显式编码
引用另行解码。普通上下键只在空输入或未修改的 recalled text 起止边界导航。
Corki DraftEntry/DraftHistory、terminal recall/accept/read_message、ExpandedFileHistory
与队列 rich input 的分离已检查。literal 图片/粘贴标签永远不自动解析成真实元素。

发现并修复：1,000,000 字符历史预算小于刚对齐的 1<<20 合法消息上限，导致合法大
粘贴提交后立即被淘汰。预算改为 4 Mi 字符（仍最多 128 项，图片去重后 44 MB），
支持最大合法文本连同折叠元数据；新增上限消息可恢复测试。
底层 prompt-toolkit 原 FileHistory 一次读整个文件且内存缓存无上限；现在最多读取
文件尾 16 MiB，截断窗口先丢弃不完整旧记录，仅缓存最新 128 项/4 Mi 字符，追加同样
受限。测试验证多行 Unicode、窗口落在旧记录中间不制造残片、持久化文件原样保留。
与 Codex 按需遍历持久历史不同：本实现只加载有界近期输入；更旧的磁盘记录不删除，
本地附件按完整条目淘汰而不留下孤立绑定。该容量差异满足本任务资源有界要求。

新增真实 composer 管道混合草稿测试：字面 [Image #9]、真正图片、绑定技能、中文
多行长粘贴一同提交，普通 Up 恢复后 DraftEntry 全字段精确相等，再提交图片位置不变。
已有真实 PTY 覆盖本次运行图片历史恢复和排队，不使用跨进程文字标记伪造附件。
CLI 全单元 + 图片/队列 PTY：1037 passed、14 forkpty warnings，8 workers，18.01 秒；
后加混合草稿后历史单元全文件 7 passed，0.55 秒。ruff/diff 通过。
第 3 项勾选；第 1/2/5/6/7 及整体验收仍未完成。

## 第 7 项引用恢复修复与真实工具不重跑验证

本轮复核 Codex app_backtrack.rs::backtrack_fork_before_turn_id：可见用户消息序号、
拒绝 steer/运行中 turn、确认显示内容与持久数据一致，再恢复 canonical mention。
继续跟到 chatwidget/user_messages.rs::mention_bindings_from_user_inputs，以及
chat_composer.rs::bind_mentions_from_snapshot/find_next_mention_token_range：绑定按顺序
逐一匹配，不把同名所有文本都绑定；@ 必须排除邮箱内嵌、路径和扩展名，$ 维持原边界。

Corki prompt_input 此前将每个 selector 绑定到所有同名出现位置，且缺 @ 前边界及路径
后边界；两个同名不同路径技能会被第一个 selector 占满。修复为 scan_from 顺序匹配，
每个持久 selector 仅恢复一个合法位置，保留图片元素重叠保护。新增 13 项覆盖邮箱、
斜杠/反斜杠路径、扩展名、连字符/下划线、标点、中文、重复标签与两个同名技能路径。
参照 Codex 不凭空恢复文本中不存在的 selector，也不从无持久元数据的字面标记造绑定。

回溯/引用单元 + 真实运行时分支 + 40/100 列选择/取消 PTY 联合：54 passed、4 forkpty
warnings，4 workers，5.01 秒。进一步加强真实 runtime 集成：原会话先实际执行本地
测试工具写临时工作区文件，再选择第二个问题 fork；fork/resume_pending 不增加模型
调用或工具调用，源 display snapshot 精确不变，文件内容不回滚。集成全文件 8 passed，
0.90 秒；ruff 通过。没有访问真实模型或用户工作区文件。
第 7 项暂不勾选：超大 transcript 的有界选择展示仍需收敛；其他未验收项不受本次
测试通过抵消。工作树改动未提交，teach.md 未修改。

## 第 7 项大历史分页与独立验收

复核 Codex pager_overlay/scrolling.rs::CellRenderable/render_scrolled 与
render_offset_content：按可见视口渲染，避免构建全部隐藏行。Corki 原回溯 picker
拼接整个历史文本、超过 8 MB 直接拒绝，导致长工具结果使旧问题不可选。
改用 BacktrackPages：保留原始 snapshot item 引用和 prompt 序号映射，只生成当前
32,768 原始字符窗口，控制字符按显示阶段转义；支持跨 item/单个超长 item 连续翻页。
不切割持久数据，不遗漏失败后缀，不缓存已离开的页面，不维护无限增长页栈。
删除仅服务旧全量拼接的 transcript_selection 路径。

Esc/←/→ 仍选择原始问题，选择变化定位到该问题所在页面；↑↓ 在页内滚动，PgUp/PgDn
到页边界后继续前后历史。翻阅上下文不会改变确认的 prompt 序号；Enter 编辑当前选中
问题，q/Ctrl+T/Ctrl+C 取消。Highlight 使用当前页实际转义后的范围，resize 由现有
wrap/布局处理。相比 Codex Rich history cell，Corki 仍为纯文本只读分页，不复刻 Rust
渲染器；无整段容量拒绝，所有历史内容均可前后翻阅。底层仍使用 runtime 的持久快照
作为原始数据，没有在本任务中重写会话存储；本次约束的是派生渲染与页面缓存。

新增 >8,000,000 字符工具结果完整分页重组、每页容量、反向游标、失败提示、控制字符
跨页安全，以及大历史选旧问题/翻页后确认/翻页后取消的实际键盘输入测试。
回溯单元/真实 runtime 分支/40 与 100 列 PTY：46 passed、4 forkpty warnings，4 workers，
5.41 秒。后加分页键盘场景的定向结果另见下条。此前真实工具写文件不重跑、源会话
精确不变、工作区不回滚和故障恢复证据继续有效。第 7 项勾选，整体仍需 1/2/5/6 验收。

新增分页键盘场景后的 picker/pages 全文件：13 passed，1.13 秒；先前 ruff/diff 检查通过。

## 第 6 项会话选择独立验收与资源修复

复核 Codex resume_picker.rs::Row::matches_query 与 resume_picker/archive.rs 的
request_archive_for_selected_session/handle_archive_result/request_unarchive/
handle_unarchive_result。归档无额外确认弹窗，Ctrl+A 发起、busy 防重复，Archived
中 Enter 恢复并继续 resume；过期结果不能把已归档项重新插入当前列表。
Corki 已有 generation/preview_generation、75 ms 搜索合并、页容量 50、加载期间
禁止旧选择、归档 busy、canonical lease/事务与取消等待；源码及 race tests 复核通过。
搜索原用 SQLite lower 仅支持 ASCII，本轮注册 Unicode lower 函数，与 Codex
Unicode to_lowercase 子串匹配一致；新增希腊文、重音拉丁文、中文及目录大小写测试。

存储复核发现 SQLiteThreadArchiveStore 的 `with connection` 只管理事务、不关连接；
read/list 的 asyncio.to_thread 取消也不 join。修复显式 closing，写事务保留 commit/
rollback；read/list 改用已有 _joined_write 生命周期管理。新增成功读取/列举/归档/
取消归档/失效 ID 的连接关闭断言、取消读等待 worker 完成测试。另外连接采用 mode=rw，
只打开已有库，避免会话库被移走后恢复操作新建空数据库；新增不重建测试。
这些修改仅修复本地会话选择使用的资源路径，不改变存储语义或重新启动核心对齐。

会话/预览/异步竞争/目录选择单元及真实 session picker、目录恢复 PTY：66 passed、
36 forkpty warnings，8 workers，13.74 秒。资源修复后归档单元、runtime/memory 归档
集成及 picker 单元/PTY：33 passed、6 warnings，4 workers，5.83 秒。最终 mode=rw
改动后全部相关存储/runtime/memory 集成：20 passed，3.54 秒；ruff/diff 通过。

独立验收包括：空列表/失效会话/取消，搜索筛选及预览，归档不删除 conversation items，
恢复继续最近/指定 ID 能力，跨目录 picker/id/legacy 入口的真实工具 cwd，以及写入记住
目录选项时保留配置；40/100 列 resize/异步过期及退出回收。
必要差异：列表采用 Corki 已有 ID/cwd/preview 元数据，无 Codex Git 分支/会话重命名
元数据；预览纯文本，Ctrl+T 补充历史每页 20 items、单值/页 8 MB 限制会显示加载失败，
不影响普通恢复或最近消息预览，未偷偷截断持久化历史。CLI 单元与 PTY 不代表所有真实
终端视觉已人工验证。第 6 项勾选；第 1/2/5 和最终联合验收仍未完成。

## 第 5 项补核：调用边界与并行失败状态

重新阅读 Codex tui/src/exec_cell/model.rs 的 complete_call/should_flush/
is_active/is_exploring_call，以及 render.rs::exploring_display_lines。实际规则是
按整个调用是否纯 Read 决定合并，相邻纯 Read 调用去重；混合 Read/Search 调用
内部不跨界合并。失败调用需等同组其他调用完成才 flush，期间仍显示 Exploring。

Corki 之前将调用全部拍平再合并，破坏上述边界。tool_activity.summary_calls
现保留整次调用边界，terminal 的实时与历史重放共用；单次匿名工具标题也一致。
增加混合调用相邻读取不合并、并行一失败一未完成时仍 Exploring 且可查看错误，
最终完成显示失败退出码的测试。无执行语义变更。

定向分类/显示单元 49 passed；加入历史重放、探索真实命令、长输出完整展开、
流式交错工具 PTY 共 68 passed，4 workers，8.29 秒，12 个已知 forkpty warning。
ruff 与 git diff --check 通过。第 5 项仍待完整历史展示容量/生命周期审计，未勾选；
第 1/2 项及最终联合验收也仍未完成。

## 第 2 项：大项目文件候选的有界流式筛选

再次阅读 Codex file-search/src/lib.rs::walker_worker/matcher_worker：ignore walker
hidden(false)、follow_links(true)、require_git(true)，跳过单个遍历错误；文件与目录
一起注入，Nucleo 使用 match_paths、CaseMatching::Ignore、Normalization::Smart，
仅发布排名前 limit 个匹配。Corki 原来先缓存全部 rg 输出，并对整个项目设 10,000
文件/目录限制，所以精确搜索也会因不相关文件过多失败，区别于“限制结果数量”。

本轮 reference_completion.FileCandidates 接收 NUL 分隔流，跨 chunk 保留完整 UTF-8
路径，只缓存当前前 100 个匹配；父目录去重也仅占候选容量，无全项目集合。查询传入
扫描过程而不是取完全集后才过滤。owned_process.run_owned 增加可选同步字节消费者，
默认调用行为不变，消费者模式不累积原始 stdout；仍累计总传输字节检查上限，任何
消费者异常/超时/取消沿用原有 owned child/process group 的 kill + join 清理。
文件扫描总传输限制 64 MB、单路径 64 KiB、3 秒超时；超过这些安全界限明确报错，
不会把不完整结果当成成功。不是无限缓存或只取遍历顺序前 100 个文件。

新增 20,000 路径随机块边界/中文/重复/完整排序对照与候选容量断言；10,000/10,001
候选之后的匹配、目录查询、无效 UTF-8/控制名/不完整流/路径超限；消费者失败回收，
消费模式总输出恰好等于/超过上限。真实临时项目 10,002 文件经 rg 找到中文精确匹配。
第一轮相关单元 35 passed；全部 CLI 单元、搜索 PTY（40/100 列文件/目录/技能/图片）、
owned process 单元合计 1069 passed，8 workers，22.76 秒，8 forkpty warnings。
后加真实大目录测试的 reference_completion 全文件 11 passed，1.54 秒；ruff/diff 通过。

第 2 项仍未勾选：空目录仍需补充、路径排名仍非 Nucleo、循环链接错误行为仍有差异；
当前改动只解决全项目容量导致候选整体失效的问题，不声称实现完整 Codex 搜索器。

## 第 1 项独立验收：粘贴清理顺序与撤销/突发缓存

复核 Codex bottom_pane/chat_composer.rs::handle_paste、提交长度校验和
history_cell/messages.rs::sanitize_user_text 及 messages_tests.rs。先 CRLF/CR→LF，
移除 CSI 与除 Tab/LF 外控制字符，再以 Unicode 字符数 >1000 折叠；未闭合 CSI
移除剩余尾部，非 CSI ESC 只移除控制字符。提交展开后 >1<<20 拒绝并保留草稿。
旧 Corki 仅换行归一化，因此控制序列也计入折叠字数/提交内容。本轮增加同规则
sanitize_user_text，仅应用粘贴输入，不改工具输出的 lossless 转义和运行时历史。
覆盖 Codex sanitizer 原测试及 Unicode/未闭合 CSI/OSC 差异；真实 composer 在
1000/1001 阈值、带图片位置、历史写入和显式 Enter 场景均验证。

资源审计发现 prompt-toolkit KeyProcessor 除 Corki rich undo 外仍调用 Buffer 的
无界纯文本撤销缓存。主输入框禁用重复缓存，现有 Ctrl+X Ctrl+U 同 Ctrl+_ 使用
ImageDraft 撤销；其他弹窗 buffer 不受影响。不引入可配置按键或 Vim 功能。
rich undo 最多64条/历史文本100万字符/共享元素4400万字符；清空释放全部引用，
旧快照被逐条淘汰，不删当前草稿。新增真实按键折叠→删除→两种撤销→完整提交，
以及容量/清空断言。突发缓冲在达到合法最大消息长度时转存当前草稿，保留 Enter
抑制窗口和全部内容，避免瞬时字符列表无界；取消同步 drain、timer cancel 仍有效。
活动草稿是用户尚未提交的输入，不因缓存限制静默丢弃，过长时提交校验明确拒绝。

必要差异：超长非 bracketed paste 可分成多个折叠元素，而非 Codex 的单个无界
突发字符串；合法长度不受影响，完整展开一致。超长报错使用 ptk 校验栏而非 Codex
历史错误 cell。撤销属于已有 Corki rich draft 能力，其容量限制不支持无限撤销。
此前阈值/编号碰撞/字面伪标记/编辑删除/队列重新合并/同次运行历史/非 ASCII
retro-grab/8ms 时序/120ms Enter 保护等单元与 PTY 证据继续有效。

初次联合回归 1082 passed、3 failed，均为旧测试要求保留 ANSI 原文，与新确认的
Codex 清理规则矛盾；据源码更新断言，仍要求无控制码清屏、完整普通文本及正确历史。
最终全部 CLI 单元+粘贴/图片/排队/停止队列/审批 PTY：1185 passed，8 workers，
44.89 秒，114 个已知 forkpty warnings。另增40/100列两次中文长粘贴、删除撤销、
resize、仅一次完整提交的真实 PTY；粘贴 PTY 全文件6 passed，5.45秒，6 warnings。
ruff/diff通过。第1项勾选；第2/5及最终整体联合验收仍未完成，未声明 goal 完成。

## 第 5 项：关闭历史窗口释放派生缓存

源码依据 Codex app_backtrack.rs::overlay_forward_event/close_transcript_overlay：
关闭时离开 alternate screen、处理 deferred history、overlay=None 释放其拥有的
TranscriptOverlay/PagerView/live-tail 派生数据，canonical transcript cells 独立保留。
Corki HistoryView 是常驻 UI 对象，原 close 仅隐藏，content/formatted/lines/cache_key
仍保留全文的多份渲染表示。本轮 close 在 finally 清除这些缓存、deferred_output
及旧焦点/屏幕模式引用，终端 write_raw 失败也释放；重开按当前宽度从 transcript
重建，不删除或截断真实输出。

新增成功/终端断开两条测试：500行工具输出关闭后缓存为空、完整源不变、重开全部
500行与末尾可见、重复关闭不重复刷新失败输出。第一轮 PTY 发现额外重置 row 改变
既有导航检查，撤掉不必要的位置重置，保留原来的整数位置/行数，仅释放大对象。
最终历史/重放/探索单元与历史、长输出、交错工具、表格流式 PTY：45 passed，
4 workers，14.75秒，26 forkpty warnings；ruff/diff通过。第5项仍待打开期间的
派生渲染缓存容量/分页审计，未勾选；第2项及最终联合验收也仍未完成。

## 第 2 项：空查询与过期目录加载不启动扫描

复核 Codex chat_composer.rs::sync_file_search_popup/sync_mentions_v2_popup、
file_search_popup.rs::set_empty_prompt 与 file_search.rs::FileSearchManager::on_user_query。
只有 `@` 时清除文件查询/搜索会话，仍可显示本地技能插件候选；并非列出全项目文件。
Corki CommandCompleter 原空查询也调用 files，现仅非空查询扫描。另在异步 catalog
返回后立刻检查 generation，避免已过期请求继续启动目录扫描，最终发布前的检查保留。
这不改变非空文件引用的绑定、候选确认或运行时语义。

新增空 @ 不扫描但保留真实 skill selector，以及慢 catalog 期间用户退回 @/改为普通
文本时不再启动旧扫描的测试。候选/错误/命令单元与40/100列文件/目录/图片/技能
绑定 PTY：38 passed、8 forkpty warnings，4 workers，7.38秒；ruff/diff通过。
路径排序源码确认 Nucleo match_paths/Ignore/Smart 与路径字典序平分；尚未据此完成
路径评分实现，仍不勾选第2项。空目录候选和打开期间历史缓存限制仍为后续工作。

## 第 2 项：独立本地搜索程序实现并验证，尚待接线/打包

本轮找到本地 Rust 1.95 工具链（未在 PATH），不再用 Python 模拟 ignore/Nucleo。
新增 native/file_search 独立 crate，仅依赖 ignore=0.4.25、固定 Nucleo matcher
4253de9faabb4e5c6d81d946a5e35a90f87347ee 和 JSON/Serde，无 Codex/OpenAI 服务依赖。
版本来自本地 Codex Cargo.lock；读取 matcher/src/pattern.rs、config.rs 与
nucleo/src/pattern.rs 确认 parse/reparse、match_paths/Ignore/Smart 使用方式。
实现自己的单次请求进程与有界前100候选容器，不复制 Codex 应用层 Rust 模块。

walker 与 Codex file-search::walker_worker 一致 hidden(false)/follow_links(true)/
require_git(true)，单条错误（含链接循环）跳过，空目录作为实际 Directory 候选。
以 Nucleo 路径得分降序、路径字典序平分排序，不再使用技能名称的评分。总遍历3秒
期限、输入16KiB/查询1000字符/最多100结果；期限失败明确非0退出，不伪报完整结果。
Python 测试通过现有 run_owned 启动/超时/回收，无实际模型、密钥或剪贴板。

Cargo release 首次构建成功，格式化后 locked/offline 重建通过；新增集成8 passed，
4 workers，2.98秒，覆盖空/隐藏目录、gitignore、非Git父级ignore、外部链接拼写及
循环链接容错、重音归一化、路径边界排名、^前缀、10001目录后的精确匹配和请求界限。
首次测试发现旧假设 `.git` 不应出现不成立：Codex默认exclude为空，原生walker确会
提供 `.git` 目录自身，按源码修正此断言而未私自追加规则。ruff/diff通过。

构建产物位于 /tmp/corki-file-search-target/release/corki-file-search；锁文件/源码/
README及独立集成测试已入工作树，sdist包含源码。**CLI仍使用旧rg路径，本项未完成**。
下一步须完成可靠的开发安装及wheel打包、依赖许可说明、CLI调用接线、避免再次用技能
评分重排路径结果，并重跑真实候选/取消/异步过期PTY；不能只凭helper测试勾选第2项。

## 第 2 项：原生路径结果接入候选及真实运行时

native_file_search.py 以 run_owned 启动安装目录中的 helper，5秒外层超时、8MB输出
上限，验证输出数组/100候选、路径类型与相对路径、重复/控制字符、bool目录标志、
u32得分，畸形结果不可选择。没有helper时保留rg安装兼容；helper运行失败则明确
报错，不能悄悄降为不同搜索语义。取消沿用 owned process join，不吞 CancelledError。
Reference 新增可选 file_score，原生结果直接按负分进入候选排序，禁止再次按技能名
匹配，否则 café→cafe 或 ^前缀会被错误删除。未改变文件实际路径插入/图片解码/
技能与插件selector运行时绑定链路。

验证：协议边界单元、原生真实目录及菜单排名集成、既有工具候选评分共37 passed，
4 workers，3.49秒。真实CLI候选PTY扩展rg/native两后端，原生目录场景故意不创建
子文件；40/100列文件/空目录/图片/技能经真实CorkiApplication和LangGraphRuntime
传到确定性本地Model，assert实际user内容/图片位置/selected skill context与仅一次
调用。与相关引用/过期错误单元合计38 passed，16 forkpty warnings，11.72秒；
ruff/diff通过。测试显式指定/tmp构建产物，不访问账号、密钥或真实剪贴板。

尚待：正式构建/安装与wheel打包、依赖许可收集。当前开发安装未放置helper，默认
实际运行仍会使用rg后端，因此第2项仍不勾选；不存在“测试原生即视为已发布”的假设。

## 第 2 项：默认安装、校验与 wheel 实证

新增 native/file_search/build.py/artifact.py：locked Cargo build，记录Rust/Cargo源码
摘要、二进制/许可摘要，复用已有通用二进制格式/架构/部署目标检查；收集全部已锁定
crate许可证及当前Rust标准库COPYRIGHT-library.html。新目录完整构建后rename安装，
已有目录拒绝覆盖。补齐小写license文件模式后构建成功，开发bundle已安装到
src/corki/_native/file_search（gitignore），默认CLI现实际选用原生路径。
首次不含标准库说明的本轮自产bundle移至/tmp/corki-search-wheel.Cl4yOh/initial-bundle
留作可恢复备份，最终bundle含完整已收集说明；未移除用户文件。

现有hatch hook追加搜索bundle校验/force_include/native tag，不修改官方服务或
sandbox执行语义。与现有sandbox资产共存时再次验证两者适合最终平台tag。原rg测试
显式固定后端，不让本机安装状态偷偷改变fallback语义断言。
默认安装后全部CLI单元、原生集成、rg/native候选PTY：1117 passed，16 forkpty
warnings，8 workers，16.02秒。最终wheel实际构建成功：
/tmp/corki-search-wheel-final.0rFCwo/corki-0.1.0-py3-none-macosx_15_0_arm64.whl。
再测试artifact完整性/二进制和两份说明损坏拒绝/已有目录保留/解包后的默认入口实际
查找中文空目录（无测试resolver覆盖），加原生与既有compiler build单元共21 passed，
4 workers，3.33秒；ruff/diff通过。

必要差异：单次进程/查询而非Codex持久索引，3秒遍历/5秒外层deadline；未构建helper
的安装仍可使用旧rg降级，功能不完全等价。构建前须按README安装bundle。仅本机macOS
arm64 wheel/执行已实证，Linux ABI与Windows原生打包未验证；没有发布制品。
第2项尚待最后按引用全链路清单验收；第5项和最终整体回归仍未完成。

## 第 2 项独立验收：插件候选到真实工具选择

最终复核 Codex bottom_pane/mentions_v2/search_catalog.rs：插件显示名与config_name
分离，选择项绑定 plugin://config_name；@菜单隐藏已呈现插件拥有的技能，$仍可直接
选技能。Corki catalog/InputMention/DraftText/input_image_kwargs/runtime 的完整链路
对应，文件则插入实际相对路径并复用图片候选解码，不把显示名伪造为运行时绑定。

扩展 test_plugin_requirement_identity 的 legacy/agent 两种本地manifest场景：从
runtime.input_reference_catalog 真实目录进入CommandCompleter，@second搜索，真实
CompletionState/accept_reference选择，完整草稿提取mentions，再runtime.stream。
模型仅能使用被选中第二安装实例的MCP工具；验证只等待第二实例启动、只调用其lookup，
first未被等待/调用，checkpoint记录的required_plugins是fixture@second。MCP连接均
为本地测试double，未发起网络服务连接。初次fixture错传LoadedPlugin而非公开目录API，
修正为与CorkiApplication一致的input_reference_catalog，不为测试改生产类型边界。

引用别名/过期查询/错误/原生协议边界/owned process回收加插件运行时共58 passed，
4 workers，4.37秒；补跑host skill选择、plugin skill sources、MCP attribution、
回溯绑定恢复55 passed，5.26秒。此前1117 CLI+原生+PTY回归以及21 bundle/wheel
默认入口证据有效；ruff/diff通过。

逐项结论：本地文件/空目录/技能/插件可搜索、选择、取消；引用绑定穿过图片/长粘贴/
排队/同次历史/分支；异步过期不提交或误绑定；候选容量/子进程超时取消回收有界；
宽窄屏真实PTY选择仅提交一次；默认开发安装及本机wheel不依赖测试覆盖即可调用原生。
第2项勾选。差异不隐瞒：原生单查询子进程而非常驻索引，显式时间/结果限制；无helper
的旧安装降级rg（空目录/评分不等价），须按README构建bundle；Windows/Linux实际
原生平台发布未验证。仍有第5项及最终整体联合验收，goal未完成。

## 第 5 项：有界历史行存储与流式渲染接口（窗口接线待完成）

参考 Codex pager_overlay/scrolling.rs::CellRenderable::render_scrolled 与
render_offset_content：只向viewport输出可见行，避免隐藏行的大缓冲。检查ptk ANSI
源码发现现有HistoryView的ANSI(full_text)为每字符构造tuple，长输出会放大内存。
新增history_rows.HistoryRows，复用同一个ptk ANSI状态机跨write/行维护SGR样式，
逐行合并同样式片段写入私有TemporaryFile，另一文件保存定长偏移索引；只有访问过
的行进入LRU，最多128行/1MiB序列化行内容。两个文件合计512MiB上限、单行65536
字符上限，错误显式抛出；canonical history完全独立，禁止用容量限制删除源输出。
close清除缓存并关闭/移除两个匿名临时文件，context manager覆盖异常路径。

Transcript.render新增可选output sink，原调用返回str行为不变；有sink时Rich直接
写入，不先建立全文StringIO。真实Transcript完整1000行工具输出与旧渲染逐字对照，
容量失败时原console/replaying/expand_tools及源calls恢复，文件关闭。
初测发现空行存储的__len__令Rich把sink当假值退回stdout，补__bool__始终True；
颜色测试显式no_color=False，不改变产品对宿主NO_COLOR的尊重。

行存储/Transcript/探索单元23 passed，4 workers，1.80秒；补容量失败状态恢复后
行存储全文件9 passed。覆盖逐字节CSI拆分/跨行颜色/中文、1万隐藏行缓存容量与回读、
Rich实际输出、行/总容量错误、重复finish/close。ruff/diff通过。
尚未将HistoryView切换到新存储；窗口打开期间仍走旧全文缓存，下一步须完成接线、
异常反馈、frame与关闭的文件生命周期以及真实PTY回归，不能凭这些单元勾选第5项。

## 第 5 项：历史窗口使用有界行存储，分页及流式回归

HistoryView.text 改为直接让Transcript.render写入HistoryRows，不再保留全文content/
formatted和逐字符split_lines副本。HistoryControl按行索引读取；text的内部调用者
可迭代片段，不自动物化全文。每次新来源/resize产生独立不可变行快照，旧UIContent
引用保持不变；ptk Window源码确认frame缓存最多8份，弱引用登记全部活快照，丢弃
frame后finalizer回收文件，显式close立即关闭全部快照。finish断开ANSI sink自引用，
避免等待循环GC。窗口级全部存活行存储合计512MiB预算，而非每个frame无限叠加。

存储/容量失败在窗口显示固定可取消错误，不泄露底层文件路径，不删canonical输出；
失败store关闭，close/reopen后可重试。新增容量故障→关闭→恢复完整100行测试，
现有关闭失败/重复关闭/旧frame快照/resize/相同来源不重复解析测试适配行存储。
分页加载HistoryPager原交换content/formatted造成2个PTY失败，已改为交换line store；
候选render_error时不发布source/cursor，保留原有加载事务回滚。计划尾部单元不再
检查已删除的内部content字段，改对照实际可见行=committed渲染+preview，且队列和
canonical calls不变。未弱化“隐藏计划不能被提交进历史”约束。

最终全部CLI单元及历史、长工具输出、交错工具、流式表格、计划尾部PTY：1136 passed，
8 workers，18.61秒，34 forkpty warnings；ruff/diff通过。第5项仍待最后生命周期
收尾：关闭期间renderer异常与晚到历史加载，以及历史打开期间deferred_output缓存
上限仍需核对；不能仅因当前联合回归全绿就声明完成。整体goal仍未完成。

## 第 5 项：屏幕异常与关闭后晚到页面的回收

复核 Codex app_backtrack.rs::close_transcript_overlay，离开alternate screen失败不能
阻止overlay所有权清理。Corki原renderer.erase在finally之外，错误会跳过临时文件和
模式/焦点恢复。现open/close共用_restore_and_release，擦屏/写入/焦点路径异常仍
清除deferred缓存、关闭所有frame行存储并重置引用；终端IO错误仍向调用者报告，
不虚称断开的物理终端已成功重绘。新增open/close两种erase故障，验证内部模式/
焦点及canonical历史不变、全部文件关闭，恢复输出后可重开完整历史。

HistoryPager的异步读可在用户Esc关闭viewport后完成，_replace_history会构建新的
隐藏frame。现_load_older finally在viewport不活跃时立即_release_rows：不丢已加载
canonical页面，也不让隐藏窗口留住渲染文件。新增真实SQLite/runtime分页的延迟
返回→先关窗口→完成读取→所有store已关闭→重开仍能看到旧问题测试；全部Model
禁止调用，未使用服务。原cancel join/clear不复活历史场景保留。

异常回收+历史PTY27 passed，18 forkpty warnings，9.43秒；随后分页/压缩分页/
异常窗口单元集成18 passed，3.05秒，4 workers；ruff/diff通过。仍剩打开窗口期间
deferred_output的容量处理及最终全量联合验收，第5项未勾选。

## 第 5 项：延迟输出有界与实际屏幕恢复

HistoryView 的主屏输出重放队列现在最多 1,000,000 字符/4096 块；超限清空该
优化队列并标记 canonical transcript 重绘，不丢源历史，也不继续积压输出块。
关闭窗口后由 Application 原有、退出时取消并等待的 resize watcher 重绘；
无额外后台任务。关闭或异常清理同时重置队列计数，保留待重绘标志。
字符/块两种超限单元验证20条中文通知逐条完整且只出现一次，重开历史仍完整。

真实PTY新增40/100列超限场景：历史打开期间不跳出alternate screen；关闭后
检查清屏重绘序列，通知和回答各出现一次，草稿仍可原样提交。初测fixture只运行
TerminalUI，缺少Application的watch_resize生命周期，补真实监听启动及cancel/join；
明确等待异步重绘而非首帧草稿出现就提前断言。未更改生产等待时间或弱化内容断言。
历史窗口单元+完整历史PTY最终30 passed，20条既有forkpty警告，10.34秒。
开始8 worker的全unit/integration与全E2E最终回归，尚未以启动测试代替验收。

## 第 5 项独立验收及最终 E2E

调用归类/聚合/失败顺序、长输出从executor与Code Mode到完整历史、窗口容量与
异常/取消生命周期已逐项补齐；第5项独立勾选完成。保守命令解析、分组容量和
有界磁盘行存储属于已说明差异，不能宣称逐像素等同Rust界面。
最新全 `tests/e2e`：400 passed，409 warnings，267.74秒，8 workers/load，
实际退出0。包括新增原生文件候选、历史溢出恢复及所有既有CLI交互PTY。
警告主要为macOS/Python的多线程forkpty弃用提醒，没有忽略失败或跳过这些PTY。
新构建wheel `/tmp/corki-cli-acceptance-wheel.GfJWdu/corki-0.1.0-py3-none-macosx_15_0_arm64.whl`
通过6项安装包测试（9.17秒），确认默认搜索helper、权限、许可证及校验链路。
全unit/integration尚在运行且已出现失败，整体goal继续保持未完成，须核对最终报告。

## 全项目回归失败排查与隔离复验

全unit/integration首次结果：4 failed、17104 passed、8 skipped，679.58秒。
不能记录成全绿。串行复现相关两个文件：3 failed、21 passed，9.83秒：
大目录搜索失败未复现，其余三项稳定失败。逐项定位如下：

- `test_cli_stream_cleanup` 的两个交错输出fixture先完成tool item，却在最终
  ModelCompleted里倒置为(message, call)，违反运行时已完成项目顺序约束。
  与此前真实PTY fixture相同，修正为(call, message)，不修改运行时校验。
- 取消测试仍覆盖旧show_tool_started；生产已使用带call_id的
  show_identified_tool_started。改为监听真实回调，保持“完整显示一次、实际
  tool取消回收、无running turn”原断言，没有放宽取消语义或时间阈值。
- 10001目录搜索在高并发全项目运行时helper退出1，串行原样通过；该fixture
  对3秒生产扫描期限及磁盘资源敏感。未加重试、提高生产超时或减少目录量；
  最终验收隔离串行运行此项，其他无冲突用例继续8 workers。

两文件修正后完整串行24 passed，5.52秒，退出0。继续包含全部CLI单元、执行器/
存储、runtime/图片/分支/分页/插件/Code Mode及打包搜索的联合回归。
ruff check全部src/tests/native通过；相关范围1066文件format检查通过，diff检查通过。
全仓format额外发现两个不在本任务范围的既有文件（tests/example/vivi.py、
tests/unit/protocol/test_namespace_identity.py）格式差异，保留用户文件未改。

## 最终验收结论

最终相关联合回归1233 passed，13.56秒，8 workers/load，退出0。
覆盖全部CLI单元、执行器/owned process/会话归档、流式取消、分支、分页、图片提交、
插件身份绑定、Code Mode、原生文件搜索及新wheel安装验证。10001目录资源敏感用例
从该并行批次隔离后，完整原生搜索文件另行串行9 passed，2.51秒，退出0。
此前修正两文件的串行24 passed保持记录，未丢弃或弱化原验收断言。

全E2E400 passed、409 warnings；生产代码此后没有改动，仅修复上述测试夹具。
第一次全项目回归17104 passed/4 failed/8 skipped如实保留，不能改写成同次全绿；
4项均已在后续修正/隔离批次通过。条件跳过不计作通过；新wheel条件测试已另行
显式提供产物执行通过。未用实际付费供应商或真实用户剪贴板补测。

本地最终wheel SHA256：
`9a731f81bebba2cadb05be03ad81b7dd84db7294f3a5fe63cefd6c86308c8eb1`。
没有发布/提交/推送。八项清单独立完成；使用说明、必要差异、平台/真实终端限制
及准确测试批次见 `cli-universal-acceptance.md`。目标不包含已明确排除的功能。
