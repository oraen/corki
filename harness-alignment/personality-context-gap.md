# Personality 上下文链路：C1/C2 明确缺失

后续全量验证已完成：79918 退出 0（941983），15450 passed、1 文件系统限制
跳过，532.06 秒，8 workers。包含下述修复，日志 full-regression-79918.txt；
下文“生产后全量未刷新”为该批次当时的历史状态，不再是当前回归结论。

## 旧可见正文与损坏比较元数据：请求投影已修复

在上述原生 Unknown 恢复语义基础上，继续检查 Corki 的 render_context_history：
旧原始 ContextItem 被当作新快照再次渲染，损坏 JSON/结构会在请求构建时终止 Turn。
扩展真实 SQLite 冷启动测试后，五种可见旧记录全部失败（91f174，74585 退出 1；
总计 5 failed / 8 passed），确认静默比较记录的修复尚未覆盖请求投影。

现在 personality.render_history 独立处理旧记录：比较状态不可恢复或缺少有效
渲染信息时保留原消息；正常新状态仍走严格 render_update，不放宽执行账本验证。
投影仍经过原有角色变更处理，不重写持久历史。集成测试检查连续两 Turn 成功、
原前缀不变、每次实际请求保留完整旧消息、仅追加一次新风格更新。
补充旧格式保留及新损坏状态不被吞掉的单测。

完整 unit/context 与六个 personality 集成文件 427 passed / 5.58 秒
（ca36c6，31949 实际退出 0），4 workers/loadfile/禁重启，null keyring 与当前
sandbox compiler。Ruff 和本批 diff check 通过。此结果不是全量回归；最新
15397 全量仍早于近期 Hook 和 personality 生产修复，需要后续刷新。

## 损坏的旧比较快照：Unknown 降级已修复

原生 world_state/mod.rs:117–142 对 section snapshot 反序列化失败记录 warning 并
转 Unknown；PersonalitySnapshot 的 model 必须字符串，personality 缺省为 None。
Corki 之前直接 json.loads/索引旧比较状态，破损 JSON、数组、缺字段会使 TurnFailed，
错误 model 类型还会静默抑制应有更新。五种真实 SQLite 静默比较记录冷启动反例
先 4 失败/1 通过（连旧三例为4失败4通过，307b47），证明具体运行链路问题。

personality._previous_snapshot 现验证旧类型/枚举、兼容缺省选择，失败时只记录
不含旧内容的诊断并走 Unknown/可信旧模型回退；不改原行，不忽略新生成状态错误，
不放宽执行账本或数据库完整性验证。测试检查一次更新、第二 Turn 不重发、旧前缀
不变及诊断存在。完整 unit/context 与六个 personality 集成文件416 passed，
5.32秒（ae57b7，6287实际退出0），4 workers/loadfile/禁重启，null keyring/
当前 sandbox compiler；Ruff771f68通过。无活动测试，生产变更后全量尚未刷新。
本批直接覆盖静默比较记录，不泛称所有 section 或旧可见消息投影的损坏均已处理。

## 独立旧 reference 格式的适用性已核定

见context-reference-compatibility.md：已追踪Codex RolloutItem::TurnContext →
reference → PersonalityState以及Corki旧Message/typed context/默认设置/准入
checkpoint链路。独立Codex rollout格式导入不是本目标要求；核心状态用途分别
已有Corki来源与冷恢复证据，七文件43通过（e3fdab）。不是因字段名不同免除恢复。
下方“独立旧reference仍需核定”不再作为开放项；其它C1/C2/C8要求仍独立验收。

## 隐式启动恢复：四种选择已直接验证

追踪现有 cli/main.py:83 → repository.read_thread_model_settings →
CorkiSettings.for_directory:711–720 → Runtime.create：未显式选模型/provider/effort
时使用持久 Thread 选择，包括 None，不被新宿主 personality 默认覆盖。
本次只补启动组装的核心恢复测试，不改 CLI 实现或恢复视觉/交互验收。
新测试通过真实配置文件、SQLite、冷组装、普通模拟 HTTP：保存四种选择，修改
宿主 provider/model/effort/personality，再无显式设置恢复；检查内存选择、原完整
默认、历史前缀、实际请求仍为旧 provider/model，总请求两次。未设置与明确 none
分别验证，不以显式配置的冷 Runtime 代替隐式恢复证据。
完整 resume_model_settings 与六个 personality 集成文件及迁移单测 55 passed，
7.92 秒（822179，12768 实际退出 0），4 workers/loadfile/禁重启，null keyring/
当前 sandbox compiler。无活动测试，无生产修改。

