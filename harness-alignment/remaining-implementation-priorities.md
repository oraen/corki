# 当前实现缺口与验收顺序（不替代完整 A–F 清单）

2026-09-22 用户再次恢复 A–E Harness 核心对齐，`objective.md` 已切换；
CLI 交互细节暂缓，原目标单独归档。最新 B3/E2 检索故障隔离反例：畸形
可选 schema 元数据原会使整批 deferred 索引失败，现跳过非预期形状的
检索文本字段，不改原 schema；相关五文件 69 passed、0 skipped，静态
检查通过，详见 `tool-search-ranking-review.md` 顶部。未跑本批全量，
下方全量数字均为此次生产改动前基线，不冒称当前代码全绿。

最新 B1/B8 工具策略注册准入修复后，非 CLI 同范围全量实际退出 0：
**15635 passed、7 skipped、568.85s**，8 workers/loadfile/禁重启。
`-rs` 确认六项仍需不存在的历史编译器、一项为文件系统非 UTF-8 名称限制。
七项新构造反例先红后绿，工具/协议与搜索/Agent 循环联合 1401 通过；
详见 `tool-search-ranking-review.md` 顶部。下方 15627 为此次生产改动前基线。
仍需核对 A–E 的其它开放项；不能用全量绿代替行为差异审计。

最新非 CLI 全量在 E2 模型输出身份边界修复后实际退出 0：
**15627 passed、7 skipped、578.55s**，8 workers/loadfile/禁重启。
七项跳过仍为六项依赖已不存在的历史编译器、一项当前文件系统拒绝非 UTF-8
文件名，本批 `-rs` 已核对。此结果替代下方 15616 生产前基线，不代表条件能力
或全部 A–E 差异已完成。

## 2026-09-22 E2 普通模型输出身份边界

Codex `protocol/src/models.rs` 的函数调用 `call_id` 是 `String`，反序列化/类型系统
保证字符串身份；Corki `ToolCall` 和 conversation item 的 Python 注解不作运行时
校验。`graph._validate_model_items` 原先先对 step/item/call ID 建集合，异常适配器
返回列表会抛未分类 `TypeError`；整数会被接受，孤立 surrogate 会在后续 UTF-8
持久化时失败。六个反例先全部失败，现于模型输出提交前按普通协议错误拒绝
非法 turn/step/item/call ID 与工具名；空字符串仍沿用既有合法语义，不按名称
自动匹配任何工具。11 个专项通过；core/models 与模型提交、续行、工具模式、
恢复集成合计 603 passed，退出 0。没有新增官方协议或服务请求。修复后的
全量结果见本页顶部；A–E 仍开放。

## 2026-09-22 恢复 A–E 核心目标（覆盖下方 CLI 阶段入口）

最新 C3/D1 修复：普通供应商低报 usage、可见历史已超过本地硬窗口时，
`get_context_remaining` 原仍报告 17900，下一请求却立即压缩。现在剩余量
和通知的硬上限与 `prepare` 的完整请求本地估算一致，同时保留 usage 对
自动阈值的所有权。真实 Runtime 反例先红后绿，total/body-after-prefix
均覆盖；相关 97 通过。扩大 context/token-budget 初批 600 通过、28 跳过，
确认全部因未传原生编译器；补传后同范围 **628 通过、0 跳过**，
见 `context-budget-review.md` 顶部。该生产改动晚于下方 15614 全量，
但修复后的同范围非 CLI 全量现已退出 0：**15616 passed、7 skipped、
569.03s**，8 workers/loadfile/禁重启；跳过原因同此前，15614 已被本批
替代。不能因此宣布 C3 或 A–E 全部完成。

最新 D/E 数据安全修复：旧记忆 Git 基线原仅凭一条提交及 Codex 固定正文
认定“自有”，可能把相同正文的无关仓库交给 `reset` 删除 `.git` 历史；
现还核验作者/提交者的名称与邮箱。真实旧基线仍可迁移并改用 Corki 身份，
伪造身份在删除前拒绝。反例先红后绿，四文件 44 passed，详见
`memory-source-review.md` 顶部。新提交从未使用 Codex 身份，之前的
身份疑点已纠正。扩大 memory 全组 840 passed；生产修复后非 CLI 全量
已退出 0：**15614 passed、7 skipped、592.09s**，8 workers/loadfile/禁重启。
跳过原因与下方历史批次相同，15609 已被本批替代；其它 A–E 差异仍开放。

最新 B4 继续修复：动态搜索输出 `output_schema` 仅 JSON 类型改变时，
缓存最后一层仍把当前与旧 handler 定义当成相同；现重新绑定定义但复用
不变的检索索引。单位反例先红后绿，真实 Runtime 三种替换 × 两类流式
结果 × 两类旧配置输入通过；两文件 37 passed。详见
`tool-search-ranking-review.md`，扩大 12 文件 140 passed。生产修复后的
同范围非 CLI 全量现已退出 0：**15609 passed、7 skipped、564.23s**，
8 workers/loadfile/禁重启，跳过原因同下方历史批次。15604 已被本批替代；
其它 A–E 差异仍未全部关闭。

最新 B3/B4 修复：动态搜索缓存与索引仍把 schema 中的 JSON `true`/`1`
视为相同，导致换代后继续返回旧定义；两个单位反例先红后绿，真实 Runtime
已准备 Step 冻结→新 Step 重搜→新定义执行链通过。工具发现 12 文件
135 passed，详见 `tool-search-ranking-review.md` 顶部。修复后的同范围非 CLI
unit+integration 全量现已退出 0：**15604 passed、7 skipped、569.94s**，
8 workers/loadfile/禁重启，`-rs` 确认六项因五份历史编译器不存在而跳过、
一项因当前文件系统拒绝非 UTF-8 文件名而跳过。命令同下方 15598 批次，
增加 `-rs`。这是本次修复后的全量基线；不据此关闭其它 A–E 差异。

用户已明确恢复 A–E Harness 核心对齐，范围以
`objective-core-paused-2026-09-21.md` 为准。此前 CLI 基础体验目标已结束；
本阶段不继续扩张 CLI 样式/斜杆命令，仅处理必要的核心安全与生命周期故障。

构造迁移后首次当前代码非 CLI `unit+integration` 回归，8 workers、
`--dist=loadfile --max-worker-restart=0 -x`，在 54% 首败终止：
8653 passed、1 skipped、1 failed，340.55s。失败为
`test_mcp_managed_loading.py::test_cli_captures_file_once_before_directories_and_runs_real_tool_loop`
在运行中的事件循环内调用同步 `build_application()`，触发预期的无 owner 拒绝。
该测试已改为 `await build_application_async()`，保留原 managed policy 捕获、
真实工具循环与清理断言；该文件 27 passed。生产代码未因本批更动。
随后相同非 CLI 范围（移除 `-x`）完整回归实际退出 0：
**15598 passed、7 skipped，566.65s**；8 workers、loadfile、禁 worker 重启，
设置当前原生沙箱编译器。命令使用 `PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring`
及 `CORKI_TEST_SANDBOX_COMPILER=.../src/corki/_native/sandbox/corki-sandbox`，
执行 `pytest tests/unit tests/integration --ignore=tests/unit/cli
--ignore-glob='tests/integration/test_cli*.py' -n 8 --dist=loadfile
--max-worker-restart=0 -o addopts='' -q --tb=short`。本批未传 `-rs`，
7 项跳过已用 `-rs` 定向核对：六项需要五份不再存在的历史原生编译器，
一项由当前文件系统拒绝非 UTF-8 文件名；不能当作通过。新增修改文件的 ruff check、
ruff format --check 与 git diff --check 均通过。原 50851 的 799 失败是历史结果，
不能继续当作当前失败数；此轮全量通过也不代表 A–E 全部行为对齐。
下一步回到差异表中的未闭合源码/行为契约，优先可在独立兼容模型路径验证的缺口。

## 当前入口（覆盖下方按时间保留的旧状态）

2026-09-21 用户明确切换目标：以 objective.md 为准，当前优先 CLI/TUI
交互对齐和实际使用 bug 修复，暂停 A–E 核心独立扩展及全面等价审计。
下方核心待办和测试状态仅作历史记录，不再决定当前执行顺序；旧目标完整归档
于 objective-core-paused-2026-09-21.md。仅在修复 CLI 故障必需时做最小核心改动。
先建立 cli-interaction-audit.md，优先启动、输入、流式显示、审批、取消和退出，
再完善布局和样式；继续排除 OpenAI 官方专属服务依赖。
核心构造迁移的后续局部回归没有已确认的最终通过结果，保留为未验证，不能宣称全绿。

### 以下为切换前的历史状态（不是当前执行指令）

50851失败已按JUnit归类（e49d2b）：137例同步构造准入拒绝，其余主要是未await
协程的属性/解包错误，另1例预期ValueError未抛。跨文件直接async调用135处
已补await（93382退出0，e311e1）；还剩sandbox_denial_retry:103与
context_snapshot_depth:30的同步包装层，以及原合法同步helper被异步调用的
路径需迁移。当前未启动新测试，不能把源码补齐当作失败已修复验证。

50851已实际退出1（b05d1f）：799失败/14801通过/1文件系统skip/103warnings，
613.79s；完整日志full-regression-50851.txt，JUnit在原临时目录。现可修复
跨文件导入helper的await遗漏；先按具体失败类别确认，不把全部失败预判同因。
不要再轮询已结束句柄，不以旧全量或局部constructor通过宣称当前迁移全绿。

50851仍运行，已输出多项失败，不能称迁移完成/全绿。只读AST跨文件引用检查
89814退出0（6045ef）发现137处导入已迁移async helper的调用未await；初次
迁移仅检查定义文件内引用，遗漏外部调用。下一步先等50851实际终态及具体
失败清单，再迁移跨文件调用（含同步包装层），保持原断言/故障窗口。
本轮仅诊断，运行期间没有修改生产/测试；不得提前并发重跑同范围。

构造调用迁移第二批完成（helper/嵌入式脚本），完整非CLI回归句柄50851
正在运行，日志 /private/tmp/corki-construction-regression.BHOX16/pytest.log，
JUnit同目录results.xml。命令存储construction_full_command，工具最近状态
construction_full_result；8 workers/loadfile/禁重启。先等实际退出，不重跑，
不在测试期间修改生产/测试。详情sync-in-loop-construction-decision.md。

用户已批准E4修复及目标内合理兼容调整，不再等待同一确认。无owner的in-loop
同步create现提前拒绝，constructor27专项已先红后绿；531处直接async调用
已迁移，仍需处理同步helper/字符串内进程fixture，见
sync-in-loop-construction-decision.md。联合94761已退出0（38c869），core单元+
constructor/CLI构造204通过/2.30s，4 workers/loadfile/禁重启。当前无活动测试。
旧25019全量是本次生产修改前的证据，不冒称新版本全绿。

2026-09-14 等待用户决策：连续三轮确认E4构造兼容选择仍无答复；已复核
construction.py、实际故障证据和仓内迁移影响，没有可保持同步签名且在当前
event loop等待任意异步closer完成的安全替代。不能后台无owner清理或换loop
冒充等价修复。首要解阻：是否允许运行中loop无owner的create在分配前拒绝，
迁移为await acreate。Persistent调度入口、多执行环境及真正多Agent范围亦
未确认，不自行扩建或排除。已有全量/E2E已终态且生产未变，无测试进程待等；
不会以重复已通过回归替代选择。目标未完成，等待明确决策后恢复实施与验收。

组合E2E三项已通过（85843退出0，ce81fe，3.41s），覆盖多Step/跨Turn、
终端/patch/图片、搜索→定义加载→真实stdio MCP→Observation。编译与依赖检查
通过；项目规定lint通过，format仅无关用户文件tests/example/vivi.py未通过，
未改该文件。详见core-e2e-2026-09-14.md。生产/测试未变，不再重复全量；
继续A–E开放项，E4与条件能力的用户选择仍未收到，不自动把它们排除。

25019 已实际退出0（299418）：15598 passed、1 skipped、553.20s；唯一skip
为文件系统拒绝非UTF-8文件名。完整命令/范围见 full-regression-25019.md，
原始日志在 /private/tmp/corki-harness-regression.XUK755/pytest.log。
这是当前stdin修订及world-state生产接口的非CLI全量证据，不关闭其它A–E
开放项。51160结果未知与33685失败仍保留。下一步继续能力范围核定和组合
E2E/静态验收；多Agent版本选择的新证据见 context-section-inventory.md，
已询问用户是否本轮纳入真正子Agent编排，不以V2默认关闭排除整个能力。

2026-09-14 重新核实：51160 句柄已不存在（Unknown process id），pgrep 未发现
pytest/execnet；累计输出仅至约20%，最终退出码与汇总未保留，故本批结果未知，
不能写成通过或仍在运行。当前 Corki HEAD=e53a368，启动前工作树干净；Codex
仍为 ddf04ad26789d040f9ef6a96736f76602e35a6cc，工作树干净。
重新评估12逻辑核/18GiB/38%空闲及测试子进程成本，采用8 workers/loadfile，
不提高到96。相同非CLI unit+integration全量重新执行，日志直接写入
/private/tmp/corki-harness-regression.XUK755/pytest.log，JUnit写入同目录results.xml。
该批命令为下方51160原命令追加 --junitxml=上述results.xml，并重定向stdout/stderr
到pytest.log；六个compiler均已确认存在。跟进工具实际退出，不以日志进度当终态。
本次ruff check通过；format额外发现用户文件 tests/example/vivi.py 未格式化，
其余1527文件通过（teach.md仍按要求额外排除）；不擅自修改该无关文件。

stdin 等待修订后的新非 CLI 全量已启动，句柄 **51160**，尚未完成。范围与
33685一致：unit+integration排除CLI，8 workers/loadfile/禁重启，当前及五个
历史compiler显式配置。资源重查12核/18GiB/40%空闲、无其它pytest（a15a9d）；
compiler均存在，项目lint与保留原排除规则的format（额外排除teach.md）通过，
1527文件（04c383）。命令/累计输出/最新状态：stdin_full_command /
stdin_full_output / stdin_full_result。先跟进51160至实际退出，不重复启动；
运行期间不修改生产或测试。33685失败日志仍保留。

33685 的 stdin 断言已按真实观察契约修订：原3s生产timeout不变，轮询同一
活跃session至READY/退出；六个门控参数与三个负向对照证明不能将0.2s yield
当完成保证。六文件 **78 passed / 11.99s**，12980 实际退出0（609ed7）。
详细过程含错误测试路径/格式失败见 stdin-observation-regression.md。
下一步重新评估资源刷新非CLI全量；33685仍保留失败，不以定向通过替代。

**33685 已实际退出 1**（8af413）：15589 passed、3 failed、1 skipped，544.67s。
完整命令/日志 full-regression-33685.txt；唯一 skip 为非 UTF-8 文件名限制。
失败均在 test_stdin_terminal_contract：source eof/raw、interrupt pipe(False)。
观察值 exit_code=None 且 session_id 有效，输出尚空；测试在 execute 的0.2s
yield后直接要求READY/退出。下一步核对 ProcessManager 的yield/等待契约，
用受控延迟证实测试是否误把观察窗口当作完成期限，不先提高超时或删断言。
新扩展没有在本批报错，但不能把失败全量写成通过。已停止，不能重复轮询此句柄。

当前非 CLI 全量已启动，句柄 **33685**，尚未完成。unit+integration 排除
unit/cli 与 integration/test_cli*.py；8 workers/loadfile/禁 worker 重启，当前
及五个历史 compiler 显式设置，与81536范围一致。资源重查12核/18GiB/33%空闲、
无其它pytest（d488eb），compiler均存在（515be8）。命令与累计输出保存在
extension_full_command / extension_full_output，最近状态 extension_full_result。
必须跟进此句柄至实际退出；运行期间不修改生产/测试，不重复启动。
项目ruff check通过；format全项目仅teach.md未通过，按用户要求不修改。
一次误用--exclude替换项目原排除规则，额外检查了skills脚本，结果不能算项目
格式失败清单；已改用--extend-exclude teach.md核对。compileall与pip check完成。

扩展普通 HTTP wire 两模式与 JSON 比较规则已补证/对齐，见
extension-world-state-contract.md。联合 **608 passed / 5.61s**，82411 实际退出
0（584ce0），静态通过；生产有变，下一步重新评估资源后做更广回归及项目
静态检查。当前无测试进程待跟进，不重复旧的角色/JSON/请求投影未知清单。
其它条件能力和 E4 待选择仍保留，CLI 继续暂停。

新接口 capture/render 两处取消控制流已通过 Runtime 验证；同 key 旧宿主
相同 JSON 缺少显式来源却被误判 Known 的问题已先红后绿修复。联合
**603 passed / 5.90s**，1807 实际退出 0（a6c17b），静态通过。详情
extension-world-state-contract.md。下一步完成角色变化、JSON 比较与普通请求
投影的有界核验，然后更广回归；当前没有活动测试。CLI 继续暂停。

自定义扩展前缀兼容缺陷已先红后绿修复：识别 producer ownership 需要显式
content_kind，不仅 key。自动 compact 实证及四种 renderer/重复 ID 故障已补；
联合 **600 passed / 5.77s**，60078 实际退出 0（1ffa1f），静态通过。
见 extension-world-state-contract.md。下一步核对取消、同 key 接管/角色变化、
JSON 比较与请求投影，再刷新更广回归；没有活动测试，不恢复 CLI。

