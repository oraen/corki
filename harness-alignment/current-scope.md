# 当前用户范围修订（优先于旧对齐审计）

## 当前有效范围

以objective.md及外部goal目标文件的最新修订为准：仅推进A–E核心Harness；F的
CLI页面/风格/交互对齐由用户要求暂停，不属于当前完成门槛，也未宣称已完成。
保留既有CLI改动，只在核心故障确实跨适配层时做必要的最小修复；不修改teach.md。
普通模型兼容路径、Harness工具搜索/本地历史压缩与通用MCP OAuth保留；官方
账户/配额/history服务、原生tool search/namespace及远程compact等专属协议排除。

本文下方所有“最新CLI”“完整A–F继续”等段落均为范围调整前的历史记录，不是
恢复CLI工作的授权，也不是当前进度。当前实施顺序见remaining-implementation-priorities.md，
验收与回归结果见acceptance-index.md及core-regression-current.md。

## 以下为历史进展记录

最新审批实现：列表编号现在支持与原生一致的数字键显式提交，仍拒绝越界和
bracketed paste授权；实际once/session/rule桥接28通过，审批PTY等104另通过。
详见approval-scope-ui.md，未新增权限或官方入口，完整A–F继续。

最新摘要故障修复：独立 aclose 错误不能覆盖已发生的 ModelError，不再破坏
原摘要重试判断；无主错误时关闭失败仍传播。新反例修复后，完整历史重试、
关闭次数及单一摘要均符合断言；上下文/压缩 702 通过（8f4080），静态通过。
详见 summary-cancellation-review.md，完整 Goal 未缩减。

最新上下文修复：摘要流关闭异常不再覆盖本次取消请求；保留原输入、工具对
与单一取消终态。无取消的关闭错误仍失败、不落摘要。四反例先红，最终
上下文/压缩联合 701 通过（2bb9a8），见 summary-cancellation-review.md。
仍使用普通模型请求，没有远程压缩协议；完整 A–F 继续验收。

最新 D6：已核验父关闭等待者取消不遗失超时记忆子任务，不提前关依赖、不重复
关闭或迟到发布；shutdown 专项 11 通过。记忆单位/集成较大范围 800 通过
（新增参数前收集），所有权证据表见 memory-close-recheck.md。本轮未改生产
逻辑、未引入官方 quota guard，完整 A–F 目标继续。

最新 CLI 实施：Hook 完成信息不再整块灰显，按原生区分状态圆点、来源前缀
与正文；保留 NO_COLOR、字面文本及 transcript 重排。真实事件入口已接，
CLI 单元和 24 Hook PTY 联合 456 passed（2eefbe），完整 A–F 仍未完成。

最新 B8：补齐 Chat/Responses 的平铺别名碰撞拒绝边界，两个不同工具不能
静默共享 wire 名；零 provider 请求、零执行。相关命名/注册/冷恢复/数值
身份联合 113 passed（380406），详见 flat-tool-identity-review.md。没有恢复
原生 namespace，也没有据局部测试宣布完整 A–F 完成。

最新回到工具检索主链：补验搜索提交后冷恢复的 Hidden/CodeModeOnly 曝光降级，
旧定义不能恢复普通调用资格，猜名调用无副作用；缓存/排序/路由/跨 Turn 联合
112 passed（5c6f69），静态通过。未改变普通兼容协议或生产行为，详见
tool-search-ranking-review.md；完整 A–F 仍未完成。

最新批次提交前失败/取消补证：真实 Runtime、SQLite/Volatile、实际批准命令
正对照，69 项存储/冷恢复联合通过（bb94d9）。跨搜索/压缩/记忆/恢复组合
215 项通过（88db1a）；修正 CLI 冷恢复 fixture 的旧 idle 时序假设，保留
单次采样/副作用/历史断言。生产逻辑未为此改动，完整 A–F 仍待逐项收敛。

最新整批 Hook 恢复已实施：首次副作用前保存版本化完整批次，冷恢复按原批次
复用完成结果、拒绝 unknown、重新校验未执行命令的当前授权；删除/撤销不再
绕过账本，新增不混入，改身份不替换。核心组合 273、追加变更 24、CLI/PTY
446 分别通过，详见 hook-batch-recovery-design.md。旧未完成 Hook 若无批次
快照会明确报错，不猜测补写或重跑；未删除旧数据，完整 A–F 仍未完成。

最新配置变化恢复审计：删除声明或撤销授权后，旧 unknown/completed Stop 会被
绕过；四真实冷恢复探针均违反原断言（46339a）。已保存整批持久化与授权边界
方案到 hook-batch-recovery-design.md，尚未实施，不作为通过测试或已修复项。
上轮配置不变的 200 项通过仍有效，但不能外推为配置变化也正确。

最新上述冷恢复修复扩大至存储/evaluation/Stop 来源与恢复联合 200 passed
（9af1fd，15.30 秒），静态检查通过。未宣称全 A–F 完成。

最新 Stop 冷恢复修复：晚到输入不再跳过已有本 Step claim。完成结果复用，
unknown 明确失败，不增加采样或再次执行；两反例先失败后相关 54 passed
（fd4560）。新增仓库只读存在检查，无 schema/key 迁移；配置变化时的完整
批次恢复仍未完成，见 stop-pending-input-boundary.md。Goal 全范围保持。

最新回到 A4 主循环：已修复 Stop 明确停止被 hook 期间新输入覆盖、额外采样的
缺陷。服务三态返回，graph 停止继续但 Runtime 正常保存收到的输入；两顺序先红，
相关 78 passed（9739c1）。详见 stop-pending-input-boundary.md，A–F 索引已刷新，
不以已有 UI 修复代替核心循环验收，也不将异步/non-root 等缺口判完成。
最终所有 Stop 来源/恢复/control、evaluation、默认循环预算联合 114 passed
（866169），静态检查通过，详见对应边界审计。

最新恢复入口：已有 resume_pending 迭代器交给统一交互消费者，仅在真实
TurnStarted 后开启 composer；空恢复不抢读，新输入只排队，绝不重提原输入
或启用恢复 steering。四个输入/清理边界通过（2d3483），实际 Hook fresh/resume
PTY 与既有 checkpoint 恢复回归见 hook-cli-lifecycle-audit.md。全目标继续。
最终 Hook 双入口 24 PTY/CLI 单元/Stop checkpoint 恢复联合 458 passed（68038f），
静态通过。checkpoint 故障窗口本身尚未与 PTY 组合，不能用分别通过冒充该证据。

最新非实时 composer 修复：TerminalUI 普通 Turn/compact 复用输入所有者保持显示，
新输入排到下一轮，不打开 Runtime realtime、不调用 steer。双模式 Hook PTY 与
CLI 单元 428 passed（5e8bf7）；原非实时 PTY 先失败（b74826）。计划 PTY 夹具需
区分忙时和空闲读取，复验见 hook-cli-lifecycle-audit.md。resume 活动显示、专属
样式和冷历史回放仍开放；全 A–F 目标不收窄。
最终双模式提问/压缩与计划 PTY 24 passed（dcb4f0），审批/提问/压缩/计划取消
另一组合 58 passed（7c5f08）；为重叠集合，不加总为独立测试数量。

最新 Hook 真链路 PTY：40/100 列 × 安静成功/阻塞继续/Ctrl+C 六项通过（e990b1），
使用真实批准命令与 Runtime，不伪造事件；活动提示不留 Transcript/输入历史。
非实时显示缺口已定位到事件消费期间 PromptSession 不运行，并非 timer 失效；
保持未完成，详见 hook-cli-lifecycle-audit.md。此后文中“无 Hook PTY”为旧阶段。
加入真实取消 pid 退出断言后，Hook PTY/CLI/evaluation/Stop 控制集成联合 459 passed
（334337，21.31 秒），静态通过。本轮新增验证，无生产修复。

最新 Hook 活动状态：300ms reveal / 600ms quiet bookkeeping、单 timer 所有权、
终态及 EOF/异常/取消清理已接入工具栏。CLI/evaluation/Stop 集成联合 509 passed
（10aeea）；终态即时清理补充复验单独记录。非实时模式没有活动 PromptSession，
仍缺相应视图；Hook PTY、颜色、历史回放、exec/usage 顺序未完成，见生命周期审计。
A–F 仍保持全范围开放，本轮不改变整体完成标准，也不增加官方协议/服务。
最终终态即时清理与基础 CLI PTY 复验 101 passed（2f74db），静态检查通过；
基础 PTY 不作为 Hook 专项呈现证据。

最新 HookStarted/HookCompleted 已接入 Runtime 与 CLI 完成文本，不冒充 ToolCall。
类型化诊断只显示一次，quiet/context-only 成功不留历史。联合 482 passed（fd631b），
延迟活动状态、专属样式及 PTY 尚未完成，见 hook-cli-lifecycle-audit.md；后文仍用
WarningEvent 表述执行结果的记录为此前阶段，当前仅发现/信任警告继续使用它。

最新 Hook CLI 生命周期审计：原生 started/completed→独立状态机，300ms 延迟
显示、安静成功不留历史、Context 不显示；Corki 缺少事件配对与瞬态状态。已保存
源码调用链及 Runtime/渲染/PTY 验收方案到 hook-cli-lifecycle-audit.md，本轮为
实施前审计，尚未宣称专属 UI 已实现。

最新修复 hook 诊断丢失：systemMessage 独立于 stop/block 控制结果，通过 WarningEvent
发送，suppressOutput 不误吞；不混入继续反馈。真实 Runtime 三个反例先失败后通过，
联合 99 passed（8877a5），静态通过；专属 hook CLI 展示仍未完成，详见执行方案。

最新修复多 Stop hook 汇总优先级：任一 continue:false 压过所有 block，执行结果
逐条保留，但没有 stop 才提交继续反馈。两个声明顺序反例先失败后通过，多 block
仍按顺序回灌，联合 90 passed（29abac），静态通过。详见 stop-hook-execution-plan.md。

最新 Stop 整次调用快照验证：两个真实命令之间刷新定义/授权，不改变当前调用
捕获的第二个命令；后续 Stop 使用新状态。六个场景通过（b7b269），无生产代码
改动；并发刷新等仍开放，详见 plugin-hook-runtime-audit.md。

最新纠正 hook 热刷新边界：参考源码每次 Stop 捕获 session 当前 Hooks，不是等
新 Turn。已移除先前错误的 graph 插件快照延迟方案，改为定义/授权预先准备后
原子发布；避免新定义配旧 Step 授权。三个反例先失败后通过，hook 集成 53 项
通过（23c839）；更大插件配置刷新/恢复联合 461 passed（17a7b5），静态通过。
详见 plugin-hook-runtime-audit.md；旧“新 Turn 发布 hook”记录作废。

最新 Legacy 插件冷恢复验证：结果未知/已提交/反馈已追加三窗口重建 Runtime，
原路径下不重复执行/输入/反馈；目录变化后账本拒绝身份不一致，不自动重放。
联合 276 passed（a1b26d），静态通过。本轮增加测试而未改生产逻辑；热刷新、
更多事件及 CLI 审批仍开放，详见 plugin-hook-runtime-audit.md。

最新 Legacy 插件同步 Stop 已接入：五种来源的不可变快照、安装身份、模板哈希与
执行环境分离，随 graph 的新 Turn 插件视图发布。真实 Runtime 有/无批准 × 五种
来源通过，联合 298 passed（257d4a），静态通过。插件专属刷新/冷恢复、完整
事件与 CLI 审批仍未完成；证据及夹具错误说明见 plugin-hook-runtime-audit.md。

最新插件 hook 源码审计：参考默认路径只执行 Legacy 插件 hook，新 AgentPlugin
明确返回空来源，不能把 overlay 元数据校验误当执行授权。Legacy 来源在 Corki
manifest 返回时丢失，且需接入新 Turn 的 graph 快照发布；完整修复与验收方案见
plugin-hook-runtime-audit.md。本轮此项为审计进展，未宣称插件执行已经实现。

最新 Stop hooks.json 已从配置文件快照接入真实 Runtime：目录去重、JSON 在
TOML 前执行，用户授权独立于定义来源，禁用项目不执行，坏 JSON 不丢 TOML。
冷加载更改命令会失去旧批准，不影响已捕获快照。联合 128 passed（476440），
详见 stop-hook-execution-plan.md。插件来源、SessionFlags 和 CLI 审批仍开放。

最新 Stop hook 平台字段：修复 POSIX 错误拒绝 commandWindows/command_windows，
按平台选定命令后再校验/计算指纹。真实 Runtime 的 POSIX 执行与模拟平台选择
先复现五项失败，修复后联合 56 passed（837de8），静态通过。Windows 实际进程
隔离未实现、不算完成；完整来源与 CLI 审批仍开放，见 stop-hook-execution-plan.md。

最新 Stop hook 用户状态合并：已修复 hash-only 覆盖丢失 enabled=false 的
权限边界问题，按字段合并并独立读取禁用用户层的状态；项目层无授权写权限。
五个反例先失败后通过；联合 hook/配置/项目信任 146 passed、1 skipped
（4b0728），静态通过。SessionFlags 和完整来源/CLI 审批仍开放，见执行方案。

最新 Stop hook 配置 key 已对齐原生 pathname 格式，旧 file: 授权仅在新 key
不存在时兼容读取；新禁用/空授权/修改不能被旧批准绕过。账本与反馈 ID 保留
旧执行身份，真实冷恢复切换 key 不重放命令。新增七个反例先失败后通过，
联合 122 passed（7a0989），静态通过。完整来源、状态合并及 CLI 审批仍开放，
详见 stop-hook-execution-plan.md；后文“key 未完成”为此前阶段记录。

最新 Stop hook 指纹修复：按原生 event_name/hooks 包装、类型/默认规范化、
紧凑规范 JSON 计算哈希；离线 Rust 三向量核对，六项反例修复。真实 Runtime
验证等价默认值不丢授权、旧指纹不自动授予新授权。联合 34 passed（20d533），
静态通过（5e1789）；来源 key/完整配置与授权 UI 仍未完成，详见执行方案。

最新 Stop hook 冷恢复：真实 graph 在结果未知/结果提交/反馈追加三窗口中断，
cold Runtime 未重跑旧命令，已知结果继续、未知结果失败；无重复输入/反馈。
联合 98 passed（21b5e7），静态通过（f06dc3）；本轮只新增验证，详见
stop-hook-execution-plan.md。其余 hook 来源/CLI 审批等仍未完成。

最新 Stop hook：本地 TOML 同步命令已接实际 _finalize，独立批准后可反馈
block 提示并继续，预算/新输入仍走原判断；超时/取消清理、独立执行记录接入。
发现并修复反馈误撤销，相关 79 passed（ffe292），静态通过（a87ef3）。
完整来源/审批 UI/跨平台/冷恢复验收仍未完成，见 stop-hook-execution-plan.md。

最新 Stop hook 实施中：SQLite/RAM 会话新增独立执行记录，领取与结果提交
具有身份约束、未知不重放、完成不可改写，不混入工具 Observation。相关
142 passed（f63f48），静态通过（237def）。执行器与 Runtime hook 仍未接入，
不是功能完成；详见 stop-hook-execution-plan.md，下一步继续同一接入批次。

最新 Stop hook 实施前审计：单独的规范化哈希信任、仅用户/宿主可写授权、
同步结果控制、超时进程组清理和错误语义已追踪；确认可复用逐层配置快照，
不能从项目合并字典读取批准。接入顺序见 stop-hook-execution-plan.md。
现有配置/元数据 83 passed、0 skipped（681a76），执行能力仍缺失，未改生产代码。

最新 A2/A4 收敛：终态/继续/默认及显式预算/结束窗口输入联合 108 passed
（195f99）；索引已去掉过时的“预算专项待收敛”。同时确认配置 Stop hook
尚无执行与结束控制，仅有元数据校验，A4 保持部分一致。详见
turn-decision-acceptance.md；本轮仅审计和回归，下一批先追踪本地信任/执行方案。

最新 B6/A8 补验：搜索结果落库后中断，冷启动遇到同名新定义，旧结果保留
归档但不能加载；重新搜索后只执行新工具一次，无重复输入/结果或误触发压缩。
搜索/缓存/执行策略/压缩联合 56 passed（47e5fd），静态通过（c8a69c）。
仅新增恢复场景测试，详见 tool-search-ranking-review.md；完整 A–F 未完成。

最新 F1/F6 补验：实际生成计划表格时 Ctrl+T 进入历史，继续输出新行并实时
刷新，q 返回保留草稿，Ctrl+C/stop 取消清除预览且不持久化半截正文。
40/100 列与既有历史/渲染/缓存联合 38 passed（882c9f），静态通过
（2f1e1b）。只补测试；详见 plan-history-tail.md，针对性键盘缺证现已补齐。

最新 F1/F6 修复：历史面板现组合计划表格的只读临时尾部，动态刷新且取消
撤销，不 drain 排队行、不写入已提交历史。四例先红后绿；CLI/真实 Runtime
联合 420 passed（a24630），静态通过（726a5e）。详见 plan-history-tail.md；
完整视觉和针对性历史切换 PTY 仍需验证，不据此关闭整个 F/A–F。

最新 F / 组合回归：完整 tests/e2e 已退出 0，166 passed、0 skipped，
290.12 秒（530bad）；覆盖真实 PTY 交互及三个核心示例。静态通过
（6c6ac6），本批未改生产/测试。详见 core-baseline-refresh.md；
不将终端测试全绿等同完整视觉一致，也不据此关闭整个 A–F。

最新 D6/E7 补验：1/2 个直接记忆 pass × close/重复取消/同时发生六组合，
等待 claim 清理后退出，无提前关闭/残留任务；相关 72 passed（460fb2），
静态通过（98f0f8）。仅增强测试，详见 memory-close-recheck.md。

最新 D6/E7 修复：直接 run_once 原未登记，关闭会先关模型；现改为服务拥有
独立 pass，关闭/调用方取消均 join 后再释放依赖，start 保留原覆盖语义。
真实 SQLite 挂起反例先红后绿，相关 120 passed（aa1039），静态通过
（d594b4）。详见 memory-close-recheck.md，完整 A–F 仍未完成。

最新 D6/E：取消记忆结果等待者不会取消后台提取；新增六组合仍完成合并、
summary/search/read/citation 和反馈持久化。与 ownership/shutdown 联合
66 passed（c8a117），静态通过（6660e3）；只增强测试，详见
memory-close-recheck.md。完整 D/E 与 A–F 尚未完成。

最新 C2：真实插件配置重载的 192 组合已增加独立指导检查；联合新旧片段/
两普通接口自动压缩 201 passed（0224e1），静态通过（d0a16e）。指导与目录
混合这一差异已修复并补证，下一批回到其他核心项；不因此关闭整个 C2/A–F。
完整证据与边界见 plugin-guidance-state.md。

最新 C2/C4：两普通 HTTP 接口 × pre/mid-turn 自动压缩四场景，插件指导
单份、工具对完整、摘要走配置端点、冷恢复不重采样/执行工具，相关
20 passed（12fcfd），静态通过（b33408）。只新增测试，详见
plugin-guidance-state.md；压缩故障窗口/真实磁盘重载交叉仍未由此证明。

最新 C2/C4：插件指导实际手动压缩 → 普通下一 Turn → 冷恢复补验，按当前
可用性重新注入且单窗口不重复，旧归档保留。相关 24 passed（b19b77），
静态通过（f561c7）；本批只扩展测试。自动压缩/wire/重载交叉仍待补验，
详见 plugin-guidance-state.md，完整 A–F 未完成。

最新 C2 兼容修复：识别有效旧混合插件指导，避免重复；旧整片撤销后则重新
提供。新目录通知仅撤销能力清单、不撤销通用用法。新迁移反例先红后绿，
相关 259 passed（74427a），静态通过（e4c506）；普通压缩/wire/重载交叉
仍待补验，详见 plugin-guidance-state.md。完整 A–F 未完成。

最新 C2 实施：插件通用说明与动态目录已分离并接入 world_state；新 Runtime
目录变化/撤销/冷恢复测试先红后绿，相关 421 + 30 passed（e029ba/add786），
静态通过。旧混合 catalog 迁移与实际压缩恢复仍未完成，详见
plugin-guidance-state.md 顶部；不以新片段路径通过宣称 C2 或 A–F 完成。

最新 C2 差异确认：插件通用说明与动态目录共用片段，目录变化会连带替换
固定说明，尚不具备原生独立指导状态。plugin-guidance-state.md 已记录真实
调用链、影响、旧历史/压缩兼容及修复验收；现有相关 211 passed（3beb71）
不证明此缺口已关闭。未修改生产/测试，下一批按该审计实施。

最新 C2：项目规则修改/删除在同 Runtime 保留创建快照，冷启动追加一次
替换/撤销通知，旧历史不变、后续 Turn 无重复；实际请求及冷恢复联合
33 passed（4ebe1f），静态通过（e7482e）。仅补测试，无新生产修复，
详见 context-snapshot-wire.md；其他上下文 section 与完整 A–F 仍待验收。

