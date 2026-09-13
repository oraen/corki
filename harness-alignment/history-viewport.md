# 独立历史视口（F，部分实现）

导航成本审计（实施前）：当前 HistoryView.text 虽缓存 Rich 渲染文本，但每次都新建
ANSI，FormattedTextControl.create_content 每次重新展开 fragments、split_lines，并
把整份 fragments tuple 用作 cache key。上下键只改 row 也重新遍历整份已加载文本。
Codex pager_overlay/scrolling.rs 的 render_scrolled 只向可见区输出，分页缓存另由
overlay 管理。计划让 Corki 历史控件使用缓存行的 UIContent.get_line，源/宽度变化
才重新解析 ANSI/切行；导航帧只更新 cursor。不声称源变化后的 Rich 全量渲染或持久
分页也已解决。验收直接通过真实 HistoryView.control.create_content 多次移动，
验证没有重复 ANSI 解码/整行拆分，同时样式、行数、尾部跟随和 PTY 输入隔离不变。

本批实现：HistoryControl 已替换历史区的通用 FormattedTextControl（标题保留原控件），
UIContent 绑定当前缓存 rows 的 getitem，旧帧持有旧行集合，不被下一次刷新偷换。
HistoryView 在源/宽度 key 变化时生成 formatted fragments 与 lines；移动只更新 cursor。
专项修复前因每次重建行对象失败（a30ce5）；修复后移动不重复 ANSI/split_lines，
新通知与宽度变化各失效一次，样式与旧帧快照验证通过。初批历史单位/真实 PTY16通过
（d9a231，20.91秒）；扩展后的全部 CLI 单位与流清理296通过（39717a，6.28秒）。
仍会在源变化时全量 Rich 渲染，且源 key 仍遍历条目身份；不宣称所有导航成本已为常数，
也不宣称已实现按 cell 增量渲染或数据库分页。静态1100文件/77包等通过（c459bd）。

扩大实际审批 PTY、冷 CLI 历史、reasoning 与真实恢复69通过（62bf73，59.21秒）。
参考 commit 不变且干净（db86e8），测试全部结束。完整 A–F 仍未完成。

基准纠正（优先于下文早期缺口列表）：当前 Codex pager_overlay.rs::PagerView.render
按当前内容高度夹紧数值 scroll_offset；TranscriptOverlay 的尾部跟随使用 bottom 状态。
prepend 专门按新增内容高度补偿数值偏移，原生测试
transcript_overlay_preserves_manual_scroll_position 验证前插前后可见内容不移动，
transcript_overlay_keeps_scroll_pinned_at_bottom 验证高度变化和新尾部仍跟随。
这不是跨任意宽度变更的原文字符/source 锚点实现。因此早期把 source 级 resize 锚点
列作必补的 Codex 差异缺乏依据；不据此扩张实现。不能反过来认定前插位置补偿已完成：
Corki 仍没有持久分页，未来加载旧页仍必须保留浏览位置；可见行渲染/缓存也仍有实际差异。

新增真实 PTY 宽40→100→40与100→40→100，并改变高度：尾部跟随、Home后顶部
保留、备用屏幕和输入草稿唯一恢复。首版原始字节断言失败（5dfd5d），诊断3758aa
确认渲染器复用已有共同前缀，仅输出行号后缀，并非 Home 未跳到顶部。测试改用与
尾部不同的顶部标识，避免把终端差量字节当作完整屏幕。没有修改生产布局或过滤逻辑。

最终顶部标识使用与旧画面不共享字符的连续 Ω，避免英文标识也被按共同字符拆分；
真实双向缩放2通过（59e14f，3.43秒）。子进程同时断言尾部行跟随、Home 后行0
保持、备用屏幕仍活动、草稿内容不变；退出后输入历史只有原草稿一次。此证据覆盖
缩放行为，不证明旧页前插补偿或数据库分页，不虚称新增 source 锚点实现。

本轮最终 CLI 单位、历史/审批真实 PTY 及 CLI 流清理337通过42个forkpty弃用警告
（c22cbb，80.14秒）；静态1099文件/compileall/77包/diff通过（a538c5）。
参考 commit ddf04ad26789d040f9ef6a96736f76602e35a6cc 未变且干净。无活动测试。

活动输出后续审计（实施前）：Codex app/resize_reflow.rs 把overlay期间的display追加到
deferred_history_lines，app_backtrack.rs关闭后排入普通历史；Corki remember_display
仍直接打印，stream_commit/Transcript.repair仍进入in_terminal，导致浏览时反复切屏，
repair甚至清理主屏滚动区。状态行为不一致。计划保留源和流状态更新，只延迟屏幕写入，
关闭后一次提交；repair在overlay期间保持pending。真实PTY须证明新正文/通知在历史内
可见而没有1049l，关闭才退出，并且普通历史输出不丢失/不重复；不改模型历史与协议。

该批实现：remember_display 在历史活动时同步capture已渲染字节，仍记录source并执行
流状态变化；stream_commit不再为这些delta进入in_terminal。短回复完成的直接写入也
纳入同一capture。close在恢复主屏后按顺序write/flush并清空队列，reader取消沿用close。
Transcript.repair在历史活动时保留pending并刷新历史视图，不清主屏；退出后既有watcher
继续repair。范围是Corki显示方法，第三方直接stdout日志仍不在该队列内，不宣称全局
输出统一完成。字节队列的resize重排和主屏canonical repair仍沿用既有机制，非源级锚定。

