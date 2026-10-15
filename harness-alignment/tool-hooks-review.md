# 普通工具前后 Hook：实施前源码契约与差异

## PostToolUse接入前的执行事实审计

新增post-tool-use-implementation.md，完整追踪registry → handler输出/污染 →
Post选择/事件 → context → 原工具终态 → 模型反馈的顺序，明确Corki当前缺口。
特别确认shell success_for_logging恒true，不应按exit_code==0过滤；终态资格由
process_id/hook_command控制；write_stdin需要原event_call_id。当前不可覆盖的
工具ledger不能用Post反馈二次提交，必须分离执行事实与模型反馈及其恢复。
相关已有输出/参数恢复/身份24通过（8e636d，7.79秒），diff通过、参考干净不变
（82a5bb）。这些是基线，不证明Post已实现；本轮只更新审计与实施要求。

## 当前批次：MCP原始参数的Hook投影

原生handlers/mcp.rs::mcp_hook_tool_input在Rust trim为空时返回{}，其他输入
serde_json解析成功则保留任意JSON Value，失败则保留原字符串；不同于实际RPC
调用的对象参数限制。Corki Pre此前只取call.arguments，导致空输入、非法JSON、
数组与未提前解码的合法对象均丢成None。修复方案在MCP arguments模块分离
hook_arguments与call_arguments，共用现有wire解析器和Rust whitespace定义，
只改变Hook投影，不放宽远程RPC契约、不改原始历史；可信Hook改写仍经原MCP路径。
新增真实Runtime原始参数组合后24失败12通过（79f8f2）；空串/Unicode空白/
非法对象/数组/合法对象/非Rust空白控制符暴露投影差异，JSON null原本已一致。
本批原始文本测试走direct入场，已有nested正常对象路径仍保留，不冒充nested
语法错误输入可由JavaScript原样生成。

修复后首次联合106通过（56109d）。进一步加入不改写分支及direct原始归档
逐项检查：非法JSON/数组/null/控制字符不产生RPC调用且返回工具错误，空白仍
按原RPC语义传None，合法对象照常传递；可信改写才发送新对象。最终72项MCP
组合与通用Pre/已有MCP非法响应联合142通过（c69118，24.04秒）。静态1202文件/
77包通过，参考commit干净不变（915134）。未扩大成真实网络/OAuth验收，剩余
Post、MCP型Hook执行及其他A–E矩阵仍开放，CLI保持暂停。

## 当前批次：MCP Hook名称不使用内部::路由名

原生McpHandler::hook_tool_name经join_tool_name、ensure_mcp_prefix构造本地Hook
名称：规范化namespace尾/leaf头的下划线后用__连接，必要时补mcp__前缀；
pre_tool_use_payload使用此名，with_updated_hook_input重编码参数但不改RPC路由。
Corki MCPTool.with_model_name保存规范化身份，spec.name使用内部::，Pre此前直接
使用call.name导致mcp__docs__write等规则漏匹配（行为不一致）。
方案：由实际MCPTool的已绑定模型身份提供hook_tool_name，仅供本地Hook匹配/载荷；
不改普通兼容模型别名、注册身份或remote_name。内置同名远程工具不套Bash/patch
映射。新增direct/nested × 有/无模型前缀 × write/exec_command八项Runtime+
真实MCPTool+mock client反例，修复前全部仍向RPC发送原参数（7a5874）。此测试
只证明本地Hook至MCP client调用链，不代表真实网络、Manager审批或OAuth验证。

实施后MCP Pre/通用Pre/既有MCP审批与catalog wire联合188通过（46684c，49.64秒）；
静态1202文件/77包与diff通过，参考commit干净不变（10d901）。该联合运行不等于
新增Hook与Manager审批的所有组合已覆盖。MCP非法raw参数的Hook投影、空参数、
特殊规范化边界、Post/async/MCP型Hook本身及冷恢复仍需继续完成，CLI保持暂停。

## 当前批次：shell typed参数解析在Hook之前

