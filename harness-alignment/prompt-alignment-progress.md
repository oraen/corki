# Harness 提示词对齐审计

八项清单见 `prompt-alignment-goal.md`，尚未完成。

参考Codex提交：`ddf04ad26789d040f9ef6a96736f76602e35a6cc`；
Corki起始提交：`a4ae06b2db153cd39e4619e8de586dff4fb4b50b`。

## 第一轮：定位实际基础指令路径

Codex不只有一个base模板：`models-manager/src/model_info.rs::with_config_overrides`
处理显式base覆盖、模型元数据模板和personality；未知模型fallback使用同目录的
`prompt.md`。`core/src/context/base_instructions.rs::BaseInstructionsFragment`
为developer独立消息。不能混合models.json内各模型的互斥指令来构造“统一Codex模板”。

Corki从`context/builder.py`的settings/model_info选择、base_instructions与session
持久化出发，经graph的turn_base_instructions与models适配器进入实际请求；
`prompts/agent/base.md`只是其中一个fallback。继续核对model catalog来源和启用条件，
尚未修改base模板。`prompts/README.md`存在过期入口和已实现扩展仍写reserved的问题，
待请求链路确认后更新，不以文档宣称代替实现证据。

## 第8项首个差异：Default模式规则过度简化

先核对Codex `collaboration-mode-templates/templates/default.md` 全文，
`core/src/context/world_state/collaboration_mode.rs::from_collaboration_mode/render_diff`
及 `collaboration_mode_tests.rs` 的模式变更/清除/单次注入/catalog选择用例。
Codex以developer的collaboration_mode标记提供模式指令，并明确旧模式失效、
用户文字不改变模式、可选提问/无答案继续及提问工具不能用于审批。

Corki `prompts/modes/default.md`原先只有两段概括，缺失上述规则；现恢复参考模板
相同段落和措辞，唯一适配是available条件还要求工具描述允许当前模式。
依据 `tools/builtin/user_input.py::spec/execute`：默认开关关闭时即使工具被列出，
实际执行仍拒绝Default；不能仅照搬“在列表里就能用”。不改变工具可用性或审批实现。

实际链路：ContextBuilder.build生成developer贡献 → world_state.render_update
包collaboration_mode → runtime请求；现有持久化diff保留旧历史并只追加一次新模式。
在真实runtime的Plan→Default、direct/Code Mode、跨工具Step请求测试补完整规则/
角色/标记/不重复断言。尚未据此勾选第8项：Plan、子代理及其他生命周期仍待审计。

模式及基础指令生命周期定向35 passed（4 workers，4.18秒）。另逐字diff确认现有
`modes/plan.md`等同Codex `collaboration-mode-templates/templates/plan.md`；这只证明
静态文本，不能据此宣称Plan全生命周期已验收。

## 第3项首个修复：本地压缩模板与自定义值边界

参考Codex `prompts/templates/compact/prompt.md`、`summary_prefix.md`，
`core/src/compact.rs::run_inline_auto_compact_task`的as_deref().unwrap_or规则、
UserInput::Text注入及`core/tests/suite/compact.rs::manual_compact_uses_custom_prompt`。
Corki `context/window.py::_summarize`已保留base、追加user压缩请求且tools=空；
handoff prefix文本也已相同。默认compact文本却被重写，现恢复参考原文。

另外构造器原(compact_prompt or "").strip() or None会改变自定义指令字节，
把显式空字符串当未配置。改为保留原值，仅None选默认，与参考Option<String>一致。
新增None/空串/纯空白/有首尾空白四种请求捕获，验证user角色、正文、base不变、
旧history保留和无tools；不改供应商或恢复远程压缩。

首轮上下文/模板+模式/base生命周期/managed恢复联合487 passed、2 failed；
两项旧测试匹配小写checkpoint compaction，因模板恢复Codex大写原文而失败，
同步更新明确字面断言，不改原overflow删最旧pair/保留原始历史断言。继续复跑。

复跑上下文/模板/模式/base生命周期/managed恢复489 passed，15.32秒。
进一步压缩wire/输入/手动/冷恢复/runtime/HTTP重试初测35 failed、195 passed：
34项由测试fixture旧小写短语识别造成，1项自定义prompt测试仍期望去首尾空白；
全仓定位相关fixture并同步为新模板标识，后者断言改为实际配置的精确含空白值。
该组最终230 passed，8.76秒。没有放宽历史保留、安装原子性或恢复不重采样断言。

扩大到工具搜索/技能/环境更新/CLI压缩PTY的一组初测172 passed、1 failed。
失败是固定token阈值测试受Default模板长度增长影响，在首次工具读取前就压缩；
提高测试阈值又会错过第二次压缩，因此不采用阈值猜测或改生产策略。最终方案固定
该fixture的collaboration_instructions为原先的短文本，让测试专注原有1/2次压缩
和工具重发现的行为，不随默认策略文本长度漂移；生产仍使用新的完整模式模板。
该扩大批次最终173 passed、12条既有forkpty警告，14.99秒；ruff及diff检查通过。

## 后续审计入口（不计完成）

- 基础指令：已确认bundled_models主要投影容量/权限元数据，不自带所有模型base；
  本地显式catalog可提供base_instructions/instruction_template，未知模型走agent/base。
  需继续完成通用fallback逐段对照与实际wire/新会话/旧会话边界验证。
- Skills：已定位ext/skills/src/catalog_prompt.rs的HostAliases/ResourceAliases/
  Unaliased三个分支和Corki skills/catalog.py。应选本地filesystem分支，不能复制
  不存在的skills.read executor/orchestrator接口。完整读取及生命周期仍待核对。
- 其他六项尚未逐项审计完成。所有清单继续未勾选，不以这些专项替代整个目标。

## 第5项：本地技能指引与虚构资源入口修复

上轮为有效进展：修改实际模板/选择逻辑并完成请求与恢复测试。本轮重新核对工作树
和清单，无未完成进程需要等待；继续此前未完成的Skills审计。

源码依据Codex `ext/skills/src/catalog_prompt.rs`的HOST_ALIASES全文、
`fragments.rs::AvailableSkillsInstructions/SkillInstructions`及
`extension_tests.rs::empty_catalog_records_zero_metrics_without_a_fragment`。
catalog是developer，已选技能是user；HostAliases提供文件读法，不要求执行环境包。

Corki原catalog.md是八条概括，丢失纯文本提名触发、主agent亲自读完整指令/引用、
不得委托解读、脚本/资产复用、多技能顺序/使用公告、上下文卫生，并自行允许未完
任务跨turn继续激活。现恢复Codex相同规则、段落结构和通用措辞；只适配实际存在的
skill_read(name,file)、skill_dir，以及已加载body/运行中提名/压缩后的读取条件。

绝对路径catalog介绍原样带了executor/orchestrator/custom package，但Corki实际
只产生本地file路径，不具备对应skills.read接口。删除虚构来源宣称，改为本地绝对
路径介绍；短路径alias介绍保留。没有新增远程技能能力或改写用户技能文件。

请求链路：SkillContextContributor.contributions经WORLD_STATE注入developer目录；
input_contributions_with_mentions在输入接纳时读取完整body，以独立user消息绑定
源路径；不会在每个Step刷新旧body。加强runtime断言catalog规则实际进入请求，
selected body仍为user独立消息、不升级权限。保留已有memory技能按路径读取修复。

