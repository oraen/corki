# 显示历史分页（A/C/F，未完成）

本轮补验与修复：Codex app_server_session/history.rs::apply_older_history_page 检查
loading 和 cursor 归属；app/history_pagination.rs 对非当前 thread 的晚到页不应用。
Corki 原来仅在 _replace_history 检查 welcome anchor，清屏时仍保留可加载状态。
门控实际 repository 读取后调用 TerminalUI.clear，旧实现 closed 断言失败（aad3c7）。
现 clear 立即 stop 并解绑 loader；stop 请求取消但不遗弃任务，应用原 pager.aclose
仍 join Runtime owned read。已撤销加载器的异常不向新画面写 notice。此为本地显示
所有权对应处理，不复制官方服务协议，也不声称原生 /clear 的全部行为相同。

新增 test_cli_paged_compaction.py：真实 SQLite 保存后关闭/冷启动 CorkiApplication，
以1/2/3/5条小页跨越 compaction marker、保留副本、tool call/result。每次旧页前插
检查隐藏副本与误报中断不存在；结果先于调用加载的中间状态也覆盖，最终原用户/结果/
正文唯一且顺序正确，canonical items 不变；NoSampling 模型禁止回放采样。只验证已
持久压缩的显示投影，不把它称为真实模型压缩质量测试。测试初稿误用不存在的 kind
属性，修正为类型检查；该初稿失败不属于生产缺陷。

本批扩大 CLI/冷回放/恢复/分页组合/历史PTY342通过、14条forkpty警告（5cd2dd，
37.01秒）；先前扩大暴露无_history_view的headless渲染入口兼容问题（2ca0fc），
clear现允许该入口，不影响有视图的同步撤销。最终静态1107文件/compileall/77包/diff
通过（9dc3f8）。参考commit未变且干净（8419e6），没有改teach.md或官方服务代码。

当前实现（覆盖下方历史阶段记录）：实际 CorkiApplication + TerminalUI 默认已接入
HistoryPager，初始100条目/5 Turn，按未知 Turn 继续补元数据；旧页由上滚/Home触发，
仅替换初始历史区域，保留 live 输出、草稿和手动位置。未加载旧页的恢复事件通过初始
sequence 上界的身份查询去重，不把新提交结果当旧事实。通用自定义宿主缺少分页接口
时保留原全量兼容回放。全部使用本地 Runtime/SQLite，不新增官方服务依赖。

本轮发现并修复：_read 已提交游标、随后 replay/layout 抛错会跳过失败页。故障注入
replay/layout 两组合在旧实现失败（c3888d）；现先构建候选 frame，再发布 source/cache，
读取或渲染失败恢复该批 items/turns/cursors/seen 状态。前三类失败均可重试并保持旧内容，
不会把显示故障转成模型 Turn 失败；读取取消仍 join 后关闭。四专项通过（d7c90a）。
这是 Corki Python 渲染失败的补救，不宣称 Codex Rust 具有相同异常路径。

真实 PTY：130个持久 Turn，启动仅100条，40/100列中 Ctrl-T → Home → q → 提交草稿，
验证旧页加载、备用屏幕退出、输入历史唯一、无模型采样和 loader 关闭，两项通过
（7b765b）。首次测试因终端差分复用 PAGED_ENTRY_ 前缀导致原始字节匹配超时
（af44f6），改用最旧页独有标记后通过；不将该超时计作生产缺陷。

扩大验证：CLI单位、实际恢复、两种持久页、读取生命周期/准入、冷回放和历史PTY
411通过，14条forkpty弃用警告（a93483，41.66秒）。随后增加源区域/seen游标回滚断言，
最终故障专项4通过；ruff check、1106文件格式、compileall、77包依赖与diff检查通过
（c53ebb）。本批无活动测试；Codex参考commit仍ddf04ad26789d040f9ef6a96736f76602e35a6cc
且工作区干净（daf79c），未修改参考仓库或teach.md。

