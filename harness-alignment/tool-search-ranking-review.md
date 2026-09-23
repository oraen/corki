# B3/B4/B7 搜索排序与缓存复核

## 2026-09-22：畸形可选 schema 元数据不再击穿整批检索

恢复 A–E 核心目标后重新核对 Codex `core/src/tools/handlers/tool_search.rs`
的检索入口与 Corki `ToolSpec`→`ToolSearchHandlerCache`→`ToolSearchIndex`。
进一步追踪到 `tools/src/tool_search.rs::append_schema_search_text`，它遍历
`JsonSchema` 中有类型的 properties/any_of；`tools/src/json_schema/types.rs`
将二者分别定义为可选 BTreeMap 和 Vec，故 Corki 的 list/null 属性访问异常
不是该有类型检索入口中的同类状态。跳过不规范文本字段是 Corki 的容错选择，
不能写成已证实 Codex 对畸形原始 schema 也采用相同策略。原生输出协议仍排除。

Corki 的 JSON 可编码准入仍允许 `properties: []`、`anyOf: null` 这类
不规范的可选 schema 字段。此前 `_schema_text` 假定它们总是对象/数组，
在索引所有 deferred 工具时抛 `AttributeError`，导致同目录正常工具也
无法检索。先增加失败反例，再只让索引提取器遍历实际为对象的
`properties` 和实际为数组的 `anyOf`；原 schema 不改写，正常的递归
描述仍参与排序。缓存入口和索引直接入口均验证正常工具可返回。

相关五文件 69 passed、0 skipped，4 workers/loadfile，实际退出 0；
改动 Python 文件 Ruff check/format 与 diff check 通过。此修复不宣称
畸形 schema 可以被模型供应商接受或执行器完整校验，也不证明真实模型
的工具选择质量。全量核心回归本批未执行；其他 B8/E2 schema 语义边界
仍按各专项收敛。

续轮补充真实 Runtime 验收：在现有搜索→加载→调用→Observation→跨 Turn
场景中加入一个不相关、畸形可选元数据的 deferred 工具；正常 calendar
工具完整执行且只产生一次副作用，坏候选未广告、未执行。原正常目录对照
仍保留；真实 Runtime 文件及索引/缓存单位联合 54 passed、0 skipped，
4 workers/loadfile，句柄 96678 实际退出 0（225485），Ruff/格式检查通过。
模型是离线脚本；该证据验证主循环链路，不验证远程供应商对坏 schema 的接受度。

## 2026-09-22：工具定义在目录发布前拒绝非法类型与非 JSON schema

沿 Codex `tools/src/responses_api.rs::ResponsesApiTool` 的 `String` 名称/描述和
`JsonSchema` 有类型字段，以及 Corki `ToolSpec`→`ToolRegistry`→搜索/模型请求链
重新核对。Corki 原仅靠 Python 注解：整数名称在 `split_tool_name` 抛
`AttributeError`，空名、整数描述/检索来源、列表形式或含 set/NaN 的 schema
能构造并进入目录，直到索引或 wire 序列化才失败。九个构造反例先红；
另补一个 `freeform_format` 非对象的明确准入断言。

现 `ToolSpec.__post_init__` 在发布前要求名称/描述及可选检索文本为 UTF-8
字符串，名称非空，参数/output schema 为可编码 JSON 对象；原合法
`WireNumber`、普通函数定义、旧枚举字符串归一与动态工具行为不变。
插件注册坏 schema 的直接回归验证没有部分发布。协议、工具、插件、MCP
schema 与真实搜索/插件集成范围 **1687 passed / 11.94s**，实际退出 0。
这只验证类型和 JSON 可编码性，不伪称校验 JSON Schema 所有语义关键字，
也不以此关闭 B1/B8 的所有来源组合。