最新 A4/A5：三个结束窗口 × 默认/1/4 Step 九组合验证新输入与结束判断，
预算耗尽不误报完成、仅一次采样，已接收输入正常落库且不冒充取消草稿。
相关 47 passed（3dbee1），静态通过（baa09a）；只扩充测试，详见
default-loop-limits.md。完整 A–F 仍未完成。

最新 A2/A4：显式 max_steps=1 的 batch/streamed × 终答/继续/工具六例补验，
正常终答完成，其余失败、无工具副作用、单采样/单终态且关闭模型。相关
33 passed（a356a8），静态通过；只新增测试，详见 default-loop-limits.md。
下方完整回归发生在新增这六例之前；不将其算入旧全量数量，A–F 仍未完成。

最新全量回归已结束：session 72348 退出 0，13574 passed、1 skipped，
1954.44 秒（13c4ff）。唯一 skip 是文件系统拒绝非 UTF-8 文件名；该环境
分支仍未验证。覆盖 unit/integration，启用当前及五份真实旧 compiler，
不包含 tests/e2e。运行期间未改生产/测试；完整 A–F 仍未完成。
不再轮询该已结束句柄，详情见 core-baseline-refresh.md。

最新 A/C 组合：默认长任务第 26 次采样触发普通摘要，继续至第 28 次完成；
当前输入唯一、工具对完整、原始归档及保留副本来源验证通过。相关 166 passed，
最后来源增强后专项 6 passed，静态通过；见 default-loop-limits.md。
不证明真实模型摘要质量或同轮压缩/崩溃全部交叉窗口，A–F 未完成。

最新 A4/A7/A8：新增超过旧 24 Step 上限后的模型提交/工具结果挂起恢复，
六个 Runtime/CLI × batch/streamed/挂起组合确认前缀、调用结果不重复；
相关 32 passed（24cd8e），静态通过（d47cbc）。是实际 graph/checkpoint
故障注入，不是物理强杀，详细边界见 default-loop-limits.md。A–F 未完成。

最新 A4/A7/A8 补验：默认长任务完成后冷启动不重新采样/执行工具，后续 Turn
请求保留旧正文/调用/结果。普通/Code Mode × batch/streamed 四场景扩展，
与恢复/搜索压缩/记忆链路联合 40 passed（5a0ce9），静态通过（3b8f52）。
不冒称执行中崩溃长任务全部验证；详见 default-loop-limits.md，A–F 未完成。

最新 A2/A4 修复：默认不设 Step/工具调用次数上限，显式预算继续生效；配置
生成、evaluator、graph recursion、Code Mode 两入口均已接入。新长任务及
Code Mode 24 passed，原预算/配置/恢复组 719 passed、1 环境 skip。详情见
default-loop-limits.md 顶部，完整 A–F 未完成。

最新 A2/A4 审计发现：默认 24 Step/64 工具硬上限与原生普通循环不一致，
影响 evaluator、LangGraph recursion 和 Code Mode；尚未修复。已记录完整
修复入口及验收于 default-loop-limits.md，既有显式预算/恢复回归 81 passed
（38b96d），不将其当成默认长任务通过证据。下一批优先处理此核心差异。

最新跨模块补验：从已有审计找回五份真实旧 sandbox 编译器并核对 receipt，
补齐六项旧 metadata/FS/patch/approval/delta 拒绝契约，相关 109 passed、
1 skipped（d9d090）。剩余非 UTF-8 路径实测 EILSEQ，仍属未验证环境分支。
未修改生产或测试；不将局部补跑冒充最新全量。详见 core-baseline-refresh.md。

最新 F/A/E：真实 PTY 暴露计划预览外层容器仍只检查正文，四例修复前超时，
已改为按实际 fragments 显示。40/100 列 × Ctrl+C 或 /stop 验证模型完成前
可见、取消清理且不入历史；联合 401 passed（9f837d），静态通过（263b2f）。
历史面板尾部同步、屏幕逐格擦除和复杂交错仍待验证；完整 A–F 未完成。

最新 F：计划表格 mutable tail 已接输入区临时预览，保持正文优先级、半行
不可见、队列不越过及中断撤销，不写入 Transcript。四例宽度/前缀测试及
相关联合 409 passed（04fdb1），静态通过（a6e76a）。计划尾部物理 PTY、
历史面板尾部同步及复杂交错仍需验证；完整 A–F 未完成，见流式审计顶部。

最新 A/F 修复：活动历史/重绘不再重放计划 append 并提前显示待提交行，
改用已提交 fragment；历史缓存跟踪提交进度。新增测试修复前失败（ed172c），
相关 CLI/Runtime/PTY 联合 405 passed（d6f55b），静态通过（33464b）。
完整计划仍保留 source 重排；mutable tail、复杂交错等缺口尚未关闭。
详见 stream-animation-design.md 顶部，完整 A–F 未完成。

最新 A/E/F 验证：未完成计划失败/模型侧取消/重试 × 已提交行有无六组合，
真实 Runtime → Application → TerminalUI/SQLite 验证终态、取消传播、草稿
不入模型历史、重绘不补隐藏行及任务退出。相关联合 78 passed（1f9b2b），
静态通过（09e415）；本批只补测试，未改生产代码。物理信号/消费端取消、
复杂交错和完整 A–F 仍开放，详见 stream-animation-design.md 顶部。

最新 A/F：计划流式正文队列已接入 TerminalUI，与正文共用压力策略及稳定
动画时钟。模型等待时提交/双队列压力/中断不补隐藏行/真实 PTY 流中可见后
完成均验证；联合 466 passed（29d4d0），原表格/修复 PTY 18 passed（95135a），
静态通过（6a896d），无活动测试。计划 mutable tail、复杂交错及完整故障矩阵
仍开放；详见 stream-animation-design.md，完整 A–F 未完成。

最新 F：完成计划已使用独立 source-backed 渲染，标题/缩进/padding/空计划
与原生未知背景路径对齐。40/100 快照及真实 PTY resize 保留草稿通过，联合
444 passed、2 forkpty 警告（071e2a），静态通过（9a24a6）。独立流式队列
尚未接入，完整 A–F 未完成；见 stream-animation-design.md。

最新 A3/F：最终计划普通正文解析、本地事件与完成后 CLI/历史显示已接入。
真实 Runtime 红绿及最终联合 443 passed（8613ab），静态通过（604968），
无活动测试。仍缺独立流式计划显示、双队列与故障/PTY 完整矩阵；详见
stream-animation-design.md。只完成本批接入，不宣称整个最终计划或 A–F 完成。

整体基线已退出：13479 passed、1 failed、7 skipped（61fb44）。失败为并行
测试过严的启动顺序断言，已修正并增加强制反序启动场景。七项 skip 原因待核实。
同时修复完整/分页 DisplayTurn 丢失入场模式，新增两例真实 Runtime 红绿证据；
联合补跑 216 passed（9a8039），静态通过（bb04d8），无活动测试进程。
详见 core-baseline-refresh.md。这不是完整基线一次全绿；proposed_plan 的
后续接入状态见顶部，完整 A–F 未完成。

最新 A5/E1/E7：新增 batch/streamed 普通错误不取消兄弟工具、独占屏障和调用序
回灌组合，并扩展 streamed claim/complete 存储故障清理。联合 89 passed
（30c848），静态通过（b36d84）；见 parallel-error-barrier-review.md。本轮
未改生产逻辑，完整 A–F 未完成。

最新 A7/A8/E5：补两普通接口 partial tool 边界的真实请求组合：Responses 已提交
item 的工具结果进入重试，Chat 未终结 delta 不提前执行；冷历史前缀不变，工具
只执行一次。新 4 例及相关联合 78 passed（6bb4e1），静态通过（6d2ab0），
见 partial-tool-transport-review.md。本轮未改生产逻辑，完整 A–F 未完成。

最新 E3/E6：非 2xx 正文读取/自动关闭/finally 关闭的普通异常不再覆盖 HTTP
状态或取消。新 54 例矩阵修复前 30 failed，修复后联合 206 passed（fd0c95），
静态通过（f11f38）；详见 model-stream-close-review.md，完整 A–F 未完成。

最新 A/E：回到主循环错误边界，修复 HTTP 响应关闭异常覆盖原始断流错误。
实际工具已执行后断流+关闭失败新增红绿证据，重试继续使用 Observation 历史、
工具不重复执行；参见 model-stream-close-review.md。完整 A–F 仍未完成。

最新 C1/D4：内置记忆/技能的来源分类及条件排序已修复，使用显式 PromptPhase，
技能有完整权限时插入权限前，否则位于普通工具目录后。真实请求修复前
18 failed，最终相关 1140 passed（b0017e），静态通过（dde162）；详见
context-snapshot-wire.md。其他来源和完整 A–F 仍未完成。

最新 C1：初始宿主 developer 扩展/模式排序已修复，初始及压缩重建使用
临时来源元数据，不改变增量邻接、工具目录或旧消息组。真实请求修复前
9 failed，修复后最终相关 437 passed（09e8eb）；边界和证据见
context-snapshot-wire.md。其他上下文来源继续审计，完整 A–F 未完成。

最新 B/E：通用 MCP OAuth 刷新与 deferred 搜索/定义加载/read Observation/
冷恢复已在同一真实 Runtime 链路验证，不再只有分开验证。64 例矩阵及最终
相关联合 287 通过（883eed），静态通过（501f44），无活动测试。细节及脚本
模型限制见 provider-isolation-refresh.md；完整 A–F 未完成。

最新 B/C/E：回到普通模型/搜索/压缩/认证隔离。搜索 provider/地址/模型交叉
48 通过，与 OAuth/旧响应/回放联合 249 通过，随后扩大压缩拒绝矩阵 144
通过；静态通过，无活动测试。provider-isolation-refresh.md 明确分批及测试
能力边界。本轮不改生产协议，完整 A–F 和同步嵌入限制仍未完成。

最新 F/A/E：真实 PTY 在启动回滚挂起时发送单次 Ctrl+C，四种宽度/原失败组合
均等待插件关闭后以 130 退出。CLI 联合 403 通过（e0ed63），静态通过
（d61d43），无活动测试。证据范围及重复信号未验收说明见 mcp-startup-review.md；
同步嵌入等待及整体 A–F 保持未完成。

最新 A/E：Runtime.__init__ 末端普通错误/取消 × 持久/临时四组合证明惰性
内部组件未开启任务/锁/notes/checkpoint，临时内存连接已关闭。联合 86 通过
（befeca），静态通过（612d42），无活动测试。本轮无需增加内部组件清理；
同步嵌入等待/启动物理取消仍开放，见 mcp-startup-review.md，A–F 未完成。

最新 A/B/E：借入工具注册表在 Runtime 构造失败时立即恢复旧状态，异步资源
清理不再留下可调用的失败插件，也不覆盖宿主稍后新增的工具。核心组合 380
通过（70d29b），静态通过（92c6b1），无活动测试。见 mcp-startup-review.md；
同步资源释放/cls 内部分配/启动物理取消仍待处理，整体 A–F 未完成。

最新 A/E/F：生产 CLI main 已等待构造失败清理，复用同一事件循环；完整 Runtime
接管 Application 构造故障。联合 437（ddbc0c）与最后五场景专项（81851b）
通过，静态通过，无活动测试。同步嵌入入口、物理启动取消和后期注册回滚仍开放，
详见 mcp-startup-review.md；不再把生产 CLI 描述为尚未接等待入口，A–F 未完成。

最新 A/D/E：acreate 在失败时等待已登记自建资源清理，接入真实 memory agent，
不关闭构造尚未成功时的宿主传入模型。错误及取消清理矩阵/记忆主链联合
286 项通过（e9402e），静态通过（3bc121），无活动测试。同步入口及宿主注册
回滚仍开放，具体限制见 mcp-startup-review.md；未增加官方服务路径，A–F 未完成。

最新 A/D/E：存储配置错误在插件执行前拒绝，避免已注册资源后再抛确定性错误。
新增真实 create 场景先红后绿，257 项及 48 项分批通过（c2c8f9、4735cd），
静态通过，无活动测试。mcp-startup-review.md 保留整体同步构造接管缺口及
实际调用者审计；未新增后台兜底或官方功能，完整 A–F 未完成。

最新 E 修复：可信 Python 插件模块在 exec 前交给本批次所有者，避免模块抛错
或入口不可调用时漏掉已定义 aclose。真实 Runtime 三例修复前失败，修复后相关
250 项及核心组合 95 项分别通过（e5274f、aee980），静态通过，无活动测试。
详见 mcp-startup-review.md；同步构造中断整体接管及完整 A–F 仍开放。

最新 F 定时补证：重复内容不推迟首 tick、阻塞后不补发旧 tick、替换及停止重启
使用新 deadline，均由实际事件包装器的确定性测试验证。CLI/Runtime 补跑
386 通过（f4baf0）；扩大批次 62 个真实 PTY 通过，但该批次另有三个修正前
测试入口失败，不视作整批全绿。详细范围见 stream-animation-design.md。
静态通过，无活动测试；未修改生产协议或加入官方功能，完整 A–F 仍未完成。

最新 F 动画已接主路径：TTY/live 中事件读取与定时出队分离，Smooth 单行、
CatchUp 排积压，所有终态收回队列/读取任务。真实模型等待/突发与流式 PTY
已通过；联合 402（9a0a57），最后新增参数专项 17（50f9c2），静态通过
（c254ad）。无活动测试；详见 stream-animation-design.md，精确时序及完整
A–F 验收仍开放，不把主路径接入等同所有完成。

最新 F 动画前置状态接入：真实 StreamMarkdown.write 使用稳定渲染行队列，
支持分批出队/队首年龄/积压 resize。CLI 与 Runtime 清理 379 通过（7dd269），
流式 PTY 20 通过（ccc138），静态通过（fc40a6）。当前仍立即排空，定时动画
与 CatchUp 尚未接入；见 stream-animation-design.md。无活动测试，A–F 未完成。

最新 E/F 修复：输出写入失败后重绘再次失败不再盖住首因；保留写入异常并附带
恢复失败链，取消仍优先传播，不重试未知写入。真实 Runtime 先红后绿，CLI
联合 376 通过（f9b7a6），交错工具/表格/修复 PTY 20 通过（601dd2），静态
通过（713fe8）。无活动测试；原生动画队列与完整 A–F 仍未完成。

最新 E/F 流式提交补证：更新/write/flush/redraw 单点故障与重复取消，及真实
Runtime 写入/刷新/重绘失败清理均已验证。CLI 联合 374 通过（a285ce），静态
通过（94aa33），无生产修改或活动测试。原生 commit_tick 的动画调度不等于
Corki stream_commit 输出锁定；该差异仍开放，见 cli-streaming.md，A–F 未完成。

最新 A/C/F Plan 活跃验证：Default/Plan 两模式的实际请求上下文、Shift+Tab
禁用、排队/停止/手动恢复 32 项真实 PTY 通过（871996），静态通过（305d07）。
无生产改动或活动测试。另确认流式输出已接 stream_commit，旧审计不能据此
重复实现；下一步核验其顺序/取消分支。完整 A–F 未完成。

最新 F 物理 Ctrl+C 提交窗口：真实 SIGINT 命中持久化挂起，成功/失败两分支
均等待写入后按实际模式恢复界面，正文保留、自动发送暂停、零模型采样。
40/100 列四例通过；CLI 联合 373 通过（54bbd1，12 条 forkpty 警告），静态
通过（1975b8）。无生产改动或活动测试，完整目标未完成；见 cli-mode-entry-review.md。

最新 F 运行键盘补证：真实模型挂起时 Shift+Tab 不切换模式，随后 Tab 入队，
Ctrl+C 或 /stop 后完整恢复并由 Enter 手动提交；40/100 列及待消费 steer
组合含对照 16 通过（3f90ec），静态通过（82588c），无生产改动或活动测试。
见 cli-mode-entry-review.md；Plan 运行及设置提交中的物理取消等矩阵仍开放。

最新 F 模式提示：已按按键同一可用性条件显示 shift+tab to cycle，闲时窄窗
退为模式名，状态变更重绘。CLI 组合 369 通过（e6b00a），额外真实提示文字
PTY 8 通过（46f977，40/100 列），静态通过（be922b）；无活动测试。详见
cli-mode-entry-review.md；整体 footer 布局、物理取消/运行按键与 A–F 仍开放。

最新 E 并发撤销修复及跨模块回归：候选复用同一清理 manager，第二个 abort
不再在首个关闭完成前返回。先红后绿（92e8a5），插件/关闭与搜索、压缩、记忆、
恢复六文件组合共 342 通过（1707d6，0 跳过），静态通过（6fc3d5）。已更正
验收索引中过期的“Plan 缺失”，保留实际未完成交互；无活动测试，A–F 未完成。

最新 E 修复：候选撤销时的模块关闭由 manager 单一任务持有，父任务重复取消
不再打断钩子并提前返回。真实 Runtime 故障测试先红后绿；相关 245 通过
（2d65c6），配置重载/关闭补充 12 通过（029562），静态通过（eb7e11）。
取消仍传播，旧工具不替换，无活动测试。见 mcp-startup-review.md；同步初始
构造与完整 A–F 仍未完成，不保证强制终止不合作的可信 Python 钩子。

最新 E 修复：Python 插件 register 取消时未返回可 abort 候选，新模块原先失去
关闭所有权；现交回存活 manager，Runtime 关闭会清理失败模块及先前新兄弟。
真实 Runtime 测试先红后绿（fab8fb → 17b62f），相关 244 通过、0 跳过，全部
静态通过（4575ff）。原取消继续传播，旧定义/handler 不变；同步初始构造失败的
异步资源接管仍开放。见 mcp-startup-review.md，无活动测试，完整目标未完成。

最新 B/E 扩展注册：补测失败替换隔离、旧 Step handler 可用、健康包不重复注册
及修复后新结果发布。注册/重载三文件 221 通过、0 跳过（ffc247），静态通过
（3dd021），无生产改动或活动测试。原生 discard 仅为历史写入器清理，不能
据此证明 MCP 总关闭；详细调用链与限制见 mcp-startup-review.md。A–F 未完成。

最新 A/B/E MCP 启动：补强 required 失败后的工具注册隔离与健康连接复用断言，
四文件联合 45 通过、0 跳过（0a0b9e），静态全部通过（161667）。已确认原生
Session 构造失败与 Corki 同 Runtime 重试的生命周期区别，不宣称完全相同；
详见 mcp-startup-review.md。无生产改动，无活动测试，完整 A–F 仍未完成。

最新 D/E 广回归：记忆单元及 test_memory*.py 集成 788 通过、0 跳过（5ede60，
204.10 秒）；补充 Thread/临时会话/MCP/引用/Runtime 关闭七文件 120 通过、
0 跳过（766d53）。两个进程均退出 0，无活动测试；全部静态通过（e929e9）。
见 memory-regression-current.md。无生产修改，不能以全绿宣称所有 D 或 A–F
完成；已知内容过滤/启发式脱敏也不是对任意秘密或提示注入的保证。

最新 D/E skills 发布中断：真实兼容输出在移除旧 skill 后、新 SKILL.md 写入前
故障，两种来源变化均确认 DB failed、基线和水位不提前确认；冷 Runtime 重试
补齐新 skill，后续 no-diff 与旧事实移除通过。39 项联合通过（ada0be，0 跳过），
全部静态通过（170475），无活动测试。本轮仅补测试；见 memory-publication-review.md，
不宣称整组文件原子替换，也不把本批覆盖等同所有 D/E 或 A–F 完成。

最新 D/E 兼容输出发布中断：来源 delete/modify 两例均注入详情成功、摘要失败，
证实混合文件版本会保留但任务报告 failed、旧成功基线不变；冷 Runtime 重试仍
获取来源变更证据并修复输出，后续 no-diff 及当前摘要召回通过。初次错误的水位
增长断言已按扩展来源语义纠正，没有生产改动。三文件联合 37 通过（29f560，
0 跳过），静态通过（9161f2），无活动测试。见 memory-publication-review.md；
不承诺跨文件原子事务或掉电安全，skills 同步故障与完整 A–F 仍待验收。

最新 D/E 冷 Runtime 重建：DB success 故障后完整关闭旧 Runtime，再建新连接/
Pipeline/记忆模型，下一 Turn no-diff 补齐状态和水位，新模型零采样。37 项通过
（ac3c13，0 跳过），静态通过（3dba87），无活动测试；这不是 OS 进程重启测试。
调用链还确认暂存 helper 非主 Pipeline 默认，但 JSON 兼容输出逐文件发布真实
可达，下一优先核验其写入中断。见 memory-publication-review.md，完整目标未完成。