新增单位先红（5fc82a，浏览时直接打印）；实现中修正Rich capture必须退出后get的错误。
扩大CLI回归发现4个__new__夹具绕过HistoryView构造，改为实际TerminalUI构造，保留
原流/最终历史断言。最终CLI单位+历史/审批/表格PTY+流清理331通过42个forkpty警告
（96b39b，79.92秒）。随后流式/短回复×40/100的活动历史PTY4通过（071b54），
证明新通知正文在历史内显示、不提前1049l、退出后主屏各写一次。静态1092文件、
compileall、77包/diff检查通过（898988）。无真实外部模型测试。

本轮实施前差异：Codex app_backtrack.rs::open_transcript_overlay →
tui.enter_alt_screen，close_transcript_overlay → leave_alt_screen 后恢复普通界面；
Corki HistoryView.open/close 仅切换 ConditionalContainer，浏览会占用主滚动区。
计划使用同一 prompt-toolkit renderer 的全屏模式，在切换前 erase/reset 清除旧帧，
关闭恢复 inline 模式，沿用 read_message finally 的取消/审批收尾。验收要求真实PTY
观察备用屏幕进入/退出序列，关闭后原草稿唯一提交，以及审批抢占回归。输出到主屏时
现有 in_terminal 仍会暂时离开备用屏幕，并不等同 Codex deferred_history_lines 队列。

参考 Codex pager_overlay.rs 的 TranscriptOverlay、PagerView.handle_key_event，以及
pager_overlay/scrolling.rs：历史独立于主视口，支持逐行/翻页/首尾导航，新增内容在底部时
跟随、离开底部时保留位置。原 Corki Ctrl+T 只是 repair 主滚动区，没有可滚动只读视口。

新增 HistoryView，接入同一 PromptSession layout 和 key bindings，不创建应用或第二个
stdin读取者。Ctrl+T打开，方向键/PageUp/PageDown/Home/End导航，Esc/q/Ctrl+T关闭。
保存原焦点及输入Document；普通字符、Enter和Tab在历史视口内不进入composer。
退出read_message（含取消）关闭视口并恢复焦点，取消保留原草稿。详细文本来自既有
Transcript.render，缓存根据源条目身份和宽度失效；底部跟随、上翻不强制跳回。

真实PTY40/100宽度覆盖60行历史、首尾/翻页、隐藏草稿、浏览字符/Enter/Tab不提交、
q后原草稿唯一提交。新增单位覆盖焦点、底部跟随、上翻保留和reader取消。原有活动
表格PTY也覆盖运行中切换历史后steering，保持单一输入所有者。

初始实现的未完成项（全屏切换已由下述后续批次修复）：当时仍是同一inline app内的
独立viewport，不是Codex全屏alternate-screen overlay。
尚无持久分页、全量源渲染之外的可见行优化、滚动位置的source级resize锚定、搜索/选择。
审批中断/并发事件/所有取消键的完整交互矩阵还需要继续验证，不能凭当前测试宣布全F。
不访问官方平台，不改变模型历史，不改teach.md。

验证：最初单位测试在无event loop下设置真实Buffer导致夹具失败，改在asyncio场景运行。
扩大回归发现旧preemption测试以__new__绕过构造，补齐视口夹具并断言两次退出均close，
原草稿断言保留。最终CLI单位/Runtime清理/历史及活动表格PTY294通过6个forkpty警告
（d7c8dc，15.76秒），静态1092文件、compileall、77包/diff检查通过。无活动测试。

输入隔离补验：实际PTY增加bracketed paste多行内容，仍仅提交原draft，40/100宽度2通过
（b98774）。并发approval原6场景再叠加历史视口已打开，使用真正InputOwner.elicit抢占
reader，确认视口关闭、默认选择不自动提交、first显式接受后second拒绝/取消/排队取消，
最后原草稿+followup唯一提交，表单/输入历史无串入。12组合通过（23e373）。
本轮无生产修改；InputOwner的锁/取消join与TerminalUI.read_message finally真实协作。
Codex app/input.rs active_keymap_contexts 在overlay使用Pager上下文、modal禁用全局
composer快捷键，是输入隔离参考；不声称当前inline viewport已等同原生完整overlay。

完整approval PTY、历史paste PTY及相关输入/视口单位74通过（a7405f，55.66秒），
静态1092文件、compileall、77包/diff通过，无活动测试。全屏、分页与滚动锚点仍开放。

全屏后续实现：HistoryView 在打开前 erase inline frame，切换 Application/Renderer
两处 full_screen，使下一帧进入备用屏幕并填满终端；关闭 erase/reset 离开备用屏幕、
还原原模式及焦点，运行中重新请求主屏光标位置。read_message finally 仍负责取消和
审批抢占收尾，不增加输入reader。参考补充 tui.rs::enter_alt_screen/leave_alt_screen
的保存/恢复主视口与丢弃跨屏diff基线。Codex基准commit保持ddf04ad26789d040f9ef6a96736f76602e35a6cc，参考树无改动。

新增PTY备用屏幕序列断言先失败（b240cd，缺少1049h），修复后初批34通过
（0928fe，52.56秒）。扩大关闭键覆盖q/Esc/Ctrl+T；审批场景现在等真正备用屏幕
渲染后才请求抢占，并断言1049h→1049l发生在审批展示前，不仅检查active布尔值。
这不等于原生deferred_history_lines输出队列：活动输出仍可通过in_terminal暂时切回
主屏再重绘历史。分页、source resize锚点、可见行渲染及统一输出排序保持开放。

本批最终验证：CLI单位/历史/审批/流式表格PTY321通过40个forkpty弃用警告
（6eb709，73.71秒）；独立CLI流清理7通过（a72ced，1.29秒）。一次扩大测试命令
误用不存在的test_stream_cleanup.py，未采集任何测试；随后发现并执行实际
test_cli_stream_cleanup.py，未用失败命令冒充验证。最终ruff/format1092文件、
compileall、77包依赖检查、git diff --check均通过（94d0f7），无活动测试。