原生ExecCommandHandler::pre_tool_use_payload先parse_arguments::<ExecCommandArgs>
成功才产生Bash请求。ExecCommandArgs对cmd/shell/login/tty/yield/max_output/
sandbox_permissions/justification/prefix_rule有typed约束；workdir在独立的
ExecCommandEnvironmentArgs里，不能误将它也提前到此门控。Corki此前仅检查cmd
字符串，其余非法字段也启动用户Hook，再由executor拒绝（行为不一致）。
修复计划：复用实际shell schema的共同字段类型校验，保持原生Option字段的null
准入与workdir后置解析，失败返回原调用给executor生成Observation；不提前对
所有工具全局schema校验，也不让预校验异常吞掉取消或存储错误。
新增tty/login/yield/prefix/权限枚举五类非法参数 × direct/nested，修复前10项
均已错误执行Hook（e6d53e）。不据此声称所有原生未暴露字段/环境解析差异已关闭。

首次修复联合78通过（1b020c）。增加u64上溢/布尔值及workdir/Option null后置
对照后2失败84通过（b1a057）：max_output_tokens超出原生64位usize虽跳过Hook，
却因原executor schema没上界进入process manager。将ExecCommandTool的对应
schema补上U64_MAX，前置门控与executor共享同一约束，避免非法调用产生进程。
workdir和Option null对照保持Hook运行后由普通schema拒绝；这是门控顺序验证，
不宣称普通模型schema的可空字段契约已全面对齐。

最终shell/patch/Pre与builtin/executor联合109通过（da366d，18.16秒），静态
1201文件/77包通过（e08e36）。参考commit仍干净不变（a09775）。本批覆盖实际
Runtime与process入口，不能以此替代全部协议null/未知字段/MCP/Post验收。

## 当前批次：write_stdin不重复执行PreToolUse

原生unified_exec/write_stdin.rs的CoreToolRuntime::pre_tool_use_payload明确
返回None：空写为已有命令轮询、非空写为续写，Bash Pre已在exec启动时运行。
post_tool_use_payload则可在观察到原命令完成时产生其Bash Post，不能把Pre豁免
理解成Post豁免。Corki原先仅豁免CodeModeWaitTool，实际WriteStdinTool也走通用
Pre，导致第二次Hook可阻断已有进程的读取/输入（行为不一致）。
修复方案：按实际WriteStdinTool类型豁免Pre，不按字符串名豁免同名第三方工具，
保留stdin自身schema、终端授权review及结果/进程生命周期。Post仍属未实现差异。
新增真实终端跨Turn direct/nested × 空轮询/非空输入4项：启动时记录唯一Bash
Hook，第二次若错误运行Hook则明确阻断。修复前4项均被第二次Hook阻断（bcc9b9）。

修复后真实终端/Pre shell/原stdin授权联合42通过（ecf068，27.06秒）；非空写
实际产生INPUT:hello，空轮询收取POLL_DONE，日志只有启动时一个Bash载荷。
另增direct/nested同名自定义Probe对照2通过（e6c748），证明按实际类型豁免，
不会让第三方write_stdin跳过自己的Hook。静态1201文件/77包及diff通过。
本批只关闭Pre重复执行差异，不代表终态Bash Post已实现；其他A–E缺口保持开放。

## 当前批次：exec_command的Bash载荷和命令单字段改写

原生unified_exec/exec_command.rs::pre_tool_use_payload解析ExecCommandArgs，
产生Bash及{command:args.cmd}；with_updated_hook_input调用handlers/mod.rs的
updated_hook_command及rewrite_function_string_argument，仅替换cmd、保留其他
原参数，随后走实际执行与审批。Corki当前直接暴露exec_command和完整参数，
Bash matcher不执行（行为不一致）。本批限定实际ExecCommandTool，转换本地
Hook载荷与cmd字段；Hook中的其他字段不能改工作目录/tty或获得权限。
不改普通模型schema，不引入专属模型协议；write_stdin及完整非法原参数门控
仍需独立核对，不能用本批结论覆盖所有shell-like工具。

验收新增direct/code_mode_only × 改写/非法/阻断/拒绝：真实native规则要求
printf审批，process manager入口检查改写后的命令和原workdir/login/tty/yield，
审批载荷检查同一操作，接受才得到新命令输出，拒绝不执行；direct历史保持原样。

修复前8失败（678342），均未选择Bash规则。接入后6通过2失败（b781ae）：
接受场景的shell退出134，夹具误用了仅允许临时目录读写的文件helper profile，
不足以支持普通shell启动。改为既有shell审批测试使用的read-only profile，保留
printf prompt规则，不扩大产品权限或跳过审批。最终shell/patch/Pre/执行审批
联合123通过（087af8，25.67秒）。真实输出证明新命令实际运行，拒绝场景无输出；
process入口检查和审批载荷检查共同覆盖改写及原参数保留。
ruff、1200文件格式、compileall、77包依赖与diff通过（6411dc）；参考commit
干净不变（4ed39d）。未关闭write_stdin、完整非法原参数门控、MCP/Post/async。

