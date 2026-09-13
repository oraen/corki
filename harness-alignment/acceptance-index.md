# A–F 验收索引（当前候选，不能据此宣称完成）

33685 当前已退出 1：15589 passed、3 failed、1 skipped（544.67s），日志
full-regression-33685.txt。失败为 stdin 核心终端测试的三个等待断言，需核对
yield 后仍活跃会话的观察契约；不是已暂停的 CLI 页面/交互对齐任务。
下方“33685 尚在运行”是启动时记录，不再代表当前状态，不能称全量通过。

当前生产已有自定义 world-state 接口及来源兼容修复，不再沿用下方“生产未改”
判断。证据见 extension-world-state-contract.md：608 定向通过，覆盖真实
Runtime、SQLite、冷恢复、自动/手动 compact、取消与普通 HTTP 两模式。
新非 CLI 全量 **33685 尚在运行**，必须等待实际退出；81536 是历史生产基线，
不是新接口的全量验收。其它 A–E 开放项未据此关闭，F 继续按用户要求暂停。

E7 条件资源补证：真实 reset 文件删除中取消 close 观察者，不影响共享 teardown，
模型/repository/writer 等待删除后仅关闭一次。五文件 100 passed / 10.77s，
92233 实际退出 0（0bfb81）；详见 runtime-close-failure-acceptance.md。
新增验证独立于 81536 全量计数，生产未改；不将此窗口继续笼统列作缺失。

最新非 CLI 全量 **81536 实际退出 0**（c30736）：15532 passed、1 skipped，
527.21 秒，日志 full-regression-81536.txt。唯一跳过为非 UTF-8 文件名限制。
包含本批记忆窗口及 elicitation 修订；此前 1765/1728 失败记录保留，但不再
作为当前结果。仍需完成独立 A–E 开放项；CLI 暂停。退出后新增条件资源关闭
测试正在专项验证，尚不包含于本次全量计数；生产逻辑未改。

MCP elicitation 计时边界联合验证：244 passed / 35.95s，46356 实际退出 0
（389e19），详见 elicitation-timing-boundaries.md。已区分用户等待暂停与提交后
交付超时，保持不重放/隐私/关闭断言，未改生产逻辑。1765 原始失败原因不能
精确恢复，仍保留全量失败状态，等待新全量验证；不据此关闭 A–E 整体。

最新全量 1765 已退出 1（82dbcc）：15530 passed、1 failed、1 skipped，546.65 秒。
window-recall 相关修订在全量中通过；唯一 MCP elicitation/Code Mode human-wait
失败待定因，详见当前 priorities。日志 full-regression-1765.txt。不能把前三项
定向复现通过当作修复或全量通过，A–E 仍在进行，CLI 继续暂停。

1728 的失败场景现已定向处理并联合 212 通过（34c232），详情见
window-recall-regression.md。新全量句柄 1765 正在运行，未取得最终结果；
下方 1728 仍是失败记录，不以联合测试替代全量，更不关闭其它 A–E 开放项。

最新非 CLI 全量 1728 已退出 1：15481 通过、38 失败、1 跳过，556.78 秒
（4254c5，full-regression-1728.txt）。当前全量不通过。已先处理 memory 策略/重置
三处旧窗口预期并联合 39 通过（d3e840）；其余 34 个记忆召回场景及 1 个 MCP
deadline 失败待逐项处理，详见 remaining-implementation-priorities.md 当前入口。

记忆摘要 initial-window 生命周期差异现已实施，详情与失败/定向验证记录见
extension-context-producers.md 顶部。Runtime 不在同窗口重复读取，冷恢复沿用
持久化窗口，压缩后再加载。生产已变，旧 15504 全量仅为历史基线，当前批次
仍需全量回归；不据此关闭 C1/C2 全项或其它未完成要求。

C1/C2 已确认新的生命周期差异，见 extension-context-producers.md：原生 memory
thread 指导仅在初始窗口构建读取，Corki 当前每 Step 读取并产生删除更新。
此项待修复，不因初始排序/输入身份组合测试通过而关闭。生产代码未改。

C1/C2剩余section已按生产入口拆表：context-section-inventory.md。实时会话与
文字steer、多执行环境提示、Persistent策略、V2多Agent提示及扩展producer的
来源/撤销顺序须分别核定；预留slot/普通effort字段不算实现。尚未对这些分支
作整项一致或不适用结论，不新增官方接口。本轮只有只读审计与文档更新。

C1/C2的独立旧reference子项已核定，见context-reference-compatibility.md：
按旧片段/确切窗口选择/未来默认/准入状态分别追踪来源，43联合通过（e3fdab）。
不要求导入Codex rollout格式，不以格式差异免除Corki历史恢复；其余section与
跨Hook故障未据此关闭。本轮未改生产或测试，15504全量仍是当前回归基线。

最新非CLI全量35013已实际退出0（c86532）：**15504 passed、1 skipped**，
535.66秒；唯一跳过为非UTF-8文件名限制。日志full-regression-35013.txt，
8 workers/loadfile/禁重启，与前两轮相同覆盖范围，包含全部新发布/冷恢复与
测试边界修订。下方88968/64194失败保留为历史，不再将“全量待刷新”作为
本次A5修复缺口；A5归并为逐要求已核验，其余A–E开放项不因全绿自动核销。

最新全量64194实际退出1：15499通过/1失败/1跳过，539.43秒（7ded6e）。
心跳测试已通过，新失败是子进程测试把提前yield结果当最终退出状态；延迟主进程
两例复现后修订同session观察，保留原时限/输出/资源断言，59联合通过（7ab66d）。
见process-observation-test-boundary.md。修订后全量待刷新，不能把两轮失败
拼接为全绿；当前无待观察测试进程。下方所有运行中记录均为历史状态。

最新全量88968已实际退出1：15497 passed、1 failed、1 skipped，544.59秒
（5bc64a）。失败为记忆心跳测试run_once总1秒超时；慢准备两例稳定复现后
修订计时起点，保留故障后1秒停止与不发布断言，339联合通过（46b415）。
见memory-heartbeat-test-timing.md及full-regression-88968.txt。全量尚未重验
通过；下方88968“运行中”均为历史状态，不得重新轮询已结束句柄。

前缀已发布/尾部未完成的 batch/streamed **真实进程退出冷恢复**已补证：
50联合通过（df9384），历史不重排、旧调用不重放、尾部unknown不冒充成功。
非 CLI 全量 **88968 运行中**，必须观察原句柄至退出；不是全量已通过。
详见 ordered-tool-publication.md 顶部。本轮全局格式检查已恢复通过。

A5 新确认并修复：工具结果从整批等待改为有序前缀逐项发布，batch/streamed
两反例转绿，发布前/后存储失败尾部 join 有证据。调度/输入/恢复联合196通过，
core/storage/Hook等联合686通过，均退出0；见 ordered-tool-publication.md。
74939 全量早于本次 graph/live_tools 修改，不能视为新修复的全量证据。
下一步补前缀发布后的冷恢复窗口并刷新非 CLI 回归，CLI 对齐继续暂停。

最新回归为74939，实际退出0（92002f）：15490 passed、1文件系统限制跳过，
547.32秒，非CLI unit+integration、8 workers/loadfile/禁重启；日志
full-regression-74939.txt。已包含模型快照、环境/目录深度异常生产修复及工作
状态新增组合测试。取代下方79918/15450等历史基线，不自动核销行为开放项。

当前回归基线更新：79918 实际退出 0（941983），非 CLI unit + integration
15450 passed、1 skipped，532.06 秒，8 workers/loadfile/禁重启。
唯一跳过为文件系统拒绝非 UTF-8 文件名；完整日志 full-regression-79918.txt。
包含近期 compact Hook 待写取消终态及 personality 损坏旧状态/可见历史投影修复。
下方“15397 全量早于修复、尚需刷新”的记录已被本轮回归取代；各项行为状态
不因全量通过自动升级为完成。E4 兼容选择及其它具体开放项仍保留，CLI 暂停。

A7旧无marker会话新增Interrupt计划兼容识别，两误续跑反例转绿，四文件73通过
（c292de）；新旧记录均保持模型/Hook不重放，见interrupt-hook-gap.md旧兼容节。
全部持久字段损坏及无证据历史限制未冒充完成；CLI暂停。

A1/A7/E7取消控制与展示解耦：marker关闭、终态失败的冷恢复两反例先红后绿，
新意图记录阻止重采样并保持Hook已知/未知事实，无Hook同样有效。初批72通过，
扩展四文件66通过（db741b），见interrupt-hook-gap.md当前修复节。旧无marker
半提交迁移等仍开放，CLI对齐暂停。

Interrupt专属metadata与官方executor插件allowlist已追踪到实际定义/过滤链，按目标
排除，不作为必补缺口；普通Turn模型与Step metadata分离。HTTP MCP测试增加精确
_meta与无x-codex头断言，见interrupt-hook-gap.md范围纠正节。通用插件授权/冷恢复
仍开放，不以排除官方产品代替核心验收；CLI暂停。

A4/E7 Interrupt通用HTTP MCP中断/关闭十场景通过：身份模板、错误/超时降级、
未信任隔离、唯一取消终态与客户端回收；五文件60通过（b7fcea），见
interrupt-hook-gap.md顶部。仅补离线验证，最后Step来源/冷恢复仍开放；CLI暂停。

A4/E7关闭中Interrupt异步准入两反例先红后绿（c9e19e→a68146），将共享Hook owner
封闭移到活动Turn完成中断处理之后，仍立即禁止新Turn/输入。六文件101通过，见
interrupt-hook-gap.md当前关闭节；其它生命周期/恢复仍开放，CLI对齐暂停。

A4/A7/E7 Interrupt基础分发已实现：同步/异步×root/child/review×中断/替换12场景，
解析与发现专项合计38通过（fdb911）；此前九文件151通过（fb9423）。见
interrupt-hook-gap.md当前节。最后Step来源、真实MCP/关闭/冷恢复仍未全验，CLI暂停。

A4/A7/E7 Interrupt 源码审计与实际取消反例：root interrupted 脚本未执行，子 Agent/
replaced 五个阴性对照通过（a5cfdc）。见 interrupt-hook-gap.md；生产尚未接入，
不能称取消生命周期已完整对齐或当前测试全绿。CLI 对齐暂停。

A4/A7/E7 SessionEnd待落盘输入顺序两反例先红后绿：首次/再次close均在输入提交后
执行一次脚本，模型关闭一次。两文件35通过（a1365f），既有关联101通过（49fdf0），
见session-end-gap.md当前补充。当前全量及其它生命周期仍开放，CLI对齐暂停。

A4/E7 SessionEnd基础关闭执行已接入，新增stream_close通知出口。原root反例转绿，
专用超时/async/子Agent隔离/观察者退出专项16通过（b514f7），相关101通过（d4a071），
见session-end-gap.md当前节；账本/资源故障交错等未整体验收，CLI继续暂停。

A4/E7 SessionEnd源码及真实关闭反例已审计：root两例未执行可信脚本，SubAgent四
阴性对照通过（df164c）。见session-end-gap.md；生产尚未接入，不能称全部生命周期
支持，CLI继续暂停。

A4/A7取消compact start后旧unknown阻塞新Turn两反例已修复，同Runtime/冷开均继续
接收输入且不重放Hook，相关34通过（f2ad37）；见session-start-gap.md。依据原消费
Turn持久终态释放跨Turn义务，不把unknown改成功。其它取消/持久窗口仍开放，CLI暂停。

A4/A7/C8压缩start同步冷恢复manual/auto四提交窗口×stop/allow新增16例，脚本/
摘要/工具不重放、输入身份与反馈保持。新文件22通过（06cb78），相关92通过（f83814），
见session-start-gap.md；仅补证，登记/consumer绑定及其它生命周期仍未整体关闭。

A4/C8压缩后SessionStart已接入source登记/消费、即时请求context与成本刷新、正常
stop及输入准入保护。五文件103通过（345e5e），最新专项10通过（176700），见
session-start-gap.md当前节；消费故障窗口/clear/其它生命周期仍开放，CLI继续暂停。

A4/C8压缩后SessionStart的manual/auto×continue/stop四反例已红（55ee69）：
安装摘要后未触发；方案需同时接入source队列与注入后模型历史预算，见
session-start-gap.md顶部。当前生产未修复，不把先前初始入口通过等同本项完成。

A4 SessionStart/SubagentStart初始生产链已实现，startup/resume原反例及三窗口冷恢复、
子Agent身份/控制分离已验。新功能37通过（d14f90），相关prompt/MCP78通过（599ab3），
见session-start-gap.md当前节；compact/clear来源等仍缺失，不称完整A4/A7/C8关闭。

A4 SessionStart/SubagentStart缺口已追踪到Runtime：startup/resume×context/stop新增
四反例失败于可信脚本未执行（ceb885），见session-start-gap.md。当前未修复，下一步
接入来源队列及持久执行/准入；不要将仅事件元数据承认判作支持。CLI继续暂停。