最新 D/E 发布失败重试：共享文件与 Git 基线已成功、DB success 失败的场景，
移除故障并仅推进测试重试时钟后，真实下一 Turn 后台执行 no-diff 收敛。状态成功、
水位追平、owner 清空，模型调用数仍为 2，文件/基线不变。36 项组合通过
（3bc25a，0 跳过），静态全部通过（c44b51），无活动测试。本轮无生产修改；
证据见 memory-publication-review.md，不覆盖冷进程或暂存逐文件中断，A–F 未完成。

最新 D/E 共享记忆发布：原生先提交 Git 基线后标 DB success，失败不撤销已经
执行的文件编辑。Corki 新增基线失败、基线成功后 DB success 失败两项实际子
Runtime 故障测试，验证 failed、完成水位不推进、owner 释放及文件/基线真实状态。
联合 34 通过（0dbc15，0 跳过），全部静态通过（f8540e，1125 文件/77 包），
无活动测试。见 memory-publication-review.md；本轮无生产修复，不自造跨文件/
数据库原子性承诺。后续 retry/no-diff 和暂存发布中断仍待验收，整体 A–F 未完成。

最新 A–F 总清单回查：模式改动后重跑六文件跨模块组合，95 通过、0 跳过
（c190b9，18.44 秒），没有活动进程；已更新 acceptance-index.md 的当前入口。
本轮无生产修改，不将旧 audit 中过期 live/缺失条目当作当前事实。下一优先核验
D2/D3/D6/E4 的共享/暂存记忆发布失败与成功基线关系，避免长期仅追加 CLI 小项。
已确认两分支的实际发布接点，但尚未证明缺陷或一致，需原生 phase2 与故障测试
对照。CLI 剩余交互保持开放，全目标不缩减、不宣称完成。

最新 A/E/F 模式循环故障验收：40/100 列返回 Default 失败后错误可见、Plan 保持、
草稿/输入历史不丢不污染；用户手动提交仅一次，实际模型请求仍为 Plan medium。
补全菜单的 Shift+Tab 禁用也由真实 PromptSession 验证。最终相关组合 343 通过
（62a892，8 条 forkpty 警告），全部静态通过（ed43c1，1125 文件/77 包），
无活动测试。本轮仅补测试，详见 cli-mode-entry-review.md；物理 Ctrl+C、运行中
真实键盘组合及提示仍待补齐，B/C/D 与 A–F 整体完成审计仍未完成。

最新 F Shift+Tab：已接入独立控制信号、闲时绑定和串行模式发布。保留输入草稿、
不写输入历史、不采样；Plan → Default 恢复同 UI 基础 model/effort。40/100 列
真实键盘往返通过，另有 PromptSession 的 idle/running/history/modal 禁用检查。
CLI 单元、模式/冷恢复/问题集成与命令 PTY 组合 365 通过（4c82a8，6 条 forkpty
警告），静态检查通过（014710，1125 文件/77 包），无活动测试。见
cli-mode-entry-review.md。循环失败/取消直接 PTY、补全 popup 和实时 Turn 按键
组合矩阵及快捷键提示仍待补齐；全 A–F 目标保持 active，未宣称最终完成。

最新 A/F 冷恢复模式：源码确认同 TUI 缓存基础值与冷 resume 不同，后者以恢复的
model/effort 启动 Default；不需要永久保存进入 Plan 前的旧 Default 值。已修正
CLI for_directory 冷恢复错误继承旧 Plan/自定义模式指令的行为，保留 provider/
model/effort，已入场 checkpoint 的 Plan 不变。两种普通传输真实 build_application
测试先红后绿（d7bd68 → 4d4b43）；配置/恢复/模式/CLI/PTY 653 通过、1 跳过、
4 forkpty 警告，全部静态检查通过（c0fc07，1124 文件/77 包），无活动测试。
详见 cli-mode-entry-review.md。Shift+Tab 循环与闲时焦点矩阵尚待实现；整体 A–F
仍未完成。本批修正了旧审计推断，不能继续将“永久保存 Default”当作未完成要求。

最新 A/F `/plan` 设置发布取消：修复尚未入场正文丢失，以及已提交 Plan 但 UI
仍为 Default 的窗口。取消继续传播；等待 Runtime 的写入结束后按实际快照同步
显示，原命令保留并暂停自动重发、不采样。挂起提交成功/失败两例先红后绿
（24468e → 2545d1）；CLI/Thread 设置/真实命令 PTY 组合 352 通过、4 条
forkpty 警告；全部静态检查通过（084127，1124 文件/77 包），无活动测试。
见 cli-mode-entry-review.md。未跑物理 Ctrl+C 故障 PTY；Shift+Tab 模式循环、
冷恢复基础值及整体 A–F 仍未完成，不以本批替代整体验收。

最新 F 直接 `/plan` PTY：40/100 列成功与设置写入失败四例通过，真实键入裸命令
零采样、粘贴多行正文单次提交、成功发布后显示 Plan、失败不显示且保留输入。
测试端补 CPR 响应修复原夹具隐藏 toolbar 的问题，无生产改动。CLI/模式/问题/
PTY 组合 339 通过（26c43d，6 条 forkpty 警告），全部静态检查通过（224bab，
1124 文件格式、77 包）。测试进程已退出，无活动测试。见 cli-mode-entry-review.md。
下一缺口仍是 idle/no-modal 的 Shift+Tab 循环、Default 基础值冷恢复与发布取消；
A–F 整体保持未完成，以上证据不替代全仓当前版本验收。

最新 F CLI /plan：已接入裸命令不采样、inline 正文原样单次提交、发布后更新模式
显示、本地 Medium/effort 覆盖与运行中拒绝（不 steer）。提交失败保留命令队列并
暂停自动重发。真实 Application/Runtime 与全部 unit CLI 等组合 335 通过
（bad06d，2 条问题 PTY forkpty 警告），Ruff/1123 文件格式/compileall/77 包检查
通过（a129e0），无活动测试。见 cli-mode-entry-review.md。返回 Default 快捷键、
冷恢复基础值、发布取消窗口及直接键入 /plan 的 PTY 仍待实现/验收，A–F 未完成。

最新 CLI 模式入口审计：确认 `/plan` 非 toggle、运行中不可用、inline 正文需在
设置发布后提交，内置 Plan reasoning 为 Medium，Default 另有基础设置。已记录
cli-mode-entry-review.md；下一步不能仅添加命令字符串而遗漏 effort/输入所有权。
本轮无生产修改、无新增测试；新证据改变了实现方案，CLI 入口仍未完成，A–F 仍 active。

最新 F 多问题确认：修复确认层 j/k 和 Backspace 无效、用户想返回补答却提交空
答案的差异。四条真实 PromptSession 多问题序列先红后绿，包含跳题未提交和 notes
跨题保留；问题/Runtime/CLI/40、100 列 PTY 组合 32 通过（fce6c9，2 条 forkpty
警告），静态 Ruff/1122 文件格式/compileall/77 包兼容检查通过，无活动测试。
详见 question-panel-review.md；复杂布局、结果历史回放、CLI 模式入口及整体 A–F
仍未完成，不以这四条交互覆盖全部多问题按键或整体 CLI。

最新 F/B CLI 提问批次：UserInputRequested 已被 CLI 消费并打开独立问题面板；
真实 function call→面板→显式选择→模型 Observation 已贯通。面板与普通 reader
互斥，草稿保留；终态/工具完成可关闭面板，旧请求不能中断后续 Turn。
Default 非阻塞问题的 60+60 秒无交互空回答策略来自原生 TUI，Plan 不自动结束，
任何按键禁用该计时器，不会自动选首项。全部 unit CLI 及范围内 Runtime/输入所有权/
计划与问题 PTY 组合 354 通过（bb1237，5 条 forkpty 警告），exit 0，18.44s；
Ruff、1122 文件格式、compileall、77 包兼容检查通过（aad70e），无活动测试。
见 question-panel-review.md；多问题/复杂布局/结果回放/CLI 模式入口等仍需完善，
整个 A–F 未完成。下方“CLI 尚未消费”仅为旧批次历史状态。

最新 A/B/E 提问批次：普通 request_user_input 已注册并接入真实 Runtime，默认仅
Plan 可用，可显式开启 Default；DIRECT_MODEL_ONLY 保证 code_mode_only 的普通
请求能直接调用，但 nested 工具不可调用。问题等待真实回应，严格绑定当前 Turn/
call ID，错位/重复/迟到回应不生效；取消不被吞掉，回应不进入普通输入或审批。
组合回归 1591 通过、1 跳过（9ca359：文件系统不支持非 UTF-8 文件名），exit 0；
Ruff、1118 文件格式、compileall、77 包检查通过（8868f7）。无活动测试。
CLI 尚未消费新问题事件：下一批必须接入问题面板及焦点/取消/PTY，当前仅宿主 API
可回应，不能把 Runtime 通过说成终端提问已完成。详见 collaboration-mode-review.md。

最新 A/B/C/F 模式批次：模式已接入 Thread 完整替换、Turn/Step 持久快照、普通
上下文、本地 Plan 模板、direct/Code Mode 的 update_plan 拒绝边界与 SQLite 旧表
迁移。当前 Turn API 不提供原生也不支持的模式切换；未来默认与当前任务独立。
真实 checkpoint 冷恢复及交错模型切换有验证。组合回归 1238 通过、1 跳过
（3a5533：文件系统不支持非 UTF-8 文件名），3 条 forkpty 警告；Ruff、1113 文件
格式、compileall、77 包检查通过（28c1e7），无活动测试。详见 collaboration-mode-review.md。
request_user_input、CLI 模式入口/问题面板及其取消/焦点链路仍未完成，整体 A–F 未完成。

最新 B/F 计划解释：工具不再提前 trim 或将空字符串变成 None；原始解释进入
PlanUpdated 与持久结果，CLI 仍仅在展示时 trim。direct/Code Mode 的缺省、空串、
纯空白、带空白正文组合先红后绿；Runtime/存储/渲染/PTY/执行器 42 通过
（d3cd71，3 条 forkpty 警告），Ruff/1111 文件格式/compileall/77 包检查通过。
Plan 模式、request_user_input 仍缺失，完整 A–F 未完成。

最新 B/F 计划结果：普通 update_plan 固定确认、Code Mode 空对象与解释事件分离，
修复之前的解释回灌/嵌套字符串差异。四实际调用组合先红后绿，Code Mode生命周期/
工具/PTY 联合43通过（02e77e，3forkpty警告），静态1111文件等通过；见
plan-wrapping-review.md。完整A–F未完成，Plan模式边界尚待核对，无活动测试。

最新 B/F 计划契约：原生 schema/serde handler 允许空计划、空步骤及多个 active，
single-active 仅模型指导；已删除 Corki 多加的硬拒绝，保留类型/状态校验。三项真实
Runtime 先红后绿，含后续清空、结果回灌、持久历史冷读取，连同工具/渲染/PTY 41通过
（2a0129，3forkpty警告）。详见plan-wrapping-review.md；静态1111文件通过，无活动
测试。完整 A–F 仍未完成，不把此局部数据契约修复称为最终对齐。

最新 F1/F6：实际 Runtime PTY 暴露 planning.validate_plan 还会 strip 步骤；已保存
原文。40/100列缩放、新输出及旧计划重绘/草稿保留验证完成，CLI单位+PTY 309通过
（f48065，3forkpty警告），工具23通过（56c8d6），静态1110文件等通过。详见
plan-wrapping-review.md；下方“尚待PTY”被本节覆盖。另发现计划非空/单active
约束比原生handler严格，已记录待核对，完整A–F未完成；无活动测试。

最新 F1：修复计划含 URL 时首词前空白丢失，原生 adaptive wrapping 有保留缩进
测试依据；新增实际 TerminalUI 断言先红后绿，计划/转录/历史缓存 17 通过，详见
plan-wrapping-review.md。本批在全量终态之后修改生产，尚待 CLI 扩大及 PTY 验证，
不把之前 13142 通过视为此改动验收。完整 A–F 仍未完成。

最新全量终态：34854 已结束（f3f9e6，exit 0），13142 通过、7 跳过，2025.40 秒。
详见 full-regression-current.md 的最终结果及跳过表；下方所有活动进度为历史记录。
六项缺旧 compiler、一项当前 FS 不支持非 UTF-8 文件名，均不计为已验证。全项目
Ruff/1110 文件 format/compileall/77 包检查通过。该全量启动后的修改另有专项证据，
仍不能称全部最终源快照验收；完整 A–F 未完成，下一步继续关闭清单中的行为缺口。

最新 D2/D3/D6：原生真实 Turn 入口、当前 host/历史 source 区分和筛选复核见
memory-source-review.md。新增 compact/resume/入库失败后下一正常 Turn 仍启动的
连续状态断言，启动/ephemeral/来源/mode/claim 联合 77 通过（02d463），修改文件
静态与 diff 通过。无生产修改，不移植官方配额 guard。全量 34854 仍活动（30b510
约 47% 测试计数），不是整体目标完成百分比；完整 A–F 仍未完成。

最新 B2/B10：原生六种曝光与Corki模型/嵌套双入口核验；MCP掩码/候选路由矩阵378
通过（122139），强化真实cell隐藏调用不得发MCP请求、恢复后可调用，单项通过
（8e9bd6）。静态1110文件/77包等通过（919e75），详见tool-exposure-review.md。
无生产修改；全量34854仍活动（559395约26%测试计数），继续原句柄，完整A–F未完成。

最新 B3/B4/B7：重读原生搜索handler/cache/metadata与Corki真实路径，排序/分词/
缓存/Runtime refresh/按需链111通过（1978b2）；新增默认8/显式1/超过目录容量的
Top-K主循环验证，ranking22通过（b22149）。详见tool-search-ranking-review.md，
静态1110文件/77包等通过（d6fade）。无生产修改；全量34854仍活动（2d5afd约21%
测试计数），继续原句柄。完整A–F未完成，不以脚本模型结果声称真实选择质量。

最新 F3/F4/A/E：快捷键 actual TerminalUI→Application→Runtime→native 桥接20通过
（1999e1），覆盖direct/Code Mode的y/a/p范围、规则保存失败/冷加载及d/n不同终态
与无副作用。扩大执行取消/规则/审批84通过（69a8df），静态1110文件/77包等通过
（60dca6）。详见approval-scope-ui；本轮无生产修改。全量34854仍活动（f10bf3约13%
测试计数），继续原句柄，未作为完整最新快照验收；整个A–F仍未完成。

最新 F3/F4/F6：宿主执行列表接原生默认 y/a/p/d/n，按请求可用范围绑定；新增显式
BracketedPaste忽略，修复原粘贴进表单缓冲。6项先红，19单位及CLI/40/100列审批PTY
350通过（c57d6c/c7bc94），静态1110文件/77包等通过（392d33）。详见approval-scope-ui。
全量34854仍活动（8ada74约7%），采集早于此批，不冒称最新CLI完整快照验收；继续
同句柄。后续快捷键实际执行桥接及完整A–F仍未完成，不引入官方功能。

当前全量回归正在实际运行：session 34854，启用且核对 native source/binary receipt，
最后 poll 288d23 仍活动、输出增长，尚非通过证据。后续继续同句柄，不重启、不把旧
流水审计的活动状态作为依据。详见 full-regression-current.md；本轮只读核对了 F3
原生 y/a/p/d/n 与 options 绑定语义，下一批待实现。运行期间无生产/测试修改。

最新 A6/C8/E6：新增实际预算媒体投影 CPU 门控取消/重复取消/Runtime 关闭验证，
未 join 前不关闭模型、不采样、不完成；释放后单一取消终态且传播取消，非关闭分支
保留旧历史并可执行新 Turn。相关 54 通过（c47bec），静态 1110 文件/77 包等通过
（e9f0c8）。详见 context-budget-review；无生产修改、无活动测试，完整 A–F 未完成。

最新 C3/C4 媒体预算修复：本地估算及 usage 锚点后的增量内容按模型实际媒体投影计入，
不支持图片/音频时不再按原附件误触发压缩；归档不改写。两处分别先红后修复，扩大
553 通过（c76549），新增媒体 8 组合通过（640f6b）；详见 context-budget-review.md。
本批不改变媒体能力配置、普通协议或 OAuth；全格式附件与完整 A–F 尚未完成。

最新 C3/C4/E4 修复：供应商 usage 低于本地硬窗口估算时，原来跳过摘要后直接超限
失败；现在同一个本地硬上限也触发现有压缩恢复，未改 usage 自动阈值或 body-prefix
计数。四项先红，完整 context 与普通压缩/降窗/TokenBudget 联合 472 通过无跳过
（d1ba52）。原生源码确认输出余量使用比例配置，主 ResponsesApiRequest 没有独立
max_output_tokens；不再把缺独立精确输出参数当已证实差异。详见 context-budget-review。
完整 A–F 未完成，媒体成本组合仍待核验；无活动测试，未加入官方服务逻辑。

最新 C3：确认请求估算在媒体准备/上下文更新/工具解析后执行，压缩后重新计工具成本。
扩展真实 Runtime 大正文与大私有归档元数据对照，assistant/reasoning × 热/冷共 8 通过
（d0c639）；预算/普通压缩/降窗另 73 通过（56f373）。静态 1109 文件与 77 包等通过
（1a83d7）。详见 context-budget-review.md；比例窗口余量不冒称独立输出预留已完成。
无生产算法修改、无活动测试；下一步输出限制原生调用链与媒体成本组合，完整 A–F 未完成。

最新C1/C2：追踪原生AgentsMdManager缓存/信任失效、AGENTS typed diff和同role合并；
补两种普通HTTP请求的contributor角色迁移→撤销→冷恢复实际Runtime验证。相同key/text
变角色先撤销旧高优先级内容，删除只追加一次，canonical前缀保持。指令来源/managed
限制/provider生命周期/输入附着/world-state扩大55通过0跳过（bf4e9f），静态1109
文件/compileall/77包/diff通过（b1a471）。详见context-snapshot-wire.md。无生产修改、
无活动测试；环境/权限/目录各section的完整typed diff与全部C/A–F仍未由本批关闭。

最新A/E/F规则发布桥接：实际键盘选择rule后，direct/Code Mode × 成功/I/O失败验证
精确保存提议、live发布、无session缓存、后续与冷Runtime重新按规则判定。保存失败
遵循原生保留当前批准+Warning语义，不放行后续命令；40/100列PTY已扩展第三项rule。
键盘桥接/规则发布全部场景/审批单位与PTY134通过0跳过36警告（4c1ea3，73.67秒），
静态1108文件/compileall/77包/diff通过（07b49d）。详见approval-scope-ui.md，之前
“规则实际发布未由新列表验证”的阶段缺口被本批覆盖；没有活动测试，无生产策略修改。
下一批回到C1/C2指令来源顺序与增量快照，而非继续扩张审批外围。完整A–F未完成。

最新A/E/F执行审批桥接：新增真实TerminalUI管道键盘→Application→Runtime→原生
执行，不注入预设decision的UI。direct/Code Mode × once/session四组合验证同命令
复用边界、换命令重新询问、冷Runtime不继承会话授权，表单/普通输入历史不污染。
实际临时目录操作发生，后端source/binary再次核对一致。扩大取消/审批单位与PTY
107通过0跳过34警告（bf708b，64.61秒），静态1108文件/compileall/77包通过
（a83df8），diff通过（10cf4a）。详见approval-scope-ui；没有活动测试，生产授权未改。
保存规则实际发布不在这四组合范围内；完整A–F仍未完成，不扩张官方服务或teach.md。

最新F3/F4/F5：源码确认原生审批同列表选择授权范围，Corki此前scope字符串+二次确认
不一致。现宿主shell/patch接一次/会话/规则显式列表，方向键不提交，Enter才返回对应
scope；取消返回无授权内容，普通MCP表单不升级权限语义。新增scope单位/真实40/100
列PTY与CLI/执行取消/MCP/patch扩大447通过1跳过34警告（16ee19，80.33秒）。跳过
旧patch编译器兼容，不当作已验收；原生后端source/binary均已核对，原14取消跳过项
另跑14通过（6a7641）。静态1107文件/compileall/77包/diff通过（ef92eb）。详见
approval-scope-ui.md；无活动测试。原生快捷键及全部视觉/网络选项仍需核验，整个A–F
未完成，不改变模型协议/官方服务边界或teach.md。

最新横向/E：补真实模型Responses HTTP与MCP File刷新同场隔离，覆盖provider/server
为openai时也只访问配置fixture地址，模型与MCP/issuer凭据及私有头不串用，普通函数
调用、慢刷新/拒绝/冷恢复仍正常。含更新后独有秘密哨兵与旧专属协议拒绝矩阵167通过
（ad366c，41.13秒）；先前OAuth/HTTP两层重试/流可靠性/连接恢复联合151通过
（93d188，23.49秒）。静态1107文件/compileall/77包/diff通过（053ba2）。本轮只新增
测试证据，未改生产协议或认证。详见acceptance-index与mcp-oauth-lifecycle；没有活动
测试，完整A–F未完成，Responses+File联合验证不冒充所有传输/凭据后端验收。