首轮169 passed、1 failed：绝对路径介绍缩短后，两技能fixture不再受益于alias，
导致“alias按发现顺序”测试误判。改为每个根两技能，确保alias真正节省空间，保持
根序/显示序原断言，不更改生产预算或选择算法。复跑170 passed，3.76秒。
继续更广Skills回归；这一批不足以勾选第5项全部完成（wire适配/资源限制等仍需复核）。

扩大Skills回归最终283 passed，16.39秒，4 workers，退出0：全部技能单元、所有
test_skill*集成、host选择、plugin来源及memory路径路由。包含新增selected body
user角色/独立消息断言。ruff check/format和diff检查通过；无外部模型调用。

## 第7项：中断提示及文本steering的分支核对

上轮修改模板并取得283项回归证据，属于有效进展。本轮核对当前工作树后继续。
参考Codex `core/src/context/turn_aborted.rs`全文和`tasks/mod.rs`中的
interrupted_turn_history_marker：普通分支是user消息，V2为不同措辞的developer；
不应为了文字一致擅自切到未实现的V2分支。
Corki `context/interruptions.py`正文已逐字匹配普通分支，runtime只在interrupted
取消且agent_interrupt_message_enabled时追加，ID去重；chat/responses适配均为user。
无需重写正确生产逻辑，新增全文回归并加强实际MockTransport请求中的完整正文/
role/后续输入顺序。测试地址改为provider.invalid，仍为本地mock而非真实网络。
中断历史、恢复、终态保留及Plan完整性共68 passed，5.54秒，4 workers。

核对Codex `prompts/templates/realtime/realtime_start.md`、realtime_end.md后确认
其是语音/中间代理backend executor语境，不等于Corki的实时文字steering。
因此保留Corki的live text channel、sampling boundary、旧响应不被强制取消及
结束后回归普通turn的真实能力说明，不复制“用户不直接与你说话/语音识别错误”。
`RealtimeContextContributor`以developer注入，world_state._removal显式结束；
已有runtime测试验证冷恢复不重复结束、重新进入live及压缩/steer提交边界。
补实际请求developer角色、非取消行为及无虚构中间代理/语音识别声明断言。
本项仍有工具错误、排队和收尾等范围待核对，不据这些专项勾选整个第7项。
新增断言后steering/压缩提交边界9 passed，2.22秒；ruff和diff检查通过。

## 第4项：生成/整理模板初审与实际存储契约修正

上轮中断/steering请求验证属于有效进展。本轮核对当前工作树后开始Memory生成路径。
Codex参考：`memories/write/templates/memories/{stage_one_system,stage_one_input,
consolidation}.md`，`memories/write/src/prompts.rs::build_stage_one_input_message/
build_consolidation_prompt`。source输入按有效窗口70%保留头尾，扩展输入仅在目录
存在时绑定；与读取链路的summary developer context不同，不把生成模板用于主agent。

逐文件diff确认Corki stage_one_system全文与参考相同。stage_one_input保留JSON三字段
任务说明与不执行历史指令约束，必要差异为真实thread_id/SQLite渲染和历史包裹标记，
不恢复不存在的rollout_path/JSONL。pipeline._extract先过滤/裁剪再作为content送入
_sample_json，system模板作为instructions；已有实际HTTP生成测试覆盖完整模板hash、
无schema/schema、有效/空/畸形/超长/取消、敏感信息与冷读取。

整理模板原880行保留参考主体、忘记已删除输入等规则，仅有路径语法与存储适配。
发现两处不符合Corki实际实现的说明并修复：
- memory_summary不应声称Always loaded into system，而是memory use开启且摘要非空
  时作为developer context。与MemoryContextContributor及read_policy开关一致。
- common模板不应固定称private snapshot：agent.py根据prepared.shared分别使用
  shared root或private sampled workspace。改为中性memory workspace，让追加的
  runtime_shared/runtime模板分别明确立即写入不可回滚和检查来源后发布的不同语义。

保持全部其他规则原样；不修改用户memory、生成器行为或共享/私有默认策略。
更新整份模板hash，并新增两分支实际构建指令的互斥断言和runtime请求正文断言。
第一次（仅summary说明修改）相关59 passed，7.68秒。工作区修复后重跑确认
61 passed，7.35秒，4 workers（此前session句柄已失效，未以缺失结果算通过）。
本项未完成：读取/引用/更新/遗忘的完整源码及生命周期还需逐项完成审计。

## 第1项：恢复通用基础规则及实际HTTP选择边界

继续逐段阅读Codex `models-manager/prompt.md`全文，`src/model_info.rs`的
model_info_from_slug/local_model_messages_for_slug/with_config_overrides。
参考测试 `core/tests/suite/remote_models.rs::unknown_model_sends_builtin_instructions`
和disabled_update_plan_preserves_custom_catalog_instructions，及model_switching.rs
模型切换追加developer指令测试、model_info_tests.rs禁用personality回归。
选择普通未知模型fallback，不混合官方模型catalog实验模板。

Corki原base仅约5KB的概括，丢失大量规划正反例、验证策略、更新规则、精确修改边界
及最终答复规范。现恢复Codex通用原文，保留必要适配：
- Corki身份，删除官方产品归属说明；能力/工具/升级审批均以当前请求实际暴露为准。
- AGENTS可能已作为user context提供，不虚称始终作为developer提供。
- apply_patch示例改为真实JSON patch参数，删除Codex command数组示例。
- update_plan存在且当前模式允许时使用；不在Plan模式调用TODO工具。
- Corki终端未实现裸文件路径自动链接，不承诺路径可点击。
- 保留用户未相关修改、精确破坏性操作范围和禁止泄露敏感信息规则。
其他通用正文/结构/规则强度尽量直接沿用，移除参考文本一处尾随空格。

实际路径：ContextBuilder.with_instruction_settings/with_session_base选择并保存
fallback或catalog，自定义base包括空串优先；graph冻结当前turn，旧会话保留原base，
不强行改写历史。chat映射首条system，responses映射instructions；模型切换提示
另走developer增量，不把普通历史提权。既有生命周期测试覆盖custom、切换及fork。

新增test_base_prompt_wire.py：整个适配模板hash及关键规则断言，2种协议×4种来源
（fallback/catalog/显式覆盖/空覆盖），经真实runtime和MockTransport验证普通执行、
手动压缩、压缩后继续、SQLite冷恢复共4次请求，base完整/位置正确/不重复、不泄露
测试凭据。无真实网络。初测489项中488 passed，1项固定12k预算fixture因新base
增大提前压缩；将该fixture的base固定为约5KB测试文本，不改生产预算或原断言。
复跑及扩大回归中，第1项暂不勾选。

上述核心批次复跑489 passed，15.40秒。扩大到全部compact集成最初72 failed /
616 passed；新的完整base约21KB，原测试固定8k窗口/3k阈值不再能代表预期阶段。
为七个相关fixture固定原约5KB基础预算，不缩短生产指令、不调高生产窗口，原工具
执行次数、失败、恢复、取消、SessionStart及history安装断言保留。
第二批24 failed / 673 passed，进一步定位两种fixture副作用：
- 手工构造的legacy PreCompact检查点不支持后来补入custom base。仅automatic
  分支固定base，manual legacy分支继续使用默认来源，不改变恢复安全校验。
