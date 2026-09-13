# 单元/集成整体基线结果及后续修复

## 当前e2e补验：220通过，无跳过

单元/集成77351结束后，启动完整`tests/e2e`目录，保留null keyring和bundled
sandbox显式配置，`-o addopts='' -o faulthandler_timeout=60 -q -rs --tb=short`。
启动前ps确认没有旧pytest；运行期间没有修改生产代码或测试。
该目录覆盖真实PTY交互及示例，使用独立临时工作区和CORKI_HOME。

会话63766已正常退出0（97ebcc）：**220 passed，217 warnings，398.81秒**，
无失败或跳过。警告来自Python 3.12的forkpty多线程弃用提示：子进程可能死锁；
本次未发生停滞，不隐藏警告，也不将本次通过解释为该平台风险已消除。
覆盖完整审批目录（含详情浏览）、模式/计划、历史、Hook、MCP登录、排队/停止
恢复、流正文/表格/交错工具及用户输入，不将这些现有测试泛化为完整视觉等价。

本批与下方14193通过的单元/集成是两次独立运行，不合并冒充单次全仓结果。
63766及77351均为终态，停止轮询；下一步回到A–F剩余实现/验收，不重启基线。

## 当前全量刷新：14193通过、1环境跳过，正常终态

在联合类型准入修复、默认无界事件流与混合压缩冷回放补验之后启动新的完整
单元/集成运行。启动前ps确认没有旧pytest进程；旧29459等均为历史终态，不重启。
五份CORKI_TEST_PRE_*二进制SHA-256重新核对与下表一致（5f6cd0），当前bundled
sandbox及null keyring继续显式配置。生产/测试在采集后保持不变。

命令主体：`pytest tests/unit tests/integration -o addopts='' -o faulthandler_timeout=60
-vv -rs --tb=short --junitxml=/private/tmp/corki-core-baseline.hmFlJb/results.xml`。
逐项输出保留当前测试名，单项超过60秒只打印诊断、不自动杀测试。范围不包含tests/e2e。

启动d34a63，会话**77351**/PID**38230**已正常退出0，停止轮询，不重启。
终态：**14193 passed，1 skipped，2067.68秒**。实际JUnit报告已解析核对
（824e9c）：tests=14194、failures=0、errors=0、skipped=1。
唯一跳过是test_non_utf8_worktree_backlink_and_lossy_config_key，原因是当前
文件系统拒绝非UTF-8文件名，不视为该平台场景已验收。没有跳过旧compiler兼容测试。

本次正常越过旧Stop pending恢复中断附近及全部集成末尾，运行期间生产/测试未改。
这证明当前单元/集成基线完整通过，不追溯声称已取得旧停滞的异步栈级根因。
结束后ruff（39c8f3）、format（1183文件，94b9a6）、compileall（ba390b）、
依赖检查（77包，894a95）通过。A–F尚有独立实现/验收差异，本基线不自动核销它们。
下文29459的19项失败/中断是旧运行记录，不是本次结果。

## 历史终态：29459完整基线被中断，19项失败及后续修正

29459/PID51201长时间停留在事件等待，独立Hook文件60通过后，发送正常SIGINT
尝试取得诊断；第一次未退出，第二次后pytest退出2（915424）。没有强杀或重启。
汇总：**19 failed，13643 passed，1 skipped，2368.98秒**，KeyboardInterrupt。
这是未完成的全量运行，不能当作完整基线；唯一skip为文件系统拒绝非UTF-8文件名。
16项失败来自test_mcp_pending_call_generation的model_direct/model_nested、等待中
替换配置分支；3项来自test_stop_pending_recovery的双handler/same配置分支。
中断位置为asyncio.runners，尚未定位此前不退出的Python异步任务，停滞仍需追踪。