首次非 CLI 主组 **15332 passed、7 skipped、3 failed**：旧搜索错误恢复
用例构造 object/NaN/孤立 surrogate schema 的步骤现在正确提前拒绝，故
夹具无法到达原目标。未删减这三条错误恢复断言；改为复制合法 ToolSpec 后
由故障注入的外部搜索 handler 绕过构造准入，验证结果发布层仍把损坏定义
转为错误 Observation，随后重新搜索成功。该文件 **9 passed**。第二次主组
在 MCP elicitation 的 stdio 可选预热等待出现一次 3 秒超时：单例复跑通过。
曾尝试放宽该测试的 stdio 请求预算，但它改变目录与采样时序并造成 9 项
工具未广告失败，因此完全撤回；恢复原测试后该文件 **37 passed**。
这两轮失败不计作全量通过。

最终生产修改后的非 CLI 全范围独立收集 **15801 项**。第三次主组
**15335 passed、7 skipped / 729.10s**、敏感六文件
**459 passed / 56.82s**，两组均退出 0，合计 **15794 passed、7 skipped**。
七项跳过仍为六项缺历史原生编译器、一项文件系统不接受非 UTF-8 文件名。
修改文件 Ruff、格式及 diff 检查通过。MCP 的单次并发时序超时根因尚未
独立证实，不据单例通过宣称它已修复。

## 2026-09-22：旧搜索别名与当前目录碰撞的真实请求边界

复核 `models/namespaces.py::request_tool_aliases` 时，直接构造含旧发现定义
与新平铺同名工具的 `ModelRequest` 会因别名碰撞报错；但这绕过了真实
`window.prepare` 的失效定义投影，不能据此判定 Runtime 存在故障。
新增普通 Responses/离线 HTTP 的真实 Runtime/SQLite 用例：第一 Turn
搜索 `notes::read`，随后撤销它并注册平铺名相同的直接工具；同 Runtime
与同 Thread 冷恢复各一例。两种路径均保留原始搜索历史，但下一模型请求
只带当前直接工具定义，旧搜索结果明确提示需重新搜索，不发生错误别名路由
或伪冲突。两例通过；与工具碰撞、搜索生命周期、deferred 上下文/普通 wire
及 namespace 身份五文件联合 **131 passed / 11.06s**，4 workers/loadfile/
禁重启，退出 0。仅新增测试，不改生产；后续非 CLI 完整范围已刷新为
**15773 passed、7 skipped**，独立收集 15780 项，包含本批两例，详见
`acceptance-index.md` 顶部。本结果不证明真实模型的工具选择质量。

## 2026-09-22：B1/B8 工具策略注册准入

Codex `tools/src/tool_executor.rs::ToolExposure` 是有类型枚举，工具运行器的
并行能力是布尔契约；非法曝光/调度策略不会作为目录元数据进入主循环。
Corki `ToolSpec.__post_init__` 此前只规范化 `input_kind`，接受未知
`exposure`/`concurrency` 字符串：未知曝光值要到 `build_tool_plan` 读取
`is_model_visible` 时才报 `AttributeError`；非整数输出预算还可能把 `True`
当成 1 字符，或因字符串比较抛 `TypeError`。七个构造反例先全部失败。
现把曝光/并发值在工具定义构造时转为相应枚举，拒绝非法值，输出预算
仅接受正整数且排除布尔。有效字符串归一化保留旧配置/归档兼容。
新增七项转绿，工具/协议与搜索、schema、Agent循环相关联合
**1401 passed / 8.80s**，4 workers/loadfile/禁重启、退出 0。

另核实 `ToolResult.__post_init__` 已把有序 `content_items` 复制为 tuple；
handler 返回后修改原列表不会改写已规范化结果，故只加一个回归断言，
没有对此做生产重写。本批不证明 B1 所有注册来源或 A–E 整体完成。

## 2026-09-22：动态搜索缓存的 JSON 类型身份修复

