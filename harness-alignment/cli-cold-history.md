# CLI 冷恢复历史展示：缺口与接入约束

## 最新：失败/取消终态展示已接入

未匹配ToolResult的call现按Turn/call_id保留待确认显示，并在已知终态或旧abort时
显示“interrupted; completion not confirmed”，不伪造完成，不提前关闭running。
同名双调用部分结果、failed/旧abort真实冷回放及原历史不改已验证，相关198项通过。

展示专用DisplayHistory从SQLite单一读事务获取items及Turn状态/error。Runtime保持
读取所有权，CLI以Turn边界显示错误/中断，不修改模型历史；已有abort marker去重。
真实冷回放失败/取消×有/无正文、多Turn顺序及读取取消生命周期、并发写入快照
组合278项通过。40/100列冷失败真实PTY+流重绘16通过，静态1067文件77包通过。
这里只关闭遗漏失败终态，完整历史工具中断展示、分页和其他F项仍待继续验证。

## 最新：普通归档消息与仅文字内容块

cli.history现显示普通RemoteHistory message的user/assistant文字与媒体占位，仍跳过
opaque agent_message/compaction。本地用户content为空但content_items有文字时会
显示文字，text-only不再错误显示附件占位。真实冷Runtime+application+TerminalUI
验证无采样、无历史改写、无输入历史污染、正文/重绘各一次、密文及内部metadata
不显示，相关132通过2.23秒。图片/音频仍是占位，不代表完整附件浏览或冷PTY完成。

## 最新补验：读取生命周期与pending图恢复

读取生命周期10组已覆盖read/cancel/repeat_cancel/close/cancel_close×正常/抛错，
已启动读取先join、资源后关闭，关闭后拒绝再次读取。见test_display_history_lifecycle.py。

test_cli_pending_history.py现通过真实_compiled图与SQLite checkpoint在模型commit后、
工具结果append后两种窗口停止，再用新Runtime/registry真实冷CLI恢复；持久化用户/
正文/结果/计划均一次显示，重绘亦不重复，工具总1次、冷模型后续答案采样1次，
历史旧项未丢且无重复user/result，pending终结。相关525通过5.21秒。
替代下文“实际pending尚无验证”的笼统缺口，但不意味着全故障矩阵或真实kill/PTY
已验收；失败Turn与附件、行上限/分页/冷PTY仍开放。没有新增原生协议或官方服务。

## 最新实现（覆盖下文修复前状态）

Runtime新增load_display_history，在turn/lifecycle锁下完成正常初始化后读取本地原始
items，已发起读取shield/join并保留取消；不调用resume执行或工具handler。CLI在
resume_pending前调用并将用户/assistant、工具/结果、计划、可显示reasoning summary
映射到实际TerminalUI显示入口与Transcript源；审批输入、ContextItem和专属归档
payload不打印。CompactionItem之后replacement_item_count覆盖的模型替换项跳过，
retained_from_id用户副本亦跳过，原始用户记录仍显示。

AssistantTextDelta/AssistantMessageCompleted新增可选item_id，ModelOutput真实发出；
PlanUpdated新增可选tool_call_id，graph真实发出。CLI以回放的item/call身份过滤
随后的恢复事件，不按文本去重；字段缺省None兼容旧构造，未修改模型请求协议。

真实两Turn→关闭→同库同Thread冷CLI验证：两个原始请求各一次，同文不同id的答案
两次，计划两次；追加压缩标记/保留副本不造成重现用户副本或显示私有摘要；冷模型
0次采样、仓库items前后相等、输入历史为空，重绘源仍保留两条合法相同答案。
额外应用层恢复事件fixture验证旧正文/工具/计划身份去重，新增id同文仍显示。
CLI/core/protocol单位与相关body/recovery/citation集成590通过20.70秒。

仍待验收：实际pending checkpoint故障窗口跨冷CLI的组合验证、读取重复取消与关闭
故障注入、历史失败Turn错误与附件更完整呈现、分页/行上限及真实冷恢复PTY。当前
不把手工恢复事件fixture当成这些实际故障窗口已验证；下文原始审计保留其依据。

状态：缺失；不能把`resume_pending()`成功或模型记得旧内容当作UI回放完成。

## 已核验源码

Codex `tui/src/chatwidget/replay.rs`：`replay_thread_turns`依次处理Turn及其items，
通过`replay_thread_item`→`handle_thread_item(...Replay(...))`区分回放与live。用户消息、
assistant最终消息、命令和MCP完成项等进入显示状态，不再执行其工具；历史状态也能
展示失败/中断。原生远程Thread读取属于产品传输，Corki应接本地业务仓库而非照搬服务。
本轮读取完整replay.rs；其引用的各具体cell样式仍由F逐项对齐，不视为全部已完成。

Corki `cli/main.py::build_application`：解析resume为具体thread_id，传入Runtime；
`application.run`欢迎后直接消费`runtime.resume_pending()`。后者在生命周期锁内
初始化Runtime，检查latest_running_turn及checkpoint，没有running Turn就返回，不
产生任何已完成历史展示。不能把该方法改成“重跑所有旧Turn”来实现回放。

`storage/sqlite.py::_load_items`按conversation_items.sequence读取原始业务项，
不经过模型窗口构造；这是需要的源。必须保留与ContextItem、CompactionItem、
归档兼容项的区别，不能无差别把所有content打印为用户/assistant消息。

## 真实冷启动证据

离线临时目录中，第一Runtime执行一轮并保存`HISTORY_REPLAY_EVIDENCE`，关闭后，
使用相同数据库/thread_id创建冷Runtime和真实CorkiApplication/TerminalUI，输入
立即EOF。结果（3d2a5d）：

- 第一轮终态TurnCompleted，原始仓库确认有最终assistant内容。
- 冷CLI退出0，冷模型调用数0，输出中没有已保存答案。
- 临时目录由TemporaryDirectory回收，未修改用户会话、未连接外部模型。

因此“数据丢失”不是这个场景的原因；缺少从本地业务项到展示源的启动回放。

## 实施前需要固定的契约

1. 在恢复执行前取得一致的本地历史视图并渲染；不得为了显示调用模型或工具handler。
   与writer/lifecycle锁边界协调，关闭与取消时不遗留异步读取。
2. 回放用户提交不写入PromptSession输入历史、不重复回显；把它加入Transcript展示源，
   以后resize可重绘。不得回放审批回答或未提交草稿。
3. 工具调用/结果按call_id配对显示；有结果的操作不变回“运行中”，失败不能标成功。
   计划从已保存ToolStateUpdate还原说明和步骤，附件保留显示占位/元数据而非泄露原始大数据。
4. 同一pending Turn可能已有持久化用户项、正文和结果，随后resume事件可能再次报告
   已完成工作。回放与live必须用稳定身份去重，不能只按文本（合法重复内容会被误删）。
   现有ToolCallStarted/Completed有call_id；AssistantMessageCompleted缺item_id，
   PlanUpdated缺call_id，这些现有事件边界需要一并评估，不能跳过pending恢复场景。
5. 复用与resize相同的显示行预算；业务源不被裁剪。先显示尾部时要明确更早历史的
   本地可访问路径，不能引入官方history notes/namespace/native协议。
6. 验收至少包含：已完成多Turn冷恢复；工具及计划恢复不重执行；压缩后回放仍见原始
   消息而非仅摘要；pending故障窗口不重复；失败/取消终态；PTY及resize后回放源稳定。

本轮只确认原因和接入契约，未实现冷历史展示；A–F整体目标保持不变。