最新总清单收敛：新增 acceptance-index.md，按原目标 A–F 逐项编号，区分局部直接实证
与待收敛，不把旧流水审计的过期“缺失”或旧全绿当成当前结论。重读普通函数搜索、
模型降窗与拒绝无效压缩响应、记忆pipeline和恢复测试断言，六文件组合95通过
（0ee96c，19.46秒），diff检查通过（e5c4ce）。本轮无生产修改、无活动测试；不能由
测试数量计算整体百分比。下一步刷新provider隔离/通用OAuth/模型流错误组合，再逐项
闭合源码与验收；完整A–F仍未完成，明确排除的官方功能不重新列为缺口。

最新 A/C/E/F：冷CLI以1/2/3/5条小页验证压缩副本跨页隐藏、工具结果先于调用加载，
最终正文/调用关系不重放、无误报中断且canonical不变。清屏门控测试先红后修复：
立即撤销并解绑loader，应用关闭仍join实际读取；补保留无交互视图的渲染兼容入口。
CLI/恢复/跨页组合/历史PTY342通过，14条forkpty警告（5cd2dd，37.01秒），静态1107
文件/compileall/77包/diff通过（9dc3f8）。详见history-pagination.md；下方待补验的
跨页压缩工具组合与清屏读取竞态已由本批覆盖。未声称全量性能对齐或完整A–F完成；
后续回到总清单合并有效证据，区分尚缺实现与旧阶段已关闭记录，避免重复修复。

最新 A/C/E/F：实际 CLI 已协调 items/Turn 分页，恢复去重覆盖未加载旧页，Home/上滚
触发加载，前插保留 live 输出与位置。修复读取成功后回放/排版失败仍推进游标的问题，
两类故障先红后绿；候选 frame 成功后才发布显示状态，失败保留原页可重试。真实40/100
列持久分页/Home/草稿PTY通过。扩大 CLI/恢复/两类页/读取生命周期/历史PTY411通过、
14条forkpty警告（a93483，41.66秒）；详见 history-pagination.md。下方“CLI仍全量”
是旧阶段记录，已被本项覆盖。跨页压缩/工具完整CLI组合和清屏竞态仍待补验，整体渲染
成本仍随已加载历史增长，完整A–F目标未完成；本批未改官方服务或teach.md。

最新 A/C/F：Runtime.load_display_turns_page 已接入，独立有界读取 Turn 状态，包括
无消息的失败/取消 Turn；游标绑定 thread 和 Turn ID，更新状态不改变旧页边界，支持
冷续读。源码确认 Codex 也是独立 Turn/items 分页后按 Turn ID 合并，详见
history-pagination.md。扩大198通过（e0d579），静态1104文件/77包等通过（4afb8e）。
无活动测试；CLI 仍全量回放，两种页的协调、前插与恢复去重尚待接入，完整目标未完成。

最新 A/C/F 持久分页读取：Runtime.load_display_items_page 已接入真实 SQLite，默认
100/硬上限200，倒序游标绑定 thread，页内正序；附带压缩副本初始计数，旧序号间隔、
嵌套保留 marker、并发追加和冷续读均验证。新增 partial index，不改 canonical 数据。
扩大177通过（bffd15），最终分页专项27通过（62c8d0），静态1103文件/77包等通过
（33d297），无活动测试。详见 history-pagination.md：CLI 仍全量回放，终态分页合并、
前插和恢复去重尚未接入；前缀元数据扫描仍有历史相关成本，不能称完整分页或 A–F 完成。

最新 F 导航渲染：真实历史控件已使用缓存行 UIContent，避免只移动光标时重新解析
ANSI、拆分并复制整份历史行。源或宽度变化才失效，样式/旧帧快照保持；先红后绿。
历史单位与真实 PTY16通过（d9a231），全部 CLI/流清理296通过（39717a），审批PTY/
冷历史/摘要/恢复69通过（62bf73）。静态1100文件/compileall/77包/diff通过
（c459bd），无活动测试。源变化仍全量 Rich 渲染，源身份 key 仍线性遍历；按 cell
增量渲染与持久分页均未完成。详见 history-viewport.md，完整 A–F goal 继续。

最新 F 基准核验：原生 PagerView 保留数值偏移，prepend 通过新增高度补偿位置；
没有证据支持将跨宽度 source 字符锚点列作必须补齐的 Codex 能力。纠正旧审计，
不扩张实现；持久分页、旧页前插补偿和可见行渲染仍是实际缺口。新增双向宽高缩放
真实 PTY 验证尾部跟随、Home顶部、备用屏幕与草稿唯一恢复；最终 CLI/历史与审批
PTY/流清理337通过42警告（c22cbb），静态1099文件/77包等通过（a538c5）。
详见 history-viewport.md；本批无生产代码变化，无活动测试，完整 A–F 未完成。

最新 A/E：模型 Step 原子提交的后台线程也已纳入取消 join。门控提交前/提交后返回前
× cancel/close 四组合先红后绿；关闭后重开数据库确认模型结果与历史唯一，工具未执行。
同 claim、结果恢复、关闭、搜索和 Code Mode 等扩大229通过（cf698d），静态1099
文件/compileall/77包/diff通过（11ae1f）。详见 tool-result-commit-cancellation.md。
无活动测试，完整 A–F 未完成；继续推进持久历史分页和总清单验收。

最新 A/B/E：工具 claim 也改为 joined write，取消/关闭不再遗弃仍在插入 ledger 的
后台线程。正确测试在旧方法下整响应四组合失败；当前整响应/流式八组合及结果恢复、
关闭、搜索、Code Mode、checkpoint 和核心存储扩大225通过（455b7e）。
cancel_active 仍只请求取消，Turn join 承担清理等待，未改接口语义。详见
tool-result-commit-cancellation.md。静态1099文件/compileall/77包/diff通过
（f4ab51），无活动测试；整体目标及持久历史分页仍未完成。

最新 A/B/E：定位并修复工具结果提交的取消所有权缺口。原 complete_tool_call 直接
to_thread，取消后实际写入仍运行；现复用 _joined_write，清理查询前等待写入终态。
实际 Runtime 两个门控新组合先红后绿，六恢复组合包括二次取消及 CLI；扩大534通过
（d96ba4），静态1098文件/compileall/77包/diff通过（6654a1）。详见
tool-result-commit-cancellation.md。前轮随机冲突未采到字段差异，不声称所有同类
报错原因已排除；整体 A–F 及持久历史分页继续未完成。

最新 A/C/F：状态化 HistoryProjection 已接入现有 CLI 回放，验证压缩替换段跨批次
过滤一致；投影、冷回放与压缩恢复47通过（c65099），静态1098文件/77包等通过
（d202ed）。持久游标分页、终态跨页合并及 UI 加载仍未完成。
扩大回归374通过1失败（e82c63）：流式工具模型提交后冷恢复的稳定结果 ID 内容冲突，
两次批量复现、独立运行通过，根因尚未确认。详见 history-pagination.md，下一步优先
诊断恢复内容差异，不把此次回归称为全绿。无活动测试，完整 A–F goal 继续保持未完成。

最新 A/C/F 历史读取准入阻塞：排队Turn持_turn_lock等待前一worker时，纯显示读取也
被阻塞。先红后_load_display仅保留生命周期锁与owned-read join；raw/snapshot读取
在第一Turn活动时完成，不启动第二模型、不取消第一轮。显示生命周期/SQLite快照/
恢复/输入边界及核心存储176通过（82fae4），静态1096文件/compileall/77包/diff通过
（a043bc），无活动测试。新history-pagination.md记录全量fetchall与跨页compaction/
工具配对/无item失败Turn约束；持久分页尚未实现，不能把此读取修复称为分页完成。

最新 D/E 原生执行补验：验证现有bundled权限编译器的平台、二进制和当前source摘要后，
仅向测试进程设置显式后端。此前memory回归26个skip对应场景全部实际运行；连同共享
目录、重置和关闭组合52通过0跳过（8f43c1，31.42秒）。无生产修改/重建/用户配置修改。
详见memory-reset-handoff.md：Managed的实际读写/网络限制、父权限快照和CodeMode
路径有OS效果证据，External仍由宿主负责隔离。静态1095文件/compileall/77包/diff通过
（e12fd7），无活动测试。此结果不等于整个A–F完成，也不证明真实模型选择质量。

最新 D/E 执行期记忆重置：共享worker首次工具接纳后持有根目录外OS锁，直到真实关闭；
超时/失败关闭保留锁。reset在任何清理前取得相同锁，busy或I/O失败明确不清状态，
worker关闭后可重试。同/异宿主执行期先红后绿，跨进程互斥及后台terminal关闭矩阵验证。
详见memory-reset-handoff.md；这是针对Corki竞态增加的安全保护，原生清理入口未见
相同busy协议，不声称逐项行为完全相同。完整memory752通过26跳过（e0135a），
新增专项38通过、静态1095文件/compileall/77包/diff通过（bc62ea）。无活动测试。
非协作外部写入及父进程崩溃后存活子进程不由这把协作锁保证，完整A–F仍未完成。

最新 D/E 真实缺陷：memory reset撤销claim后，旧模型晚到apply_patch仍可修改共享目录，
仅最终publish检查不足。已给记忆子模型接入请求前/完成item接纳前所有权检查，
整响应和流式工具输出失效时不进入执行链。六故障组合通过（b45043），静态1092文件/
compileall/77包/diff通过（83b2d9）。详见memory-reset-handoff.md。
已接纳或运行中的命令跨reset仍需独立隔离验证，本批不代表完整副作用边界完成。
修复后完整memory回归748通过26跳过（11e793，194.36秒），其采集早于额外流式item
变体，后者包含在六组合专项中。无活动测试；跳过项不算已执行验收，完整goal仍未完成。

最新 A/B/E 冷恢复目录变化：扩展真实ledger/历史追加/checkpoint三窗口×未变/描述/schema
变化九组合。保存Step请求保留旧spec但当前handler拒绝失效调用；下一prepare重新搜索，
旧搜索不重执行、旧持久定义不改写、用户唯一、当前工具执行一次并回灌。不是生产修复，
是补齐跨模块行为证据；详见search-after-compaction.md。扩大67通过（1e4434），
最终九组合9通过（c09b40），静态1092文件/compileall/77包/diff通过（05a25e）。
Codex参考commit不变且干净，无活动测试；全A–F及真实模型选择质量仍未完成验收。

最新 F 活动历史输出：已接入显示capture/延迟主屏写入，流和短回复仍更新source，
repair在历史内保留pending；退出或reader取消才提交队列。真实PTY新通知正文可见且
不提前切屏，关闭后各写一次。详见history-viewport.md。扩大331通过42警告（96b39b），
补充短回复/流式4通过（071b54），静态1092文件77包通过（898988）。
第三方stdout、全局排序、分页与source resize锚点仍开放，不宣称全A–F完成。

最新 F 全屏历史：同一输入应用切换备用屏幕，关闭及审批抢占恢复inline模式/焦点/草稿。
真实PTY1049h/1049l先红后绿，q/Esc/Ctrl+T及真正进入备用屏幕后并发审批均验证。
CLI单位+历史/审批/流式表格PTY321通过40个forkpty警告（6eb709，73.71秒），
独立CLI流清理7通过（a72ced）；静态1092文件/compileall/77包/diff通过（94d0f7）。
无活动测试。详见history-viewport.md；分页、source resize锚点、统一输出排序及全A–F
验收仍开放。本轮未改模型协议/认证，不涉及官方服务，不改teach.md。

最新 F 审批/历史组合验证：真实PTY历史多行paste不污染草稿；历史开启时并发审批
抢占、显式确认/拒绝/取消/排队取消后草稿唯一恢复，12组合通过。完整相关74通过
（a7405f），静态1092文件77包通过。无生产修改，完整目标仍未完成。

最新 F：新增独立只读历史viewport，Ctrl+T打开，方向/翻页/首尾导航，Esc/q/Ctrl+T关闭；
同一输入应用，草稿与焦点保留，reader取消关闭viewport。详见history-viewport.md。
CLI/清理/历史与表格PTY294通过6警告（d7c8dc），静态1092文件77包通过。
全屏overlay、分页、审批交互矩阵仍未完成，完整goal未完成。

最新 F：详细reasoning按原始块整体渲染Markdown，跨片段粗体/代码/列表不再显示原始标记，
dim/italic风格，活动块可展开，保留通知与原始历史。先红后CLI/摘要/恢复/PTY311通过
4警告（e0e12c）。独立历史面板、统一输出排序与动画仍开放，完整goal未完成。

最新 A/F：空summary part added归一化为有身份的空delta，标题无需等待正文即可重置，
下一同段文本不重复分段。先红后59通过，静态1089文件77包通过（1db5b8）。完整E2E/
CLI/清理/摘要376通过140.54秒（e009a9），其采集早于空段补丁，后者由59项补验。
当前无活动测试，完整goal未完成。

最新 A/F 通道修复：Responses raw/summary及Chat raw reasoning保留独立channel，
Runtime仍向宿主发raw但不纳入summary补尾，CLI默认不显示raw。混合标题污染先红后
相关1011通过（c996df）。不删除原始存储，不新增请求/官方分支；完整目标未完成。

最新 A/F 真实恢复验证：模型提交后/checkpoint前中断，经新Runtime及CLI.run恢复；
流式item/整响应两路径，摘要正文结果各显示一次、工具各执行一次、已提交模型不重采样。
4组合及扩展39通过（a36cfd），静态1089文件77包通过。无需生产改动，完整目标仍未完成。

最新 A/F：恢复启动收集已回放reasoning id，CLI跳过重复delta/完成，不过滤新id同文摘要。
宿主事件探针先红3份后只留旧/新各一份；CLI/Runtime摘要与清理292通过（78b0c8），
静态1089文件77包通过（45dda6）。本轮不是新增checkpoint故障验证，完整目标未完成。

最新 A/F：reasoning 完成事件已接入 Runtime/CLI，无delta摘要即时可读、前缀流补尾、
双完成入口按id去重。相关1015通过（1a3e96）；额外修正匿名delta补尾不拆段。
raw/summary差异替换与恢复回放去重仍待验证，不宣称完整reasoning/整体goal完成。

最新 A/F 分段修复：Responses 既有校验后的 item/section 身份贯穿 Model→Runtime→CLI，
新段首个delta重新提取标题，旧段详细历史保留一次。先红后相关633通过（a22a96），
静态1089文件77包通过（590580）。空added通知和完整reasoning终态仍待审，完整goal未完成。

最新 C/F 兼容修复：旧 ReasoningItem 缺 summary 时，只从已保存的 typed body.summary
恢复 CLI 详细展示，不猜 content、不显示加密体、不写回数据库。新旧冷恢复6路径与
CLI/模型item/storage378通过（71a599）；补充8单位及静态1089文件77包通过（a41487）。
无来源的旧纯文本无法安全恢复，分段事件仍待修；完整目标未完成。

最新 A/C/F 修复：真实 Responses summary 原先只落 content，导致冷 CLI 历史不展示。
现明确 summary/summary delta 映射安全摘要字段，raw-only 不提升；Runtime→SQLite→
新Runtime→CLI链路2红后通过。相关51项通过（6f0aeb），静态1089文件77包通过。
分段标题事件、旧记录显示兼容仍待推进；详见 cli-stream-commit.md，完整goal未完成。

最新 F 实施：reasoning 默认不再打印全文，缓存首个 bold 标题到状态栏；Ctrl+T 通过
现有输入绑定切换详细历史。重绘保留活动状态，冷历史安全 summary 可展开，原始记录
不删。先红后 CLI/清理/PTY 280 通过4警告（cc889e）；见 cli-stream-commit.md 实施节。
专用 transcript overlay、reasoning 样式及 section/status 优先级仍开放，完整目标未完成。

最新 F 复核：混合 reasoning→正文→表格的 PTY 顺序可通过，但不是原生默认展示对齐。
再次追踪 Codex chatwidget/streaming.rs 确认 reasoning 默认仅更新标题、全文 transcript-only；
Corki 仍直接打印全文，旧 audit 已记录的差异尚未修复。下一优先实现是状态标题与历史
可见性分离（含取消、换宽），而非继续强化全文输出。见 cli-stream-commit.md 最新章节。

最新 A/E/F 验证：补齐 owned async 提交实际 write_raw 故障到 Runtime 收尾的链路，
覆盖正常失败和重复取消后写入失败，确认原始取消保留、模型流关闭、无 running turn。
生产逻辑无需修改。CLI 单位/Runtime 清理/严格表格 PTY 276 通过、4 警告（9cb3c1）；
静态1087文件77包通过（96ab22）。详见 cli-stream-commit.md；完整目标仍未完成。

最新F修复：严格PTY首次alpha前须有正文，4项先红。新增owned async delta提交，
同一绘制临界区同步更新源/输出正文/flush再重画tail，取消等待者需join；无新stdin。
CLI264通过、提交/清理/回放PTY25通过，最终正文顺序PTY+提交6通过（5600d3），
首帧未启动prompt路径也已覆盖。CLI+清理+全部e2e88725终态349通过77警告139.92秒
（e6ab5e）；首帧调整由随后3单位/6PTY组合补验。静态1087文件77包通过（12a58e）。
详见cli-stream-commit.md；动画和所有其他输出的统一排序仍开放，完整goal未完成。

最新D/E复核：真实SQLite claim暂停叠加首个close等待者取消/再次close，提取/合并
及owner替换、commit未知、cleanup失败等原断言保持，未提前关库或误清新owner。
记忆所有权/管线/Runtime关闭60通过（99d523），静态1085文件77包通过；无需生产
改动。原生memory guard账户配额明确排除，见memory-close-recheck.md。完整goal未完成。

最新B/E修复：搜索定义内部object/NaN/无效Unicode原先在ledger/SQLite导致TurnFailed，
3项先红；现复用持久payload/精确数字编码器提前验证UTF-8和定义原始体积，返回错误
Observation，不删字段或字符串化。搜索/执行器/冷恢复/压缩/storage151通过（dd35e0），
静态1085文件77包通过。详见search-after-compaction.md，完整goal未完成。

最新B/E修复：搜索返回null/map定义原先越过执行器直到历史asdict崩溃；2项先红。
现执行边界校验ToolSpec并持有深拷贝，错误搜索不携带发现定义；Runtime可继续纠正
搜索。执行器/搜索/快照/冷恢复/压缩42通过（45b1b0），静态1085文件77包通过。
详见search-after-compaction.md；不是任意schema语义或完整A–F完成声明。

最新B/E：新增真实Runtime错误搜索→未加载调用拒绝→修正搜索→唯一执行→结果回灌
五步测试，覆盖参数错误和搜索索引一次异常；错误不加载定义，四Observation身份独立。
搜索/缓存/快照/冷恢复组合43通过（41b176），静态1085文件77包通过。无需生产改动，
非真实模型选择质量证明，详见search-after-compaction.md。完整goal仍active。

最新B/C组合复核：扩展自动压缩测试，验证旧Step快照调用→摘要后释放→重新搜索新目录→
加载新定义→真实handler结果回灌，旧/新handler各一次且当前输入保留。工具生命周期/
冷checkpoint/普通请求/配置端点隔离组合39通过（c154f7），静态1084文件77包通过。
未改生产工具逻辑；只证明脚本模型的Harness行为，不证明真实选择质量。详见
search-after-compaction.md。当前无活动测试，完整goal仍未完成。

最新：原全项目22649已终态12851通过7跳过1891.18秒（cf5967），停止poll。
本批真实线程gate复现append取消后写线程未join；普通append_items现复用既有
_joined_write，重复取消不放弃写入，关闭不会越过。输入/关闭/storage单位128通过
（353f09），静态1084文件77包通过；额外恢复/压缩/初始化/记忆组合72通过（27e0ba），
63726已终态，当前无活动测试。
全项目采集早于本批，不称全A–F完成；详见pending-steer-ownership.md。

最新核心修复：关闭时flush正常收尾待写输入；失败保留存储与Thread写锁，关闭入口
只重试存储阶段，不重开执行、不重复关闭模型/MCP。2项先红；输入/关闭/core/realtime
76通过（48ac8a），覆盖取消重试等待者与新Runtime冷读原id一次。静态1084文件77包
通过。原全项目22649仍live54%（e63059）。硬退出内存待写队列恢复仍未证明，完整goal未完成。

最新核心修复：正常收尾输入保存失败保留重试标记，下次admission先保存，失败不启动
模型；取消交还不走此路径。4组合先红，补重复失败/commit后报错身份幂等验证；
core/realtime/输入边界/清理54通过（718951），静态1084文件77包通过。全项目原
22649仍live50%（509530）。进程退出待写恢复/关闭重试仍开放，完整goal未完成，
详情见pending-steer-ownership.md。