源码复核：Codex parallel.rs先wait_until_ready再锁与dispatch；McpHandler.handle_call
才prepare_mcp_call获取当前binding。原测试把模型readiness当成已获执行binding，
与新增分阶段调度不符；host直接call已选binding的稳定性断言仍须保留。
Hook原生dispatcher用FuturesUnordered并发，旧pending夹具在首个complete就取消，
却预设其他handler未claim/未执行，形成竞态。拟在第二handler的claim之前明确挂起，
固定原测试要验证的“一个已提交/未知、其余未开始”窗口，保留所有恢复安全断言。

上述两类测试现已修正：Hook在第二handler claim之前挂起，不再依赖进程完成竞态；
MCP区分readiness后的当前generation选择与host已选执行binding，原host失败/重载
不重绑检查保留。没有修改生产调度或引入官方协议。Hook pending整文件60通过
9.86秒（3d024f，76760最终e7da14退出0）；MCP generation/readiness两文件
84通过9.99秒（72dece，44365退出0）。静态9648a8：ruff、1181文件格式、
compileall、77依赖及diff检查通过。这些局部回归不改写原中断全量结果。
当前无运行中的回归句柄；全量剩余未执行部分及此前事件等待停滞仍需验证。

### 中断后补验及位置校正

当前完整collection为14086项（71268a）；原中断已报告13643+19+1=13663项。
当前收集序号13664为test_stop_pending_recovery[2-same-True-True]（a86ef1），
与此前三个失败紧邻，强于仅凭lsof残留文件推测正在执行test_stop_hook_recovery。
两次改动没有增删用例数量，但未保存原运行逐项日志/异步栈，因此这是收集顺序
推断，不宣称已经拿到停滞根因栈。原lsof定位仅作资源残留线索。

按路径从test_stop_batch_admission.py至tests/integration末尾全部补验，578项，
41206退出1（fd830d）：577通过、1失败，106.90秒，没有再次停滞。覆盖中断附近
及后续范围，但不是全量一次通过。新增失败为test_stop_hook_json[both]将JSON/TOML
并发命令的文件效果顺序写死。原生dispatcher的FuturesUnordered并发、结果按配置
顺序汇总；修正为效果各一次的排序比较，并新增HookStarted/HookCompleted的原配置
顺序检查，保留独立授权、冻结配置、自授权拒绝等断言。无生产代码变动。
JSON/控制/并发/冷恢复/pending五文件143通过28.21秒（fadd0e，45604退出0）。
静态322390：ruff、1181文件格式、compileall、77依赖、diff全部通过。补验与专项
有重叠，不累加成唯一通过数，也不将原中断状态改写为全量通过。

### 此次运行与诊断时间线

