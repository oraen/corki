# Markdown 表格围栏（F，显示层）

## 性能后续（实施前）

原生 controller.rs::stable_prefix_len_for_source_start 按边界/宽度缓存前缀行数；
Corki修复上下文后的 `_stream_tail_fragments` 每次新行都会全量解析并渲染前缀和全文。
已有 MarkdownParseCache/MarkdownRenderCache 仅接稳定输出，尚未用于活动tail。
计划在每流 StreamMarkdown 内保存独立tail解析/body缓存和前缀行数缓存，不与
稳定输出的parser混用；源替换/边界/宽度改变必须失效。真实UI计数验证稳定段落
不随表格行数重复解析/渲染，并维持开放/闭合围栏及引用定义的canonical一致性。

已实现：StreamMarkdown.tail_lines 独立持有 MarkdownParseCache + MarkdownRenderCache，
前缀行数按原始前缀/宽度缓存；TerminalUI实际调用，生命周期随每流对象开始/结束，
Transcript回放仍保存恢复同一对象，不共享稳定输出parser。
真实UI30行追加先红60次Paragraph渲染（04c0e8）；现在渲染及包含稳定正文的parse
均不超过3次。逐行40→100→40列、围栏闭合、晚到引用、源替换，尾部Segments与
全文基准完全相等。CLI单位+表格PTY266通过4警告12.27秒（c31904）；静态1084文件
77包通过（094b68）。全项目22649仍live37%（880b8b），不重复启动。
活动表格自身仍会重排；规范化/旧body拼接/gutter仍全文访问，不声称总体线性。

## 后续差异：活动 tail 丢失上下文（实施前）

Codex streaming/controller.rs::current_tail_lines 从完整 render.lines 按行切片；
tail_budget_from_source_start → stable_prefix_len_for_source_start 仅用源前缀确定行数，
不会独立解析表头后缀。Corki TerminalUI._stream_tail_fragments 却直接渲染 raw tail，
因此开放 md 围栏中的管道文本被提前显示成表格，闭合 marker 被误当成新围栏；
同时丢失 assistant gutter。属于默认 realtime TTY 路径的 F 行为不一致。
修复验收：全文上下文渲染后切除稳定前缀行，缓存键包含完整源、边界、宽度；
开放围栏先为代码，闭合后为表格；先前代码行不能在 tail 重复；不显示未换行部分。

本轮已接入 TerminalUI：`_render_lines` 对完整源和稳定前缀使用相同宽度及 gutter，
仅尾部行交给 prompt-toolkit，缓存键为完整源/原文边界/终端宽度。
实际 get_size 覆盖40/100列；测试无前缀、正文前缀、围栏内已有代码行三种情况。
6项先红（88bfad），修复后专项和真实表格PTY39通过（a594b1）。
全项目原22649仍live32%（a6f253）；静态1084文件77包通过（8a2a87）。
仍是全量渲染再切片，不称该热路径已具备增量性能；stdout/tail 原子顺序仍独立开放。

最终本批回归：1629终态278通过、18 forkpty警告、31.06秒（9263d4），包含完整CLI
单位、表格PTY与流式终态修复PTY。停止poll1629；不作为A–F整体完成证明。

源码基准：Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc，
`tui/src/markdown.rs::append_markdown_agent` / `render_markdown_with_block_ranges`
先调用 `unwrap_markdown_fences`，再送 renderer；`table_detect.rs` 提供结构判定。
不是模型服务的 namespace 或远程协议。

修改前：Corki `AssistantMarkdown.__init__` 直接交 Rich；`MarkdownParseCache.document`
直接解析原文。已闭合的 md/markdown 表格仍是代码，而 TableStreamSource 已对其保留表格尾部。
状态：行为不一致。优先级 F 可见显示差异。

验收：只去掉匹配闭合且含相邻非 delimiter 表头 + delimiter 的 Markdown 围栏行；
保留正文原样、引用前缀、其他代码围栏、未闭合围栏、空行打断的表格示例。
引用围栏只接受引用闭合；非引用围栏里的引用表格示例不能触发。
全量渲染和逐行缓存解析使用同一显示规范化；40/100 列比较裸表格渲染，
原始 transcript/model 历史不能被替换。动态尾部的原始偏移仍由原文所有者管理。

此项不替代仍开放的流式输出原子顺序、动画、有状态增量高亮或完整 A–F 验收。

## 实现与验收

`markdown_fences.py` 实现显示规范化，AssistantMarkdown 和 MarkdownParseCache
共用；缓存使用规范化后的坐标，闭合导致源不再延续时清空稳定块并重建。
TableStreamSource 的源坐标及 Transcript 原始参数不变。
结构拆分复用 `_segments(strip_quotes=False)`，避免错误解开非引用围栏中的引用示例。

修复前专项 6 失败、10 通过（096d16）；修复后渲染/逐行解析/代码状态/表格专项
58 通过（fe6cd0）。完整 CLI 单位 + 40/100 列裸表格与 Markdown 围栏真实 PTY
258 通过、4 forkpty 警告，11.82 秒（40e61f）。PTY 使用真实 Runtime，检查下一次
模型 request 的 assistant 原文和 transcript 参数仍包含原始围栏，最终回放不再显示
原始 delimiter 且表格数据各一次。静态检查 1084 文件、77 包通过（442c45）。

当前范围此差异已修复并验证。规范化仍扫描全文，不能把缓存性能称为全链路线性；
活动 tail 与 stdout 原子顺序、未闭合围栏尾部的上下文呈现仍需独立跟进。
既有全项目 22649 在本批前采集，7bd005 确认 live 27%，不能代替本批专项证据。