## 当前批次：apply_patch普通函数与本地Hook载荷转换

原生ApplyPatchHandler::pre_tool_use_payload产生apply_patch身份、Write/Edit
别名和{command:补丁文本}；with_updated_hook_input通过updated_hook_command
要求字符串command，然后交回正常补丁解析/执行/审批。原生custom传输已被用户
排除，但此本地Hook契约仍属核心行为。Corki实际ApplyPatchTool普通JSON schema
使用patch字段，pre_tool_hooks此前无转换、也不匹配Write/Edit（行为不一致）。
修复方案：仅对实际ApplyPatchTool做双向字段转换和别名匹配，不按同名第三方
handler猜测载荷；改写后继续原executor schema及native patch审批，原历史不改。
非字符串改写返回工具错误，不产生写入、不能当作Hook许可绕过审批。

新增真实native补丁、Runtime direct/code_mode_only × 改写/非法/阻断/拒绝8项：
修复前8失败（d0c936），暴露输入字段不符及别名规则没运行；审批断言阻止夹具
写入原文件。验收要求改写内容进入审批，接受才写新文件，拒绝不写，联合别名
只运行一次。shell专用输入转换及其他Hook阶段仍待核对。

修复后8专项通过（b3c098）。Pre/native patch/delta/retry/API审批联合113通过、
3跳过、14未选（79368f，21.24秒）；3跳过均需独立旧版本helper二进制，14项
按not cli未选，不把暂停CLI或旧版本未执行项记作通过。新增同名自定义Probe
对照确认不套内置patch转换，最终Pre与补丁专项58通过（ce4665，12.73秒）。
测试中的审批回调断言实际native准备后的patch/files，接受写revised.txt，拒绝
不写，非法command不进入审批；direct归档仍为原patch。修正了测试读取结果的
字段名（ToolResultItem.tool_name），未将这个夹具修正算作产品修复。
静态检查1199文件/77包通过（9475f9），参考commit干净不变（d6867f）。

## 当前批次：普通spawn_agent的Hook匹配别名

源码链：tools/registry.rs::function_hook_tool_name对默认域spawn_agent返回
HookToolName::spawn_agent；hook_names.rs保留canonical spawn_agent并增加Agent
匹配别名；registry dispatch调用run_pre_tool_use_hooks，后者分别传canonical
tool_name和matcher_aliases。这是本地Hook契约，不是模型namespace协议。
Corki pre_tool_hooks.bind当前只用call.name匹配，Agent规则不执行（行为不一致）。
修复方案：对平铺普通spawn_agent增加内部匹配候选Agent，仍只选择每条配置一次，
不更改payload、调用身份或归档；不通过别名授予审批。native multi_agent_v1是
内部域映射参照，本批不引入原生namespace，也不猜测第三方平铺前缀。

真实Runtime direct/nested专项使用名为spawn_agent的Probe隔离Hook契约：Agent
规则应改写执行副本但脚本收到spawn_agent；Agent|spawn_agent只执行一次。
修复前2失败2通过（b75f98）。测试不证明实际子Agent创建功能；shell、patch、
MCP载荷映射和其他Hook阶段仍保留为未关闭差异。

实施后Pre 48项、冷恢复96项、损坏反馈契约9项联合153通过（cc10cf，33.57秒）；
ruff、1198文件格式、compileall、77包依赖与diff检查通过（38fa92）。参考commit
仍干净不变（fc8188）。仅改本地选择条件，不改协议、存储格式或Hook信任hash。

## 最新修复：嵌套调用保留宿主身份与能力

固定参考commit不变且干净。原生hook_runtime.rs的
thread_spawn_subagent_hook_context/subagent_hook_context仅对typed ThreadSpawn
提供当前thread的agent_id与agent_type；缺省role为default，显式空串保留。
code_mode/delegate.rs::start_turn_worker保留ExecContext，并用原session及
StepContext构造ToolCallRuntime，而不是创建一个默认根Agent上下文。

Corki发现两处差异：Pre载荷缺少子Agent字段；graph._activate_code_mode重新
构造GraphRunContext，仅传events/realtime/turn_diff，丢失来源、非根标记、
审批回调及其他宿主上下文。内部记忆worker的嵌套工具因此误执行用户Pre Hook，
普通子Agent的嵌套调用也误按根Agent处理。现为typed thread_spawn添加载荷，
并以replace(runtime.context, events=...)仅替换cell事件出口，保留原宿主能力。