在 tool-readiness-gate-review.md 的生产调度改动与专项验证之后，启动完整
`pytest tests/unit tests/integration -o addopts='' -q -rs --tb=short`。
当前 compiler/null keyring 和下方五个 CORKI_TEST_PRE_* 均显式配置；五份旧二进制
SHA-256 已重新核对与表格一致（6f678f），没有替换 bundled compiler。
启动句柄 **29459**（26b3f1），首次实际轮询10a41e仍活跃，约15%，一项skip，
尚无失败标记。skip身份须等最终 -rs，不根据位置/数量提前判定原因。
本次不含tests/e2e，也不把测试执行百分比当作goal完成率。
运行期间不改生产/测试；本次仅记录进度与只读核查取消/恢复链路。
后续同句柄45秒轮询5ff215到约43%，再45秒5d1031到约45%，仍活跃、没有
失败标记。未取得退出码/-rs终态汇总；这不是全量通过结论，也不重启该进程。
再续轮询29459：1a096c到约47%，78d672/755ae5两次45秒等待推进至约50%，
仍返回同一活动句柄，没有新增失败标记。本轮是verified wait；未修改生产/测试，
没有停止或重启进程。此前一项skip仍待最终汇总确认，不能当作已经通过。
最新verified wait仍为29459：0c361c约51%，399824/26d4d4两次45秒轮询推进至
约54%，无新增失败标记、无终态。生产/测试保持不变；继续等待原句柄。
后续39a429确认约56%；同句柄9bc4c6/b7911d两次45秒等待推进至约59%，
仍活跃且无新增失败标记。本轮继续verified wait，未改生产/测试或重启进程。
本轮badf9b确认约60%；同一29459经d0e7cf/397b4a两次45秒等待推进至约64%，
仍活跃，无新增失败标记、无退出码。继续保留原进程与生产/测试快照。
最新同句柄c094b5有持续输出；36d400/6ba363两次45秒等待推进至约67%，
仍无新增失败标记、无终态。继续verified wait，不因速度变化重启；生产/测试未改。
最新重要变化：01bc16约68%；528615在约69%出现多项F，随后推进至70%，
c1c49a同句柄45秒等待到约71%，进程仍活跃。失败详情尚未输出，不能预判为
夹具或生产问题，也不能宣称全量通过。继续完整运行，终态后按实际失败名单定位；
本轮未修改生产/测试、未停止或重启29459。
再续verified wait：1f5a27约72%，64eee9/acaa27同句柄两次45秒等待推进至约75%。
此前F仍待最终详情，本轮新输出未见额外F；进程未终止。继续原运行，生产/测试未改。
后续1828ec约75%，ee9980/b3a399两次45秒轮询仍为29459，推进至约76%且
持续输出。本轮未见新增F，已有失败详情仍未汇总；未重启，也未修改生产/测试。
最新64f688约77%；同一29459经33ea86/186d52两次45秒轮询推进至约81%，
仍有输出且未见新增F。已有失败待终态定位；继续verified wait，生产/测试未改。
本轮21c0f5约82%；a41575/7b8c6d两次45秒轮询仍返回29459，推进至约83%。
持续输出、未见新增F，已有失败待最终汇总；生产/测试不变，未重启进程。
最新8f812e约84%；f574bb/2b55d1同一29459两次45秒轮询推进至约87%，
仍活跃、未见新增F、无退出码。继续verified wait，已有失败待终态汇总，生产/测试未改。
最新152be0约87%；a6c63f/6a8081同一29459两次45秒等待推进至约92%，
仍持续输出、本轮未见新增F。没有最终退出码或失败/skip汇总，未重启或改动生产/测试。
后续276f64/383e97推进至约96%；1c0c2b又出现失败标记。c09d7a同句柄
45秒等待没有新输出，仍返回活动句柄29459，尚无失败详情或终态。保留原运行，
不预判失败原因；生产/测试未修改，不能据此宣布基线通过或目标完成。
继续29459轮询9b778d/9ae7d7/3da0a2/d9cdca/4d576a均无新输出，句柄仍活跃。
只读ps确认PID51201；lsof最后一组测试库为test_concurrent_batch_cold_rec4，
这只定位持有资源，不直接证明当前Python任务位置。系统sample（2410b1）显示
主线程在select_kqueue_control/kevent等待，未取得Python异步任务栈，不能确定死锁根因。
不修改生产/测试、使用独立临时目录及-p no:cacheprovider进行隔离诊断：
test_stop_hook_recovery.py的并发冷恢复[False-1-Stop]单独1通过0.54秒（2ebad0）；
整个文件60通过13.62秒（f316c9，42202已退出0）。这不能替代原全量结果，
也不能排除整轮运行中的顺序干扰/取消残留。原29459未停止或重启，已有失败尚待详情。

注意：下方全量发生在SubagentStop实现之前。本批新增的类型化来源、matcher和
执行分流以subagent-stop-review.md的298项专项回归为证据，不把旧全量冒充新版本全量。

## 当前刷新终态与失败修正：不要与下方历史结果混用

2026-09-12，摘要取消/关闭错误保护、Hook样式、审批数字提交之后，启动：
`pytest -o addopts='' -q tests/unit tests/integration --tb=short`。
句柄 **10586**（启动6f52bd）已退出1，最终9ed464：
**2 failed，13862 passed，7 skipped，2000.36秒**。运行期间没有修改生产/测试。
两个失败均为test_cli_pending_history.py的model_commit/tool_history冷恢复场景；
独立重现bc50aa仍2失败。旧UI夹具首次read_message就抛EOF，新增恢复composer
会把它视为恢复期间的显式取消，因此缺后续结果/最终答案；不是重复执行缺陷。

