# CLI 原始展示内容与窗口重排审计

## 最新实施状态（覆盖下文修复前判断）

长代码布局分为两层：Markdown保留完整逻辑代码行，最终终端默认折行重复实际行首
空白，对应原生PreWrap；不是所有终端路径“永不折行”。容器不提前裁切长代码。
width10逻辑行、嵌套列表/引用、40/100列源重排及真实Runtime PTY通过，组合177项。
URL例外/精确折词/raw输出/极窄宽度仍开放，不能据此宣称所有长内容已一致。

完成正文、冷历史和重绘的代码块已接Rich/Pygments语法配色，语言元信息切分、
512KiB/10000行/单行4KiB超限前置降级，未知语言保留原文。保留首空行和尾随空格，
禁用高亮Text默认justify填充和背景。26专项、168 CLI/冷历史/PTY组合通过；下文
“语法高亮缺失”现缩小为原生精确主题/词法差异与块级流配色，长代码布局仍待处理。

列表新增原生样本对齐：每层4列marker，wrapped多行项后空行、代码前后空行，
root container不带伪首空行，代码自己的首空行保留。0起始编号保留。40列快照
多行项后新增空行，100列不变；同文嵌套列表真实PTY40/100列修复后层级保留。
160项组合及27项专项通过；下文“嵌套列表间距仍待对齐”缩小为尚未覆盖的完整变体，
不能把已修复的这些样本反复当缺失。流式块提交/表格/文件链接/语法高亮仍开放。

已接入完成消息Markdown与两列正文gutter，冷历史和Transcript重绘复用相同renderer。
标题保留#，列表使用-和数字点号，引用用>，代码不解释标记并保留缩进/尾随空格，
链接保留可读目标，HTML显示为文字。相同文本的Markdown流完成仍需要TTY repair；
转义/实体和超出可用正文宽度的纯文本亦需重绘。下文“相同文本不得清屏”只适用于
无需格式变化且未换行的普通短文本。非TTY流不能擦除已输出字节，保持原有追加语义。
源码快照40/100列和同文Markdown完成真实PTY已新增。当前仍不是块级增量Markdown
流，窄表格降级/文件链接解析/嵌套列表精确间距/语法配色、分页和完整reasoning显示
仍待对齐，不将Rich默认实现冒充Codex全部样式。

活动流resize专项已补：40/100列×有无resize×最终正文相同/不同，共8项真实Runtime
PTY通过10.50秒（595f32）。resize时先等待实际清屏与流片段再现，再允许模型完成，
要求最终源合并后再次重绘；无resize且正文相同不得额外清屏。watcher显式cancel/join，
模型显式关闭。此证据不覆盖并发交错的多个assistant item或断流/取消的全部组合。

权威完成正文已实现：AssistantMessageCompleted.text通过TerminalUI合并最近assistant
流源，并在内容不同或流中曾重绘时请求repair；repair与resize共用终端/模态所有权。
真实Runtime的40/100列PTY验证不resize也会修复旧片段；无delta、相同正文、不同正文、
连续消息验证通过。总110项通过9.17秒，详见audit最新节。下文关于“最终权威内容
替换未实现”的旧状态已由此覆盖；行上限、冷恢复、Markdown/reasoning等仍未完成。

补充验证：新增真实审批resize矩阵，40/100初始列宽×接受/拒绝/Ctrl+C，确认窗口
变化且超过75ms延迟后仍不重绘、也不自动选择；提交后待处理重绘发生，composer
恢复，确认输入和请求正文不进入展示源，普通followup进入普通历史。六组合通过
9.70秒（ef0f20）；进一步加入只改变高度的矩阵，完整结果记录audit最新节。
本批验证原生resize_reflow.rs在overlay期间保留pending请求的对应行为，不涉及
实际命令执行授权；未运行被审批的shell命令。并发审批之间切换时resize仍需专项验证。

已新增`cli/transcript.py`并接入TerminalUI各实际展示入口；原始参数deepcopy留存，
以原方法在目标宽度重新渲染，不能将已折行ANSI当源。提交输入由PromptSession回显，
只记录重绘源，不当场二次输出；form回答不记录。clear清除展示源后重建欢迎内容。
`application.run`启动watch_resize并在Runtime关闭前join；25ms采样尺寸变化、75ms
尾沿延迟，使用已安装prompt-toolkit的set_app/in_terminal暂让终端所有权并恢复草稿。
modal_depth非零时延后，取得终端前后都检查。展示源与会话数据库分离。

真实PTY旧计划40→100重排在没有新模型事件/用户提交时发生，随后完整提交原草稿；
3项计划PTY通过4.49秒。单位测试验证外部参数修改不会污染源、两种宽度确实重排、
重绘不重复记录、clear后旧源不复活。CLI/输入/elicitation集成与PTY联合143通过
16.22秒，7个forkpty DeprecationWarning；静态1056文件/77包通过。

**仍为部分实现**：目前保留方法调用源，尚未做最终assistant权威内容替换/流合并、
默认显示行上限与分页、冷启动已完成历史源恢复；reasoning默认状态栏路径亦未对齐。
modal延期代码已接入，但真实审批期间resize、重复窗口拖动、仅高度变化及流中断
组合仍需专项行为证据；不能以草稿PTY替代。源重绘暂不改变原来的plain-text正文
渲染风格，Markdown历史cell与快照仍需推进。下文是原始审计，用于保留差异来源。