test_pre_tool_hooks扩展direct/nested × child_default/child_named/child_empty/
internal/custom_label；真实Hook进程检查身份及自己的转录，内部来源不执行Hook，
伪装来源的custom字符串不被当子Agent。真实handler同时检查非根标记及审批
回调仍存在。最初专项7失败3通过（c669a5）；只补载荷后仍有4失败136通过
（db5dd9），暴露嵌套上下文根因；修复后Pre 44通过（f29e9a）。
加入handler断言后的Pre、冷恢复、全部Code Mode及cell cleanup联合396通过
（f12f9d，73.17秒，原运行handle 87001正常退出）。这证明上下文继承，
不将“回调存在”冒充完整交互审批验证；跨Step审批/资源入场另有专项。

完整Hook映射、Post/async/MCP与剩余恢复故障矩阵仍开放；本修复不引入官方
协议或服务，也不恢复CLI对齐。teach.md未修改。

## 最新验证：Hook反馈与普通本地管理压缩

参考compact.rs本地路径：克隆当前历史构造摘要请求，collect_annotated_user_messages
只收用户消息，build_compacted_history安装用户文本与摘要；developer角色的
HookAdditionalContext不直接当原始用户文本保留。Corki对应window._summarize、
local_retention.retained_user_messages、active_history；不使用远程专属压缩。

新增test_pre_hook_compaction.py：真实可信command及direct/nested工具，短反馈与
超限落盘反馈，手动成功/手动重试耗尽再成功/工具Step后usage触发自动压缩，共12
场景。断言developer反馈原片段与工具结果进入无工具摘要请求；失败不改历史，
成功后模型窗口仅使用摘要而不复活原Hook片段；SQLite原始历史前缀保留。冷重开后
再次直接调用恢复组件检查无追加，再发真实Runtime请求确认无重复反馈、脚本和
工具各一次。此恢复组件检查不是“压缩安装瞬间冷崩溃恢复”的完整证明。

先手动4通过（2b785a）、加入自动8通过（288d28）。新增失败夹具最初假定一次
摘要错误立即终止，得到4失败39通过（28363f）；核对window与原生独立摘要重试
后，改为明确一次重试、连续两次拒绝，第三次显式压缩才成功。另修正ModelUsage
导入路径（f74212）。这些是夹具问题，不冒充产品红绿修复。本批未改生产实现。
最终12专项与手动/自动/输入保留联合43通过（4edf63，6.98秒），ruff、1198文件
格式、compileall、77包依赖和diff通过（37ebd0/ab04ba），参考commit干净不变。

范围限制：预设摘要只证明Harness链路，不证明真实模型摘要事实无损；此专项未
直接检验双HTTP传输（已有普通压缩传输专项独立）。压缩安装故障窗口、跨工具
恢复顺序、恢复追加自身中断、后续Hook类型仍开放。CLI保持暂停、teach.md未改。

## 最新补充：双Hook部分完成、逆序落盘与损坏契约拒绝

在真实冷Runtime矩阵新增partial/reversed两窗口：两个可信command，强制配置
第二项先claim/提交，第一项随后claim，分别保留未知结果或完成结果，再中断。
从数据库读取确认行顺序为1→0，partial中第一项结果确实为None。恢复仍按保存的
配置顺序0→1注入，partial仅注入已知第二项；各脚本一次，handler零次，原事实
逐行不变。覆盖direct/nested、配置保留/删除、0/20预算和新/旧日志，新增32通过
（3a2078）；增加实际存储逆序断言后再验32通过（dcc6d3，7.90秒）。

损坏契约测试使用真实SQLite读写、两项反馈中一项有效一项无效，要求任何损坏
不得造成部分上下文归档，也不得改写Hook事实。发现missing_owner与duplicate_order
未拒绝（7af869：2失败7通过）；已要求v1显式包含source_input_id，且待恢复的同
调用顺序编号唯一。其它覆盖bool冒充version/order/limit、错误输入/事件/turn/call。
该部分是存储/恢复组件故障注入，不冒称全部来自真实子进程或完整Runtime。