终态后仅修测试夹具：恢复busy期间await Future，回到idle后才EOF退出；保留全部
持久历史、一次工具执行/采样及重排断言，新增无Turn interrupted与未取消断言。
没有关闭恢复composer，也没改生产EOF取消语义。与recovery_and_concurrency及
test_resume_input联合**34 passed，12.29秒**（c4521b，session71908随后50ce3c退出0）。
此局部复验不把原全量结果改称“一次全绿”，也不再重启33分钟全量替代已完成的
失败分析。修正后静态1c350b通过：ruff、1163文件format、compileall、77包依赖、diff。

本次设置当前CORKI_TEST_SANDBOX_COMPILER及null keyring，未设置下表五个
CORKI_TEST_PRE_*，因此这些用例在全量中的skip不能冒称已验证。
等待期间已独立补验这六项（不再等待全量终态）：五份旧二进制SHA-256再次核对
与下表一致（463edf），显式设置全部五个PRE变量，以`-p no:cacheprovider`
隔离pytest缓存写入，未改生产/测试、未重启全量。session53170退出0，
225f45：**6 passed，0 skipped，0.60秒**。精确选择为：

- test_metadata_compiler_contract.py（runtime/context两项）
- test_native_read_helper.py::test_old_compiler_rejects_native_helper_contract
- test_native_patch.py::test_old_read_only_helper_cannot_silently_accept_patch
- test_patch_approvals.py::test_old_patch_compiler_rejects_approval_protocol
- test_patch_deltas.py::test_old_approval_compiler_rejects_delta_contract_before_write

这些证明旧协议不能静默接受新语义，不替代全量其他用例；不要把补验6项与全量
重复相加。原全量已终态，详情及两项夹具修正见本节开头。
另独立运行非UTF-8 worktree用例（960d2a）：文件系统返回EILSEQ，明确
1 skipped / filesystem rejects non-UTF-8 filenames。该平台路径未得到执行证明，
不能用六项旧compiler成功覆盖这个限制；不通过放宽该测试来凑零skip。

## 最新 CLI / 示例端到端回归：166 项通过

2026-09-12，`pytest tests/e2e -o addopts='' -q -rs --tb=short`：
**166 passed，290.12 秒，0 skipped**。session 38469 已退出 0，
最终输出 530bad；不是下方 unit/integration 全量的追加计数。
使用当前 CORKI_TEST_SANDBOX_COMPILER 和 null keyring；示例采用
ScriptedModel、临时 workspace/home，MCP 登录采用 mock transport 和本地
回调，仅验证通用 MCP OAuth，不访问官方账户服务。

本批重跑现有完整 tests/e2e，未修改生产代码或测试。覆盖 40/100 列的
审批显式选择/拒绝/取消、并发确认与草稿恢复、运行中新输入、启动取消、
计划正文及可变 tail、流式表格/工具交错、历史、压缩期间输入，以及三个
核心循环/内置工具/扩展示例。它证明这些已编写场景的实际终端或 Runtime
行为，不证明真实模型选择质量、所有屏幕像素或全部 F 需求已对齐。

同批静态输出 6c6ac6：ruff check、ruff format --check（1140 文件）、
src compileall、uv pip check（77 包）及 git diff --check 均通过。
下方 13574 项全量发生在后续插件指导与记忆所有权修复之前，不冒充最新
生产状态的完整 unit/integration 回归。完整 A–F 仍待逐项验收。

## 最新全量结果：默认可选次数限制之后，已通过

session **72348 已退出 0**，最终输出 **13c4ff**：
**13574 passed、1 skipped，1954.44 秒（32:34）**。
唯一 skip 为 tests/unit/config/test_project_trust_selection.py:164：
`filesystem rejects non-UTF-8 filenames`。已核对源码，仅实际 rename 返回
EILSEQ 时走此分支；该文件系统场景仍未验证，不当作通过或产品不适用。
本次启用了下方五份真实旧 compiler，旧协议拒绝用例没有再因缺少配置跳过。