A4/A7异步UserPromptSubmit采样checkpoint首请求丢反馈已修复，持久选择集合再投递，
恢复时更新模型请求；选择计划/delivery receipt提交后再次中断冷开已验证。
相关五文件90通过（df966d），详见user-prompt-submit-gap.md顶部。此为分项证据，
其余生命周期/压缩交错及当前代码完整回归仍开放；CLI对齐按用户要求暂停。

D3重置交接新增新owner空输出/失败状态：旧成功/空输出/失败回调均不能改写
新状态、重试预算或结果，事务回滚后重试同样成立；新增12例，联合真实Runtime
重置/执行互斥35通过（2cad44），见memory-reset-handoff.md。仅补测试。

B8/B9结果契约新增六个Runtime场景：错身份、NaN/Infinity元数据、dict/None
返回均形成原调用的持久错误Observation，工具一次执行、模型继续。相关联合
95通过（d32972），见tool-result-contract-review.md；本批仅补验证。

摘要401与402边界复核：原生compact.rs独立重试包含认证错误，Corki同路径；
用户要求的402例外仍立即失败。新增两HTTP接口×成功/耗尽四例，联合摘要重试
23通过（27cca1），见payment-error-review.md。本批未改生产，不关闭全部E3。

异步Pre新增direct/nested×手动/自动/失败重试压缩六组合：原反馈进入摘要，
第二次冷开不复活，原始归档与工具/脚本单次效果保留。联合恢复与压缩116通过
（270783），详见async-pre-tool-review.md；仅补测试，完整A7/C8仍开放。

近期核心改动后的扩大回归已退出0：session65002，14337通过、7跳过，
2284.01秒（1ab422），见core-regression-current.md。范围排除CLI及e2e；
本批未输出逐项skip原因；后续同环境定向重现103通过/7跳过，配置经哈希
核对的真实旧编译器后109通过/1文件系统跳过（ba3b8e），详见同文。
运行中loop的同步create兼容缺口及
其他A–E开放项不因回归通过自动关闭。

最新自动压缩安装补验：大工具结果触发摘要，正常/提交前/事务内/提交后四
路径验证冷恢复不重跑工具，已提交摘要不重采样、原始归档及当前输入保留。
上下文unit/手动压缩/Pre压缩/异步Hook联合489通过（7c2183），全静态通过。
见summary-cancellation-review.md；未改生产，不称全部取消交错或A–E完成。

最新C8安装验证：手动摘要恢复扩展非空约束/回答历史，并在真实SQLite事务
插入marker后抛错，确认整个窗口回滚、冷重开再完整安装；已提交窗口不重采样。
五窗口组合30通过（d369e8），此前相关联合54通过，静态通过。见
summary-cancellation-review.md；本批未改生产，不冒称自动安装/OS强杀验收。

最新投递损坏处理：有效+缺失ID计划原会部分提交，两反例先红后修复为整份
可用性预校验。相关82通过（cdade1），全src/tests静态1220文件及编译/依赖
检查通过（d016fb）。见async-pre-tool-review.md；不称全量pytest或A–E完成。

最新checkpoint投递竞态修复：计划保存时新完成反馈不再被计划外确认，保留
至后续边界；恢复队列顺序变化也可仅确认选中项。两反例先红，四组件场景
通过，Runtime checkpoint/Pre/Post联合78通过（d1d538），静态检查通过。
详见async-pre-tool-review.md；完整A–E继续开放，CLI暂停。

最新异步Pre checkpoint修复：恢复采样入口接持久投递计划，原Pre缺context
反例已绿，投递后检查点更新前再重开验证通过；相关182通过，最终静态通过。
见async-pre-tool-review.md。完整投递取消/损坏/压缩/新完成交错矩阵仍开放，
不以本批关闭A7/C8；CLI暂停。

新确认异步Pre checkpoint缺口：实际脚本成功提交后从call_model冷恢复，
首次模型请求缺context，Post对照通过（3f3bab，1失败1通过）。新Turn冷恢复
的99通过不覆盖此路径。见async-pre-tool-review.md；需实现可重入的Pre
checkpoint反馈投递，当前未修复、不称全绿，CLI暂停。

最新异步Pre冷恢复验证：direct/nested×六种投递/关闭/提交故障路径12项
通过，各冷场景再次重开无重复脚本/context；未知结果保留可信配置仍不重放，
真实PID已回收。Pre解析/并发/Post联合99通过（a89361），静态检查通过。
见async-pre-tool-review.md；本批仅补验证，不冒称新生产修复或OS强杀验收。
checkpoint跳过prepare、压缩安装交错等仍开放，CLI继续暂停。

最新异步调度修复：Pre/Post/Stop原各有8个名额，真实Runtime owner反例测得
24项同时启动（0441d1）；现共享会话8名额，释放/关闭两路径先红后绿，相关
79通过（f23749），静态通过。见async-pre-tool-review.md。新增证据为混合
owner调度测试，不冒充完整模型驱动混合脚本验证；其他A–E开放，CLI暂停。

最新异步Pre实施：Graph准入/采样反馈与Runtime关闭已接入，两真实direct/
nested反例已通过；异步控制不能改写参数，25解析用例通过。Pre/Post/Stop
联合269通过（bb8689），改动文件静态检查通过。见async-pre-tool-review.md；
下段“生产尚未修复”是前置反例记录。Pre冷恢复与共享并发等仍未验收，不能
称A–E完成或最新全量全绿；CLI继续暂停。

最新异步Pre审计与反例：Codex实际支持异步Pre提供context但不控制工具，Corki
仍跳过配置。真实direct/nested两项均未启动脚本而失败，未xfail；见
async-pre-tool-review.md。生产尚未修复，异步Post的通过不能代替Pre实现。
CLI继续暂停，当前不宣称测试全绿或整体完成。

最新关闭所有权补验：六个重复/并发close观察者取消场景通过，工具cleanup仅
收到一次取消；与Runtime、MCP readiness和Code Mode取消联合83通过，静态
1214文件通过。见parallel-error-barrier-review.md。本批仅补验，不宣称所有
A5/E7完成，不恢复CLI对齐。

最新A5/E7修复：fatal sibling清理期间再次取消Turn会打断另一工具finally，
batch/streamed两反例已先红后绿。LiveTools关闭改为独立共享join，不重复取消
清理中的子任务；联合70通过，新增单一TurnCancelled终态两项通过，静态1213
文件通过。见parallel-error-barrier-review.md；其他核心矩阵开放，CLI暂停。

最新核心事件纠错：原生should_emit_hook_notification只允许同步通知，之前异步
Post发HookStarted/Completed是错误实现。两反例先红后修复，改为当前Turn警告+
原来源context，不发同步通知；联合99通过，新增警告归属专项60通过，静态
1213文件通过。见async-post-tool-review.md；旧异步完成通知描述不再作一致证据。
本项属主循环事件契约，CLI外观对齐仍暂停，A–E整体仍未完成。

最新异步自动/失败压缩证据：12新增组合通过，自动跨Turn冷prepare和首次摘要
失败历史不变后手动重试均接真实Runtime；联合105通过、全静态1213文件通过。
首次失败为测试usage=None夹具错误，非生产修复。见async-post-tool-review.md；
安装崩溃/取消与执行中交错等仍开放，不能据此关闭整体C8/E7，CLI继续暂停。

最新异步压缩验证：六个真实Runtime场景确认反馈/工具进入普通摘要、原始历史
保留、手动压缩后冷重开不复活反馈且不重放脚本，联合93通过，静态1213文件
通过。见async-post-tool-review.md；自动/失败/安装崩溃的异步交叉矩阵未由此
关闭，其他A–E缺口仍开放，CLI继续暂停。

最新异步运行中关闭验证：6个真实脚本PID/任务回收、可信配置保留后冷启动及
再次冷启动场景通过；账本保持未知结果，不重放、不伪造反馈。完整异步Post/
Stop/receipt联合63通过，静态1213文件通过。见async-post-tool-review.md；
这不是OS强杀/所有取消证明，压缩及其他A–E开放项仍待验收。CLI保持暂停。

最新异步投递故障验证：context提交后及receipt前/后故障，真实Runtime冷恢复
新增18项，异步组合共36通过；receipt严格版本反例2红后修复，相关57通过。
详见async-post-tool-review.md；事件emit到receipt间故障可重复完成事件，
不重复脚本或模型context。全静态1213文件通过；取消/关闭和压缩矩阵仍开放。

最新异步Post冷启动：新增六个真实Runtime反例先失败，跨Turn读取已保存结果和
投递receipt接入后18组合通过；六个二次冷启动检查无重复事件/context/脚本通过。
同步Post/恢复/压缩/storage联合183通过，全静态1212文件通过。见
async-post-tool-review.md；投递再中断、损坏/历史兼容、压缩及关闭矩阵仍开放，
未以正常关闭重建冒充OS强杀验证。CLI继续暂停。

最新异步Post实施：Session后台执行、非控制输出解析、prepare/finalize投递已
接入；6反例修复后同步Post/恢复联合132通过，再增跨Turn配置移除场景，12
异步Post与Stop/解析/压缩联合64通过。全静态1212文件通过。详见
async-post-tool-review.md；冷启动跨旧Turn投递、完整取消/关闭/压缩矩阵仍开放。
下方“尚未启动”是修复前记录，不再代表当前暖Session行为；CLI仍暂停。

最新异步Post反例：真实Runtime direct/nested×三类控制输出6项均失败，尚未
启动脚本，见async-post-tool-review.md与test_async_post_hooks.py。未加xfail，
未修改生产代码；接下来必须实现异步所有权、投递与恢复，不能称当前测试全绿。

扩大回归已结束：14193通过、7条件跳过，session57623退出0；见
core-regression-2026-09-12.md的终态及跳过原因。范围不含CLI及e2e，不关闭
仍开放的A–E差异，也不把跳过当通过。
异步Post完整调度/安全投递边界已追踪并记录async-post-tool-review.md：现有Stop
队列只回传诊断，不能当成Post上下文实现；尚未修改生产代码，缺口保持开放。

最新同步Post解析修复：10反例先红后绿，严格wire字段/类型与停止控制优先级、
空停止文本和stop事件条目对齐；修正遗漏hookEventName的测试夹具。真实Runtime
新增6项，解析/执行/冷恢复/压缩联合160通过（660025）；async和完整旧输出迁移
仍未验收。详见post-tool-use-implementation.md；CLI保持暂停。

最新工具/Post原子提交：原工具结果与准备好的Post batch同事务落盘，脚本在
提交后执行；六个SQLite/volatile事务故障/成功场景证明不留半提交。Post冷恢复
及基础链120通过，storage/Pre/压缩/Post联合276通过。扩展主循环回归发现并
修复首checkpoint未创建时缺checkpoint_id的旧遗漏，最终52通过（0513ed）。
静态1209文件/77包通过。准备阶段/取消/损坏/跨Turn与async Hook等仍开放，
不将外部副作用与SQLite事务宣称为全局原子。详见Post实施文档；CLI仍暂停。

最新Post压缩证据：新增12真实Runtime组合，覆盖普通/嵌套、短/溢出反馈、自动/
手动/摘要失败重试，安装摘要后冷重开不复活原Hook上下文、原历史保留且脚本一次。
Pre/Post共24项通过，storage/executor/压缩/Post基础链联合188通过（7a573a）；
静态1208文件/diff通过。本批未改生产代码；压缩安装中途崩溃等故障矩阵仍开放，
不把成功冷重开等同全恢复验收。详见post-tool-use-implementation.md。

最新恢复幂等性：按checkpoint保存待投影反馈ID清单，先记意图再写历史，同一
checkpoint重试不丢反馈，新checkpoint不无条件回填旧片段。含恢复写入前失败、
写入后重复恢复与冷Runtime续跑的Post108场景；与Post/Pre恢复及Pre压缩联合
228通过（ac9f27），静态1208文件/77包及compileall/diff通过。真实Post压缩
故障安装、旧清单兼容与完整跨Turn/损坏矩阵仍开放；详见Post实施文档。

最新yield恢复：exec/wait保存内部cell生命周期，区分yield与脚本完成；冷恢复
新增反馈同步到待执行模型checkpoint。新增yield反例初次8失败28通过，修复后
Post/Pre/CodeMode/存储联合193通过（5397bb），静态1208文件/77包通过。
恢复append→checkpoint更新间再次崩溃仍有已知缺口，不能宣称原子性完成；
旧yield元数据、完整跨Turn/损坏矩阵仍开放。详见post-tool-use-implementation.md。

最新nested Post阻断恢复：宿主保存父脚本call_id，父ledger未完成时独立恢复
阻断/未知反馈，保留外层unknown、不重跑；父已完成则不重复注入。冷恢复六项
xfail已移除，Post/Pre冷恢复与CodeMode联合156正常通过（2dbd32）。静态1208
文件/77包及compileall/diff通过。后台yield/cell-wait归属、旧batch无parent、
提交到batch前窗口及完整损坏矩阵仍开放；详见post-tool-use-implementation.md。

最新Post上下文恢复实施：独立post_hook_recovery接入Runtime，恢复已提交的
additionalContext，校验原输入归属/保存预算并复用稳定ID；嵌套外层未知不再
丢失这部分context，不重跑脚本。冷测试新增context类12项正常通过；Post/Pre
联合126通过6预期失败（207907），剩余六项是nested阻断理由恢复，尚未修复。
静态1207文件/77包及compileall/diff通过。详见post-tool-use-implementation.md。