修复后契约9项+完整冷Runtime矩阵96项共105通过（60a86b，22.66秒）；静态ruff、
1197文件格式、compileall、77包依赖、diff通过（d89a1b/08ed37）。参考commit干净
未变，CLI/teach.md未修改。仍开放跨多个工具调用的顺序、恢复追加自身中断、压缩
生命周期和后续Hook类型；不把本批通过视为整个核心Harness完成。

## 最新修复：从已提交Hook事实补回纯反馈

前节确认的completed窗口现已补齐（新格式）：Pre execution request在命令claim前
保存feedback v1的配置顺序、预算和准入输入id；此元数据仅用于本地日志，不加入
Hook stdin或模型协议。新增repository.load_hook_executions只读固定thread/turn
前缀事实；Runtime在确认无持久取消标记后、恢复图执行前调用pre_hook_recovery。
无需进入当前工具目录，也不运行command/handler，因此丢失的Code Mode父cell
不会妨碍恢复已提交的嵌套Hook纯反馈。

恢复只解析已提交有效结果，校验事件/turn/call-key关系、预算/顺序版本与原输入
身份，按原配置顺序准备并批量归档。已归档确定性id直接保留；unknown不推测结果。
工具claim的unknown/已完成缓存语义不变，恢复不应用参数改写或重新授权执行。
当前Hook配置被删除也不修改原事实或原预算。旧格式缺失feedback元数据时，若已有
上下文则保留；若需补回则发明确WarningEvent，不用当前配置猜测预算/来源。

提升completed窗口反馈期待后，修复前1失败1通过（21d6eb），修复后基础38通过
（85c519）。新增真实冷Runtime矩阵：4窗口 × 配置保留/删除 × 0/20预算 ×
direct/nested × 新/旧日志格式，共64通过（d6410d，16.02秒）。同时断言脚本一次、
handler不重复、原历史前缀与Hook执行表逐行不变、输入不重复、反馈原输入归属，
再次resume为空。旧格式通过模拟旧writer在首次claim前省略元数据生成，不修改
已提交事实制造兼容性。Pre/上下文/Stop及通用恢复、模型设置、工具身份/参数恢复
联合156通过（debd54，37.27秒）。静态ruff、1196文件格式、compileall、77包依赖、
diff通过（b9bcf2）；参考固定commit干净未变。

仍需验证：多Hook/多工具混合部分完成时的顺序与故障组合、反馈恢复自身中断、
压缩保留与召回、损坏元数据的完整拒绝矩阵；子agent、canonical、async/MCP/Post
仍开放。下方“completed反馈不会补回”为已修复前快照；不将此补丁宣称完整恢复
或整个Harness完成。CLI对齐暂停，teach.md与用户凭据未改动。

## 最新实施与恢复审计：完成事件先于反馈归档

原生core/hook_runtime.rs::run_pre_tool_use_hooks明确先emit_hook_completed_events，
再record_additional_contexts。Corki此前在每个完成事件前逐条归档，时序不一致。
现在prepare_context只准备片段，Pre聚合发完完成事件后批量append，再执行工具
或返回block。真实direct/nested时序断言修复前10失败（b5b85f），修复后相关39
通过（2d0400）。已去掉仅测试引用的旧record_context包装，单位测试直接覆盖准备
与真实repository append边界，仍验重复取消join与冷读取去重。

新增test_pre_hook_recovery.py：真实command/工具/主图同步checkpoint，在Hook结果
未提交、已提交、反馈已归档、工具结果已提交四窗口停止，关闭warm后新建Runtime
及registry并resume_pending。脚本始终一次，未确认工具不执行；已完成handler一次
并复用结果；原历史前缀、Hook execution事实保留；输入不重复；再次resume为空。
初次测试误复用sealed registry导致4个夹具失败（ea561d），改为独立registry后
4项通过（3aca1a），不将夹具失败冒充产品缺陷。联合Pre、上下文与Stop冷恢复
103项通过（773810，20.64秒）；后续又增加Hook事实逐行不变断言单独重验。

**确认仍有恢复缺口**：completed窗口中已提交Hook原始输出包含additionalContext，
但未归档反馈不会在冷恢复补回。当前工具claim缓存返回unknown，完全跳过Pre，
这是已验证的不重放边界，不是“反馈恢复完成”的证据。后续须从不可变Hook事实
恢复纯反馈，不能借机重跑command/handler，也不能用当前配置覆盖当次准入契约。
本测试记录当前保守边界；修复时应将completed窗口反馈期待提升为保留一份。
压缩、子agent载荷、canonical映射、async/MCP/Post等仍开放，CLI继续暂停。

