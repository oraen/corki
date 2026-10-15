# 审批完整详情入口

## 深色回退配色与色深接入（实施前）

原生diff_render.rs::diff_theme_for_bg在没有背景采样时使用Dark；rich色深下
新增/删除整行分别使用#213a2b/#4a221d（truecolor）或22/52（256色），ANSI16
不着背景，换行后的整行背景保持。Corki目前所有色深都只保留前景，且详情Rich→PTK
转换丢弃bgcolor。现有CLI没有背景采样基础设施，本批先完成原生“无背景采样”的
明确默认分支；不以环境字符串猜测浅色、不将浅色和自定义scope覆盖项判定不适用。

方案：审批分页用实际PromptSession的有效color_depth创建Rich console，预览折行时
对带diff元数据的新增/删除行及续行填满可见宽度背景，保留前景/语法/行号，普通
文本与context行不着色。PTK桥保留背景颜色，色深改变时使已缓存行失效。测试验证
40/100列、truecolor/256/16/无色，以及实际分页片段，不改变原始审批内容或权限。

已实施：wrap_preview仅对带内部diff_gutter元数据的增删行识别正负号，按每个
物理行填充深色背景，不修改原始Text；context/普通文本维持原样。ApprovalDetails
读取session.app.color_depth，构建对应Rich色深并保留bgcolor到PTK片段；缓存键
加入色深。背景矩阵修复前8 failed/8 passed（d6492c），实施后通过。
分页测试最初给PromptSession传了不支持的callable色深（fd6bd3），改为其真实
color_depth属性；源码prompt.py由内部lambda读取此属性，不能将夹具误用算生产缺陷。

最终CLI完整单位480 passed（099c17，8.45秒），40/100列shell/patch详情真实PTY
8 passed、56 deselected（a10827，17.24秒）；静态ccf52c通过（1182文件、77包）。
新增精确样式检查覆盖整行/续行RGB或索引背景与分页色深切换；PTY验证交互回归，
没有宣称截图逐格对比。仍需浅色背景采样、自定义syntax scope背景与平台色深
识别差异的独立处理；本批只关闭明确的Dark fallback与背景桥接缺口。

## 高亮初始化失败：确认内容不可被装饰错误阻断

本轮复查原生 diff_render 的配色链确认：rich色深有新增/删除背景及明暗配色，
ANSI-16为仅前景，主题scope可覆盖背景；Corki仍未完整对齐这部分，不能关闭主题项。
同时核对 render/highlight.rs::highlight_to_line_spans_with_theme 的失败返回None/原文降级，发现
Corki syntax.highlight_code 仅捕获词法器选择时的ClassNotFound，其他初始化异常
会穿过审批详情生成。已有“词法器异常均回退”描述过宽。

方案：将词法器选择、TextLexer判断与Syntax高亮统一置于装饰失败捕获边界。
字节/行数限制仍在选择前执行，失败不影响原文、不改变审批动作。扩展现有故障
测试至Syntax构造、按文件名/按语言名选择三处；先验证两处新增初始化反例。

修复前两处选择器初始化异常测试失败（8c9dff，2 failed/6 passed）。现已统一
捕获装饰异常，保留超限先行拒绝与原文一致性检查；三处故障同时验证补丁预览
及实际AssistantMarkdown渲染均保留源文本。CLI完整单位集合463 passed
（502da1，8.39秒），静态d302a5通过（1182文件、77包）。这修复失败降级，
不是完成主题配色；未更改审批结果或执行权限。
40/100列shell/patch完整详情只读进入/返回真实PTY回归8 passed、56 deselected
（36fdc3，17.42秒）；此PTY是交互回归，初始化故障由上述渲染测试注入。

## 补丁语法高亮（实施前方案）

原生diff_render.rs按目标扩展名选语言，新增/删除整文件高亮，Update每hunk整块
高亮以保留跨行状态；删除正文叠加dim，正负号保持diff色。render/highlight.rs
限制512KiB、10000行及单行4KiB，未知语言/超限回到原文。Corki patch_preview当前
只给整行红绿，Markdown已有Pygments/Rich高亮。方案提取共享有界高亮，保留词法器
不能修改文本的校验，接入补丁正文与现有硬折行；不读取待审批文件、不执行patch。
复用现有Markdown主题，不将其冒称已实现原生全部主题/色深/背景与精确词法配色。

