# F / B：CLI 用户问题面板

## 多问题确认按键修正

原生 handle_confirm_unanswered_key_event 明确规定 j/k 在 Proceed/Go back 间移动，
Backspace 与 Esc 都返回第一个未回答问题。Corki 原先给 j/k 使用了仅 options 的
filter，确认层只能忽略字符；Backspace 也未接入确认层，后续 Enter 仍提交空答案。
新增真实 PromptSession 多问题输入序列在修复前 2 失败、2 通过（47b4bc），证明
不是仅凭源码推断。现扩大 j/k 的选择过滤范围并补确认层 Backspace；不改 notes
中的普通字符含义。另两条覆盖翻页不自动提交及跨题保留已提交 notes。

修复后联合 Runtime 提问、CLI 所有权、40/100 列真实 PTY 共 32 通过、2 条 forkpty
警告，7.35s，exit 0（fce6c9）；Ruff、1122 文件格式、compileall、77 包兼容检查通过。
无活动测试。该结果不覆盖整个多问题/布局矩阵，也不代表整个目标完成。

基准仍为本地 Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc。此功能属于
普通 Harness/CLI，不涉及官方账户、服务或原生模型协议。

## 源码链与原差异

原生 chatwidget/tool_requests.rs::on_request_user_input 经 interrupt manager 延后或
调用 handle_request_user_input_now，先 flush_answer_stream_with_separator，再调用
bottom_pane::push_user_input_request。现有视图可接收同类请求，否则新建 overlay。
bottom_pane/request_user_input/mod.rs 的 reset_for_request 为首项高亮，但
answer_committed=false；select_current_option/submit_answers 只序列化显式提交项。
问题可翻页；未回答的问题在最终提交时进入确认面板。notes 逐问题保存，作为
`user_note: ...` 附加答案；Other 的标签为 `None of the above`，不是授权选项。
handle_key_event/on_ctrl_c/handle_paste 区分导航、提交、清空 notes 和中断 Turn。
读取了 options 基准快照、主要按键/提交/草稿/取消源码；未完成所有 render/layout
源码及快照逐项映射，不能将当前面板称为像素级对齐。

Corki 原差异：Runtime 已发 UserInputRequested，但 CLI 不处理该事件，因此工具
等待无法通过终端回答；InputOwner 只有 MCP/执行审批模态入口，不能拿审批 response
代替问题答案。这是实际功能缺失，不是用户排除项。

## 本批实现

- cli/user_input.py 实现独立问题面板；选项高亮/committed 分开，Enter/数字显式提交，
  options/notes、逐题草稿、未回答确认、自由输入/粘贴及中断路径；使用 DummyHistory
  的独立 form session，不把回答当成普通聊天输入。
- TerminalUI.read_user_input 管理 modal_depth、字面量显示和 form buffer 清理；
  InputOwner 的通用模态所有权用于互斥/让出普通输入，不复用 MCP 授权语义。
- CorkiApplication 消费问题事件后创建受 renderer 所有的面板任务，不阻塞 Runtime
  事件读取；工具完成可撤销对应面板，Turn 终态/渲染器失败/取消都 join 面板清理。
- Runtime.cancel_user_input 同步校验活动 Turn、call ID 和仍 pending 的 waiter 后
  中断精确 owner，避免旧面板误杀 successor。回答仍走 respond_user_input，不授予
  权限；面板故障以取消问题的 Observation 解除等待并记录故障类型。

重要修正：原生 core 在两种模式都等待回应，但 TUI 对 isBlocking=false 有明确
60 秒隐藏宽限 + 60 秒可见倒计时策略，无人交互时返回 **空 answers map**。
任何用户按键会禁用此自动结束；Plan 的 blocking 请求不使用该计时器。
Corki 已实现此宿主策略；不是“超时默认接受首选项”，也不是调用官方服务。

## 验证与仍需完成

实际 PromptSession 管道键盘测试：默认不提交、上下选项、数字、notes、Other、
Esc/Ctrl+C、表单草稿清理与无输入历史；注入单调时钟验证仅无人交互的非阻塞问题
返回空答案。实际 Application + Runtime 验证普通 reader 与问题面板互斥、答复/
面板中断/外部取消/面板异常后释放所有权。40/100 列真实 PTY 走普通 function call
到问题面板、显式选择、下一模型请求验证答案，并验证恢复原普通输入草稿。
这些是 ScriptedModel Harness 证据，不证明真实模型的提问质量。

仍未验收：多问题翻页/未回答确认完整按键矩阵、长内容/极窄与滚动布局、多个外部
回应/连续问题的交错、结构化结果历史呈现与冷回放、重映射快捷键、完整模式 CLI
入口及提案块。当前面板不应被描述为整个 F 或整个 A–F 已完成。