## 最新实施：additionalContext 进入真实历史与后续请求

核对 core/context/hook_additional_context.rs：role 明确为 developer，content_kind
为 hooks.additional_context，无额外包裹。已新增 core/hook_context.py 并接入同步
Pre结果聚合：有效输出按配置顺序归档，block仍保留反馈，非法控制/类型不注入。
ContextItem使用确定性id，绑定已准入request_items中的用户输入，不把执行期间
新到输入当来源；已归档反馈冷读取复用，不重复落盘或追加。

StopCommand新增有默认值的additional_context_limit，Pre从可信配置实际读取，
既有Stop快照可缺省该字段。默认2500四字节估算tokens；显式0不截断（与参考一致）。
超限使用已有UTF-8首尾截断规则并预算路径footer，完整输出以独占0600临时文件保存，
不覆盖用户文件。与原生一样，截断提示/极小预算下的footer有额外开销，不能把该
数值当完整请求的硬token上限；原始命令输出仍受run_command的读取上限约束。
文件写入OSError只退为截断，不伪造恢复路径。文件任务在重复取消后仍join；
后到worker异常不得覆盖取消，取消后不追加上下文。

真实Runtime direct/nested新增短反馈、block、非法控制/类型、超限、落盘失败及零
预算，Pre整组34通过（ecb9e7）。联合Stop身份与冷恢复/待处理输入189通过
（30afe3，33.52秒）；本地冷仓库复用/输入归属/重复取消5项加Pre最终39通过
（3c8385，7.26秒）。取消叠加worker错误先暴露1失败4通过（ec4885），修复后通过；
其余新增上下文场景未执行修复前版本，不虚构红绿证据。静态ruff、1194文件格式、
compileall、77包依赖、diff通过；参考commit干净未变（ae92a9）。

仍开放：完整Runtime冷恢复故障窗口、压缩后的保留与重新召回、并发多工具上下文
顺序与生命周期、HookCompleted和持久化的精确事件时序、临时完整输出的长期清理。
子agent载荷、canonical映射、自由文本、async/MCP/Post Hook亦未因此完成。
以下“additionalContext未回灌/预算尚未消费”为历史快照，由本节覆盖。

## 最新补充：systemMessage 诊断与模型上下文隔离

原生 hooks/events/pre_tool_use.rs 在退出0且JSON解析成功时，将 systemMessage
放入 HookCompleted 的 warning entry；即使控制字段不支持也保留警告。它不是
模型 system 指令。Corki 原先忽略此字段，也错误接受非字符串字段携带的改写。
现在解析并限长4000字符，警告与控制错误分别输出；非字符串使输出无效、保持
原始工具参数，不因诊断警告阻止合法改写，不向模型历史注入警告。

真实 command 的 direct/nested × 有效警告/警告与非法控制/非法警告类型，修复前
6项失败（001db5），修复后Pre整组20通过（220711）。联合Stop与完整tools单位
测试956通过（02db52，15.43秒）。静态检查、1192文件格式、compileall、77包依赖
及diff通过（f86cd8）；固定参考commit干净未变。CLI、teach.md及凭据未修改。

additionalContext仍开放：已追踪common::append_additional_context按配置顺序汇总，
output_spill默认2500估算tokens，超限保存完整文件并为路径footer预留预算，写文件
失败退为截断；显式0在原生中表示不限预算。core/hook_runtime.rs再经
HookAdditionalContext/ContextualUserFragment写入会话。后续须核对具体role、来源、
幂等归档与压缩保留规则，不按局部变量developer_messages的名字猜测role。

## 最新补充：PreToolUse 本地转录路径

参考 core/hook_runtime.rs::run_pre_tool_use_hooks →
session/mod.rs::hook_transcript_path，前置事件应获取当前线程的本地历史路径。
Corki 原先固定传 None，依赖路径的真实 command 因此失败，改写失效：
direct/nested 改写测试修复前均失败（aaad3b，2 failed）。现复用 repository 的
materialize_transcript，在可信同步 Hook 执行前发布本地 JSONL；不访问远程历史。
无匹配同步 Hook 不触发该步骤；文件系统 OSError 记录日志并传 None，仍执行
Hook，不吞取消或其它存储异常。已有工具与 Hook execution claim 边界不变。