本次范围为 tests/unit 和 tests/integration，不含 tests/e2e；不能用此结果
替代真实 PTY 或 A–F 全部需求的逐项验收。运行期间未改生产/测试。
下方运行观察均为本次已结束进程的历史，不再继续轮询或重启此句柄。

### 本次运行过程（历史）

最新 verified wait：d1056e 确认约 86%，两次各 45 秒等待 7ef88f/1912f8
均返回同一活动 session 72348，已到约 89%，无新增失败标记。没有终态，仍等待原进程；
本轮未修改生产/测试。下方为之前的进度观察。

后续 verified wait：1f2381/556663 的同一 72348 输出由约 30% 推进到 43%；
再等待 45 秒，497427 仍返回活动 session，推进到约 45%，无新增失败标记。
仍未获得终态或 skip 原因汇总，不停止/重启。此期间只读记忆来源审计及更新
本记录，未修改生产/测试；该百分比只代表已执行测试计数，不代表目标完成率。

启动输出 8f638e，exec session **72348**。最近实际轮询 e689f5 仍返回同一
活动句柄，输出约 10%，已出现一项 skip、尚无失败标记；没有退出码，不能
宣称全量通过。后续继续轮询此句柄，不因观察超时重启。旧 session 1908 等
均为已经终止的历史运行，不是当前任务。

命令为本文原全量 `pytest tests/unit tests/integration -o addopts='' -q -rs --tb=short`，
除当前 CORKI_TEST_SANDBOX_COMPILER 及 null keyring 外，启用下方表格全部
五个 CORKI_TEST_PRE_* 环境变量，对应路径不变。启动前重新核对五份二进制
hash（93e411），均与已记录 receipt 一致；参考 commit 仍为
ddf04ad26789d040f9ef6a96736f76602e35a6cc，参考工作树干净。Corki 有 1134 条
混合改动状态记录，全部保留，不把它们误归为本批新增。

本次包含新的默认长循环、长任务冷恢复、26 Step 提交窗口恢复及普通压缩
组合，不包含 tests/e2e。启动后本批仅编辑运行记录，未修改生产/测试。
终态与 -rs 汇总现已取得，见本节顶部；本次通过不能代替
未关闭的 A–F 行为项验收。

## 最新补验：真实旧编译器拒绝契约

本批不修改生产代码/测试，也不重跑或重述历史全量结果。旧基线输出没有
保留详细 skip 汇总，不能只凭七项数量认定其身份。当前按源码定位后，
先按基线环境补跑六个 native/metadata/patch/bundled 文件，取得
67 passed、6 skipped（ca85bd，11.66 秒），明确六项缺的是旧版编译器配置。

审计中记录的五份真实旧二进制仍存在，已逐一将 SHA-256 与同目录
manifest.json 核对一致（8eeaa2）。使用原件，不重新构造假旧协议 helper，
不覆盖当前 bundled compiler、不修改默认环境。路径均位于 /private/tmp：

| 本次命令环境变量 | 旧二进制相对路径 | SHA-256 |
|---|---|---|
| CORKI_TEST_PRE_METADATA_COMPILER | corki-metadata.XnlekR/source-bundle-before-211/corki-sandbox | 47fecbec061012bb9fee22eda2e05abc589ae0c15ad665de34a0e64b2f44b2a0 |
| CORKI_TEST_PRE_NATIVE_FS_COMPILER | corki-native-fs.w6KpJ7/source-bundle-before-213/corki-sandbox | c014b4c6925536107c3c4a117833c47b138eb31043fafaa3c43f51293c624410 |
| CORKI_TEST_PRE_NATIVE_PATCH_COMPILER | corki-native-patch.jwlual/source-bundle-before-214/corki-sandbox | 57b50abc2d7236c6334b7d243d589095417871b04ce1480bb179522e900906b0 |
| CORKI_TEST_PRE_PATCH_APPROVAL_COMPILER | corki-patch-approval.FRW1tt/source-bundle-before-215/corki-sandbox | d55114af2529024a835f49a8aefd74c8e1eac24edfd17a5cee5d902f3036300d |
| CORKI_TEST_PRE_PATCH_DELTA_COMPILER | corki-patch-delta.e7I0yC/source-bundle-before-216/corki-sandbox | 8a389de141eceb13d4fb8fe6636738f0ef6c77214e2ef3ed6514eebb6998dc15 |