基准：Codex `ddf04ad26789d040f9ef6a96736f76602e35a6cc`，只读参考。
这属于目标 F 的核心 CLI 行为，不涉及官方账户、模型专有协议或远程历史服务。
当前状态为缺失，不能以两项“改变宽度后的新计划输出”PTY通过判定一致。

## 已追踪的原生链路

`tui/src/app/resize_reflow.rs` 的调用顺序是：

1. `handle_draw_pre_render` → `handle_draw_size_change` 记录实际尺寸。
   宽度变化和仅高度变化都会请求重建；第一次宽度观测只建立基线。
2. `TranscriptReflowState` 分开保存已观测宽度、真正重绘宽度和待处理宽度。
   `schedule_debounced` 使用75ms尾沿延迟；同一目标不会因每帧检测无限推迟。
3. `maybe_run_resize_reflow` 在到期但overlay仍占有屏幕时保留请求。
   若提前绘制则重新安排到期帧，不能等下一次键盘输入才处理。
4. `reflow_transcript_now` 从`HistoryCell`源生成当前宽度的行，再清除旧待插入行与
   Codex拥有的终端历史，写入新结果；不从旧屏幕折行内容反推源文本。
5. 流式期间的请求与已执行重排分别记账。`maybe_finish_stream_reflow`在流内容
   合并为最终源cell后消费标记并补一次重建，覆盖“请求尚未到期就已结束流”的窗口。
6. 初次恢复、线程切换和窗口重排共享展示行限制；从尾部源cell向前计算，扩展到流
   continuation起点恢复分隔关系，最后精确裁剪显示行。裁剪不删除业务历史源。

已读完整模块：`app/resize_reflow.rs`（694行）、`transcript_reflow.rs`（331行）、
`resize_reflow_cap.rs`；测试入口位于`app/resize_reflow_tests.rs`。后续实施还需映射
终端底层写入与stream consolidation调用方，不把这三模块视为整个TUI的完整追踪。

行数配置为Auto/Limit/Disabled，**Disabled表示不限制行数，不是关闭重排**。
Auto：VS Code1000、Windows Terminal9001、WezTerm3500、Alacritty10000，其他终端
回退1000（`config/src/types.rs::DEFAULT_TERMINAL_RESIZE_REFLOW_FALLBACK_MAX_ROWS`）。
显示截断提示也消耗行预算；一行预算优先内容而非提示。旧历史未加载时可请求本地
历史页补足展示，不能引入官方远程history notes或账户服务。

## Corki 当前对应行为与影响

| 行为 | 当前证据 | 差异与影响 |
| --- | --- | --- |
| 保存展示源 | `TerminalUI`各show/append方法直接`Console.print` | 无统一源cell；窗口变更无法重新生成历史 |
| 流式正文/reasoning | `begin_*`打印标记，`append_*`立即写片段，`end_*`只写换行 | 没有最终源合并点；不能只重绘已结束正文 |
| 普通输入源 | `read_message`由PromptSession留下已提交输入，application未显式回显 | 重建只收集show方法会丢用户输入；回显又可能重复 |
| 确认面板 | `InputOwner`用modal计数/锁打断composer；form使用DummyHistory | 输入所有权已有，但没有显示重排延期，不能绕过它清屏 |
| 新宽度输出 | Rich重新读取终端尺寸；两Turn计划PTY40↔100已验证 | 只证明后续输出，不证明旧内容修复 |
| 冷启动历史展示 | `application.run`欢迎后直接`resume_pending()` | 不等于回放已完成业务历史，源恢复仍需单独接入 |
| clear | `TerminalUI.clear`清屏后欢迎 | 新缓存必须同步清理，否则resize会让已清除展示重新出现 |

优先级：F的重要缺口，需先建立真实生产使用的展示源与所有权，再实现重排。
不能仅新增无人调用的reflow状态类，不能捕获终端ANSI输出作为“源”，也不能把审批
回答、授权token、当前未提交草稿写入业务历史或普通输入历史来解决展示问题。

## 实施验收约束

- 真实CLI统一记录已提交用户内容、完成和活动中的正文/reasoning、工具、计划、通知。
  源数据与模型历史分开，保留明确的stream合并/取消边界；不重复执行工具或采样模型。
- 重排必须在prompt-toolkit绘制所有权下运行，modal期间延后，关闭后恢复草稿/焦点。
  窗口变化可独立触发，不要求新事件或按键；应用关闭要join相关任务。
- 使用可控时钟验证75ms合并、已观测与已绘制宽度差异、仅高度变化、结束流之前/之后
  的请求，以及空源/clear/线程切换复位。显示行裁剪不能修改持久化会话。
- PTY在提交新输入之前resize，要求**旧内容**以新宽度重现；分别覆盖活动流、空闲输入
  草稿、确认面板、连续resize、取消和冷恢复。不用新输出宽度测试冒充这组验收。
- 当前未实现上述重排。后续保留目标A–E的其他待验收项，不能把F本项当作整体目标。
