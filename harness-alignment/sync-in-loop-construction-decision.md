# E4：运行中事件循环的同步构造兼容决策

## 当前结论：用户已批准，生产入口和调用迁移已验证

2026-09-22 源码复核：`src/corki` 内直接构造 `LangGraphRuntime` 的生产入口
只有 `cli/main.py::build_application` 的同步 `create`（CLI `main`
通过 `build_application_async` 的 `create_owned` 提供 owner；其它无 loop
的直接同步调用沿用兼容路径），以及 `memory/agent.py` 的 `await acreate`。
未发现第三条运行中 loop 无 owner 的生产调用；`runtime.py::acreate`
自身仍经 `create_owned(cls.create, kwargs)`，`construction.py` 的同步
fail-fast 在资源分配前。这个入口枚举不声称第三方宿主也已迁移，亦不把
下文原始“待批准/待回归”记录误当当前状态。

迁移后只读AST对比（19710退出0，f8e8ac）：340个变更测试文件，归一化Await、
async函数、Runtime.acreate名称及脚本文本的同一替换后，仅两个文件有其它
AST差异：新增准入测试，以及mcp_bound_http唯一同步错误断言的asyncio.run。
其余338个文件不含其它AST变化，原故障点/断言保持；此证据不代替行为回归。
README已说明async宿主必须await acreate、无loop同步兼容和成功后aclose。

第二轮迁移完成：仅在helper全部引用均为异步函数直接调用且无同名歧义时，
转换153个helper/526个调用点/131文件（fdb81b）；混合调用runtime_for另行
迁移，唯一同步非法输入断言用asyncio.run等待原异常。13处内嵌Python脚本
逐个解析，确认构造在async函数内后迁移，保留原终态/崩溃点/PTY行为断言。
辅助迁移命令首次存在括号语法错误，9f0339退出1且无修改；修正后3baa99退出0。
剩余17处create文本为同步测试/同步准备及新拒绝专项。变更文件format与lint、
compileall通过。第一批531处，第二批153个helper，
另一个混合helper和13处脚本；本段计数不代表跨场景行为验证已全部完成。

重新评估12核/18GiB/38%空闲，无pytest/execnet，沿用独立tmp/端口/catalog与
只读compiler隔离，采用8 workers/loadfile/禁重启刷新非CLIunit+integration。
日志与JUnit直接写 /private/tmp/corki-construction-regression.BHOX16/。
测试期间不修改生产/测试；实际结果待句柄终态，不能以迁移/编译成功代替。

用户明确同意此调整，并要求目标范围内合理的Codex核心对齐决定自行落实，
无需反复确认。下方等待批准记录已失效，不再作为阻塞依据。
construction.py现对运行中loop且无_construction owner的同步调用在进入factory
前抛RuntimeError，明确要求await LangGraphRuntime.acreate；无loop及有owner
路径保持原有回滚。两例新准入测试在修复前触达ProcessManager哨兵失败，
60724实际退出1（755d77）；修复后完整constructor文件27通过/0.95s，
50092实际退出0（2e32a5）。断言借用/自建两种情况均零资源分配、零任务增加、
registry不变、无文件副作用；保留原同步成功和故障回滚测试。

首轮机械迁移只修改AST中async函数直接调用：531处/245文件（7f3d89），
同步helper、字符串内子进程脚本及同步API专项未替换。lint通过、247个变更
Python文件format无变化；仍有184处create文本命中，需逐类确认合法同步路径
与必须迁移的helper/子进程，不宣称调用迁移已完成。生产已变，25019全量
仅是修复前基线，后续需要覆盖迁移后的全量；不恢复CLI视觉/交互对齐。

2026-09-14 迁移影响复核：src中的直接LangGraphRuntime调用为CLI main.py:91
同步create与memory/agent.py:262的await acreate。测试/进程fixture另有大量
同步create调用，不能全局文本替换：应区分运行中loop、普通同步构造、专门
验证同步API的测试及字符串内子进程程序。尤其冷恢复/真实进程退出fixture
必须保留原故障注入位置，不把迁移变成不同窗口的测试。
已再次向用户明确询问是否允许fail-fast API调整；自动goal续行不是确认。
当前没有测试进程在运行，既有非CLI全量与3个组合E2E已通过；不靠重复回归
解决这个兼容性选择，也不在批准前修改生产接口。

本轮重新检查当前源代码并独立复现，不靠历史审计推断。
core/construction.py::synchronous_rollback 在无loop时收集closers并asyncio.run回滚；
在已有loop且无_construction owner时直接调用factory。runtime.py在Graph构造前
已创建自有模型并登记aclose，但该分支没有可等待的回滚owner。
create_owned/acreate会捕获构造异常并await _finish_rollback。
Codex session/session.rs::Session::new为异步初始化；行为对齐要求资源有owner，
并不要求Python同步API逐字模仿Rust。

只读故障诊断（c82560，退出0）：临时目录、实际Runtime构造、注入最后Graph
构造ValueError、自建模型aclose记录。sync_in_loop在异常返回时closed=False，
async_owned同样故障closed=True。诊断结束显式关闭模型并清理临时目录，
不创建真实供应商连接、不改变生产代码。该证据仅针对明确的自建模型路径。

保证“异常返回前异步回滚完成”不能用同步函数阻塞已有loop实现；任意插件/宿主
closer可能依赖该loop，不可擅自移到另一个loop。无owner后台cleanup也不满足
交付前清理完成。延迟初始化整套资源会改变更大的启动/错误边界，不能当零影响修复。

建议需用户确认的兼容调整：已有运行中loop、未提供construction owner时，
同步create在分配资源前报明确错误，要求await acreate；无loop同步create不变，
已有owner/acreate路径不变。须同步迁移受影响调用和补准入/零资源分配测试，
不能仅改抛错而留下仓内调用失败。用户确认前不实施此API变更。

本轮仅诊断与记录；15504通过全量基线没有因生产修改失效。该项仍开放，
不能把其它A–E可推进工作一概标阻塞，也不能在用户未同意时宣称已修复。