自定义 world-state 第一批已接入 Runtime 普通 prepare/自动压缩重建。新集成
覆盖 cold/silent/移除再挂载/手动 compact；与 unit/context、unit/core、既有
extension 生命周期和 plugin compact 联合 **594 passed / 5.40s**，95694 实际
退出 0（752d61）。此前同名测试收集失败保留。详情 extension-world-state-contract.md。
下一步完成自动压缩与冲突/非法回调/角色故障验证、JSON null 语义和旧命名空间
兼容审查，再刷新匹配范围回归。生产已改，81536 不再是当前实现的全量证明。

新确认可独立实现的 C1/C2 缺口：自定义 world-state 缺少生产者控制的 diff /
legacy / retained 契约。真实 Runtime+SQLite 两 Turn 诊断确认来源消失会自动
生成通用撤销，64092 实际退出 0（2699af）；这不是修复通过。原生当前 section
遍历不会自动生成该正文。见 extension-world-state-contract.md 的调用链、实测
和下一批验收要求。优先补独立契约并接入真实窗口，保留旧 PromptContribution
兼容；不依赖 Pending 的 Persistent/E4/多环境选择，不重复已覆盖的 Hook 测试。

Realtime 角色边界已复核：原生默认是 intermediary 后的 backend executor，
不能简化成“语音”，也不能与 Corki 直接用户文字 steer 混为一谈。当前文字
入口不应复制原生间接对话正文，见 context-section-inventory.md。未据此宣称
整个能力一致或自动扩建中间层协议。Persistent/E4/多环境选择仍未收到回复；
下一步继续清理 C1/C2 来源总表中真实实现证据与条件范围，不重复已有 Hook 回归。

Persistent 本地入口已追踪到 settings→准入副本→ContextBuilder。接入选择见
persistent-mode-decision.md：建议显式本地开关与 provider effort 解耦，宿主
负责再次采样；若需要 Runtime 自动调度，则不是单纯上下文注入。此语义选择
需用户确认，不静默替代完整模式。未改生产；E4/多环境选择亦仍待回复。

C8 已有证据按六个真实入口归并，见 compact-recovery-boundaries.md；不再把
“所有其它交错”列作无界测试要求。C1/C2 Persistent 资产已完整核查：本地
跟进策略不授予新权限，正文不负责再次采样；Corki 普通 effort 字符串不等于
已实现该模式。下一步明确本地模式/宿主调度入口，见 context-section-inventory.md。
本轮只读源码与归并文档，没有生产/测试变更，81536 生产基线仍有效。

C8 stopped/损坏 receipt 补证完成：Pre/PostCompact × 手动/自动八例，停止
保持 CancelledError/TurnCancelled，字符串 stopped 被拒绝，均不重放副作用；
三文件 **106 passed / 6.31s**，7718 实际退出 0（257d9d），静态通过。
见 compact-recovery-boundaries.md；生产未改。下一步归并 C8 已有安装、异步
投影、SessionStart/输入窗口证据后回到 C1/C2 实际条件能力，不反复新增同类排列。

C8 新补明确窗口：Pre/PostCompact 成功 receipt 已写、graph 尚未继续，手动/
自动 × 保持/移除配置八例；恢复不重复 Hook、摘要或工具，历史前缀保持。
两文件 **54 passed / 4.76s**，15846 实际退出 0（a98a22），静态通过，生产未改。
见 compact-recovery-boundaries.md。下一步先核对 stopped=true/损坏 receipt
已有覆盖，不从成功 receipt 外推；已完成的异步投影/SessionStart 窗口不重复列未知。

Stop 损坏持久批次补证完成：版本、request 身份、未知 execution key × 有无
晚到输入六场景，真实 SQLite 冷恢复拒绝新副作用。首次错选 prompt batch 的
两项 fixture 失败保留记录，限定 stop: 后两文件 **77 passed / 5.30s**，
22530 实际退出 0（f0836a）。详见 hook-batch-recovery-design.md；生产未改。
下一步将 C8 剩余恢复交错核定为具体未覆盖窗口，而非重复本批已完成场景。

A7 Stop 批次换序补证完成：两个不同真实命令在冷配置交换位置和匹配批准，
批次无 claim/unknown/completed × 有无晚到输入六场景均在新副作用前拒绝
identity collision。原 60 场景保留，完整文件 66 passed / 5.25s，35148
实际退出 0（58195d），ruff 通过；生产未改。详见 hook-batch-recovery-design.md。
下一步核对损坏批次/记录是否已有实证，并将 C8 的“其余交错”落实到具体窗口；
不恢复历史 CLI/PTY 门槛，不把有界补证变成无穷组合测试。

E7 资源证据已按实际 ownership/终态/存储入口归并到
runtime-close-failure-acceptance.md，验收主表同步纠正旧 11 入口与过期全量门槛。
14 入口及 reset、MCP/Code Mode、模型 HTTP、memory jobs、checkpoint fallback、
terminal 存储屏障不再笼统列未知。未新增生产或测试；下一步推进 A7/C8 具体
未关闭恢复要求与 C1/C2 条件来源。E4 API 兼容选择及多环境范围仍待用户回复，
不能把自动 goal 续行视为授权，也不据此阻塞其余独立工作。

在途 reset 组合补证完成：复核发现原本已有真实文件删除 barrier 测试，本轮
只扩展 close 观察者取消与 downstream model/repository/writer 顺序和 lease。
五文件 **100 passed / 10.77s**，92233 实际退出 0（0bfb81），ruff 通过。
见 runtime-close-failure-acceptance.md 的纠正与新证据。生产未改，81536 全量
仍是当前生产基线；不再把此具体窗口当未知。下一步归并 E7 已覆盖资源与实际
剩余项，并推进 C1/C2 的其它条件来源；多执行环境范围及 E4 API 选择仍待回复。

条件资源 shutdown 专项 **19118 已退出 0**（8c59c0）：48 passed / 2.65s，
覆盖真实启用 memory reset/service/history notes 的 28 个错误/取消参数。
下一步补在途 reset 与 Runtime close 的等待、取消观察者及资源依赖验证；
本批未改生产逻辑。详情 runtime-close-failure-acceptance.md。

**81536 已实际退出 0**（c30736）：15532 passed、1 skipped，527.21 秒。
完整日志 full-regression-81536.txt；唯一跳过为文件系统拒绝非 UTF-8 文件名。
1765 失败保留为历史，不再将其当当前全量结果；原失败精确原因未追溯确认。
本次覆盖记忆窗口修复及 elicitation 计时测试，不据此关闭其它 A–E 开放项。
退出后才扩展 Runtime shutdown 矩阵，真实启用 memory reset/service/history notes，
新增 3 个故障入口 × 2 类错误，共 28 个矩阵参数；当前专项句柄 **19118**，
4 workers/load，尚未退出，ruff check/format 已通过。生产逻辑未改。

新非 CLI 全量已启动：句柄 **81536**，尚未完成。资源重新检查为 12 核、
18GiB、31% 空闲，无其它 pytest/execnet，五个历史 compiler 均存在（73b809）。
采用 8 workers/loadfile/禁重启，沿用 unit+integration 排除 CLI 的完整范围，
当前及五个历史 compiler 显式设置；输出累计 elicitation_full_output，
最新状态 elicitation_full_result。先跟进该句柄至实际退出，不重复启动。
运行期间冻结生产/测试；允许只读核对未闭合上下文来源。244 定向通过不代替全量。

elicitation 计时边界已补确定性故障注入及源码对照，见
elicitation-timing-boundaries.md。普通用户等待与回答后的交付超时分开验收；
未改生产预算或重试。六文件 4 workers/loadfile 联合 244 passed / 35.95s，
46356 实际退出 0（389e19）。原全量失败精确原因仍未知，不将定向通过替代全量。
暂停失效负向对照已确认实际成功断言拒绝超时，50523 退出 0（944b1d），
ruff check/format 通过。下一步检查资源刷新非 CLI 全量；其它 A–E 开放项保留。

elicitation 诊断补充：完整 test_mcp_elicitation 文件 4 workers/load 并行运行，
36 passed / 6.32 秒（77938 实际退出 0，b8987d）；各参数使用独立临时目录和
离线 HTTP/独立 stdio 进程。与串行三个 action 一样，未复现原全量失败，不是修复。
当前无活动测试。下一步对确认后恢复计时及响应交付做确定性故障注入，先取得
具体 observation 错误与各阶段耗时；不要在没有新证据/改动时反复全量重跑碰绿。

**1765 已实际退出 1**（82dbcc）：15530 passed、1 failed、1 skipped，546.65 秒。
日志 full-regression-1765.txt 已保存。此前 1728 的 38 个失败场景本次均通过，
但全量仍不通过：唯一失败是 test_mcp_elicitation::
test_runtime_host_response_survives_human_wait[accept-code_mode-tools/call-http]。
模型断言工具结果缺少 host response received，原错误文本被 pytest 截断，
目前不能确定是实际超时、暂停预算问题或其它错误，不可直接提高超时掩盖。
已仅补 observation.content / TurnFailed.error 的断言诊断，未改生产或成功条件。
同路径三个 action 串行通过（7634 实际退出 0，c3c931），不视作修复；下一步
完整文件并行诊断及暂停预算/HTTP响应交付的真实故障窗口。不要重启已结束1765。

1728 的 38 个失败已逐类处理，见 window-recall-regression.md：37 个 memory 场景
在真实新窗口中保留原召回/来源/隔离断言；SSE deadline 用实际预算在 GET 前或
拒绝响应关闭后确定性到期，不要求 HTTP 准备必在 30ms 内结束。未修改生产逻辑。
十一文件联合 **212 passed / 24.22 秒**（90907 实际退出 0，34c232）。
新非 CLI 全量已启动，句柄 **1765**，尚未完成：与 1728 相同 unit+integration
范围及 compiler 环境，8 workers/loadfile/禁重启。12核/18GiB、38%空闲且无其它
pytest/execnet（7d4181），五个历史 compiler 存在（e41a2f）。输出累计存于
window_recall_full_output，状态 window_recall_full_result。先跟进该句柄，不
重复启动；运行期间冻结生产/测试。1728 的失败日志保留，不能改称已全量通过。

**1728 已实际退出 1**（4254c5）：15481 passed、38 failed、1 skipped，556.78 秒。
完整日志 full-regression-1728.txt 已保存；不再轮询或把此批当全绿。37 个失败位于
memory 集成场景，另 1 个为 unit/mcp/test_sse_resume 的 tools/list-401 截止时间
场景（GET 未开始就超时），须独立诊断，不能归因于记忆或静默重跑掩盖。
本轮先修订 3 个已源码确认的旧预期：memory_read_policy 两例和 reset_runtime
enabled 例，同窗口保持历史、新窗口重读，保留策略/工具禁用/删除断言。三文件
联合 39 passed（d3e840，6538 实际退出 0），ruff 通过。未进一步改生产代码。
下一步逐文件修订并加强新窗口召回验证：memory_model_http（6）、
memory_pipeline_runtime（13）、memory_agent_runtime（3）、memory_change_evidence
（10）、long_term_memory（1）、memory_retention（1），合计 34 个此前失败场景。
不能简单移除摘要断言；保留提取/合并/来源/搜索/读取/引用及 provider 隔离的原覆盖，
在真实新窗口重建后断言新摘要。之后定位独立 MCP 30ms 截止时间问题，再刷新全量。

本批窗口生命周期修复的非 CLI 全量已启动，句柄 **1728**，尚未完成。
unit+integration 排除 unit/cli 和 integration/test_cli*.py；8 workers/loadfile/
禁重启，当前及五个历史 sandbox compiler 与 35013 环境一致。资源记录见
extension-context-producers.md；先跟进 1728 至实际退出，不重复启动。输出累计
在 initial_context_full_output，最近状态在 initial_context_full_result。
912 定向测试已通过（d26633），不冒充全量结果。运行期间不改生产/测试。

记忆窗口生命周期已实施（extension-context-producers.md 当前实现节）：普通
prepare 延迟 initial contributor，已有窗口不读取/撤销，新窗口及自动压缩替换
重新加载，手动压缩仍在下一 prepare 重建。冷恢复/摘要出现、更新、删除已有
真实 Runtime 次数与历史证据。生产已改，需本批回归，不能沿用 15504 全量结论；
下方“待实施”是发现时状态。其它 C1/C2、C8、E4/E7 及 CLI 暂停边界不变。

新确认 C1/C2 实现差异：memory thread contribution 被每 Step 当 world-state
刷新/撤销，原生仅 full initial window 注入。见 extension-context-producers.md。
优先追踪 prepare/compaction/cold restore 并修复窗口生命周期，不再只核对初始
排序。新增真实 skills+memory 组合测试覆盖权限两分支及输入正文身份；未改生产。

C1/C2“其他section”现拆为明确producer/条件清单，见context-section-inventory.md。
新核对的未闭合分支是实时会话与文字steer的能力差异、DeferredExecutor多环境
指导、Persistent本地策略与V2多Agent策略；后两项不能从普通effort字符串或
预留slot推导已实现/已获授权。另有extension producer来源/删除/顺序需逐项归并。
本轮未改生产或测试，15504回归基线仍有效。下一步按该清单先核对实际能力入口，
不新增虚假工具/语音提示，不把这些分支悄悄算作一致，也不重复已关闭风格用例。

C1/C2独立旧reference格式适用性已核定：Codex本地rollout的TurnContext参考
不能误称官方服务，也不需要复制其文件导入格式。Corki旧Message/typed context、
Thread默认与准入checkpoint各自恢复用途已映射，七文件43通过（e3fdab）。
见context-reference-compatibility.md。未修改生产或测试，15504全量基线仍有效；
后续处理其它具体来源/section或跨Hook窗口，不再把独立Codex rollout导入列作缺口。
E4同步构造入口兼容调整仍待用户确认，没有因自动goal续行而视作同意。

E4运行中loop同步create缺口本轮重新独立复现（c82560）：Graph晚期构造异常后
自建模型未关闭；同场景acreate已关闭。见sync-in-loop-construction-decision.md。
建议在资源分配前拒绝无owner的loop内同步create、改用await acreate，需要用户
确认API兼容变化；未擅自实施。当前无测试进程，生产未改，15504全量仍为当前
回归基线。若等待该选择，仍可推进C1/C2/C8等独立开放项，不据此标整个goal阻塞。

最新非CLI全量 **35013已实际退出0**（c86532）：15504 passed、1 skipped，
535.66秒（fff162）。唯一跳过为文件系统拒绝非UTF-8文件名；完整输出见
full-regression-35013.txt。8 workers/loadfile/禁重启，范围与64194一致；启动
资源、当前及五个历史compiler记录见下方。覆盖有序增量结果发布、真实进程退出
冷恢复、心跳计时和进程观察测试修订。运行期间没有改生产代码/测试，没有失败
重跑或worker重启。88968和64194失败日志保留，不把旧结果覆盖成通过。
当前无活动测试，不再轮询35013。A5明确需求（并行/独占/排序/运行中新输入）
已有逐项源码与Runtime证据并完成本次回归，主表已归并为已核验；不外推关闭
A7/C8/E4/E7等独立故障要求。下一步回到这些实际开放项及C1/C2来源/旧状态
适用性核定，不重复运行没有新状态变化的全量测试，也不继续排列已验收A5用例。

两处测试边界修订后的全量已重新启动，句柄 **35013**（启动结果亦保存在
process_full_result）；8 workers/loadfile/禁重启、unit+integration排除CLI文件，
参数与64194相同。启动前无pytest/execnet、12逻辑核/18GiB、报告38%空闲，
五个历史compiler均存在（b43944）。运行期间冻结生产代码/测试，不覆盖旧失败日志。

最新全量64194已结束退出1（7ded6e）：15499通过/1失败/1跳过，539.43秒。
唯一失败为inherited stdout测试读取execute提前yield的旧观察结果；已用主进程
延迟0.15秒两例稳定复现并修订为同session最终观察，保留1.5秒上限与全部资源
断言，未改生产代码。五文件59通过（7ab66d），详见process-observation-test-boundary.md。
当前无活动测试；下一步仍需刷新非CLI全量。64194/88968失败原日志均保留，
不可改称通过或继续轮询已结束句柄；A–E其它开放项与CLI暂停范围不变。

修订心跳测试后的新一轮非CLI全量已启动，句柄 **64194**；不是已通过。
启动前复核12逻辑核/18GiB、memory_pressure报告43%空闲、无pytest/execnet，
五个历史compiler文件存在（a444d8）。与88968相同范围/环境：unit+integration
排除unit/cli与integration/test_cli*.py；8 workers/loadfile/禁重启，addopts=''、
-q --tb=short -rs、null keyring、当前sandbox及五个历史compiler。运行期间冻结
生产代码和测试；后续先观察64194至实际退出，不重复启动，不覆盖88968失败记录。

88968已结束退出1（5bc64a），15497通过/1失败/1跳过，544.59秒，完整日志
full-regression-88968.txt。唯一失败为记忆心跳测试run_once总1秒超时。
通过新增1.1秒慢准备两例稳定复现测试计时缺陷，修订为真实心跳失败后的1秒
模型关闭检查，finally显式关闭owner，不改生产逻辑；339联合通过（46b415）。
详见memory-heartbeat-test-timing.md；当前无待观察测试句柄，下一步刷新非CLI
全量。不得将本次失败改写为全绿，也不再轮询/重启88968；A–E范围不缩减。