仍有限制：两类页不是同一时点原子快照；首次补齐 Turn 元数据和身份/压缩前缀查询有
历史相关成本。source/width 变化仍整体渲染已加载历史，尚非逐 cell 增量渲染；跨页
工具配对和压缩投影的完整 CLI 组合、清屏与加载竞态等仍需补验，不称整个 A–F 完成。

CLI 协调器实施前：参考原生 history.rs 的独立页归属合并及 loading/cursor 检查，
初始5 Turn/100 items，遇到未知 Turn 补读元数据。显示只使用已加载可见范围的终态，
避免把尚未加载条目的 Turn 当作空 Turn；全部条目读完后仍允许加载更早的空 Turn。
旧页替换只修改 transcript 中初始历史区域，保留 welcome、后续 live 输出及输入焦点，
按新增渲染高度补偿手动浏览位置。读取失败保留原 cursor，可重试；关闭 join 读取任务。
恢复去重不能只看已载入页：拟记录初始条目上界，用局部身份查询判断恢复事件在该
边界前是否已持久化；不能抑制恢复期间新提交的正文/工具结果，不能为去重全量解码历史。

Turn 元数据分页实施前：原 load_display_snapshot 全量读取 turns，回放以其顺序安放
无 item 失败/取消 Turn。条目页不能替代这些事实。计划独立的 DisplayTurnsPage 和
Runtime.load_display_turns_page：limit 有界、按原 turns rowid 顺序向旧页读取，cursor
绑定 thread 和边界 turn id，读取时解析该 id 的当前 rowid（不直接持久化隐式 rowid）。
状态更新保留原行身份，追加 Turn 不挤入旧页；每页是自己的读快照，跨页并不冻结状态。
沿用生命周期锁/shield/join。随后 UI 合并必须协调两种页，不得直接把独立快照冒充
同一时刻的 items+terminal 原子快照，也不能省略没有 items 的 Turn。

源码追踪补充：Codex tui/src/app_server_session/history.rs::thread_turns_page 使用
ThreadTurnsList（初始5个 Turn、items_view=NotLoaded）；thread_items_page 默认100条。
merge_thread_item_page 遇到未知 turn_id 时继续拉 Turn 页，再按 item id 去重归属；
advancing_cursor 跟踪已见 cursor，apply_older_history_page 检查 thread/cursor/loading
归属。Corki 的独立 Turn/items 页与此分层一致，尚未实现相应 CLI 协调器；不能照抄
原生完整 app-server 协议或把本地读接口说成官方服务依赖。

Turn 页实现：DisplayTurnsCursor 用 thread_id/before_turn_id，事务内查其 rowid 再
向旧页 LIMIT 读取；DisplayTurnsPage 正序返回 DisplayTurn(id,status,error)，包含
无 item 的 Running/Failed/Cancelled/Completed。Runtime 与 repository 端口已接入，
默认100、硬上限200（未来 CLI 初始加载可显式请求原生的5），SQLite 新增 thread 索引。
边界 Turn 不存在、跨 thread/错误 cursor 类型、非法 limit 均拒绝。已有库只增加索引。

验收：新增能力起初因缺少类型失败（b80127）；实现后实际 Runtime 不采样、4个无item
Turn 在 limit1/2/5 下按序分页，旧 Running 更新失败、追加新 Turn、关闭重开后续读仍
不丢不重；每页重复请求一致，第一页已读到的状态不会假装被后续状态更新追溯改写。
同时覆盖 Turn 页读取取消/二次取消/异常/并发关闭，以及首 Turn 活动、第二 Turn 排队
时的即时读取。最终核心/存储/两类分页/显示生命周期/冷 CLI/ephemeral198通过
（e0d579，10.27秒），静态1104文件/compileall/77包/diff通过（4afb8e）。参考commit
不变且干净（15a65b），无活动测试。CLI 分页合并、前插位置、恢复去重仍未接入。

