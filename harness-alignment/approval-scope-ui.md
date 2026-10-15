# 宿主执行审批：统一范围选择列表

## 新确认：编号选项缺少对应按键

原生 approval_overlay.rs::build_options 为选项创建 SelectionItem，未开启
require_explicit_confirmation；handle_key_event 把非审批字母快捷键交给列表，
list_selection_view.rs 将有效数字映射到 enabled item，select_shortcut 调用 accept，
overlay 再 apply_selection。数字本身是明确提交，不是默认高亮自动授权。
Corki choose_approval 显示编号但 Keys.Any 忽略数字，形成可观察的不一致。
本批仅为存在的选项绑定 1..9 到既有 action；不改变一次/会话/规则的可用性、
后端身份检查或权限。越界数字和 bracketed paste 仍不提交、不污染输入历史。
先补单位与真实 PTY 的数字选择，再验证现有授权桥接；不引入官方服务。

已实施：choose_approval 对当前 actions 前九项绑定编号，并直接返回同一 action；
编号不依赖当前高亮。修复前新增九个数字用例全部超时、原十九项通过（5b85ff）；
修复后28单位通过（d49448）。现有粘贴隔离探针加入1/2/3与换行，越界0/4/9
在只提供once的三选项列表中仍忽略。执行桥接从方向键/字母扩为方向键/字母/数字，
28项通过（6335ba，14.40秒）：direct/CodeMode、once/session跨命令及冷启动、
rule成功/I/O失败后的真实执行与权限状态均保留原断言，不是只验证UI返回值。
完整审批PTY、单位与当时20项桥接联合104通过（fad4c1，108.01秒）；PTY新增
数字选择三scope及拒绝/取消，40/100列下粘贴不提交、普通后续输入与历史恢复。
该104收集早于桥接数字扩充，最终28桥接由6335ba独立证明，不混算唯一数量。
ruff/format/compileall/77包依赖/diff通过（3bc4ff）。本批不实现自定义keymap或
新授权范围，不宣称整个F或A–F已完成。

## 最新：快捷键实际执行桥接已补验

test_cli_execution_scope 将既有 once/session 与 rule 保存成功/失败场景扩展为
方向键提交/快捷键两种输入，仍走真实 TerminalUI→Application→Runtime→原生执行。
direct/Code Mode 下 y 每次询问、a 同命令复用但换命令/冷 Runtime 重问；p 保存精确
提议后才发布 live 规则，I/O 失败只保留本次明确批准，后续和冷启动均不获新授权。
额外 d/n 四组合验证命令未执行、会话授权空、pending 清空、唯一终态；d 返回模型
继续采样，n 不再采样且 Code Mode 的取消后语句不执行。表单与普通输入历史保持空。

最终桥接文件20通过（1999e1，10.41秒）；加入d/n前的扩大执行取消/规则发布/审批
单位84通过（69a8df，21.71秒），不把该84称为已经包含随后新增d/n。静态1110文件/
compileall/77包/diff通过（60dca6）。本轮无生产授权策略修改。下方“仍需实际后端
桥接”阶段项由本节覆盖，完整CLI视觉/自定义keymap及整个A–F不据此关闭。

## 最新 F3/F4/F6：默认快捷键与粘贴隔离

按原生 keymap.rs/approval_overlay.rs 真实 options 映射，宿主执行审批新增
y=once、a=session、p=rule、d=decline、n=cancel；a/p 只在请求提供对应 scope 时
绑定。当前高亮不改变快捷键含义，标签显示可用快捷键，方向键/Enter/Esc 原语义保留。
普通信息表单不自动套用执行授权快捷键，后端范围与规则提议验证仍保持原链路。

原生 BottomPane::handle_paste 将粘贴交给活动 view，ApprovalOverlay 沿默认忽略实现；
Corki 原先 Keys.Any 没拦住 prompt_toolkit 的特定 BracketedPaste 默认处理器，粘贴
会插入表单缓冲。新增显式 BracketedPaste 忽略，阻止粘贴 y/a/p 与换行授权或污染历史。
修复前 6 失败 12 通过（a95e43）：5 快捷键超时及1粘贴缓冲污染。修复后19单位通过
（c57d6c），含移动高亮后 y 仍仅允许一次；不存在 session/rule 时 a/p 不授予权限。
真实40/100列PTY覆盖三scope快捷键、粘贴后不提交、d/n语义及普通输入焦点/历史恢复。
完整CLI单位与审批PTY350通过、46条forkpty警告（c7bc94，82.28秒），采集早于最后
一个高亮无关性单位测试，该项由后续19组覆盖。静态1110文件/compileall/77包/diff
通过（392d33）。本批不声称实现原生自定义keymap、全屏详情或线程切换。

粘贴证据限定终端明确标记的 bracketed paste；未标记的字节流无法可靠区分真实按键
与粘贴，不宣称自动识别所有输入来源。仍需把快捷键接实际执行后端的授权桥接验证。

范围 F3/F4/F5，涉及执行授权边界。不是官方服务入口；不修改 sandbox 权限、审批
缓存键、规则写入策略或 MCP OAuth。

## 源码基准与实际差异

Codex tui/src/bottom_pane/approval_overlay.rs::exec_options 按 available_decisions 生成
一次允许、会话允许、允许并保存提议规则、拒绝、取消。handle_key_event 将列表显式
提交转换为 apply_selection；try_handle_shortcut 还支持配置快捷键。默认高亮不调用
apply_selection。不同范围最终由不同 CommandExecutionApprovalDecision 表达。

