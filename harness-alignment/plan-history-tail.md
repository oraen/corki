# F1/F6：历史面板计划临时尾部

## 针对性真实键盘补验

test_proposed_plan_tail_pty.py 新增 browse_history 维度，保留原不浏览场景：
40/100 列 × Ctrl+C/stop。真实 Runtime 的模型挂起在计划表格生成中；
输入草稿后 Ctrl+T，必须在进入 alternate screen 之后再次收到 alpha/beta。
模型等历史视图 active 后补齐新行，要求新标记实际输出；q 返回主界面后
草稿仍可见，清空草稿再取消。结束断言临时视图和重放无尾部、SQLite 没有
半截 AssistantMessage、模型仅采样一次且关闭。没有改生产代码。

第一次增量标记采用 gamma，40 列两例超时（619315）：终端实际输出
`g ESC[C mma`，复用旧单元格的 a，不是尾部未刷新。测试改用先前画面
没有的 ΩΨЖ，避免把原始字节连续性当成屏幕连续性；不以正则放宽到任意文本。
此补验不构成全屏像素比较，也不验证模型选择/摘要质量。

最终联合计划尾部/历史 PTY 与计划渲染/历史视图/缓存单测：38 passed，
36.03 秒（882c9f），session 12898 退出 0。静态检查全部通过
（2f1e1b，1140 文件、77 包）。下方旧“针对性 PTY 未执行”现由本节补齐；
本项预览缺失已修复并验证，完整 F 的其他差异仍按总清单推进。

## 已实施及验证

HistoryView.text 现将计划预览组合到 formatted/lines；content 与 Transcript
仍仅保存已提交渲染。缓存包含预览 fragments，完整新行刷新，取消撤销；
正文控制器存在时不显示计划尾部。没有新增持久化字段或协议。

四例反例修复前失败（03ef66）：历史面板无 alpha/beta。
修复后完整 CLI 单测 383 passed（7bd680）；继续增加未完成行补齐后的
gamma 刷新、无重复、队列不变与归档不含尾部断言，CLI + 实际 Runtime
计划/失败/历史/流式清理联合 420 passed（a24630，12.27 秒）。
静态检查全部通过（726a5e，1140 文件、77 包）。物理历史面板中途切换
的新增针对性 PTY 尚未执行，不能仅由上述测试关闭完整 F。

既有历史面板、计划尾部和计划完成三个 PTY 文件另 22 passed
（8f7539，35.04 秒），进程退出 0。此批防回归覆盖既有场景，不冒充
“计划表格流中切换历史面板”的新增键盘场景。

## 实施前源码差异

参考 commit ddf04ad26789d040f9ef6a96736f76602e35a6cc。
原生 tui/src/chatwidget/streaming.rs::sync_active_stream_tail 将计划控制器
current_tail_display_lines 放入 StreamingPlanTailCell，更新 active revision；
chatwidget.rs::active_cell_transcript_hyperlink_lines 把该 active cell 提供给
历史 overlay，而不是写入已提交历史。正文控制器优先，清理会撤销 transient cell。

Corki PlanStreamUI._plan_tail_fragments 已能生成只读尾部，但 HistoryView.text
只调用 Transcript.render；后者有意仅重放计划 committed_rows。因此历史面板
缺少主界面已经显示的表格尾部。状态：行为不一致；范围仅本地 F，不涉及模型协议。

方案：历史面板在已提交内容之后组合计划临时 fragments，并将其纳入帧缓存键；
不修改 Transcript.render 的持久/修复语义，不 drain 队列，正文存在时不抢占。
验收：40/100 列、有无稳定前缀、增量尾部刷新、无半行、取消撤销、calls 和
队列不变。物理历史面板切换需后续 PTY 补验，不由渲染单测外推完成。