A5 前缀发布冷恢复专项已完成：batch/streamed 实际 os._exit 后恢复保持历史
前缀、调用/输入不重复、旧工具不重放、tail unknown，不虚构外部副作用原子性；
50联合通过（df9384），见 ordered-tool-publication.md。下一步先跟进非 CLI
全量 **88968** 到实际退出，运行中冻结被测代码与测试，禁止重复启动。
本轮全局 ruff/格式/compileall/依赖/diff 均通过。全量未结束，不能核销为全绿。

本轮 A5 回到源码发现并修复工具结果的整批发布差异：原生 FuturesOrdered
逐项记录，Corki 旧 gather 后整批 append；现在普通 batch/streamed/模型错误
drain 均有序逐项写入。两反例转绿，新增四项写入前/后失败取消尾部测试；
196 调度/输入/恢复与686 core/storage/Hook 联合通过，句柄均退出0，详见
ordered-tool-publication.md。下一步先补前缀发布后冷恢复窗口，再刷新非 CLI
全量。下方74939/15490为本次 graph/live_tools 生产修改前的全量，不覆盖本次。
A5 已将 readiness、Code Mode、steer 按源码与断言分项归并；A–E其余缺口仍保留。

最新非 CLI 全量回归 **74939 已实际退出0**（92002f）：15490 passed、1 skipped，
547.32秒。唯一跳过为 test_project_trust_selection.py:164 的非UTF-8文件名限制。
完整日志 full-regression-74939.txt；当前无活动测试，不再轮询或重启旧句柄。
启动前确认无 pytest，12逻辑核/18GiB、空闲38%；8 workers/loadfile/禁worker重启，
范围 tests/unit + tests/integration，排除 unit/cli 与 integration/test_cli*.py，
addopts=''、-q --tb=short -rs。null keyring、当前sandbox compiler及五个历史
compiler路径同79918，五个文件已确认存在（6d1b28）。本次覆盖后续模型快照与
environment/deferred_tools递归异常修复，以及新增工作状态组合测试。期间未修改
被测代码或测试，无失败重跑/worker重启。替代79918/15450作为当前回归基线；
下方“近期生产修改后全量待刷新”均为历史状态。回归不代替A–E具体开放项验收，
下一步回到主清单的来源/跨Hook恢复/资源所有权，不继续重复已关闭快照排列。

C2 环境/目录解析递归异常现已实际复现并修复：本机2000层可解码、16000层
触发RecursionError，四例红（30f73d）后仅两个decode_snapshot扩大捕获范围，
沿既有Unknown刷新，不放宽权限或账本。关联447通过（d27ef6，53078退出0），
详见context-snapshot-wire顶部。当前无活动测试；模型快照及本次生产修改均
晚于15450全量，下一步应刷新非CLI回归并回到A–E其它开放项，避免继续只沿
同一类损坏快照排列。CLI对齐仍暂停。

C2 环境/工具目录已有未知状态降级补真实冷启动四例：2000层数组旧结构不阻断
Turn，旧正文保留、新快照一次、未搜索工具不被加载或执行。扩大415通过（c42afc，
98805退出0），无生产修改/活动测试，详见context-snapshot-wire顶部。不要宣称
修复了并未复现的崩溃；也未验证解释器RecursionError边界。普通损坏/未知版本的
当前路径不再笼统列作缺失。仍需按其它section的实际契约收敛，不放宽权限或账本。

C2 模型快照修复后，缺 accepted marker、Model/Custom 基础来源及持久压缩
回放边界已补12例，扩大461通过（bb59f0，6201退出0），详见context-snapshot-wire。
无新生产改动/活动测试。夹具ensure不能覆盖已固定基础的4次失败已解释并修正，
不记作生产修复。下一步转到其它section实际解析/恢复契约或A7/C8明确故障项，
不继续将本模型回退边界列为未知。15450全量仍早于前轮模型快照生产修复。

C2 模型指令旧比较快照损坏的实际缺陷已修复：原16组合15红，现Unknown回退
保留可信旧模型身份和可见正文；同模型不误发切换，跨模型只追加一次新规则。
扩大449通过（195009，11534退出0），见context-snapshot-wire.md顶部。当前无
活动测试；生产已变更，15450全量早于本修复。下一步核对缺少可信旧模型标记与
压缩后恢复是否遵循原有base_model回退，以及剩余section实际解析边界；不把
比较元数据降级扩大到执行账本。CLI继续暂停。

D1 已归并为 working-state-boundaries.md 的四类明确状态。新增真实终端 fork/
冷启动旧ID拒绝及源owner仍可写入两例，关联七文件39通过（d40b4d，59816退出0），
无生产变更/活动测试。工具发现沿既有当前目录重校验及压缩释放证据，不重复新增
同类排列。主清单D1已更新；下一步回到A–E其它开放项，尤其C2快照来源及A7/C8
Hook控制账本的具体未验窗口，不继续将上述四类状态笼统标作未知。CLI仍暂停。

D1 已提交 Code Mode store 的跨Turn/压缩/fork/冷启动组合已直接验证：同Runtime
保留，分支及冷Runtime不从归档重建，源分支隔离，六次脚本与一次摘要无重放。
四文件15通过（b59f68；58439清理后cf8c95退出0），只加测试，无生产变化；
详见code-mode-acceptance.md顶部。当前无活动测试。下一步是外部进程会话与
工具发现状态的跨Thread/冷恢复证据归并，不再笼统列 store 为未知。

D1 计划/压缩/fork/冷启动组合已补直接证据，扩大七个 fork 文件、plan_contract
与 storage/plan_explanation 共 54 passed（b4d9eb，46315 退出 0）。只新增测试，
无生产变更，详见 plan-event-boundary.md；当前无活动测试。下一步核定其它工作
状态的具体所有权：Code Mode store、外部进程会话、发现状态的跨Thread/冷恢复，
先读取既有专项避免重复证明。主清单 D1 已改为具体边界，不继续笼统记录“计划
恢复未知”。CLI 暂停，E4 同步 API 兼容选择仍未确认。

非 CLI 全量回归 **79918 已实际退出 0**（941983）：15450 passed、1 skipped，
532.06 秒；完整累计日志见 full-regression-79918.txt。当前无活动测试，勿再轮询
或重启旧句柄。唯一跳过为 test_project_trust_selection.py:164，当前文件系统
拒绝非 UTF-8 文件名；没有失败、worker 重启或为过关而重跑。
范围为 tests/unit + tests/integration，排除 tests/unit/cli 与 test_cli*.py；
8 workers、loadfile、max-worker-restart=0、addopts=''、-q --tb=short -rs。
机器 12 逻辑核/18 GiB，启动前 memory_pressure 显示空闲 39%，无既有 pytest；
保留资源供测试内部子进程使用。null keyring、当前 sandbox compiler 与五个历史
compiler 的环境和路径沿用上一轮完整回归，启动前确认五个文件存在。
运行期间未修改生产代码或测试。本次覆盖近期 Hook 待写取消终态消费修复及
personality 损坏静默状态/可见正文投影修复，替代 15397 旧全量作为当前回归证据。
全量通过不核销尚未实现或未被测试证明的行为；E4 同步构造兼容选择及 A–E
各项跨模块所有权仍按主清单核验，CLI 对齐暂停。

C2/E6 旧 personality 静默比较状态和可见正文投影的损坏恢复均已有反例修复。
可见记录新增五个红例，现保留旧正文并将不可恢复的比较状态按 Unknown 处理；
不放宽新状态或执行账本。扩大 427 passed（ca36c6，31949 实际退出 0），
详见 personality 专项顶部；已包含在上述 15450 全量通过中。
其他 section 仍需按实际解析边界核验，不能泛化核销。下一步回到 A–E 主清单
及跨模块回归，避免只沿单一 section 追加同类排列测试。

已修 A4/A7/C8/E7 交叉缺陷：旧取消终态待写时，compact Hook 只看 SQLite RUNNING
会把旧 unknown 义务转给新 Turn 导致失败。现 GraphRunContext 携带本 Runtime
持有的待写终态集合，防止跨 Turn 转移，不改变旧账本或冷恢复未知语义。
红例99c77a转绿，扩大514通过（73565d，19136退出0），详见session-start-gap顶部。
已包含在上述 15450 全量通过中。继续审计跨模块所有权，CLI暂停。

自动压缩安装后 RUNNING 冷恢复四例已补：完整 start_hook_recovery 92 通过
（2aedad，79017 退出 0）。marker 已提交而 consumer/脚本尚无事实时，公开恢复
按当前配置选 Hook，摘要/工具不重做，停止决定及原输入身份保留。仅测试修改，
无活动测试，详见 session-start-gap 顶部。下一步取消/终态缺失下跨 Turn 消费
所有权；不重复已验证安装后同 Turn 正常恢复。CLI 仍暂停。

来源登记/marker 安装失败后下一 Turn 的手动路径新增 8 例，五文件扩大154通过
（23f654，98329 退出 0）。仅已安装 marker 触发 Hook，孤立登记不触发，失败
终态不被恢复、摘要不重做，详见 session-start-gap 顶部。无生产修改/活动测试。
下一步结合既有自动安装恢复证据核定剩余 RUNNING/取消交错，不能把本测试泛化成
所有安装故障已完成；CLI 继续暂停。

A4/A7/C8 consumer 绑定前后冷恢复新增 16 例，完整 start_hook_recovery 88 通过
（d835de，46325 退出 0）。绑定不冻结 handler，恢复时按当前配置选择；与已保存
执行计划后撤销授权的拒绝语义分开。摘要/工具/输入无重复；详见 session-start-gap
顶部。仅测试修改，无活动测试。下一步 source 登记/marker 安装及终态/取消交错，
不重复已验的计划/执行/receipt/done 正常冷恢复排列。

回到 A4/A7/C8：压缩来源的计划提交后、执行前冷恢复新增 8 例（manual/auto ×
stop/allow × 保留/撤销配置），完整 start_hook_recovery 72 通过（47e1b6，71551
退出 0），无生产修改，无活动测试。撤销授权不执行旧计划、摘要与工具不重做，
详见 session-start-gap.md 顶部。下一步 consumer 绑定/安装登记及取消交错；
E4 无 owner 的运行中 loop 同步 create 兼容选择仍未确认，不擅自改 API。

非 CLI 全量已刷新：74261 实际退出 0（f57b1a），15397 通过、1 文件系统限制
跳过，539.86 秒，8 workers/loadfile，完整日志 full-regression-74261.txt。
当前无活动测试，不再轮询/重启旧句柄。下一步回到 A–E 当前验收矩阵，核对尚未
证明的跨模块恢复/资源所有权，不继续重复正常 Personality 排列。完整回归通过
不等于核心对齐完成；CLI 仍按用户要求暂停。

隐式启动恢复四种 personality 已直接验证：改变宿主默认后从持久 Thread 解析，
真实冷组装和模拟 HTTP 保持旧选择/provider/model及历史。55 关联通过（822179，
12768 退出 0），无生产/CLI 修改，无活动测试。下一步刷新非 CLI 全量，随后转回
A–E 跨模块故障项；旧 reference 的字段名差异必须落实为具体历史状态行为需求，
不要擅自扩大成 Codex rollout 导入，也不要无证据核销。详见 personality 专项。

Personality 普通 HTTP 与旧列迁移已补证：两接口 × 两 provider 名称覆盖冷启动、
变更、去重、模型切换、手动压缩；四选择旧库迁移保留其它默认与历史。207 扩大
通过（b9600c，84414 退出 0），仅测试新增，详见专项顶部。下一步核定独立旧
reference 格式的实际适用性、隐式配置恢复及跨模块故障项；全量仍需刷新。不要将
显式冷配置测试当成隐式恢复，也不重复已通过的正常风格排列。CLI 保持暂停。

自动压缩重建丢失 personality 跨模型比较结果的问题已修复：新增四种真实 Runtime
手动/自动与同/跨模型组合，先 1 失败/3 通过，最终 460 扩大通过（0aace1，11376
退出 0）。当前无活动测试，详见 personality 专项顶部。下一步普通 HTTP/迁移与
压缩故障交叉验收，之后回到 A–E 其余开放项；全量仍需刷新，CLI 继续暂停。

Personality 旧模型回退及精确快照优先级已修复：442 扩大通过（e6eba7，78529
退出 0）。Unknown 可使用可信 previous_model/旧选择 None，Known 不再被较晚
旧文本标签覆盖；有红转绿及真实冷启动/跨 Turn 测试。独立旧 reference_context
映射、压缩参考生命周期、冷热模型切换/普通 HTTP 与迁移仍开放，不能使用当前
Thread 默认冒充旧选择。详见 personality 专项最新顶部。当前无活动测试，全量
尚未刷新；CLI 暂停，不新增公开 Turn 风格 API。

先纠正计划：Codex公开TurnSettingsUpdate不含personality（protocol.rs:494与
step_activation.rs:273），不要新增此API。已修复既有模型切换串用后改Thread风格
的问题，648扩大通过（fb8c99，6454退出0），详见personality专项顶部。
下一步feature门控、旧标签/reference兼容、跨模型/冷开/压缩/普通wire验证。
当前无活动测试，全量仍是早期基线，不能宣称已刷新。

Personality Thread持久默认/更新和typed片段首批已接入，1474扩大通过、1文件
系统跳过（aff143，94175退出0）。同模型改变追加独立developer片段，不改基础
指令；baked首轮只存状态。下一步按personality专项开放清单补活跃Turn更新、
feature门控、旧标签/reference恢复与模型切换/冷开/压缩/普通HTTP验证。
当前无活动测试，全量仍未刷新，不能把1474通过当作全部personality完成。

Personality宿主选择和Turn冻结已接入，四种选择真实冷恢复不受新宿主none影响，
1318扩大通过/1文件系统跳过（272e70，36851退出0），详见专项顶部。当前无活动
测试，全量未刷新。下一步ThreadModelSettings持久默认/更新API与typed section，
尤其同模型变更不能只重渲染被冻结的session base，必须追加正确的独立片段。

本地模型模板默认渲染已接入真实Runtime及JSON/checkpoint，1284通过、1文件系统
跳过（bd9b51，83182退出0），详见personality-context-gap.md最新实现。本次生产
已变更、全量尚未刷新。下一步贯穿personality的宿主选择、Thread更新、Turn/Step
冻结恢复，再实现独立section；不要停在仅模板类或将此阶段称为完整personality。

Personality上下文缺口已逐链确认，见personality-context-gap.md。执行a6f49e证实
本地完整模型模板被忽略，settings和ModelSettingsSnapshot均无选择字段。下一步
按该文分批接入本地模板→冻结选择→typed section→普通wire/恢复；不得仅加一段
提示词冒充baked/同模型变化/跨模型去重。context-snapshot-wire顶部原managed
“未实现”陈述已纠正。本轮仅审计/入口诊断，无生产修改，无新测试批次。

多条终态待写的真实进程退出已补验：子进程三Turn/两pending后os._exit(73)，父
进程禁止采样恢复两结果，原历史不变、无RUNNING。51关联通过d1f116，91198退出0。
详见partial-output顶部。本轮仅测试，无活动批次。下一步转C1/C2明确缺失的typed
section，优先context-snapshot-wire.md顶部personality，不在终态正常排列上无限扩展；
压缩/Hook故障交错仍归C8，不因本测试完成而泛化核销。

新Turn准入的可恢复旧终态阻断已按原生继续调度语义修复，双Turn红例转绿，
扩大1094通过（4b3056，52863退出0，61.96秒）；详见回归与partial-output顶部。
不把旧结果待写当成新任务失败，不跳过新任务提交；恢复/关闭仍要求排空。
下一步核验多条待写跨进程丢失、压缩或Hook恢复交错，并回到A–E清单其余模块，
不重复只跑相同绿色排列。48797全量是最近两轮生产修复前基线，当前无活动测试。

新增取消终态ack确认反例已修复：旧分支跳过CANCELLED读回导致清理误报；现在
精确确认后保留取消与Warning。442扩大通过，85612实际退出0（07426a），
详见partial-output-acceptance.md及回归顶部。48797全量是此生产修复前基线。
仍须收敛持续待写下新Turn准入映射；原生tasks/mod.rs:800–865在flush失败后
仍maybe_start_turn_for_pending_work，不能直接把Corki更严格屏障称为已对齐。

非CLI全量48797已实际退出0（b482fd）：15328通过、1文件系统限制跳过，
526.57秒，8workers/loadfile。完整输出full-regression-48797.txt，环境及范围见
core-regression-current.md新顶部。勿再轮询或重启旧句柄；下方“活动/待全量”均为
历史记录。下一步回到A–E验收，先核对持续待写下准入与原生继续执行语义；
完整回归通过不证明所有对齐差异已关闭，CLI仍暂停。

终态告警扩大回归现1092通过（6fb5ed，68161退出0），8文件先137通过020468。
首次扩大34失败1058通过095a3d已保留日志warning-regression-74206.txt；修正故障
注入同步拦截即时retry，保留原冷恢复与副作用断言。详见core-regression-current.md。
下一步应刷新非CLI全量，勿重复跑同一绿色定向组；当前无活动测试。仍不宣称
A–E完成，取消/宿主终态所有交错及其他模块清单须继续按证据核销。