后续同一缓存链再确认一个遗漏：当工具搜索文本、普通模型可见输入定义均未变，
只有内部 `output_schema` 的 JSON `true`→`1` 变化时，
`search_cache.get_or_build` 的最后一层 `specs == _definitions` 仍错误命中旧
handler。Codex `ToolSearchInfo::from_spec` 明确剥除 output_schema，因此原生
搜索语料不因它重建；Corki 也不应重建相同的 BM25 索引。但 Corki 的发现
Observation 与已加载定义身份包含该内部字段，必须重新绑定当前定义，否则
旧搜索结果被身份校验剔除，新搜索仍只返回旧定义，工具无法重新加载。
现用既有 `same_tool_spec` 判定 handler 定义重绑定，保留索引实例。
单位反例修复前 1 failed，修复后单位与真实 Runtime 两文件 37 passed，
工具发现相关 12 文件 140 passed / 9.02s（4 workers/loadfile/禁重启）；
Runtime 的 metadata/input_schema/output_schema 三种替换 × 普通/流式 × 两种
旧配置迁移输入均验证已准备 Step 不被改写、下一 Step 重新搜索并执行新版本。
随后修复后的同范围非 CLI 全量实际退出 0：**15609 passed、7 skipped、
564.23s**，8 workers/loadfile/禁重启；`-rs` 跳过原因与上一批相同，
见 `acceptance-index.md` 顶部。
这是 Corki 普通函数路径的内部契约修复，不复制原生 namespace/output 协议。

延续下方“已加载定义”类型修复，重新核对 Codex
`core/src/tools/handlers/tool_search.rs::sources_match/get_or_build`：动态
`ToolSearchInfo` 使用有类型的 Rust 值相等比较，改变 schema 的 JSON `true` 为
数字 `1` 会建立新 handler。Corki 此前 `same_tool_spec` 仅用于已加载定义和
executor；`search_cache._DynamicSearchInfo.output` 与
`ToolSearchIndex.prepare` 仍用 Python 容器/dataclass 相等，把 `True == 1`
误当作相同，导致刷新后 `tool_search` 返回旧定义。

新增两个单位反例在修复前均失败：动态目录替换后 handler 原样复用，独立索引
generation 未推进。现把动态搜索输出键保存为排序后的精确 JSON wire 字符串，
索引逐项使用 `same_tool_spec`；对象键顺序仍不影响身份，执行期预算/并发变化
仍由原逻辑重绑定。现有真实 Runtime 的已准备 Step 冻结测试扩展到 schema
仅从 `true` 改为 `1`：旧 Step 搜索返回旧定义，下一 Step 旧结果投影失效，
重新搜索加载新定义并执行新 handler；覆盖普通/流式及两种历史兼容配置输入，
不恢复原生 tool search 输出协议。

红测 2 failed；修复后四文件 44 passed，扩大工具发现相关 12 文件
**135 passed / 8.53s**（4 workers、loadfile、禁重启），均退出 0。
五个变更 Python 文件的 ruff check、format --check 与 diff check 通过。
之后同范围非 CLI 全量实际退出 0：15604 passed、7 skipped、569.94s，
8 workers/loadfile/禁重启；跳过原因见 `acceptance-index.md` 顶部。
这证明缓存与 Runtime 刷新链的该类型边界，不证明真实模型检索质量或所有
A–E 项已完成。

## JSON类型敏感的已加载定义身份（已实施）

本轮实际冷Runtime反例发现：discovery._loaded_definition_matches采用ToolSpec
dataclass相等，其嵌套参数字典把True/1、False/0视为相等。旧搜索schema的enum
由布尔改成数值后，同名新工具未重新搜索就被曝光；两例失败于lookup仍在tools
（0391a4）。executor的“定义变化”检查同样使用普通相等，需要同一修正。

