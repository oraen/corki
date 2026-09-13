# 流式正文与tail提交顺序（F/A，实施前）

Codex streaming/controller.rs以同一render快照维护stable queue和tail行边界；tail从
enqueued_stable_len之后切片，不能把未提交前缀重新显示在tail中。此项对齐有序显示，
不把尚未实现的commit animation当作已完成。

Corki Application._render_events_owned同步调用append_assistant_delta；Rich正文进入
prompt-toolkit StdoutProxy异步flush线程，table source却立即更新并invalidate。
patch_stdout.py默认批量延迟0.2秒，_write_and_flush仅安排run_in_terminal，不提供await
完成屏障，因此tail可先显示。旧PTY测试特意等迟到prefix，不能证明正确顺序。

验收先加强真实PTY：首次alpha出现之前必须已有渲染后的bold/code，不能靠后续flush
或final repair通过。实现方向为可等待的UI delta提交：同一terminal绘制临界区内更新
源并输出capture后的正文，再恢复composer绘制；不增加stdin读取者，取消需join提交任务。
普通文件/pipe继续同步路径；尚未运行的PromptSession无需暂停绘制，但仍直接提交正文，
避免首帧越过stdout队列。原始transcript仍只记录一次delta。

## 实施与证据

新增stream_commit.commit_delta，经Application可选async UI方法真实接入。只对
TTY/realtime/StdoutProxy且含完整新行的delta使用in_terminal；Rich capture内同步
更新原始源，直接向同一app.output写出并flush，再恢复composer。未换行片段不切换
绘制模式。文件/pipe、显式非proxy Console继续原同步路径。
提交由独立小任务持有，调用方取消后join且传播取消，不增加stdin所有者。

严格PTY4项先红（99f11f）：首次alpha前无bold/code。修复后CLI+PTY263通过，另3个
pipe fixture发现session访问过早，已把非TTY门控移到session访问前；CLI264通过
（20c078）。提交取消契约+stream cleanup+回放/表格PTY25通过（38f559）。
静态1087文件77包通过；进一步CLI+清理+全部e2e88725终态见下文。

此项只证明assistant新行提交相对其活动tail的顺序。未把所有日志/reasoning/tool的
StdoutProxy输出改成统一队列，也未实现Codex commit animation。不要据此宣布完整F。

进一步移除“prompt未运行就退回stdout队列”的竞态：in_terminal未运行时自然no-op，
依旧直接flush正文再允许首帧显示。实际PromptSession尚未run的单位探针验证proxy为空、
正文已写、tail仍保留；取消/绘制顺序3项通过。此调整后的提交+严格PTY6通过（5600d3）。

88725终态349通过77 forkpty警告139.92秒（e6ab5e）：CLI单位、stream cleanup、全部
现有e2e。它采集早于首帧补丁，首帧由后续3单位（4d610f）/6PTY（5600d3）补验。
最终静态1087文件77包通过（12a58e），当前无活动测试。完整A–F仍未完成。

## 异步提交故障与 Runtime 收尾复核

再次确认参考 HEAD 为 ddf04ad26789d040f9ef6a96736f76602e35a6cc，工作树干净。
原生 tui.rs 的 insert_history_hyperlink_lines_with_wrap_policy 缓存历史并安排帧，
flush_pending_history_lines 传播写入错误（?），draw 返回 Result；不能把显示失败当作
成功提交。Python 额外需要验证异步提交任务与 Runtime 事件生成器的所有权。

既有 renderer fault 测试使用非 TTY Console，只验证同步路径。新增真实 Runtime +
TerminalUI + Application 测试，通过绘制入口 gate 及 app.output.write_raw 故障注入，
实际进入 owned commit。覆盖直接 OSError、等待者重复取消后提交再发生 OSError：
取消前任务不提前结束，释放后分别传播写入错误/原始取消；写入只尝试一次，模型流已
关闭，display end 一次，数据库无 running turn。没有改动生产逻辑，也不声称操作系统
终端故障后可以恢复显示或继续当前 Turn。

