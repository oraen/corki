# C4/C5/C8/E6：摘要取消与流关闭双重失败

## 工具后自动摘要取消与冷开

normal_model_return矩阵现为manual/pre_turn/after_tool×read/close。新增工具后两例
通过真实普通ToolCall执行、Observation落库和input_tokens=6000触发8000窗口的
自动摘要；请求无tools，且包含COMMITTED FACT。模型读流或关闭吞掉取消后，仍
只发一次取消终态，不安装摘要、不继续采样；原输入、ContextItem及精确工具对/
完整原始结果保留，效果一次。关闭后新Runtime无工具注册，resume_pending为空，
历史完全相等且请求/效果计数不变。这是正常取消后冷开，不冒充RUNNING强杀恢复。

初次两例没有超阈值usage，实际走普通采样而触发摘要断言；并行与串行均复现
（050082、bb72f7），属夹具触发条件错误。随后原历史长度预期漏计正常ContextItem
（0ac0d9、f7e6a5），修正为明确的六项类型/位置断言，未放宽原始结果或副作用断言。
本批仅测试修改，四文件8worker最终64通过/3.80秒/退出0（1bb180），无跳过、无
自动worker重启。工具后自动取消这一具体组合已补验，其余Hook交错及A–E仍独立。

## 自动入口的取消吞掉对照补验

重新核对固定Codex compact.rs约290–310行：drain返回Interrupted/TurnAborted时
直接退出，不进入后续replace_compacted_history。Corki既有共享摘要取消保护的
手动入口证据不能替代自动入口，现将normal model return测试扩为自动/手动×
read/close四组合。自动路径通过真实Runtime.stream和超阈值旧历史触发摘要，
明确断言实际请求为COMPACT且tools为空，未以直接调用摘要函数替代主循环。

两新增自动场景中，模型吞掉CancelledError后返回完整文本或关闭正常返回，仍只
产生一个TurnCancelled，不安装CompactionItem、不发ContextCompacted/成功/失败
终态；原始约束与当前输入保留，流已关闭，只发生一次摘要请求，无正常模型续采样。
本批未改生产。四文件8worker、禁止自动worker重启，62通过/4.12秒（eab9e3），
范围为local_compaction_inputs、runtime_compaction、manual_compaction_recovery及
compaction_steer_commit_boundary。初次格式检查发现函数签名需合行，已格式化复查。
各例使用独立临时数据库/home及进程内Event，无共享端口。仍不覆盖工具后自动摘要
取消和所有Hook投递交错，也不将本批局部回归当作完整A–E完成证明。

## 压缩过程中到达的新输入：提交前后与取消

固定参考session/turn.rs在自动压缩后设置can_drain_pending_input为
!model_needs_follow_up：需要续执行时先采样一次，之后才消费排队输入。
compact.rs成功摘要后构造用户保留history再replace_compacted_history。
Corki graph.py在window.prepare返回compacted后应用同一准入顺序；window.py
用一次append提交marker与replacement。Runtime取消时读取已提交身份，只归还
未提交的realtime输入，不把它自动写入下一Turn。

新增test_compaction_steer_commit_boundary.py使用真实Runtime.stream/steer及
SQLite，在摘要生成中、append前、append成功返回前分别用Event屏障注入新输入。
三个窗口均验证：摘要不包含后来输入、压缩后首次请求先续执行、随后输入只入账一次，
同Thread冷开保留相同输入身份和完整原始归档前缀。
另在三个屏障取消，验证未提交输入只归还一次、没有写入历史；只有after_commit
留有完整压缩marker。冷开不复活已归还输入，已安装摘要不再次生成。
这是正常取消后的新Turn，不冒充RUNNING checkpoint恢复；不是OS强杀验证。

首版夹具误取CompactionItem.content导致三失败，已加类型判断；取消版摘要屏障
未释放会阻挡冷开后的合法重试，已在旧worker取消并join后释放。两者均是测试错误，
不是生产缺陷，不记录为生产红绿修复。最终六新场景及pending sampling/input identity/
manual recovery/lifecycle/realtime steering联合53通过、0跳过（dbea0b，6.04秒）。
本轮未修改生产代码。这一明确交错已核验，剩余Hook投递checkpoint交错仍不因此关闭。

## 自动工具循环的安装恢复补验