最新核心复核：原生RegularTask遇terminal_error不因pending重跑，非外部取消的
on_task_finished仍保存待处理输入；Corki此分支一致，无需生产改动。新增真正模型流错误
（正文前/后）与失败/预算后的显式下一请求身份检查，输入边界/steering/清理27通过
（49e27b），静态1084文件77包通过。全项目22649仍live41%（35a0fc）。输入收尾
存储故障后的恢复尚需核对，详见pending-steer-ownership.md；完整goal未完成。

最新：活动tail接独立解析/body缓存和前缀行数缓存，30行表格追加先红60次稳定段落
渲染，修复后解析/渲染均≤3次。逐行换宽/引用/围栏闭合/源替换Segments等同全量。
CLI单位+真实表格PTY266通过（c31904），静态1084文件77包通过；原全项目22649
仍live37%（880b8b）。表格自身重排、全文访问及原子输出/动画/高亮仍开放，goal未完成。

最新：活动表格tail不再独立解析后缀，改为全文上下文渲染后切行，保留开放围栏
代码语义与assistant gutter，闭合后按表格重排。6项先红，CLI单位+表格/回放PTY
278通过18警告31.06秒（9263d4）；静态1084文件77包通过。全项目22649仍live32%
（a6f253），未重启。全文tail渲染性能、原子输出/动画/状态高亮仍开放，完整goal未完成。

最新：已闭合 md/markdown 表格围栏显示规范化接入全量与缓存渲染；未闭合/代码
示例保留。6 测试先红，专项 58 通过，CLI 单位 + 40/100 列真实 Runtime/PTY
258 通过（40e61f），验证模型请求与 transcript 原文不改；静态 1084 文件77包通过。
原全项目22649仍live27%（7bd005），未重启。详见markdown-table-fences.md；
全文规范化扫描、流式原子顺序/动画/有状态高亮等仍开放，完整 A–F goal未完成。

最新：全项目22649运行中（e002a3），bundled后端摘要核对一致；974033仍live约5%，
继续原句柄，未因等待重启。本轮核对Syntect/Pygments状态API，补跨行颜色契约，
确认不能用无状态逐行高亮替代；尚未实现增量高亮backend，详细限制见cli-streaming.md。
这不是完整A–F完成证明，也不是阻塞；可以继续推进有状态适配/输出队列等工作。

最新：带语言顶层开放围栏解析快路径接入，原文=token正文才追加，关闭/规范化/转义
等回退；复制新token身份避免body缓存陈旧。100行先红69800解析字符，修复后<源3倍，
逐行tokens/样式等同全量。CLI/流/PTY244通过，静态1081文件77包通过；有状态增量
高亮、gutter/动画等仍需处理，见cli-streaming.md，完整goal未完成。

最新验收：69934终态249通过18警告29.32秒（848e58），包含主题切换、CLI全单位、
流清理集成和流式相关真实PTY；停止poll，无活动测试。不是全项目/A–F完成证明。

最新：每流顶层块body渲染缓存已接入，真实write100段落先复现5050次render，
修复后<250次且输出100段。逐行40/100列文本+Style等同原全量；静态1081文件
77包通过。主题切换及CLI/流清理/流式PTY组合69934运行中；body拼接/gutter仍
遍历完整旧内容，长代码块/动画等仍开放，不称整体线性或完整goal完成。

最新：每流最后顶层块解析缓存已接StreamMarkdown，引用定义/源替换/CR有明确
回退，逐行tokens对比全量解析一致。100段落真实write解析字符<3倍源，对照全文
基线>40倍；总渲染仍全量，不能称整体线性。CLI/流清理/表格与工具PTY232通过
10.48秒（6ebc1a），81540终态，无活动测试。剩余渲染缓存/代码快路径/动画见
cli-streaming.md，完整goal未完成。

最新：稳定正文已由原样输出改为源快照Markdown渲染、按新行输出；代码围栏上下文
不按delta丢失，空围栏占位行跳过首行缺陷已修。CLI211通过、流/工具/PTY23通过，
扩充流中粗体/code两PTY通过。旧99165终态303通过，停止poll。仍无解析块缓存/
动画队列，长流成本和stdout/tail原子顺序需继续修，详见cli-streaming.md，完整goal未完成。

最新：表格完整源行holdback和活动tail已接现有PromptSession，未换行行不显示；
稳定原文与可变表格分开，终态canonical回放。2测试先红，CLI单位208通过，真实
40/100列Runtime/PTY两项通过，新增replay/live-off13通过；静态1076文件77包通过。
广回归99165运行中，继续原句柄。一般增量Markdown、动画队列及完整F仍开放，
详见cli-streaming.md，不将本批称完整两区域完成。

最新验证：普通压缩错误按原生on_error继续后续FIFO，取消才恢复草稿。新增失败
Runtime/40/100列PTY后76通过16.49秒，终态错误非压缩成功；生产逻辑无需调整。
测试明确关闭摘要重试以隔离终态归属，不声称验证默认重试策略。上一轮88966已终态
299通过71警告127.95秒（2bf82d），停止poll。详细证据见cli-streaming.md，完整goal未完成。

最新：默认独立压缩期间已复用现有输入所有者，Enter/Tab排后续、停止恢复草稿，
不调用steer、不改摘要请求、不新建stdin所有者；/realtime off保持旧选项。81项单位/
所有权通过，40/100列实际完成/停止PTY4通过，静态1073文件77包通过。广CLI/输入/
全部e2e88966运行中（432216），后续继续同句柄。详见cli-streaming.md，完整goal未完成。

最新验收：93921终态86通过8警告19.57秒（fea20e），含Tab压缩、实际40/100列忙时
拒绝后停止恢复。静态1072文件77包通过（583e92），当前无活动测试。CLI压缩期间
活动输入及增量渲染仍待对齐，不把入口修复当完整F；下面运行中记录已由此终态覆盖。

最新纠偏：原生available_during_task明确禁止忙时Enter /compact；上一批混淆SDK
spawn_task(Replaced)与CLI入口。现CLI拒绝、不取消、不排队；Tab /compact仍排队
完成后执行，空闲压缩保留。3测试先红，修复后CLI/边界/所有权89通过；专项含真实PTY
93921运行中。SDK取消交还保留。下方旧“显式替换保持Tab”不是当前策略，完整goal未完成。

最新验收：34950终态exit0，695通过67警告119.00秒（69404d），停止poll。
范围为CLI/core/protocol/realtime、输入边界与全部现有e2e；替换专用PTY仍待补。
原生TUI对Interrupted恢复Tab；需继续完整追踪Compact入口/状态映射后确认Corki
保留显式替换和Tab的策略，不能用这批绿色测试替代原生行为证据。完整goal保持active。

最新：有任务的replaced取消也交还未提交Enter，CLI独立保存返回项，恢复草稿不吞
/compact或Tab后续任务；新增两测试先红后绿。专项91通过19.91秒，补充压缩3组合
通过0.83秒，静态1072文件77包通过。广回归34950运行中（38e89e），详情见
pending-steer-ownership.md。旧全项目20572已终态12779通过7跳过1863.41秒
（b43d8c），不再poll；早于本批，不能作为最新改动验收。完整goal仍未完成。

最新：普通取消的未提交Enter已通过TurnCancelled字段/宿主take接口交还，不再强制
入模型历史；按已提交item.id识别append成功但ack中断，CLI身份去重后与Tab草稿合并。
两个边界先红后修复，含同文Enter+Tab实际PTY/输入边界18通过17.20秒。replaced及
跨进程草稿恢复仍开放，不改用户旧归档。广组合60286终态726通过2旧断言失败；两项
已改为验证宿主交还，最终专项62736终态89通过19.33秒（34adf5），停止poll二者。
全项目20572仍live52%（796946），采集早于这批核心修改，继续原句柄。详情见
pending-steer-ownership.md，完整goal未完成。

最新：发现未采样Enter steer的取消归属差异，真实Runtime探针证明取消收尾无条件
落入模型历史，下一次无关请求仍携带它（29fc0d）。不能只在UI复制恢复，否则重复。
已追踪原生abort清pending与正常task_finished落库的区别，保存pending-steer-ownership.md。
本轮无生产修改，此核心差异尚未修复，优先于补steer预览。全项目20572仍live35%
（32b811），原句柄与JUnit路径不变，不能重启或当作已通过。

最新：修复停止当前Turn后队列自动执行：3种停止先红后改为join输入后恢复多行草稿，
需要重新提交，/compact显式替换不受影响。恢复失败保留队列并暂停自动发送、原取消
不被覆盖；真实40/100列×两种停止PTY4通过7.88秒。完整CLI29069终态exit0，304通过
107.60秒（63个forkpty警告，9a1bf3），停止poll。最终区分父任务/子任务取消修复手动
压缩回归，专项76通过2.68秒（2a30fa），静态1072文件77包通过。
全项目20572仍live31%（828716），早于本批继续原句柄。完整A–F和steer恢复/增量
Markdown/表格tail等仍未完成，详见cli-streaming.md。

最新：Alt+Up队尾编辑接入真实composer，原生pop_back→替换草稿语义；不发送、不新增
输入记录，重提后按Enter/Tab处理，绑定后才显示提示，审批不绑定。实际40/100列
PTY编辑后只发送改后文本，含单位7通过10.41秒；静态1071文件77包通过。CLI组合
91001已终态exit0，295通过100.82秒（59个forkpty警告，a710a0），停止poll。
全项目20572仍live18%（7dbb20），采集早于预览/编辑，继续原句柄。
完整A–F仍开放，steer分组/队列恢复和两区域增量渲染未完成，详见cli-streaming.md。

最新：队列预览接到现有composer上方非聚焦Window，入/出FIFO发布临时tuple并刷新，
prompt结束隐藏，不写Transcript/输入历史、不新建stdin读取者；审批所有权不变。
新增可见PTY断言后的CLI组合71877已终态exit0，292通过95.84秒（57个forkpty警告，
ed7602），停止poll71877；静态1071文件77包通过。
全项目20572仍live5%（f3e56e），原JUnit路径不变；采集早于本批，继续原句柄不重启。
队列编辑、steer分组、增量Markdown/表格tail及完整A–F仍未完成。

当前唯一全项目回归20572已实际启动（70c73f），报告
/tmp/corki-default-input.XOwVWQ/results.xml，含现有bundled原生后端；binary摘要与manifest
重新核对相符（78302a）。继续同句柄，不因观察超时重启。该采集含本批默认输入修改。

最新：默认CLI现接本地文字steering和Tab队列；settings/新配置默认true，已有false
不覆写，/realtime off和SDK stream默认false保留。默认_consume_turn所有权测试先红
后修复，无显式true的实际PTY通过；静态1069文件77包通过。完整CLI/配置/集成/PTY
19390已终态exit0，908通过1跳过97.13秒（57个forkpty警告，de0d8e），停止poll。
活动区增量Markdown、表格tail、队列预览编辑及完整A–F
仍未完成；旧“默认无reader”记录已由本批覆盖，详见cli-streaming.md。

最新：显式Tab排队已接现有实时文字composer，Enter仍steer。UI QueuedInput不进入
模型协议，FIFO替代单槽，关闭竞态不覆盖旧消息，审批bindings不变；真实40/100列
PTY证明两Tab各下一Turn、Enter同Turn、输入历史各一次，2通过4.89秒。静态1069文件
77包通过；完整CLI/PTY47378已终态exit0，289通过94.49秒（57个forkpty警告，337cb6），
停止poll，当前无活动测试进程。默认普通Turn活动输入和两区域仍
未完成，不能把条件路径称默认已对齐。详细证据见cli-streaming.md，完整goal保持active。

最新：新增同一正文跨工具事件的40/100列真实PTY，2通过2.93秒，验证执行不被展示
延迟、权威正文/工具头/结果各一次；测试直接消费Runtime，不宣称活动composer完成。
原生Enter提交优先steer、Tab排队及关闭竞态已追踪至app/thread_routing.rs，下一步
活动区必须保持两种输入语义，不能简单全排队；详见cli-streaming.md。完整目标仍开放。

最新：A/F真实Runtime复现同一正文跨工具开始后重绘首行重复，两变体先红后修复。
参照Codex defer_or_handle/handle_stream_finished，工具展示FIFO延迟至正文收尾，
不延迟工具执行/审批；异常EOF/取消/三种Turn终态排空并关闭未知工具。相关206通过
22.88秒，静态1067文件77包通过；完整CLI/PTY组合97987终态exit0，283通过85.97秒
（53个forkpty警告，5027aa）。停止poll，当前无活动测试进程。
详见cli-streaming.md。完整A–F仍未完成，增量Markdown/表格tail/活动输入区仍开放。

最新：全项目含原生后端33467已终态exit0，12730通过7跳过1890.58秒（7eab58），
JUnit /tmp/corki-harness-native-current.rlFz2H/results.xml独立解析12737总数、0失败/
错误（cb0cdb）。六项需真实历史编译器、一项文件系统不支持非UTF-8名称，不伪造
历史版本补验。停止poll33467；5803亦终态269通过。当前无活动测试进程。
全量采集早于冷终态快照、未知工具展示、换行提交等最近修改；它们有各批专项和
最新CLI组合，不当作同一份全状态证明。活动区输入/确认所有权及本地realtime文字
队列核验补入cli-streaming.md，本轮无生产修改；完整两区域和A–F仍未完成。

最新：完整CLI/PTY组合5803已终态269通过89.26秒（53个forkpty警告，5fa135），停止
poll。table holdback/scanner/fence源码核验补入cli-streaming.md；确认default普通Turn
没有活跃PromptSession，不能只挂toolbar做tail。下一实现必须覆盖普通模式的活动区
及输入/确认所有权，不能以realtime测试代替默认路径。本轮无生产修改。全项目33467
继续原句柄，最近live52%（a75e4f），完整A–F仍开放。

最新：F流式源行边界已接Runtime使用的TerminalUI；仅换行前源文提交，未完成尾行
在失败/取消时不输出，重绘保存活动缓冲。新测试先红后修复，相关177通过；UI处理
屏障的流PTY/清理17通过20.02秒，静态1067文件77包通过。提交内容暂仍纯文，增量
Markdown/表格tail/稳定队列继续开放，不能以此替代完整两区域实现。
当前CLI/PTY组合5803运行中；全项目33467仍live50%（f8db74），JUnit
/tmp/corki-harness-native-current.rlFz2H/results.xml；两者继续原句柄，不重启。

最新：F两区域流式正文已追踪collector/解析缓存/稳定队列/可变tail/finalize/resize
核心链路并保存cli-streaming.md。源码确认无换行残片暂不渲染、表格holdback与解析
稳定块是不同边界；Corki直接打印delta仍缺这套状态。下一步按文档补实际UI所有权
和PTY，不能仅最终格式化或每delta全历史清屏。本轮无流式生产修改。
全项目33467仍live40%（bd8c9e），继续原句柄，报告
/tmp/corki-harness-native-current.rlFz2H/results.xml；完整A–F目标未完成。

最新：E/F冷历史无结果工具已补展示终态，按Turn/call_id区分同名调用；仅已知终态/
abort关闭待确认显示，running不提前结束，不伪造结果。原生finalize_turn链已核对，
三种终态先红后修复；真实冷failed/legacy abort回放证明无采样、无历史改写。
相关198通过7.80秒（2个forkpty警告），静态1067文件77包通过。全项目33467仍live
35%（8efff1），继续原句柄；早于最近冷历史生产修改，不当作该批完整验收。完整目标
仍active，CLI块级流/分页等及A–F逐项验收仍未完成。

最新：E/F冷失败历史已修复：展示快照原子读取items+Turn终态，Runtime保持读取取消/
关闭所有权，CLI按Turn边界呈现失败/取消并去重abort，不改模型历史/数据库格式。
四个真实冷回放变体、并发写入读快照、两套读取生命周期及相关278项通过；新冷失败
真实PTY和流重绘16项通过22.38秒。静态1067文件77包通过（f1116e）。完整A–F仍
开放。全项目33467继续原句柄，采集早于本批生产修复；报告
/tmp/corki-harness-native-current.rlFz2H/results.xml。

最新：E/F确认真实冷历史缺陷：失败Turn的error已持久化，但Runtime展示只加载items，
CLI仅显示已提交的半截正文、不显示失败原因。实际warm失败→关闭→cold Application
回放探针34db8e复现，模型调用仍1，无用户数据修改。Codex replay_thread_turns会在
items后回放status/error。下一优先修复展示专用原子快照及终态回放，不污染模型历史，
并保留读取取消/关闭所有权；详细验收见audit最新节。该缺陷尚未修复，不可宣称完成。
全项目33467仍live14%（c4b561），JUnit /tmp/corki-harness-native-current.rlFz2H/results.xml。
继续原句柄，完整目标保持active。

最新：A/E补充reasoning+正文+双工具的模型提交后/checkpoint前冷恢复，普通完整
响应及流式工具已完成两变体均通过；仅一次后续采样、工具各一次、原item身份唯一、
结果有序、再次resume为空。相关26通过5.83秒，静态1065文件/77包通过，无生产改动。
全项目33467仍live4%（3ba30a），报告/tmp/corki-harness-native-current.rlFz2H/results.xml；
继续原句柄，不重启；采集早于本次新增测试。完整A–F仍未完成。

最新：F长代码逻辑行与最终终端折行分离，容器不裁切，终端续行保留实际行首空白。
五项先红后修复，新增两项真实Runtime PTY；CLI/冷历史/流PTY177通过21.63秒，
静态1065文件/77包通过。源码确认默认PreWrap并非所有代码永不折行，URL例外、
raw模式/精确折词/极窄宽度仍需核验。完整A–F及块级流/分页等仍开放。
当前全项目含原生后端回归已实际启动33467（e80135），JUnit
/tmp/corki-harness-native-current.rlFz2H/results.xml。仅测试进程指定已验证bundled
编译器；继续同句柄，不因短期无输出重启。旧28545/67663等均终态。

最新：F完成/冷历史/重绘代码块已接语法高亮及未知语言/超限纯文降级，语言元信息
支持逗号/空格/tab。Rich补齐空白问题先红后修复，源文不变、不加背景。Markdown
26专项通过，CLI单位+冷历史+流PTY168通过18.94秒（12警告），静态1065文件/77包
通过。默认Rich主题与原生Syntect精确主题仍不同，长行布局、块级流、分页等继续
开放；不能将此次配色接入宣称F完成。无活动测试进程，完整目标保持active。

最新：67663 已终态，571通过62失败7跳过（132.42秒），不再poll。全部62失败来自
stdin审批共享夹具把LocalConfigState伪造为None，未进入Runtime；现修正为与features
一致的ConfigLayer快照，未放宽生产契约。原62项重跑全部通过46.23秒（fd53fa），
另加无需native后端的夹具契约测试2通过（9f5831），防止整组skip掩盖失效。
原640个节点合并证据为633通过、7仍跳过（不是一次运行结果）；6需要真实旧编译器，
1宿主不支持非UTF-8文件名。静态1065文件/77包通过（fa3206）。无活动测试进程。
完整A–F仍开放；CLI块级流、代码配色/长行、分页等差异继续按源码闭环，不提前完成。

最新：全项目28545终态exit0，12053通过640跳过1674.12秒，JUnit
/tmp/corki-harness-projection.IYHCBu/results.xml，停止poll。该采集早于Markdown。
当前CLI单位/全部CLI集成和PTY235通过82.03秒（49个forkpty警告），静态1065文件/
77包通过。跳过项多数仅缺显式编译器环境变量；本机现有bundled权限后端源码摘要/
二进制摘要与manifest一致，已精确选择原640个skipped节点补跑，session67663，
JUnit /tmp/corki-native-verification.PpTU2f/results.xml，继续同句柄勿重启。只对该
测试进程设置CORKI_TEST_SANDBOX_COMPILER，不改用户配置或构建产物。完整A–F仍开放。

最新：复核三个组合示例实际断言并运行独立进程，3通过4.44秒。覆盖主循环/计划/
文件写入/连续历史、长进程与图片、普通搜索加载后的stdio MCP/插件/技能Observation。
core_loop的cross_turn_memory只证明同Thread连续历史，不能当长期记忆证明；脚本
模型不证明真实模型选择质量，示例也不证明Markdown呈现。全项目28545仍live43%，
继续原句柄，JUnit /tmp/corki-harness-projection.IYHCBu/results.xml，不能重启。
本批无生产修改，git diff --check通过，完整A–F仍未完成。

最新：F原生三个列表样本先红后修复：嵌套每层4列、列表内代码前后空行、多行项
后分隔及根列表伪首空行。0起始编号保留，代码首空行/尾空格保留。40列快照按原生
多行项规则更新，100列不变。CLI/冷历史/PTY160通过20.47秒（13个forkpty警告），
新增嵌套同文流40/100列真实PTY与Markdown专项27通过16.00秒；静态1065文件/77包
通过。代码配色/长行、完整列表表格链接、块级流/分页仍未完成。全项目28545仍live
41%，继续poll同句柄，报告/tmp/corki-harness-projection.IYHCBu/results.xml；
该采集早于Markdown修改。完整A–F保持active，后续继续核心差异与全项目结果检查。