已实施：cli/syntax.py提取原Markdown有界前景高亮，按语言名或文件名选lexer，
超限、未知语言、词法器异常或文本被改写均回退原文。patch_preview按目标文件名
选择语言，新增/删除整块、Update逐hunk；删除正文dim，正负号保留红绿，现有
hard-wrap保留Rich样式。ApprovalDetails转PTK时保留bold/italic/underline及原有dim。
不读取真实待修改文件、不执行补丁、不改变授权。主题暂沿用现有monokai，背景未补齐。

新增目标扩展名/rename/add/delete/update、跨行字符串与40/100列续行、三项性能
阈值和lexer异常回退测试。高亮专项20通过（99b03a）。首轮导入误写Pygments API
导致收集失败，已按本地get_lexer_for_filename修正；这不是生产先红后绿证据。
完整CLI单位初轮451通过4失败（57887d），失败为旧Markdown测试仍替换已迁移的
局部lexer函数；迁移哨兵至共享syntax模块并保留超限不调用断言，新增短参数ID避免
失败日志输出巨大源码。最终CLI单位、真实部分补丁重试、完整审批PTY联合
**529 passed，64条forkpty多线程弃用警告，122.61秒**（19b6df，77203退出0）。
静态d00881通过：ruff、1183文件格式、compileall、77依赖、diff，无活动测试。
新增语法细节为文本/样式断言，PTY是实际审批交互回归，不冒称逐格主题对照完成。

## 多hunk结构化预览（当前修复）

重读原生tui/src/diff_render.rs::render_change的Update路径：按hunk分组渲染行号，
首块不显示@@头，后续块之间以按行号栏对齐的dim ⋮分隔，不重复---/+++文件头。
Corki patch_preview之前同时显示文件标题、原始头与逐行编号，存在可观察差异。
两个预览断言先红（75be1b，2失败），现改为相同分隔规则、全文件最大行号宽度；
真实源码行内容与正负号保留，request_details仍单独展示完整原始patch与参数，
没有删除审批证据或改变确认授权。语法高亮、颜色主题仍是独立未完项。

修复后详情单元11通过（55131f）；详情/实际部分补丁重试/40和100列只读详情
PTY联合23通过、56 deselected、8条forkpty多线程弃用警告，19.22秒（41c93d）。
静态13ae40通过：ruff、1181文件格式、compileall、77依赖与diff，无活动测试。
此次新增多hunk外观为精确文本断言；PTY为现有审批只读/返回回归，不冒称新增
多hunk逐格终端快照已经完成。

## 续行与源文件行边界（实施前确认）

原生 diff_render.rs::render_change 使用 Rust str.lines（仅 LF/CRLF）；
Corki render_changes 使用 Python splitlines，额外拆分 U+2028、VT、裸 CR，
使源文件行号和增删计数错误。原生 push_wrapped_diff_line 的续行保留空 gutter，
wrap_styled_spans 按显示列硬折行；Corki ApprovalDetails.create_content 使用
通用 Text.wrap，续行回到最左列且按单词折行。两项均为 F3 可观察显示差异。
计划：仅标记内部生成的编号 diff 行，按显示宽度折行并保留样式及空 gutter；
普通消息/原始参数不推断为 diff。以精确行号、空行、CRLF、Unicode 和 40/100
宽度回流验收，不改审批状态或执行协议。主题背景/语法高亮仍另行跟踪。

已实施：_source_lines 仅按 LF/CRLF 分行，裸 CR/VT/U+2028 不增加行号，
编号正文在进入 Rich 前将控制字符替换为空格，避免 Rich 删除字符后拼接两侧文本。
render_changes 用内部 Style metadata 标记 gutter；wrap_preview 仅对这些行按
显示列硬折行，空白 gutter 对齐正文，保留尾部空格和颜色，普通文本继续通用折行。
ApprovalDetails.create_content 已接入，不是独立的未使用 renderer。
极窄窗口可出现 gutter 加单个宽字符超过窗口宽度（原生同样保证至少消费一个字符）；
不据 40/100 列测试声称所有宽度逐格等价。