最新冷恢复反例（2026-09-12）：Post 三个真实故障窗口 × direct/nested × 配置
保留/删除，初次6通过6失败（e77cdc）。direct反馈恢复已验证；nested不重复副作用，
但外层未知调用绕过内层缓存投影，保存的Post反馈丢失，尚未修复。六个nested
场景明确xfail；与原Post组合运行18通过6预期失败（81188e），不是全绿。
新增测试ruff/format及git diff --check通过，参考Codex commit未变且工作树干净。
详见post-tool-use-implementation.md；完整A–E保持开放，CLI暂停。

最新Post实施：可信同步command已接真实主循环，原工具事实先落盘，Post block/
反馈/context与原结果分离，事件顺序保留。12实际Runtime组合及保存结果恢复组件
通过，相关联合75、Post/Stop33通过；详见post-tool-use-implementation.md。
这不是完整Post验收：全Runtime冷恢复、提交到batch的故障窗口、async/MCP型Hook
及各工具交叉矩阵仍开放。CLI继续暂停，不据此关闭全部A–E。

最新Post前置实施：复用已有terminal_info把原命令/调用ID传到观察结果，终态
生成并保存Bash Post载荷，运行中不生成。纠正前轮“会话缺身份”的过宽审计，
实际缺口在观察/结果传递。真实相关70通过（a61d11），存储/资格专项14通过；
详见post-tool-use-implementation.md。Post脚本仍未接入，CLI暂停。

最新Post前置实施：executor捕获Pre改写后、handler调用前的不可变输入快照，
随工具事实落入账本，不覆盖原历史、不进入模型窗口；旧结果字段缺省仍未知。
storage/executor/Pre/MCP/参数恢复联合265通过（a9c942），静态通过。详见
post-tool-use-implementation.md；Post执行/反馈阶段仍未接入，CLI继续暂停。

最新Post实施前审计：post-tool-use-implementation.md逐项记录执行副本丢失、
不可覆盖的原工具ledger、反馈投影分离、shell原调用来源缺失及所需故障矩阵。
确认原生shell非零退出不直接排除Post，yield由载荷资格排除。现有shell输出/
参数恢复/ledger身份24通过（8e636d），仅为后续改动基线，Post仍未接入，不能
计为功能完成。本批未改生产代码，CLI暂停，完整A–E范围不变。

最新B/E修复：MCP Hook区分空白{}、有效JSON Value及非法原文本，不再丢成None；
不放宽实际RPC对象约束，改写/不改写及原始历史均有验证。24反例先红后绿，最终
MCP组合/通用Pre/已有非法响应联合142通过（c69118），静态通过。详见
tool-hooks-review.md；完整Post及其他核心差异仍开放，CLI继续暂停。

最新B修复：实际MCPTool使用已绑定身份构造mcp__服务__工具的本地Hook名，
不改普通模型别名及RPC路由；远程exec_command不套Bash映射。八反例先红后绿，
MCP Pre/通用Pre/既有MCP审批/catalog wire联合188通过（46684c），静态通过。
详见tool-hooks-review.md；MCP raw输入投影、Post及其他核心缺口仍开放，CLI暂停。

最新B/E修复：shell typed非法参数不再启动Pre脚本；保留workdir后置、Option
null前置准入的边界。补测还发现输出预算上溢仍进入process入口，已补exec_command
schema的U64_MAX上限。10+2反例先红后绿，最终相关109通过（da366d），静态通过。
详见tool-hooks-review.md；完整可空/未知字段协议、MCP/Post等仍开放，CLI暂停。

最新B/E修复：实际write_stdin不再重复执行已启动命令的Pre Hook；真实终端
跨Turn direct/nested × 轮询/输入4反例先红后绿，与shell/原stdin授权联合42
通过（ecf068）。同名自定义工具仍执行Hook的2对照通过，静态通过。原命令终态
Bash Post仍未实现，详见tool-hooks-review.md；CLI继续暂停。

最新B/E修复：内置exec_command以Bash/{command}接本地Pre Hook，改写仅替换cmd，
保留原工作目录/login/tty等选项并再次经过正常执行审批。8反例先红，修正一处
shell测试profile夹具后，shell/patch/Pre/执行审批联合123通过（087af8），静态
通过。详见tool-hooks-review.md；write_stdin及完整非法参数门控仍开放，CLI暂停。

最新B/E修复：内置apply_patch的普通patch字段与本地Hook command字段双向
转换，支持Write/Edit别名；非法改写返回工具错误，有效改写仍走原native审批。
8真实direct/nested场景先红后绿；相关联合113通过、3旧helper缺失跳过、14 CLI
未选，最终含同名自定义工具对照58通过（ce4665）。详见tool-hooks-review.md；
没有恢复custom/namespace模型协议，shell/MCP及完整Hook仍未完成。

最新B修复：普通spawn_agent支持Agent匹配别名，payload仍保持canonical名，
同时匹配原名和别名不重复执行。direct/nested两反例先红后绿，Pre/冷恢复/损坏
反馈契约联合153通过（cc10cf），静态通过。详见tool-hooks-review.md；这是
Hook契约验证，不冒充实际子Agent创建功能，其他工具载荷映射仍开放。

最新B/E修复：Code Mode嵌套调用保留宿主上下文，修复内部worker误执行用户
Pre Hook及子Agent身份丢失；typed子Agent载荷补齐agent_id/agent_type。
Pre/冷恢复/Code Mode/cell cleanup联合396通过（f12f9d），真实handler检查
非根标记及审批回调保留。详见tool-hooks-review.md；CLI仍暂停，完整Hook
契约与剩余A–E故障矩阵未关闭。

最新E/C修复：摘要所有者此前仍重试402，双普通HTTP适配器×自动/手动压缩4项
先红后绿；现在一次请求即失败、不安装摘要，冷重开不再发请求。相关34项通过
（34f04e）。按用户明确要求覆盖原生通用摘要重试默认，详见payment-error-review.md。

最新验证：真实Pre反馈/工具进入自动及手动普通摘要请求，失败不安装窗口，成功
后冷读取不重新注入已压缩反馈，原始归档保留。direct/nested等12专项与压缩回归
联合43通过（4edf63）；本批未改生产代码。具体证据及尚未覆盖的安装故障窗口见
tool-hooks-review.md，不代表整个C/Hook验收完成。

最新：双Pre Hook在claim/提交逆序与部分未知时的冷Runtime恢复新增32项通过；
修复v1缺少输入归属字段、重复顺序编号未拒绝的问题（2项先红后绿），损坏记录
不部分归档反馈。契约+完整恢复矩阵105项通过（60a86b），细节及未完成范围见
tool-hooks-review.md；CLI对齐仍暂停。

最新修复：新格式已提交Pre结果的纯反馈可在冷Runtime补回，不重跑command或
handler，使用原预算/顺序/输入归属，不依赖当前Hook配置；旧格式信息不足明确
警告、不猜测。direct/nested等64项恢复矩阵通过（d6410d），相关恢复链156项
通过（debd54）。多Hook部分完成、恢复自身中断及压缩等仍待验证；详见
tool-hooks-review.md。以下未补回描述保留为修复前证据，不代表当前新格式行为。

最新：Pre完成事件已移到批量反馈归档之前，10个时序场景先红后绿；真实冷Runtime
四故障窗口验证不重放command/handler并保留已归档反馈，联合103项通过（773810）。
新确认未关闭缺口：Hook结果已提交但反馈未归档时，冷恢复尚不补回纯反馈；
不能把保守unknown工具结果等同完整反馈恢复。详见tool-hooks-review.md。

最新核心实施：同步Pre additionalContext已归档为developer上下文并进入下次真实
请求；支持配置预算、超限私有文件/预览、写失败降级，冷仓库去重并保留准入输入
归属。Pre/Stop身份及恢复189通过，新增冷读取/取消与Pre最终39通过（3c8385）。
详细证据与仍开放的压缩/冷Runtime/并发/事件时序见tool-hooks-review.md；不关闭
完整Hook契约，不恢复CLI对齐。

最新核心补充：前置Hook的systemMessage已作为有界诊断输出，非模型system指令；
类型无效时不采纳参数改写。新增6项先红后绿，Pre/Stop/tools联合956项通过
（02db52）；详见tool-hooks-review.md。additionalContext及完整Hook契约仍开放。

最新核心补充：PreToolUse 本地转录路径已接入普通/Code Mode 嵌套调用，真实 Hook
进程可读取当前用户历史；OSError 降级仍执行 Hook。修复前2项失败，修复后联合
Pre/Stop/存储与记忆转录51项通过（c0f150）。详见 tool-hooks-review.md；不代表
additionalContext、PostToolUse 或完整冷恢复已完成。CLI 对齐仍暂停。

## 当前范围覆盖：CLI 对齐暂停

按用户最新要求，活跃实现与本阶段验收仅为 A–E 及普通协议/服务隔离边界。
F1–F6 下方记录均保留为历史证据或“用户要求暂停”，不继续推进，也不作为
本阶段完成门槛；暂停不是已完成。以 objective.md 和 goal 引用目标文件为准。
已有 CLI 改动不自动回滚，已知未通过/未验证项须保留记录。核心审批授权、取消、
错误传播和资源所有权仍属 Harness 范围，必要适配层修复不等于恢复 CLI 对齐。

最近被用户打断的浅色显示工作尚未验收：新增两宽度的实际补丁详情 PTY
因未捕获预期 truecolor 序列超时（6336ef：2失败、4通过）；已发现 PTK 色深
配置与夹具只设置 COLORTERM 的假设不一致，但尚未修正/重验，不能记作通过。
Pygments 浅色主题也不等于 Codex Catppuccin 映射。保留现状，不继续该分支。

用户优先提出的三项故障（402重试、重试原因隐藏、正常输入取消误报）已修复，
详见下方最新证据与 payment-error-review.md；外部账户余额未改变。

## 当前快照刷新（优先于下方历史运行时间）

最新工具Pre实施：可信同步command已接到普通/嵌套执行入口，支持阻断、执行副本
改写、schema复验、并发完成序与独立Hook账本。12实际Runtime组合及完整工具单位
联合927通过；Stop等前一组166通过，静态通过。见tool-hooks-review.md最新节。
仍缺context/spill、完整载荷映射、async/MCP/Post和恢复矩阵，不将基础链当完整对齐。

最新工具Hook实施前审计：tool-hooks-review.md记录前后阶段契约、同步并发完成序、
allow非审批授权、Post成功门控与原始副作用/模型反馈分离、wait豁免及恢复要求。
可复用Stop/身份/来源/matcher/batch/冷恢复111通过；未据此宣称Pre/Post已实现。
下一批接可信同步command Pre的真实普通/嵌套工具链，后续Post/MCP/async范围保留。

最新B10总项收敛：完整Code Mode相关目录256通过、无跳过，生命周期要求逐项映射
见code-mode-acceptance.md。确认新的具体交叉缺口：PreToolUse/PostToolUse目前仅有
元数据接收，无普通/嵌套工具执行链；Stop/SubagentStop不能替代。原生wait明确跳过
自身Hook、嵌套工具仍经过Hook，后续先审计配置与授权后实现，不继续笼统写“待核验”。

最新B10/E7取消优先：spawn收到取消后再抛OSError不再吞掉取消，原错误留作cause；
无取消仍报告failed。两反例先红后绿，六文件71通过，实际Runtime扩展后两文件16
通过，静态通过。见code-mode-spawn-review.md；不据此关闭全部A–E。

最新B10/A6进程交接：修复Cell spawn首次取消后裸await导致第二次取消丢失已创建
进程的问题。真实引擎反例先红后绿，持续shield到取得进程所有权再传播取消并reap。
六文件67通过，静态通过；实际Runtime交接另补验，见code-mode-spawn-review.md。
不由此宣称全部cell生命周期、恢复和A6/E7关闭，CLI对齐仍暂停。

最新D3重置交接：补齐新stage-one owner运行中/已成功时，旧成功、空输出、失败
回调不能改写新job、删除新output或改变重试/水位的12组合；联合真实Runtime
执行期reset、租约及claim取消58通过、无跳过，静态通过。见memory-reset-handoff.md。
这是既有owner fence的补验，不冒充生产修复，不由此关闭全部D/E生命周期。

最新B1来源复核：实际MCP→插件→冷恢复动态工具组合增加运行中失败目录发布，
确认旧快照/handler/owner不变、下一Turn仍可执行。四文件44通过；最初三失败为
未初始化registry的快照identity夹具错误，未冒充生产修复。来源调用链及剩余
构造owner边界见registration-source-review.md；README过时原生协议描述已修正。

最新B6/B8定义身份：修复Python把schema内True/1、False/0判为相等，导致旧
搜索结果授权同名替换定义的问题。discovery与executor共用精确JSON字段比较；
冷Runtime两反例先红后绿，旧名称拒绝后重新搜索才执行，归档不改。工具/协议
等联合1334通过，补充直接执行/对象键序对照36通过，静态通过。
见tool-search-ranking-review.md；不据此关闭B1全部注册链或B10。