- SessionStart的下一普通turn受完整Default模式长度影响又压缩；此时序fixture
  同时固定模式文本。模式内容由独立实际请求测试覆盖。
这两个文件最终149 passed，8.70秒；全组及personality、memory扩展回归继续中。

补充来源边界：Codex `core/src/client.rs::build_responses_request`明确只有
use_responses_lite分支将base作developer前缀；普通分支仍放instructions。
因此不能把BaseInstructionsFragment的角色错误地套用到所有普通供应商。
`session/mod.rs`明确配置覆盖 > 历史base > 当前模型模板，Corki保留相同优先级。
参考get_prompt_base_instructions在禁用update_plan时仅裁掉内置模型相关段落、
不改自定义catalog；Corki无同等update_plan_enabled全局开关，使用真实工具列表
和模式约束的条件语义，不为复制这项裁剪新增配置/功能。

扩大回归最终846 passed，58.74秒，4 workers、退出0：全部compact集成，加模式、
thread settings、skill catalog、base override/recovery/wire、personality全组、
本地instruction template与memory生成/整理/读取/技能入口。新增请求测试以解码
content正文检查重复，不用转义JSON字符串做易漏检的子串比较。ruff check、format
和git diff --check均通过。此前489项核心批次与此批次有重叠，不宣称两者之和为
独立覆盖数。未运行真实供应商；所有模型调用均本地fake/MockTransport。

第1项勾选完成，其他七项保持未完成。补prompts/README.md基础选择/持久化/协议
位置说明；其他旧目录说明待其对应项审计后更新。完整base增大初始上下文开销是
保留完整参考规则的代价，不静默截断关键指令；极小模型窗口仍执行原预算拒绝机制。

## 第2/8项：规划、提问及通用发现的工具参数指引

上轮有模板修复、实际请求测试及第1项验收，是有效进展。本轮重查工作树后继续。
参考Codex `core/src/tools/handlers/plan_spec.rs`、plan.rs、spec_plan_tests.rs：
update_plan原始description为三句，四处字段有说明；返回Plan updated，Code Mode
结果为{}，Plan模式拒绝TODO工具。Corki执行契约已一致，但说明被缩写且字段说明
缺失。恢复原文description及step/status/plan/explanation说明，不改变接受的数据。

参考request_user_input_spec.rs及对应spec_tests，核对Corki user_input.py和
protocol/user_input.py：选项必须非空、默认Other为真，模式开关控制可用性。
恢复参数原文（impact/tradeoff、snake_case映射、UI标题、推荐选项及自动Other），
保留已有root-thread限制和实际host输入通道；不新增确认或审批语义。

参考tool_search_spec.rs的Include/Omit来源分支和来源去重/预算测试。
Corki ToolSearchTool._build_spec主指引已匹配BM25、下一请求暴露、MCP工具发现
不能用resource列表替代等规则；query/limit说明仍简写，现恢复原文。保留整数
minimum=1及普通function schema，不引入Codex ToolSpec::ToolSearch专属传输类型。
discovery.build_tool_plan仅暴露当前仍匹配的发现结果，过期定义回放提示search again；
搜索只是召回完整定义，不执行检索到的工具，contains_external_context保持不变。

验证新增：test_base_prompt_wire通过真实HTTP适配捕获两协议下规划工具完整schema，
跨普通请求/压缩后继续/冷恢复核对描述，压缩请求仍无tools；提问runtime请求覆盖
direct/code_mode_only、Plan/Default参数说明及回答仍为tool result，不作新user消息。
搜索增加普通function与完整参数说明断言。首批52 passed，4.35秒。

扩大工具单元/规划/提问/搜索回归初测1040 passed、2 failed。两失败是大型定义测试
完整40KB结果与新21KB base使summary请求超过fixture固定17100预算，而原归档/
工具不自动执行/压缩次数断言仍通过。固定该fixture原4962字节base预算，不删完整
工具定义、不放宽请求预算断言、不改生产裁剪行为。复跑中，第2和8项仍未完成。

复跑最终1042 passed，12.20秒，4 workers、退出0。ruff check/format及diff检查
通过。下一步继续shell/patch/image等工具说明、失败反馈与协作生命周期，不以这
三个工具的通过替代整个第2/8项验收。

## 第2项续：命令工具说明与图片精度参数暴露

上轮实现了工具参数说明并完成1042项回归，是有效进展。本轮核查工作树后继续。
参考Codex handlers/shell_spec.rs及shell_spec_tests.rs，view_image_spec.rs，
apply_patch_spec.rs；对应Corki builtin/shell.py、shell_policy.py、shell_output.py、
image.py与runtime工具构造。shell的实际限制和配置不等于参考固定值，不能只复制
“默认10000ms/250ms”而忽略host自定义等待时间。

命令工具补齐workdir、tty、login、max_output_tokens、session_id、chars说明。
通用措辞沿用参考；默认等待值调用与execute相同的clamp函数生成，后台最大值也
反映5000ms下限。保留实际字符串session_id、turn shell选择、Windows初次等待下限。
主description明确只有tty=true才分配PTY，避免参考主文案与默认pipe行为矛盾。
login禁用时明确omit/false而非宣称默认true；保留现有参数及拒绝路径，不改权限。
输出预算10000且可由policy裁小，已核对shell_result，不虚构输出容量。

图片发现实质注入差异：Corki原spec始终列detail，即使能力不支持original或启用
unified budget。现按参考仅supports_original && !unified时暴露，并恢复原始
path/description/detail说明；保留已有执行端降级兼容，未新增图片能力/环境ID。
四组合单元断言覆盖字段出现/消失，已有图片处理测试仍验证实际detail输出。

apply_patch确认Corki是JSON patch字段、Codex当前spec是freeform Lark，不能复制
“do not wrap in JSON”；本轮未把这段错误移植，完整patch提示/语法审计待续。

验证：工具单元+wire最初946 passed；增加配置默认值15组合后961 passed，4.39秒。
之后在实际HTTP请求中增加shell/stdin注册和说明断言，跨两协议/4种base来源/
普通请求、压缩后继续、冷恢复，9 passed，2.20秒。图片未支持detail的schema也
在这组实际HTTP验证，压缩请求始终无tools。图片/命令集成继续中。

扩大图片/命令集成初测142 passed、6 failed，均为旧图片fixture在不支持original
的模型上发送detail=original；修正schema后这已是额外参数错误，而不是成功读图。
将假模型改为遵守实际path-only schema，保留所有原断言：wire缩放1600、ledger
原始2048像素/原始bytes、typed detail=high、JS输出及不向模型泄露原data URL。
复跑148 passed，16.85秒，4 workers；ruff check/format和diff检查通过。
本轮没有改进程执行/回收或图像解码流程，没有读取真实图片/剪贴板；测试图片均
由fixture生成。第2项仍未完成，patch完整指引、其他工具与失败恢复边界待继续。

## 第2项续：JSON补丁接口的等价语法说明

上轮有图片能力门控和shell参数修改及回归证据，为有效进展。重查当前树后继续。
参考Codex core/assets/tools/apply_patch.lark、handlers/apply_patch_spec.rs及spec_tests，
并读handler解析/verification失败到RespondToModel调用链。Codex通过freeform
grammar提供增删改、Move to和上下文行语法；Corki是普通JSON函数，不能照搬
FREEFORM/不要JSON指令，也不能只保留几个文件头而丢失语法信息。