test_runtime_compaction现沿真实大Observation触发自动prepare摘要，分别验证
正常安装、append前异常、事务内仅marker插入后异常、提交后节点未checkpoint
异常。冷resume保留当前输入与完整原始归档，工具效果仅一次；未提交摘要重采样，
已提交摘要不重采样。最终请求继续使用摘要，预算仍满足原有断言。四场景通过
（480cb5）；注入在裸compiled graph上保留RUNNING记录，不把用户正常取消后的
Turn强行恢复。SQLite事务内异常真实回滚，但并非OS强杀/断电验证。

源码对照仍是compact.rs构建替换history→replace_compacted_history→更新usage，
Corki自动分支见context/window.py::prepare中的marker+replacement一次append；
这与手动compact的恢复判定独立，本批为自动入口新增证据而非从手动共享推断。

上下文全部unit、自动/手动恢复与生命周期、Pre压缩、异步Pre/Post联合489通过
（7c2183，37.45s）；全src/tests的ruff与1220文件format及diff检查通过
（cdf759）。本批未改生产；全部Hook/新输入/取消与安装的交错仍未关闭，
不以此代替完整A–E验收，CLI继续暂停。

## 新增安装事务与非空历史恢复证据

重新追踪固定Codex compact.rs：成功摘要之后构造保留用户消息的新history，
调用Session.replace_compacted_history；不据此假定与Corki采用相同落盘方式。
Corki ContextWindow.compact把marker与保留用户消息放在一次append中，SQLite
append_items经_joined_write进入BEGIN IMMEDIATE；检测同Turn已安装marker时
返回现有窗口而不重新采样。自动prepare的安装分支另行计算预算/注入context，
不能用手动路径测试代替自动入口完整验收。

原test_manual_compaction_recovery使用空历史。现扩展真实用户约束及旧回答，
验证摘要请求包含它们、原归档前缀完全保留、保留用户消息与marker一起安装、
冷resume不重复采样已提交摘要。再增加事务内部已插入marker但尚未追加保留
用户消息时抛错：事务回滚，不遗留半个窗口；重开后重新摘要并完整安装。
此注入发生在真实SQLite连接和事务内，不是仅在append调用前后抛错。

五安装窗口×空/非空历史×三个旧配置/接口组合30通过（d369e8，3.20s）；
新增事务内窗口之前，手动恢复/生命周期/Pre压缩联合54通过（3c9a02）。
最终ruff/format/diff通过（13bdc4）。本批只新增验收，未修改生产实现；
受控异常与冷重建不是OS强杀或真实磁盘断电证明，也不关闭自动压缩与后台
Hook安装交错、全部取消窗口。CLI保持暂停。

## 新核验：取消被 ModelPort 正常返回掩盖

当前 _summary_attempt 仅在 Exception 分支比较进入前后的任务取消计数。
若模型迭代器收到 CancelledError 后仍交出 ModelCompleted，或 aclose 收到取消后
正常返回，则不经过该保护。需要用真实 Runtime 检查是否安装了被取消的摘要。
原生 compact.rs::run_compact_task_inner 的 Interrupted/TurnAborted 直接返回，
不进入成功历史替换；本检查仅涉及普通 ModelPort 和本地摘要安装，不引入专用接口。

实际反例已确认：read收到取消仍yield ModelCompleted，或close收到取消后正常返回，
调用方都未收到CancelledError（07a273，2 failed）。补查取消返回瞬间的数据库，
未观察到CompactionItem；因此不把这两个反例夸称为已证实历史被覆盖。
缺陷是成功返回分支漏掉取消检测，具体外层终态/安装后果不能靠推测替代证据。

修复：在正常返回后、解析/接受摘要之前也检查本次新增取消计数；流仍先关闭。
无新增取消的正常完成不受影响，不清除取消计数，也不改变重试策略。
新例再验证取消异常、唯一TurnCancelled、无TurnFailed/TurnCompleted/ContextCompacted、
原约束和一条TurnAbortedItem保留、无CompactionItem。最初修复后两项断言失败来自
在消费任务写入终态前读取了旧存储快照（82a9b5），改为await消费任务结束后重读；
没有修改生产终态/写入行为来满足夹具。定向 **31 passed，1.83秒**（f867a1）。
扩大所有context单位与文件名匹配*compaction*的集成测试 **704 passed，52.70秒**
（d29c45）；静态ruff/1173文件格式/compileall/77包/diff均通过（c41bed）。
新取消反例走真实手动compact入口；自动摘要使用同一_summary_attempt，但不把共享
函数本身称为新的自动取消故障注入证据。完整A–F仍未完成。