当前正常选择/恢复/普通 wire 链路已有上述证据，下一步应刷新非 CLI 全量并转回
A–E 跨模块故障项。独立旧 reference_context 格式条目仍需核定具体行为需求：
目标是 Corki 自身旧数据库/checkpoint 兼容，不默认扩展成导入 Codex rollout 格式。
不能仅因字段名不同宣称缺失，也不能在未追踪历史状态来源前直接判为不适用。

## 普通 HTTP 与旧列迁移：补充端到端证据

新增 test_personality_wire：真实 LangGraphRuntime + 两普通 HTTP 适配器，使用
MockTransport 离线返回 SSE；api_mode Responses/chat_completions × provider
openai/independent 四组合。连续四次冷 Runtime 验证首轮、同模型风格变更、未变
去重、跨模型切换，再手动摘要和重建，共六次请求。所有请求只到配置的模拟端点，
使用普通 Bearer；无 x-codex 请求头、personality/namespace/context_management/
previous_response_id 顶层字段，工具只为 function。风格保持独立 developer
消息（Chat 适配为 system），内部 content_kind 不泄漏，原历史前缀保持不变。
四项直接通过（4e5d0d，19590 退出 0）；这不是线上模型质量或所有网络隔离的证明。

新增 test_personality_migration：仅降级临时测试库的 personality 列，保留已有
model/provider/effort/mode 与用户历史；初始化迁移恢复 None，再写入四种选择并
冷读验证完整默认及历史不变。完整 unit/storage 与七个相关集成文件共 207 passed，
5.52 秒（b9600c，84414 实际退出 0），4 workers/loadfile/禁重启，null keyring/
当前 sandbox compiler。Ruff 5344f9 通过。本轮仅新增测试，无生产修改。

仍需独立旧 reference 格式的适用性/迁移映射与隐式宿主配置恢复直接验收，以及
压缩故障窗口等跨模块项；正常 HTTP 冷启动显式提供设置不等于隐式设置恢复。
无活动测试，全量尚未刷新。后续回到 A–E 开放清单，不继续重复已绿正常风格排列。

## 自动压缩重建的跨模型重复注入：已修复

原生 compact.rs:65–115 将手动/pre-turn 的清除基线与 mid-turn 的初始上下文
注入分开；PersonalityState 的 Absent 仍检查 previous model，跨模型不单独注入。
Corki window.prepare 自动压缩重建仅保留 model.instructions 的渲染/快照配对，
personality 则存回原始模板文字，随后历史投影在缺少 previous_model 的情况下
重新渲染，丢失跨模型抑制条件。现同样保留 personality 的渲染/比较状态配对。
手动 compact 仍只替换摘要和保留输入，下一普通 Turn 重建，不改变其原有流程。

新增真实 Runtime 测试覆盖手动/自动 × 同模型/跨模型，显式 HOST base 与本地
模板；检查摘要请求加正常请求共三次、基础指令不变、原历史前缀不变、风格更新
数量及冷启动下一 Turn 不追加重复快照。修复前 small/automatic 单例失败、其余
三通过（bc0972，58579 退出 1）；修复后完整 unit/context 与十个关联集成文件
460 passed，8.38 秒（0aace1，11376 实际退出 0），4 workers/loadfile/禁重启，
null keyring/当前 sandbox compiler。Ruff/scoped diff check 3ff603 通过。
无活动测试，未刷新全量；未覆盖此交叉点的故障注入或真实 provider wire，不能
由这些正常流程测试核销所有 C8 故障恢复项。独立旧 reference 格式与迁移仍开放。

## 旧模型回退与精确快照优先级：已修复

本轮对照 session/world_state.rs:103–116 和 PersonalityState::render_diff：缺少
reference_context 时 previous_model 仍可建立旧选择 None 的参考。同模型且当前
明确选择非空风格时应发送更新，无模型参考或跨模型则不发送独立风格更新。
personality.render_update 已使用原有可信历史模型标记实现该回退，不读旧文本猜
选择，不取当前 Thread 默认。三种真实冷启动场景先 1 失败/2 通过（796103），
修复后扩大 440 通过（69a4af，69627 退出 0）。两次冷启动验证不会重复发布。

进一步核对 world_state/mod.rs:416–435：精确 section 快照优先于任何旧标签。
Corki 原先扫描到较晚旧标签会将 Known 覆盖成 Unknown，造成未变风格也重发。
真实 Runtime 在有无自定义 base 两种情况下，风格改变和未变 Turn 前各插旧标签，
先两失败两通过（fd3d6d）。现旧标签只在没有 section 基线时建立 Unknown。
最终完整 unit/context 与七个关联集成文件 442 passed，8.85 秒，4 workers，
loadfile/禁重启；78529 实际退出 0（e6eba7），null keyring/当前 sandbox compiler。
Ruff 与 scoped diff check 84b514 通过；无活动测试，未刷新全量。