先跑新增反例：3个真实行号/正文失败、2个待实现 helper 导入失败，旧4项通过
（12d7ae）；首次实现后3项仍失败，确认 Rich 会删除部分控制字符，随后显式净化。
最终单位10通过（bc8437，0.68秒），覆盖三种变更、CRLF/空行、硬折行、宽字符、
组合字符、空白和样式；真实 Runtime 的38k补丁在40/100列重组折行正文逐字一致，
并检查每个续行缩进、无副作用、取消/拒绝/关闭。详情/重试/PTY联合34通过、
84未选、8条forkpty警告（632d57，22.98秒）。PTY证明既有完整详情交互回归，
新增 gutter 的直接画面证据是单位精确文本及 Runtime 管道渲染，不冒充新增PTY快照。
静态检查1177文件、compileall、77依赖、diff检查通过（052066）。
本批未运行原生 Rust 测试，基准 commit 与干净状态已重新验证。
扩大 CLI 单位回归451通过（084a92，7.83秒）；与上方联合集合重叠，不累加计数。

## 结构化差异预览与样式接线

本轮追踪diff_render.rs::DiffSummary：按路径排序、line_counts显示增删计数，
render_change的Add/Delete从1编号，Update从hunk头分别维护old/new行号，删除取旧行号、
插入/上下文取新行号；push_wrapped_diff_line按增删颜色保留样式。Corki CLI没有可复用
的等价显示器，core/turn_diff是生成业务diff而非审批画面，不复用执行逻辑渲染审批。

新增cli/patch_preview.py，直接消费已经通过native patch_review校验的changes，
只读显示排序后的文件、增删计数、行号、移动目标与绿色新增/红色删除。完整raw patch、
其他参数和不确定性证据仍保留，既不重新应用补丁也不改变审批身份。request_details
返回Rich Text，ApprovalDetails净化控制字符后保留样式，折行转换为prompt_toolkit
片段；并未把ANSI控制序列作为可信内容交给终端。原plain source仍兼容。

新增精确文本/样式用例覆盖add/delete/update/move、hunk old/new行号；真实Runtime
长补丁在40/100宽度检查着色片段中仍包含尾部。详情单位4通过（b6cd2f，0.70秒），
此前真实Runtime详情/重试16通过（8f6cad，5.01秒）；最终扩大回归另列。
静态1177文件/77依赖通过（01edc4）。这不是全部DiffSummary视觉复刻：语法高亮、
主题背景、续行gutter缩进及完整终端逐格快照仍有差异，应继续收敛而不是隐藏。
最终详情单位、Runtime长补丁/长重试和shell/patch PTY联合28通过、84未选，
8条forkpty警告（58c526，22.79秒）。这批实际修改显示代码，审批/执行权限逻辑未改。

## 补丁正文换行差异（后续修复）

再核对app/event_dispatch.rs::FullScreenApprovalRequest::ApplyPatch：原生将changes
交给DiffSummary，显示逐行补丁而非序列化JSON。patch_approval_pager_top.snap包含
独立行号、+alpha/+beta及颜色。Corki完整详情此前虽无截断，却把patch字符串整体
json.dumps，真实换行成为可见反斜杠n，损害审查可读性。
本批先将原始patch独立为逐行正文，其他工具元数据与完整重试风险仍保留，且不修改
request参数身份。完整DiffSummary的行号/增删颜色仍是显示差异，不能把这一小步
宣称全部视觉一致。终端控制符继续经现有只读pager净化，不直接输出原始控制序列。
已实施request_details对patch_approval复制参数后取出patch，作为独立Patch正文，
不修改原请求。完整正文/风险/参数身份精确比对修复前失败（d01eda）；修复后详情
单位、真实Runtime长补丁/部分写重试和40/100列shell/patch PTY联合27通过、84未选
（d4d24d，22.41秒），8条forkpty多线程弃用警告。静态1176文件/77依赖全部通过
（c552ae）。无审批权限、执行或持久化行为变更；后续继续收敛DiffSummary显示差异。

完整 A–F 复核后，优先补 F3/F6 的实际风险信息缺口，而不是继续扩展已经修复的
Hook 局部组合。原生基准仍为 ddf04ad26789d040f9ef6a96736f76602e35a6cc。

原生 bottom_pane/approval_overlay.rs::try_handle_shortcut 在选择之前响应
open_fullscreen（默认 Ctrl+A），发送 FullScreenApprovalRequest，不提交批准。
app/event_dispatch.rs 对 Exec/ApplyPatch/Permissions/McpElicitation 分别打开完整
静态 pager；pager_overlay.rs::StaticOverlay/PagerView 接收滚动、翻页、首尾，
关闭返回仍未决的审批。app/tests/patch_approval_tests.rs::open_patch 显式断言
打开 pager 不得产生 SubmitThreadOp。ListSelectionView 的截断提示不是全部实现。