终态告警语义首批已实现：已准入且原写入为可恢复存储错误、record已被拥有时，
发终态前调用有限排空。重试成功则确认；仍为可恢复错误则保留队列，Warning先于
原任务终态，不将原成功/认证错误改成storage failure；不清掉其他cleanup_error。
冲突、未准入、不支持受保护重试或非可恢复错误仍不适用此路径。取消不被catch成
普通异常。原生tasks/mod.rs:373–389重核；不声称所有终态/关闭交错已完成。
新增terminal_storage_warning四红例8d2df3已转绿；连原确认与存储22通过35efc3，
56648实际退出0 bc2ee7。旧pending/admission 22失败/14通过f59b5a：原测试假定
首次storage failure且尚未即时排空，已让故障持续覆盖首次排空，并按新契约检查
原任务结果，保留后续冲突/取消/关闭/幂等断言；相关三文件40通过12f86d（5.36秒）。
下一步优先扩大恢复/关闭回归，旧崩溃fixture可能还仅拦save_turn而未拦即时retry，
需逐处核对不能只清队列（首次补写现在更早）。全量必须再次刷新；不称当前全绿。

全量64868已退出1（52c421）：15303通过/21失败/1skip，完整日志已保存。
21项均是另五份崩溃fixture正常close补写造成冷恢复为空，逐处修正内存丢失模拟，
五文件87通过（db2a32，3455退出0），原恢复和副作用断言未弱化。详见回归新顶部。
目前无活动测试；全量不能称绿。继续终态告警语义，最后刷新完整回归，不再轮询64868。

读回失败所有权已修复：终态失败且确认/状态读取不可用时，已成功准入或已验证
resume的Runtime保留原record，后续CAS验证同一身份/结果；初始准入也失败则
不凭空建待写。只确认读取失败但状态已为终态时也保留精确payload供验证，
已明确读出冲突（confirm false）的旧分支不被改称确认成功。
三红例0ac83a；扩成8组合（RUNNING/同结果/冲突/未准入×状态读取成败），
关联44通过6dddc6；核心存储/终态/关闭/取消437通过1c24f8（10.33秒，87594退出0，
4workers/loadfile），静态d3aa8c。下一步先刷新非CLI全量，再继续宿主终态/告警映射。

有限重试已接入共用待写屏障：每条记录首次OSError或带明确BUSY/LOCKED/IOERR/
FULL/CANTOPEN主码的SQLite OperationalError失败，再用新事务尝试一次；第二次
失败保留队列并传播，不自动循环。SQL语法/未知错误码、身份/结果完整性错误及
取消不重试。原生recorder.rs:1730–1760的每屏障两次I/O尝试已重新核对。
新10组合（提交前/后×I/O、BUSY_SNAPSHOT、完整性、SQL、取消）先4红6绿b9c05c，
实现后定向46通过3e6f8c；持续失败关闭例断言恰2次。核心/存储/终态/取消关闭429
通过（75acb2，10.45秒，37047退出0，4workers/loadfile）；Ruff通过c58f78。
下一步仍为写入后读回失败时的待写所有权和宿主终态/告警语义，随后全量刷新。
本机制仅重试受身份保护的终态事务，不用于修饰未定位的记忆database is locked。

待写排空已接入新Turn/恢复入口并共用关闭逻辑。真实写线程取消交错两红转绿，
不产生新输入/采样，取消后提交但未确认的队列可再次幂等排空。扩大1070通过
（586982，67405退出0）。首次34失败为三份崩溃fixture的正常close现在补写结果，
已显式模拟内存队列丢失并保留原冷恢复断言；详见core-regression-current.md。
下一步读回失败下待写所有权、每屏障有限重试及宿主告警语义；不能将当前storage
failure+准入阻断直接视为原生归档warning行为一致。仍需刷新全量，SQL锁风险保留。

终态待写关闭屏障已首批接入：未确认写入失败且独立读到RUNNING时，Runtime保留
TurnRecord；_close_storage_resources在SessionEnd/存储关闭前用原子retry_turn_terminal
补写。失败保留队列、存储及Thread租约，再次aclose只重试存储阶段，不重关执行资源。
缺失/身份不符/冲突终态拒绝覆盖；相同终态幂等，保留准入base/model字段。
新存储红例2（4f7d68），接入后旧冷恢复2例因正常关闭已补写而失败（fd641c）；
冷恢复fixture显式模拟内存队列丢失，另增真实正常关闭失败/恢复/冲突两例。
定向34通过（e2a57a），存储+核心+关闭/取消/恢复417通过（e30ef3，8.94秒，
4workers/loadfile，65731退出0）；Ruff/5文件格式通过（90c898）。
未完成：正常运行/新Turn/恢复前排空时机、读回失败无法确定所有权时的队列契约、
每屏障两次尝试及取消交错；当前仍返回原storage failure，不声称原生warning等价。
下轮优先补这些同一核心链路边界，再刷新全量。数据库锁未确认风险继续保留。

SQL诊断已加到shutdown_deadline失败断言，定向11及同范围记忆837均通过
（f18916/6c6008，进程已退出0）。原database is locked未复现、未判定根因、
未称修复；保留风险和后续具体SQL诊断，不持续无差异重跑。下一实现回到已确认
的终态待写所有权/关闭重试屏障，不能盲目覆盖冲突终态；全量验收仍需后续刷新。

最新记忆扩大回归836通过/1失败（277717，89191已退出1）。旧流关闭测试已增加
慢准备反例并隔离计时，14通过；但shutdown_deadline[close-cancelled]主Turn在关闭
之前报database is locked。下一步追踪此实际并发存储失败，详见回归记录新顶部。
本轮只修改测试，未修改生产；不把扩大回归称为全绿，终态待写实现仍顺延。

最新非CLI全量79636退出1：15298通过、1记忆所有权测试超时、1文件系统skip。
原文件串行13通过，目标例0.61秒，但未判定根因或修复，不能称全量通过。
先定位test_memory_completed_is_terminal_and_closes_stream的1秒总预算在哪个阶段
耗尽；不得直接放宽断言。详见core-regression-current.md和full-regression-79636.txt。
终态待写所有权实现排在这个失败诊断之后；当前无活动测试句柄。

未提交终态的冷恢复两例实证：原成功/认证失败，首调用storage failure，冷开禁止
再采样仍还原原结果，关联184通过（b993d9）。这不是原生后台待写队列，差异仍开。
原生实际路径codex-rs/rollout/src/recorder.rs的队列/两次尝试/成功前缀drain已重读。
下一步运行期待写所有者及关闭重试屏障，不凭确认false盲目覆盖可能冲突的终态；
详细证据见partial-output-acceptance.md最新段。本轮无生产修改，未刷新全量。

终态读回实际worker与关闭交错已补验：八组合含成功/原认证失败、观察者取消、
Runtime关闭；确认先于模型/存储关闭，重复关闭不重清理。扩大411通过（fd6940），
本轮无生产修改，详见partial-output-acceptance.md。下一步提交前失败的待写所有权
与原生归档告警契约；不要再把公共关闭等待确认列为未验证，后续全量仍需刷新。

终态确认本轮补验原认证失败与finishing后观察者取消：四组合通过，原错误/kind
保留、一次终态、告警顺序正确、取消传播；关联关闭/取消/核心与存储407通过
（9543ec）。本轮无生产修改，详见partial-output-acceptance.md。后续重点仍为
确认读回本身取消/资源所有权、未提交待写策略及其他上下文section，非CLI全量待刷新。

终态写后确认丢失开始修复：真实提交的一红例已转绿，独立读回精确payload才保留
原终态并Warning；未提交/读失败/冲突仍storage failure。存储+恢复825通过，
观察者实际投递文件12通过；详见partial-output-acceptance.md最新段。下一步原FAILED
错误保留、读回取消/清理与未提交待写策略；本轮生产修改后全量尚未刷新。

无关贡献者故障现已两红例实证并修复：纯宿主政策片段共用，恢复不调用普通贡献者。
相关442通过，非CLI全单元+政策/wire/记忆上下文5932通过/1文件系统skip，54942
最终退出0（3c34f5），静态通过。详见managed-developer-instructions-gap.md和
core-regression-current.md；先前15281全量属于该改动前。下一步其他section压缩
保留与A4/E6/E7终态协调，不重复把贡献者故障当作尚未验证假设。

最新全量27039已退出0：15281 passed/1文件系统skip（755204，523.52秒），完整
日志full-regression-27039.txt。不要再按下方活动记录轮询27039，也不再把90904的
五失败当作当前未修复状态。下一步先验证恢复完整builder的无关贡献者失败假设，
随后其他section压缩保留及终态协调；全量通过不证明A–E所有需求完成。

当前全量session27039（单元+集成同批8workers）已实际轮询到8%（dd2b78），
无终态、未见失败。先继续同句柄，详见core-regression-current.md，不修改被测代码。
只读源码7d5b2a确认恢复build仍调用所有普通/异步/Step贡献者，之后才判断政策
变化或丢弃非政策快照。下一步故障注入确认已冻结请求是否会被无关贡献者的新
失败阻断；如果证实，政策恢复应读取宿主已验证政策片段而非触发全套贡献者。
这是待验证假设，不据只读推断宣称新增故障测试已执行；不把读取官方服务作为方案。

政策×日期/catalog组合已补验：日期2、搜索18通过；扩大恢复文件群与完整context
1089通过（b2cd88），本轮无生产改动，静态通过。细节见core-regression-current.md。
下一步刷新全量，并核对恢复完整builder的非政策读取/失败及其他section压缩保留，
不要反复把日期/catalog组列成未执行。90904仍是旧失败全量，不能改称当前全绿。

恢复五项回归已在原四文件修复验证，新增政策×Shell变化两红例也修复，当前52通过
（724cd8）；生产刷新门槛及冻结section/工具结果投影见core-regression-current.md。
下一步政策×日期/catalog变化的组合与其他section压缩恢复，扩大恢复测试后刷新
全量；原90904五失败记录保留，不冒称当前全量已绿。终态协调继续后移。

最新全量90904终态5 failed/9353 passed（461d1d），完整输出见
integration-regression-90904.txt。首要任务改为修复恢复刷新破坏冻结环境/Shell和
工具搜索结果的五项回归；详见core-regression-current.md最新段。90904已退出1，
不再跟进活动句柄。终态提交协调退到这批回归之后，不以单元全绿忽略集成失败。

恢复新增政策预算的两个分支均已直接验证：不可容纳则CONTEXT_WINDOW失败，
可容纳则压缩一次、正常采样完成，政策和当前输入各一次；原始历史前缀不变。
详细证据见managed-developer-instructions-gap.md，不再重复把这两项列为待补。
最新非CLI单元5911通过/1文件系统跳过，连同恢复14例共5925通过（3766fb）。
当前集成全量session90904已启动且真实轮询至6%（eadaf6），仍活动，使用8workers。
先跟进同句柄至终态，不重启、不修改被测代码；输出存储见core-regression-current.md。

下一实现优先回到A4/E6/E7业务终态持久化故障与原生归档告警的语义映射：
现有测试已经区分提交前失败、真实提交后抛错、恢复不重采样；不要再重复同样
fixture排列，也不能直接把执行账本失败降为归档warning。先明确事实提交、
恢复索引与宿主终态三者的契约，再决定是否需要实现变更。
A7/C8其他world-state贡献者与Hook反馈的恢复交错仍开放；E4同步构造资源所有权
待既有API决策，不擅自引入breaking拒绝。CLI继续暂停，A–E整体尚未完成。

最新只增验证：旧失败事实已提交但checkpoint滞后时，恢复推进attempt仍刷新新政策，
包含刷新提交后再崩溃的四组合。专项12及相关56通过（6ad32c），静态通过。
下一步新增政策导致上下文预算溢出/压缩的直接验证，再刷新恢复改动后的全量；
不再把失败采样保留刷新标记列为完全未验证。详见宿主策略专项最新段。

采样前政策恢复两红例已修复：使用已绑定Step视图重新准备世界状态/预算，不重跑
输入Hook或重绑工具。关联432及恢复群677通过；提交后再次崩溃8通过；最终失败
采样刷新标记细化后关联52通过，格式修正后静态通过。详见宿主策略专项最新段。
下一步新增政策造成预算溢出/压缩、已有失败采样重试组合及贡献者恢复边界；全量
尚未刷新，不沿用旧15252作为此次生产版本证明，也不再重复报告两红例未修复。

当前首要真实红例：test_managed_policy_recovery.py有2 failed/2 passed（186bd4），
采样前checkpoint冷恢复直接用旧request_items，遗漏新宿主政策/撤销；工具后恢复
已有prepare所以正确。下一批修复恢复请求刷新及持久幂等/预算，不重跑副作用。
详见managed-developer-instructions-gap.md最新段；这两红例未修复，不沿用旧全量绿色。

最新只增验证：宿主政策在工具后中途压缩的摘要/后续请求中完整保留，冷开新Turn
不重复工具，相关434通过（1879cf）。下一步RUNNING冷恢复时政策变化与旧checkpoint
的契约，区分固定基础指令和可更新宿主策略；不要把已完成Turn冷开当作故障恢复。

最新生产增量：Guardian策略省略误发撤销的一红例已修复，普通缺省撤销对照保留。
宿主ContextSnapshot以omitted_sections传递不参与比较的section，不删除旧历史，
关联400 passed（e2ad91，7.10秒，退出0），Ruff通过。详见宿主策略专项最新段。
下方15252通过/1跳过的全量是此次变更前证据；不重启旧30649，后续按需刷新。
下一步中途工具压缩/恢复交错，随后回到A–E开放清单，不继续扩张Guardian产品范围。

## 当前全量终态：集成9341通过，单元5911通过/1跳过

session30649已退出0（3db7ca），9341 passed、506.75秒，无失败或跳过，完整输出
integration-regression-30649.txt。结合非CLI unit5911通过/1文件系统跳过（c4e45a），
合计15252通过、1跳过；两批8workers/loadfile/禁自动worker重启，运行期间未改
生产或测试。此前各处“活动/待刷新/未终态”现在是历史记录，不再轮询或重启30649。
当前全量覆盖近期模型来源与宿主developer策略改动，不证明A–E所有缺口已关闭。
下一步恢复Guardian section省略与普通政策撤销的差异反例，沿宿主策略专项继续。

当前先跟进活动集成session30649至终态，5%（afb645），不重复启动或修改被测代码。
全单元5911通过/1文件系统跳过已终态（c4e45a）。存储句柄/输出见core-regression-current.md。
之后补Guardian“省略section”与普通缺省“撤销政策”区分反例：原生仅遍历当前section，
Corki通用消失key撤销可能混淆，见managed-developer-instructions-gap.md最新源码证据。

最新宿主策略验证：两普通HTTP真实适配器冷开注入/替换/缺省撤销通过，实际记忆
worker四种skills场景每次请求保留唯一策略且最终发布通过，联合18通过（c89a77），
静态通过。本轮无生产修改。下一步Guardian已有历史语义/中途恢复与全量刷新，
不再重复把HTTP或记忆worker传递列为完全缺失，细节见宿主策略专项。

最新宿主策略增量：旧developer政策四红例修复，Unknown协调及缺省撤销只追加一次；
自动/手动压缩的typed请求重建两例通过，无需额外压缩生产修改。关联450 passed
（d9b27a，7.82秒，退出0），4workers，静态/格式通过。详见managed-developer-instructions-gap.md。
全量仍待刷新；下一步内部已有历史/记忆worker传播及普通HTTP wire，A–E未闭合。

宿主developer策略首批已接真实Runtime：来源合并/预算/typed增量/Guardian初始隔离，
关联414测试通过，import排序修正后静态通过。下一步legacy Unknown、压缩重建、
缺省撤销和内部已有历史隔离，不再把当前入口描述为完全缺失；详见专项最新段。

下一项明确生产缺口：宿主 additional_developer_instructions 未接通，真实宿主层
解析抛 Unsupported non-MCP managed requirement（5b427d）。原生来源/校验/Step
注入/专属替换撤销链已追踪，见managed-developer-instructions-gap.md。先核对多层
来源与内部会话隔离，再接真实Runtime及冷恢复反例；不把它误列官方服务排除项。

最新生产增量：基础模型来源回退补上“基础正文不同于当前模型指令”门槛，
与原生 session/world_state.rs 一致；没有已接受Turn且同正文时不误发切换，
真实上一模型不同仍发。四组合一红（508a16）转绿，关联462 passed（618b64，
8.21秒，退出0），4workers/loadfile，Ruff/格式通过；细节见model-instructions-gap.md。
本轮与旧标记修复后的全量尚未刷新。A–E仍未闭合，下一批应回到开放清单的
上下文 typed sections/恢复交错与终态故障语义，不继续扩张同正文配置排列。


## 最新增量：旧模型标记恢复已验证（全量结果为此次变更前）

Codex 基准仍为 ddf04ad26789d040f9ef6a96736f76602e35a6cc；
core/src/session/rollout_reconstruction.rs 的 TurnContext 独立恢复 previous_turn_settings，
context/world_state/model.rs 在 section 缺失时根据 previous_model 判断切换。
Corki changed_context_items 现复用 harness.previous_model 的既有解析，读取可信内部
快照中的上一模型；该身份跨压缩保留，不新增持久化记录、不重写旧历史。
旧无 model.instructions 的冷恢复 × 压缩/未压缩 × 继续 small/切回 large 四例，
此前四失败（1162c8），现关联 452 passed（2c46ea，7.57秒；d2966a 确认退出0）。
原 session30239 已不存在且无活动 pytest；未将丢失结果视为通过，本次重新验证。
追加 accepted/user/input_attached/visible/malformed/other_key 六项来源边界，
只有可信内部快照可触发切换；专项共50 passed（2df2c0，7.59秒，退出0），Ruff及
三文件格式检查通过。两批4workers/loadfile/禁止worker自动重启，小范围不启96并发；
本轮测试执行期间未修改生产或测试。无官方服务调用，也未推进CLI对齐。