核对Corki builtin/patch.py原生/embedding两条解析路径，补patch参数description：
Begin/End、Add每行+、Delete无正文、Update的@@及空格/-/+、Move to的位置、
工作区相对路径及解码后换行。保留两路径均支持的操作形式，不宣称embedding
支持原生专属End of File、无正文move或任意绝对路径，不扩大底层能力。

错误链路核对execution/patch_result.py及patch_retry.py：未知执行结果显式警告
no rollback/Do not automatically retry，delta不精确不能伪装原子成功；原生失败
保留已提交prefix，重试要求新host审批。现有真实runtime测试覆盖这些边界，不
为文案一致改掉Corki必要的SQLite/delta适配或引入自动无审批重试。

在test_base_prompt_wire注册真实ApplyPatchTool，捕获两协议下patch字段和所有
语法标记；压缩/继续/冷恢复规则原断言保留。工具单元、native patch、approval、
retry、worker ownership、delta与wire合计1036 passed、3 skipped，13.53秒。
跳过项需要CORKI_TEST_PRE_NATIVE_PATCH_COMPILER、
CORKI_TEST_PRE_PATCH_APPROVAL_COMPILER、CORKI_TEST_PRE_PATCH_DELTA_COMPILER
指定的历史编译器，当前未配置，不声称已验证。当前编译器由测试环境明确指定。
未使用真实模型或真实用户文件。第2项尚有其他工具/失败恢复和完整发现生命周期
待审计，不据此勾选完成。

## 第6项：项目、环境与权限指令验收

上轮patch参数实现与回归属于有效进展。本轮核对Codex core/agents_md.rs、
agents_md_manager.rs、context/user_instructions.rs、world_state/agents_md.rs，
codex-home/instructions/mod.rs，及agents_md_tests.rs的根到cwd/override/fallback/
预算/来源顺序案例。Corki project_instructions.py、instruction_manager.py、
user_instructions.py采用相同单环境语义：全局与项目分开读、全局空override回退、
项目空override遮蔽、根到cwd、字节预算、有损UTF8、untrusted项目不读。
按环境选择/权限/trust失效，不按mtime；Codex manager同样不因每次tool step或
仅文件变化重读。加载前清旧缓存，收紧权限后读失败不会回退旧授权来源。
replacement/removal notice与参考原文相同，user角色，不混入skill或官方app目录。

核对world_state/environment.rs及environment_tests.rs的legacy single分支，
Corki environment.py采用对应单本地环境XML，cwd/shell变化全量、日期/时区/
filesystem/network变化只补必要字段；未知历史重新建立类型化基线，不从XML
猜可信状态。保留无多环境/PowerShell版本扩展等能力差异，不伪造环境。

权限参考world_state/permissions.rs及新增prefix/删除prefix/legacy测试；Corki
native/sandbox/src/permission_context.rs实际调用codex_prompts原生renderer，两个
额外权限工具开关为false，避免指引不存在工具。context/permissions.py验证本地
helper响应和有界XML，缓存键含有效权限/cwd/rules/model messages；developer角色。
新增prefix只通知增量，删除prefix或policy改变重发完整策略，关闭静默清基线。
builder→message_groups初始developer分组优先，AGENTS/environment始终user；
增量仅按相邻角色合并，历史不重新排序。

新增实际HTTP断言：临时AGENTS正文含伪developer标记仍为user消息，环境为user，
权限在Responses为developer；普通Chat Completions按既有通用适配映射为system，
不是把项目正文提权。跨普通/压缩后继续/冷恢复验证各段只出现一次且cwd正确。
首次72项中6失败：4项断言误假设Chat也支持developer，核对adapter后保留映射；
2项环境时钟fixture的5000预算被长base提前触发压缩，固定该fixture原base预算，
不改原日期增量、压缩/冷恢复、历史保留断言。

最终上下文单元与AGENTS发现/快照/provider生命周期、权限、有效环境、runtime
环境和实际HTTP组490 passed，7.71秒，4 workers。此前managed/base/压缩回归也
保留有效证据。第6项勾选完成；不把它当作其他上下文/主循环/协作项完成。
更新prompts/README环境入口和角色/生命周期说明；无需修改正确的生产行为。

## 第5项：Skills预算、来源绑定与全文读取收尾

上轮第6项取得源码/请求/回归证据，为有效进展。本轮重查技能生产链路。
Codex参考ext/skills/src/{fragments,render,selection,host_prompt,extension}.rs，
特别区分host与扩展分支：catalog developer、selected user独立消息；普通host
load_skill_prompts不做8KB裁剪，agent plugin及extension路径才用8000字节限制。
不能把该限制错误套到全部本地技能，也不能复制不存在的orchestrator read接口。
metadata预算显式配置最大10000tokens、默认窗口2%、无窗口8000chars，描述上限
1024chars、先缩描述再省略条目；Corki catalog.py采取相同计算，别名只在更省时用。
预算只数metadata，使用规则不被目录预算截断；整体窗口仍由context manager控制。

Corki context.py、mentions.py按实际源路径/qualified name绑定，输入接纳时冻结
独立user正文，不在tool step重读；异步I/O取消后join且禁止过期缓存发布。普通
shell环境变量不是技能提名；路径提名优先，歧义名称不猜错来源。catalog开关不
冒充禁用已明确选择的技能，关闭/撤除有现有world-state与history回归。
selected.md与参考host结构相同，真实skill_dir定位脚本/资源。memory技能仍走
记忆路径，不混普通catalog，同名普通技能不能替代记忆引用。

发现并修复边界说明缺口：SkillService读取完整UTF8资源且拒绝>128KiB，但输出
仍受function_output模型预算裁剪。skill_read没有offset/pagination，因此补说明
截断/超限后用现有普通只读文件工具分段读到EOF，不反复调用同一无分页接口。
补name的插件限定名前缀、file默认SKILL.md/相对路径/禁止逃逸说明，不新增接口。
保留Corki已有128KiB本地读取安全上限与完整正文策略；不复制官方agent-plugin
专用8000字节注入分支而让已注入的本地技能看似完整实则缺尾部。

新增测试证明真实读取正文完整、模型投影可截断且不修改原记录；runtime请求验证
补读指引和memory入口分离。既有HTTP验证selected正文跟在用户输入之后、user角色、
继续请求不重复且不泄露source_input_id/snapshot_state，目录关闭仍不注入目录。
全部技能单元、skill集成、host selection、plugin来源及memory routing最终
284 passed，17.76秒，4 workers。覆盖别名/中文/预算、读取失败、异步取消、压缩、
恢复与来源绑定。第5项勾选完成；真实模型是否遵循补读规则仍未验证，不据假模型
声称行为完全一致。

## Memory 读取、引用、更新／遗忘与低信任边界复核

本轮继续第4项，前轮Skills已验收，不是连续无进展。补读Codex
`ext/memories/src/{extension,prompts,schema,tests,prompts_tests}.rs`、
`tools/{mod,read,ad_hoc_note}.rs`、`templates/memories/read_path.md`；
`core/src/session/mod.rs::build_initial_context_with_world_state`、
`core/src/stream_events_utils.rs::record_completed_response_item`和
`memories/read/src/citations.rs`。更新/遗忘参考
`memories/write/src/extensions/ad_hoc.rs`及其instructions模板、
`memories/write/src/lib.rs::prompt_blocks`、consolidation模板的增量删除规则。