本批有界读取设计：新增 Runtime.load_display_items_page，按持久 sequence 向旧记录
读取，返回页内正序原始 items、thread 绑定的 next_cursor 和页首 remaining_replacements。
后一值供既有 HistoryProjection 正确跳过前页 compaction 的保留副本；不改 canonical
记录，不按 turn_id 切断跨 Turn 副本。只解码 limit 条 payload，limit 有硬上限；倒序
游标使新追加记录不挤进已开始的旧历史遍历。压缩 marker 元数据仍需扫描前缀，暂不
宣称每页数据库 CPU 为常数。复用 Runtime 的生命周期锁/shield/join，不等 Turn 准入。
验收需实际 Runtime/SQLite、并发追加、页边界压缩、游标归属、非法 limit、读取取消。
这是分批接入的实际 Runtime 读取能力；终态元数据分页、UI 前插、恢复事件去重仍未接入，
不能把这个接口单独当成整个显示分页完成。

已实现本批读取契约：sessions/display.py 的不可变 cursor/page、storage/display_pages.py
真实 SQL 读取、SessionRepository 端口及 Runtime 生命周期读取入口均接入。limit 1..200，
默认100，使用 limit+1 判断旧页存在，只解码 limit 个 payload；返回 next_cursor 的
sequence 单调递减且绑定 thread。页首副本状态扫描 compaction 元数据，并通过实际
行数而非 sequence 相减推进，支持旧序号间隔与被外层替换段覆盖的 marker。
新增 compaction partial index，已有库启动自动创建；不改既有 payload/sequence。
这不是持久化新模型协议，也不改变原 load_display_snapshot/CLI 启动行为。

验证：实际 Runtime 逐页读取＋并发追加＋与全量 HistoryProjection 对照，覆盖 limit
1/2/3/7、序号连续/有间隔、运行中/关闭重开后续读，原始条目不丢不重，副本不泄露；
跨 thread cursor、非法 limit/sequence 拒绝，NoSampling 模型禁止任何推理。
页读取并入原显示取消/二次取消/读取异常/并发关闭矩阵及排队 Turn 准入测试。
核心/存储/显示/冷 CLI 与 ephemeral 回归177通过（bffd15，8.40秒），新增冷读与
游标上界后的完整分页专项27通过（62c8d0，2.15秒）。静态1103文件/compileall/
77包/diff通过（33d297）。参考 commit 不变且干净（2df80e），无活动测试。

后续必须接入：Turn 终态（包括无 item 的失败 Turn）的分页/合并、CLI 旧页前插及位置
补偿、已持久但尚未加载条目的恢复事件去重。不能直接对每个 raw 页独立 replay_history
并宣告完成，因为工具关系与终态可能跨页。前缀 marker 扫描仍有历史相关成本，且仅有
条目数上限，不等于总字节或总 CPU 常数界限。完整 A–F 未完成。

当前后续状态：此前扩大回归的恢复竞态已在 tool-result-commit-cancellation.md
记录并修复已证实的结果/claim/模型提交线程取消所有权缺口。本文旧失败记录保留作证据，
不代表仍有相应测试进程。重新核对 pager_overlay.rs 后，跨宽度的 source 字符锚定
不是当前 Codex 已实现的前提，详见 history-viewport.md；分页前插按新增高度补偿
才是原生明确实现且 Corki 仍需补齐的行为。分页本身仍未实现。

原生基准：tui/src/app/history_pagination.rs::request_older_history_page一次请求有限
ThreadItemsList，按thread/cursor判定归属；handle_older_history_page按item id去重，
前插transcript及overlay，不直接重写终端滚动区；处理完整/部分/继续到开头状态。
与模型输入窗口和恢复事实不同，历史显示分页不应删改canonical模型记录。

Corki当前链路：CLI.run → Runtime.load_display_snapshot → repository同一SQLite读
事务内fetchall conversation_items与turns → replay_history → Transcript.calls。
HistoryView只对已载入文本做视口，不是持久分页。状态缺失，长历史有全量解码/渲染成本。