下方15214通过/1跳过属于此次生产增量之前的全量证据，不代表当前全量已刷新。
下一步仍需核对完全缺少可信上一模型信息的旧历史边界，并回到A–E开放清单；
本组只证明已有旧标记的恢复，不把未知历史猜测成可恢复，也不宣称整体对齐完成。


当前全量已刷新：非CLI unit5894通过/1文件系统跳过，integration9320全部通过
（587b02，session55650终态退出0）。不重启该批；下一步返回A–E开放清单，优先
旧无model section前态及历史兼容边界，不把全量绿色作为未实现能力的完成证据。

账本/checkpoint基础冲突校验两红例修复，旧字段None兼容通过；关联254通过。
非CLI全部unit已刷新5894通过/1文件系统跳过（ef29c0）。接下来刷新非CLI集成全量，
并继续旧无model section前态与A–E开放项，勿重复运行已终态33006。

首次checkpoint前基础已接账本：普通/手动摘要四红例修复，474/182两组通过，
专项八通过。下一步旧缺字段兼容和账本/checkpoint冲突校验、旧无模型section前态，
随后刷新全量；不要再把已修复首次prepare窗口列为完全缺失。

已准备Turn冷恢复前缀一红例修复：工具后prepare沿用checkpoint固定基础，未来
Turn使用新宿主配置；扩大468通过，空串/非空专项四通过。下一步首prepare前
账本边界及旧无section前态，随后必须刷新含本轮生产变更的全量。

旧NULL来源的精确文本推断一红例已修复，442项通过；Custom不因文本相同改来源，
原基础行不变。下一步待恢复Turn覆盖交错，以及无基础行/旧Turn缺section的独立
兼容问题；不要把本组NULL来源等同于所有旧历史迁移。

文件入口现已接通：按配置来源解析、内容优先级、读取失败及快照固定六红例转绿，
扩大687通过/1文件系统跳过。接下来优先旧未知来源与待恢复Turn覆盖交错，之后
刷新包含新生产变更的全量；不重复已验证的文件正常排列。

显式宿主覆盖已实现：instructions/base_instructions、Custom 来源、新建与恢复
区别、fork 继承/覆盖四红例转绿，扩大1109通过/1跳过。下一步旧来源与文件入口、
待恢复Turn覆盖边界及本轮生产后的全量刷新；下方“当前首要四失败”为修复前记录。

当前首要：显式 instructions TOML 覆盖四个真实请求反例均失败，未修复；
下一批须同时接通配置/初始化优先级/Custom 来源/持久恢复，不仅改请求正文。
详细原生源码依据、旧来源兼容与验收见 model-instructions-gap.md 最新章节。

自动压缩后模型片段两红例已修复；工具后轮内压缩的身份/调用结果/当前输入正常
链路两例补验，扩大452通过。优先转向显式宿主基础覆盖与旧无来源历史，不继续
穷举同一正常路径；取消恢复交错仍按完整A–E清单保持开放。

模型指令最新：基础持久化、fork、初始化故障/取消已有实现或证据；连续手动压缩
后模型比较的四红例已修复，448 项定向通过。下一步聚焦显式宿主覆盖、旧无来源
历史以及自动压缩/Step 前态，而非重复基础正常排列；详见 model-instructions-gap.md。

当前C1/C2新增明确缺口：model-instructions-gap.md。已追踪Codex会话基础指令
优先级→来源→模型切换独立片段，与Corki静默model/comp_hash快照和降窗摘要不同。
后续按完整初始化/恢复/Step快照链实施通用本地模型指令，不能只补标签，也不接
官方目录。guidance及协作模式（含旧Message）已有修复和专项证据，先避免反复
扩展同一局部正常排列。A–E其他开放项继续保持，CLI暂停。

转录发布告警出口已修复：SQLite 可选只读 pending 查询，Runtime final_events
在原终态前通知一条当前线程副本未更新警告，不阻塞队列、不重放模型/工具。
原缺失事件反例转绿，关联 66 通过（70828a）；详情见 partial-output-acceptance.md。
本批有生产变更，当前全量需刷新；业务账本故障和默认/按需转录仍分别核对，CLI 暂停。

归档待写所有权已确认存在：transcript_pending 随业务事务持久化，副本发布失败
仅日志告警、下次写入补齐。新增真实 Runtime 工具链在副本持续失败时仍完成，
关闭后冷存储补写且副作用不重放，联合 14 通过（55a1d9）。见
partial-output-acceptance.md，勿再按“没有归档重试”重复建队列；下一步核对按需
转录与原生默认 rollout 的触发/告警出口，独立处理业务终态政策。CLI 暂停。

模型提交/消息事件顺序四故障窗口已补验：真实 partial/whole commit 前后异常均
无虚假完成事件，数据库保留实际提交事实，关联 26 通过（c125b2）。仅测试。
原生 writer 警告配套 pending_items 保留和重新打开重试已追踪，详见
partial-output-acceptance.md。下一步核对 Corki 归档层与执行账本是否各有等价
待写所有权，不直接将关键 SQLite 写入异常改 warning；本差异仍开放，CLI 暂停。

E6 终态写入前/后异常补验：原 owner 均报告 storage failure；前者恢复已完成模型
记录、后者无待恢复 Turn，两者不再采样，关联 62 通过（207266）。详见
partial-output-acceptance.md。新确认原生 rollout flush 错误仅警告，与 Corki
业务 save_turn 失败的分类并非字面一致；不可凭“更保守”关闭差异，也不贸然
删除恢复索引错误。下一步核对模型项提交/事件顺序并收敛此差异，CLI 暂停。

近期 fork/managed Hook 后全量已取得终态：非 CLI unit 5878+1 文件系统跳过，
integration 9222 通过/1 失败（23c187）。失败为不匹配插件身份用固定 120 ms
判断采样发生的夹具时序，原例串行通过；改为 MCP 未释放前等待实际 Turn 完成，
关联 61 通过（3e4474），仅测试修改。完整证据见 core-regression-current.md；
原全量不称全绿，不再轮询 76602。下一步继续 A–E 剩余项，CLI 暂停。

managed Hook 输入结构及普通 user/project 伪造宿主来源已有直接证据：19 单元
反例与 2 个真实构造/刷新/Turn 场景，四文件 64 通过（21aa41）。本批仅测试；
不再重复扩展正常来源矩阵，下一步回到 acceptance-index 的 A–E 剩余实质缺口，
尤其未关闭的构造资源所有权及当前全量验收。CLI 暂停，整体仍未完成。

必需 Hook 两类非法 handler 的真实 graph/acreate 回滚已补 8 场景，覆盖持久/
临时存储与自建/借用模型；关联 43 通过（95fd94），仅测试，详情及首次夹具
错误见 managed-hook-gap.md。下一步宿主策略结构/普通配置伪造来源验证，再回到
A–E 验收清单收敛；运行中同步 create 回滚与当前全量仍未关闭。CLI 暂停。

managed Hook 的项目/实际插件来源对照已补：已授权普通来源在正常模式保留、
only-managed 屏蔽、总开关关闭全部不执行，刷新后同样成立；16 专项，关联
40 通过（2739ca），仅测试。下一步宿主策略非法输入/伪造来源及构造回滚，
不再重复这三个正常来源组合。见 managed-hook-gap.md；A–E 未完成，CLI 暂停。

Hook 关闭开关时的在途异步正常收尾已补真实进程证据：不替换 owner/任务、不
启动新 Hook，跨 Turn/手动压缩后唯一原执行可读取新转录并提交结果。4 专项、
关联 33 通过（caef5a），只改测试。下一步项目/插件来源及策略非法输入/回滚，
不再把正常收尾列为未验证；见 managed-hook-gap.md 当前节。整体未完成，CLI 暂停。

Hook 删除配置键后热刷新沿用 false 的实际差异已修复：独立缺省值不再被生效值
覆盖。两反例先红后绿，关联 31 通过（6cb921），静态通过。详见
managed-hook-gap.md 当前节。下一步在途异步与项目/插件来源、策略非法输入，
不重复扩大已修复删键窗口。最新全量仍早于本次生产修改，A–E 未完成，CLI 暂停。

Hook 文件解析与运行中显式 true/false/true 已补验，非法值刷新不发布。关联首次
29+1 暴露并发 runner 顺序夹具假设，按源码修正为快照顺序+调用集合/次数，
最终 30 通过（df1bcd），静态通过；详情见 managed-hook-gap.md 当前节。
下一步删除开关键默认恢复/在途异步及项目插件来源，不重复这三段显式切换。
本批仅测试，A–E 未完成，CLI 暂停。

Hook 总开关已按 Codex Stable/default true 的 features.hooks 接入，初始/刷新
快照在关闭时不发现普通或宿主项，也不因 handler 加载错误拒绝启动。8 反例
先红后绿，关联 84 通过（6c5852），静态通过。见 managed-hook-gap.md 当前节。
下一步运行中开关转换/配置解析与在途义务收尾，及项目/插件来源和 malformed
策略校验；不能将已有总开关再列作缺失。整体未完成、CLI 暂停，最新全量待刷新。

managed Hook 基本链已实现：宿主 ManagedHookPolicy → CorkiSettings → graph/配置
刷新 prepare → 来源过滤与必需错误分类；原 user/project 授权不变，SessionEnd
MCP 仍拒绝。4 专项通过，六文件联合 72 通过/12.68 秒/退出 0（0d002f）。
首次关联命令路径错误退出 5 已如实留档，不称通过。见 managed-hook-gap.md
当前实现节。下一步总开关来源语义及项目/插件、malformed/回滚边界；整体未完成，
最新全量早于此次修改，CLI 继续暂停。

managed Hook 已新增两个真实 Runtime 反例，当前均失败于策略模块缺失
（dc7f83，80548 已退出 1），不是生产实现完成。下一步增加宿主策略值及
初始 graph.prepare / Runtime 配置重载 prepare 的统一传递，并实现来源
选择与 required 错误分类。详见 managed-hook-gap.md 当前节。当前新增测试
未通过，不能引用先前回归称最新全绿；CLI 仍暂停，整体目标保持开放。

managed Hook 差异已由源码确认，不是官方服务：系统 requirements 的必需 Hook
与 only-managed 来源策略，Corki 当前没有宿主入口。完整链与下一批契约见
managed-hook-gap.md。特别注意 Codex 功能关闭会清除加载错误，reconfigured
也不等同 new 的拒绝门；先追踪这些条件，不擅自扩大策略。下一步真实 Runtime
反例与宿主策略接入，不能将本次审计称作已修复。CLI 暂停，A–E 继续开放。

SessionEnd 账本提交前/后六窗口已补验，6 专项和关联 32 通过（d6c697、04dc67），
原终态不改、关闭错误不吞、已知/未知结果不重放、资源只关闭一次；详见
session-end-gap.md 当前节。本批仅测试。下一步核对 managed Hook 加载拒绝及
当前配置来源链，不重复扩大这六个已明确窗口。A–E 未完成，CLI 暂停。

A4/E7 SessionEnd 反序完成/局部 OSError/转录失败及关闭顺序补验完成：2 专项、
关联 26 通过（ec38c1、c0ce90）。仅测试，见 session-end-gap.md 当前节。
下一步围绕该关闭生命周期的账本提交故障及 managed 加载失败收敛，而非重复
扩充已通过反序/转录窗口；E4 同步构造兼容选择仍开放，CLI 暂停。

跨存储 fork 取消/源不可用/冷开补验已完成，8 专项通过、关联 195 通过
（83d1d8、6af130），仅修改测试，详见 thread-initial-history-gap.md 当前节。
取消等待真实 reader 收尾、无半目标/无采样、可重试、源关闭后独立冷恢复及新输入
均有行为证据。不再把这些明确窗口当作缺失，下一步回到 acceptance-index 的
A–E 剩余条目，尤其生命周期归并和上下文 typed section 证据收敛。
E4 同步 create 在已有 loop 中的兼容选择仍未决；旧全量早于跨存储生产改动，
不能冒称当前全量全绿。CLI 继续暂停，完整目标仍开放。

跨存储 fork 基本实现已接入：Runtime.create/acreate/构造器接受借用的
fork_source_repository；SessionRepository.load_fork_snapshot 返回独立历史事实，
SQLite 在单一读事务取 items/turns/usage，目标复用原子 publish_fork 的映射、
截断与回执逻辑。同库默认仍在一个写事务内读取并发布；外部源不被目标关闭。
首次成功读取后保留快照用于发布失败重试，初始化成功后释放借用引用和快照。
临时目标仍仅使用私有 RAM 库。未复制 model_steps、工具/Hook 执行义务。
两入口反例转绿（848df6）；扩展提交前/后故障时夹具曾错误假设临时目标有 writer，
4 通过/2 夹具失败（742018，最终退出 1：4c21e4），按真实私有存储边界修正后，
所有 fork 集成与 unit/storage 联合 182 通过、8.74 秒，静态/格式通过（9a1542）。
此前全量 15036 通过早于此次生产变更，不能算最新全量；下一步验证跨存储读取
取消/源不可用及冷恢复边界，再回到 A–E 清单。CLI 暂停，整体未完成。

已进入下一批：新增 test_thread_fork_external.py 两个真实 Runtime 反例，目标为
独立持久库/私有临时库，覆盖借用源所有权、源不变、无执行账本复制、usage
与输入传承、关闭目标后源继续运行及临时目标无磁盘状态。串行实跑两例均失败
于公开 create 缺少 fork_source_repository 参数（f5e712，session 33634 退出 1）；
后续行为断言尚未执行，不能算已验证。下一步实现借用源的一致性快照与目标原子
发布，保持同库事务/回执重试语义，不继续新增同样入口缺失的反例。
先前 9177 集成全绿是新增此文件之前的基线，不代表当前新增测试已通过。

最新回归终态：session 25320 已退出 0，9177 集成通过、500.19 秒（7396ad），
无 skip；加单元 5859 通过/1 文件系统 skip，共 15036 通过。原始输出见
integration-regression-25320.txt。不要再轮询或重开该组；下方运行中状态为历史。
当前可开始跨存储 fork 的 Runtime 反例与实现，整体 A–E 未完成，CLI 暂停。

跨存储 fork 只读复核（当前集成组运行期间，不修改生产/测试）：Codex
`thread_manager.rs:1304` 的 `fork_thread_from_history` 接受独立已加载历史，
经 `fork_thread_with_initial_history`、快照转换、`spawn_thread` 启动；
`session/session.rs:854` 在 ephemeral 时不建立持久线程，`:941` 不绑定状态库，
而 `session/mod.rs:1467` 仍重建 Forked 历史和 usage。因此持久来源与临时目标
解耦属于核心历史生命周期，不是官方服务能力。对应已有 Codex 测试
`core/tests/suite/fork_thread.rs:174` 验证无源 rollout 路径的历史分叉，但并未
直接覆盖 ephemeral 组合；不得声称已跑过该组合。
Corki `Runtime.create` 为 ephemeral 新建私有 VolatileSessionRepository，
`storage/forks.py::publish_fork` 又只在目标连接查询 source，故无法继承另一个
库的历史。下一批应先建立真实 Runtime 的持久源→临时目标失败反例，再拆分
一致性快照读取与目标原子发布；保留身份映射/usage/截断规则，不复制执行账本，
不把源 repository 的关闭所有权转给目标，不为临时目标产生磁盘状态。
这仍是待实现项，不因同库 fork 已通过而关闭；先等待 session 25320 终态。

最新全非 CLI unit 已刷新：5859 通过/1 文件系统 skip，退出 0（fdfd10）；全静态
1264 文件通过（c1c715）。全非 CLI integration 正在 session 25320 运行，8 worker/
loadfile/禁重启，配置当前及五份历史编译器。下一轮先跟进该句柄终态，不重复开组，
运行期间不改生产/测试。见 core-regression-current.md 当前节；A–E 未宣称完成，
跨仓库边界/E4 兼容选择等仍待收敛，CLI 仍暂停。

legacy fork fallback 已修复：无显式 Turn 的末用户后无终态时补中断或越界截断，
不伪造 Turn 行；已中断分支再次分叉保留单标记。两反例转绿，联合 176 通过
（5da0ac），见 thread-initial-history-gap.md 当前节。下一步核对跨仓库边界及
回到整体 A–E 剩余清单，不继续把该 legacy 两窗口当成未实现；最新全量回归仍
早于近期生产变化，CLI 继续暂停，E4 构造兼容选择仍未决。

fork 记忆独立 claim/owner/版本与同版本不重复领取已补验：父租约保留、子独立领取、
父 token 无权完成子任务，子实际新输入才重提取，源不变；联合 48 通过（bca7f6），
仅测试，见 thread-initial-history-gap.md 当前节。不再重复扩充该已明确窗口。
下一步回到 A–E 差异收敛：fork 仍需 legacy 无显式 Turn 与跨仓库边界，E4 同步
构造兼容选择未决，最新全量核心回归尚待刷新；CLI 继续暂停。

fork 计划历史/新事件/冷开不重放已补验，六文件 39 通过（100b30），仅测试。
记忆待办纠偏：Codex copied rollout 提取未按 fork 前缀剔除，stage1 按线程/source
版本 claim；不能自创跨线程前缀去重。下一步验证分支独立 claim/owner/version 与
同分支不重复提取，不复制父记忆任务。详见 thread-initial-history-gap.md 当前节。
全 A–E 及全量回归仍开放，CLI 暂停；旧“继承前缀去重缺失”不作为确定修复要求。