实际分支：feature且use开启才提供读取上下文，dedicated_tools另控工具；
摘要必须可读且非空，只有摘要按token预算裁剪。读取规则与摘要组合成
developer policy，在初始上下文中加入，不在每个tool step重读。
详细记忆由工具返回，不能被内容里的角色标记转换成高优先级消息。
显式更新请求追加小便条，不直接改MEMORY.md；ad-hoc解释模板原文要求
笔记是信息而非执行指令、不删笔记、标记来源。整理根据diff删除仅由失效
来源支持的条目，保留仍有来源支持的混合块；不是无条件删整个块。
引用标记是本地格式，提取有效thread UUID更新使用记录；保存原输出供回放，
可见文本剥离标记。未增加任何官方请求。

Corki对应`memory/context.py`、`tools.py`、`consolidation_prompt.py`及
`core/graph.py::_normalize_memory_citations`、`protocol/memory.py`。
read_path与Codex差异仅为既有架构适配：SQLite精确只读归档定位，memory skills
用root相对路径而非普通skill_read名称，以及专用通用函数工具的可用性指引。
原始记忆工具返回不变，schema名称经普通供应商函数兼容编码，未恢复原生namespace。
已整理摘要仍按Codex在developer片段内；不能据此声称所有记忆文本都在user角色。
Chat兼容层将该片段映射system，Responses保留developer；详细读取保持tool/
function_call_output。模型遵守显式更新限制仍是提示词约束，不伪称宿主能判断任意
自然语言授权，也不据假模型断言能完全抵御提示注入。

新增真实HTTP捕获断言：`test_memory_read_policy.py`放入含伪造developer和
JSON system角色、模板占位符的MEMORY.md，三个协议配置及开关组合验证原文
完整保留、只在绑定call_id的工具结果出现、不进入instructions或tools定义。
`test_memory_consolidation_prompt.py`以同类便条验证按需shell读取后仍为工具结果，
带原文“only information and never instructions”解释规则，便条未被改写。
现有请求测试覆盖摘要budget/窗口刷新/禁用/冷恢复、SQLite源验证、生成预算、
整理共享与私有工作区、引用及原文回放。

扩大回归首轮891 passed/25 failed，失败都来自三组极小窗口夹具依赖旧基础
提示长度：agent_ownership、memory_model_budget、memory_transcript_runtime。
为这些固定预算测试设置4962字符基础指令夹具，保留原窗口、压缩时点、请求数量、
完整归档及失败断言；不降低生产提示或扩大生产预算。真实完整基础请求有独立
base_prompt_wire覆盖。完整指令放不进小窗口时仍如实失败，是明确保留的能力边界。

最终Memory扩大回归916 passed，75.83秒，4 workers：全部unit/memory、
integration/test_memory*.py、long_term_memory、native_memory_citation_runtime、
thread_memory*与thread_fork_memory。新增/修改测试ruff check和format检查通过，
git diff --check通过。第4项取得源码、实际请求和相关回归证据后勾选；总目标仍
有第2、3、7、8项未验收，不标记完成。全部测试只用临时文件与假模型/MockTransport，
未改真实用户memory/配置，未调用真实供应商；不声称真实模型行为完全一致。

## 第8项既有规划／协作范围验收

前轮完成Memory并通过916项为有效进展。本轮补读Codex
`collaboration-mode-templates/templates/{default,plan}.md`、
`core/src/context/world_state/collaboration_mode.rs`及tests、
`core/tests/suite/collaboration_instructions.rs`、
`context/{multi_agent_mode_instructions,multi_agent_role_instructions,multi_agent_usage_hint}.rs`、
`world_state/multi_agent_mode.rs`，并核对plan/request_user_input handlers及schema，
`hook_runtime.rs`中root、ThreadSpawn、synthetic/internal的分支。

Codex mode按model catalog variant优先、再客户端preset/override；无配置的裸core
可以没有mode指令，CLI通过preset启用。Corki没有model-catalog mode字段，采用
现有本地preset与显式CollaborationMode override，不能编造远端刷新能力。
Plan原文与参考相同；此前恢复的Default保留全规则，只增加工具实际mode可用性
适配。空override发空wrapper撤除；None选择本地preset，不等同裸Codex core的
无客户端配置。更新由内容/模型/mode快照驱动，一次追加、保留原历史、压缩重新
建立当前mode、冷恢复不重复。既有测试涵盖legacy快照与未知历史刷新。

实际能力核对：`core/runtime.py::_default_registry`及插件/工具发现没有通用
spawn_agent、send_input、wait、close_agent；现有SubAgentSource是宿主来源建模、
start/stop hook与root-only交互限制，不是创建/调度子代理的API。按用户“双方
已有核心能力、不为模板新增功能”范围，不新增协作工具，不注入Codex V1/V2
多代理role/mode/catalog提示。memory consolidation是独立已有内部worker，其
真实工作区/来源/提示词已由第4项验证。通用ThreadSpawn的SubagentStop按真实
类型和独立信任配置触发；字符串伪造来源不能启用，synthetic不冒充root。

新增`tests/integration/test_collaboration_prompt_wire.py`真实HTTP捕获两个普通
协议：Plan→带首尾空白custom→empty→Default，完整规则逐字验证；用户伪造
mode标签保持user且不能切换宿主状态；手动压缩后只保留当前模式，冷恢复无
重复。同时断言没有多代理角色指令和不存在的协作工具。首次测试正则误把
Default正文示例标签当外层结束，改成外层首/末边界验证，不修改生产内容。

163 passed，8.06秒，4 workers：collaboration mode/wire、request_user_input、
plan_contract、proposed_plan各集成、interrupt_plan_integrity、compact_plan_save、
thread_fork_plan、subagent_stop及session_source/parser/broker/storage相关单元。
工具成功/Plan拒绝反馈、提问root/mode限制、结构化答案与plan解析回放均有现有
测试证据。第8项勾选；缺失的多代理系统和model-catalog mode variants为明确
能力差异，不通过提示词伪装。第2、3、7项仍未验收，总目标继续。

## 第2项继续：Code Mode完整帮助与MCP发现规则冲突

读Codex `core/src/tools/code_mode/{execute_spec,wait_spec}.rs`及对应tests、
`code-mode-protocol/src/description.rs`完整EXEC/WAIT模板、build方法与helper、
deferred、namespace、input/output schema描述测试。Codex通过TypeScript描述
嵌套接口，Code Mode deferred直接在tools对象可用，ALL_TOOLS可查；非code-only
分支省略重复工具声明。没有要求先执行原生tool search。

Corki `code_mode/{specs,tools,service,cell,worker,bootstrap}.py/js`核对了raw JS、
pragma默认10000ms/10000tokens、QuickJS进程、await/模块结束、媒体data URL与
detail优先级、store/load内存生命周期、notify/yield、wait增量及终止/清理。
此前描述把完整helper规则压缩成一句列名，wait参数完全无说明。新增
`prompts/tools/code_mode_{exec,wait}.md`并由spec builder使用，保留参考的结构与
逐helper规则；真实差异明确写QuickJS不谎称V8，JSON schemas不假装TypeScript
声明，notify不伪称必为provider原生custom输出，store不承诺跨进程持久化。
wait的4个参数补Codex原文描述，保留实际integer/minimum0约束和缺cell不重放
副作用规则。没有修改执行/取消/资源回收逻辑。