分页实施约束：conversation_items有sequence索引但允许没有TurnRecord的旧记录；
CompactionItem.replacement_item_count可覆盖带旧turn_id的副本，不能简单按turn截断
后重复运行_visible_items。工具call/result跨页与失败终态必须避免伪报未完成；无items
的失败Turn也要显示。需明确源cursor、可见投影、终态与实时item去重，不能只新增未接入
Runtime的LIMIT接口或裁掉旧记录当作分页完成。

先行并发核验：Runtime._load_display同时持_turn_lock和_lifecycle_lock。_start_turn
在_turn_lock中等待前一worker完成，排队准入可能阻塞纯读取。原生页请求独立异步发出，
不应等当前Turn完成。计划以真实Runtime阻塞第一模型、排队第二Turn，再读snapshot；
读取应立即得到已提交第一输入/Running终态，不能采样第二模型或取消第一轮。
若复现则显示读取只保留生命周期锁和既有owned-read join，SQLite事务保证快照一致。

已复现并修复先行读取阻塞：267934在模型仍阻塞、第二Turn已占准入锁时读取超时。
_load_display移除_turn_lock，仅持_lifecycle_lock并保留shield/join。新增实际Runtime
测试同时覆盖raw显示读取与含终态snapshot；返回只有已提交第一输入，snapshot仍为
Running，第二模型未采样，首Turn不被取消；解除gate后两Turn均正常完成。

显示读取取消/关闭、数据库一致性快照、真实恢复、reasoning历史和输入边界首批71通过
（6b7a97，8.54秒），扩充raw变体及核心/存储单位后176通过（82fae4，9.57秒）。
静态1096文件、compileall、77包/diff通过（a043bc）。参考commit仍
ddf04ad26789d040f9ef6a96736f76602e35a6cc且无改动，无活动测试。
这是读取并发修复，不是分页实现；后续仍需实现有边界的显示投影、分页加载及跨页回放。

显示投影先行阶段：_visible_items当前每次调用都把replacement_items重置为0。
若将数据库批次各自交给它，分页边界落在CompactionItem后会把摘要保留副本误当新历史。
先将这段过滤提为有状态的HistoryProjection，真实replay_history使用同一实现；提供
剩余副本计数的显式初值，使未来SQL页边界可以携带正确状态。验收对同一原始历史的
任意分割都与全量投影相同，覆盖连续压缩、跨Turn副本、retained user和工具call/result。
这是可执行的过滤组件，不是数据库分页完成；游标查询、终态分页及UI加载仍需后续接入。

本批实现：cli/history_projection.py 的 HistoryProjection.feed 按时间正向消费批次，
保持剩余替换数量；现有 replay_history 的过滤入口已委托此组件。返回立即消费的 tuple，
避免部分消费生成器与后续批次交错。任意三段分割、跨边界恢复及非法初值测试通过。
这保持已有全量回放语义，不声称原有全量回放会泄露副本；新增测试初次失败为模块缺失，
之后修正了测试 CompactionItem 缺少 through_item_id 的构造错误，不算生产缺陷证据。
投影/回放及冷 CLI、手动压缩恢复、保留来源组合47通过（c65099，7.42秒）。
静态1098文件、compileall、77包、diff通过（d202ed）。

扩大回归未全绿：首次372通过3失败（1015cf），其中两个为上述测试构造错误；修正后
374通过1失败（e82c63，19.46秒）。两次均在已有
test_recovery_and_concurrency.py::test_mixed_output_commit_recovers_without_repeating_tools
[False-True] 中出现 stable ToolResultItem id 内容冲突，恢复以 TurnFailed 结束。
该参数为 via_cli=False、streamed_items=True，不经过 CLI 显示投影。独立运行通过
（e1a718）；仅进程内包装存储追加、失败时打印字段差异的独立诊断也通过（977d05）。
因此存在批量/时序相关未解决故障，不能将独立通过视为修复，也未证实具体根因。
下一步优先在相同批量条件采集冲突字段，核对流式工具结果已持久化后恢复重构的契约，
不得通过忽略内容冲突、吞错或重复工具执行消除失败。无测试进程仍在运行。