提交及 Runtime 清理 10 项通过（225cfb）；全部 CLI 单位 + Runtime 清理 + 严格表格
PTY 共 276 项通过、4 个 forkpty 警告（9cb3c1，12.32 秒）。静态检查、1087 文件格式、
compileall、77 包依赖及 diff 检查通过（96ab22）。统一输出排序、动画队列仍未完成。

## 再次核对 reasoning 的默认路径

真实 PTY 现先经过 ModelReasoningDelta，再提交正文和活动表格；原始 assistant 源、
跨 Step 后续请求、换宽与回放断言保持。两宽度/两围栏组合通过（5efe4d）；当时额外
断言 reasoning 全文先出现也通过，但该断言已移除：它会把 Corki 的错误默认展示固定
为要求。该测试只用于混合事件下正文与 tail 的顺序，不证明 reasoning 展示已对齐。

Codex chatwidget/protocol.rs 的 reasoning 通知进入 chatwidget/streaming.rs
on_agent_reasoning_delta：缓存文本，提取首个 **bold** 标题为状态，不写正文历史；
on_agent_reasoning_final 构造 transcript-only reasoning summary。此前 audit.md 已指出
此差异，本次再次确认仍未修复。Corki Application._render_events_owned 转到 TerminalUI
begin/append/end_reasoning，三者直接 console.print，Transcript.render 又复用同一输出，
尚无默认滚动区与展开历史的 reasoning 可见性区分。状态：行为不一致，F 高优先级。

下一实现需一起覆盖：标题跨 delta 提取、无标题 fallback、结束/取消状态清理、reasoning
不污染正文滚动区、历史详细读取可见、resize repair 不泄漏全文；保留原始模型历史。
不能仅把全文换成异步提交或直接删除 reasoning 数据来声称修复。先处理这一展示契约，
再审统一输出队列，动画仍是独立未关闭项。

最终保留的混合事件 PTY 4 项通过（ff65e7，7.02 秒），ruff/1087 文件格式/diff 检查
通过。完整 CLI/清理/PTY 276 项也曾通过（e98825），采集早于移除错误展示断言。
本轮未改生产代码，没有把重新确认的 reasoning 缺口标记为已修复。

## Reasoning 默认可见性实施

TerminalUI 现默认缓存 reasoning delta，仅更新 composer toolbar 的首个非空 **bold**
标题；没有完整标题时显示 Thinking。标题按普通单行文字展示并过滤控制字符。结束、
失败/取消的既有 end_reasoning 调用清空活动状态，原始 display calls 和模型历史不删。
Transcript.render 新增 include_reasoning（默认 false）；resize repair 使用当前展开状态，
重放保存/恢复活动 reasoning 状态。Ctrl+T 使用现有 composer 键绑定切换详细历史并重绘，
不创建第二个输入读取者。冷历史中的安全 summary 同样默认隐藏，展开可读，不显示 opaque。

参考 chatwidget.rs extract_first_bold、chatwidget/streaming.rs reasoning 生命周期，以及
history_cell/messages.rs ReasoningSummaryCell 的 display_lines 空/transcript_lines 可见。
测试先红（c4d6ad）；CLI/Runtime 清理/真实 PTY 280 通过4警告（cc889e），包括两宽度、
两围栏下默认无 reasoning 全文、Ctrl+T 展开全文、收起后继续 steering/模型后续请求。
新增冷历史检查随后单独验证。不是完整 Codex transcript overlay：当前展开通过重绘现有
滚动区实现，专用分页/滚动、reasoning Markdown 细节样式、工具等待状态优先级及 section
break 的完整事件映射仍需继续对齐。不能把这些未完成项随本次可见性修复一并关闭。

补验 reasoning 单位5项通过（d23802，含冷历史），发现并修复2处长行/格式问题。
compileall、77包依赖通过；最终重新运行全项目 ruff/1088文件格式/diff 检查。

## Responses 摘要的冷恢复字段审计（实施前）