最新E3错误原因传递：ModelRetryScheduled的error不再被应用层丢弃，采样与压缩
重试显示有界、控制字符过滤后的Reason，保留原事件及预算。四反例先红，扩展
16通过；真实HTTP Runtime事件→应用输出、应用/压缩HTTP联合137通过，静态通过。
见payment-error-review.md。402、输入取消误报及原因隐藏三项已处理，不代表
全部A–E验收完成；CLI视觉对齐仍暂停。

最新E7/A6取消清理：仅对已识别输入owner的正常EOF/InputInterrupted不再误报
Realtime cleanup failed；其它owner同名异常与真实OSError仍记录，取消继续传播，
等待清理结构保持。真实Runtime两反例先红后绿，输入所有权/应用/关闭联合126
通过、静态通过。见payment-error-review.md；通用重试原因展示仍待处理，不恢复F。

最新E3/A8普通402错误：新请求不可自动重试，原始失败原因直达TurnFailed；
旧402事实不改写，但冷恢复采用当前不可重试策略，零新采样、已有副作用不重放。
双适配器反例先红后绿，相关114通过、实际进程崩溃冷恢复4通过，静态通过。
见payment-error-review.md。外部余额未改变；通用重试原因展示和输入取消清理
误报仍待处理，F的CLI对齐仍暂停，不据此关闭全部E/A。

最新F3/F6颜色采样：真实TerminalUI首次活跃输入应用复用现有reader，100ms
获取前景/背景；缺失、迟到、错误、取消均有回退及listener清理，不重复查询。
CLI单位+浅/深/无/部分回复PTY联合509通过，静态通过。见terminal-theme-review.md；
显示消费者尚未接入，不把采样成功当浅色diff/语法主题已完成。

最新F3/F6输入边界已实施：TerminalUI及独立审批复用现有VT reader过滤终端回包，
六个误取消反例先红后绿，完整CLI单位498通过；分片回包→显式决策→恢复composer
的40/100列真实PTY14通过，静态通过。见terminal-theme-review.md。尚未发送
颜色查询或接入浅色渲染，不以此关闭启动采样/自定义主题/跨平台色深等剩余项。

最新F3/F6实施前复核：浅色适配缺的不仅是色值，还有终端回包输入边界。
真实choose_approval仅收到OSC颜色回包即返回cancel，不能直接发送探测请求。
已追踪原生100ms启动采样、输入回放、缓存、语法主题和diff配色调用链；
相关现有39通过不覆盖此缺口。见terminal-theme-review.md，尚未实施或关闭主题项。

最新B5及服务入口：普通函数搜索、专属响应拒绝、旧归档/配置和旧Apps入口联合
154通过。模型、MCP OAuth、显式宿主远程载体与本地history/notes调用链分别盘点，
见service-boundary-inventory.md；未发现官方账号/配额入口，不把旧本地Git署名
识别字符串当网络调用。B5已核验，宿主任意扩展代码不在内置网络行为保证之内。

最新B6：已发现工具的活动窗口生命周期已核验。补强压缩后立即冷重开，不从
旧搜索归档复活已释放定义，重新搜索后才能调用；第一次未压缩冷恢复仍保留定义。
目录变化/预算/普通wire等联合84通过，静态通过。见tool-search-ranking-review.md；
不把普通调用资格等同审批授权，也不据此关闭B1/B10完整生命周期。

最新D4：修复search/read扫描UUID即增加使用计数的差异，反馈仅来自完成assistant
引用。实际SQLite四个反例先红后绿，检索→细读→有/无引用→冷重开保持正确计数；
记忆读取/协议/HTTP引用等联合240通过、静态通过。见memory-read-feedback-review.md。
D4普通读取链已核验；不反向猜测修正旧计数，不代替D3/D5/D6的独立验收。

最新A2：逐边追踪真实graph与Codex采样循环，主循环路由已核验；终态答案新增
工具→继续→五种完成输出的实际Responses断言，不回填早先中间正文、不拼接多条答案。
预算/继续/新输入/实时工具/恢复/Stop控制联合184通过，静态通过。
见turn-decision-acceptance.md；不把A2核验扩成A4–A8或E7全部完成。

最新B8/B9/E2：修复显式畸形type声明被当作缺省而放行工具的漏洞，保留无type
的合法enum等schema。五个真实Runtime反例先红后绿，非法声明零handler副作用、
错误持久化并回灌；完整工具单位与相关集成994通过，静态通过。
见flat-tool-identity-review.md；不以此声称完整JSON Schema支持或全部B/E关闭。

最新C9/C10已按普通适配器路径核验：成功隔离矩阵扩展到手动/轮前/工具后压缩
三入口，共144例，冷恢复不重复摘要或工具副作用；八文件联合429通过，静态通过。
见provider-isolation-refresh.md。此次仅补验收，不改变生产协议；不把供应商
模拟响应当真实模型摘要质量，也不据此关闭其他排除项清理或全部A–F。

最新A8/C5：修复pending压缩按正文相同误认恢复副本的问题，_same_user_input
增加原始提交身份约束。真实Runtime同文/异文对照先红后绿，摘要、后续请求及
冷重开保留两个提交；相关428+34分别通过、静态通过。见local-retention-review.md。
该生产修复晚于下方两次完整基线；旧全量不冒充包含此改动。

最新完整e2e目录63766正常退出0：220通过、无失败/跳过，398.81秒；包括真实
终端交互及示例。217条Python forkpty多线程弃用警告保留，本次未死锁。
见core-baseline-refresh.md；不与下方单元/集成合并成单次全仓，不自动核销F剩余差异。

最新单元/集成全量77351已正常退出0：14193通过、1环境跳过，2067.68秒；
JUnit落盘报告核对零失败/零错误，静态检查通过。唯一跳过为文件系统拒绝非UTF-8
文件名；不含tests/e2e。详见core-baseline-refresh.md，停止轮询77351。
这是当前完整基线，不代表下方所有A–F差异已关闭；历史29459中断不再是最新结果。

最新C5/C6：轮前/轮中/手动压缩补齐混合reasoning/正文/工具对进入摘要、替换
窗口移除旧项、归档原文保留及同Thread冷重开证据；上下文与保留/输入联合400
通过、静态通过。见local-retention-review.md；不冒充真实模型摘要事实完整性。

最新B8/B9/E2：普通工具type列表原先跳过类型检查，nullable及数值联合可被
错误值绕过；7个真实Runtime反例先红后绿，嵌套/同层约束及错误声明也已覆盖。
工具完整单位与schema/失败/命名联合989通过、静态通过。见flat-tool-identity-review.md；
不以此宣称完整JSON Schema引擎或MCP自定义解析链已全部验收。

最新A6：默认输出事件队列改为无界（0），配置模板同步，显式正数背压保持。
SDK/新配置两个暂停消费者反例先红后绿，1024delta在Turn完成及关闭后有序读回；
相关133通过、静态通过。见event-backpressure-review.md，README说明积压内存成本。

最新A3：混合正文/reasoning/工具提交与恢复18组合已复核，新增实际适配器混合
输出断流（Responses三项已完成、Chat正文/调用仍为delta），不重复持久化/执行。
原六文件84通过，扩展后HTTP8通过、静态通过，见partial-tool-transport-review.md。

最新F3/F6：补丁详情接入深色默认增删整行背景与续行填充，使用真实审批终端
色深，16色/无色不加背景，色深变化刷新缓存；CLI480通过、详情PTY8通过、
静态通过。见approval-details-review.md；浅色探测/自定义scope背景仍开放。

最新F3/E：修复词法器选择阶段初始化异常逃出高亮降级边界的问题，保留补丁及
Markdown原文；两反例先红后绿，CLI单位463通过、静态通过。见approval-details-review.md。
原生明暗背景/色深配色仍未完成，不用此修复核销主题项。

最新B8/B9/E2：修复enum把布尔与0/1视为同值的准入漏洞，包含嵌套数组/对象；
保留1/1.0数值等价及对象键顺序无关。四个真实Runtime反例先红后绿，联合86
通过、静态通过。原数组测试归并到test_tool_schema_contract.py，见flat-tool-identity-review.md。

最新B8/B9/E2：修复普通工具数组未声明items时绕过minItems/maxItems的执行
准入缺口；真实Runtime两反例先红后绿，12组合验证合法调用/错误Observation与
零非法副作用，联合62通过、静态通过。见flat-tool-identity-review.md。

最新C9/C10与排除项：成功摘要/冷恢复矩阵增加官方域名和gpt-5模型名（全程
MockTransport），两普通接口均只请求配置端点。另删除无生产引用的两个旧远程
压缩辅助模块，保留有效归档兼容测试；清理后相关86通过、静态通过。
见provider-isolation-refresh.md，不把无网络调用等同所有排除项均已清理。

最新A4/E6：工具结果媒体准备阶段吞取消的return/error同故障已在真实Runtime
补验；不发完成事件、不继续采样、ledger保持unknown。失败边界37通过、媒体
六文件137通过、静态通过。见tool-cancellation-boundary.md；生产保护沿用既有实现。

最新D2：来源/启动/提取/合并/存储/发布及冷召回按真实调用链收敛；当前两组
123和31通过、无跳过，覆盖真实子Runtime工具纠错与文件验证，不再把已有发布
全链列为缺证。见memory-source-review.md；跨文件原子性和真实模型质量不作承诺。

最新B8/A8：已提交Model Step冷恢复补齐原始数值身份碰撞：舍入相同与下溢
相同的decoded参数仍不得复用旧结果；旧调用completed/unknown两状态均拒绝
执行，错误Observation回灌后正常完成。联合98通过、静态通过。见
flat-tool-identity-review.md；这是持久化故障夹具，不冒充HTTP/杀进程验证。

最新F3/F6：补丁按目标扩展名有界语法高亮、按hunk跨行状态、删除dim与续行样式
已接入，复用Markdown高亮并保留词法失败/超限原文降级。CLI/部分重试/完整审批
PTY联合529通过，静态通过；主题背景与精确配色仍独立开放。见approval-details-review.md。

最新F3/F6：结构化补丁预览去除重复原始文件/hunk头，多hunk按行号栏以⋮分隔，
原始patch与风险参数仍完整保留。两个外观反例先红后绿，详情/真实部分重试/PTY
联合23通过，静态通过，见approval-details-review.md；语法高亮与主题仍未关闭。

最新B9/E2：工具状态与宿主元数据构造错误已补真实Runtime五例，均变成持久错误
Observation，不携带非法状态/完成元数据；联合79通过，补强事件断言后21通过、
静态通过。见tool-result-contract-review.md；已有构造校验未重复实现，生产未改。

最新A4/E1/E6：工具handler捕获取消后返回值或改抛普通异常，会丢失取消的两个
真实Runtime反例已修复。executor退出时检查本次新增未撤销取消，禁止伪完成事件
与后续采样；89项工具/Code Mode错误/任务所有权回归通过，静态通过。见
tool-cancellation-boundary.md；嵌套cell同故障注入及ledger恢复仍需独立核验。

后续已补嵌套cell同故障四组合：工具自身取消可被JS捕获为调用失败，Turn所有者
取消仍传播；同调用重claim返回unknown不重复。相关162项通过，见同文档。这里
只证明实际账本API复用，不将其描述为跨进程冷恢复或全部Code Mode生命周期完成。

最新排除项/B5/C10：ordinary wire、MCP OAuth及旧协议接收/回放十文件405通过；
新增缺地址/缺key的两adapter×provider标签×同thread冷启动零HTTP请求8例，所在
文件24通过，见provider-isolation-refresh.md。源码分支只依api_mode；默认base空，
标签/模型名不触发官方回退。此为离线HTTP与Runtime证据，不是真实模型质量测试。

最新基线29459受控中断：13643通过、19失败、1跳过，未完成全量。源码核对后
修正readiness/实际binding混同及Hook部分恢复竞态夹具，MCP84、Hook60通过。
中断附近至集成测试末尾补验577通过1失败，后者是JSON/TOML并发效果顺序断言；
修正并补公开事件顺序后，Hook五文件143通过，静态通过。详见core-baseline-refresh.md。
不得将下方历史10586结果或这些重叠专项称为当前全量一次通过；原停滞没有栈级
根因证明，当前collection将位置指向已修的pending双Hook/迟到输入夹具附近。

最新 A5/B10/E7：MCP readiness 错占执行屏障的四反例已修复，普通 batch/stream
和Code Mode统一就绪后排锁。完成/启动失败/取消/关闭及冷恢复、fatal等36项通过，
扩大调度/存储/Code Mode联合232通过，见tool-readiness-gate-review.md。恢复目录
重建与按调用预热区分；不因此声称完整A5/B10/E7关闭。

最新 F3/F6：结构化补丁详情已支持按源文件 LF/CRLF 编号及按显示列硬折行，
续行保留正文缩进/颜色/尾部空白。真实 Runtime 的38k补丁40/100列逐字重组通过；
详情/部分写重试/PTY联合34通过，见approval-details-review.md。语法高亮、主题背景
及完整原生逐格视觉对比仍未关闭，不能以此宣称全部 CLI 对齐。