同时发现MCP目录无条件要求deferred先tool_search，与Code Mode实际可直接调用
冲突。`extensions/mcp/catalog.md`与`mcp/context.py`的inventory指引现在区分
direct调用需装载definition和exec里查ALL_TOOLS的nested调用；直调保留完整名，
nested用实际normalized identifier。不恢复供应商专属工具或namespace。

`test_code_mode_output_wire.py`的真实HTTP定义增加完整帮助/参数默认值断言，
原有成功失败、输出budget、冷归档及压缩回放断言不变。
`test_model_search_capability.py`新增两种Code Mode runtime测试：模拟MCP真实
catalog初始化，检查注入目录，cell仅查ALL_TOOLS后调用echo，不通过tool_search；
验证真实MCP调用、返回内容及client关闭。既有namespace/mask矩阵回归检查excluded
及direct-only能力不因新文字变成可嵌套调用。

最终658 passed，32.37秒，4 workers：全部test_code_mode*单元/集成、
test_model_search_capability及test_mcp_exposure_surfaces矩阵。ruff check、format
检查及git diff --check通过。第2项仍有MCP schema/反馈与其他工具完整审计未完成，
不因该组通过勾选全项。后续继续第2/3/7；整体独立项5/8已验收。

## 第2项继续：MCP资源说明与动态schema复核

前轮第8项验收、Code Mode修复及658项回归是有效进展。本轮读Codex
`core/src/tools/handlers/mcp_resource_spec.rs`及spec_tests、
`mcp_resource/{list_mcp_resources,read_mcp_resource}.rs`调用链，核对Corki
`mcp/resources.py`的实际参数解析、单server分页、聚合失败隔离及输出裁剪。
原资源工具只说“List MCP resources/templates”，遗漏资源用途/优先使用指引及
字段描述。现恢复Codex三个通用资源工具描述和server/cursor/uri的来源说明，
并补实际解析要求cursor必须与server同用；未改执行语义、参数名或注册条件。
并非新增工具：这些已有入口在server初始化前即可注册，按步骤快照绑定能力。

`test_mcp_resource_registration.py`在direct和code-only实际请求中分别检查schema
与exec嵌套定义的完整文案、分页约束、必填字段；原pending/dormant/failed、聚合
与具名调用、启动/关闭断言保留。资源inventory/registration/step_binding首轮
69 passed，6.25秒。

继续读Codex `handlers/mcp.rs::{create_tool_spec,build_mcp_search_text,handle_call}`、
`tools/src/{responses_api,mcp_tool}.rs`和`json_schema/compaction.rs`及测试布局。
Corki `mcp/{tools,input_schema,tool_definition,projection,output}.py`已有对应流程：
普通MCP描述不套agent-plugin的1000字节限制，显式host agent-plugin分支才限长；
规范化schema以5000字节软预算依次裁剪description、defs、深度、composition，
不是强制截断JSON文本；超大agent-plugin声明才用8000字节参数回退。schema说明
不等同服务端参数执行授权，完整原参数交RPC，malformed定义隔离。search字段保留
完整名称/描述/属性；connector metadata不授予宿主权限，不恢复官方Apps路径。
result保留真实isError，transport timeout明确提示执行结果可能未知、勿自动重试
副作用；取消不被catch伪装成正常结果。最终输出与其他错误恢复链路仍待继续审计，
不凭这一段阅读宣称第2项全部完成。

扩大验证298 passed，7.85秒，4 workers：resource*单元与集成、schema*、enum/float
projection、agent_budget和mcp_input_schema集成。ruff check和git diff --check通过。
所有MCP均是本地fake client/MockTransport，无真实服务凭据或网络调用。

## 第2项继续：失败反馈与上下文工具输出说明

上一轮补齐资源工具并通过298项是有效进展。此次参考Codex
`core/src/tools/parallel.rs::{failure_response,aborted_response,abort_message}`：
RespondToModel形成绑定call_id的失败工具输出，Fatal终止，不伪装成功；取消反馈
按工具类型区分。Corki `tools/executor.py`、`core/graph.py::_execute_claimed_tool`相关
执行分支及`storage/sqlite.py::claim_tool_call`复核：普通错误为ToolResult错误值，
Fatal直调终止、nested转拒绝promise；Cancel不被普通Exception捕获，handler
吞取消后的成功返回仍被拒绝。ledger已完成返回原结果，未完成/旧参数未验证时
不重放副作用；明确“执行前取消未执行”和“结果未知未重复”不能替换成同一句
泛化aborted文本。保留此SQLite持久化适配，不冒充已回滚。

`test_tool_failure_boundary.py`新增普通TimeoutError场景：只执行一次，下一次
ModelRequest仍为同一失败ToolResult，包含结果未知与勿自动重试副作用的原文。
连同code_mode_error_channels/nested_cancellation、interrupt_mcp_recovery、
search_error_recovery、tool_argument_recovery共131 passed，8.99秒，4 workers。

进一步读Codex new_context_window_spec、get_context_remaining_spec与handler。
Corki new_context实际先总结后新窗口，已有描述保持该必要差异；不能声称即刻
清空。发现get_context_remaining返回真实CodeModeOutput {tokens_left}却未提供
output_schema，模型嵌套调用看不到返回类型。补Codex原输出schema与说明，integer
或null与运行时一致，不改变直接调用文本或HTTP协议。token_budget_reset测试验证
spec及exec实际嵌套描述包含schema，保持原tokens_left数值和工具曝光断言。

token-budget组首轮3个rollover夹具因新base长度在压缩后再次跨reminder阈值失败；
为该测试固定4962字符base（辅助工厂显式接收该设置，不混入TokenBudgetConfig），
保留旧9000窗口/2000reminder阈值及原单次提醒断言。最终reset/notices/pending_recovery
48 passed，4.86秒，4 workers。ruff check及git diff --check通过。没有降低生产
基础指令或修改预算逻辑。第2/3/7仍需整体验收，总目标未完成。

## 第7项：重试、排队与钩子注入边界验收

继续读Codex `core/src/session/turn.rs` 的pending-input drain、
`run_hooks_and_record_inputs`、采样完成/end_turn和Stop收尾分支，
`responses_retry.rs`及`hook_runtime.rs`，以及`protocol/src/items.rs`的
`build_hook_prompt_message`、`context/hook_additional_context.rs`和
`tools/registry.rs`的PostToolUse反馈投影。参考测试为core/tests/suite/hooks.rs
中blocked_user_prompt_submit_persists_additional_context_for_next_turn、
blocked_queued_prompt_does_not_strand_earlier_accepted_prompt、additionalContext
角色/溢出、Pre/Post/Stop反馈及compact测试；已有中断与realtime分支对照见前文。