Codex chatwidget/replay.rs 的 Reasoning 分支默认回放 summary，raw content 仅在显式
show_raw_agent_reasoning 时加入。Corki history._replay_items 也读取 ReasoningItem.summary，
但 ResponseContent.complete 创建该对象时仅填 content，供应商 summary 未映射到 summary。
影响：实际兼容 Responses 请求的摘要虽然在数据库 content 中，冷 CLI 历史却无法展开。
修复需只映射确实来自 summary 字段/summary delta 的内容，不把 raw reasoning 或加密体
提升为安全摘要；真实 Runtime→SQLite→新 Runtime→CLI 历史验证，不靠手工构造 summary。
分段事件的 identity/summary_index 仍待补齐，本批优先关闭已发现的恢复数据契约缺口。

实施：ResponseContent.complete 仅在显式 summary 列表或已收到 summary channel delta 时
填充 ReasoningItem.summary；raw-only fallback 保持 None。原 content、provider body 和
encrypted_content 不改变，普通请求投影不新增协议字段，token 估算不重复计入 summary。
不新增数据库字段。测试最初 fixture 缺 capabilities，修正后真正2红1绿（d2c01d）：
新 Runtime 冷读 summary 为 None；修复后冷恢复/Responses item与JSON边界/CLI reasoning
51通过（6f0aeb）。静态1089文件、compileall、77包与diff检查通过（2e16f3）。
本次修复新写入记录；旧记录如果没有明确 summary 来源，不能凭 content 猜测为安全摘要。
既有旧 provider body 的安全 summary 读取兼容仍待核对，不能据此声称所有旧会话已修复。

扩展模型/context/storage全部单位与冷历史组合824通过（481432，9.87秒），无活动测试。

旧记录兼容实施前：再次确认原生 replay.rs 仅默认使用 summary（参考 HEAD 未变化、干净）。
旧 Corki 完整 Responses item 仍保留 response_body_json.summary，可只在 CLI 读取投影时
恢复；旧 synthetic summary delta 则已经丢失来源，与 raw content 无法区分，不猜测恢复。
验收扩展原真实请求/SQLite/冷Runtime测试，模拟旧 writer 仅缺 summary 字段，包含完整体、
summary delta、raw delta；详细视图前后再次读取历史需完全相等，不写回旧记录。

旧记录实施：history._replay_items 在 summary is None 时复用既有 typed body 解码器，
只提取 body.summary，各分段保留空行；明确空 summary 不回退，有值优先使用该字段。
不触及原始 content/encrypted_content，也不按 provider 名称开启分支。完整旧体冷回放
先红1项（fa1cae），修复后新旧6路径与 CLI/Responses/storage 回归378通过（71a599）。
原历史重读保持完全相等；旧无来源文本仍不展示，属于数据来源已丢失的恢复限制。
多段/显式空/显式值优先级等8单位通过（a41487），静态1089文件、compileall、77包、
diff检查通过。分段流式状态事件与专用历史面板仍未完成。

分段实施前：原生 on_reasoning_section_break 将当前正文移入 summary_parts 并清空标题。
Corki ResponseContent 已校验 summary_index，但 ModelReasoningDelta 未携带，graph 还丢弃
已有 item_id，CLI 因而不能判断分段。拟将可选 item_id/section_index 贯穿普通模型内部
事件，首个新段 delta 时结束旧展示块并开启新块；没有身份的旧适配器保持连续行为。
不会新增请求字段或原生专用工具协议。空分段 added 通知的即时重置仍需另行核对。

实施：ModelReasoningDelta 增加可选 section_index，AssistantReasoningDelta 增加可选
item_id/section_index，graph 原样转发，Application 在活动 reasoning key 改变时 end/begin。
ResponseContent 在原有 index 校验后携带该值。新字段默认 None，原构造方式兼容，
无数据库/checkpoint字段或模型请求变更。以真实 ResponseContent→Runtime→Application→
TerminalUI 验证 First→新段未闭合→Second→新item Third，旧代码全部仍为First（470312）。
修复后分段/冷历史/模型单位/CLI/Runtime清理633通过（a22a96），详细历史每段正文一次，
终态无活动标题。静态1089文件、compileall、77包/diff通过（590580）。
本批分段在首个delta到达时重置，不声称支持单独空 added通知；raw/summary通道区分、
权威reasoning完成事件、专用历史面板与工具等待状态优先级仍需要继续审计。