本地转录发布现用持久pending集合，只刷新有未发布记录的线程，失败/冷重开保留
重试，旧库一次性迁移；事务回滚不留伪pending。反例及201项回归见
hook-transcript-storage-review.md。活跃长线程的完整前缀校验成本仍未关闭。

最新E4/E7：无运行中事件循环的同步Runtime.create失败，现等待自建资源回滚；
两项自建模型泄漏反例已修复，构造/CLI/初始化/关闭49通过。成功组装仍为同步，
借入资源不关闭。运行中loop的同步嵌入仍需acreate；见mcp-startup-review.md。

最新A4/C7：本地JSONL转录已接入提交链路和Hook v3，v1/v2恢复保留原身份。
真实Hook读取/父子隔离与存储182通过；异步打开文件跨Turn、压缩保留原文及取消/
fsync故障联合23通过。后续v3路径可用性恢复冲突已有12个反例并修复，见
hook-transcript-storage-review.md。仍有大历史发布成本及更多提交故障组合待验证，
不能宣称完整转录或A–F完成。

最新C4/E6：补齐摘要流/关闭收到取消后正常返回的检查，取消不再被正常返回掩盖。
真实Runtime两反例、704项上下文/压缩回归与静态通过见summary-cancellation-review.md；
不将旧缺陷推断成已观察到历史覆盖，也不把该组测试称为完整A–F验收。

SubagentStop同步链路已实施：类型化来源→独立matcher/授权→真实命令→反馈继续；
原Stop身份保持，冷恢复三窗口与插件迁移24通过，见subagent-stop-review.md。
异步Stop/SubagentStop也已接入会话owner、8并发、延迟诊断、关闭和冷恢复；
214项联合通过，见async-stop-review.md。转录文件、授权UI及其他边界仍未完成。
后续同步命令并发差异已修复：实际命令逆序完成、结果仍按配置顺序；关闭和存储
失败会清理同批在途进程。扩大236项通过，见sync-hook-concurrency-review.md；
该集合与历史结果重叠，不累加为唯一测试数，也不是当前全量验收。
根 Stop 输入身份已纠正：新批次使用持久化 session_id 和有效审批模式；v1批次
保留原请求身份以保护恢复。真实反例、旧版三窗口和126项回归见
hook-input-contract-review.md。可空字段不代表可读转录能力已完成。

全量10586已终态：13862通过、2失败、7跳过（9ed464）。两项CLI冷恢复夹具把
恢复期间无输入误写成EOF取消，独立复现后修正；保留原断言并补取消负断言，相关
34项通过（c4521b/50ce3c），静态1163文件通过。旧compiler六项另补验通过，详见
core-baseline-refresh.md；不把局部修正写成原全量一次全绿。
remaining-implementation-priorities.md记录Hook实施及剩余边界，并依据源码
纠正“记忆worker应执行项目Stop”及“原生冷历史应回放Hook通知”的误推断。

最新 F3/F4：补齐原生编号直接提交选项的交互，九数字反例先红；只绑定存在
的actions，默认高亮/越界数字/粘贴不授权。审批PTY+单位+原桥接104通过，
最终扩充的真实执行桥接28另通过，见approval-scope-ui.md；授权后端与规则策略未放宽。

最新 C4/E：摘要读流 ModelError 后独立 aclose 异常曾覆盖主错误、破坏重试，
新反例先红后修复；现在按原错误重试相同历史，只安装有效结果，次要关闭错误
保留诊断。最终上下文/压缩 702 passed（8f4080），见 summary-cancellation-review.md。

最新 C4/C5/C8/E6：摘要取消被生成器 finally 的关闭异常覆盖、误变 TurnFailed
的四反例已修复；部分摘要/工具对/用户约束保持，唯一取消终态且无摘要 marker。
无取消的关闭异常仍失败，不安装摘要。最终上下文/压缩 701 passed（2bb9a8），
详见 summary-cancellation-review.md，不用此前 696 全绿掩盖这个遗漏窗口。

最新 D6/E7：父 Runtime 关闭等待者取消后，超时记忆子任务仍被强引用持有，
依赖不提前关闭、最终不重复关闭、不迟到发布；shutdown 专项 11 passed
（f1358b）。记忆单位/集成较大范围 800 passed（eae122，新增参数前收集）。
memory-close-recheck.md 新增五类所有权证据表，区分合作式关闭与强杀/跨平台限制。

最新 F1/F6：修复 Hook 完成信息整块 dim 的视觉差异，状态圆点按完成/失败
区分绿/红，正文默认前景、来源前缀 dim，真实 HookCompleted 接入专用 renderer。
ANSI 快照、回放、NO_COLOR 及真实终端红色圆点已验；CLI/Hook PTY 联合
456 passed（2eefbe）。hook-cli-lifecycle-audit.md纠正冷历史误判并保留授权UI边界。

最新 B8/E5：普通 Chat/Responses 的平铺别名冲突均在网络提交前拒绝，不依赖
strict 目录开关；新增 Chat 两例，与路由/注册/冷恢复/数值 ledger/精确结果
联合 113 passed（380406）。flat-tool-identity-review.md 区分原生目录冲突与
本地平铺身份防歧义，明确存储单位证据不等于 HTTP 崩溃恢复全链路。

最新 B2/B6/B9：搜索 Observation 提交后冷恢复，工具改为 Hidden/CodeModeOnly
时，旧加载定义不进入普通请求；猜名调用得到错误 Observation，实际 handler 零
执行，归档/输入保持。相关搜索/路由/跨 Turn 联合 112 passed（5c6f69），见
tool-search-ranking-review.md；这是恢复曝光边界补证，不是模型质量或完整 Code Mode 验收。

最新 A7/E5/E7：批次提交前真实 INSERT 失败/取消，两 backend Runtime 正反对照
已补证；存储与冷恢复联合 69 passed（bb94d9）。跨搜索/压缩/记忆/恢复组合
215 passed（88db1a），其中 CLI 旧 fixture 已适配活动 composer，未删恢复断言。
详见 hook-batch-recovery-design.md；不代表完整 A–F 或所有资源故障窗口关闭。

最新 A7/A8/E5：hook-batch-recovery-design.md 方案已落地版本化批次表与 Runtime
恢复预检。配置删除/撤销不再绕过旧执行，已完成复用与未执行授权分离；新增/改
命令边界实证。核心组合 273 + 追加 24、CLI/PTY 446 分别通过，不作为全项目
一次全绿；旧无批次 Hook claim 的安全报错兼容限制及未验边界见文档。

最新 A7/A8/E5 未关闭缺陷：冷恢复删除配置/撤销授权会让旧 Hook ledger 不被读取，
unknown 可误判完成、已完成 Stop 可失去控制结果。四真实探针均违反既有恢复
断言（46339a），本轮仅审计未修复。hook-batch-recovery-design.md 明确完整批次
持久化、当前授权与历史结果复用的边界；上轮 200 passed 不覆盖这些配置变化。

最新 A7/A8/E5：晚到持久输入曾使冷恢复绕过 Stop 执行账本，既可能忽略完成
停止，也可能把 unknown 结果当成功。两反例先红，已加入按 thread/turn/Step
的只读 claim 存在检查，既有 claim 必须校验/复用/拒绝 unknown；相关 54 passed
（fd4560）。stop-pending-input-boundary.md 记录新增仓库契约及配置变化恢复限制。

最新 A4/E：真实 Stop 执行期间到达的新输入曾覆盖 should_stop，导致多采样一次；
已用明确 Allow/Block/Stop 替代 bool，停止循环但仍正常持久化收到的输入，符合
原生 turn.rs 与 tasks/mod.rs::on_task_finished 的不同职责。两顺序先红，相关
78 passed（9739c1），详见 stop-pending-input-boundary.md。A4 仍部分一致，
异步/non-root 等未完成；下方只列 TOML 与旧测试数的 A4 行是早期记录。

近期 F/A/E：普通/compact/resume 活动 composer 已接；恢复首次真实 TurnStarted
前不读输入，非 realtime 新输入只排队。Hook fresh/resume 双入口 24 PTY 与
CLI/Stop checkpoint 联合 458 passed，见 hook-cli-lifecycle-audit.md；未将
Hook 样式、冷历史完成回放、checkpoint 故障+PTY 组合视为完成。

最新单元/集成全量：13574 passed、1 skipped，1954.44 秒，session 72348
退出 0（13c4ff）。唯一 skip 是文件系统拒绝非 UTF-8 文件名；没有用跳过
替代该场景验收。五份真实旧 compiler 拒绝契约已包含，不含 tests/e2e。
详见 core-baseline-refresh.md；本结果不自动关闭下方任何待收敛需求。

A2/A4 最新：默认循环次数限制已改为可选，实际长任务及 Code Mode 嵌套调用
红绿证据见 default-loop-limits.md；显式预算及恢复回归 719 passed、1 环境
skip（439cca），Code Mode/新长任务联合 24 passed（aebc41）。下方“尚未修复”
为实施前记录；完整跨恢复/压缩组合和 A–F 仍待验收。

A2/A4 新确认默认差异：普通原生循环以待处理工作/压缩/停止条件推进，Corki
默认 max_steps=24、max_tool_calls=64 会提前失败，且同时影响 graph recursion
与 Code Mode 嵌套准入。default-loop-limits.md 记录调用链、修复范围与验收；
这是尚未修复的核心差异，不再仅泛称“预算专项待收敛”。

A5/E1/E7 新组合证据：普通 handler 错误/错误结果不取消并行兄弟，exclusive
等待全部前序任务，结果按调用序；streamed 存储故障在终态前收拢兄弟任务。
联合 89 passed（30c848）、静态通过（b36d84），见 parallel-error-barrier-review.md；
不外推全部 readiness、Code Mode 或任意 fatal 错误与原生等价。

A7/A8/E5 两普通接口的 partial call 重试与完成后冷历史组合已补证：Responses
已提交调用先执行/回灌，Chat 未提交片段不执行；单输入/调用/结果，旧前缀保留。
联合 78 passed（6bb4e1），静态通过（6d2ab0），见 partial-tool-transport-review.md；
不等于所有外部副作用 exactly-once，也不等于本用例执行了崩溃重启。

E3/E6 非 2xx 双重失败已修复：普通关闭异常不再将 429 改成连接重试或将取消
改成失败；真实两适配器新 54 例与既有联合 206 passed（fd0c95），静态通过
（f11f38）。范围和未覆盖的物理取消组合见 model-stream-close-review.md。

A/E 新修复：成功 HTTP 响应交接后的关闭异常不再覆盖原始采样断流或取消；
实际工具已执行→断流→关闭失败→按更新历史重试已有红绿证据，单工具副作用。
model-stream-close-review.md 记录边界；非 2xx 另见页首更新，完整资源矩阵仍未关闭。

C1/D4 内置组合分类已修复：显式 PromptPhase 区分记忆 extension 与 skills world
state，host skills 随完整权限片段是否存在选择插入位置。真实请求修复前
18 failed，最终相关 1140 passed（b0017e），静态通过（dde162）；详见
context-snapshot-wire.md。此项不代表 C1/D4 全范围或模型选择质量已验收。

C1 初始宿主 developer 扩展/模式排序已修复：初始和压缩重建稳定前移扩展，
增量及旧历史不重排，工具目录/输入附件/独立片段不误分类。真实请求断言
修复前 9 failed，修复后最终相关 437 passed（09e8eb）；源码映射与限制见
context-snapshot-wire.md，C1 其余来源及完整 A–F 仍未完成。

B/E OAuth + 按需搜索已组合：64 例覆盖 direct/deferred、实际 Responses 与
脚本对照、刷新时机/拒绝/慢刷新及冷恢复；检查先搜索再加载定义、read-call
结果与密钥隔离，冷恢复不重复刷新/调用。最终相关联合 287 通过（883eed），
静态通过（501f44）。provider-isolation-refresh.md 明确当前已不止分开验证。

B/C/E provider 隔离刷新：搜索三步真实适配器扩大至 48 例，压缩拒绝边界扩大
至 144 例，交叉 provider/域名/模型且只发普通请求。前者与 OAuth/旧协议回放
联合 249 通过，后者另 144 通过；静态通过。provider-isolation-refresh.md
列明运行差异与覆盖限制，不将 OAuth 直调测试冒充 OAuth + deferred 同一链路。

F/A/E CLI 回滚期间单次物理 Ctrl+C 补证：40/100 列、原错误/取消四例，
SIGINT 已进入父 task 后仍等插件关闭，再以 130 退出。CLI 联合 403 通过、
8 条 forkpty 警告（e0ed63），静态通过（d61d43）。见 mcp-startup-review.md；
不覆盖任意构造点/重复 SIGINT，同步嵌入资源等待和完整 A–F 仍开放。

A/E __init__ 分配补证：源码确认 writer/notes/Code Mode/后台控制器均惰性启动，
真实末端故障四组合验证无任务/锁/notes/checkpoint 遗留，volatile 连接已关闭。
联合 86 通过（befeca），静态通过（612d42）。mcp-startup-review.md 记录证据，
不再无依据假定当前 cls 内部另有活跃资源遗漏；同步嵌入等待及启动物理取消仍开放。