fork 普通工具发现传承已补验：不变/变更/移除目录的加载、重搜、屏蔽与冷开，
源工具不重做，实际调用次数/身份和源历史保持，联合 41 通过（00c0a2），仅测试。
见 thread-initial-history-gap.md 当前节。计划需按 Codex PlanUpdate 历史/事件链
验证，不以新 Turn plan=() 推断必须新增长期计划管理器。下一步该历史链与记忆
继承来源去重，CLI 仍暂停；A–E 与最新全量回归继续开放。

fork usage 继承已修复并接入预算消费：独立 inherited_context_usage 保存筛选/映射
后的历史事实，不复制 model_steps；本地新采样优先，即使无 usage 也不回退旧值。
完整/截断/二次分叉/冷开与首次输入自动压缩有行为证据，联合 566 通过（f6dd72）。
见 thread-initial-history-gap.md 当前节。下一步工具发现/工作计划及继承记忆来源
去重，保留 legacy/跨仓库等明确差异；不再将基本 usage 读取列为完全缺失。
全量核心回归尚未覆盖本次生产变更，CLI 对齐仍暂停。

实际手动压缩（1/2 轮）→fork→分支采样/再压缩→冷恢复已补验，引用映射、
最新摘要/保留输入、精确采样次数与源不变，联合 201 通过（e4c769），仅改测试。
下一步修复已定位的继承 usage：当前 load_context_usage 仅读 model_steps，而
fork 不复制该表，首采样前预算缺少历史事实；独立保存映射后的 usage，不复制
执行义务。详见 thread-initial-history-gap.md 当前节。其他 A–E 仍开放，CLI 暂停。

fork 截断差异已修复：严格按用户消息位置保留同 Turn 的较早输入，不将已截断
回答带作成功结果；初始 context 保留。压缩保留副本不能把旧完成 Turn 误标取消，
两类反例先红后绿，最终关联 288 通过（6a9583）。详见 thread-initial-history-gap.md
当前节。下一步仍是完整压缩/context 重建、工具发现/工作状态、usage、记忆去重，
不重复证明已修复截断位置；全量核心结果早于本轮生产修改，CLI 仍暂停。

fork 基本入口已实现，上一轮四个反例转绿；新增事务回滚/提交后复用及活动源未知
工具结果隔离，全文件 15 通过，关联 286 通过（4cab5c）。真实 Runtime 接入
SessionRepository.fork_thread，SQLite 原子复制并重映射引用，独立目标保留来源
回执，不复制执行义务。详见 thread-initial-history-gap.md 当前实现节。
下一步不是继续补缺失入口，而是验证压缩/context、工具发现/工作计划和 usage，
处理继承历史的记忆来源去重及 legacy/跨仓库边界；状态仍部分一致。全量回归尚未
刷新到本次生产改动，CLI 暂停。下方“新增四失败”仅为修复前历史。

fork 四个公开 Runtime 反例已加入 test_thread_fork.py，目前全红于接口缺失
（d1e5e4），不是已验证能力。下一批直接实施 fork_from_thread_id 与
fork_before_user_message 对应的核心快照/原子发布，不重复增写入口不存在的测试。
必须映射 Item/Turn 与压缩/context 引用，不复制源执行义务，保留原全局碰撞检查；
后续还需活动源/工具/压缩及失败窗口，详见 thread-initial-history-gap.md。
当前工作树有这四项新增失败，不能引用先前 126 通过称最新全绿；CLI 仍暂停。

clear 持久计划四窗口专项已补：16 例覆盖配置撤销/保留、停止/继续，原 clear
计划不被 resume 改写、未知结果不重做；五文件 126 通过（7af89e），本批仅测试。
下一步 fork：SQLite item/turn ID 全局唯一，不能原样复制历史；先设计身份/引用
映射、压缩边界与原子发布。详见 thread-initial-history-gap.md 最新节及存储约束。
不再重复扩充已覆盖 clear 四窗口；CLI 暂停，整体 A–E 和全量刷新仍待完成。

clear 基本闭环已实现：公开 Runtime 增加显式 startup/clear 新线程来源，默认仍
自动创建/恢复；已有身份拒绝覆盖，旧历史保留。真实 clear matcher、停止/继续、
创建提交前后重试、完成后冷开不重发 clear 已验证。五文件 8 worker 共 110 通过
（548b80），详见 thread-initial-history-gap.md 当前实现节。下一步补 clear 的持久
计划冷恢复，再推进 fork 快照/独立发布；不重复声称 clear 入口完全缺失。全量回归
尚未覆盖此次生产改动，CLI 暂停，A–E 仍开放。

2026-09-13 初始历史入口复核：clear/fork 为实际缺失，不是仅剩 Hook source 测试。
Codex clear 是新空线程，fork 有独立身份、快照截断和持久中断边界；Corki 公开
Runtime/Repository 尚无对应入口。详见 thread-initial-history-gap.md 的调用链与
验收契约。下一批先接入非破坏性的 clear 核心启动闭环，再处理 fork 的快照/发布；
不新增 CLI 命令或界面对齐，不把 MCP 连接 fork、来源类型或注释误算为已有实现。
以下各轮记录保留历史；session-start-gap.md 的显式新 ID 修复位于末尾同名章节，
旧记录中的“顶部”定位不准确。已有 startup/resume 窗口不作为新待办反复扩充。

startup持久计划与冷resume归类交接已补验：新增未claim计划窗口及明确startup
matcher，冷保留授权执行一次、撤销拒绝、completed复用、unknown不重复，计划
不改写；保留原manual/auto压缩矩阵。三文件8worker90通过（87234d），只补测试，
见session-start-gap.md顶部。下一步仍需全A–E差异收敛，勿把已核验原计划归类当
新缺口反复扩充；CLI暂停，最近生产修复后的全量仍待刷新。

SessionStart创建提交前后故障、空历史已有线程冷开及子Agent显式新ID已补验：
无提前Hook/采样，writer释放，同Runtime来源保持，元数据已存在的冷开走resume；
只有ThreadSpawn发SubagentStart且不以continue:false停止。六文件8worker91通过
（f8ca98），只新增测试，详见session-start-gap.md顶部。此批不等于所有生命周期
完成；下一步回到启动来源/持久义务组合及其余A–E，CLI暂停。

A4显式新thread_id误判resume已确认并修复：两反例先红，初始化锁内按存储存在性
解析一次来源，新ID走startup、已有记录走resume；六文件8worker共70通过
（410e9f）。见session-start-gap.md顶部。新增SessionRepository.thread_exists，
内置SQLite/Volatile实现；后续需创建半提交与子Agent等定向窗口，当前完整回归
早于此生产修复，不能冒称最新全绿。CLI暂停，E4接口兼容选择仍待用户。

stdio EOF夹具修正后的五文件关联94通过（56f5f4），61607最终退出0（c995a0），
无活跃回归句柄。下一步A4：runtime.py约533/838以thread_id是否None决定startup/
resume，对“显式新ID”和真正历史恢复的区分尚需沿实际创建链反例验证；仅是待核对
假设，不先宣称生产缺陷。整体A–E继续，CLI暂停；E4公开API兼容选择仍待用户。

集成74817已退出1：9075通过、1失败、8:19（9b29f6），最终报告已保存。
唯一失败源自stdio EOF测试使用关闭后可清空的_process字段，改为已保存的真实
Process引用，保留退出码/期限/账本等断言。原例串行通过，修正后整文件8worker
36通过（89cd70），生产未改。下步扩大EOF/刷新/关闭关联验证，再完成A–E剩余
验收；不再轮询74817，不把原并行失败重述为全绿。CLI暂停，E4兼容选择待答复。

当前集成session74817仍运行，最新约67%；约51%已出现一个失败标记（40ea65），
详情待终态。先读同一进程最终报告，不重启、不改在跑代码/测试，不先假定时序或
生产原因。此前无失败的进度记录仅属历史。用户同步create兼容问题仍未收到选择。

非CLI全部集成组已启动，session74817，8worker/loadfile/禁止自动重启，已配置
当前及五份历史编译器。首次续读约1%，尚未终态（1d3cfc）。先跟进此进程并处理
最终失败/skip；运行期间不改生产/测试，不重启原批。见core-regression-current.md
顶部隔离检查。已结束单元组88800不要再轮询；整体A–E验收与CLI暂停范围不变。

最新分组回归：非CLI全部单元测试8worker/loadfile共5859通过、1已知文件系统skip，
23秒退出0（e0fd66）；session88800已结束。下一步检查并刷新非CLI integration组，
不要重复启动单元组或把旧完整结果称作最新全绿。详见parallel-test-readiness.md，
仍保持全部A–E验收范围，CLI对齐暂停。

原完整回归六项历史编译器skip已在当前代码补验：五份旧二进制hash一致，六worker
六通过/零跳过（d7aaee），未改默认编译器；唯一非UTF-8文件名场景仍受当前文件
系统限制，重验仍skip（fb0a48）。全src/tests静态、锁及依赖检查通过（ea69df）。
详见core-regression-current.md顶部；不再把六项旧编译器列为待补，也不冒充最新
全量已跑完。后续仍须收敛A–E剩余差异及最近生产修复后的完整回归，CLI暂停。

E1/E5/E6新增同Turn组合：工具错误Observation→模型采样重试→后续工具Fatal或
取消，明确采样/执行次数、历史一致和唯一终态。两文件8worker共35通过
（08c627），见error-classification-sequence.md。只补普通直接调用组合，不把
嵌套Code Mode的Fatal降级或后台/Hook错误混为同一分类；全目标继续开放。

工具完成后的自动摘要吞取消两场景及冷开已补验：工具对/完整原结果保留，效果
一次，唯一取消终态，无摘要安装和续采样，冷resume为空。四文件8worker共64通过
（1bb180），见summary-cancellation-review.md顶部。此前工具后自动取消待办已覆盖
此具体read/close组合，不扩张为所有Hook交错或OS强杀；本批未改生产，CLI暂停。

C4/C8/E6自动压缩取消补验：超阈值历史沿公开stream触发摘要，read/close吞取消
正常返回仍取消、不安装摘要，原输入保留且无续采样。四文件8worker共62通过
（eab9e3），只补测试，见summary-cancellation-review.md顶部；不再仅从手动入口
推断此行为。工具后自动摘要取消、Hook交错及E4同步API兼容选择仍独立开放。

有效取消意图与损坏Interrupt计划的交叉恢复已补验：保持唯一取消终态和持久意图，
保留错误日志，无采样/命令执行，重复resume为空。七文件8worker共106通过
（cc34a5），仅新增测试，详见interrupt-hook-gap.md顶部。此项诊断不再列为未验；
并发关闭及MCP模板/结果结构仍未由本批覆盖，下一批回到A–E验收清单按影响排序，
避免继续仅扩大Interrupt基础矩阵。CLI继续暂停，完整回归仍早于最近生产修复。

Interrupt旧计划结构损坏四个冷恢复反例已修复：restore_plan在认可取消证据及
执行恢复前校验payload/命令基础结构与唯一key，错误终态保留原记录且无采样。
相关七文件8worker共101通过（de98b4），全静态通过；见interrupt-hook-gap.md顶部。
新取消意图同时存在的损坏诊断、MCP模板/结果结构与其它A–E仍开放。14913通过的
完整回归早于本次生产修复，不当作修复后全量结果；CLI继续暂停。

并行支持已落地dev extra及uv.lock，本机缓存安装xdist3.8.0/execnet2.1.2，无其它
依赖升级。8worker中断契约/冷恢复/关闭四文件74通过（342dbd），锁与依赖/全静态通过。
详见parallel-test-readiness.md；不再把并行安装列为待办。接下来回到Interrupt完整
持久字段及A–E剩余缺口，适合隔离的定向测试沿用并行，六历史编译器skip仍待补验。

最新核心回归96933已退出0：14913通过、7跳过、42:42（846f9d）。最终报告见
core-regression-96933.txt；不再轮询此进程。跳过为1文件系统限制及6历史编译器需求。
下一步按parallel-test-readiness.md落实并行开发依赖与隔离后的测试，结合本批skip
补验；同时继续Interrupt完整持久字段和其它A–E开放项，CLI暂停。下方运行中段落
属于历史，不以本批回归通过替代全目标验收。

新完整核心回归已启动：session96933/PID28230，首次续读约12%、一个skip，
未终态，详见core-regression-current.md顶部。全src/tests静态及1254文件格式通过。
下一步先续读同一进程并处理实际失败，运行期间不修改生产/测试，不重复启动。
终态成功但取消意图提交前/后失败的并发close四例已补验：原始错误传播、单次资源
关闭、取消终态落盘及冷恢复不重采样；专项6通过（9596b2），见interrupt-hook-gap.md。
仍保留其余A–E开放项，CLI暂停；不能用回归通过代替差异清单验收。

取消意图实际提交前/后故障补验12例完成，结合终态和Hook结果写入失败、marker开关，
公开取消及冷恢复均无重采样/副作用重做；两文件34通过（65a493），详见
interrupt-hook-gap.md顶部。下一步仍需终态成功但意图失败、并发关闭错误传播、
Interrupt完整持久字段校验及其余A–E开放项；全量尚未重跑，CLI继续暂停。

当前扩大核心回归已结束：session91344退出1，14848通过、36失败、7跳过（1ce85a）。
回到A1/A7/E7，取消意图合法/损坏冷恢复新增10例，与真实中断恢复/命令/关闭
联合36通过（2eb86a）。合法记录阻止模型采样，损坏记录明确失败且证据保留；
不覆盖意图写入本身失败，后续仍需该提交窗口及其余A–E开放项。
原3000阈值下的二次压缩现已补入参数测试，验证fresh工具结果进入第二摘要、
最终摘要可见、当前输入保留、定义再次释放且副作用不重做；相关扩大381通过
（94c248）。不以单次高阈值测试替代该场景，全量和A–E开放项仍待后续。
六失败文件的后续联合154通过（16ba71），原36失败均定向复验通过；原因和
夹具修改依据见core-regression-current.md顶部。全量尚未重跑，A–E开放项仍继续。
环境两个旧预期已按Codex日期增量携带权限快照的源码语义修正，环境六项通过
（540620）；当前剩余2项为memory worker预算和deferred末次请求结果断言。
后续三个Stop文件的32失败已定位为夹具拦截到新增UserPromptSubmit批次；限定原Stop
阶段后136项通过（3f3413），原安全和次数断言保留、生产未改。下一步剩余4项
memory窗口、deferred压缩和environment增量失败；原全量结果不改称全绿。
完整回溯及六组失败见core-regression-current.md和core-regression-91344.txt；
下一步独立重现并定位，优先Stop恢复副作用/终态与准入故障边界。以下为启动历史。

原扩大核心回归启动：session91344/PID10468，初次续读约13%，尚未终态；
全src/tests Ruff及1253文件format通过。见core-regression-current.md当前批次。
先续读同一进程并处理实际失败，不因观察超时重启；运行期间不修改生产/测试。
CLI仍排除，旧14337通过不能当作本批结果，A–E差异验收继续开放。

旧无取消意图/展示marker但有Interrupt计划的误续跑两反例已修复，恢复先校验该
计划归属再取消，不重复Hook/模型。相关四文件73通过（c292de），见interrupt-hook-gap.md
旧会话兼容节；完全无持久证据不能猜测取消。下一步持久字段/提交故障及A–E剩余项，
不重复扩大已验的基础中断矩阵；CLI继续暂停。

取消展示marker关闭+终态保存失败会冷恢复重采样的两反例已修复：取消意图独立
持久化，恢复先处理控制状态，无需Hook或展示文字。旧marker兼容保留。初批72通过，
扩展无Hook及历史/恢复四文件66通过（db741b），见interrupt-hook-gap.md当前修复节。
下一步意图/旧无marker半提交兼容与剩余A–E，不把可选展示作为恢复屏障；CLI暂停。

Interrupt来源待办范围已纠正：Codex最后Step metadata是x-codex-turn-metadata专属键，
executor临时信任例外仅匹配指定官方插件；按用户边界不复制。普通payload模型取初始
Turn，不能误改成最后Step模型。详见interrupt-hook-gap.md范围纠正节；通用可信插件
来源和MCP授权仍在范围内。下一步优先持久提交/取消/冷恢复，CLI暂停。

Interrupt实际HttpMCPClient离线中断/关闭×五结果专项10通过，模板原身份、无重采样/
重调用、超时响应先于终态关闭、未信任无调用已补验；联合五文件60通过（b7fcea）。
见interrupt-hook-gap.md顶部，本批仅补测试。下一步最后Step来源/持久恢复，
不重复把基本MCP中断接入列为未知；CLI继续暂停。

Interrupt关闭准入两反例已修复：共享Hook owner原先在Turn中断处理前封闭，导致
普通/realtime的正常close抛CancelledError。现活动Turn join后才关闭该owner，
真实脚本受控启动/回收、单终态和重复close均验证；六文件101通过（a68146）。见
interrupt-hook-gap.md当前关闭节。下一步最后Step来源/真实MCP与持久恢复，CLI暂停。

Interrupt基础链已接入Runtime取消收尾：原root反例转绿，命令/MCP发现1～3秒、
专用非控制输出与异步owner已接。12个真实取消场景含异步脚本门控，专项38通过
（fdb911），此前九文件151通过（fb9423）；见interrupt-hook-gap.md当前节。
下一步优先最后Step来源/关闭异步准入及持久恢复，不称全部中断路径完成；CLI暂停。