实际子进程读取转录并确认包含当前用户输入；direct/nested 同时覆盖正常路径与
OSError 降级。Pre 14项通过（15fb0b）；联合 Stop、本地存储转录、memory transcript
Runtime 共51项通过（c0f150，9.33秒）。ruff/1192文件格式检查通过（eb5950），
compileall、77包依赖与 diff 检查通过；参考仓库干净、commit 不变。
尚未覆盖子agent完整载荷与冷恢复路径变化组合；additionalContext/systemMessage
及其预算、spill 仍未实现，不能据此关闭完整 Hook 上下文契约。

## 最新实施：同步 command Pre基础链已接入，完整契约仍开放

新增core/pre_tool_hooks.py，graph在tool claim前捕获可信目录，普通/Code Mode嵌套
通过ToolContext.before_tool进入同一ToolExecutor准入。配置身份与来源复用既有
Stop机制，新增PreToolUse独立event/matcher/hash，不授权未信任或修改后的脚本。
Runtime配置重载沿既有原子Hook snapshot发布路径；internal非thread-spawn worker
不运行用户Hook。CodeModeWaitTool按实际handler类型跳过，自由文本工具暂未接入。

已实现同步command的match、并发执行、exit2/有效block阻断、allow+updatedInput
执行副本改写、错误诊断和HookStarted/Completed。原始ToolCall不变，改写后仍经过
普通schema或handler parser及已有执行权限流程；不是Hook批准即跳过工具审批。
执行错误/非法Hook输出fail-open只记录Hook失败；Hook存储异常不被ToolExecutor
转成普通成功结果。每个Hook有独立execution claim/result，未知结果不重跑。
竞争改写顺序在进程结果返回时记录，不用随后数据库提交的完成顺序代替。

实际Runtime direct/nested × 阻断/改写/非法schema/非法输出/未信任/竞争改写共12
组合通过（c74f4d，2.70秒），包括反转落盘次序、handler零非法副作用、原调用归档
不改写。工具单位全目录加此集成927通过（55035f，10.22秒）。此前Stop身份/来源/
控制/恢复、Executor与Code Mode取消/错误联合166通过（4b33be，26.07秒），采集早于
最后增加竞争改写对照，不混作单次全仓。未运行修复前新测试，不虚构先红后绿。
静态a0a474通过：ruff、1192文件格式、compileall、77包依赖、diff；参考commit干净
且不变。所有本批进程已退出。CLI/teach.md与用户凭据均未修改。

**未完成**：additionalContext的完整恢复/压缩/并发契约（基础回灌与预算/spill见最新实施），
子agent载荷（本地transcript_path实物已由最新补充实现）、shell/patch/MCP canonical名与
alias/输入映射、自由文本工具、async非控制反馈、MCP Hook、PostToolUse及完整冷恢复
故障组合。async Pre目前明确警告不执行；不能把此基础链宣称为完整Pre契约已对齐。
additionalContextLimit基础消费已由最新实施接入，完整上下文生命周期仍待验证。
下文“尚未接Pre”属于实施前快照，由本节覆盖；其它未完成范围不变。

状态：缺失，核心工具行为差异，非官方服务功能。仅接收插件元数据不算实现。
本文件接续code-mode-acceptance.md的发现，不把Stop/SubagentStop已有能力重新列为缺失。
参考固定Codex commit ddf04ad26789d040f9ef6a96736f76602e35a6cc。

## 已追踪的原生行为

1. core/tools/registry.rs：工具身份/载荷kind校验之后、handler之前获取
   pre_tool_use_payload并run_pre_tool_use_hooks；阻断返回RespondToModel，未执行handler。
   updated_input必须经工具自己的with_updated_hook_input转换，再进入正常handler。
   默认Function重编码JSON；shell/patch/MCP等的专用输入映射必须继续逐工具核对。
2. hooks/events/pre_tool_use.rs：匹配canonical名及aliases，并发执行同步Hook；结果事件
   按配置顺序，多个updatedInput则按**实际最后完成**选择。任一有效block压过全部改写。
   不是串行把上一个Hook改写后的输入传给下一个Hook；它们读取同一请求快照。
3. engine/output_parser.rs：Pre接受deny+非空reason，或旧decision:block+reason。
   permissionDecision:allow仅在带updatedInput时表示改写，不代表审批授权；单独allow、
   ask、approve、continue:false、stopReason、suppressOutput均为不支持的输出。
   无效输出记录Hook失败但不直接阻断工具；不能自行将它们解释成权限提升。
