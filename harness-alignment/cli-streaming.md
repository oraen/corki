# CLI 流式正文：真实链路与待实现契约

最新动画主路径已接：显示消费者持有事件读取与 tick，正常一行、积压追赶，
完成/失败/取消排队列后关闭。stream-animation-design.md 记录真实 Runtime
逐行/突发测试及修正；联合 402 通过（9a0a57），新增 burst 后专项 17 通过
（50f9c2），静态通过（c254ad）。精确定时/历史切换/复杂缩放仍待收敛，不能
继续将主路径标为完全缺失，也不能宣称整个动画/F 完成。

最新行队列接入：StreamMarkdown.write 真实路径已拆为 enqueue/drain，记录
不可变渲染行、队首年龄与已显示边界；分批提交及积压缩放测试通过。CLI/真实
Runtime 流清理 379 通过（7dd269）。当前保持立即排空，动画定时/追赶策略
未接入，详见 stream-animation-design.md，不把结构准备当作完成动画。

最新动画源码审计见 stream-animation-design.md：确认真实渲染行队列、双计数、
8.333334ms tick、积压追赶阈值/迟滞、终态直接规范化及 resize/replay 所有权。
Corki 仍全量提交本次 remaining，未实现该动画路径；不要将 stream_commit
误认作队列调度，也不能用每 delta 后 sleep 替代。此轮仅形成具体实现前证据。

## 双重失败首因丢失（已修复）

本地 prompt_toolkit/application/run_in_terminal.py 的 in_terminal 在 finally
中 renderer.reset / request cursor / redraw，末尾再完成串行 future。若正文
write_raw 已抛错，这一 finally 又抛错，Python 默认将第二个异常向上传播。
Corki commit_delta 未保留首因，真实 Runtime 双故障回归要求最终报告 write
而不是 redraw，同时继续关闭模型和显示；外部取消仍必须优先传播。

修复限于现有流式输出所有者：捕获正文提交异常，恢复界面失败时记录错误类型
并传播原始提交异常；单独恢复失败照常传播，不重试未知结果的 write。原生
streaming/commit_tick 不执行这种 Python 终端恢复，不能拿它当直接同构代码；
对齐的是 E 的失败分类和清理不掩盖首因，不扩大为原生动画队列实现。

真实 Runtime 双故障回归修复前 3de2fd：普通路径报告 redraw 而非 write，
取消路径仍正常。现保留正文原异常，在退出失败时记录错误类型，以异常链附带
恢复失败；若退出抛取消则继续传播取消。CLI 单元/流清理联合 376 passed、
0 skipped（f9b7a6，9.05 秒），静态通过（713fe8，1126 文件/77 包）。

额外 interleaved_tool / stream_table / stream_repair 三文件真实 PTY 20 passed、
0 skipped（601dd2，27.00 秒）；两批进程均退出 0，无活动测试。该修复不撤销
部分输出、不自动重试终端写入，也未实现原生自适应动画队列。

## 提交失败与取消：本轮直接证据

完整读取原生 streaming/commit_tick.rs：queue snapshot → adaptive chunking
decision → drain plan，从 controller 队首取行，调用者负责历史插入；它本身
不操作 UI。controller.push_delta 只提交完整源行，finalize_remaining 用完整
源规范渲染后按已提交行切余量。Corki 的 stream_commit 是输出所有权机制，
不是原生动画队列/自适应 drain policy 的等价实现，不能混淆两项完成状态。

当前 Application 顺序 await append_assistant_delta_live 后才处理下一事件，
commit_delta 将更新/capture/write/flush 放在同一 in_terminal 阶段；取消会
join 独立提交任务。扩展 test_stream_commit.py 为更新/写/flush/redraw 单点
失败 × 正常/重复取消：普通失败保留原异常对象，取消优先且完成退出重绘，
不把 capture 内容送进延迟 stdout 队列。原有无失败顺序断言仍保留。

test_cli_stream_cleanup.py 的真实 Runtime 故障扩展至 write/flush/redraw ×
取消，要求模型生成器关闭、显示 end 恰好一次、无 running Turn、仅写一次。
这些证明单点失败和取消组合，不宣称同时写入失败及重绘再次失败时仍保留首因，
也不保证 OS 部分 write 可撤销或可安全自动重播。本轮仅补测试，无生产修改。

验证：CLI 全单元 + test_cli_stream_cleanup.py 共 374 passed、0 skipped
（a285ce，8.86 秒，session 69482 退出 0）；全部静态通过（94aa33，1126
文件/77 包），无活动测试。原生动画队列和增量高亮仍是独立的未完成差异。

## 当前源码回查：旧“无原子提交”描述不可直接作为缺口

当前 Application._render_events_owned → TerminalUI.append_assistant_delta_live
→ stream_commit.commit_delta 已接独立任务：TTY/live/StdoutProxy/换行路径在
in_terminal 中 capture，直接 output.write_raw/flush 后恢复 composer；取消
shield/join 该提交。历史视图及其他路径另有分支。这是已存在的实现，不需要
再新增同名输出所有者；本次仅核实入口，未据此宣称所有顺序/取消分支已验收。
下一步应读取 test_stream_commit.py 并对照原生 streaming，而不是按下方旧
chronology 重做功能。StreamMarkdown 仍有全行拼接及代码块高亮成本待核验。
文内早期测试 live 句柄是历史记录，不代表当前运行状态。

## 最新：活动tail稳定前缀缓存

StreamMarkdown.tail_lines 独立缓存full-source解析/body，与稳定输出parser分离；
原文前缀/宽度键缓存行数。真实UI30行追加60次重复渲染先红后≤3次，全文等价
Segments覆盖引用/源替换/围栏/宽度；CLI+表格PTY266通过（c31904）。详见
markdown-table-fences.md。表格自身重排和完整body访问仍非增量，未称全链路线性。

## 最新验证：tail保留完整渲染上下文