Corki对应 `core/graph.py::_prepare_model_context/_retry_update/_finalize`、runtime的入队与恢复、
prompt_hooks/stop_hooks/pre_tool_hooks/post_tool_hooks/hook_context及async_*：
新输入先按顺序持久化，首轮不让steering挤掉原输入；模型/工具未完成的压缩续轮
先采样，再接入新输入。普通排队消息始终为UserMessage，引用按输入身份绑定。
重试基于已提交输出重建history，保留Turn基础指令与step工具快照，不把重连UI
文字当成新指令，不重复执行已完成的工具。恢复收尾时比较已采样与已持久化输入，
避免崩溃窗口中遗漏steering。模型明确end_turn=false和待执行工具均触发续轮；
step预算仍约束续轮，不凭中间assistant文本判定完成。

受信任钩子的additionalContext为developer/hooks.additional_context，输入钩子
在接受的user消息之后插入；被拒绝输入本身不入history，但其受信任附加上下文
保留到下一轮（与参考测试一致）。Stop block是user反馈，非developer；Pre/Post
阻断形成工具失败输出，Post拒绝不代表已执行工具回滚。异步上下文只在安全边界
进入请求，key/receipt防重复；诊断、警告和SessionEnd输出不新增模型请求。
Hook上下文溢出仍保留完整临时文件并给出路径，取消会join worker；本轮未更改
执行或清理机制。仅已有受用户授权的hook可注入developer，普通工具结果不走此路。

新增验证：test_history_retry实际Mock HTTP中带伪developer标签的工具正文，在
断流后的请求里只出现一次，仍是原call_id的function_call_output；instructions
不变，fake key和Reconnecting UI不入payload。test_stop_hooks明确反馈user角色；
test_session_start_hooks明确developer角色与hooks.additional_context kind。
已有历史重试不重复执行、冷恢复、授权变化、排队/收尾竞态、工具取消和钩子失败
断言全部保留。必要差异：SQLite执行ledger/receipt用于跨进程恢复，不复制JSONL
机制；realtime仅指Corki已有文本steering，不注入Codex语音模式指令或官方WS降级。

起初start_hook_recovery旧8000/3000窗口因完整base提前压缩；直接设自定义短base
又破坏了它特意构造的“旧Turn没有base快照”测试入口，故未保留该做法。最终该
fixture仍不设base_instructions，仅窗口32000/阈值8000，使完整基础提示能容纳、
16000字符工具返回后才压缩。原压缩一次/工具执行一次/冷恢复不重复与授权校验
断言均未改变；92个边界用例通过。不改生产预算或恢复逻辑。

最终两组独立回归：612 passed（41.87秒）覆盖stop/start/prompt/pre/post恢复、
history/connection/HTTP retry、steering、compaction_steer_commit_boundary及
turn_admission；353 passed（30.96秒）覆盖model_continuation、turn_input_boundary、
user_prompt_submit/pre/post、同步异步hook checkpoint/concurrency、全部interrupt*
集成及session_end/hook_input_contract。均4 workers、本地假模型或MockTransport。
第7项范围内证据齐全，勾选；当前6/8，工具整体清单与上下文整体验收仍待完成。

## 第3项：上下文组装、压缩、回放与本地恢复信任边界验收

补读Codex `core/src/compact.rs`的初始上下文注入枚举、local总结请求、overflow
重试、20k用户文本保留和summary-last规则，`context/compaction_summary.rs`的
user角色；`context_manager/{history,normalize,updates}.rs`的工具输出1.2 allowance、
补aborted与删除orphan、整组消息边界；`session/{mod,world_state}.rs`的初始组装、
world-state捕获及model/personality先后，`context/world_state/{model,personality}.rs`
的Known/Unknown/Absent差分与对应*_tests；后者的增量requires_separate_message
不能误套到新窗口的聚合规则。并读ext/history-notes/src/{extension,tools}.rs的
thread hint、4000字节上限、ContextWindow slot及官方backend启用条件，作为适配
参考而非复制服务调用。原compact模板/自定义空值修复及相关证据见前文。

Corki对应 `context/{builder,window,world_state,message_groups,history,local_retention,
function_output,personality,model_transition,token_budget}.py`与protocol/context_messages：
immutable step快照，初始分组/增量相邻合并，comparison metadata不入模型；角色
变化先用原角色撤销宿主旧section，再按新角色追加，不抬升用户正文。历史消息不
因后续相邻变化重新合并。配置关闭、omitted和显式删除区分；已选技能/input上下文
不作为可随意删改的world-state。模型切换保留session base，只在真实模型身份变化
时追加新模型指令；personality遵守模型/选择和baked条件，已有测试覆盖冷恢复。

压缩继续使用普通模型请求、原base、history+最后一个user compact请求、无tools。
仅供应商实际overflow后裁掉最旧整组及相关call/result，不为估算先抹历史；失败或
取消不安装summary，成功原子提交marker+replacement，冷恢复已安装不再次总结。
手动压缩后下一轮重建初始上下文；自动压缩按pre-turn/mid-turn分支安排当前输入与
summary。保留当前真实输入、来源绑定和Corki小窗口headroom保护，20k上限仅用于
旧用户文本。SQLite保留完整审计/执行ledger，model投影可裁剪；不机械照搬JSONL。
summary为user，工具结果为tool/function_call_output，普通日志/重连UI不混入。

发现并修复新窗口顺序：freeze_context_messages此前把model/personality和所有
standalone developer一起放到主bundle之后，managed policy也在user context之前。
按参考初始规则，model.instructions现在在developer bundle首位（早于extension），
personality保留world-state顺序进入bundle；managed policy在项目/环境user bundle
之后独立放置。只匹配宿主结构key，不解析用户正文标签；incremental standalone
规则不变，已记录的message_group身份/顺序不重写。

新增unit初始/增量双边界检查；test_base_prompt_wire加入真实Chat/Responses
初始→manual compact→新窗口→cold请求的managed独立角色/顺序与恶意summary标签
断言，并新增模型切换后新窗口的实际HTTP检查：FIRST_BASE仍保留，NEXT_BASE只在
首个developer bundle第一段，extension紧随，switch仅一次。该文件10 passed，
2.83秒。此前上下文总组912项中两个固定5000阈值测试因base加长多压缩，只有该
fixture固定4962字符base，其原summary次数、工具次数、失败不安装、最新snapshot
及当前输入保留断言不变。修正后扩大组1044 passed，40.80秒，4 workers：全部
unit/context、compact*/manual_compaction*、runtime_compaction、context journal/
groups/role wire/fragment kinds/snapshot depth、instruction snapshots/provider
lifecycle、extension lifecycle、model instruction、personality*、managed和base wire。
这1044次运行开始时尚未加入最后一个模型切换wire用例；其后10项专测单独通过。

本地history/notes已有provider-independent实现，不恢复参考的alpha端点、鉴权、
加密输出或多代理访问。发现本地thread_hint将工具可写的note路径原样拼入developer
context，路径中的换行/伪指令可能抬升为宿主说明。现只注入固定恢复指引，具体路径
和正文通过已有notes list/read工具返回；不删除笔记、不改变SQLite或工具调用语义。
固定指引不硬编码某一种协议的点号/双冒号工具拼写。扩展现有真实payload矩阵：
带换行和伪developer标签的路径跨new_context/冷恢复读写成功，但任何system/developer
message都不含该路径或笔记正文；原无account HTTP、无加密字段/metadata、普通供应商
与code-only边界断言保留。local_history_notes/history_notes_runtime/token_budget*
共72 passed，8.06秒，4 workers。notes详细工具描述尚归第2项收尾，不据此宣布全部
工具已对齐。ruff check、format及git diff --check通过。