最新：F完成正文/冷历史/Transcript重绘接入Markdown与两列gutter；标题/列表/
引用/代码适配，不再仅原样Text。TTY同文Markdown、实体转义或超宽正文也重绘，
短纯文本不多清屏，非TTY保持追加限制。HTML块渲染IndexError已修复并回归。
40/100列快照与两项新增真实Runtime PTY验证同文Markdown结束时格式化；最终
相关158通过21.19秒（13个forkpty警告），静态1065文件/77包通过。块级增量流、
精确表格/本地链接/嵌套间距/语法配色、reasoning/分页仍开放，不宣称完整F。
全项目28545仍live31%，继续poll，JUnit
/tmp/corki-harness-projection.IYHCBu/results.xml；早于本批生产修改，不作新增
Markdown全项目验收。完整A–F目标保持不变。

最新：D/E补充真实后台合并Runtime模型流关闭，非run_once替身。主Turn成功后
关闭parent，流finally屏障未结束时claim仍running且仓库未close；重复取消两个
关闭观察者不撤销清理。正常/清理OSError两变体均先流结束再仓库close，claim为
failed/owner空，无MEMORY.md发布或retained worker。相关50通过8.21秒，静态
1063文件/77包通过，无生产修改。全项目28545仍live13%，继续同句柄，JUnit仍
/tmp/corki-harness-projection.IYHCBu/results.xml；新增测试晚于全量采集。完整A–F
仍未完成，不能把该窗口通过当成Memory全验收。

最新：A/B/C增补搜索结果提交后、节点checkpoint前中断的真实冷恢复测试。移除
大定义工具后重启resume，仅追加1次模型采样，总2次，无重复搜索/用户输入、无误
压缩，旧归档完整、终态无running Turn；再次resume为空。手动压缩按原生独立任务
历史语义不刷新工具清单，上一批投影仅正常prepare入口，不扩大生产修改。相关68
通过8.64秒，静态1063文件/77包通过。新全项目已运行session28545，最新live3%，
JUnit /tmp/corki-harness-projection.IYHCBu/results.xml；继续poll同句柄，不能重启。
采集早于本批新增冷恢复测试；旧74071终态。完整A–F未完成，goal保持active。

最新：B/C实际Runtime复现并修复失效大搜索定义在请求投影之前被预算计费，误触发
压缩。graph三个prepare入口绑定同一Step冻结specs投影；window在预算/摘要/请求
共用投影，切换模型清空旧绑定，原始记录不改。removed/replaced两变体均2次正常
模型请求、无压缩、无旧定义广告、旧归档完整；有效大定义/真实压缩控制仍通过。
上下文/core/搜索恢复等463通过11.59秒，静态1063文件/77包通过。未启动新全项目
回归，旧74071保持终态；完整A–F及真实模型质量/CLI余项仍开放，不提前宣称完成。

最新：E通用MCP刷新锁等待取消核验，Codex在锁前spawn整个owned事务，不能把该
行为套用登录POST前取消准入规则判为缺陷。Corki真实OS锁争用三变体证明重复取消后
重读最新凭据，usable/deleted不POST，expired只用新token刷新一次，保存/解锁后
保留CancelledError。无生产修改；相关MCP/OAuth与技能发现40通过6.41秒，静态
1063文件/77包通过。该疑点已关闭，其他OAuth边界及完整A–F仍未完成。回B/C核心
余项，不启动官方账户路径；没有运行新的全项目回归，旧74071保持终态。

最新：F真实冷CLI复现并修复普通RemoteHistory message不显示、text-only parts
用户被显示空行+附件占位。只渲染已验证明文与真实媒体占位，opaque仍跳过；
展示/重绘文字各一次，原始items不变，不写输入历史、不采样。CLI及冷恢复132
通过2.23秒，静态1063文件/77包通过。全项目74071已终态exit0：11995通过640
跳过1628.00秒，JUnit /tmp/corki-harness-current.1mhUaV/results.xml，不再poll。
该回归早于近期显示收尾/协议排除项/预算及普通归档兼容修复，不作为它们全项目
验收。A–F仍开放，后续继续核心余项及F样式/失败历史/分页/真实冷PTY。

最新：C/E修复Chat将普通旧RemoteHistory message一概当专用压缩拒绝的问题。
仅请求副本转User/Assistant既有路径，保留角色/文字/媒体，不改原始SQLite归档，
不发内部metadata；opaque compaction及agent_message仍拒绝。两真实Chat场景先红，
Responses作对照；最终模型/拒绝/metadata/媒体组合481通过61.08秒，静态1063文件/
77包通过，README同步。额外媒体测试修正文案大小写夹具，不改降级行为。
全项目74071最近live52%，报告/tmp/corki-harness-current.1mhUaV/results.xml；
继续poll同句柄，不重启，采集早于最近修复。完整A–F仍开放，尤其其他接收边界、
记忆/工具/上下文余项及F历史显示/分页/完整PTY，不将本批作为全目标完成。

最新：C实际Runtime而非仅adapter验证旧opaque compaction归档：开启预算管理，
Responses/Chat×openai/independent×小型/600KB共8组，Turn因compaction明确失败，
0 HTTP、原始归档未变且无新压缩标记，不静默迁移/重新启用专用端点。metadata/
Lite/retention组合84通过37.64秒，静态1063文件/77包通过。本批无生产改动；
普通message与专用opaque的边界不能混淆，Chat普通旧normalized message兼容及
其他归档/MCP接收仍待细化。全项目74071 live42%，报告路径不变，继续poll原
句柄；早于最近排除项与CLI修改，不替代新增验收。完整A–F仍开放。

最新：C旧工具密文仍按原生9/16或freeform原长度计费，导致600KB不发送数据仍
误触发压缩，已修复。六项先红；删除原生估算helper，协议层提示常量供请求与预算
共用。direct/CodeMode真实Runtime各2次模型请求、工具1次、无额外压缩，密文归档
保留。上下文/HTTP/协议431通过7.84秒，预算/压缩恢复85通过12.75秒；静态1063
文件/77包通过，README同步。全项目74071 live34%，继续poll同句柄，报告仍
/tmp/corki-harness-current.1mhUaV/results.xml；采集早于本批。归档RemoteHistory
预算投影、MCP接收边界及A–F其余未完成项仍开放，未修改teach.md或用户历史。

最新：发现旧官方history-notes的加密工具输出发送入口仍存在，已按排除项清理。
删除Responses→media的encrypted_enabled传递/原生编码、Runtime能力绑定；旧
supports_encrypted_tool_output=true明确拒绝，capabilities旧构造字段仅无效兼容
数据，不被transport读取。所有供应商统一普通文本不可读提示，归档opaque不删。
四项先红；模型/配置/协议集成1003通过1跳过13.76秒，增补openai/independent
标签、实际HTTP路径、冷恢复等49通过6.52秒，静态1063文件/77包通过。README及
旧第五十九批审计已标原生发送“用户明确排除”。普通reasoning字段不与工具密文混淆。
全项目74071 live30%，报告/tmp/corki-harness-current.1mhUaV/results.xml，继续poll
同句柄不重启；早于本批。下一步密文预算原生折扣/请求视图与接收边界仍需核验，
完整A–F未完成项不变，不能把这次发送路径清理称为整个goal完成。

最新：E/F工具头已显示但没有完成事件时，三种Turn终态或事件流EOF/异常/取消
都会标记“interrupted; completion not confirmed”。按call_id追踪，不混同同名调用，
不伪造结果/副作用回滚；冷回放尚无result的调用也纳入。六组先红，扩展回放变体；
真实Runtime工具执行中取消验证handler关闭、模型/工具各1次、显示/重绘提示各1次。
最终CLI单位/集成及stream/plan PTY181通过22.38秒（11个forkpty警告），静态1063
文件/77包通过。全项目74071 live21%，继续poll同句柄，报告仍
/tmp/corki-harness-current.1mhUaV/results.xml；早于最近正文/工具收尾，不当新增验收。
完整A–F不变，下一步回核心剩余审计，F完整样式/失败历史/分页/冷PTY亦仍开放。

最新：E/F事件迭代器EOF/异常/取消不再遗留正文/reasoning活动流。六组先红后通过，
显式_DisplayStreams由_render_events finally收尾，原事件终态不重复end，收尾异常
不覆盖原错误/取消。精简UI未启用的显示方法不提前解析，初次组合28个回归已修复。
含收尾失败变体的CLI/恢复/stream和plan PTY176通过24.07秒（11个forkpty警告），
另真实Runtime+TerminalUI写入失败2通过0.57秒，无running Turn/模型stream遗留。
静态1063文件/77包通过。全项目74071 live11%，JUnit路径仍
/tmp/corki-harness-current.1mhUaV/results.xml；继续poll，不重启。它早于本批代码，
不能代表新增收尾的全项目验收。A–F完整目标及其余缺口保持开放。

最新：基线prepare写线程被屏障阻塞时重复cancel，同时实际线程争抢过期claim；
成功/报错两组证明准备先结束再允许接管，取消不被文件错误覆盖，临时状态收尾、
记忆.git保留。相关24通过5.30秒，静态1062文件/77包通过。本批无生产改动。
新全项目已启动session74071（677f26），JUnit
/tmp/corki-harness-current.1mhUaV/results.xml；下一轮poll同句柄，不重启。
该次采集包含目前冷CLI回放、稳定事件身份与记忆prepare修复，不用旧30955代替。
A–F目标仍开放，下一步回到核心剩余项并持续观察该句柄。

最新：D/E确认并修复旧owner在基线prepare阶段删除新owner工作区diff的问题。
先红FileNotFoundError（987212），现pipeline通过SQLite token校验+写事务执行
prepare_shared，保留配置先于输入同步；失效owner不再触碰新工作区。新增所有权
回归，旧sync测试调整到配置后注入。memory单位+test_memory*集成最终729通过26
跳过181.64秒，session25550已终态exit0，不再poll；静态1062文件/77包通过。完整A–F保持开放，
尤其记忆配置变化/其余故障窗口、F历史呈现/分页/PTY及统一协议边界最终审计。

最新：pending冷CLI已有两个真实checkpoint故障注入窗口：model commit后/tool
history写入后，在节点checkpoint前外层取消；同库新Runtime恢复。正文/结果/计划
一次显示、重绘不重复，工具总1次、冷模型只采样后续答案1次，旧项保留且Turn终结。
tests/integration/test_cli_pending_history.py已接真实application与TerminalUI；相关
525通过5.21秒，静态1062文件/77包通过。本批无生产改动。不是实际进程kill或冷PTY，
也不代表全部恢复矩阵完成。读取取消上一批已验证。下一步回到A–E重要剩余项；F
失败Turn/附件、行上限/分页/冷PTY继续开放，完整目标不变。

最新：全项目30955已终态exit0：11953通过640跳过1610.14秒，JUnit仍为
/tmp/corki-harness-cli.WcFlFo/results.xml；不再poll或重启该句柄。它早于Transcript/
冷历史新增实现，不作为其验收。新增真实Runtime显示读取生命周期10组：正常/取消/
重复取消/关闭/取消加关闭×成功/读取错误；已启动读取先join，数据库与model后关，
关闭后拒绝新读取，取消不被读取错误覆盖，无模型采样。连同冷CLI/身份回放12通过
1.26秒，进程exit0；静态1061文件/77包通过。本批无生产改动。
实际pending冷CLI、失败Turn/附件、行上限/分页/冷PTY仍缺；下一批按A–F总清单
重新排序，不能将F局部或旧全项目全绿当整体完成，长期记忆状态/故障边界仍需核对。

最新：本地业务history→CLI已接入resume_pending之前；原始用户/正文/工具结果/
计划回放，跳过compaction替换副本/摘要，不写输入历史。正文item_id、计划call_id
贯通真实事件并用于恢复展示去重。真实两Turn冷CLI不采样、不改原记录，合法同文
不同id保留；合成恢复事件去重另验证。最终相关590通过20.70秒，静态1060文件/77包。
仍缺实际pending冷CLI故障矩阵、读取取消/关闭、失败Turn与附件、行上限/分页/冷PTY。
全项目30955 live52%，报告路径不变继续poll；早于本批，不能当新增回放验收。

最新：真实两Runtime同数据库冷启动复现旧消息不显示：仓库有答案、冷模型0调用、
CLI退出0但输出无旧答案。已读原生完整replay.rs并记录cli-cold-history.md：需要本地
业务项回放、pending恢复稳定身份去重，不能拿压缩窗口/重执行当显示。尚未实现冷
回放，本轮无生产修改。全项目30955 live33%，继续poll同句柄，报告路径不变。
下一批受控本地读取/展示与事件身份边界；A–F范围和其余未完成项不变。

最新：活动流resize专项8组通过：40/100×resize有无×正文一致/不同；确认先重现
活动delta再放行模型完成、最终合并后二次重绘，无需重绘时不额外清屏。CLI/流及
计划PTY114通过16.34秒，11个forkpty警告；静态1057文件/77包。本批无生产修改。
全项目30955仍live32%，报告路径不变继续poll。下一步行上限/冷历史回放或回A–E
重要剩余项；Markdown/reasoning和复杂流矩阵仍开放，不把本批当作完整F完成。

最新：AssistantMessageCompleted.text已接权威展示源，折叠最近流，差异主动repair，
modal期间pending保留；无delta完成/连续消息不重复。流中重绘后完成也标记修复。
先红后绿；真实Runtime+PTY40/100证明无需resize也修复旧文本。CLI/主循环/PTY110
通过9.17秒，5个forkpty警告，静态1057文件/77包。非TTY补发最终正文、无法撤回旧字节。
全项目30955仍live28%，报告路径不变，继续poll。行上限/分页/冷恢复、Markdown/
reasoning和复杂流专项矩阵、A–E余项仍开放。

最新：审批期间resize的真实PTY矩阵补齐宽/高变化与仅高度变化，显式接受/拒绝/
Ctrl+C前不重绘不默认提交，结束后pending重绘和composer恢复，表单/展示历史隔离。
审批/计划PTY+源单位28通过43.27秒；静态1056文件/77包。全项目30955 live20%，
继续poll，报告路径不变。下一批优先将AssistantMessageCompleted.text作为权威源
修复delta差异；并发modal切换、活动流重排、行上限/冷历史等仍开放，A–E范围未减。

最新：F展示源与自动resize重排已接TerminalUI/application.run，旧计划PTY40→100
无需新事件即可重排且草稿保留。CLI/输入/elicitation集成+PTY143通过16.22秒，7个
forkpty警告；静态1056文件/77包。详见cli-transcript-reflow.md最新节：仍缺权威
完成内容替换/流合并、reasoning状态栏、行上限/分页/冷恢复及modal/流/高度PTY矩阵。
全项目30955仍live12%，报告/tmp/corki-harness-cli.WcFlFo/results.xml，采集早于
本批显示源修改；下一轮poll同句柄，不重启。A–F完整目标继续开放。

最新：CLI事件aclose重复取消现join到底并保留原取消；两项先红，补原取消+清理失败
又一项先红，最终CLI/输入/elicitation135通过5.96秒。旧全项目80481已结束：11910
通过640跳过2失败1589.27秒，两个测试依赖MCP启动词法顺序；改明确barrier并故意
延迟首服务矩阵，28通过2.25秒。静态1054文件/77包通过。新全项目session30955
已启动，JUnit /tmp/corki-harness-cli.WcFlFo/results.xml；下一轮poll此句柄，不再poll80481。
显示源/重排仍未实现；新源码确认reasoning默认状态栏及权威完成正文修复与Corki
仍有差异，详见audit。A–F完整目标仍开放，未新增官方服务或原生协议。

最新：新增cli-transcript-reflow.md，基于原生三个完整模块及Corki输入/显示调用链，
明确旧历史重排缺口、75ms/overlay/stream合并/源保留与冷回放约束；未实现重排。
不能只缓存assistant输出，否则PromptSession回显用户内容会丢，clear又可能复活旧显示。
原全项目80481 live51%，累计两个F尚未输出详情；继续poll同句柄不重启。上一批
101项CLI/PTY与静态仍只是其采集范围证据，A–F整体目标继续开放。

最新：真实Runtime→CLI计划PTY40→100/100→40两Turn验证通过；URL字节完整，
说明markup保留，新输出按当前宽度折行。完整CLI+composer/plan PTY101通过9.19秒，
强化宽度断言后新PTY2通过3.08秒。6个forkpty DeprecationWarning未隐藏。静态1054
文件/77包通过。补齐ApplicationUI说明签名。旧scrollback源重排仍缺，不能与新输出
resize混淆；下一步追踪原生resize_reflow完整生命周期或回A–E重要剩余项。
全项目80481仍live45%，已有一个F详情未出，继续poll原句柄不重启，目标仍开放。

最新：F计划说明已贯通builtin→executor→持久化/graph事件→CLI，说明与步骤合并
展示且不重复工具输出。旧ledger/item缺字段兼容、Code Mode嵌套plan说明传递已覆盖。
两项先红，最终CLI/tools/protocol/storage+主循环/Code Mode集成1494通过18.09秒，
静态1053文件/77包通过。全项目80481仍live39%，先前一个F详情未出；下一轮poll原
句柄，报告路径不变，不重启。F剩余长内容PTY/resize/wrapping边界及A–E未完成要求
继续保持；本批不是整体完成。

最新：F计划URL保持完整、相邻普通长词仍折行，已接TerminalUI；5001位数字host
导致ValueError两项先红后绿。CLI单位+输入/elicitation集成129通过6.50秒，静态
1052文件/77包通过。原全项目80481仍live超过30%，约25%出现一个失败，详情未出；
下一轮poll同句柄，不重启，不称通过。报告/tmp/corki-harness-oauth.6FM4eY/results.xml。
原采集早于本批UI。F还缺完整wrapping边界、explanation、resize与长链接PTY；A–F
其余未完成要求继续保持，不能以局部绿色视为整体完成。

最新：回F发现计划step/工具失败名称的Rich markup导致原文丢失，3项先红后绿；
已改Text，Updated Plan状态与缩进参照Codex plans.rs。E三store×两issuer×四取消
阶段24项通过。静态1051文件/77包。全项目80481仍live超过13%，下轮poll同句柄；
原采集早于UI修改。完整CLI/交互集成与OAuth矩阵147通过6.75秒（29b0f7），已终态。
F还需explanation、URL型长token折行/resize等证据；A–F完整目标继续开放。

最新：Auto/Keyring首次登录保存、匹配旧File条目清理及CLI/技能安装已接通。按原生
写keyring后best-effort清理，Auto写失败才回退File，显式Keyring不降级。三store实际
首次授权→工具调用→冷Runtime复用验证，MCP/skills/CLI+probe2263通过20.98秒，
静态1050文件/77包。新全项目session80481 live，JUnit /tmp/corki-harness-oauth.6FM4eY/results.xml，
下一轮先poll，不重复启动；测试显式null/内存backend，未触碰系统钥匙串。
下一轮回A–F总清单及存储故障/插件CLI/helper等剩余项，整体目标尚未完成。

最新：Auto/Keyring读取与固定source刷新已接入Runtime→manager→HTTP客户端；新增
keyring25.7依赖与锁文件，Corki独立service。初始Auto可回退File，选中后不得热切。
冷Runtime三store实际工具调用通过；MCP/CLI/skills+probe2254通过30.91秒，额外
并发keyring刷新矩阵联合19通过0.65秒。静态1050文件/77包，无live测试句柄。
所有测试显式null或内存backend，未触碰真实钥匙串。首次登录Keyring保存/旧File
清理及CLI/安装接入仍缺失，不能称完整Auto/Keyring；下一批优先补齐此写入链。

最新：真实PTY 40/100列×MCP登录接受/拒绝/Ctrl+C六组合通过，退出130/1/0，
取消/拒绝无token/凭据，HTTP及回调端口关闭；与现有会话/审批PTY和CLI单位联合
105通过32.75秒（7b4fc3）。静态1048文件/72包通过，无live测试句柄。本批生产未改。
已完整读取Codex resolved_store与登录写入降级：Auto启动选择后source固定，锁失败
不得当backend不可用回退。下一步实现Auto/Keyring的读/存/刷新及Runtime/CLI接入，
不能仅解除当前File门控。A–F剩余目标继续开放，未调用真实钥匙串/外部供应商。

最新：corki mcp login NAME [--scopes read,write]已接真实入口，独立配置/管理策略/
发现/浏览器/回调/保存，不启动模型会话。显式File本地配置HTTP成功链路已验证；
Auto/Keyring/插件目录/helper等仍明确缺失。CLI+输入确认集成+OAuth联合134通过
6.08秒（b99cd7），真实console --help退出0。无live测试句柄。下一步真实PTY取消
与凭据存储策略推进，不能把局部CLI入口当整个F或通用OAuth完成。