当前优先 Interrupt 生命周期：已追踪 Codex 两条取消收尾路径、专用输出/超时/异步/
MCP 契约；Corki 只有事件元数据，公开 root interrupted 反例未执行脚本，五个阴性
对照通过（a5cfdc）。见 interrupt-hook-gap.md。下一步真实分发、取消终态前通知及
持久恢复，不把 SessionEnd 实现等同 Interrupt 支持；CLI 继续暂停。

SessionEnd早于待落盘输入屏障的两例真实反例已修复（fae163→a1365f）：先完成输入
flush再执行关闭脚本，首次close失败时延后，重试不重复关闭执行服务。两文件35通过，
既有关联101通过（49fdf0），见session-end-gap.md顶部。该具体顺序缺口已关闭；
下一步回到A–E生命周期剩余项及当前整体回归，不反复扩展已验普通关闭矩阵。CLI暂停。

SessionEnd基础生产链已接Runtime关闭：专门同步1～3秒命令、独立账本身份、忽略stdout
控制，新增stream_close宿主事件出口且退出观察不取消底层清理。原root反例转绿，
async/真实超时回收专项16通过（b514f7），既有关闭/初始化等101通过（d4a071），见
session-end-gap.md顶部。下一步关闭账本故障/资源交错及其它生命周期，不重复称仅
SessionEnd元数据；CLI继续暂停。

当前优先A4/E7 SessionEnd实际关闭链缺失，见session-end-gap.md。两个root真实脚本
反例失败、四个SubAgent阴性对照通过（df164c）；生产未改。需专门1～3秒同步关闭
执行、拒绝MCP、忽略stdout控制、transcript和宿主通知，不能复用普通start/stop路径。
下一步接入执行服务关闭后/storage关闭前边界，CLI继续暂停。

取消后的compact start旧unknown阻塞下一Turn两反例已修复：消费Turn已持久终态时不
转移旧义务，原执行未知事实不改写；同Runtime/冷开新输入均通过，相关34通过（f2ad37）。
见session-start-gap.md顶部。后续终态未落盘/consumer绑定窗口及其它生命周期仍开放，
不将已验的取消终态后新输入阻塞重复列为未修复；CLI仍暂停。

压缩后SessionStart同步执行冷恢复补验16例完成：manual/auto的unknown/completed/
receipt/done四窗口×stop/allow，移除配置冷开后原决定/反馈保持，脚本/摘要/工具不重放。
新文件22通过（06cb78），相关五文件92通过（f83814），见session-start-gap.md顶部。
仅补测试。下一步登记/marker/consumer绑定、取消跨Turn与其它生命周期，不重复扩大
已验的执行提交矩阵；CLI继续暂停。

压缩后SessionStart基础生产链已修复：按已安装marker消费source，执行时选可信配置，
正常stop及输入准入保护，window刷新/重算包含context的模型历史。原四反例转绿，
同Turn两次压缩/手动冷开/下一Turn无旧来源及配置新增移除已验。相关103通过（345e5e），
最新专项10通过（176700），详见session-start-gap.md顶部。下一步持久消费故障窗口、
clear/分叉来源与其它生命周期，不重复当作基础compact来源缺失；CLI仍暂停。

当前优先压缩后SessionStart：manual/auto×continue/stop四个真实Runtime反例均红
（55ee69），摘要已安装但Hook不执行。见session-start-gap.md顶部；本批未改生产。
需按marker排队source、实际消费时选可信handlers，并重建/预算注入后的模型历史。
仅PostCompact回调append会被window返回的旧compacted_active遗漏，不可当作完整修复。
初始SessionStart已实现，勿退回重复该基础矩阵；CLI继续暂停。

SessionStart/SubagentStart基础生产已接Runtime：原startup/resume四反例转绿，未知/完成/
receipt冷恢复及配置移除六例、ThreadSpawn角色与内部SubAgent隔离三例补验；新功能37
通过（d14f90），已有prompt/MCP相关78通过（599ab3）。见session-start-gap.md顶部。
下一步优先compact/clear来源队列与恢复/分叉义务关系、异步checkpoint及取消；不能再
笼统称所有start只元数据，也不能将初始入口通过等同完整生命周期完成。CLI继续暂停。

当前优先A4 SessionStart/SubagentStart真实缺口，源码链及落地方案见session-start-gap.md。
startup/resume×context/stop四个公开Runtime反例均失败（ceb885），可信脚本未执行。
仅新增测试/审计，生产尚未接入；下一步实现来源队列、持久执行与用户准入前的控制/
context链，随后压缩后compact来源及子Agent区分。CLI继续暂停，不以旧回归称当前全绿。

异步UserPromptSubmit采样checkpoint首请求丢反馈反例已修复：持久选择execution集合
先于投递，Runtime恢复时将反馈投影回冻结请求。选择计划/delivery receipt提交后
再中断冷开均验证，五文件90通过（df966d），见user-prompt-submit-gap.md顶部。
下一步检查投递取消/压缩交错与A4其它生命周期；不再将基础call_model恢复列为未实现。
当前全量仍待重跑，CLI继续暂停。

异步UserPromptSubmit真实进程完成/运行中关闭两场景通过：Turn不等脚本、关闭回收
PID，二次冷开完成反馈不重复、未知不重跑。联合103通过（4166a0），见
user-prompt-submit-gap.md；仅补测试。下一步checkpoint/投递中断/压缩交错，不再
把基础真实进程关闭列为未验证，CLI继续暂停。

异步UserPromptSubmit完成结果丢反馈反例已修复：后台只落盘，prepare/finalize从账本
投递并保存receipt，冷开移除配置仍恢复，二次冷开不重复，未知效果不重做。
联合101通过（ee8df4），见user-prompt-submit-gap.md。下一步checkpoint跳过prepare/
投递取消与压缩及实际异步进程关闭，不再把基础跨Turn冷恢复列为缺失。CLI暂停。

终态UserPromptSubmit取消逃逸导致无终态/关闭失败的真实反例已修复：取消结果正常
持久化，未提交steer归还宿主，flush义务清除，联合104通过（b6add5）。详见
user-prompt-submit-gap.md；后续仍需未完成准入冷恢复和异步投递，不重复将该
具体runner取消列为未知，不宣称整个E7关闭。CLI继续暂停。

终态flush绕过UserPromptSubmit两反例已修复，Stop期间到达输入在正常收尾仍检查，
拒绝不入历史，允许保存user→context且不重新采样，相关45通过（33dc6d）。
详见user-prompt-submit-gap.md；未完成检查的重试恢复、终态Hook取消及异步反馈仍
开放，不将全部flush标完成。CLI暂停。

UserPromptSubmit准入后prepare失败/取消丢context的两反例已修复：Runtime终态初始
输入保留复用receipt按user→context写入，终态语义不变，联合88通过（061d35）。
见user-prompt-submit-gap.md；下一步未检查realtime leftovers/异步投递恢复，
不再将已验的初始准入后普通失败/取消保留列为未知。CLI仍暂停。

UserPromptSubmit同步context顺序四反例已修复：receipt保留待投递context，window
接收user→contexts，初始/steering与冷恢复初轮26通过（88c4b7）。见
user-prompt-submit-gap.md顶部；不再把“同步context始终先于user”当当前行为。
下一步优先准入后取消/flush及异步投递恢复，CLI继续暂停。

UserPromptSubmit基础RUNNING冷恢复12场景通过（f2f190）：unknown不重做且失败，
completed/receipt在移除配置后仍保留原决定/context，二次resume无重复。详见
user-prompt-submit-gap.md；仅补测试，不把全部恢复列为未开始，也不据此关闭异步
投递、context顺序与终态flush未检查输入交错。下一步优先这些实际剩余边界，CLI暂停。

UserPromptSubmit基础生产接入已完成：初始与运行中新输入检查、持久plan/执行/receipt、
context注入、拒绝正常结束及终态不复活输入已接Runtime。14场景通过（667ed8），
首轮相关160通过（fc2254）；详见user-prompt-submit-gap.md顶部。下一步优先真实
RUNNING冷恢复与context顺序/投递、终态未检查输入flush交错，不能把下方历史
“只解析/未接入”当成现状，也不能称完整UserPromptSubmit或A4已验收。CLI暂停。

UserPromptSubmit输出解析已实现且45项单测通过（cb993e），但尚未接入Runtime，
两实际反例仍待修复，不能当成能力完成。输入flush/ack及prepare无条件采样路径
已核对，见user-prompt-submit-gap.md；下一步将解析接入持久准入和主循环终止边界，
不以独立解析模块替代端到端交付。CLI继续暂停。

当前优先A4 UserPromptSubmit真实缺失：源码追踪表明需在初始/后续用户输入记录前
检查，Corki只列事件元数据，没有执行链。实际Runtime两反例均未启动可信脚本
（39b0d2）；生产未修改，不能称当前测试全绿。见user-prompt-submit-gap.md，
下一轮实施该准入/上下文/持久恢复链，避免只在启动处调用或把阻止错误映射为取消。
CLI仍暂停；既有压缩Hook正常/异步关闭已验证矩阵不再重复扩大。

自动异步压缩Hook真实Runtime/实际脚本完成与关闭新增四场景通过；联合121通过
（c738cf），详见compact-hook-gap.md。压缩不等脚本、关闭回收PID、冷开不重启、
摘要及工具效果仅一次已补证；仅改测试。后续聚焦A4其它生命周期、MCP故障/恢复
与剩余持久契约，不再重复扩展自动异步正常关闭矩阵；CLI仍暂停。

压缩Hook恢复payload基础类型校验已补齐，自动/手动五字段损坏十反例先红后绿，
六文件联合158通过（7c449f），见compact-hook-gap.md。合法历史model/cwd不改写，
旧session兼容和模型切换回归保留。后续聚焦command持久契约、MCP故障/恢复、
自动异步及A4其余事件，不再笼统把所有payload类型校验列为完全缺失。CLI仍暂停。

压缩Hook恢复session归属两反例已修复，旧v1 thread_id身份两对照保留原payload；
自动/手动均拒绝foreign-session且不调用Hook，已安装摘要不重采样。联合六文件
148通过（9bffc4），见compact-hook-gap.md。其它payload/command持久字段契约、
MCP故障/冷恢复与未验自动异步交错仍开放；CLI暂停，不将本批视为全量验收。

异步Pre/PostCompact真实脚本完成与运行中关闭四场景通过，确认压缩不等待、
后台输出不控制终态、关闭回收PID、冷开不重启。联合103通过（227633），见
compact-hook-gap.md。下一步剩余持久字段校验、MCP故障/恢复与未验自动异步
边界；不再将异步基础执行/手动关闭清理列为缺失。CLI仍暂停。

PreCompact未知/完成冷恢复新增八例通过；实际HttpMCPClient压缩Hook正常控制/
未信任等16例补验，联合68通过（357e71）。详见compact-hook-gap.md，未改生产。
下一步异步执行、MCP自身故障/恢复及剩余持久字段损坏；不再将Pre账本或MCP
基本接入列为未验。CLI仍暂停。

PostCompact执行账本unknown/completed自动/手动冷恢复及配置移除已补验；另外
计划version=true误接受为1的两反例已修复，联合90通过（eaa185）。见
compact-hook-gap.md。下一步Pre执行恢复、异步/MCP及其余持久字段损坏，不把
已验的Post完成/未知不重放重复列为完全未知。CLI仍暂停。

压缩Hook model字段误用当前Step而非摘要owner的两反例已修复；Step动态激活/
previous-model降档（含摘要失败无Post与checkpoint恢复）联合90通过（d5b835）。
见compact-hook-gap.md。下一步执行账本unknown/completed及异步/MCP、计划字段
损坏验收，不继续把已验的模型归属列为未实现；CLI仍暂停。

压缩Hook session与ThreadSpawn元数据八反例已修复，联合122通过（3b8d93）；
session从已保存会话读取，仅ThreadSpawn附agent_id/角色，不给内部来源伪造身份。
见compact-hook-gap.md。下一步实际摘要模型owner元数据、计划/执行账本故障与
异步/MCP验收；不要重复把session/agent字段缺失列为当前行为。CLI仍暂停。

PostCompact计划先于摘要marker安装落盘已实现，安装后新增/移除配置两个真实
反例转绿；自动/手动六场景及相关联合95通过（a2e057）。见compact-hook-gap.md
顶部，不再将安装后才建立计划列为当前行为。下一步payload来源身份及计划/执行
账本损坏、未知/完成恢复、异步/MCP实际执行验收；CLI仍暂停。

压缩Hook多同步事件反序两反例已修复：保留并发执行，完成通知按配置排序。
提交失败取消join追加两例，联合66通过（882415），见compact-hook-gap.md。
下一步优先Post计划/安装关联及payload来源身份，不再将配置顺序聚合列为未实现。

当前Pre/PostCompact基础生产接入已完成，四原始反例转绿；允许/非法block/
未信任扩大16通过，receipt后联合474通过（2c4cca），解析16通过。见
compact-hook-gap.md顶部的当前实现和具体剩余边界。下一步优先Post计划/安装
恢复关联、多Hook事件顺序及payload来源身份，随后异步/MCP实际执行验收；
不能把下方历史“生产未修改”当现状，也不能称A4/C8已经完成。CLI仍暂停。

最高优先：Pre/PostCompact真实Runtime四反例已红（5ec7c0），手动/自动均不执行
已信任Hook。test_compact_hooks.py未xfail，生产尚未修改。详见compact-hook-gap.md
安装/重试/模型切换及实时输入入口记录；下一步应实现此项，不将仅元数据支持
算成一致，也不继续重复扩展已通过的MCP测试矩阵。CLI仍暂停。

最新Stop/SubagentStop MCP三窗口冷恢复及配置key迁移新增12例，联合123通过
（19d6e2），见mcp-tool-hook-gap.md。下一步转向A4/C8已确认的Pre/PostCompact
真实执行缺口，见compact-hook-gap.md；不继续把已验的四类MCP恢复当作全新缺失。
压缩Hook属于本地Harness，不属于用户排除的远程压缩接口。CLI仍暂停。

最新Pre/Post MCP Hook已完成/未知结果冷Runtime恢复16组合已验，配置及MCP服务
移除也不丢已保存反馈或重复调用。联合MCP Hook/原Post恢复159通过（f1a67b），
仅新增测试；详见mcp-tool-hook-gap.md。下一步Stop/SubagentStop的MCP自身恢复
与A4事件/来源，不能再将全部MCP冷恢复列为未验证，也不因此关闭A7/C8/B10。

最新MCP Hook真实HTTP半截响应体超时/取消已补验，Pre/Post四组合及既有HTTP
清理/模板联合53通过（1ec5fa），终态前响应关闭、取消向调用方传播、调用不自动
重发。仅新增测试，无生产修复，见mcp-tool-hook-gap.md。下一步MCP Hook自身
完成/未知结果冷恢复与A4事件/来源审计；下文HTTP待验记录已被本证据更新。

最新优先：B10/A4的mcp_tool型Hook基础同步链已实现，原两反例转绿。Pre/Post
普通/嵌套及旧command异步/冷恢复联合322通过，模板/指纹另10通过，见
mcp-tool-hook-gap.md当前节。下一步MCP自己的撤销/就绪/传输故障/冷恢复及Stop
验收，不能把command回归当作MCP全部验证；当前生产变更尚未跑完整核心全量。

mcp_tool Hook信任/就绪补验已完成：Pre/Post×普通/嵌套×7策略28场景，未信任/
撤销/修改、采样期间禁用/移除服务、连接替换一直pending均不发越权调用；联合
模板/reconciliation/binding69通过（c37cda）。单测同名收集问题已通过重命名
修正。Stop/SubagentStop用户Hook现有root/child/internal三场景补验，联合77通过；
原生内部memory worker仍保留system/托管/ExecutorScoped Hook，不能将用户Hook
隔离泛化为所有来源跳过。下一步真实HTTP超时/取消、MCP自身冷恢复及A4来源审计。

当前状态以本节及acceptance-index.md主表为准，下文按时间保留的旧状态不是新任务。
最新核心全量14337通过/7跳过，退出0；旧编译器定向补验109通过/1文件系统跳过，
见core-regression-current.md。最新已新增环境权限快照生产修复、原生编译器重建
及Code Mode逐调用计划事件修复，
该全量早于本次修复；不能把新增测试数量算进该全量或称当前代码全量已验证。

已补证，不再当作完全缺失重复实现：异步Pre/Post执行与冷恢复、共享并发、
已消费Pre反馈手动/自动/失败重试压缩后不复活；结果错误身份/非JSON元数据/
非法返回值的Runtime错误Observation；stage-one重置后新owner四种状态的
旧回调拒绝；摘要401独立有限重试与用户要求的402立即失败。

E3 普通模型故障链已按当前源码和七文件 148 项回归收敛，见
model-failure-acceptance.md；不再笼统追加“其他 HTTP 状态和关闭组合待收敛”。
摘要独立策略、工具/扩展/记忆及全部资源所有权仍按相应 A–E 条目验收。

E2工具故障已完成逐分类验收归并，八文件246通过，新增未知工具/非法JSON/
handler直接Runtime后定向33通过，含实际32MB原始输出超限；见
tool-failure-acceptance.md。后续不再用“其他工具错误尚未核验”笼统占位，
重点回到E4/E7资源生命周期及E5内部重试和A–D剩余项。