原先独立渲染后缀导致开放md围栏提前变成表格，6项先红；现全文渲染切行，与
Codex controller的行坐标桥接一致，恢复gutter。278项CLI/真实PTY通过（9263d4），
详见markdown-table-fences.md。全文渲染性能和输出原子顺序仍待修复。

## 最新验证：有状态高亮不可用无状态逐行替代

完整读取原生render/highlight_streaming.rs：Syntect保留HighlightState/ParseState，
按完整行追加，未知语言永久plain fallback，累计大小/行数/主题变化要求canonical。
本地Pygments常用Python/JS/Rust/Bash是RegexLexer，无公开可恢复的最终stack；
ExtendedRegexLexer虽有LexerContext，回调签名及状态契约不同，不能直接强行套用。
JS未闭合/*注释还会在结束标记到达后重新解释前文，不能假设所有lexer前缀稳定。
新增实际StreamMarkdown跨行颜色契约：Python三引号、Rust注释、JS模板字符串，
流中颜色等同完整当前围栏，独立单行高亮作为反例，闭合后不重复正文。
初测试环境NO_COLOR使全部颜色被抑制；fixture现显式no_color=False验证颜色，未修改
生产尊重NO_COLOR的行为。JS注释不适合作“当前前缀即识别注释”的正例，改模板字符串，
注释的未来依赖作为后续backend设计约束保留。最终颜色+缓存26通过1.22秒（9885ca），
全静态1082文件77包通过（d681cd），最后fixture调整后lint/diff通过（4f5ac0）；
本轮没有引入增量高亮，不能用新增正确性测试替代其实现。

当前全项目22649已实际启动（e002a3），使用既有bundled sandbox后端，SHA256
966d3aaf44f77a78447d8c0758c0fe9594904733da52d8e49a0d2fcb89e4c3ba已复核（6cdcb7）。
最新poll仍live约5%（974033），继续原句柄不重启。采集早于本轮新颜色测试，不冒充其验收。

## 最新实施：开放围栏的解析追加路径

MarkdownParseCache按已解析末尾顶层fence和原文正文完全相等的证据进入追加；仅带
语言、无缩进/CR/NUL/信息转义/潜在关闭行允许。新完整行只更新末尾token.content/map，
复制token身份使body render缓存失效，不能原地修改导致旧代码被复用。潜在关闭行、
源替换等回退到canonical parser；渲染仍使用原Pygments整块高亮以保留跨行状态。
性能场景先红（ed0842）：1390字符100行代码累计解析69800字符；修复后<最终源3倍，
每行token等同全量parser。增加长/短/不同围栏、缩进、信息转义、无语言、NUL和中文
逐行tokens及40/100列body Style比较。CLI+流清理+表格/工具PTY244通过4警告10.84秒
（5a4b1a，40094终态）；最后将代码计量改接实际StreamMarkdown.write，1通过0.97秒
（9a549e），100代码行各一次，关闭后仍无重复。最终全静态1081文件77包通过
（d3ec93），当前无活动测试进程。本批仅解析追加，不宣称实现原生有状态增量高亮，
高亮全块成本/gutter全量遍历/动画输出等缺口继续保留。

当前长围栏审计：已完整读取原生streaming/code_fence.rs。只有原始顶层带语言围栏、
完整行、无CR/NUL/信息转义、正文不含潜在关闭行才可直接追加；关闭/规范化/主题变化
必须canonical回退。Corki目前每增一行重解析整个最后fence，Pygments高亮也全量。
本批先实现围栏token追加，使用与原文正文相等的证据进入快路径并在关闭等条件回退；
渲染继续原高亮以保留跨行语法状态，不假装已实现StreamingCodeHighlighter等价功能。

## 最新实施：顶层块body渲染缓存

MarkdownRenderCache接每流解析文档，缓存该快照顶层块的body Segments，AssistantBlock
仍统一加gutter/换行。缓存保留token对象而非仅整数id，防止对象回收后的id复用命中；
下一快照只保留当前块，不积累已丢弃的最后块。宽度、颜色模式、ASCII、主题样式、
code/inline主题、justify/hyperlinks变化失效。引用定义全文解析更新token身份。
块间分隔按Rich真实new_line和容器规则合并，不改变静态Markdown默认路径。
真实write性能回归先红：100段落5050次Paragraph.render（bc88f5）；接入后<250次，
输出仍100段。初CLI224通过3.27秒（7fdf2b）；13项逐行parse与40/100列body Segments
（含Style）完全比较通过0.78秒（ee47c4）；新增主题切换与整组69934已终态exit0，
249通过18个forkpty警告29.32秒（848e58），停止poll。当前无活动测试进程。
静态1081文件77包通过（acd9d7）。这里降低的是重复段落布局，不宣称总耗时线性：
tokens扫描、body拼接、gutter仍遍历旧内容，长开放代码块仍需专门追加快路径。
动画/原子stdout-tail顺序、最终样式和完整F继续开放。

当前渲染缓存审计：已核对Rich Markdown.__rich_console__，分隔由上一块new_line及
容器内部子节点关闭决定；不能机械用一空行拼每块。原生StreamingRender按完成顶层
块保留rendered前缀，Corki现虽有token缓存仍重复render所有段落。新增实际write路径
段落render调用计量；方案缓存顶层块body Segments，加入现有AssistantBlock gutter
之前合并。token身份变化、宽度和主题样式变化应失效；引用定义全文解析自然更换token。

## 最新实施：每流解析前缀缓存已接真实渲染

MarkdownParseCache接StreamMarkdown→AssistantBlock，保留最后顶层块前的tokens和
源/行边界，后缀重解析后重定位map；Rich样式渲染仍使用同一AssistantMarkdown。
引用定义通过实际parser env.references检测，出现后该源流持续全文重解析，晚到定义
可重释早先文本；源替换重置，CR归一化源走全文解析，源码位置只按换行而非splitlines
的其他Unicode断行算偏移。缓存局限在该StreamMarkdown，不跨消息/会话污染。
10组来源在每行到达后token字典与全量Rich解析完全比较（段落、setext、列表、引用、
代码、表格、晚引用、CR、Unicode）；真实StreamMarkdown.write的100段落计量中，
累计解析字符<最终源3倍，旧全文快照基线>40倍，实际正文仍仅100次。
这些证明解析工作量降低，不是总渲染时间线性：当前render仍全快照，开放长代码块/
长单段还会重解析最后块。渲染块缓存、代码追加快路径、动画/原子输出所有权仍需继续。
CLI全单位+流清理集成+真实表格/工具交错PTY232通过4警告10.48秒（6ebc1a），81540
已终态；包含上一批最后空围栏修复的回归。本批首次静态仅格式未过（fc43ea），
已formatter修正，最终全静态1080文件77包通过（7fe4b6）。当前无活动测试进程。

当前缓存审计：原生StreamingRender.append只保留最后顶层块之前的解析/渲染结果，
引用定义会影响早先块并触发全量recompute。Corki每次AssistantBlock构建都会新建
MarkdownIt并parse完整source（已检查本地Rich实现）。本批先接解析token前缀缓存：
最终顶层块重解析，检测env.references后全文回退，源替换重置。显示行缓存和开放
代码围栏的直接追加仍下一批，不能把解析缓存等同全部长流优化。

## 最新实施：稳定正文改为Markdown渲染行输出

StreamMarkdown接TerminalUI完整稳定源，按已输出rendered行推进，不逐delta重开解析
上下文，代码围栏/inline格式在流中生效。表格tail保持独立；resize按前一源快照重算
计数，Transcript replay保存/恢复活跃render对象。pipe/live-off仍原输出。
两测试先红（229866）；初实现暴露Rich空围栏占位行消耗代码首行（bf2c51），现尾部
空/仅gutter占位不提交，内部空行保留、最终canonical校正。CLI211通过2.80秒
（e09a52），表格/交错工具/流修复/清理23通过26.02秒（d82fd4）；后者早于最后空行修正。
真实40/100列PTY扩展为模型仍阻塞时粗体/code已渲染、表格可变、最终各一次，2通过
4.05秒（f717b8）。初PTY假定stdout必在tail前flush而失败（6d5b72），改等前缀实际
到达再放模型，未把final repair当流中证据。当前patch_stdout与tail绘制先后不是原子
提交，仍需结合动画/输出所有者处理；不要把短暂尾部先出现标为完整顺序对齐。
本实现全稳定源快照重渲染，尚无原生解析块缓存，长流成本可二次增长；这明确是下一
批核心缺口，不宣称已实现高效增量渲染。样式、动画队列/行预算等仍开放。
最后补齐无前导正文的空围栏3变体通过0.54秒（a1cc62），全静态1078文件77包通过
（38e5ac）。当前无活动测试进程；211/23并非最后小修后的整组重跑，下一批需回归。

最新回归99165终态303通过75警告134.23秒（a2fd1e），停止poll。当前推进稳定正文
Markdown：原生StreamCore.sync_stable_queue按rendered line边界推进，而StreamingRender
的source/parsed缓存边界是另一层；Corki仍_write_assistant_stream输出原标记。先接
稳定源快照渲染和已输出行计数，表格held部分不混入；宽度变化需按旧源重算边界，不
按原字符数裁渲染文本。完整解析块缓存/动画队列仍后续实现，不把全量快照重渲染称缓存。

## 最新实施：完整行表格holdback接入真实活动区

TableStreamSource按原生结构识别规则保留候选/确认表格；只接受newline完整源行，
字符offset留在Python字符串内部，不用其冒充Rust byte offset。转义pipe、引用、围栏、
空行候选与邻接确认均有测试。TerminalUI将稳定原文继续写入Rich，held部分用
AssistantMarkdown→ANSI fragments在现有PromptSession的非聚焦Window显示，不另开
stdin；宽度/源内容key缓存tail渲染。终态清tail，已有完整源Transcript负责canonical
完成；replay保存并恢复活跃table对象，不把tail写入stable回放。pipe及关闭live输入
保留append-only原输出，/realtime开关同步UI。
两新测试先红（245a96），随后接入。初回归暴露4个非TTY fixture的无session错误
（815b24），修复为仅活跃table source需要invalidate；没有给pipe引入prompt依赖。
CLI单位208通过2.91秒（1a4edb）；真实Runtime+TerminalUI两PTY40/100列通过3.68秒
（cd7045）：alpha可见但PARTIAL不见、resize后steer继续、最终宽窄回放各一次。
新增replay活态不被改写和live-off控制后13通过0.42秒（15db12）。全静态1076文件
77包通过（abff9b）；最后新增测试另需结束时检查。广CLI/输入与流清理/全部e2e
99165运行中（5f13ba），不作为通过证据。
限制：稳定区仍原文，不是增量Markdown；暂无独立动画入队/出队；markdown围栏展开、
尾部gutter/样式精确对齐、长table占屏和审批/取消组合仍需继续。此批不是完整F完成。

## 当前实施前证据：表格源行被过早固定

重新完整读取streaming/table_holdback.rs及table_detect.rs：按完整行递增扫描，候选表头
遇空行仍保留但确认必须紧邻delimiter；确认后到reset始终hold。转义pipe、blockquote、
3字符以上匹配围栏及仅md/markdown首token的分类决定是否扫描；不是任意pipe就确认。
Corki TerminalUI.append_assistant_delta仍把全部完整行直接_write_assistant_stream。
计划先接源码边界与现有PromptSession上的只读表格tail，终态清tail并用已有canonical
repair重绘；纯pipe和显式关闭活动输入的路径保持原输出。这个阶段仍未实现一般正文
增量Markdown、动画入队/出队和渲染缓存，不能称完整两区域对齐。

## 最新验证：普通压缩失败与显式取消的队列语义不同

原生protocol.rs::TurnStatus::Failed→handle_non_retry_error→turn_runtime.rs::on_error
在finalize_turn/错误展示后调用maybe_send_next_queued_input；不能按取消路径强制恢复。
Corki compact_node先await window.compact再发ContextCompacted，window.compact在
_summarize成功后才安装CompactionItem；CLI普通TurnFailed保留后续FIFO，取消才恢复。
新增真实Runtime和40/100列PTY失败注入，终态显示错误、不显示Context compacted，
后续两Turn各采样一次、不重发摘要；补查持久化原seed身份保留且没有CompactionItem。
初始新增测试未限制摘要重试导致预期错误（602480，23169终态73通过3失败）：摘要
自身对ModelError有重试循环，不能假定retryable=False意味着它不重试。测试明确设置
model_max_retries=0以聚焦终态归属，未修改生产重试策略。最终76通过6个forkpty警告
16.49秒（80f051），再加持久化检查的3变体单独重跑3通过0.92秒（ecccaf）；
新增断言后的两文件静态与diff检查通过（fefbab）。当前无活动测试进程。
上一轮广回归88966终态exit0，299通过71警告127.95秒（2bf82d），停止poll；其采集
早于本轮失败测试，只证明上一批CLI输入接入。静态1073文件77包通过（5d69cb）。
本轮无生产修改，普通错误语义已有实际证据；不能扩展成特殊错误/所有故障已验证。

## 最新实现：独立压缩复用活动输入所有者

默认run COMPACT现复用_consume_realtime_turn(None)，输出仍来自Runtime.compact的
普通模型请求；新Enter直接排CLI后续队列（不调用Runtime.steer），Tab沿现有队列。
成功后FIFO提交；/stop、Ctrl+C/EOF沿同一cancel→join reader/output→恢复草稿路径。
已有/realtime off保持非活动输入路径，不覆写配置。没有增加stdin reader或官方服务依赖。
两个真实Runtime先红（02d93e，32963终态），修改后新场景通过；旧4个压缩测试曾以
模型刚返回作为EOF时机，新增reader正确将该EOF当取消（c7f46b，24568终态77通过4失败）。
现测试改为输出消费者完成后退出，未放宽生产取消语义；最终单位/输入所有权81通过
3.23秒（8a58e2）。新增实际TerminalUI/Runtime 40/100列×完成/停止PTY4通过9.80秒
（ea4df7）：压缩前seed、压缩不含新消息，完成后两Turn或停止后合并重提一Turn。
静态1073文件77包通过（1a0fe6）。广CLI/输入边界/全部e2e88966运行中（432216），
尚非通过证据。流式两区域/完整F及压缩失败、重复取消的专用组合仍需验证。

## 待修复：独立压缩期间没有活动输入

原生core/session/turn_input.rs::steer_input对Compact返回ActiveTurnNotSteerable；
TUI保留输入所有者，把拒绝steer移入后续队列（input_restore.rs::enqueue_rejected_steer）。
tests/slash_commands.rs::compact_queues_user_messages_snapshot及
slash_compact_eagerly_queues_follow_up_before_turn_start覆盖压缩进行中/启动前排队。
Corki run的COMPACT分支却直接_consume_events(runtime.compact())，没有reader，
只能等压缩结束，/stop也无法经现有composer处理。属于默认CLI核心缺口，不涉及远程协议。
方案：复用现有双任务输入/输出所有权循环，压缩操作新输入直接排后续队列，不调用steer，
成功后FIFO发后续Turn，停止则join后恢复草稿。不新建第二stdin所有者，不修改摘要请求。
验收采用真实Runtime普通模型请求阻塞压缩，新输入在释放前被接收，成功/取消分别验证。

最新验收：93921终态86通过19.57秒（8个forkpty警告），忙时拒绝/停止恢复PTY与
Tab压缩通过；静态通过。压缩期间活动composer/排队和流式两区域仍需后续实现。

最新纠偏：忙时Enter /compact按原生available_during_task拒绝，不取消当前任务。
Tab /compact按ParseSlash队列在当前完成后运行。已去除CLI替换特例，SDK的有任务
Replaced输入交还仍保留；89通过，补充PTY在93921。下方旧“/compact替换恢复只含
steer”已由完整入口审计推翻，详细证据见pending-steer-ownership.md。

最新输入所有权：replaced也返回未提交steer；返回项与/compact、Tab队列独立，恢复
草稿不阻止显式压缩及原后续任务。普通停止/父任务取消恢复全部，恢复失败保留暂停
队列。实际Runtime压缩三组合通过，专项含停止PTY91通过，替换PTY尚未新增。
见pending-steer-ownership.md；活动区增量Markdown/表格tail等未因本批而完成。

## 最新实施：停止后恢复队列，不自动执行下一轮

3种停止用例先红，原行为实际收到initial+queued2+queued3（907a43），确认非仅展示
问题。_consume_realtime_turn现在追踪取消终态，join消费者和reader后恢复FIFO文字
到TerminalUI._draft前、已有草稿在后，清空并刷新队列，不提交或写输入历史。
显式/compact replaced保留原执行边界。若恢复渲染失败，队列保留且暂停自动出队，
不能掩盖原取消；用户用队列编辑取回全部后解除暂停。取消发生在join期间也恢复。
实际40/100列PTY两种/stop或Ctrl+C共4通过7.88秒（5df1ae）：模型只先采样initial，
两条队列恢复为多行草稿，重新Enter后才采样合并输入；从未自动执行两条独立任务。
新增外部cancel恢复须在reader清理后/恢复报错仍保留CancelledError两变体，草稿
合并与无历史写入控制。完整CLI29069已终态exit0，304通过107.60秒，63个forkpty
警告（9a1bf3），停止poll。之后补显式外部取消解除replaced豁免，防止用户再次停止
仍执行待压缩操作；该追加修改曾误把shield传播的子任务取消也当父任务取消，手动
压缩用例红（c10527），现按当前owner.cancelling区分，最终单位/输入所有权76通过
2.68秒（2a30fa），静态1072文件77包通过（8be0a5）。全项目20572仍live31%（828716），
早于本批，继续原句柄，不将它当本批验收。pending/rejected steer恢复仍未完整实现。

### 实施前差异

Codex input_restore.rs::on_interrupted_turn调用drain_pending_messages_for_restore，
按待处理消息及当前composer草稿顺序合并、恢复输入框，不自动发送下一条。Corki的
_consume_realtime_turn取消后仍保留自动出队deque，run下一次迭代会直接执行排队任务。
本批先覆盖已有明确QueuedInput：停止/键盘/EOF/外部取消及TurnCancelled结束后，必须
先join输入reader取得最终草稿，再将FIFO文字恢复到草稿并清空自动队列；/compact的
replaced路径是显式替换操作，不按用户停止处理。恢复不得写输入历史或自动提交。
pending/rejected steer尚未完整跟踪，此批不能宣称该部分恢复已完成。

## 最新实施：Alt+Up取回队尾并编辑

application向真实UI绑定同步_pop_queued_input，pop_back后发布剩余队列；TerminalUI
仅普通bindings的Escape+Up调用它并用Document替换buffer，光标到末尾。同步无await
使取回与下一轮出队不能半途交错；未再次Enter/Tab前不提交。空队列无操作。仅绑定
真实editor后preview才显示alt+↑提示；form bindings无此键。README说明会替换草稿。
真实40/100列PTY两原场景加两编辑场景：原queued two取回、追加edited再Tab，模型
只采样queued two edited，原输入与编辑后的输入各入FileHistory一次，仍4次采样/3个
Turn。多行/替换草稿/空队列/无历史写入控制一起7通过10.41秒（9d70cb）。最初单位
夹具误在无loop时写PromptSession的自动验证buffer，改用真实非自动验证Buffer，
不更改生产验证行为；因此不把这次夹具失败称作功能先红证据。
静态1071文件77包通过（bd1ed9），完整CLI组合91001已终态exit0，295通过100.82秒，
59个forkpty警告（a710a0），停止poll91001。全项目20572仍live18%
（7dbb20），采集早于预览/编辑生产修改，继续原句柄；不作为这些新修改的全项目证明。
本批仍未覆盖pending/rejected steer分组、恢复队列持久化或整个两区域流式显示。

### 实施前差异

原生interaction.rs在edit_queued_message且无modal/popup时调用
input_restore.rs::pop_latest_queued_composer_state（pop_back）→restore_composer_state
→bottom_pane.set_composer_text_with_mention_bindings→刷新preview。这是替换当前草稿，
不是追加，也没有先把现有草稿排队。Corki尚无该操作；本批在普通composer绑定Escape+
Up（Alt+Up），同步取回应用deque最后一条并更新预览，替换buffer、光标置末，不触发
submit/历史写入。绑定存在时才显示编辑提示；表单bindings保持不变。验收需覆盖多行、
有草稿、空队列无操作、实际键盘编辑后重新Tab只发送改后文本一次及输入历史行为。

## 最新实施：composer上方的临时队列预览

TerminalUI在现有PromptSession.layout增加非聚焦Window，ConditionalContainer仅在
有队列且prompt未结束时显示；不另建Application或输入reader。set_pending_inputs
复制tuple并invalidate，application在每个deque入/出点更新；审批仍由原独立form及
InputOwner抢占主composer。pending_input_lines接原生header/↳/续行4列/每条3行与
省略行规则，窄于4列不显示；使用FormattedText字面值，不把用户文本作为HTML解析。
组件不调用remember_display，不写Transcript/输入文件；真实UI测试验证更新/清空
不产生任何scrollback源或文件。FIFO用例额外核对4次快照从1条→2条→1条→空。
单元/旧队列PTY首批185通过6.69秒（c837d0），随后PTY增加真正可见header/两条内容
断言。静态1071文件77包通过（cfcf77）；完整CLI/集成/全部PTY71877已终态exit0，
292通过95.84秒，57个forkpty警告（ed7602），停止poll71877。
全项目20572原进程仍live5%（f3e56e），采集早于本批，不冒充本批全量验收。
本批不含pending/rejected steer分组与Alt+Up编辑，未显示尚未实现的快捷键提示。

### 实施前差异

Codex input_flow.rs::refresh_pending_input_preview将InputQueue.preview交给bottom_pane；
pending_input_preview.rs在composer上方显示Queued follow-up inputs，逐条↳前缀、
续行4列，每消息最多3个折行再显示省略行，宽度小于4不显示。set_pending_input_preview
更新视图并request_redraw，不把预览作为已发送历史。编辑提示只有绑定真实编辑操作才
可显示，不能仅复制Alt+Up提示。
Corki只有一次性Queued notice，没有持续预览。本批接PromptSession同一Layout中的
非聚焦窗口（composer上方），队列每次入/出都发布不可变展示快照，invalidate刷新；
prompt结束时隐藏，审批沿用独立输入所有权，不新开stdin reader。不写Transcript、
FileHistory或模型历史。pending/rejected steer分组及队列编辑仍留作明确后续差异。

## 最新实施：默认CLI接入本地文字运行期输入

settings.realtime_enabled和新建配置的[realtime].enabled已默认true；已有文件不覆写，
显式false和/realtime off继续可用。SDK Runtime.stream的realtime参数默认false未改，
后台Memory agent也仍显式关闭。README明确默认、退出选项和Enter/Tab区别。
原输入所有权测试改从_consume_turn默认入口进入，先红于未启动reader（62eb78），
然后通过；实际队列PTY去掉显式true，默认40/100列链路通过。配置新增新文件默认及
已有false不覆写测试，CLI新增关闭时不创建reader的控制用例。
旧FakeRuntime增加真实接口的realtime关键字；FakeUI耗尽转换为EOFError，避免普通
输入被提前读取后下一次read触发coroutine StopIteration，不改生产EOF/取消语义。
静态1069文件77包通过（45cc5e）；完整CLI/配置/实际CLI集成/全部PTY19390已终态
exit0：908通过1跳过97.13秒，57个forkpty警告（de0d8e）。停止poll19390。
这关闭默认普通Turn无reader的差异，不表示活动区增量Markdown/表格tail/队列预览
编辑已完成。下面旧“默认仍无reader”的历史段落以本节为准。

### 实施前边界

原生普通运行Turn允许Enter steer/Tab排队，Corki仅显式realtime_enabled=True启用
已验证的本地文字链路。现按该可观察行为调整CLI默认：settings与新建配置默认开启
文字steering，保留用户显式false和/realtime off；不改SDK Runtime.stream默认参数，
不覆写已有配置，也不按provider/domain切换。已有RealtimeContextContributor是普通
developer文字提示，本地队列在采样边界追加UserMessageItem，不是官方Realtime API。
这次是明确对齐默认输入语义，不是为显示toolbar偷偷切换配置；原生调用证据见下文。
验收必须从默认_consume_turn和无显式true的实际PTY进入，覆盖取消/重复取消/renderer
失败及审批抢占，并证明显式关闭仍有效。活动区增量渲染/队列预览仍单独待实现。

## 最新实施：显式Tab排队与Enter提交分离

input_owner新增不可变QueuedInput，仅UI层携带动作；TerminalUI普通composer Tab提交
同一文本buffer，read_message返回QueuedInput，普通Enter仍返回str。表单使用独立
bindings，不注册此Tab动作；FileHistory仍由原PromptSession保存纯文一次。application
用_pending_messages deque取代单槽，活动文字Turn中QueuedInput只入队，Enter仍走
原steer；Turn完成竞态/steer关闭竞态追加到队尾，不覆盖既有队列；/compact立即替换
当前操作时放队首，原队列保留。空闲逐条调用原命令或Runtime入口，不把UI动作发模型。

新FIFO/steer用例先红（c74578），实现后CLI+输入所有权192通过（724486）。关闭
steer竞态新增已有排队消息变体，旧消息不被覆盖。真实40/100列PTY使用实际键盘和
LangGraphRuntime，初始+Enter共用Turn，两次Tab后续各独立Turn；4条输入历史各一次，
模型4次、Runtime关闭。两项通过4.89秒（57219e）。最初PTY把标记放在ModelCompleted
之后（生成器终态不保证再next），随后又过早Ctrl+D，被当成活动Turn停止而非退出；
修正测试为真实_consume_turn完成+新composer屏障，不修改生产退出语义或放宽断言。
静态1069文件77包通过（0b6701）。完整CLI/PTY组合47378已终态exit0：289通过
94.49秒，57个forkpty警告（337cb6）。停止poll，当前无活动测试进程。

此批仅接已有realtime_enabled条件路径的运行期队列与空闲Tab提交；默认普通Turn
仍无活动reader，队列预览/编辑、默认steer接入、完整两区域未完成，不能宣称整个F完成。

### 实施前差异

原生chat_composer.rs默认Enter/Tab经handle_submission_with_time分别生成Submitted/
Queued，input_flow.rs显式队列只在空闲时依次发出，运行期Enter仍尝试steer。
Corki TerminalUI只有Enter返回str，application只有单个_pending_message槽位，不能
表示多条显式排队输入。本批先接已有实时文字composer路径：引入仅UI层QueuedInput，
Tab标记动作但输入历史保留普通文字；application保存FIFO，空闲逐条交给原命令/Turn
分发，Enter继续steer。审批表单不得绑定此Tab行为，输入取消保留草稿，不扩展模型协议。
验收：多条Tab不steer当前轮、Enter仍steer、后续FIFO各一次，关闭竞态不覆盖已排队
消息，真实键盘/Runtime验证。默认普通Turn活动输入与完整队列预览编辑仍需后续接入，
不能把已有realtime条件路径的这一步称为默认路径完成。

## 新增真实PTY证据及活动输入路由约束

tests/e2e/test_interleaved_tool_pty.py通过40/100列实际子进程：模型先输出完整行与
未结束尾行，再发并行ToolCallItem；handler完成后通过输入屏障继续正文。清屏前无
工具头/结果或未结束尾行，清屏后权威正文、工具头和结果各一次且顺序正确；内部
断言模型2次、handler1次、结果回灌一次、Runtime关闭。2通过2.93秒（df41a0）。
此测试直接消费Runtime，不启动普通composer，不能充当活动输入区验收。
最终新增PTY+真实Runtime清理+application单位组合63通过4.76秒（4ee2b8），静态
1068文件77包通过（906d08，格式化后全量check已通过），diff检查通过。7103已终态，
当前无活动测试进程；先前完整CLI283项不含本次新增的两项PTY。

活动输入的下一步不能只增加“全部排到下一Turn”的队列：原生chat_composer.rs的
默认submit_keys=Enter、queue_keys=Tab；handle_submission_with_time区分Submitted
和Queued，input_flow.rs::handle_composer_input_result在普通运行Turn允许提交，
显式Queued经queue_user_message_with_options保留至空闲才发下一条。input_submission.rs
为运行期提交保存PendingSteer；app/thread_routing.rs的AppCommand::UserTurn检测活动
Turn后先turn_steer，Missing才转start，ExpectedTurnMismatch仅重试一次，不能steer的
活动任务重新排队。这是本地Harness/app-server协调语义，不是官方模型服务接口。

Corki默认_consume_turn没有输入reader，仅_realtime_enabled路径启用InputOwner及
Runtime.stream(realtime=True)。RealtimeController.steer为本地UserMessageItem队列；
普通Turn未activate时会拒绝steer。后续应显式对齐“运行期间提交/排队/关闭竞态”的
数据契约，再接活动区与可变tail，不能仅开toolbar或私自把所有Enter变成排队；也不能
把本地文字steer实现误认为需新增provider realtime或官方app-server连接。

## 最新实施：正文与工具展示的FIFO边界

application现在将活动正文期间的ToolCallStarted/OutputDelta/Completed及PlanUpdated
排入展示专用deque，正文完成/中断时排空，Turn终态和事件迭代器finally也排空后
关闭未确认工具。处理的是展示事件，不阻塞Runtime调度、模型生成或审批所有权。
队列按序pop，单个同步渲染异常不会阻止其余事件清理，原异常/取消优先于清理错误。
未活动正文时直接排空，保留旧即时展示；reasoning只在工具开始时结束。
真实Runtime两变体先红后绿；既有未确认工具测试扩展活动正文×回放×6种终止路径，
验证output→plan→completed顺序和正文先收尾，不把未完成工具标成完成。
CLI/真实Runtime/冷历史/流PTY组合206通过22.88秒（14个forkpty警告，5d9d73），
随后恢复仅ToolCallStarted关闭reasoning的原边界，静态1067文件77包通过（9a880b）。
完整CLI/PTY组合97987已终态exit0：283通过85.97秒，53个forkpty警告（5027aa）。
该组合采集包括最终reasoning边界；停止poll，当前无活动测试进程。
这只关闭正文被工具开始切断的缺陷，不等于完整两区域、表格tail或增量Markdown完成。

### 修复前证据

真实Runtime新用例两变体（完整首行 / 首行加未结束尾行）均复现重绘首行出现两次
（b2be7c）。工具并行执行一次、模型两次，问题位于展示而非重复执行。
Codex chatwidget/streaming.rs::defer_or_handle 在活动stream_controller存在或已有
interrupts时FIFO延迟工具生命周期，handle_stream_finished排空；tool_lifecycle.rs
的MCP started/completed接入此路径。Corki application的ToolCallStarted却直接end
正文并清streaming，下一delta创建新begin，Transcript只合并最新begin，旧前缀遗留。
方案：按活动正文延迟普通工具展示事件，正文完成/中断/Turn终态/异常EOF收尾时有序
排空，不推迟真正的工具执行或审批，不把tool开始作为assistant item终态。
验收：真实Runtime同item跨工具事件正文在40/100列各一次，工具各一次、结果回灌；
补异常/取消/EOF队列收尾，不能丢工具未确认提示。完整两区域仍开放。

## 最新实施：源行提交边界已接入，完整两区域仍开放

后续核验：完整CLI单位/CLI集成/全部PTY组合269通过89.26秒（5fa135），53个
forkpty多线程警告，5803已终态，不再poll；不因此宣称两区域已完成。

TerminalUI.begin建立空pending，append只提交最后换行之前的新源文；未换行尾行
不输出，首次提交时才绘制gutter。取消/失败经end丢弃未提交尾行；真正完成时pipe
保留追加输出，短纯文TTY无已提交行可直接完成，其余走原有权威正文repair。
Transcript.render保存/恢复pending与started状态，历史重绘不能更改活动流边界。
首个新测试先红（c5434b），修复后相关177项通过；实际PTY以UI处理后的DELTA_RENDERED
屏障验证无换行文字不可见、嵌套列表完整首行可见但末行仍不可见，resize和最终内容
继续验证，不是简单删掉旧断言。流PTY/清理17通过20.02秒（9e3a2c），静态1067文件/
77包通过（f74868）。完整CLI/PTY组合已启动5803，尚待终态。
当前已提交行仍原文打印，没有增量Markdown、表格可变tail或动画队列；这些仍是
必须继续实现的缺口。下文“尚未修改流式生产代码”仅为本批之前的审计状态。

本节以本地Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc为基准，属于F与A/E
输出生命周期范围。普通provider均采用相同UI行为，不涉及官方服务或专有模型协议。
状态：Corki完成正文已支持Markdown；两区域流式控制缺失，不能算已对齐。

## 源码链路

Codex tui/src/chatwidget/streaming.rs向streaming/controller.rs的StreamController
传递delta；StreamCore.push_delta经MarkdownStreamCollector收集原文，仅在新增换行
且commit_complete_source返回新范围时渲染。markdown_stream.rs生产collector不解析
Markdown；此前将“收到任意片段立即显示”当默认是错误的，未换行残片不会进入稳定
区或live tail。finalize_and_take_source移交剩余原文，非空末尾补换行并reset。

streaming/render.rs::StreamingRender有独立的stable_source_len/stable_rendered_len：
保留已结束的顶层块，只重算最后一个块；引用式链接定义可改变全局解释，必须全量
recompute。顶层开放代码围栏有追加路径，主题变化或围栏条件不满足回退解析。这里的
“解析缓存稳定区”不等于屏幕中的稳定提交区，不能混用两个边界。

StreamCore.sync_stable_queue维护emitted_stable_len、enqueued_stable_len和render.lines。
默认普通内容进入稳定队列；TableHoldbackScanner发现PendingHeader/Confirmed时，从
候选表头起留为可变tail，前面的正文可提交。无表格时active_tail_budget_lines返回0，
不是简单“每次保留最后一段”。表格识别只扫描允许的围栏上下文；具体scanner仍需在
实现前完整核对，不能只用行中有竖线这一启发式。

current_tail_lines从enqueued边界开始，而不是emitted边界，否则排队但尚未打印的
行会在tail重复。tick/tick_batch出队才推进emitted。set_width重渲染并重建尚未输出
队列，包含缩窄/扩宽使未输出行减少时不丢行的处理。finalize_remaining从完整source
重渲染再截取未输出部分；之后最终消息经AgentMarkdownCell承担原文重排。

原生直接验收入口：controller_live_tail_keeps_uncommitted_table_cell_newline_gated、
controller_set_width_partial_drain_no_lost_lines、controller_set_width_partial_drain_keeps_pending_queue、
controller_keeps_pre_table_lines_queued_when_table_is_confirmed、
controller_streamed_table_matches_full_render_widths、
controller_does_not_hold_back_pipe_prose_without_table_delimiter及render_tests中的
incremental_render_representative_stream。不能只比较最终静态快照。

## Corki现状与实现约束

application._render_events_owned → TerminalUI.begin_assistant_message/
append_assistant_delta现在立即输出绿色bullet和原始文本；end仅换行。
complete_assistant_message在最终内容到达后，才通过Transcript合并源并repair。
这既没有换行提交边界，也没有稳定队列、可变tail、表格holdback和增量渲染缓存。
watch_resize虽可原文重绘，但每delta清空整段scrollback不等价于上述模型。

实现须真实接入TerminalUI/Application，而非新增无人调用的collector。至少需要：

1. 原文累积、完整源行边界、解析缓存边界、入队边界、实际输出边界分别建模；保持
   emitted <= enqueued <= rendered，并为替换/结束/reset提供单一所有权。
2. 可变tail由prompt-toolkit的现有输入/确认所有者协调绘制；不可叠加独立Rich Live
   输出者导致模态焦点或确认内容被擦除。正文、工具输出和状态的顺序也必须保留。
3. finalize合并完整源；失败/取消不能把未提交preview假装成完整答案。结合既有
   AssistantMessageInterrupted、部分item提交、Turn终态和冷历史，不改变模型账本。
4. 调整现有PTY里“短的无换行片段必须立即可见”的断言以匹配原生换行门槛，同时
   新增真正newline提交/表格可变tail/工具交错/模态/resize/取消测试；不能只删旧断言。
5. 纯文本pipe不能擦除已输出字节，需明确输出策略；TTY的两区域行为不应被pipe限制
   所替代。所有UI源数据与模型source保持分离。

本轮只完成上述源码追踪并确定下一实现边界，尚未修改流式生产代码。

## 表格holdback扫描规则（后续源码补验）

已完整读取streaming/table_holdback.rs生产与测试oracle，并追踪table_detect.rs生产
helpers/FenceTracker，不再仅凭controller注释估计：

- scanner只接收已提交完整源行，source_offset累计源位置。确认要求相邻两行是表头
  与分隔行；blank会打断相邻配对，但不会立即清除pending_header。下一个非空非表头
  行才清pending。Confirmed一旦形成直到reset保持，不在空行/表后段落处自动释放。
- parse_table_segments去除可选外侧竖线，再按未转义竖线切分；反斜杠连同后一个字节
  跳过，转义竖线不分列。无外侧竖线且只有一段不是候选；有任一非空cell才是header。
- delimiter每cell允许首尾各一个冒号，中间至少三个横线；scanner本身不要求header
  和delimiter列数相等，最终结构解释由renderer负责，不能自行扩大验证条件。
- 识别表格前剥离多层blockquote前缀。FenceTracker只接受原行至多3个前导空格，
  3个以上同类backtick/tilde开启围栏；关闭须同marker、长度不短于开启且其后全空白。
  只有首个空白分隔info为md/markdown（忽略大小写）属于Markdown围栏，其余含空info
  都是Other。本行使用advance之前的context，Other中不识别表格。
- bytes/source、rendered lines、queued/emitted行边界必须分开。Python可选用字符偏移
  但需要从始至终统一，不把Unicode字符索引混当UTF-8字节偏移。

当前TerminalUI默认普通Turn没有运行中的PromptSession（_consume_turn在realtime_enabled
为False时仅消费事件），所以可变tail不能仅挂在read_message的toolbar上并宣称实际可见。
需要一个跨普通流/输入/确认共享的终端活动区所有者，或先补齐该生命周期；不得借此
启用供应商原生实时服务。该UI基础依赖尚未实施，属于F必须继续推进的工作。

## 活动区与输入所有权补充核验

InputOwner.read_message持有_lock直到UI reader返回；elicit先增加_modals、清除ordinary
准入，再取消并等待普通reader释放锁，表单完成后才恢复ordinary。join_inputs对重复
取消采用shield/join，不能把另一个会读stdin的Rich Live/PromptSession直接并排启动。
尤其活动区绘制不能等待这个整段读输入锁，否则会一直等用户提交，导致模型输出停顿。

TerminalUI.read_message使用patch_stdout并保留取消时的草稿；确认使用独立_form_session，
_transcript.modal_depth禁止历史repair侵入确认。新活动区必须遵守同一准入与恢复边界，
同时需要独立的绘制协调，不能把模态计数当作可以随意清屏的锁。

另核实名字容易混淆的realtime：Runtime.steer → RealtimeController.steer仅验证并入队
UserMessageItem，graph在采样边界接收，prompts/realtime/start.md也是本地文字追加说明。
它不是OpenAI realtime连接；但为了保持活动区存在而强制设置realtime_enabled=True
仍会改变原配置下的新输入语义，因此不能作为显示缺口的修复捷径。本轮没有改默认值
或启用任何供应商服务。下一实现应给默认非steering消费路径提供不抢占确认/输入的
活动区，显式steering模式则共用其已运行的composer。
# 最新：Markdown 表格围栏显示

全量 AssistantMarkdown / 增量解析缓存共用保守规范化：闭合且含相邻表头/分隔行
的 md/markdown 围栏才解开，未闭合和代码示例保留，来源原文不改。
源码、先红后绿与 Runtime/PTY 证据见 markdown-table-fences.md（258通过）。
此显示修复不代表动画队列/高亮增量/尾部原子顺序完成。全文规范化扫描仍存在。