最新：CLI入口源码检查发现新用户config模板仍推荐原生namespace/加密输出/native
notes，按目标优先清理。新增test_paths先红后绿；与model_search/flat_search联合30
通过0.61秒，静态1045文件/72包。README原生搜索/双轨namespace/V2及TokenBudget旧
说明已修订，development当前清单同步。CLI登录本批未实现，Auto/Keyring仍未完成。
完整config/models单位已终态963通过1跳过8.98秒（40c52b）；无live测试句柄。
此前全项目11867通过640跳过为旧采集版本，不能替代新增代码验收。A–F整体仍开放。

最新：OAuth安装宿主撤销后仍发/token已用Runtime probe复现并修复，动态检查固定
context/home/settings与当前准入。等待凭据锁时取消也不得启动token请求；已提交交换/
保存则join后保留取消。完整MCP/skills单位+probe2153通过16.47秒，静态1045文件/72包。
全项目74969终态11867通过640跳过1567.66秒，JUnit路径不变；早于OAuth修改启动，
不能充当本批全项目证明。所有测试句柄已结束。下一批推进Auto/Keyring/CLI实际入口，
继续保留A–F剩余验证，避免停留在OAuth细节；当前仍不能宣称目标完成。

最新：OAuth listener连续cancel故障注入修复前失败，现共享owned join，等待连接与
listener关闭后保留取消结果。安装probe冷Runtime复用File实际调用，无重复注册/token；
预注册client_id两种回调验证无DCR。五文件41通过2.35秒，静态1045文件/72包通过。
全项目74969仍live超过51%，下一轮继续poll；它早于生产修改启动，不能证明本批。
下一项优先授权等待期间配置撤销重新准入及token写入取消；Auto/Keyring/CLI和A–F
剩余目标保持开放。未发生真实浏览器/外部token请求。

最新：显式File公共客户端首次授权接入skill安装Runtime，包含DCR/PKCE/loopback/
token交换与首次原子保存。离线真实loopback→下一Turn工具调用→Observation验证，
与callback/file/refresh联合38通过2.23秒；静态1043文件/72包通过。修复非ASCII state
异常，错误回调不消耗code。Auto/Keyring/CLI及冷恢复、重复取消、配置撤销等仍开放。
全项目74969仍live超过37%，启动早于本次生产修改；下一轮继续poll，不重复启动。
本批不是完整OAuth，也不是A–F完成。详见mcp-oauth-lifecycle.md最新段落。

最新：OAuth安装probe顺序差异已定位为共享工具定义缓存；为不同模拟服务状态
注入独立cache后，五组合稳定3失败2通过1.68秒，三个OAuth都未到宿主授权。
仅改探针及审计，未实现登录。全项目74969仍在运行，下一轮继续poll同句柄；
先前全部分批结果不能代替全项目终态，也不能证明OAuth首次授权完成。

最新：全项目74969仍live超过12%，JUnit目标不变，下轮先poll。OAuth首次授权
源码重新核对，已修正mcp-oauth-lifecycle.md过期状态；显式File刷新有实现，但
首次授权/首次凭据保存及Auto/Keyring仍缺失。安装probe组合3失败2通过，legacy
单跑也未到宿主授权；组合曝光差异还需隔离缓存/顺序原因。无生产修改，无真实登录。
下一步需把预注册/DCR、绑定回调与存储整体接入Runtime/CLI，官方CIMD不可复制。

最新：turn_timezone两失败已按普通manual compaction的DoNotInject边界修订，分别
验证摘要旧环境、压缩Turn新时区、下一普通Turn重注入；三文件28通过5.15秒。
无生产改动，静态检查1040文件/72包通过。已启动全项目pytest，session74969，
JUnit目标/tmp/corki-harness-regression.p5n6us/results.xml；下轮先poll同句柄。
全项目尚无结果，不计通过；接下来仍需通用OAuth浏览器授权/完整CLI与A–F最终审计。

最新：89424终态223通过62跳过8失败43.07秒，工具分组旧原生回包已按普通函数
迁移，两文件50通过7.25秒。无生产改动，静态检查1040文件/72包通过。最后9集成
模块57238跑出153通过2失败27.04秒：turn_timezone冻结请求旧metadata预期，以及
独立compact时区期望Shanghai/实际Paris，下一轮先源码确认第二项语义，不能预设
都是旧协议问题。A–F整体与通用OAuth/CLI剩余工作继续开放。

最新：40160已结束293通过49跳过8失败82.53秒；shell_identity旧metadata预期及
skill_rule_reload旧Apps触发已迁移，保留冻结身份/规则发布/取消关闭等断言，两文件
53通过28.24秒。无生产改动，静态检查1040文件/72包通过。剩余32集成模块从
skill_selection_runtime继续，session89424已启动；下轮先poll，不重复启动。
A–F整体、通用OAuth浏览器流程与完整CLI交互仍待完成。

最新：checkpoint三失败确认为测试补入私有metadata的旧预期，修订后环境/shell
两文件22通过5.11秒；搜索定义平铺回包8通过2.13秒；普通摘要service_tier矩阵
36通过5.13秒。无生产改动，静态检查1040文件/72包通过。相同59集成模块已重跑，
session40160仍live超过8%，下轮先poll同句柄，不重复启动。原21984已结束。
A–F及通用OAuth浏览器授权/完整CLI剩余工作仍开放，局部通过不是完整验收。

最新：retained_history_provenance旧V2测试已迁移为普通摘要用户文本来源验证，
覆盖自动/手动、媒体投影、20k文本预算首尾裁剪和两次冷恢复；五文件联合118通过
27.04秒。无生产改动，静态检查1040文件/72包通过。剩余59集成模块session21984
已结束：85通过49跳过8失败16.11秒（3a2d2d）；无live句柄。下一轮优先追踪
runtime_environment_context与selected_shell_runtime的checkpoint请求冻结差异，
再核对search_definition_completeness旧namespace回包和service_tier请求计数。
A–F整体、通用OAuth浏览器授权及完整CLI验收仍开放。

最新：config/storage/plugins/skills单元1052通过1跳过，skip确认文件系统拒绝
非UTF8文件名。其余evaluation/execution/prompting/protocol/realtime/http_client
初次407通过7失败，旧档/在线协议预期修订后414通过1.06秒。无生产改动，静态
检查通过。集成6306结束664通过30跳过8失败111.11秒，停在retained_history_provenance，
下一轮完整追踪；所有句柄结束，A–F目标仍开放。

最新：技能源格式重载改用禁用普通MCP持久化触发，低用量hash变更统一普通摘要，
两文件43通过10.42秒。previous_model_effort完整102通过23.14秒，保留失败/取消/
checkpoint及tier/effort主旧模型隔离。无生产改动，静态检查通过。下一段85集成
模块session6306已启动，下轮先poll，不重复启动；A–F完整目标继续开放。

最新：97514结束223通过8失败49.16秒，插件技能Apps重载及previous_model压缩
待追踪。MCP单位初次137通过8失败，call_metadata旧账户预期已迁移；联合54通过，
完整MCP单位1980通过13.83秒。无生产改动，静态检查通过，所有句柄结束。
下一轮处理集成剩余失败；通用OAuth登录与完整A–F目标仍未完成。

最新：tools完整单元896通过7.17秒，两旧搜索预算/广告预期已修正；插件两文件
61096结束231通过111.97秒，含禁用普通MCP触发不得连接断言。无生产改动，
静态检查通过。下一段92集成模块session97514已启动，下一轮先poll，不重复启动。
A–F整体仍开放。

最新：广域75098结束408通过23跳过8失败142.21秒，插件安装重载测试仍依赖Apps
写入；已改为禁用普通MCP配置触发，39通过12.27秒。随后补不得连接trigger断言，
联合plugin_config_reload session61096仍live超过31%，下一轮poll。工具单元全套
结束894通过2失败7.73秒，test_search两旧预期尚待处理。无生产改动，静态检查
通过，整体A–F目标仍开放。

最新：完整模型单元346通过7.20秒；core/context/memory单元711通过25.65秒，
无生产或测试修改。Codex commit及干净状态重新核对。原广域session75098仍live
超过15%，下一轮继续poll，不重复启动；不能将单元通过外推A–F全链路完成。

最新：广域11292结束8失败5.03秒，均搜索生命周期旧能力门控预期；已迁移并保留
模型更新/重试相同请求/三checkpoint边界/一次输入与工具执行，三文件88通过
12.37秒。无生产改动，静态检查通过。剩余114集成模块session75098仍live超过3%，
下一轮先poll同句柄，不重复启动。A–F完整目标继续开放。

最新：剩余广域45229结束68通过8失败12.76秒，搜索门控/冷切摘要旧预期已迁移。
搜索/压缩/缓存/Step刷新四文件86通过11.07秒；再补重搜前不可调用替代定义等
四项通过1.08秒。通用OAuth七文件142通过6.37秒，但浏览器授权/注册仍未完成。
无生产改动，静态检查通过，所有句柄结束。下轮从model_search_compaction之后
继续剩余范围，A–F完整目标仍开放。

最新：广域91737结束441通过28跳过8失败163.96秒，旧external_events已迁移，
与线程记忆/typed准入175通过25.76秒。后续83476结束43通过24跳过8失败8.99秒，
旧reasoning_defaults已按普通协议迁移：联合96通过12.79秒，随后显式host能力
注入断言的完整37通过5.14秒。无生产改动，静态检查通过。所有句柄结束，下一轮
检查reasoning_defaults之后剩余模块；全项目及A–F仍未完整验收，目标保持开放。

最新：CLI联合122通过27.41秒；其后真实入口扩展40/100列×Ctrl+C/D四组合通过
5.39秒，含/status回到输入框与正常退出。无生产改动，静态检查通过。Codex当前
双按退出开关false，按默认单按路径验证。剩余159集成模块session91737仍live
超过14%，下一轮poll同句柄，不重复启动；未结束不能计为通过，A–F继续开放。

最新：媒体上下文按普通摘要/身份及旧归档只读边界迁移，八文件190通过24.38秒，
静态检查1040文件/72依赖通过；无生产改动，无live测试句柄。保留四旧配置及媒体
支持组合、位置/内容/降级、一次工具执行、冷恢复、原档前缀不变。已修复此前全套
停下的media_content_kinds旧预期；其余全项目和A–F差异仍未完整验证，目标继续开放。

最新：Runtime自建记忆client的普通HTTP提取/合并/下一Turn注入新增6项通过，含
全部owned client关闭；联合记忆模型/预算/长期记忆52通过26.18秒，无生产改动，
静态检查通过。全套72012已结束3522通过423跳过10失败777.58秒，停在
media_content_kinds旧私有metadata预期，XML报告已核对。全部句柄结束；下一批
追踪媒体测试，不恢复排除项。全套早于能力修改且未跑完，A–F目标继续开放。

最新：线程更新/保存profile冷恢复验收区分显式能力与配置身份，五文件81通过
11.31秒，无生产改动，静态检查通过。全套72012继续live超过26%，加载早于能力
清理与测试迁移；不能代替最新版定向证据。下一轮poll同句柄，A–F目标继续开放。

最新：移除DeepSeek名称/地址自动能力推断，统一按api_mode选择默认，宿主显式
能力注入保留，README同步。修复前能力矩阵14红，修复后16 Runtime+单位共71通过
3.49秒，静态检查1040文件通过。全套session72012仍live超过20%，但加载早于本
能力修改，不能当作新版验收；下一轮poll同句柄，A–F继续开放。

最新：补充无item ID同调用内容变化反例，16身份组合通过；连同恢复/实时工具/
参数恢复四文件154通过30.57秒，无生产改动，静态检查通过。全套session72012
仍live已到7%以上（有跳过，不计通过），报告路径不变；下一轮先poll，不重复启动。
本批新增8项晚于全套加载，以154定向为证据。A–F整体目标继续开放。

最新：修复普通function无item ID时重复done错误生成双调用。added/done按显式ID/
index（含0）/call_id匹配，保留内容变化校验；typed边界迁移且正向freeform仍普通包装。
完整响应集合455通过70.14秒，静态检查通过。新项目全套session72012正在运行，
报告/tmp/corki-harness-verification.AA7oX9/results.xml，下一轮先poll，不重复启动。
旧MCP2584绿早于本生产修复，不代表本版项目全套；A–F目标仍开放。

最新：精确数字/旧归档/普通search错误回灌32项通过5.69秒，无生产改动。
响应广域88487已结束375通过8失败57.36秒，停于typed_admission的namespace及
私有字段剥离后普通调用预期，须完整追踪。静态检查通过，本批无遗留测试进程。
MCP全91模块2584绿仍有效但不代表全项目；A–F目标继续开放。

最新：完整MCP session50154已结束2584通过549.88秒，XML实际确认91模块、零失败/
错误/跳过，/tmp/corki-mcp-verification.1LeTRz/results.xml。不能代替MCP单位或全项目。
JSON边界与旧Lite配置两文件43通过5.48秒，无生产改动，静态检查通过。响应广域
最新30694已结束311通过8失败46.19秒，停于responses_number_roundtrip待完整追踪。
本批所有测试句柄均已结束。A–F完整目标继续开放，不因MCP集合绿而提前完成。

最新：buffered admission按普通摘要/普通search迁移，最终完整51通过8.83秒；
保留数字准入、坏envelope、缺终态、一次工具执行和冷原档不变，无生产修改。
响应广域最新272通过8失败43.92秒，其中2个错误分层断言已修正，6个JSON边界旧
fixture待完整追踪。静态检查通过。MCP session50154继续运行，报告位置不变，
不要重复启动；A–F全目标仍未完成。

最新：结构化正文44项、普通压缩终态108项分别通过，仅测试迁移，无生产改动。
响应广域最新246通过8失败38.12秒，停于responses_buffered_admission原生旧fixture。
静态检查通过。旧MCP PID4783已实际退出但缺最终输出，不计通过；随后新全91文件
session50154正在运行，结果写/tmp/corki-mcp-verification.1LeTRz/results.xml。
下一轮先poll50154，不因观察超时或旧日志重启。A–F目标仍未完成。

最新：响应隐私旧fixture迁移普通摘要/显式旧档/新hosted拒绝，无生产改动。
五文件79通过15.46秒（含44隐私组合），真实Runtime+普通mock端点+SQLite冷恢复，
原档前缀不变且业务同名字段保留；静态检查通过。响应广域58573已结束，5失败停于
response_body_replay旧元数据/远程压缩前提。MCP全91文件PID4783仍经ps确认运行，
原工具session_id因上下文截断未恢复；不重复启动、不虚报最终计数。A–F继续开放。

最新：MCP最后30文件完整遍历557通过6失败138.07秒。两旧native历史预期已迁移，
四共享目录失败为审批替身签名，补齐后相关三个文件20通过3.76秒。
保留权限撤销零RPC、当前连接/当前审批、重搜与冷原档不变；无生产改动。
静态检查通过，所有进程已结束。尚未统一重跑完整MCP集合为绿，A–F目标继续开放。

最新：插件静态feature gate迁移普通授权重载后144通过；审批中普通重连保持原client
lease的18项及pending复用15项共33通过，revision锁取消3项通过。无生产改动。
分段40文件286通过10失败59.10秒，定位的旧reload和替身签名已定向修正；后半段尚未
全部执行，不代替全套。所有本批测试进程已结束，A–F完整目标继续开放。

最新：从invalid_plugin_policy_reload之后51文件继续MCP后半段，252通过10失败
42.57秒，停于orchestrator_tools旧服务过滤。迁移后该文件与source_discovery共15项
通过3.60秒，含普通双服务调用/冷历史不变、原生响应拒绝、来源变化重搜并保存原档。
无生产改动，静态检查通过，本批进程全结束。后半段未跑完，最近完整MCP广域仍
1248通过10失败；不能把分段计数当新全套结果，A–F目标继续开放。

最新：插件策略重载测试改用声明的普通MCP授权触发user层重载，不再依赖Apps写入。
180组合全通过91.69秒，保留畸形map整体回退、声明限制不放宽和修复后撤销write。
普通持久化42通过、插件策略单位40通过；新增磁盘断言子集36通过（与180重叠）。
静态检查通过，无生产实现变更、无遗留测试进程。未重跑广域或项目全套；最近MCP
广域仍1248通过10失败，所定位插件用例本批已定向通过，A–F完整目标继续开放。

最新：普通搜索路由清除残留不可达原生能力门控，新增能力False组合12项；曝光/
路由共344通过32.25秒。旧orchestrator服务过滤单测同步统一普通身份。
MCP广域1248通过10失败208.43秒：HTTP旧native定义期待四项已迁移、loopback全8通过；
invalid_plugin_policy_reload六失败待核对普通持久化/插件重载，不恢复Apps树。
静态检查通过，所有测试进程已结束，未重跑广域或项目全套。A–F完整目标仍开放。

最新：MCP广域674通过10失败120.25秒，停于exact_call_metadata旧connector身份/
link_id专属校验。已迁移普通raw身份、UI隐藏/审批/原样参数/显式刷新78组合，全通过；
绑定/曝光/投影/visibility四文件另363通过31.72秒。无生产改动，静态检查通过。
本批全部进程已结束，未重新跑广域；最近项目全套仍1157通过423跳过10失败。
A–F完整目标仍未完成，后续继续定位广域剩余差异，不恢复排除项。

最新：MCP广域258通过10失败44.97秒，停于旧Apps策略期待。本批迁移为32旧配置
×三模式×普通auto/prompt对照192项，加session/cancel18项，共210通过30.36秒。
验证旧Apps不隐藏/授权普通工具，显式普通MCP审批仍能拒绝、零RPC，SQLite与
Observation完整；无生产行为变动，仅测试和审批模块说明修正。静态检查通过，
所有本批测试进程已结束。未重跑广域或项目全套，A–F整体目标仍开放。

最新：旧Apps hard refresh/initial reconnect验收已迁移普通显式刷新与启动失败路径。
连同refresh_runtime/session_recovery共32通过5.40秒，覆盖两名称、CodeMode、采样中
刷新、旧资源快照/新函数客户端、required失败零采样、optional显式恢复和关闭。
本批无生产代码改动，无遗留测试进程；未重跑全套，最近全套仍1157通过423跳过10失败。
该次失败用例本批已定向通过，不代表其余项目全套或A–F已经完成。

最新：主图连接重试删除bedrock/amazon_bedrock名称排除条件，统一由显式开关决定；
修复前四反例失败。两普通HTTP传输×五标签×三模式及恢复/HTTP分层共75通过，
取消/steer/响应错误另35通过。README/development已同步，静态检查通过。
全套79482现已结束：1157通过423跳过10失败244.15秒，失败集中旧Apps hard refresh
与initial reconnect用例；下一步核对普通MCP刷新和初始化失败语义，不恢复Apps入口。
全套加载早于本批修复，新增行为以定向测试为准；无遗留运行句柄，A–F仍未完成。

最新：connector投影/旧名称冷恢复/inventory测试按统一普通协议迁移，相关六文件
92通过15.59秒；搜索加载、同Thread再调用、冷恢复原始RPC、未知副作用不重放、
归档前缀不变均保留。旧native/Lite配置不得恢复失效定义或专用wire事件。
本批仅测试与审计改动，无生产代码改动；ruff/1039格式/compileall/72依赖/diff通过。
所有本批测试句柄已结束。未重跑项目全套，上一全套1135通过423跳过10失败仍是
最近全套记录；本批关闭其connector旧期待，不代表剩余全套或A–F目标完成。

最新：最终模型结果先通过契约校验，再记录记忆使用反馈；避免被拒绝的hosted、
跨Turn或重复item仍影响使用统计。新增反例修复前3失败，有效引用对照保留。
边界/引用36项、恢复/流工具/core/storage/retention139项通过，静态检查通过。
项目全套32849已结束：1135通过423跳过10失败，停在旧Apps connector命名投影
测试；其旧专属重命名/过滤期待需按普通身份路由迁移，不能恢复官方分支。
全套加载早于本批修改；本批修复以定向证据为准。A–F目标仍开放。

最新：主循环新增hosted接收边界修复。自定义Model返回新HostedToolItem也必须协议
失败，不能绕过HTTP限制；旧持久化partial/committed只读兼容，不重写原档。
新反例修复前2失败；结合HTTP排除搜索/流中工具/恢复114项通过，补强边界与
core/sessions/storage106项通过。静态检查通过；首批主循环定向另39项通过。
没有重跑项目全套，A–F完整目标仍未完成。

最新：Agent Plugin目录预算已去除namespace包装计数，使用两种普通函数定义的较大
UTF-8字节数；8KB单项/64KB累计、schema fallback和隐藏策略不变。新增等值单位
测试修复前4失败；真实插件Runtime刷新验证8000可执行、8001全曝光面隐藏。
相关schema/预算/插件/刷新六文件131通过，静态检查通过。未重新运行全套，
A–F完整目标仍未完成。