第3项已验收，当前7/8；第2项仍需完成剩余工具描述逐项清单及最终跨域回归。没有
真实模型/供应商行为验证，以上证据均为本地模型桩/MockTransport和实际序列化请求。

## 第2项收尾：history/notes说明与工具注册清单

上一轮的上下文顺序/低信任hint修复及验证是有效进展。本轮重新读当前worktree，
继续对照Codex ext/history-notes/src/tools.rs（9个action、namespace描述、参数）、
extension.rs（启用条件/4000字节hint）及tools_tests.rs的输出契约，再核对Corki
history_notes/{schemas,service,archive,notes_store,local}.py的实际参数和返回。

修复schemas.py及service.py：恢复完整通用恢复/静默私有过程说明和9个action文案，
保留不透明ID、只读历史、UTF-8字节上限与接近上限应另建文件等规则。删除不真实的
跨代理路径暗示，agent_name明确仅省略或/root，本地立即可见而非远程eventual
consistency；read_item直接在schema声明真实本地item_id来源，不再先写虚假的尾部
短ID marker再由service覆盖。修正max_chars_per_item的单位说明，write_file明确
是完整替换，不是append。未加入encrypted字段、alpha端点、official auth/backend，
未改变工具执行或存储。每项工具的描述不再额外拼接重复的简化私有说明。

test_local_history_notes的实际HTTP矩阵检查以上关键句确实出现在序列化tools里，
同时保留两协议/direct/code-only/namespace兼容边界、真实9种本地操作、压缩及冷
恢复、禁止account HTTP和secret metadata等原断言。扩大描述后一个固定16000/
12000窗口fixture在恢复过程中额外压缩；固定该fixture base为4962字符以隔离
原本的大工具输出触发点，保留原1次summary/4次采样/精确尾部恢复断言。最终local
history/notes和token-budget组72 passed，8.41秒，4 workers；ruff check/format通过。

复核全部ToolSpec构造点及runtime默认/可选注册，不只看默认工具：

| 入口 | 本地Codex参考 | Corki实际曝光/适配与已有证据 |
| --- | --- | --- |
| exec_command/write_stdin/apply_patch/view_image | core/tools/handlers对应spec与执行链 | builtin/shell,patch,image；真实JSON patch/字符串session_id/图片original条件；base wire、shell、image和patch相关回归见前文 |
| update_plan/request_user_input | plan/request_user_input spec及mode入口 | 模式/host/root可用性；schema/文案和实际调用见第8项 |
| new_context/get_context_remaining | context tools spec与token_budget handlers | 本地总结再换窗、真实tokens_left output schema；reset/notices/pending recovery |
| tool_search | tool_search spec与搜索器 | 普通function BM25、返回完整定义后下一请求加载；非原生搜索；query/limit与source模板见前文 |
| exec/wait | Code Mode execute_spec/wait_spec与protocol description | QuickJS、JSON schema、实际helpers；ALL_TOOLS可直接nested发现；完整说明与658项矩阵见前文 |
| memories四工具、skill_list/skill_read | ext/memories、ext/skills本地文件分支 | SQLite/本地路径、Memory技能不同入口；第4/5项已完整审计 |
| history/notes九工具 | ext/history-notes tools/extension | 本轮完整描述/真实本地能力修正；不恢复官方后端 |
| MCP list/read资源、服务端工具 | mcp_resource_spec、handlers/mcp、tools/mcp_tool、core/tools/context | 3个资源工具已恢复完整指引；动态schema保留来源，输出分离真实error/多媒体/structured及日志；资源298、失败131等前文证据 |
| list_mcp_prompts/get_mcp_prompt | 参考core/tools与codex-mcp中无对应模型工具注册 | 保留Corki已有通用MCP prompt入口；仅返回工具数据，不把服务端role/messages作为宿主developer政策；不虚构Codex同名模板 |
| Python插件注册工具 | core/tools/handlers/dynamic.rs::from_parts/spec/search_info | 由扩展提供真实description/parameters/exposure，非固定宿主模板；Corki plugin__命名/1024字符输入校验为既有API边界，不扩大插件系统来照搬Rust |

再次核对Codex core/tools/context.rs::McpToolOutput：log输出独立，直调模型输出加
wall-time header/1.2 allowance，Code Mode raw结果不按history先裁剪。Corki
mcp/output.py遵守该分离，保留isError、媒体顺序、structuredContent优先级、图片
能力降级和普通工具结果角色；低信任MCP prompt仍包在ToolResult，不解析成指令。
RouterBuilder仅根据已捕获registry/mode配置曝光，Code Mode不可用时的direct降级
不谎称exec可用；排除策略和冲突校验不靠提示词代替授权。

工具跨域扩大回归正在执行，尚不能凭上述清单把第2项勾选；最终联合验收也仍未完成。

上述扩大组已经结束：6482 passed，303.88秒，4 workers，覆盖unit/tools、unit/mcp、
unit/plugins与integration/test_mcp*、test_tool*、test_search*、test_code_mode*、
model_search_capability、local_history_notes和history_notes_runtime。无失败或跳过。
第2项所有实际注册工具/模型反馈入口已完成源码对照和相关回归，现勾选，8/8。

随后联合执行所有本轮修改的非PTY测试文件（git diff --name-only筛选tests/*.py，
排除e2e）及3个新增测试文件：base_prompt_wire、collaboration_prompt_wire、
unit/context/compaction_prompt_selection，结果1124 passed，98.44秒，4 workers。
此集合覆盖本轮实际改动的工具、上下文、Memory、Skills、模式、钩子与恢复测试，
不等于全仓测试，也不能与前述6482及历史专项相加作为独立总数。
全部70个修改/新增Python文件ruff check和format --check通过，git diff --check通过。
参考Codex HEAD重新检查仍为ddf04ad26789d040f9ef6a96736f76602e35a6cc；teach.md无diff。
待受影响PTY组结束并完成最终交付核对后才结束goal。

## 最终完成审计

受影响test_compaction_input_pty串行运行12 passed，34.02秒：40/100列宽、
complete/cancel/fail与realtime true/false，实际PTY输入排队、停止及草稿恢复。
没有读取真实剪贴板或调用真实模型；该结果不代表所有真实终端应用/操作系统。

按原始8项逐一核对清单与现行文件，均有具体参考代码、启用分支、Corki实现/请求
位置、修复或必要保留差异、对应验证；没有用模板hash替代实际请求，也没有将
局部通过或启动测试当作完成。静态模板之外的动态工具schema、组装角色/顺序、
自定义/关闭、更新/失效、压缩/中断/重试/冷恢复、低信任输出与fake凭据隔离均有
现行测试证据。基础能力差异采用真实说明，没有为了模板新建官方功能或无关能力。

最终6482工具跨域+1124修改测试联合+12PTY各批全部通过，集合有重叠；此前独立项
专项仍在各节可追溯。全部Python改动lint/format和diff检查通过。不声称全仓所有
测试或真实供应商行为验证。未修改teach.md、真实用户记忆/技能/配置，未提交推送。
完整交付汇总见prompt-alignment-report.md；8项独立清单全部完成，满足本轮goal。