A/B/E 宿主 registry 构造失败残留已修复：真实 create 的同步事务在异步清理前
恢复旧工具/owner/策略/封存/快照，避免覆盖清理挂起期间的宿主新注册。两例
先红后绿，核心组合 380 项通过（70d29b），静态通过（92c6b1）。详见
mcp-startup-review.md；cls 内部分配、同步资源释放和启动物理取消仍开放。

A/E/F CLI 构造等待已接入：同一 loop 构造/运行，构造失败先清理再返回错误或
130，完整 Runtime 接管 Application 构造失败；运行期 ValueError 不误分类。
真实入口三例先红后绿，联合 437 通过及最终五场景专项通过（ddbc0c、81851b），
静态通过。见 mcp-startup-review.md；新启动窗口物理取消、同步嵌入入口与
后期 registry/cls 资源仍待核验，完整 A–F 未完成。

A/D/E 可等待构造入口 acreate 已复用真实 create，并接入 memory agent。
失败逆序关闭自建资源、保留借入模型、重复取消等待真实清理、钩子取消不吞。
插件/Runtime/记忆联合 286 项通过（e9402e），静态通过（3bc121）。
mcp-startup-review.md 明确剩余同步 CLI/嵌入入口、宿主注册回滚及 cls 内部分配，
此项为部分一致，不以新增异步入口宣称全局构造所有权或 A–F 完成。

A/D/E 构造前校验修复：自定义会话存储缺少必要记忆存储时，先拒绝再执行插件。
真实注册副作用测试先红后绿，初始化/关闭/插件/记忆 257 项及读取/数据库所有权
48 项分别通过（c2c8f9、4735cd），静态通过。mcp-startup-review.md 记录剩余
同步构造资源接管及 CLI/记忆子 Runtime 的调用边界，不以此关闭全部构造故障。

E 初始化资源所有权修复：模块 exec 前登记清理所有者，覆盖普通加载失败、
不可调用 entrypoint 及重载时模块取消。三例先红后绿，插件/注册/关闭 250 项
通过，搜索/压缩/记忆/恢复组合另 95 项通过（e5274f、aee980），静态通过。
见 mcp-startup-review.md；同步 Runtime.create 控制流失败的整体接管仍开放，
此修复不等于全部扩展初始化或 A–F 完成。

F1/F6 定时边界补证：实际 animated_events 的虚拟时钟测试覆盖重复事件不延期、
阻塞后无旧 tick 突发及替换/停止重启废弃旧 deadline。CLI/Runtime 补跑 386 项
通过（f4baf0），扩大批次的 62 个 PTY 场景通过；该批次因收集到修正前新测试
入口有三项失败，不能称联合全绿。详见 stream-animation-design.md 的分批证据。
静态通过；历史视图竞争、复杂 resize、独立 Plan 视觉控制器等仍开放，A–F 未完成。

F1/F6 动画主路径已接入真实 Application/TerminalUI：独立事件等待和 tick、
单行/追赶、终态排队列与关闭，402 项联合及最后 burst 后 17 项专项通过。
stream-animation-design.md 标记部分一致，保留精确时序/历史/复杂缩放等验证；
不再以“尚无定时动画”描述当前生产状态，亦未关闭完整 F/A–F。

F 行队列结构已接实际 StreamMarkdown.write（enqueue/drain，仍立即排空），
379 项 CLI/集成及 20 项真实流式 PTY 通过（7dd269、ccc138），静态通过。
stream-animation-design.md 记录状态和剩余定时/策略/终态所有权，不宣称动画完成。

F1/F6 动画队列已完成实现前调用链审计：stream-animation-design.md 记录原生
delta/queue/timer/drain/finalize 全链与 Corki 实际缺失，明确迟滞阈值、双行计数、
resize/replay 约束及可执行验收条件。未以文档或既有输出锁定声明动画已实现。

E/F 双重终端故障已修复：正文写入错误不再被退出重绘错误覆盖，取消和真实
Runtime 清理保持。376 项 CLI/集成及 20 项流式 PTY 通过（f9b7a6、601dd2），
静态通过（713fe8）。cli-streaming.md 记录先红后绿及未完成动画调度边界。

E/F 流式提交故障：单点更新/写/刷新/重绘失败与取消，真实 Runtime 关闭及
终态断言通过；CLI 联合 374 通过（a285ce），静态通过（94aa33）。
cli-streaming.md 区分输出提交所有权与尚未对齐的原生动画队列，不以此关闭 F1。

A/C/F 活跃模式：Default/Plan 的真实请求模式上下文及运行键盘/停止恢复矩阵
32 通过（871996），静态通过（305d07）。cli-mode-entry-review.md 给出覆盖；
流式旧文已注明现有 stream_commit 接入，待核验而非重复实现。

F 设置提交中的物理 Ctrl+C 已补证：成功/失败各在 40/100 列命中真实挂起窗口，
等待提交后恢复实际模式和原正文且零采样。CLI 联合 373 通过（54bbd1），静态
通过（1975b8）；具体覆盖见 cli-mode-entry-review.md，Plan 活跃/其余焦点仍开放。

F 运行中键盘：Default 活跃模型下 Shift+Tab + Tab 排队、Ctrl+C /stop、
恢复后手动提交的真实 PTY 16 项通过（3f90ec，40/100 列及 steer 对照）。
静态通过（82588c）；模式提交时物理取消及其他模式/焦点组合仍待核验，见
cli-mode-entry-review.md。没有把这组测试称为完整实时交互验收。

F 模式切换提示已接入并按焦点/运行状态及宽度降级，30 项直接渲染检查与
40/100 列真实提示 PTY 通过；CLI 组合 369 通过（e6b00a），增强 PTY 8 通过
（46f977）。见 cli-mode-entry-review.md；未关闭整个 footer 或交互矩阵。

最新并发关闭修复后，插件/注册/Runtime 关闭及下列六文件核心组合 342 通过、
0 跳过（1707d6），静态通过（6fc3d5）；mcp-startup-review.md 记录候选 abort
等待者复用及先红后绿证据。同步刷新 E4/F2，删除过时的“Plan 状态缺失”判断，
没有把尚待验证的交互、同步构造资源接管或全部 A–F 判为完成。

E 取消清理修复：插件候选撤销等待独立关闭任务，重复取消不会跳过挂起的清理；
真实 Runtime 测试先红后绿，相关 245 + 12 项通过（2d65c6、029562），静态
通过（eb7e11）。范围及剩余初始化缺口见 mcp-startup-review.md。

E 新修复：实际 Runtime 插件重载的 register 取消不再丢失新模块所有权；
失败批次由 manager 最终清理，旧注册不变、取消不吞。先红后绿及 244 项联合
通过（17b62f）见 mcp-startup-review.md；此证据不覆盖同步初始构造失败。

B/E 扩展注册补证：失败代码替换不发布半成品，旧快照 handler 与健康兄弟保留，
修复后新版本可调用且模块最终关闭。三文件 221 通过（ffc247，0 跳过），静态
通过；mcp-startup-review.md 明确 Python 回调与原生包级加载的区别及关闭边界。

A/B/E MCP 启动补证：mcp-startup-review.md 记录原生发布后 required 校验与
Corki 入场/回滚链，增强失败工具不注册、健康连接重试复用的真实 Runtime
断言。四文件 45 通过、0 跳过（0a0b9e）；未把原生 Session 构造失败和 Corki
同对象重试称为相同生命周期，动态扩展及完整资源关闭链仍需核验。

最新记忆广回归：所有 unit/memory 与 test_memory*.py 集成 788 通过（5ede60），
另七个 Thread/临时会话/MCP/引用/关闭入口 120 通过（766d53），两组均 0 跳过、
已终态。命令、环境、覆盖解释及未证明事项见 memory-regression-current.md。
这些结果刷新 D/E 近期专项兼容性，不把文件名匹配或测试全绿替代 D1–D7 逐项审计。

记忆发布矩阵扩展：共享 worker 的冷 Runtime 恢复、普通 JSON 兼容结果的摘要
及 skill 写入失败/冷修复均已新增直接测试，三文件联合 39 通过（ada0be）；
memory-publication-review.md 区分原生共享目录语义与兼容分支，明确部分文件
可能生效但任务失败，不承诺文件与 DB 原子事务。原先泛称未核验的这些具体窗口
由新证据覆盖，其余生命周期/污染与真实模型质量要求并未关闭。

记忆发布后续：database_success 故障的下一主 Turn 重试已补实证，no-diff 分支
补齐 DB success/水位且不新增模型调用，36 项组合通过（3bc25a）。仅关闭该
同 Runtime 重试场景的证据缺口；冷恢复与暂存分支仍开放，见 memory-publication-review.md。

最新 D2/D3/D6/E4：memory-publication-review.md 追踪原生共享 worker 完成后的
shutdown/validation/owner/baseline/DB success 顺序；新增 baseline 失败与 baseline
成功后 DB success 失败两窗口，真实子 Runtime 文件编辑后注入。34 项联合通过
（0dbc15）；明确文件/基线非数据库事务一部分，不凭副作用存在标成功。重试恢复与
暂存发布分支仍待核验，下文“尚未证明”由此局部证据补充而非整项关闭。

最近模式/输入改动后，重新运行下方列出的六个跨模块 integration 文件，95 passed、
0 skipped（c190b9，18.44 秒，`-ra`），进程已退出。该组合仍覆盖搜索定义加载与
回灌、跨 Step/Turn/冷恢复、普通摘要请求、记忆生成/召回、错误与并发恢复；不是
任意故障 exactly-once、真实模型质量或 A–F 全量完成的证明。

A/F 新证据入口：cli-mode-entry-review.md，包含 /plan 的输入原文、设置提交取消、
冷 CLI 从 Default 启动但 pending Turn 保留 Plan、Shift+Tab 往返及失败草稿保留。
原先这些缺口已有实现和专项证据，不能继续用旧流水账描述成“完全缺失”。CLI 的
物理 Ctrl+C/运行中真实键盘组合与提示仍开放，不优先于尚未核验的跨模块一致性。

下一优先项为 D2/D3/D6/E4 记忆发布失败一致性：当前 pipeline._run_consolidation
先 join worker/heartbeat，再在 complete_consolidation 的 publish 回调内处理输出。
SharedAgentArtifacts 分支只 reset git baseline（worker 已写共享目录），staged
分支逐文件发布后验证并 reset。不能从数据库 owner fence 推断文件组原子回滚。
需分别核对 Codex phase2 的共享目录与失败基线规则、Corki 两条分支，以及已有
test_memory_agent_runtime 等故障测试是否真正覆盖写入中断/基线失败。尚未证明
存在新的实现缺陷；也不能据旧“部分一致”措辞直接新增不符合原生行为的回滚。

权威范围为 goal 附件 pasted-text-1.txt；本表不替换、删减该目标。旧 audit.md 是历史
流水账，存在已关闭差异和过期进程记录；引用旧记录必须核对当前实现及后续覆盖项。
本轮重新运行的跨模块组95通过（0ee96c，19.46秒）。没有依据将此比例换算成整体完成率。

状态约定：**局部实证**表示当前测试直接证明所列场景，不表示覆盖该需求所有分支；
**待收敛**表示需要核对已有专项、真实调用链及反例后才能关闭，不自动判定功能缺失。
下列路径是后续核验入口，只有明确标为本轮实证的测试已在本轮运行。

## 当前直接实证

后续认证/错误组刷新：双服务模型HTTP+MCP刷新/冷恢复及旧专属协议拒绝167通过
（ad366c，41.13秒）；包括openai/independent的原生search各事件边界、专用compact
旧SDK控制联网前拒绝、旧历史与Lite/custom拒绝。通用OAuth与HTTP/采样重试、连接
恢复、底层流关闭可靠性联合151通过（93d188，23.49秒）。这两次运行有重叠，不能
相加称318个不同场景，也不代表全配置隔离已验收。新增双服务测试的完整范围见
mcp-oauth-lifecycle.md，暂只联合验证Responses+File。

E3/E6新直接证据：HTTP层只在交出SSE reader前重试；Corki model_http_stream在
yield后异常走上层采样分类。原生core/session/turn.rs采样重试重新clone_history并
检查is_retryable，而非盲重发旧请求。当前两种适配器测试确认终态到达即关闭、截流
不变成成功、standalone可见delta之后不在适配器内重采样；Runtime重试层和连接冷
恢复另有集成测试。不得把standalone行为概括成“Runtime任何部分输出后都不重试”。

运行六个 integration 文件：test_deferred_tool_search、test_model_search_compaction、
test_compaction_wire_contract、test_memory_pipeline_runtime、test_search_error_recovery、
test_recovery_and_concurrency（均为 .py）。逐项阅读测试断言，覆盖：

- 搜索前不曝光 deferred schema；普通 tool_search 后加载定义、执行并回灌；跨 Turn
  保留发现结果；ledger/历史/checkpoint 三故障窗口及描述/schema 更新后的冷绑定。