4. pre/post events：exit2仅在同步Hook写出非空stderr时产生block；exit2无理由、其他
   非零、进程故障/非法JSON记录failed；空stdout或非JSON普通文本不自动注入模型。
   additionalContext独立收集并有输出spill限制；不能直接无界拼接stdout。
5. registry.rs：只有工具结果success时才构造PostToolUse。post_tool_use.rs支持结果block
   及continue:false反馈；后者是反馈替换而非直接终止整个Turn。工具失败无Post。
   Post阻断的是已完成工具的结果，不撤销副作用。普通模型反馈替换与Code Mode原始
   结构化值分开：PostToolUseFeedbackOutput的code_mode_result仍返回original值。
6. tools/code_mode/wait_handler.rs明确不产生Pre/Post载荷；普通嵌套工具走同一registry
   逻辑，不能按call.name==wait误跳过一个同名外部工具，应按受信任handler能力判定。
7. engine/mod.rs：只有同步Hook可控制；async命令和executor-scoped Hook不能迟到阻断或
   改写已执行工具。dispatcher使用FuturesUnordered记录completion_order，最终按配置序
   报告；命令/MCP两种同步执行入口与异步命令不同，不能把MCP Hook当官方平台依赖。
8. engine/discovery.rs：enabled/trusted_hash由来源策略决定；信任hash绑定event、matcher
   和规范handler配置。官方内置清理白名单/平台注入不属于Corki移植范围；普通配置文件
   与插件的用户授权不能因元数据有效就自动获得执行权。

## Corki 当前链路与影响

- plugins/agent_hooks.py可反序列化PreToolUse/PostToolUse等事件，但plugins/hooks.py只
  保存定义；core/stop_hooks.py::prepare只构造Stop/SubagentStop快照。
- command_identity目前只接受这两个事件，StopCommand不保留additionalContextLimit；
  不可直接把event字符串换掉并复用Stop的outcome解释器。
- graph._execute_bound_tool先claim_tool_call，然后ToolExecutor.execute，再完成ledger及
  发事件。缓存completed/unknown目前都绕过handler。普通和Code Mode嵌套共享这条链。
- ToolExecutor没有Pre/Post接口，宿主可信Pre拒绝、输入改写和Post反馈均不会执行。
  影响不只是观察通知：有意配置的副作用前阻断策略不能生效。
- run_command已有超时、1MiB输出限制、进程组取消join；AsyncHooks和Hook事件、来源
  信任、转录文件、hook batch/execution存储可复用，但不能复制Stop轮级控制语义。

## 实施切分与必须保持的不变量

第一批应完成**真实普通工具的同步command Pre链**，不是只增加解析器：通用事件配置/
身份与matcher → Step捕获可信Hook目录 → 同步并发执行 → 完成顺序改写 → schema/工具
映射再校验 → 原有审批/handler。既有Stop行为不改；默认无可信Hook不执行任何命令。
接入前需核对shell/patch/MCP载荷转换及配置feature门控；不能借此默认运行以前未执行
的项目/插件脚本。对于未获用户hash授权的定义只报告未授权。

第二批接Post及恢复：原始工具完成事实必须先可靠保存，Post状态/结果单独绑定原call
与配置快照。Post期间取消/故障后恢复只补必要Hook/反馈，不重新执行已成功工具；未知
Hook自身副作用同样不自动重放。既有ledger历史保持兼容，模型原始ToolCall不改写。
完成结果回放不应因为当前Hook配置变更而重跑已完成操作。

后续扩展同步MCP Hook、async非控制反馈、context预算/spill与多来源重载；它们是未完成
核心项，不因为第一批先做command而缩小最终目标。官方executor平台分支不移植。

验收至少覆盖：direct与Code Mode嵌套；可信/未信任/修改后失效；匹配与alias；两Hook
反向完成顺序；block零handler效果；非法改写零handler效果；改写后仍审批；wait控制
豁免；Post仅成功触发；Post block保留副作用事实；普通反馈与JS值区别；前后阶段取消、
并发、冷恢复、结果提交故障；重载不得改动已入场Step的执行身份。

以上为源码审计和可实施差异，不是功能已完成的声明。当前生产仍未接Pre/Post。

可复用机制的当前基线：matcher、Stop进程/来源/身份、Hook batch与实际Stop冷恢复
六文件111通过（a2bde2，14.64秒，82785退出0）。这些测试只证明已有机制，不证明
Pre/Post可用；本批未新增执行权限或改变生产逻辑。diff检查及参考干净commit
再次核实（e07649）。