Corki TerminalUI.read_elicitation → collect_elicitation 用 display_text 将消息、
patch_retry 和 tool_params 截至12000字符；choose_approval 只有选择/提交/取消，
没有查看全部入口。超长补丁或命令可能隐藏关键尾部，状态为缺失。

实施约束：完整详情只读、转义终端控制符、不进输入历史；复用当前表单输入所有权，
不得再并行启动一个 stdin reader。查看/滚动/关闭详情不提交决定，返回保留高亮；
取消或宿主关闭时恢复表单布局/焦点。原预览限制保留，完整数据按需显示。
需要实际长内容、宽度变化、粘贴/按键不授权及 Runtime/PTY 后续验收。

## 已接入

TerminalUI 的普通决定与执行scope选择均传入按需生成的完整详情；原12k预览不改。
ApprovalDetails 复用现有表单 Application，通过条件布局切换只读页，未创建第二个
reader。消息、完整工具参数、patch_retry证据和URL按请求类型展示；普通工具数值
仍用 dumps_wire，正文不解释为markup或终端控制符。布局按终端宽度缓存折行。

Ctrl+A 打开，方向键/翻页/Home/End浏览；q/Ctrl+C/Esc返回原选择。打开和关闭不
提交决定，pager中数字/y/Enter/括号粘贴均不授权。取消任务finally恢复原容器、
全屏模式及焦点，表单草稿清空；切换屏幕按已有HistoryView重置renderer基线。

交互/表单单位联合 **73 passed，1.18秒**（ffa1c5）。新真实TerminalUI PTY
40/100列×q/Ctrl+C返回 **4 passed，56 deselected，8.50秒**（ee34fb）：
读取26k以上参数的末尾，缩放后回到首部再到尾部；查看和粘贴不产生DECISION，
返回保留“拒绝”高亮、Enter才决定，普通composer及输入历史恢复。

PTY最初按连续字节匹配含下划线/小写字符的画面失败；调试输出（ac5f62）确认
prompt_toolkit保留相同字符并用光标移动跳过，不能将字节缺失当成画面缺失。
改用首尾大写标记避免与旧帧重复字符后通过；不是放宽审批/历史断言。完整CLI与
既有审批PTY联合 **501 passed、2 failed、60 forkpty弃用警告，130.92秒**（8fa294）；
两项失败均为当时尚未修改的100列首部标记字节匹配。修正标记后的四项独立重跑见
上方ee34fb，不将此称为联合全量一次全绿。静态d0605c通过，1173文件格式；
compileall/77包b67e6d通过。

## 后续：真实 Runtime 并发审批与关闭

test_cli_execution_scope 新增 direct/Code Mode × 拒绝/取消/Runtime.aclose 六组合。
实际模型工具调用 → 原生执行审批 → CorkiApplication → InputOwner → TerminalUI
详情页，不是仅调用选择函数。详情打开时实际目录副作用不存在、router仍pending；
数字/y/Enter/粘贴仍不执行。两项拒绝场景分别打开两条独立请求的详情后返回再拒绝；
取消/关闭场景在当前详情或未决审批处结束整个Turn并清空待决请求。

验证唯一终态、采样次数、无session授权、无执行进程/启动任务、Code Mode cell清空、
原表单容器和非全屏状态恢复、modal_depth归零、表单历史/草稿为空。
首轮5通过1失败（d835c1）：Code Mode夹具Promise.all在首拒绝后结束cell并取消
另一审批，因此“再打开第二条详情”的等待不成立。该双拒绝场景改为真实allSettled
cell；其他原有取消测试仍使用all，未改变生产控制流或吞掉取消。

新增场景与全部执行scope/执行取消、详情单位联合 **50 passed，17.79秒**（91ea01）。
之后补强两条审批request_id不重复和模型采样次数断言，专项 **6 passed、28 deselected，
1.71秒**，ruff/格式/diff检查通过（52560c）。
本轮仅补测试，不修改生产执行、授权或关闭逻辑。

## 补丁长详情专项补证

重新读取原生app/tests/patch_approval_tests.rs::open_patch：Ctrl+A只发完整审批请求，
出现SubmitThreadOp立即判错；active_patch_approval_pager_preserves_changes_and_accepts_once
另验证完整changes及静态pager。这是本地审批交互，不涉及官方服务。