仍开放：独立旧 reference_context 的持久格式映射、压缩清除/重建时的参考生命周期、
冷热模型切换及普通 HTTP wire、数据库迁移验收。本次不将这些缺口核销，也不将
旧模型回退等同于完整 reference_context 兼容；下方一律静默的描述属于修复前状态。

## 旧标签无参考恢复：已验证；有参考路径仍开放

当前 world_state 只将非输入来源、legacy.developer key、developer 角色且完整包裹
于 personality_spec 的片段识别为 personality.legacy_unknown，不改写旧记录。
此前反例出现新风格指令加通用撤销两条更新；现在无可信参考时仅建立静默快照。
真实 Runtime 连续两次冷启动验证旧前缀不变、旧片段保留且不重复注入；单测覆盖
用户角色、引用前缀、自定义 key 与输入来源不被识别为宿主 personality。

本次恢复执行先检查无 pytest/execnet/xdist 进程，旧批次输出未留存，未沿用其
未知结果。重新执行完整 unit/context 加七个 personality/模板/协作/模型生命周期
集成文件：438 passed，7.96 秒，4 workers/loadfile/禁重启，55259 实际退出 0
（eb73d4）；null keyring、当前 sandbox compiler。相关 Ruff 65ff87 通过。
这是局部扩大回归，不是刷新全量，也不证明真实模型风格选择质量。

再次核对原生 PersonalityState::render_diff 与 session/world_state.rs：Unknown
会使用 previous_context 的模型和选择，缺少该记录时仍可使用 previous_model。
Corki render_update 对 Unknown 一律不发更新，尚未实现完整参考回退，仍有差异。
下一步沿准入前的持久模型选择接参考链，不能拿当前 Thread 默认冒充旧选择；
同时覆盖同模型变化、跨模型去重和压缩后的参考保留。普通 HTTP 与迁移验收仍开放。

## Feature门控与明确none分离：已接入

原生features/src/lib.rs:1635默认开启Personality；models-manager/src/model_info.rs:
70–101在功能开启且选择None时移除首个# Personality H1段，在功能关闭时改为
默认变量替换并移除变量能力；session/world_state.rs:96不添加独立section。
本地fallback按官方模型slug更换内置模板的分支不复制；Corki只处理宿主本地模板。

新增[features].personality与CorkiSettings.personality_enabled，严格bool/默认true。
ModelSettingsSnapshot/capture/bind保留准入开关，活跃模型解析也保持原开关。
模板render显式分开关闭功能与none：关闭用default，无variables时也去掉占位符；
明确none移除首个精确H1标题到下一H1或末尾，保留其它段落及换行。Builder关闭
section并标记omitted，不额外发布撤销旧片段；不改原始历史或冻结base。

四新Runtime反例先4失败43ee1d，接入关联28通过699bdd；新增LF/CRLF及H2保留
范围、TOML开关、四选择×两开关的prepared checkpoint冷恢复（宿主反转开关）验证。
完整unit/config/context/core及八个personality/模板/活跃设置/基础指令集成文件
1348通过、1文件系统跳过，9.82秒（a1fb11，67579退出0），4workers/loadfile/
禁重启，null keyring/当前sandbox；Ruff597fa6，diffb302f5。初次Ruff单行超长
已在测试实际退出后仅作换行修正。无活动测试，本次未刷新全量。
仍需旧标签Unknown/reference恢复、同Thread冷热模型切换、压缩重建及普通HTTP
wire等闭环；不把首批feature验证称为整个C1/C2完成。

## 活跃Turn边界已核对：不新增personality更新入口

纠正此前开放清单：原生protocol/src/protocol.rs:494–506的TurnSettingsUpdate仅
包含reviewer/model/effort/summary/service_tier，**没有personality**；session/
step_activation.rs:273–290明确解构这些字段并构建StepSettingsUpdate。内部共享
StepSettingsUpdate支持personality不代表公开活跃Turn接口也支持它。
ThreadSettingsOverrides则包含personality，作用于未来Turn；turn_context.rs:
281–284明确返回冻结initial personality。故不向Corki.update_turn_settings扩加该字段，
这不是牺牲核心对齐，而是避免比参考路径多做一个不同语义的入口。