原生ToolSearchInfo/Entry使用有类型的schema与PartialEq，ToolRouter同时拥有
最终广告定义与registry；不能把Python布尔的数值相等当作工具协议身份。
方案保留现有所有字段检查，对parameters/freeform_format/output_schema额外
比较排序后的精确普通JSON表示。对象键序不影响身份，布尔/数字与不同数值表示
不被误认；运行时concurrency/output budget仍沿现有discovery替换规则处理。
搜索旧观察仅投影失效，不改原归档；冷恢复先拒绝猜测旧名称，再搜索后才执行。

已实现protocol.tools.same_tool_spec，接入discovery和executor上述两处身份检查，
不全局替换dataclass相等，不改schema参数值。实际冷Runtime完成搜索→关闭→
同名布尔/数值schema替换→旧调用拒绝→重新搜索→执行，只有一次替换工具副作用，
原归档前缀不变。精确旧Step快照仍可持有旧handler，不把中途目录刷新当即时撤销。

完整工具/协议单位及冷恢复、Step刷新联合1334 passed / 10.92秒（b9810a，
session34003终止0）。再扩展直接executor冷/legacy绑定的参数及输出schema类型
差异、对象键序不影响身份等对照，四文件36 passed / 5.51秒（014140，63611终止0）。
ruff、1190文件格式、compileall、77包依赖及diff check通过（5f9e09）。
参考commit仍为ddf04ad26789d040f9ef6a96736f76602e35a6cc且工作树干净（6a392a）。
本批修复B6/B8实际类型混淆缺陷，不据此关闭B1全部注册来源/回滚或B10 cell生命周期。

## B6 当前验收：定义随活动窗口加载，不从原始归档永久授权

再次核对Codex普通本地compact.rs：从当前历史收集用户消息，构造用户保留项与
摘要，再replace_compacted_history；旧ToolSearchOutput不作为独立项保留。
tools/context.rs的ToolSearchOutput原生wire被用户排除，这里只参考定义作为
模型上下文事实的生命周期，不引入其协议或服务端工具注册。

Corki tools/discovery.py::build_tool_plan只从传入活动窗口的成功tool_search
Observation提取discovered_tools；current_discovery_history比较当前定义，
仅在请求投影中清除过期定义，原始Observation不改写。graph.prepare在预算估算前
接history_projector/tool_resolver，压缩成功后再次建立advertised/dispatch。
identity/schema/exposure变化需要新搜索；concurrency/output_char_budget不属于
已加载模型定义的身份，沿当前Step执行策略处理，不能为了“刷新”错误释放定义。
这一普通函数路径把选中定义放进后续tools数组，费用计入完整请求而不是免成本。

test_catalog_discovery_execution_cold_reopen_and_compaction强化两处：第一次冷启动
明确验证已发现read仍可用；压缩后立即再关闭并创建新Runtime，第一次请求无read，
重新search→定义加载→执行，旧搜索归档不能复活已释放schema。两次冷启动均保留
归档前缀；摘要请求无工具。四个world-state/旧native配置组合通过，旧native只作
迁移输入，并未恢复原生wire。

当前namespace context/compaction、deferred搜索、Step目录刷新、普通HTTP wire
联合84通过（9fe0fa，14.95秒，98987退出0）；包括删除/同名替换/hidden/
code_mode_only冷恢复、过期大定义不占预算、准备后的目录冻结及自动压缩重新搜索。
ruff、1184文件格式、compileall、77包依赖及diff通过（558198）。本批无生产修改，
新增的是压缩后冷恢复的组合证据，不宣称生产缺陷先红后绿。

B6在用户普通函数路径下已核验。工具调用资格不是执行权限授予；注册来源完整
生命周期B1、Code Mode cell所有权B10、任意故障窗口A7/A8仍按独立专项验收。

## 当前验收结论（B3/B4/B7）

2026-09-12重新读取原生ToolSearchHandlerCache::get_or_build/sources_match、
ToolSearchHandler::new/handle_call/search，以及Corki search.py/search_cache.py/
router.py/discovery.py实际链路。十文件502项通过41.59秒（33f2f1，61095退出0），
范围为tokenizer、BM25、cache、router、namespace policy、Runtime ranking/cache、
deferred、MCP exposure与Step refresh。无生产代码或测试改动，没有运行原生Rust测试。