- 真实 HTTP MockTransport 中只发往 fixture 配置地址的普通 Responses 请求；冷模型
  降窗先摘要旧输入，再准入新输入；旧 native/Lite 能力配置不能启用这些传输扩展。
- 无效或专用压缩响应不替换 canonical 历史；请求没有 compaction_trigger，摘要请求
  不携带工具。此为所测地址/路径证明，不替代整个项目的网络隔离验收。
- 失败搜索返回错误后纠正搜索，失败不能加载候选工具。
- 记忆后台提取/合并 → summary 注入 → search/read → citation/usage 反馈；后台心跳
  失败不终结主 Turn；显式 note 和冷合并、路径替换不越界写入。
- 混合正文/reasoning/并行工具、持久提交取消与冷恢复、未加载旧页恢复去重。

这些测试使用脚本模型和模拟网络，不证明真实模型搜索、摘要或记忆质量。

## A 主循环与 Runtime

入口：core/runtime.py、turn_run.py、graph.py、model_stream.py、storage/sqlite.py。

| ID | 目标需求 | 当前证据与待收敛部分 |
|---|---|---|
| A1 | Thread/Turn/Step 生命周期 | 已核验（本地Runtime核心生命周期）：Thread身份/单写者、准入/首次落盘、三种终态、Step冻结与身份拒绝、RUNNING冷恢复联合131通过，18旧CLI混合场景未选入。见runtime-lifecycle-acceptance.md；资源构造/关闭与Hook恢复交错仍分别归E4/E7/A7/C8，不承诺跨外部系统原子性 |
| A2 | prepare/model/evaluate/tools 循环 | 已核验：真实graph的工具/继续/完成/失败与重试边，最终输入/Stop复核；默认与显式预算、实际Responses连续采样及恢复联合184通过。见turn-decision-acceptance.md；完整调度/副作用/资源故障分别归A5–A8/E7 |
| A3 | 同时正文/reasoning/工具 | 已核验：混合提交/工具完成写入/冷恢复/CLI18组合，实际适配器混合输出断流与重试有证据；partial-tool-transport-review.md。不把Chat未提交delta当完成item，不因正文提前结束工具Step |
| A4 | 继续/完成/失败/取消/预算耗尽 | 部分一致：turn-decision-acceptance.md核销预算等证据；Stop/SubagentStop同步/异步及混合执行、手动压缩不消费异步Stop结果已有实证，见subagent-stop-review.md与async-stop-review.md。宿主/用户/项目/插件来源授权与刷新见managed-hook-gap.md；转录副本持久重试及原终态前告警已接入，见partial-output-acceptance.md。跨Hook恢复交错按A7/C8收敛；授权UI风格属暂停F项，不作为A4缺口 终态确认/有限补写/新 Turn 非阻断及原取消保留已实现，见 partial-output-acceptance.md；取消终态待写与 compact Hook 跨 Turn 误接管已修复，514 扩大通过（73565d），见 session-start-gap.md。剩余不得继续笼统列作“终态映射未知”。 |
| A5 | 并行/独占/排序/运行中新输入 | 已核验（普通Harness调度）：就绪后FIFO准入、普通错误/致命错误屏障、Code Mode Step归属、结果有序增量发布、工具完成后接入新输入及压缩续采样边界逐项见ordered-tool-publication.md。196/686联合、真实进程退出后前缀不变/未知不重放50联合有证据；修订后非CLI全量15504通过/1文件系统限制跳过。全局副作用原子性、其它Hook提交交错及构造/资源故障仍按A7/C8/E4/E7独立验收 |
| A6 | 流事件/backpressure/关闭顺序 | 默认输出流控差异已修复：默认无界，正数为宿主显式背压；暂停消费1024delta仍完成并可在关闭后读回。容量1/4/8满队列关闭亦实证；见event-backpressure-review.md，其他资源所有权专项仍独立收敛 |
| A7 | checkpoint/历史/副作用恢复关系 | 搜索/工具提交恢复已实证；Post原结果与Hook batch同事务、checkpoint投影清单及恢复再中断有专项证据，见post-tool-use-implementation.md与tool-result-commit-cancellation.md。外部副作用到本地提交前仍可能未知，不宣称全局原子 |
| A8 | 不重复输入/采样/执行 | partial-tool-transport-review.md 补两普通接口断流提交边界及完成后冷历史组合；既有冷恢复、跨 Turn 和 CLI；不是任意外部副作用 exactly-once 承诺 |

## B Tool Use

入口：tools/discovery.py、search.py、search_cache.py、registry.py、executor.py、
core/step_tools.py；后者冷恢复重绑当前 handler，持久 spec 校验与 ledger 结果仍保留。

| ID | 目标需求 | 当前证据与待收敛部分 |
|---|---|---|
| B1 | 内置/MCP/动态/扩展注册 | 已核验：显式来源优先级、保留名、撤销回退、失败候选原子性、实际Step快照与插件重载。当前分组48及213通过，见registration-source-review.md；构造资源清理归E4，MCP/Code Mode执行任务关闭归B9/B10/E7，不承诺撤销第三方任意副作用 |
| B2 | Direct/Deferred/ModelOnly/CodeModeOnly/Hidden | 一致（用户普通路径）：六种曝光、双入口与MCP mask投影；当前联合502通过，含实际隐藏/恢复调用。见tool-exposure-review.md；曝光不等于授权，B10生命周期独立 |
| B3 | search 启用/元数据/索引/排序/Top-K/缓存失效 | 已核验：本地BM25、默认8、元数据与generation缓存；当前联合502通过，见tool-search-ranking-review.md。同分使用注册序，原生无规定同分次序，不承诺真实模型质量 |
| B4 | 搜索结果变成后续可调用工具 | 一致（用户普通函数路径）：成功搜索后加载、实际调用与Observation回灌、跨Turn和冷恢复；当前联合502通过，见tool-search-ranking-review.md |
| B5 | 普通 function-calling，排除原生 search output | 已核验：两普通接口的搜索→加载→调用，专属added/done/completed/delta拒绝及旧归档不恢复原生协议；当前联合154通过。见service-boundary-inventory.md与provider-isolation-refresh.md |
| B6 | 已发现工具生命周期/上下文/恢复 | 已核验（普通函数路径）：有效定义跨Step/Turn/冷恢复保留；过期定义先投影再估算，压缩释放后冷启动仍须重新搜索。当前联合84通过，见tool-search-ranking-review.md；OAuth同链恢复另见provider-isolation-refresh.md |
| B7 | 模型选择与候选筛选职责 | 已核验：搜索不执行候选，只有后续模型ToolCall触发执行；Top-K测试含候选禁止执行哨兵，当前联合502通过。脚本验证不证明真实模型选择质量 |
| B8 | 平铺命名/schema/身份/结果规范化 | 已核验（普通工具路径）：平铺别名与碰撞、跨来源注册、参数子集/独立parser、精确调用身份及冷恢复、普通/嵌套/MCP结果通道；扩大1287通过，见tool-result-contract-review.md当前归并与flat-tool-identity-review.md。不承诺任意schema关键字或真实模型质量 |
| B9 | 未曝光/未知/畸形/失效/超时/超限 | 已核验（普通工具路径）：曝光/定义快照准入、非法参数与结果、嵌套兄弟隔离、HTTP/stdio断连与超时、原始上限和模型投影分离；扩大1287通过，见tool-result-contract-review.md及tool-failure-acceptance.md。Hook交错、提交副作用与资源关闭分别按B10/A7/C8/E5/E7验收，不承诺外部操作exactly-once |
| B10 | Code Mode 与发现路径 | 生命周期/入场/调度/媒体见code-mode-acceptance.md；同步及异步Pre/Post已接真实Runtime，父调用归属、yield/wait及冷恢复见各专项。MCP型同步Hook已接入，信任/就绪/HTTP故障及Pre/Post完成/未知结果冷恢复见mcp-tool-hook-gap.md；不能再将async/MCP基础执行列为缺失。其它Hook事件/来源及剩余跨模块恢复边界仍开放 |

## C Context

新确认的模型指令缺口见model-instructions-gap.md：现有model_transition只负责
压缩决策，不包含本地模型基础指令/会话来源保留/model_switch追加链路。
现已接通本地catalog指令、SQLite会话基础来源、普通请求及切换片段，原红例转绿、
关联483通过；后续fork继承与初始化故障/取消已补验，连续手动压缩后模型前态
四红例修复、扩大448通过；自动/工具后压缩后续补验。显式宿主覆盖及Custom来源、
恢复不改写旧基础、fork继承/覆盖已接通，扩大1109通过/1文件系统跳过。文件入口
后续六红例修复，扩大687通过/1文件系统跳过；旧无来源历史与待恢复Turn覆盖交错
仍开放，详见专项。
不以协作模式刷新或旧全量通过替代，亦不引入官方目录服务。

协作模式新增修复：mode.default/plan 共享比较 section，admitted 模型身份进入快照，
仅发布当前模式标签（清空为空标签），保留旧 durable key；跨模式/模型与冷开去重，
完整context及四个关联集成文件416通过（e3ec37）。见context-snapshot-wire.md；
后续已补旧Message模式片段Unknown识别，固定宿主选择的两次冷开协调/清空及
来源边界联合429通过（013989）；其余typed section继续审计，C1/C2未整体关闭。

最新C1/C2实证：两普通HTTP适配器的宿主片段角色迁移/撤销/冷恢复新增集成通过，
同高优先级role先撤销再降级，canonical不覆盖、不重复。连同全局/项目指令快照、
managed读取限制、provider生命周期、输入附着与world-state扩大55通过0跳过
（bf4e9f）。参见context-snapshot-wire.md；未把固定user-role的原生AGENTS称作动态
角色功能，也不把开发者片段映射system的Chat兼容实现冒称Responses原生角色。

入口：context/builder.py、instruction_manager.py、tokens.py、window.py、local_retention.py、
core/compaction.py。context/remote_compaction.py 当前是旧记录 retention helper，无模型
transport；文件名不是官方服务调用证据，也不据此豁免旧数据边界检查。

| ID | 目标需求 | 当前证据与待收敛部分 |
|---|---|---|
| C1 | instructions/输入/规则/环境/工具/扩展顺序角色 | 部分一致：宿主扩展/模式及内置 memory/skills 条件排序已修复；有效filesystem权限现在经原生约束合并后进入user环境片段，实际Runtime红绿、deny_read及压缩重建联合420通过。网络域名代理入口未开放，不冒充已实现。其他来源继续收敛，见 context-snapshot-wire.md Personality 本地模板、选择与功能门控、独立 developer 片段、普通双接口及 provider 标签隔离已补证；不引入公开 Turn 风格更新 API 或官方目录，见 personality-context-gap.md。 |
| C2 | 每 Step/key/增量/tombstone/历史视图 | 项目规则冷替换/删除及角色wire已补验；plugin-guidance-state.md指导/目录分离已修复。环境v2权限快照、v1一次刷新、同目录权限改变增量及冷开去重已接通，最终定向86通过。guidance空白入口、专属替换/撤销及旧Message的Unknown恢复已修复，真实冷恢复红绿与完整context/关联预算412通过，见context-snapshot-wire.md；其他typed section继续核验。Personality 的 Known 优先、旧标签 Unknown/模型回退、静默比较快照、跨模型及自动压缩去重、旧列迁移和隐式恢复已补证；独立reference子项已按来源用途核定，43联合通过，见context-reference-compatibility.md，不扩大为Codex rollout格式导入，也不免除Corki自身恢复。 |
| C3 | 完整 token/输出预留/schema/附件成本 | context-budget-review.md：低 usage 硬超限先压缩、未支持媒体误压缩两项修复；媒体/上下文/压缩/预算 553 通过，图片音频与 usage 锚点 8 组合实证。原生比例余量链已核验；全格式成本与预算精度仍有限制 |
| C4 | 自动/手动压缩触发/选择/失败 | 降窗自动、手动 checkpoint 与失败重试已实证；summary-cancellation-review.md 修复读流/取消被关闭异常覆盖。完整触发与输入选择见 context-budget-review.md 等专项 |
| C5 | 当前输入/约束/reasoning/tool 对保留 | local-retention-review.md：三种压缩时机的混合reasoning/正文/匹配工具对进入摘要，当前输入与长用户文本保留；两普通适配器与冷回放有证据。不承诺模型摘要事实无损 |
| C6 | 替换模型历史但保留原始记录 | local-retention-review.md：新窗口移除旧reasoning/工具对，SQLite原始记录前缀不变，关闭后重开同Thread再验证；降窗/失败及跨页投影另有专项 |
| C7 | 连续历史/搜索/长期记忆召回区别 | local-history-recall-review.md逐链核验本地归档查询、自动压缩后原文搜索/读取与冷恢复；区别于CLI分页和长期记忆。大历史查询成本及跨agent兼容边界仍有说明 |
| C8 | 恢复/取消/工具中压缩一致性 | summary-cancellation-review.md覆盖摘要取消、手动/自动安装事务冷恢复；异步Pre手动/自动/失败重试后冷开不复活联合116通过。Pre及Post各自实际恢复入口投影落盘但尚未更新采样时被compact取消替换均补验，摘要保留反馈、再次冷开不复活，最近相关204通过，见async-pre-tool-review.md及post-tool-use-implementation.md。新增摘要中/压缩提交前后输入到达与取消六组合，续执行优先、输入唯一/归还及冷开联合53通过；其它Hook投递提交点交错仍未完整验收 compact SessionStart 的计划/consumer 绑定/安装后 RUNNING 冷恢复完整文件 92 通过；手动来源登记与安装失败后新 Turn 8 例、关联154通过；待写取消终态的跨 Turn 消费修复514通过。见 session-start-gap.md，各窗口不互相替代；其余交错仍须给出具体未覆盖场景。 |
| C9 | 普通请求摘要、本地管理，不用专用接口 | 已核验（用户普通路径）：prepare/compact共用无工具ModelRequest摘要与本地历史替换；三入口、双适配器及旧专用请求/历史拒绝联合429通过。见provider-isolation-refresh.md；全部故障窗口归C4/C8 |
| C10 | 所有 provider/模型/地址统一路径 | 已核验（内置普通适配器）：三入口×两接口×四标签×三地址×两模型成功及冷恢复144例，缺配置冷热零请求8例；能力/构造源码不按标签、地址或模型启用专属协议。见provider-isolation-refresh.md；不约束宿主任意自定义ModelPort |