实际问题已修：TurnSettingsController模型变更重新从宿主捕获metadata和选择，
会带入后来修改的Thread personality。新真实HeldModel反例7c3d0d在原friendly
Turn挂起时将Thread改pragmatic，再切small，发现active选择错误变pragmatic。
现解析所用宿主视图显式保留当前任务personality，其他宿主资源仍用当前配置。
验证旧采样不被改、active/初始与Step checkpoint均保留friendly、未来Thread仍
pragmatic。关联70通过7dc506；完整unit/core/context及六个活跃设置/personality/
模板/Thread/模型指令集成文件648通过、8.25秒（fb8c99，6454退出0），4workers/
loadfile/禁重启，null keyring/当前sandbox；Ruff3a1eb8，diff53dbc5。
本次生产改动未刷新全量。下一步feature门控及旧标签/跨模型/压缩/普通wire补验，
不要再把“公开Turn personality更新API”列为待实现。

状态：本地模板默认渲染链路已实现，动态选择与typed section仍缺失。不是 CLI 风格任务，也不是官方
账户/模型服务协议；应使用宿主本地目录提供的模板，通过普通模型请求发送。

## Thread更新与typed片段首批已接入

ThreadModelSettings新增可空personality，SQLite老表迁移/读写和Runtime初始化保存；
隐式配置恢复使用持久选择。update_thread_settings增加UNSET区分未改与显式None，
沿用先提交再发布及取消join；不改变已准入Turn。Builder为已绑定模型建立
personality比较快照，保存model/selection/baked，文本仅取明确选择的非空本地变量。
world_state独立render：同模型选择改变发布personality_spec独立developer消息，
模型变化交给model.instructions；初次baked只存静默状态，自定义base则独立注入。
未改变选择不重复，原基础指令和历史前缀不覆写。当前Runtime事件类型无新增专有wire。

两Runtime红例bdcdec证明无section；首次接入遗漏具体Runtime方法参数，de76ae
两失败四通过，补签名后6通过140591。扩大完整unit/config/context/core/storage及
七个personality/模板/模型指令/基础恢复/Thread更新集成文件1474通过、1文件系统
跳过、9.12秒（aff143，94175退出0），4workers/loadfile/禁重启，null keyring/当前
sandbox编译器；Ruff354b43。当前无活动测试，本次未刷新全量。

仍开放：运行中update_turn_settings入口、Feature::Personality映射、旧developer
personality标签Unknown识别和旧reference选择恢复、跨模型/冷开/压缩专门验证、
普通HTTP角色与Thread默认迁移/隐式恢复的直接回归。本节不是整个personality完成。

## 宿主选择与任务冻结已接入

CorkiSettings及[agent].personality支持未设置/none/friendly/pragmatic，非法值在
配置构造时拒绝；capture/bind_model_settings与ModelSettingsSnapshot保存选择，
builder按准入选择渲染本地模板。旧JSON/checkpoint缺personality按未设置兼容，
不混同明确none。尚未接入ThreadModelSettings持久默认、运行中更新API及独立section。

新真实prepare→call_model前checkpoint→关闭→冷Runtime四组合先4失败1c0b38，
接入后连模板8通过0bd35b。冷宿主统一改none，恢复仍按原选择发送一次请求，
SQLite ledger与checkpoint选择均不变，第二次resume为空。新增配置读取和非法值/
旧快照兼容测试；完整unit/config、unit/context、unit/core及六个指令/基础恢复/
Thread更新集成文件1318通过、1文件系统限制跳过，8.98秒（272e70，36851退出0），
4workers/loadfile/禁重启，null keyring/当前sandbox编译器。Ruff e9d3aa，diff03ccfa。
本批未跑新全量；同模型切换不能仅靠base渲染实现，下一步必须接独立差异状态。

## 首批实现：本地模板进入Runtime和checkpoint

新增ModelInstructionTemplate有限值对象，按原生placeholder/变量缺失/default与
显式none语义解析、渲染；每个值和每种渲染结果限制30000 UTF-8 bytes，先算
展开长度再replace。目录ModelContextInfo保留模板，builder使用get_model_instructions
进入原会话基础来源链；显式旧catalog base_instructions优先，permission-only
model_messages不意外覆盖旧默认。不加载远程目录，不按provider身份选择路径。

四个Runtime模板反例先4失败9b2802（供应模板被忽略），接入后4通过cb16cf。
增加真实checkpoint读取后再4失败e824b0：新类型未被serde允许，整个选择恢复成None。
现加入精确类型白名单，ModelSettingsSnapshot JSON反序列化重建同一有限类型；
集成同时检查真实请求、JSON round-trip与checkpoint完整选择相等。
10模板选择/非法类型/UTF-8上限/展开超限/固定base兼容单测，连完整unit/config、
unit/context、unit/core及四个模板/模型指令/基础覆盖恢复集成文件：1284通过、
1文件系统限制跳过，9.02秒（bd9b51，83182退出0），4workers/loadfile/禁重启，
null keyring；Ruff2ad447。本次未跑全量或真实模型服务。