最新：旧Apps auth-result测试已替换为32项普通错误/不透明metadata验收，无授权提示、
自动刷新或RPC重放；保留完整SQLite错误与冷恢复，模型key不流向MCP模拟端点。
通用auth_result/OAuth刷新/会话恢复35项通过；elicitation36项通过，refresh旧native
期待迁移后全4项通过。静态检查通过，无生产OAuth改动，未重新运行项目全套。
下一源码差异：agent-plugin目录预算仍按namespace包装计数，待核对普通平铺实际
占用与Codex预算意图。A–F目标仍开放。

最新：全套24493已结束，1054通过423跳过10失败；新失败集中旧Apps认证结果/
refresh_mcp_apps_tools夹具，不能通过恢复官方Apps解决。新增真实PTY并发确认6项，
与原单面板共12通过，覆盖两宽度、FIFO、分别提交、排队取消、草稿与普通历史恢复。
静态检查通过；仅TerminalUI+InputOwner组件证据，不冒充完整应用权限授予/视觉等价。
A–F完整目标继续开放。

最新：Memory工具旧native夹具改为普通flat function，仍输入旧namespace/Lite配置
验证其不能重启协议；25组参数/结果契约与CodeMode/独占/冷恢复共106项通过。
读取策略冷恢复补强工具名断言；与故障测试60通过。最终Memory广域841通过35跳过
（6311 deselected），不代表项目全套或Memory每项源码差异完成。静态检查通过，
A–F目标继续开放，开始重新执行项目全套以定位下一批核心差异。

最新：Memory 阶段模型不再默认固定 GPT 型号，选择顺序为显式阶段→显式 provider
首选→用户主模型。README已同步。两传输、openai/independent 名称均验证普通模拟
端点；chat无schema/effort能力也能走提示词JSON提取、校验、冷召回和失败隔离。
预算用例同时覆盖默认8K主模型合并失败但主会话可继续，以及显式64K合并正常发布，
不隐式改换模型。定向四文件60通过，扩展生成20与预算21通过；config全616通过1跳过。
静态检查通过；最新Memory广域702通过34跳过10失败（maxfail10），新失败集中在
memory_tool_protocol的旧native namespace响应夹具，下一批按普通平铺调用核对，
不得恢复原生协议。A–F整体仍未完成，不把局部结果算作全局验收。

最新：本地历史召回剩余四项已迁移为独立摘要/业务请求计数；原始畸形参数、零handler
执行、长输出尾部offset读取和notes冷恢复断言保留。local_history_notes16项全通过，
联合归档/new_context/pending恢复等42项通过，静态检查通过。无生产改动，未重跑
全套，A–F整体仍未完成。

最新：live_tools普通raw包装流中执行、断流后等待结果/不重放等21项通过。本地notes
首组真实HTTP write→summary→read→cold read及九动作通过；全文件12通过4失败，
剩余畸形调用/大输出/plaintext召回待迁移检查。静态检查通过，无生产改动。
全套与A–F仍未完成。

最新全套824通过334跳过10失败：live_tools freeform两项、本地history_notes八项，
下批核对普通工具包装和本地摘要历史恢复。CLI定向85通过62跳过；新增真实TerminalUI
PTY六项通过，覆盖40/100列、确认/拒绝/取消、默认不提交、普通输入恢复与历史隔离。
静态检查通过。该PTY未覆盖完整应用并发审批/视觉快照，A–F仍未完成。

最新：remote_compaction_runtime/failure旧opaque/trigger夹具已迁移普通摘要终态、
部分流取消/重试和冷恢复。摘要夹带function_call不执行；旧V2=true也不启用专用
协议。联合六文件69项通过，静态检查通过，无生产改动。未重新运行全套，不能
宣称排除项或A–F全部验收完成。

最新：手动/跨Turn/工具后摘要×openai/independent六场景普通HTTP+冷恢复已验证，
保留用户/归档、不重复工具、不回传私有routing header；联合相关83项通过，静态
检查通过。remote_compaction_runtime/failure仍有旧trigger/opaque/单次失败期待，
定向5失败已定位为下一迁移批次，尚未重跑全套，A–F继续开放。

最新：legacy_compaction_failure已迁移普通摘要SSE契约，覆盖10故障×耗尽/恢复及
取消/关闭。503两层预算、其他摘要重试、消息done后ReadError、空摘要失败、原始历史
不变与恢复后单marker均验证。相关四文件52通过，最终加强body注入后22项重跑通过，
静态检查通过；本批无生产改动、未重跑全套，A–F整体仍开放。

最新全套复查758通过334跳过10失败：上下文日志两项是旧测试补造私有metadata，
已移除辅助加工，完整文件6项通过；其余8项旧专用压缩失败夹具待迁移。记忆生成/
召回/故障/关闭定向63项通过，不能据此宣称整个Memory完成。静态检查通过，A–F
完整目标与全套验收仍开放。

最新：普通Runtime摘要接入真实TCP交接/TLS握手取消测试，支持chat/responses；
取消/关闭只产生一个取消终态，无压缩marker、重试、遗留socket/读取任务。旧reset
密文测试迁移普通摘要并保留当前输入。相关20+29+35=84项通过，静态检查通过。
上一全套最先10个失败已逐批迁移并定向通过，但未重跑全套；A–F仍未完成。

最新：全套maxfail10复查696通过333跳过10失败，尚未全绿。媒体准备不再给assistant
正文制造私有metadata；真实媒体降级保持位置和id。unit/models及音频/CodeMode/
冷恢复380通过，图片Runtime普通端点与执行账本11通过。图片旧native夹具改普通
function并显式配置URL，绝不恢复默认官方地址。HTTP取消旧compact与encrypted
output旧reset计数等剩余失败待迁移；A–F仍开放。

最新：普通请求预算不再计入不出站的内部分类/私有metadata/摘要分类，也不把发现
定义重复计入Observation；普通id、正文、附件和已加载tools成本保留。三组不重叠
回归498+76+24=598通过，含热/冷恢复与工具发现执行；静态检查通过。未运行全套，
内部媒体分类生产与旧协议用例仍待整理，A–F整体目标继续开放。

最新：旧server_reasoning_included标记不再从SQLite恢复为运行时策略，估算器也不再
读取它；原始model-step与历史保持不变。冷恢复触发普通摘要、当前reasoning去重、
窗口失效及存储/压缩相关566项通过，静态检查通过。内部不出站分类/metadata的预算
残留待继续审计；未运行全套，整体A–F仍未完成。

最新：移除实时私有usage_metadata、codex_rollout_budget_units解析和
x-reasoning-included状态缓存；普通usage与连续Turn统计/冷存储/工具失败不重放等
354项通过，静态检查通过。旧存储flag影响历史估算仍待分离，旧协议测试与A–F开放。

最新：实时Responses item入口在解码前忽略根级passthrough/encrypted_function_args，
新provenance仅保存普通id；业务嵌套字段不变。普通正文/参数变更仍失败，旧归档兼容
保持；467项及静态检查通过。44项旧privacy测试、内部媒体分类与其他专有usage/header
响应分支继续待清理，A–F整体尚未完成。

最新：移除Responses请求构建先制造私有metadata再过滤的路径。普通id/call_id、
旧MsgPack/SQLite、分组顺序及音频降级保留；完整unit/models及durable/media相关
366项通过，静态检查通过。另44项旧privacy测试依赖远程压缩/live hosted尚未迁移；
实时入站capture与内部媒体/旧归档专有metadata仍需分离，不能宣称已整体清理。

最新全套复查487通过10失败后停止；首批旧native搜索/历史回放夹具已迁移为普通
function路径。执行策略更新、热/冷恢复、raw包装、归档截断和摘要等148项通过，
静态检查通过。下一优先：清理内部响应metadata接收/存储及先构建后过滤的残留，
同时保留普通item身份与旧归档读取。不宣称全套或A–F已完成。

最新：修复File OAuth刷新误占用MCP握手/业务超时预算的问题。Runtime慢刷新矩阵、
真正RPC超时及关闭等待认证事务等88项通过，静态检查通过；实际业务预算仍生效，
取消仍join持久化。GET续连、浏览器登录/注册/keyring和整体A–F验收继续开放。

最新：通用第三方MCP的显式File OAuth已接入过期刷新，不再只是读取access token。
按服务加锁重读、issuer绑定、普通token请求、先持久化后安装、取消join与失败不重放
业务RPC已验证。159项OAuth/Runtime相关及53项HTTP恢复/headers通过，静态检查通过。
浏览器授权/注册、keyring、完整超时预算/GET续连及CLI登录仍未完成；不涉及官方账户。
以下为历史批次记录；总体A–F与全套验收继续开放。

最新验证：TokenBudget统一普通摘要后，修复自动/工具触发压缩仍向模型显示旧窗口id
的问题；身份与摘要replacement原子安装。显式new_context成功/失败/取消、marker
提交前后故障冷启动、旧pending checkpoint技能冻结、提醒去重与保存工具不重放等
11个集成文件147项通过；lint/1030格式/compileall/72依赖/diff通过。仍需继续排查
失效参数与其他旧协议测试；全套、长期记忆、通用OAuth完整流程及CLI/PTY等A–F
验收继续开放。本批未执行真实模型选择质量验证。
以下“最新”段落保留为历史批次记录，以audit最前批次为准。

最新恢复清理：专用 Apps recovery/reconnect 模块及生产连接、generation、交接中的
恢复租约与分支已删除。普通显式刷新、失败记录及连接资源交接保留；旧专用测试待整理。

最新：AppsConfig/AppsRequirements/AppsPolicyEvaluator 死模块已删除；生命周期和
策略generation测试改为普通MCP。新增两名称刷新测试发现旧 Apps recovery closing
错误，已去除manager按名称创建专用恢复控制器的入口；剩余恢复辅助代码待清除。

旧配置验收已改为不启用 Apps；持久审批42项矩阵使用普通 MCP 授权路径并通过，
仅退休专用 Apps 硬刷新用例。AppsConfig/AppsPolicyEvaluator 的其他测试依赖仍待迁移。

最新配置清理：普通启动不再解析 AppsConfig 或 features.apps，managed snapshot
不再携带 AppsRequirements；旧 Apps 树不成为普通 MCP 授权来源，原始文本保留。
普通 MCP、插件和执行约束继续校验。相关死模块/旧平台测试仍需清理。

权威目标是活动 goal 引用的文件：
`/Users/corki/.codex/attachments/020680c5-33fc-47c0-b814-c8867c52dc85/pasted-text-1.txt`。
仓库副本 [objective.md](objective.md) 已同步。本说明与旧批次冲突时，以本次修订目标为准。

用户最新澄清：Corki 是独立 Agent，不依赖 OpenAI 官方服务。保留第三方 MCP 的通用
OAuth 授权、凭据管理与刷新；排除的是 Codex/ChatGPT 官方账号与专属后端功能，
不是 OAuth 机制本身。CLI 页面风格和确认交互需参考 Codex。

保留：可配置模型接口及用户提供的 API key、通用 MCP 协议与显式 Bearer/Header、
通用 MCP OAuth、工具检索/调度、上下文与本地记忆、错误恢复和安全确认。

移除：Codex/ChatGPT 官方账号登录、账户配额、远程 history notes 等专属后端依赖。
同时明确移除原生 tool_search/tool_search_output、原生 namespace、专用远程压缩端点与
V2触发协议、Responses Lite/内部协议及专有请求头。不因第三方声称兼容而保留这些分支。
所有 provider 统一按普通兼容服务处理，即使其名称或地址为 OpenAI，也不启用额外能力。
普通 Responses 传输可以保留，但只能使用普通兼容协议。API key 与服务地址可配置，
不得静默回退到官方服务。

Apps宿主来源不再豁免环境级MCP准入策略：不同来源/名称的Runtime拒绝路径均不创建
客户端；环境/恢复/资源/OAuth相关回归178通过。Apps容量提升也已删除，所有来源统一
2048项，旧8192低层参数被拒绝；容量/刷新/环境相关103通过。Apps缓存和策略尚未
整体清除，仍待移除。

账户身份Apps缓存模块、Runtime/manager宿主入口与连接层发布/回退已删除；普通缓存、
刷新、搜索等132项及旧入口拒绝3项通过。旧磁盘缓存不动。等待调用世代混合测试已改为
普通目录缓存和显式刷新，24项通过；仍有10个旧测试文件引用已删除模块，需迁移混合
核心测试并退役纯平台断言；不宣称全量测试可通过。

普通目录缓存不再按codex_apps名称排除，Step也不再仅凭此名称强制等待。缓存/懒启动/
刷新相关103通过；仍有Apps曝光策略要求connector元数据，本批用相同完整元数据
隔离验证缓存，不能据此声称普通无connector工具已全链路统一。

后续已删除候选Apps过滤、prepared call策略执行、专用名称投影/link_id要求。无
connector及带旧元数据的两名称Runtime均走普通搜索/定义加载/调用/Observation，
扩大回归163通过。旧Apps开关不再过滤该目录；配置/持久化/orchestrator门禁和重连
等其余平台分支仍待清理，旧测试和历史身份迁移仍有缺口。

orchestrator名称门禁后续已删除：普通工具计划和资源/prompt不再按该旧开关屏蔽
codex_apps。工具/资源/环境拒绝/绑定85项通过；资源混合套件已移除Apps缓存依赖，
剩9个旧测试文件仍导入删除模块。配置字段暂兼容，不再授权或拒绝这类访问。

Runtime/manager的Apps策略参数、构造、重载及更新API已移除，generation不再携带
该策略。普通审批筛选113通过；扩大混合回归105失败/131通过，含插件/技能重载核心
断言，需先迁移其Apps审批触发再判断实现是否有缺陷。不能把这些失败全部视作纯
平台测试，更不能据局部绿色宣布完成。Apps配置解析/持久化等仍待清理。

插件/技能重载套件已将Apps审批触发迁移为普通MCP，全部31种变更×3模式共93项
通过，核心视图/恢复/优先级断言保留。此前此套件的失败源于旧平台审批未触发写入，
不是已证明的插件重载实现缺陷；通用审批/持久化中的服务名特例仍需清理验证。

审批can_remember与持久化target的codex_apps特例后续已删除。两名称完整重载矩阵
186项通过，另106项目标定位/普通审批通过。批准写入按普通声明来源定位，无普通
声明时不能把旧Apps批准当成有效目的地；旧授权不自动迁移。配置解析等残留继续清理。

审批policy_document已停止合并/验证Apps树并删除无调用effective_apps。完整重载
192项、配置专项32项通过；旧平台字段不会阻断此处普通技能/MCP更新，原文保留。
settings启动解析与managed Apps要求仍存在，尚未称全部配置清理完成。

工具检索必须保留为普通函数工具：检索 → 加载后续可调用定义 → function calling →
Observation。内部命名/分组平铺为函数定义。压缩由 Harness 通过普通模型请求生成摘要，
本地管理历史替换，不要求模型本机运行。本地历史/notes、长期记忆及通用 MCP OAuth 保留。

此前删除通用 OAuth 是范围理解错误，现已从
`/tmp/corki-removed-auth.XtJJUu/removed-auth.tar` 恢复三个实现模块、Runtime/配置接入和专项
测试/探针；逐项确认共享文件差异仅为先前删除的 OAuth 片段，未覆盖 CLI 新增修改。
恢复到292批能力：主动发现、显式 File 读取和恢复边界；完整登录、写入、刷新、Auto/Keyring
仍未实现，不把恢复称为完整 OAuth。相关测试131通过，CLI键盘确认测试包含在内。

CLI 首批将最终确认接到键盘选择面板：上下选择、Enter 提交、Esc/Ctrl+C/Ctrl+D 取消，
默认高亮不自动提交；表单值仍通过原有宿主验证。整体布局、执行审批特有选项、
流式 Markdown、状态栏和完整视觉/PTY 验收尚未完成，不能声称已与 Codex CLI 一致。

官方 quota 专用传输、记忆启动门禁、账户地址及阈值配置已删除；31 项专项回归通过，
更广记忆/配置回归1403通过、27跳过。通用模型限流错误分类与 MCP OAuth 保留。
远程 history notes 传输/入口、notes ingestion metadata、codex_backend 开关已删除；
统一使用本地归档及 SQLite notes，相关扩大回归1105通过、1跳过。
下一步：移除专有请求扩展，同时保留本地历史召回；
Responses Lite 请求投影模块/调用入口与 catalog 启用已删除，旧模型字段固定 false；
不再生成 Lite inventory。additional_tools 响应识别也已删除，664项相关回归通过；
inventory 生成/合并/旧 checkpoint 入口也已删除，扩大回归1450通过、1跳过，最终专项51通过。
Responses SDK client_metadata 及其两个窗口/turn headers 的透传已删除，inventory helper
也已删除；306项相关回归通过。item metadata 出站条件分支已删除，普通请求统一剥离，
历史记录保留；专项316通过、扩大回归及后续修正记录见audit。
turn-state路由状态及采集/回传已删除，模型/context扩大回归642通过，最终专项14通过。
响应侧内部元数据和compaction metadata及其专用协议仍待清理。
压缩Runtime主链路已统一普通模型摘要，自动/手动/旧模型切换均不按provider选远程路径；
手动节点不再准备远程工具清单。普通传输、提交边界与恢复专项80通过，预算/模型切换
相关54通过，静态检查通过。SDK专用端点及V2收集器已删除，显式旧请求联网前报错，
普通摘要/恢复/隔离相关64通过；旧响应协议及相关旧测试仍待清理；
TokenBudget显式无摘要reset仍需独立审计，不能将本批视为压缩全域验收完成。
旧opaque压缩历史已禁止从Responses重放，普通明文归档message保留；281项相关回归
通过，追加明文保留断言后专项9通过。旧窗口暂明确报错并保留原始数据，自动迁移未实现。
旧context层远程压缩请求/重试入口及专有metadata生成器也已删除，相关回归62通过，
静态检查通过；未将无生产调用的旧retention测试helper误当成当前Runtime保留策略。
旧remote_compaction_v2配置不再从TOML读取，构造兼容字段固定false。相关扩大回归
722通过、1跳过；后续优先清理Graph实际传入模型的native search/namespace入口。
Runtime搜索模式已统一普通函数：旧native配置迁移为compatible，只有成功且仍有效
的发现定义可被加载/调度；删除native计划绕过分支。搜索/恢复/排名/MCP刷新相关99通过。
SDK原生search编解码和namespace仍待删除，不将配置迁移冒充协议全域清理。
Namespace出站包装/叶名分支已删除，统一平铺稳定全名别名，旧配置/SDK参数迁移；
碰撞检测与冷恢复调用身份保留。相关集成45通过、协议/配置88通过，静态检查通过。
SDK原生search与响应侧namespace识别仍需继续清理。
SDK搜索声明/历史出站已统一普通函数，旧native参数不再启用专属出站；相关36通过，
静态检查通过。原生搜索响应解析与旧helper函数仍待删除。
原生搜索done/completed转本地调用分支及专用helper已删除，77项相关测试通过，
静态检查通过。typed decoder/Hosted展示及namespace响应字段仍是剩余清理项。
非空namespace响应不再解释为可执行分组调用，而是明确失败；平铺别名正常路由，
models/tool_search.py剩余模块已删除。相关59通过，静态检查通过。底层归档typed字段
仍保留读取兼容，不能将本批视为全部历史协议清理完成。
默认模型地址及新建配置不再指向OpenAI，缺少地址时内置adapter联网前明确失败；
宿主注入模型不受影响。相关回归699通过、1跳过，静态检查通过。用户原有显式地址不改。
OpenAI/Azure名称和域名自动能力推断已删除；同api_mode使用普通默认profile，显式
宿主schema/tier能力保留。专项55通过、模型单元285通过；其他provider适配仍待审计。
ChatGPT Apps鉴权错误合成授权UI/链接与accept后刷新入口已删除；保留原始工具错误，
旧开关无效。相关及通用OAuth回归98通过，静态检查通过。Apps平台其他部分尚待删除，
通用OAuth完整登录/存储写入/刷新仍未实现。
普通输入技能解析已移除Apps目录读取与连接器名称压制，删除对应manager方法，
相关169通过、删除后专项复验通过。Apps硬刷新/缓存/策略等其余入口仍待清理。
Apps专用硬刷新宿主API/manager实现及专用锁已删除，通用MCP刷新/协调/资源绑定/
搜索缓存相关49通过，静态检查通过。自动Apps缓存与策略仍待删除。
详细记录见 audit，不宣称全局协议验收完成。
继续对照 Codex CLI 源码完善交互与视觉。通用 OAuth 属于允许范围；官方服务功能和上述
原生协议均不属于验收要求。旧审计中的冲突待办不再执行；清理后用网络隔离和普通接口
端到端测试验证，不只检查开关默认关闭。