协议单位与严格表格/详细历史切换PTY追加384通过（d691d7，8.54秒），无活动测试。

完成事件实施前：Codex replay.rs Reasoning item completed→on_agent_reasoning_final 清理
活动标题并提交 transcript-only 内容。Corki graph 只为 AssistantMessageItem 调用 output.complete，
ReasoningItem 完成没有UI通知。完整摘要无delta时当前display为空，流只给前缀时缺尾。
验收使用真实Runtime的item.completed和response.completed双路径，摘要只补一次、不打印opaque。
需保持完成身份去重并透传结束事件；raw与summary不同文本的权威替换仍需单独处理。

实施：ModelOutput 按 item 收集 reasoning 流与最后 section；完整安全 summary 是已显示
文本的延续时只补后缀，没有流时补全文，发送 AssistantReasoningCompleted 清理活动状态。
graph 在部分item完成/完整响应完成两入口接入，完成id去重，不发送原始opaque内容。
默认None身份适配器补尾继续沿用None显示身份，避免把同一段拆成两个thinking块。
无流/缺尾2项先红（b4f257），相关模型/CLI/协议/Runtime清理1015通过（1a3e96）；
额外无身份连续性先红（862ca8）后修复。新Runtime事件是本地数据契约，不新增供应商请求。
不同raw/summary文本的整体替换、恢复已回放reasoning的事件去重、空分段通知仍待完善。

匿名补尾修正后 reasoning链路/Runtime清理/严格PTY22通过4警告（ac4116，10.02秒），
静态1089文件、compileall、77包/diff检查通过。当前无活动测试。

恢复去重实施前：Codex replay.rs 区分 from_replay，只在历史回放时填充已完成 reasoning
summary；Corki run 已为正文/工具建立已回放id集合，但遗漏 ReasoningItem，新接入完成/
补尾事件可能再次填入已回放摘要。新增与既有恢复事件一致的宿主测试：旧id delta/完成
重复交付，新id同文本必须保留，旧id迟到完成不得关闭新段。拟只按已回放id过滤，不按内容。

实施：Application.run 从实际加载历史收集 ReasoningItem.id；渲染入口仅跳过匹配该集合
的有身份 delta/完成事件。未赋id事件与新id不抑制，不修改历史或执行状态。宿主恢复
事件探针先红3份内容（eeaec4），修复后旧+新各一份；CLI/真实Runtime摘要冷恢复与
清理292通过（78b0c8）。静态1089文件、compileall、77包/diff通过（45dda6）。
新探针使用替身Runtime重复交付事件，证明CLI显示契约，不冒充本轮新增真实checkpoint
故障窗口验证。完整历史面板、raw/summary差异整体替换与空分段added通知仍开放。

真实恢复补验：扩展 test_mixed_output_commit_recovers_without_repeating_tools 的既有
commit_model_step 成功后、checkpoint推进前中断场景，增加由 CorkiApplication.run
加载历史并调用实际 cold.resume_pending 的入口。流式item完成/整响应完成两路径均覆盖。
保留模型请求次数、工具调用次数、原始item身份/内容、两个结果及running turn清理断言；
新增40/100宽度下 Summary/Working/Finished/两个结果各一份、reasoning结束、输入历史为空。
4组合通过（540410）；恢复/摘要/CLI清理/历史组合39通过（a36cfd），静态1089文件、
compileall、77包/diff通过，无生产改动。该测试真实接入Corki SQLite/checkpoint，不声称
Codex也采用同一事务/checkpoint实现；参照其replay.rs历史与live显示边界，验证Corki所有权。