据此关闭B3/B4/B7的实现与行为核验，不再仅写“待收敛”：

- B3：search配置未禁用且存在deferred候选才建立搜索控制工具；默认8与显式Top-K，
  元数据递归索引、重复词/停用词/Unicode、完整构建再发布、弱身份/动态信息缓存、
  刷新保留旧Step corpus均由上述代码与针对性测试覆盖。非语义/向量检索；同分候选
  使用注册序，原生同分无指定次序，不承诺逐项相同或真实模型检索质量。
- B4：成功搜索的discovered_tools在下一请求加载，失败搜索不加载，未搜索的
  deferred schema不提前曝光；真实工具执行与Observation回灌及跨Turn、冷恢复均验证。
  旧定义删除/同名替换/曝光降级必须重新搜索，不覆盖canonical归档。
- B7：搜索阶段只有候选评分、定义与来源返回；测试让被检索handler执行即报错，
  证明没有自动执行候选。实际调用由后续模型ToolCall驱动。模型选择质量不在脚本
  验证结论内，不借“Harness通过”宣称真实模型准确率。

这三个结论采用用户限定的普通函数路径；原生输出协议明确排除，不恢复namespace。
B1注册来源、B8身份/规范化、B9完整错误以及B10生命周期仍各自验收，不随此关闭。

## 最新 B2/B6/B9：冷恢复后的曝光降级

2026-09-12 重读原生 core/src/tools/handlers/tool_search.rs 的 cache、handle_call、
search 与 tools/src/tool_search.rs 的文本构建，以及 core/src/tools/router.rs
当前 plan 的曝光判断。缓存保存捕获的 corpus，不因后续目录刷新改写旧 handler；
namespace 输出继续属于用户排除项，没有迁回 Corki。参考 commit 未变、工作树干净。

Corki discovery.py 在 prepare 时过滤失效的已加载定义，再建立 advertised/dispatch；
graph.py::_execute_call 对普通入口中不在当前计划的调用返回错误 Observation。
现有删除/同名替换的真实搜索提交后冷恢复测试，扩展 hidden/code_mode_only：
保留名字、描述和大 schema 元数据，仅改变当前 exposure；旧发现记录不能使普通
模型入口获得调用资格。恢复请求先去掉过期定义，不错误触发压缩；即使脚本模型
猜出 lookup 并发起调用，也得到 not advertised 错误，不执行 replacement handler。
随后 Turn 正常完成，原归档前缀不改写、用户输入一条、第二次 resume 无事件。

这是新故障窗口的行为补证，当前生产路径已通过，无生产修改。不是执行权限授予
测试，也不是 Code Mode 内部调用被禁止：CodeModeOnly 只限制本次普通模型入口。
相关缓存/排序/冷恢复/跨 Turn 执行设置/路由/deferred 链路联合 112 passed
（5c6f69，11.28 秒）；ruff/format/compileall/77 包依赖/diff 检查通过（3ab84e）。
测试使用脚本模型，不证明真实模型的搜索质量；B1、B8、B10 与完整 A–F 仍按索引收敛。

## 最新 B6/A8：搜索提交后冷恢复遇到同名替换

重读原生 ToolSearchHandlerCache::get_or_build/sources_match，确认 handler
拥有捕获的 search_infos，动态元数据变化后另建 generation，旧 handler 不改写。
本地 search_cache.py 同样先构建后发布；discovery.py 在请求投影中比较已加载
定义，执行期 concurrency/output budget 除外。此轮没有改生产代码。