Corki新增request_details精确结构比对：14000个Unicode字符的已提交变化证据以及
26000字符补丁参数均完整保留，包含exact=false、permission denied与尾部标记。
详情/elicitation单位联合46通过（86dadd，0.76秒）。真实TerminalUI PTY扩展到
shell/patch × 40/100列 × q/Ctrl+C，检查风险文字、长尾、缩放后首尾可读；浏览中的
y/1/Enter/括号粘贴均不提交，返回仍为未决拒绝高亮，Enter后才decline并恢复composer。
8通过、56未选（6c44d9，17.36秒）。未在该PTY中实际执行补丁，不声称它取代Runtime
补丁审批后端验收；原生逐格snapshot也未由字节标记检查代替。

首次PTY为4失败/4通过（92a37d）：新夹具没有提供真实patch_retry的committed_delta，
在预览阶段KeyError。补全为生产契约后通过；本轮无生产逻辑修改，不冒称发现并修复
了生产缺陷。静态ruff、1175文件格式、compileall、77依赖和diff通过（7974a7）。

## 真实Runtime长补丁接线补证

test_cli_execution_scope把原shell详情测试扩展为shell/patch × direct/Code Mode ×
拒绝/取消/关闭。补丁为超过38k字符的实际apply_patch调用；仍使用原生编译器的
只读/on-request策略、Application/InputOwner/TerminalUI。查看完整路径与尾部，
两种宽度折行后仍保留尾部；浏览按键/粘贴不执行，明确拒绝两条独立审批后可继续，
取消或Runtime关闭则唯一取消终态。文件不存在、无session授权、无pending请求、
无进程/cell、焦点/布局/历史恢复断言均保留。新夹具patch参数默认None不改变旧shell。

首次6通过6超时（79fb51）：测试手动create_content(40/100)改变行数缓存，随后
End和实际终端重绘用不同宽度，等候最后一行条件不成立；不是实际审批未出现。
期间怀疑只读策略不触发审批的解释已由完整堆栈推翻，试改的untrusted策略已撤回。
测试恢复实际输出宽度后12通过（018f4e，3.54秒）；无生产代码修改。
扩大scope/执行取消/补丁审批/补丁重试/详情单位联合104通过、1跳过（1d0ec5，29秒）。
跳过项要求另一个真实batch214旧编译器，当前未提供；不把它算作通过或本批回归。
全项目ruff、1175文件格式、compileall、77依赖及diff通过（b8964d）。
这验证初始长补丁审批，不等于真实部分写入后的长patch_retry证据已全链验证。

## 真实部分写入后的长重试证据

新增test_cli_patch_retry_details，direct/Code Mode × 拒绝/取消四组合：原生sandbox
先写入workspace内超过22k字符的first.txt，随后拒绝workspace外路径；首轮审批由
宿主夹具明确批准一次，第二次新审批才进入真实Application/InputOwner/TerminalUI。
检查实际patch_retry的sandbox_denied、exact=false及完整已写内容，不伪造重试数据。
详情中风险证据部分（不靠重复的工具参数）包含尾部且超过12k；只读按键/粘贴不继续
执行，返回后明确拒绝/取消。已发生的first.txt保持原样，outside始终不存在，唯一
Turn终态、不同审批身份、无session授权/待决请求/cell、布局及输入历史恢复。
四项通过（c6f0a4，1.79秒）。这是实际pipe输入的Runtime桥接，不冒称物理PTY或
完整逐格画面验证；本轮无生产修改。静态1176文件/77依赖全部通过（56bf26）。

另核验此前跳过的旧编译器实际仍在/private/tmp/corki-patch-approval.FRW1tt/
source-bundle-before-215/corki-sandbox，SHA256为
d55114af2529024a835f49a8aefd74c8e1eac24edfd17a5cee5d902f3036300d，与原记录一致。
显式设置CORKI_TEST_PRE_PATCH_APPROVAL_COMPILER后旧契约拒绝测试1通过（df542c，
0.51秒），不再称本地缺失；原批次104+1skip仍保留为当时运行事实。
最终长重试/执行scope/取消/补丁审批/补丁重试/详情单位联合109通过、无跳过
（90326b，29.77秒），明确启用经核对的旧编译器；不累加重叠测试或称为全量A–F。

剩余：尚未用屏幕模拟器逐格验证所有缩放帧；自定义keymap及更广
输入/显示并发组合仍开放。不声称整个F或A–F完成。