## D Memory

入口：memory/pipeline.py、service.py、sqlite.py、agent.py、reset.py、workspace_lease.py。

| ID | 目标需求 | 当前证据与待收敛部分 |
|---|---|---|
| D1 | 跨 Turn 历史/模型窗口/工作状态 | 历史/窗口与计划、Code Mode store、终端会话、发现定义的边界已逐项归并并直接核验，见working-state-boundaries.md。当前联合39通过（d40b4d）：计划不重发，store不从归档重建，旧终端ID不授予跨Runtime资源控制，发现定义重新校验当前目录。不得将历史复制视作执行或长期记忆发布；配置快照与Hook执行账本仍分别按A1/C2/A7/C8/E7验收，不被本项核销 |
| D2 | 来源/触发/提取/合并/存储/发布 | 已核验（普通模型核心路径）：启动与来源、两阶段持久化、真实worker工具纠错/验证、Git基线、失败后修复及冷召回；当前123+31通过无跳过，见memory-source-review.md与memory-publication-review.md。不承诺跨文件事务或真实模型记忆质量 |
| D3 | claim/lease/owner/version/watermark/去重/失效 | 已核验（普通模型记忆路径）：stage-one版本/容量/租约/水位/迁移及reset旧回调拒绝；合并Top-N/稳定顺序、保留期、精确消费版本、发布失败与冷恢复当前分组49及30通过。见memory-source-review.md、memory-reset-handoff.md与memory-publication-review.md；文件/Git/DB不是跨系统原子事务，模型质量与外部不协作写入不作保证 |
| D4 | summary/搜索/读取/引用/反馈 | 已核验（普通读取路径）：summary与use门控、可选search/read、完成消息引用持久化和反馈、冷恢复；已修复把读取误当采用的计数，联合240通过。见memory-read-feedback-review.md；不承诺模型事实正确或修正旧计数 |
| D5 | 显式记住/更新/遗忘/外部污染 | 已核验（确定性Harness行为）：note/合并/修改删除/冷召回、工具与模型项污染、Hook/项目信任及worker权限/技能/插件隔离；11文件190通过无跳过，见memory-forgetting-review.md当前结论。标记数据库失败仅告警继续；遗忘非原始历史擦除，不承诺模型理解、注入免疫或disabled/external本地隔离 |
| D6 | 后台与主循环/失败隔离/取消/关闭 | 合作式生命周期已按五类核验：后台故障隔离、wait 观察者、直接 pass、claim/citation worker、超时 child；memory-close-recheck.md。本轮记忆 800 + 单独 shutdown 11 通过；不含平台强杀后的运行中回调保证 |
| D7 | 参考实际实现，不预设向量库 | 现有文件+SQLite 路径可运行；不新增向量库作为完成条件 |

## E 故障与降级

入口：tools/executor.py、core/retry.py、model_stream.py、models/failure.py、mcp/、memory/。

| ID | 目标需求 | 当前证据与待收敛部分 |
|---|---|---|
| E1 | Observation/重试/致命/取消分类 | 已核验分类契约：普通工具 Observation/Fatal、模型失败重试与取消分离；Code Mode 内嵌错误交回脚本，不误套普通 Fatal 终止规则。三文件 126 通过，见 error-classification-acceptance.md；完整 HTTP 策略归 E3，构造/资源所有权归 E4/E7，半截输出全路径归 E6，不据分类通过关闭其他缺口 |
| E2 | 未知/JSON/schema/handler/MCP断连/超时/非法超长结果 | 已核验（普通工具/Code Mode/MCP路径）：逐分类见tool-failure-acceptance.md。八文件246通过，新增未知/非法JSON/handler直接Runtime后定向33通过；含实际32MB超限、HTTP/stdio故障、取消与错误值区分。模型/构造/资源关闭及内部重试仍按E3–E7验收，不承诺所有schema关键字或任意副作用exactly-once |
| E3 | HTTP/截流/缺终态/部分输出/重试限制 | 已核验（内置普通传输）：状态分类、请求/采样双预算、终态缺失、部分工具与冷恢复、关闭错误优先级及取消，当前七文件148通过。逐要求证据与边界见model-failure-acceptance.md；摘要独立策略归C4，其他资源所有权归E7，不以此声明全部故障处理完成 |
| E4 | 压缩/记忆/扩展初始化失败与注册回滚 | 压缩/记忆及MCP required失败/部分注册/重载取消见专项；construction.py的acreate和无loop同步create已有回滚。运行中loop直接同步create且无construction owner的失败资源接管仍开放，不能笼统称全部构造路径缺失 |
| E5 | 幂等/副作用风险，不重放未知结果 | 调用身份/旧账本/完成复用/未知不重放及模型重试已核验，58通过；普通RUNNING公开恢复、嵌套提交失败和MCP断连另有证据。内部process startup重试已有66通过，patch重试已有68通过/1历史编译器跳过，见tool-result-commit-cancellation.md顶部。两条主要内部重试不再列为未审计：重试须遵守审批策略，可能保留或重复首次部分副作用，patch累积committed_delta；不承诺跨系统exactly-once或第三方私有重试安全。当前完整回归仍待终态 |
| E6 | 不把半截失败当成功，不吞取消 | 普通两接口正文/未闭合及已闭合计划增量已到达后 EOF/读失败/取消、零重试预算已直接验证，当前文件 26 通过；计划结束标记不产生完成事件。关联 75 通过含旧 CLI 混合断言、不作为 CLI 对齐完成依据。混合项重试/冷恢复与工具掩盖取消另有证据；见 partial-output-acceptance.md，其余未列明输出路径仍待逐项收敛 取消终态写后 ack 失败的精确确认已修复；原成功/失败/取消结果和告警分离，多待写结果真实进程退出冷恢复亦有证据，见 partial-output-acceptance.md；这些子项不再作为整体未知。 |
| E7 | 每 Turn 终态与全部资源清理 | 已核验的关闭边界按 runtime-close-failure-acceptance.md 资源表归并：14 入口错误/取消与共享 close、在途 reset、MCP 物理 owner、Code Mode 回调/进程、模型响应、记忆子 Runtime、checkpoint fallback 与终态存储屏障。最新生产全量 15532 passed/1 文件系统限制跳过；后加条件资源/reset 五文件 100 通过。不得再沿用旧“11 入口/全量待刷新”。E4 运行中 loop 无 owner 同步构造仍开放，跨 Hook 恢复归 A7/C8；本项不承诺 OS 强杀后运行内存回调或未实测平台。完整生命周期交付还需连同这些独立开放项验收，不单凭本项测试关闭整个 A–E。 |

## F CLI

F1/F6 计划历史临时尾部已接入只读显示；真实 Runtime + Ctrl+T/q +
流中新行 + Ctrl+C/stop、两宽度补验通过，联合 38 passed（882c9f）。
见 plan-history-tail.md；该局部差异已修复，不以此替代其余视觉验收。

最新完整 tests/e2e 已刷新：166 passed、0 skipped，290.12 秒（530bad），
包含审批/并发/焦点/取消、流式计划/表格、历史及核心示例；静态通过
（6c6ac6）。详见 core-baseline-refresh.md。该结果更新现有 PTY 的运行
证据，不代替剩余源码差异和完整视觉验收；下方旧批次描述保留为历史。

规则列表桥接现已补验：实际键盘经Runtime与原生后端成功保存精确提议，并验证保存
失败不扩大live/session授权，冷启动按持久规则重新判定。扩大134通过0跳过
（4c1ea3），详见approval-scope-ui.md，覆盖下方先前“规则持久发布须独立验证”记录。

F3/F4/F5更新：宿主shell/patch scope已从字符串输入+通用确认改为同一显式列表，
只呈现宿主提供范围，普通MCP scope字段仍走数据表单。新增一次/会话/规则/拒绝/取消
单位与40/100列scope PTY，连同完整CLI单位、审批PTY、真实执行取消、MCP工具审批、
patch审批扩大447通过1跳过34警告（16ee19）。跳过为旧patch编译器兼容，不能计作
已执行。详见approval-scope-ui.md；原生快捷键/动态网络选项/完整视觉仍未据此关闭。
后续增加真实键盘→Application→Runtime→原生执行桥接，direct/Code Mode下once
每次询问，session复用同命令但换命令/冷Runtime重新询问；四组合与审批/取消/PTY
扩大107通过0跳过（bf708b）。规则持久发布仍须独立验证，不由session证据外推。

入口：cli/application.py、terminal.py、input_owner.py、streaming*.py、history_pager.py；
近期 PTY 证据见 current-scope.md，当前95组未重跑 CLI PTY。

| ID | 目标需求 | 当前证据与待收敛部分 |
|---|---|---|
| F1 | 布局/输入/状态/正文/reasoning/工具/错误/计划/历史 | 历史分页已接入且验证；cli-streaming.md 的增量渲染剩余项仍需处理 |
| F2 | 编辑/多行/运行中新输入/取消退出 | cli-mode-entry-review.md 已覆盖 /plan、Shift+Tab 及提示、草稿保留、发布失败/取消、冷恢复、Default/Plan 运行时 Shift+Tab/排队/停止，以及设置提交成功/失败窗口的物理 Ctrl+C。其余焦点组合仍待补齐 |
| F3 | 审批实际操作/风险/键盘/提交拒绝取消 | approval-scope-ui.md已有实际scope执行桥接；approval-details-review.md补Ctrl+A只读详情、shell/patch长内容PTY、Runtime双审批拒绝/取消/关闭，以及实际部分写入后的长重试证据与拒绝/取消。自定义keymap和逐格显示等仍开放，不再将pager Runtime桥接列为完全未验证 |
| F4 | 默认高亮非同意，不污染输入历史 | choose_approval仅显式按键提交；审批单位与真实PTY覆盖等待/移动高亮不授权、bracketed paste不提交、表单缓冲及普通输入历史隔离。见approval-scope-ui.md；未标记粘贴无法与真实按键区分，不作全输入来源识别承诺 |
| F5 | 并发确认/焦点/关闭/取消，不错误授权 | test_approval_pty.py已有并发审批身份/草稿恢复与resize等待显式决策；实际执行桥接验证取消不执行、scope不越权及冷会话不继承批准。全InputOwner关闭/取消交叉仍需独立收敛，不从这些局部证据推断全部清理路径 |
| F6 | 渲染/快照/真实 PTY 宽度长内容交互 | 已有40/100列分页等实证；完整界面与流状态矩阵仍待收敛 |
| F7 | 不扩张成官方桌面/账号/云端 | 明确排除；不是需要补齐的差异 |

## 横向交付与下一验收门槛

1. 范围清理：默认值、能力推断、旧配置、请求、响应、持久回放、提示词、测试、文档
   均需当前证据。源中四个常见官方端点/内部头字面量扫描无匹配，只是弱证据，不等价
   于网络隔离完成。通用 MCP OAuth 必须保留并单独验证凭据来源隔离。
2. 源码对齐：所有待收敛行先链接对应 Codex 调用链，再判断局部实证是否覆盖真实分支；
   不把源码文件存在视为已实现，不把旧审计“待实现”直接当成现在缺失。
3. 当前项目全面回归与必要原生后端/跳过项核对尚未刷新；不重用旧全量通过作当前验收。
4. 组合端到端已有搜索/恢复、模型降窗、记忆召回独立证据；最终交付需要明确各自边界，
   不把不同运行拼成一次未执行的全链路测试。
5. 明确实现缺口仍包括已加载历史源变化的全量渲染成本及 cli-streaming 的剩余项。
   是否属于必须对齐的原生行为，需要对应源码与可观察影响，不随意扩大为新优化项目。

下一轮优先刷新 provider 隔离/通用 OAuth 与模型流错误组合，然后把上述待收敛行逐项
转为有具体源码、直接测试与边界说明的关闭记录。不得只反复重跑95组而停留在局部验收。