## 后续确认：读流错误与独立 aclose 错误

普通 ModelPort 可以返回实现 __anext__/aclose 的流，不一定是 Python generator。
新实际 Runtime 反例在 __anext__ 抛 ModelError(TRANSPORT)，aclose 再抛 OSError；
contextlib.aclosing 会用后者覆盖前者，原本可由摘要重试循环处理的模型错误
变成直接 TurnFailed。前一轮取消计数修复不涵盖无取消的主错误保护。
修复应保留已经捕获的读流错误，仍执行关闭并记录次要关闭失败；如果没有主错误，
关闭失败继续传播。摘要重试不能因关闭异常丢失原错误分类，不能把关闭失败本身
冒充成功。参考 compact.rs 根据实际 drain 错误分类决定重试的行为不变。

已实施：显式保存读取时的主异常，并在 finally 关闭流；只有已有主异常时，
次要关闭异常记入诊断而不替换它。没有主异常时仍传播关闭异常，上一轮取消
计数保护保留。新例修复前 TurnFailed(error_kind=internal) 且只有一次摘要
请求（29cd4b），修复后通知保留 original summary read failure，关闭顺序
为 [1,2]，相同完整历史重试一次并只安装 VALID SUMMARY。四文件 41 passed
（29a992）；补充两次请求 items 全等断言后继续较大范围回归。
最终上下文单位/压缩集成联合 702 passed（8f4080，55.83 秒），静态检查通过
（ee7ede）。前一轮 701 不包含本次普通 ModelPort 的读流/关闭双重失败新例。

## 源码与反例

参考 Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc 的 core/src/compact.rs：
drain_to_completed 的 Interrupted/TurnAborted 直接返回，不进入摘要成功后的历史
替换。Corki context/window.py::_summary_attempt 等待普通 ModelPort stream 完成，
compact 在返回有效摘要之后才追加 marker 和保留的用户消息；原归档不覆盖。

新增真实 Runtime 反例：摘要流在等待时被 runtime.cancel_active 取消，生成器
finally 设置关闭标志并抛 OSError。Python 会以关闭错误替换 CancelledError；
_summary_attempt 未检查本次等待收到的取消请求，Turn 被改成失败，消费方也
不再收到 CancelledError。四场景（有/无部分摘要 × 有/无工具对与用户约束）
全部违反取消断言（aa951a）；普通取消四例通过。此前 696 项上下文/压缩回归
不含关闭双重失败，不能作为此边界正确的证明。

修复范围：在 _summary_attempt 记录进入时当前 task 的取消计数；流异常返回时，
若本次执行新收到了取消请求，优先传播 CancelledError，并将关闭异常保留为 cause。
无取消的关闭错误仍是错误，不把半截摘要或关闭失败当成功；不改变普通摘要请求、
重试预算、持久化契约或被排除的远程协议。生产改动前已保存此差异和反例。

## 实施与局部证据

上述检查已接入 ContextWindow._summary_attempt，覆盖自动/手动摘要共用入口。
新增取消场景还检查唯一 TurnCancelled、没有 TurnFailed/TurnCompleted 或
ContextCompacted，数据库只有完整原前缀和 TurnAbortedItem，没有 CompactionItem。
其中工具对与后续用户约束先真实落库，摘要请求也检查完整内容；部分摘要文本
不当作有效结果。四文件联合 39 passed（23b8ae，3.69 秒）包含修复后的双重故障。

另加无取消对照：生成器先给 ModelCompleted，再在关闭时抛 OSError，必须
TurnFailed 且不安装摘要，防止修复演变成吞掉任何 close 错误。它没有模拟
HTTP 服务器，测试的是真实 Runtime 的 ModelPort 生命周期契约；两普通适配器
的独立关闭语义仍由 model-stream-close-review.md 对应测试负责。

最终上下文单位与全部文件名匹配 *compaction* 的集成测试联合 701 passed
（2bb9a8，54.76 秒），含新增普通关闭失败对照和全部取消组合；此前 696 是
修复前运行，不能与此混为同一版本。ruff/format/compileall/77 包/diff 检查通过。
本批关闭摘要取消被关闭错误覆盖的具体缺陷，不据此宣称 C 或完整 A–F 全部完成。