通道区分实施前：原生 protocol.rs 对 ReasoningTextDelta 受 show_raw_agent_reasoning 控制，
SummaryTextDelta 默认进入标题/历史。Corki Responses 的 summary/raw 与 Chat reasoning_content
目前均转成无通道字段的 ModelReasoningDelta；raw会占据标题，还会让完整summary补尾比较失败。
拟增加向后兼容的内部channel标识，适配器按实际响应字段标记，Runtime保留raw事件给宿主、
但CLI默认不显示raw、不将raw加入summary补尾缓存。没有新增供应商请求或官方服务分支。

实施：模型与Runtime reasoning delta增加channel（summary/raw，旧宿主构造默认summary）；
Responses按事件类型标记，Chat reasoning_content/reasoning标记raw。ModelOutput只累计
summary用于补尾，所有raw事件仍透传宿主，CLI忽略raw，不写入display calls。
真实ResponseContent→Runtime→CLI混合探针先红RAW_HEADER污染标题（d3ad46），修复后
raw不覆盖标题/详细历史；模型/协议/CLI相关1011通过（c996df）。本批不增加raw查看配置，
保留SDK原始事件与持久化内容；对齐Codex默认路径，不把可选raw视图宣称已实现。
先raw后完整summary的显示不再因前缀比较失败而丢失；自定义ModelPort在summary通道内
重写已经发出的摘要仍需权威内容替换设计，不能与raw/summary正常差异混为一谈。

追加raw前置×有无summary前缀×有无身份、Chat通道及Runtime恢复/清理/严格PTY43通过
4警告（20715b，12.80秒）；静态1089文件、compileall、77包/diff检查通过，无活动测试。

空段边界实施前：原生 protocol.rs 的 ReasoningSummaryPartAdded 即调用 section_break，
无需等待文本。Corki Responses SSE 分支未处理 response.reasoning_summary_part.added，
因此只在下一文本delta时重置标题。普通兼容SSE测试先红（b90b65），拟将added归一化为
具有item/section身份的空summary delta，复用既有Runtime/CLI边界，不另开供应商专用路径。

实施：Responses added事件复用ResponseContent.delta的既有索引/身份校验，输出空summary
delta；下一同段文本不再次分段。HTTP兼容SSE→Model事件验证空边界保留；独立真实
Runtime→CLI探针验证 First→空段None→未闭合None→Second，旧段详细历史仍一份。
模型item/Runtime摘要/JSON边界59通过（1db5b8），静态1089文件、compileall、77包/diff通过。
完整E2E+CLI+清理+摘要回归40644仍在运行，采集早于本次空段补丁，不能混称全覆盖。

40644已终态376通过140.54秒（e009a9）；覆盖完整现有E2E、CLI单位、清理与摘要链路。
空段补丁由后续59项补验，当前无活动测试。完整历史面板、统一输出排序/动画、状态优先级
等剩余F事项以及全A–F完成审计仍未结束，不标记goal完成。

详细reasoning样式实施前：原生 ReasoningSummaryCell.lines 用Markdown解析，统一dim/italic
并带两列gutter。Corki详细replay仍逐delta打印原始标记和thinking标签；跨delta的粗体、
行内代码、列表不可正确渲染。拟只在详细Transcript.render按reasoning块收集源，复用
AssistantBlock Markdown渲染并附加dim/italic，放回原begin位置；保持插入通知相对位置，
活动未结束块也可展开。原始display calls和模型历史不改，默认视图依旧隐藏全文。

实施：详细Transcript.render在begin位置收集至对应end/下一begin的片段，整体交给
AssistantBlock并以dim/italic打印；只跳过该块原有逐片段reasoning打印，其他通知保留。
普通视图仍走原隐藏路径。40/100宽度×活动/结束4项先红（69dc89）；修复后9个旧测试
因断言原始星号失败，更新为渲染后同一文本，仍严格检查去重次数和前缀尾部连续性。
CLI/摘要/真实恢复/严格PTY311通过4警告（e0e12c）。增加规范化行快照及dim/italic/bold
组合样式断言，不更改源历史；该修复不等于已实现独立可滚动transcript overlay。

追加reasoning快照/样式等13单位通过（cd4762），静态1089文件、compileall、77包及
diff检查通过，当前无活动测试。