Corki execution/approvals.py::authorize/authorize_patch 已根据当前请求提供 scope enum：
once/session，以及有提议且配置规则路径时的 rule；patch retry 只有 once。此前
collect_elicitation 把这些当字符串表单，输入scope之后才choose_approval三项确认。
授权语义存在，但交互不是原生同一列表选择，不能把两次问答称为完全一致。

当前修复：TerminalUI.read_elicitation 为宿主 shell/patch 审批提供 choose_scope，
collect_elicitation 在展示原消息、实际参数与重试证据之后，仅当表单只有受支持scope
字段时调用该列表。choose_approval 以具体scope构造选择结果；一次默认高亮，方向键
不提交，Enter返回accept+scope，Esc/Ctrl-C/Ctrl-D返回cancel+None。未提供的范围不
显示；patch不接受rule列表。普通服务kind保持通用表单，即使也有scope字段。

结果继续走Application._handle_elicitation原映射：session→remember，rule→原请求
提议，Runtime.respond_execution_approval保持原身份、可用范围和准入检查。本批不
增加服务端自报kind的信任，不替换原参数，不从文本推断持久授权。

## 证据与边界

最新扩大验证：实际键盘执行桥接、全部规则发布集成、审批/elicitation单位及审批PTY
134通过0跳过36条forkpty警告（4c1ea3，73.67秒）；静态1108文件/compileall/77包/diff
通过（07b49d）。无活动测试，完整A–F继续；下方“规则发布待验证”为本批前的阶段记录。

规则发布桥接补验：test_cli_execution_scope 新增direct/Code Mode × 保存成功/I/O失败
四组合，实际选择第三项rule，经Application从原请求取提议["touch"]，调用真实后端。
保存前目标未执行/规则不是文件；成功后文件精确为一条touch allow规则、live prefixes
精确匹配，session集合为空，后续默认权限命令及冷Runtime通过规则放行。I/O失败用
临时default.rules目录制造，当前批准命令仍执行且Warning明确，live/session均未扩大，
后续与冷命令不写目标。连同前轮once/session共8通过（f4fcc4，4.64秒）。

故障基准是原生core/src/session/handlers.rs::exec_approval：持久化失败发送Warning，
仍notify原当前批准；session/mod.rs::persist_execpolicy_amendment调用append_amendment_and_update。
Corki execution/rules.py同样只在后端ack written/published后更新live prefixes，失败
返回warning、不缓存session、不重试写入。本批没有新增放宽策略，也不将保存失败改成
隐式授权后续请求。40/100列真实PTY的范围选择另扩展rule选项，覆盖第三项显式提交。

本轮补齐列表到真实执行的桥接：test_cli_execution_scope.py 使用实际TerminalUI的
PromptSession管道键盘事件，经InputOwner/Application/Runtime执行响应进入原生权限
后端，非返回预设action的UI替身。direct/Code Mode × once/session四组合通过
（214765，2.90秒）。同一Runtime连续两Turn同命令：once询问两次，session一次；
换命令必须再次询问；关闭后同thread冷Runtime不继承会话批准。测试命令仅在临时目录
mkdir -p，验证目标目录实际创建、模型请求次数、待审批队列清空及表单/输入历史为空。
这是管道键盘集成，不冒称新建了真实PTY；前轮40/100列scope PTY提供终端展示证据。

原生core/src/tools/sandboxing.rs的with_cached_approval仅在所有key命中
ApprovedForSession时省略询问，只有该决策写cache；Corki ExecutionApprovals以
命令/cwd/tty/权限意图/策略指纹的会话集合控制复用，Application把scope映射到remember。
新测试验证了命令与冷生命周期边界，不以两者内部key类型不同宣称逐字段等价。
现有原生后端binary与source摘要再次等于manifest（319f18），未重建后端或改用户配置。

最终扩大范围包含新增键盘执行桥接、全部执行取消、审批/elicitation单位和全部审批PTY：
107通过、0跳过、34条forkpty警告（bf708b，64.61秒）。静态ruff/1108文件格式/
compileall/77包依赖检查通过（a83df8），最终diff检查通过（10cf4a），没有活动测试。
本批只增加真实行为证据，未修改生产授权实现；保存规则的实际持久发布未被这四组合
覆盖，需要独立桥接验证，不能由会话缓存通过推断其完成。

新增单位覆盖一次/会话/规则/拒绝/取消，默认不提交，表单buffer/history为空；kind
控制测试证明服务端scope仍是普通数据。新增40/100列PTY直接进入宿主选择列表，
移动后等待不产生决定，Enter返回准确scope，普通后续输入唯一且恢复。初专项19通过
（0e3165，6.79秒）；扩大结果见current-scope.md。

基线审批PTY/MCP组合118通过14跳过（67b5f0）；跳过为显式原生执行后端未设置。
核对现有Darwin arm64后端binary及source摘要均等于manifest后，14项实际执行全部通过
（6a7641，2.45秒），覆盖direct/Code Mode、API/CLI绑定、cancel/decline、冷恢复和
暂停消费者。它们证明执行控制，不等于新增列表与真实命令全部组合已做PTY。

仍不声称整个F完成：原生按键配置与y/d/n等快捷键尚未对齐；当前仅方向键/Enter/取消。
新增scope列表不含原生所有网络策略选项，须先判断现有宿主提供哪些决策，不能无依据
新增授权能力。完整视觉布局/长参数浏览与其他CLI核心剩余项仍由总验收索引跟踪。
