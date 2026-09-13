# B3/B4/B7 搜索排序与缓存复核

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