配置上表并保留本文原 CORKI_TEST_SANDBOX_COMPILER/keyring 环境，运行：

```sh
.venv/bin/pytest tests/integration/test_native_patch.py tests/integration/test_patch_deltas.py tests/integration/test_native_read_helper.py tests/integration/test_metadata_compiler_contract.py tests/integration/test_patch_approvals.py tests/integration/test_bundled_execution.py tests/unit/config/test_project_trust_selection.py -o addopts='' -q -rs --tb=short
```

结果 109 passed、1 skipped（d9d090，11.97 秒），进程 55591 已退出 0。
六项旧协议拒绝已获实际证据：metadata 两入口在采样/发布上下文前拒绝，
FS/patch/approval/delta 各旧字段拒绝，不进行未知契约写入。不是通用 OAuth
或 OpenAI 官方服务依赖，也没有重新引入任何排除协议。
剩余 test_non_utf8_worktree_backlink_and_lossy_config_key 在真实 rename
得到 EILSEQ 后跳过；不伪造该结果，不判为产品“不适用”，需支持字节文件名
的文件系统另验。这一补跑不能替代最新完整 A–F 基线。临时原件以后可能被
系统清理，复跑前应再次检查路径和 hash，不把这些路径固化为产品依赖。

启动命令：

```sh
CORKI_TEST_SANDBOX_COMPILER=/Users/corki/PycharmProjects/corki/src/corki/_native/sandbox/corki-sandbox PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring .venv/bin/pytest tests/unit tests/integration -o addopts='' -q --tb=short
```

首轮进程 8299 已退出 2（875a99）：CLI 与 core 的 test_user_input.py 在默认
pytest 导入方式下同名冲突，未执行测试主体。将 core 文件移动为
tests/unit/core/test_user_input_broker.py，保留全部三个测试与断言，仅改模块说明；
未删除缓存规避问题、未改变全局 pytest 导入方式或排除任何用例。

重新启动的进程 session 1908（启动 73ec3f）已退出 1，最终结果 61fb44：
13479 passed、1 failed、7 skipped，1944.81 秒。失败是
test_recoverable_parallel_error_preserves_barrier_and_call_order[False-False]
对两个并行 handler 启动先后作了不成立的严格断言；实际 fast 先于 slow 进入
execute。七项跳过的具体原因尚未核实，不视作已验证，也不假定与旧基线相同。

核对并行契约后修正测试：允许两个 parallel handler 任意启动顺序，保留
fast 先完成、slow 等待 release、exclusive tail 不越过屏障、模型请求与历史
结果顺序不变的全部检查。新增 fast_starts_first 参数强制反序启动，覆盖
batch/streamed × 错误结果/异常，共八例。不改生产调度以迎合启动顺序。

同时修复后述显示模式缺口。首批 25 passed（f7a6e1，终态 cf2f63）；扩大
storage/history/display/mode/task ownership 联合 216 passed、0 skipped，
13.35 秒（9a8039）。静态 bb04d8 全通过：ruff check/format、compileall、
uv pip check、git diff --check，1137 文件、77 包。所有本批进程已退出。
未重跑完整 unit/integration，不能把局部补跑写成整体一次全绿。

范围仅 tests/unit 与 tests/integration，不含 tests/e2e 的真实 PTY，不是完整
A–F 验收。等待期间确认 proposed_plan 事件/显示链尚缺，详见
stream-animation-design.md 新增章节；不要把 update_plan 清单渲染当作最终计划展示。

基线收集结束后新增 test_proposed_plan_history_mode.py；其独立运行确认两例
模式丢失失败（fe010a），现已随上述 216 项补跑通过。session 1908 不包含
该新文件，只代表启动时基线，不代表后续改动。完整最终计划显示仍未实现。