下一步必须将personality选择贯穿配置、Thread更新、Turn/Step冻结与恢复，再接
独立typed section；不能把模板类的render参数当作已开放的Runtime切换功能。
下方“当前真实入口”保留为本次修复前诊断，具体当前状态以本节为准。

## Codex 调用链与分支

固定参考 ddf04ad26789d040f9ef6a96736f76602e35a6cc。

- protocol/src/openai_models.rs:523–550：get_model_instructions读取model_messages的
  instructions_template。无variables时模板是字面文本；variables存在时替换
  personality占位符，缺少对应值替换为空；无模板返回空指令。
- 同文件:680–724：supports_personality要求模板包含占位符且default/friendly/
  pragmatic三个字段均存在。未选择personality使用default；显式None枚举使用空串，
  friendly/pragmatic取对应字段。不要把“未设置”和“明确禁用”混成同一种状态。
- core/src/session/mod.rs:705：会话基础指令优先宿主配置，否则使用模型模板渲染。
- core/src/session/world_state.rs:80–117：仅Feature::Personality启用时添加section；
  根据当前模型、personality、旧context或模型来源、已渲染指令建立状态。
  “已嵌入基础指令”必须同时满足模型支持和实际base等于当前model instructions，
  不能按provider名字、模型名称或是否设置base字段推断。
- context/world_state/personality.rs：snapshot存model和personality。Known只在同模型
  且personality改变时发布新非空指令；模型改变由model.instructions承担，避免重复。
  Unknown使用旧context作比较；Absent仅未baked且无跨模型变化时发布。没有文字
  更新也必须保存快照，不可把“未发片段”等同于“没有状态”。
- context/personality_spec_instructions.rs：独立developer消息，kind为
  personality.spec_instructions，标签personality_spec；不是新的provider wire字段。
- context/world_state/personality_tests.rs覆盖初次baked/独立、同模型改变、跨模型
  不重复、持久快照无保留更新仍不重发。模型模板边界测试位于openai_models.rs。

## Corki 当前真实入口

- config/model_context.py:parse_model_contexts只把base_instructions传入ModelContextInfo；
  model_messages仅交给ModelPermissionMessages，instructions_template/variables未解析。
- protocol/context.py:ModelContextInfo只有可选固定base_instructions，没有上述模板状态。
- context/builder.py:from_settings使用model_info.base_instructions；基础来源已有持久化，
  model.instructions和collaboration_mode已有独立片段，但不能代替personality。
- config/settings.py:CorkiSettings、protocol/settings.py:ModelSettingsSnapshot以及
  core/model_settings.py:capture_model_settings/bind_model_settings均无personality选择。
  仅给builder增加文本将丢失Turn冻结、冷恢复和模型切换语义。
- 执行诊断a6f49e：本地catalog提供完整template与三种variables后解析出的
  base_instructions仍为None；dataclass字段检查settings_personality=False、
  snapshot_personality=False。此为真实解析/类型入口证据，不以rg无结果单独证明缺失。

## 实施顺序与验收

1. 增加有大小限制的本地模型模板/变量类型，解析model_messages并兼容现有固定base。
   单测覆盖缺模板、无变量、变量不完整、显式禁用、默认及两种选择；不添加远程目录。
2. 选择进入宿主配置、Thread默认/更新、Turn/Step冻结与序列化恢复；旧快照缺字段可读，
   非法值在派发前拒绝。完整模板与渲染结果须受现有上下文上限约束。
3. builder与持久比较视图接入独立personality状态；复用已有会话基础来源和模型切换，
   保留原始历史，不用全局替换旧developer消息。完整覆盖Known/Unknown/Absent及
   baked与宿主自定义base；无新文字仍持久化状态。
4. 真实Runtime通过两普通HTTP适配器验证角色、首轮、同模型变化、模型切换、撤销、
   冷恢复不重发、手动/自动压缩重建；恢复仍使用准入时的选择和模板，不取宿主新默认。
5. 关联基础指令、协作模式、模型切换、预算、权限模板及旧会话回归。完成上述链路前
   保持C1/C2部分一致，不把仅模板解析或脚本模型通过称为完整能力或真实模型风格质量。

本轮仅审计和入口诊断，无生产修改。是否复制原生内置风格文案不是验收要求；
本地宿主模板驱动与生命周期语义才是核心，继续保持Corki身份和普通传输边界。