E7 Runtime关闭编排补验：11个真实资源入口清理后分别报告普通错误或取消异常，
后续依赖仍关闭一次、重复close保留失败、writer释放。联合187通过，见
runtime-close-failure-acceptance.md。该证据不覆盖各资源内部关闭前失败或永久
挂起，不能据此关闭全部E7；后续不重复将该编排传播矩阵列为未知。
进一步核验MCP旧view/共享物理连接关闭、观察者取消后底层报错/取消与兄弟连接
回收；联合Code Mode真实进程/回调故障及MCP进程、binding五文件58通过，详见
同文后续节。此组所有权链已补证，下一步回到A–D剩余契约归并，不持续扩大相同
关闭矩阵；E4公开构造兼容选择仍保留待确认。

下一轮应优先核对尚未覆盖的完整链路，而不是继续扩展上述已验证矩阵：

A1核心生命周期已完成源码/验收归并：Thread身份与写者、准入首写、终态、Step
快照身份及RUNNING冷恢复联合131通过，18旧CLI混合场景未选入；见
runtime-lifecycle-acceptance.md。新增多个Step ID/重复item ID的真实Runtime拒绝
验证，不再笼统将A1生命周期列为整项未知；E4/E7及Hook交错不据此关闭。

A6/D1：同一Code Mode脚本内多次update_plan被合并为最后一份的真实缺口已修复。
现按内层调用身份逐项发布，外层保留checkpoint聚合但去重事件；脚本后续报错不
抹掉已提交更新，扩大132通过，见plan-event-boundary.md。此为核心事件修复，
不是恢复CLI对齐；其他工作状态边界仍需收敛。

C1/C2有效环境权限快照已实现原生元数据→Python校验→builder→v2快照/增量。
独立原生构建成功并保留旧安装备份后更新安装；75项单测通过。
managed/disabled原始红测已转绿并新增冷开去重；首轮联合30通过/2旧安装失败，
安装更新后扩大420通过（含默认Runtime、压缩重建、有效deny_read）；
最终追加同目录冷开权限变更delta后定向86通过，不与420相加。
见context-snapshot-wire.md顶部，不再重复将字段缺失当作尚未开始实现。

E5 归并：工具调用身份/旧账本/未知不重做/模型重试本轮58通过，主表已更新。
process内部权限/沙箱startup重试已完成源码与原生编译器66项验证：仅早期拒绝、
策略允许及有效批准时至多重试一次；拒绝分类不保证第一次无部分副作用。
patch内部部分写入/新审批/单次重试/未知结果已核验，相关68通过、1历史编译器
未配置跳过，见tool-result-commit-cancellation.md。两个主要内部重试链已收敛，
下一步回到A–E跨模块剩余项，
不再以相同call-id账本测试替代该链路，也不继续堆叠已证实的冷账本矩阵。

1. E4：运行中loop直接同步create且无construction owner的失败清理仍不接管。
   acreate与无loop同步create已有回滚；公开同步API兼容选择未确认，不擅自禁用。
   最新隔离故障注入再次实证（aae367）：CorkiGraph构造抛错，owned model未关闭，
   借用registry已回滚；诊断结束手动关闭无外部资源的替身。源码实际CLI调用传入
   _construction，核心示例已使用acreate。建议禁止“运行中loop且无owner”的
   同步构造、在资源分配前提示改用await acreate；这会改变公开API可用场景，
   已向用户请求兼容决策，未据此修改API或声称E4完成。
2. A7/C8：checkpoint投递计划和压缩安装、新输入、取消的交错；已有普通冷开
   不复活与安装事务测试不能替代该组合。Pre/Post各自恢复入口投影已落盘而
   采样未更新时被公开compact替换均补验，最近相关204通过；其它提交点/新输入仍开放。
   同Runtime的compact会取消并等待旧worker，勿假定两个worker并发安装。
   已新增摘要中/压缩append前/提交后新输入与取消六组合，真实stream/steer及冷开
   验证续执行优先、输入唯一、未提交归还、已安装摘要不重采样，联合53通过，见
   summary-cancellation-review.md。不再笼统将这些输入到达窗口列为未核验；
   后续聚焦剩余Hook投递checkpoint等跨模块窗口。
3. B10及跨模块恢复：B8/B9已按普通工具契约归并，扩大1287通过无跳过，见
   tool-result-contract-review.md当前结论。期间发现并修正合法native响应旧测试
   夹具缺environment字段；生产校验不变。下列B8/B9旧补验记录作为已完成证据，
   不继续笼统追加跨来源矩阵；后续聚焦B10/A7/C8及E5剩余契约。
   本地参数校验失败的Code Mode catch/uncaught、batch/stream、同身份重放16组合
   已补验，与普通schema/MCP参数通道联合139通过。MCP不套本地schema校验；
   见tool-result-contract-review.md，不将通用schema引擎新增为对齐要求。
   B1注册来源/失败发布/Step快照及插件重载已收敛，当前分组48及213通过，
   见registration-source-review.md；不把E4资源清理选择重复算作B1缺失。
   Code Mode六类非法结果的batch/stream并行兄弟隔离已补验（相关95通过），
   但不将其当作同六类冷恢复/重放已验证，见tool-result-contract-review.md。
   HTTP MCP实际传输断连/ReadTimeout的一次调用、错误回灌及同Thread冷历史
   不重发已补验（三入口新增6例，相关四文件59通过）；实际发送/半截响应体
   的期限耗尽另新增6例，连同人工等待暂停/关闭回归102通过。后续优先其它
   传输及未入账窗口，不重复将该HTTP已提交结果路径当成缺失。
   stdio实际进程收到调用后退出/输出半截JSON后退出的三入口6例也已补齐；
   错误回灌、冷历史不重发和进程/任务/pending清理联合61通过，见同一专项。
   后续优先执行器及未入账窗口，不重复扩展已核验的HTTP/stdio已提交结果矩阵。
   嵌套提交前/后存储报错的四组合新增冷Runtime原身份claim和新Turn补验，
   未提交保持未知、已提交复用原结果且副作用不重做，相关74通过；此处冷claim
   是repository入口，不冒充失败checkpoint的公开resume，见tool-result-commit-cancellation.md。
   后续明确公开resume_pending只取RUNNING：上述已终态FAILED四组合冷调用
   无事件/无采样/无副作用，连同真实RUNNING恢复回归59通过。不要将FAILED
   自动复活列为待实现；后续核验目标是未终态RUNNING工具节点的故障窗口。
   普通RUNNING execute_tools节点在副作用后返回前/真实结果提交后取消，现已
   用冷Runtime公开resume_pending补验：unknown/真实结果分别保留、执行一次、
   同Turn完成且历史唯一，联合76通过。此为节点取消注入，不是OS强杀验证。
4. C1/C2/D1/E7：按来源/状态逐项收敛主表，区分模型可见窗口、归档及生命周期。
   D3已完成版本/租约/水位、合并选择/保留期/基线的源码与行为对照，见
   memory-source-review.md，不再将已核验项列为笼统缺口。
   D5已归并显式记忆请求、遗忘依据、污染入口与来源信任、worker执行约束，
   11文件190通过无跳过，见memory-forgetting-review.md当前结论；不再把这些
   确定性契约笼统列为未核验，模型语义质量和尽力标记限制如实保留。

上述只是优先级，不缩减完整A–E范围。CLI继续暂停，teach.md禁止修改；
下方历史CLI要求不得重新放回当前验收范围。

2026-09-12，再核对当前源代码，而非以旧报告的“待实现”当作现状。
完整要求仍见用户目标文件与 acceptance-index.md，不将下面的优先项当成全部范围。

## 新优先项：MCP 就绪等待错误占住调度屏障

tool-readiness-gate-review.md 的四个真实反例已修复：普通批量/流式及Code Mode
都先等待 readiness 再进入公平并行/独占锁，结果仍按模型顺序；completed/unknown
不触发额外按调用预热。新增36项、扩大232项分别通过。冷恢复目录重建本来就会
prepare_server，不能将按调用预热跳过夸大为整个恢复离线。完整生命周期/跨模块
验收仍依 acceptance-index，而不是停留在旧“四失败未修复”描述。

## 1. 全量回归已终态，失败夹具已修正

最新77351完整单元/集成已正常退出0：14193通过、1环境跳过、2067.68秒；
JUnit及静态核对通过。见core-baseline-refresh.md。本次覆盖旧中断附近及集成
末尾，不再把“全量未结束”作为当前缺口；仍需继续下方核心实现/验收与CLI范围。

更新：最新29459因事件等待停滞受控中断，汇总13643通过、19失败、1跳过，
未完成全量。16项MCP测试需区分readiness与实际binding；3项Hook旧夹具依赖
串行启动。源码核对后仅修测试，MCP84及Hook60通过、静态通过。先追踪停滞并
补齐未执行范围，不将下方历史10586当成最新全量；详见core-baseline-refresh.md。

单元/集成10586：13862通过、2失败、7跳过；独立重现两项旧CLI夹具的即时EOF
取消后，只修夹具的busy/idle输入时机，保留全部原恢复断言并增加取消负断言。
相关34通过，旧compiler六项独立通过，静态通过，见core-baseline-refresh.md。
不把该结果称为原全量一次全绿；下一步应实现下方已证实缺口，不重复全量等待。

## 2. A4 Hook 的来源和执行模式仍有缺失

本地转录已接入SQLite提交后的持续追加、按需创建、父子隔离、ephemeral不落盘与
v3新批次/v1-v2恢复兼容。实际Hook读取和存储联合182通过，新增取消/fsync失败及
异步持有文件跨Turn/压缩联合23通过。见hook-transcript-storage-review.md。
v3路径可用性变化造成恢复身份冲突已复现并修复，恢复保留已登记路径，仍拒绝未知重放。
该项为部分一致：大历史发布成本、工具/partial专项组合仍开放，
不能把路径字段或局部测试当作完整存储对齐。

当前：SubagentStop同步命令已接入真实Runtime，类型化来源、角色matcher、独立授权
及三窗口冷恢复见subagent-stop-review.md。Stop/SubagentStop异步命令也已接入：
8并发、会话owner、延迟诊断、配置刷新、关闭及unknown/completed冷恢复见
async-stop-review.md；214项联合通过。后续真实混合等待命令及手动压缩组合18通过，
独立compact原生不消费旧异步Stop结果，不新增此分支。转录文件、授权UI、完整来源
及更多混合故障仍开放，不再把两种执行模式的基本实现当作完全缺失。
后续修复同批同步命令串行差异：并发执行、配置顺序汇总，故障时取消并join同批
任务；真实反例与扩大236项通过见sync-hook-concurrency-review.md。后续已补并发部分
入账及首位尚未claim命令的冷恢复组合：未知结果阻止整批新副作用，全提交结果复用，
只补执行从未claim的命令；恢复/并发/异步/存储64项通过。其他缺口仍按完整清单推进。

以下为实施前源码定位及仍需保持的边界：当时discover拒绝async=true，run对全部
is_non_root_agent直接返回。
其中不能笼统把全部非root当“需要运行项目Stop”：原生core/src/hook_runtime.rs
区分ThreadSpawn→SubagentStop、内部MemoryConsolidation→专用target，其他synthetic
subagent不运行用户生命周期hooks。hooks/src/events/stop.rs::select_handlers又对
MemoryConsolidation排除User/Project/SessionFlags/Plugin，只留下管理/执行器来源。

所以当前已支持的用户/项目/插件Stop不应因“补齐memory hook”而在记忆子任务
重新执行；此前泛称“内部记忆Stop缺失”不足以指导实现。应先补ThreadSpawn来源
的SubagentStop分流及参数、确认现有管理来源范围，再设计async任务所有权。
官方云端管理/执行器产品来源不能作为要求重新引入。根Stop批次恢复与当前授权
预检必须保留，不把新事件混入已有root批次或复用错误的请求身份。

SubagentStop接线细化：原生`core/src/hook_runtime.rs::run_turn_stop_hooks`从
ThreadSpawn取agent_role和parent_thread_id；agent_id是子thread id，agent_type是
角色（缺省默认角色），transcript_path是父会话路径，agent_transcript_path是子会话
路径。父路径查找失败只记诊断并给空路径，不应伪造子路径替代父路径。
Corki虽已有`protocol/session_source.py::ThreadSpawnSource`，但
`Runtime._execute_run`创建GraphRunContext时只传is_non_root_agent布尔值，
`graph.py::GraphRunContext`也只有此布尔字段；Hook层目前拿不到类型化来源。
因此修复必须贯通Runtime→GraphRunContext→StopHooks，不能仅改非root判断。
`discover`固定读取Stop，`command_identity`固定把event_name=stop纳入摘要，
配置key也固定stop；SubagentStop必须独立事件发现、匹配和授权身份，保留原Stop
指纹的兼容性，不能让一份Stop信任自动授权子任务事件。
插件`agent_hooks.py`接受SubagentStop名称只是metadata解析，不代表执行已接通。

### 异步执行的源码约束（实施前审计，当前实现见async-stop-review.md）

原生链路：`hooks/src/events/stop.rs::run` →
`engine/dispatcher.rs::execute_handlers_with_metadata` →
`engine/command_runner.rs::schedule_async_hook/schedule_async_task`。
异步handler不进入同步结果聚合；因此不是把现有同步执行放入Task后继续等待。
CommandHookRuntime按session持有JoinSet和8个并发许可，等待许可的任务不会被丢弃；
reconfigured共享任务集合、环境和结果通道。shutdown关闭准入、abort并join所有任务。
异步结果移除Stop/Feedback条目，保留Error、Warning及经过溢出处理的Context；
不会把异步阻止/停止结果追溯应用于已经完成的Turn。

结果消费是另一条真实主循环链路：
`core/src/session/turn.rs::run_turn` →
`core/src/hook_runtime.rs::drain_async_hook_results`。
新用户prompt前消费旧结果并记录上下文；采样和工具结束后消费则注入当前pending队列。
Warning另发事件。`should_emit_hook_notification`只允许非builtin同步运行，因此异步
Hook不能直接复用当前CLI的同步HookStarted/Completed展示。这里不需要复制官方遥测。

实施前Corki的discover拒绝async，StopCommand无执行模式，run仅有同步通路，
Runtime没有异步Hook owner；以上缺口现已接入，不能继续用该历史描述判定当前代码。
实现需同时补模式/授权身份、会话级任务所有权、结果安全边界和关闭顺序；后台任务
不得在repository关闭后写账本。现有持久化批次的未知副作用禁止重放仍须成立。
新增模式不能复用旧同步请求身份，也不能让配置刷新取消已获准的运行或误授权新命令。

原生已读测试：`engine/command_runner_tests.rs`中的
`async_hook_result_survives_runtime_reconfiguration`、
`async_hooks_limit_concurrent_processes_without_dropping_waiting_jobs`、
`shutdown_aborts_in_flight_async_hooks_without_delivering_context`。
这些是待映射的验收场景，不是Corki已通过的测试，也未在本轮运行原生Rust测试。
Corki还须用真实Runtime证明Turn不等待异步命令、延迟结果的历史顺序、关闭不晚写，
并验证异步输出不产生同步阻塞反馈或同步CLI生命周期通知。

## 3. F1 Hook 冷历史显示：纠正未经原生调用链支持的缺口

最新源码证据推翻此前“必须新增冷历史Hook完成回放”的实现要求：
原生`rollout/src/policy.rs::should_persist_event_msg`对HookStarted/HookCompleted
明确返回false（不因history_mode切换）；`app-server-protocol/src/protocol/thread_history.rs`
也明确忽略这两个事件。TUI启动恢复取resume_thread返回的turns，
`app/thread_routing.rs`交给replay_thread_turns，并不是从冷存储取Hook通知。
相对地，`app/thread_events.rs::event_survives_session_refresh`保留已经在内存
buffer里的Hook通知，`thread_event_store_rebase_preserves_hook_notifications`
仅证明该内存刷新行为。不能把此处replay/rebase等同于跨进程恢复。

Corki的TerminalUI在内存Transcript保存可见HookRunSummary并重建颜色；
cli/history.py不从hook_executions重建完成通知。后者在此冷启动显示边界与原生
一致，不为“补齐”另建持久显示表，也不重跑Hook补显示。已有执行账本仍服务于
副作用恢复，不能因为原生不保存显示通知就删除。冷恢复中的真实活跃Hook仍需
正常发生生命周期事件；其已提交/未知执行边界仍按A7/A8单独验证。
这只纠正一个错误差异，不关闭整个F1、完整内存线程切换或Hook授权面板等缺口。

## 4. 不再将已有实证列为“完全未验证”

D6合作式关闭五类证据已在memory-close-recheck.md列明；C4/C8的摘要错误优先级
已修复；F3数字/字母/方向键已桥接真实授权后端。剩余原生自定义keymap、审批长
详情浏览等须以现有CLI能力和源码进一步确认，不能因此恢复官方账号或平台入口。
后续F3/F6已接完整审批详情页：Ctrl+A只读浏览、返回保留未决选择，超12k尾部及
40/100列缩放真实PTY四项通过；见approval-details-review.md。不再把详情入口视为
完全缺失；后续direct/Code Mode双审批拒绝、取消及Runtime关闭的真实输入桥接六项
已补验，联合执行测试50项通过。全部视觉帧、patch长证据及其他组合仍需补验。
强杀恢复、Windows实际执行等未验证项与实现缺失分开记录；真实模型选择质量也
不由ScriptedModel/HTTP fixture冒充完成。