原有 test_step_search_refresh.py 的冷恢复故障窗口只验证工具移除；现保留
removed 场景并新增 replaced：真实 SQLite 搜索 Observation 写入后挂起，取消
graph 后关闭 warm Runtime，冷 Runtime 注册同名 cobalt 工具。恢复首个请求
不得加载旧 amber 定义，保留当前用户输入且不因过期大定义触发压缩；重新
tool_search → 新定义可用 → lookup → NEW_TOOL_RESULT，替换 handler 仅执行
一次。原始归档前缀不变，搜索结果不重复落库，用户输入仍一条，再次恢复零事件。

专项 8 passed（31273a）；与缓存、执行策略更新、排序、deferred 搜索、搜索
压缩联合 56 passed（47e5fd，10.44 秒），session 4288 退出 0。
静态全部通过（c8a69c，1140 文件、77 包）。这是实际 checkpoint/业务历史
故障注入，不是进程强杀，不证明真实模型选择质量；完整 A–F 仍按总清单验收。

## 原生调用链与范围

core/tools/handlers/tool_search.rs 的 ToolSearchHandlerCache 收集 deferred entries，
immutable 用 Weak identity，dynamic 用 ToolSearchInfo 值相等；source listing 也是
缓存键。构建完整 handler 后发布，旧 handler 保留原 corpus。
ToolSearchHandler::new 用 bm25 SearchEngineBuilder 的 English 路径；handle_call
校验 trim 后非空 query 和 limit>0，默认 tools/tool_discovery.rs 的8。search 取
候选 document ID 再取得定义。tools/tool_search.rs 拼 name/拆分下划线名称/描述，
递归 schema description、properties、items、anyOf。原生输出 namespace 合并与
ToolSearch payload 属于用户排除的协议，不据此要求 Corki 恢复专属接口。

## Corki 实际路径

Runtime._sync_tool_search 在配置未禁用且存在 deferred entries 时发布宿主普通函数；
tools/router.py::search_enabled 不按 provider/model 原生能力切换。
ToolSearchHandlerCache 对不可变 handler 使用弱身份，对动态来源比较搜索文本、普通
输出定义和来源；执行 concurrency/output budget 变化只重绑定义而复用等价索引。
ToolSearchIndex 完整深拷贝并构建后发布 generation，查询失败不污染旧索引。
BM25Scorer 使用倒排候选、固定 k1=1.2/b=.75/f32 边界；tokenizer 使用固定 English
规范化/停用词/词干链，保留 query 重复项。等分结果按注册顺序稳定，原生 HashSet
等分顺序未指定，不能声称逐项同序。该明确差异不改变候选评分或引入语义检索假象。

ToolSearchTool.execute 只产生 Top-K 普通定义与 discovered_tools，不执行候选；
下一步 build_tool_plan 才把有效已发现定义加入 advertised/dispatch。目录变化时
current_discovery_history 移除过期定义并提示重新搜索，canonical Observation 不改写。

## 行为证据

本轮重读并运行 scoring/tokenizer/cache 单位、Runtime ranking/cache refresh 及
deferred discovery 六文件，共111通过（1978b2，7.93秒）。覆盖重复词改变首选、
停用词空命中后重新查询、非ASCII规范化、实际只加载首选再执行；MCP refresh 发生在
同批工具中时旧 handler corpus 不被重写，下一Step新目录生效；失败构建不发布。

新增真实 Runtime 默认/显式 Top-K 三组合：12个 deferred 候选初始均不曝光，默认8、
limit=1、limit=20 分别加载8/1/12个；请求定义集合等于成功搜索结果，候选 handler
执行即失败，确保“搜索”没有替模型执行工具。加入后 ranking 文件22通过
（b22149，3.86秒）。静态1110文件/compileall/77包/diff通过（d6fade）。

这证明普通函数 Harness 的召回、加载边界，不证明真实模型检索质量，不涵盖B1所有
注册来源/B2全部曝光组合/Code Mode cell内部全部行为。历史测试参数 native 是旧配置
输入兼容场景，不是本轮重新启用原生协议。B3本地候选与缓存实现已核验，完整A–F未完成。
