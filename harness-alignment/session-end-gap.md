# A4/E7：SessionEnd 关闭生命周期缺口

## 当前补验：反序完成与局部失败

关闭账本六窗口已补验：test_session_end_commit_failure.py 在实际 Runtime/
SQLite 的 save_hook_batch、claim_hook_execution、complete_hook_execution
调用前/提交后抛同一 OSError。原 Turn 仍 completed；close 传播原异常、释放
writer/checkpoint、关闭 model/repository 各一次。重复 stream_close 返回原错误
而不重做效果；claim 后失败保留 unknown，完成提交前失败保留 unknown，完成
提交后失败保留结果但通知仍 failed，不伪装整个关闭成功。计划失败无执行通知，
claim/完成失败恰有一个 failed 完成通知，不用空集合断言冒充通知验证。
6 专项通过（d6c697），加强通知数量后所有 SessionEnd + interrupt_shutdown
联合 32 通过、12.74 秒/退出 0（04dc67）。本批仅测试；managed 加载策略、
配置动态来源等剩余项继续单独收敛，不再把上述六窗口列为缺少证据。

test_session_end_ordering.py 使用真实配置/信任匹配、Runtime 关闭与 SQLite，
受控命令 runner 通过事件屏障令第二条先完成并抛 OSError，第一条随后成功。
两种 transcript 正常/不可用场景均验证：模型先关闭、所有 Hook 收尾后才关闭
repository，Started/Completed 按配置身份顺序，状态分别 completed/failed，
转录失败仅告警且 payload 路径为 None，stdout 控制字段不改变关闭，writer 释放，
重复 stream_close 只回放通知，不重跑命令或关闭资源。
2 专项通过（ec38c1），联合 session_end_hooks / interrupt_shutdown 26 通过、
12.69 秒/退出 0（c0ce90），Ruff 通过。本批仅测试，不冒称 OS 命令新验收；
真实超时进程证据仍见原专项。下方“多个Hook反序/局部异常、transcript失败”
已被本段补验覆盖；账本提交故障、managed 加载策略等仍未据此关闭。

## 当前补充：待落盘输入先于 SessionEnd（A4/A7/E7）

复核基准仍为干净 Codex `ddf04ad26789d040f9ef6a96736f76602e35a6cc`：
`session/handlers.rs::shutdown_session_runtime`先结束执行服务，
`hook_runtime.rs::run_session_end_hooks`在脚本前调用`flush_rollout`。
Corki原先先执行SessionEnd、再进入`_close_storage_resources`重试输入，导致脚本
读取的transcript遗漏已经通过准入、但终态append失败的实时输入。

新增公开Runtime场景：Stop边界注入LATE_INPUT，真实准入后对append注入一次或两次
OSError，SessionEnd使用真实Python脚本读取transcript。修复前两例失败
（fae163）：一次失败场景脚本缺LATE_INPUT；两次失败场景首次close仍未落盘便已执行脚本。
现在SessionEnd位于storage关闭阶段、输入flush成功之后；清除storage重试标记后才运行
Hook，异常继续聚合并清理repository/checkpoint/writer，不重放已开始的关闭副作用。
无需新增持久字段或重复执行标记；原共享关闭任务保留单次执行所有权。

这里的输入准入/业务append屏障与Codex的transcript导出flush不是同一层：业务输入仍
未提交时保留Corki原有可重试屏障，不宣称已完成关闭；transcript物化OSError仍按原实现
告警后执行，不把Codex的“flush失败只告警”误解释为可以跳过业务输入提交。

两例验证首次/再次close恢复、最终transcript含INITIAL和LATE_INPUT、无未记录输入、
单次脚本及单次model.close、通知可重读但不重执行。SessionEnd/UserPromptSubmit两文件
**35 passed / 13.74s**（a1365f）；关闭/初始化/checkpoint/异步Stop/发现/身份六文件
**101 passed / 10.73s**（49fdf0）。修改的Python文件Ruff及format通过（74f8a4）。
该证据不覆盖OS强杀、append提交后返回失败、全部账本故障或全部生命周期。
A–E继续开放，CLI对齐仍暂停。

## 当前生产进展（覆盖下方缺失状态）

已接入`core/session_end_hooks.py`，由Runtime执行资源关闭循环之后、storage/writer
关闭之前调用。仅已解析执行身份的会话进入；仅构造但尚未初始化的Runtime不会为
关闭额外启动脚本。所有SubAgent来源跳过，其余使用固定reason=other matcher。
计划/claim/raw结果保存到独立关闭事件身份，不制造用户Turn；共享_close_task以及
storage-only重试保证重复aclose不重放。脚本exit/超时错误发failed诊断，不改变原Turn
终态或阻断后续storage清理；账本/执行基础设施异常仍参加现有关闭错误聚合。

发现规则已增加SessionEnd：默认1秒、限制1～3秒；信任指纹保留原async配置，实际
StopCommand强制同步并告警（与Codex config/runs_async分离一致）；MCP类型明确拒绝
并产生加载警告。stdout无论plain/JSON都不解释为control/context；错误诊断限2500tokens。
并发命令执行，Started及Completed按配置顺序交付，取消异常不吞掉，工作任务join完成。

宿主接口新增`Runtime.stream_close()`异步事件迭代器：可以在关闭期间消费WarningEvent/
HookStarted/HookCompleted；不向已结束Turn的sink写入。底层仍共用aclose关闭任务，
通知观察者退出不取消资源清理。后续stream_close可重读同一有限关闭事件记录，不触发
新的执行；原有aclose返回None保持兼容。宿主可用`async for event in runtime.stream_close()`
替代最后一次aclose来显示通知；本阶段没有接入或优化CLI页面。

原root两个反例转绿、六例通过（106f6a）。扩展async true/false共12例与共享发现/身份
联合28通过（c84405）；新增默认1秒/显式99秒夹至3秒×正常观察/退出观察四个真实Python
进程超时场景，检查Started在清理完成前可见、退出观察后底层继续、PID消失及单次failed
通知。专项 **16 passed / 10.25s**（b514f7）。首次超时fixture未初始化Runtime而无事件，
已显式_ensure_ready后验证所需会话关闭路径，不把该fixture修正称生产修复。
既有Runtime shutdown/initialization、checkpoint cleanup、async Stop、发现/指纹六文件
**101 passed / 10.26s**（d4a071）；修改范围Ruff/format/diff通过（ab16c6）。

仍未关闭：加载拒绝的required managed来源策略、关闭账本提交前后故障、多个Hook反序/
局部异常、transcript失败、配置撤销/动态来源、其它资源故障时的SessionEnd执行顺序、
初始化失败等组合。stream_close的多观察者/取消/回压语义也需扩大验证；上述关闭观察
退出仅覆盖单观察者场景。A4/E7未整体关闭，最新核心全量未重跑，CLI继续暂停。

## 以下为实施前审计

状态：缺失，生产尚未接入。不是Stop/SubagentStop，也不是SessionStart的参数变体。

## Codex实际调用链

- `core/src/session/handlers.rs::shutdown_session_runtime`先关闭conversation、abort/join
  活跃任务、停止prewarm与异步hooks、回收exec/CodeMode、关闭MCP与guardian，然后执行
  `hook_runtime::run_session_end_hooks`。不能将SessionEnd放在每个Turn结束的位置。
- `core/src/hook_runtime.rs::run_session_end_hooks`先预览匹配项，空则不创建默认Turn。
  有匹配项时创建关闭事件身份，排除所有SessionSource::SubAgent；读取transcript_path，
  flush rollout失败只告警，发送HookStarted/HookCompleted。非SubAgent的Internal来源
  不能仅凭名字推断跳过，具体来源应遵循该match。
- `hooks/src/events/session_end.rs`固定reason=`other`并用于matcher。命令payload仅
  session_id/transcript_path/cwd/hook_event_name/reason；没有model、用户prompt或turn_id。
  turn_id用于Hook事件身份，不是发送给脚本的payload字段。
- 完成输出：exit0 completed，stdout全部忽略，即使包含continue:false/decision:block；
  非0失败，优先非空stderr，否则退出码诊断；无退出码/runner错误失败。它不控制模型
  继续、不添加context，也不能阻止资源关闭。
- `hooks/src/engine/discovery.rs`默认timeout=1秒，限制1～3秒；显式async会被改成同步
  执行并告警。MCP SessionEnd不受支持：跳过并记录加载失败；required managed配置
  的失败还需遵循对应启动拒绝策略，不能在MCP关闭后重新连接执行。
- `core/tests/suite/hooks.rs::session_end_flushes_transcript_and_ignores_control_output`
  验证关闭时一次调用、reason及已flush对话内容；`session_end_skips_subagents`验证隔离。

## Corki现状与反例

`plugins/agent_hooks.py`只承认SessionEnd元数据；`stop_hooks.py`可信事件发现/prepare
不包括SessionEnd。`runtime.py::aclose`关闭输入和异步准入，共享_close_task；
`_close_resources`join活动Turn后逐一关闭执行依赖，最后关闭storage/释放writer，
没有SessionEnd调用。close失败可重试storage barrier，不应再次执行关闭副作用。

新增`tests/integration/test_session_end_hooks.py`：真实Python脚本读取stdin、读取给定
transcript并追加到测试日志；独立计算可信指纹，matcher=other，显式timeout=1。
root/ThreadSpawn/review×exit0（输出阻止JSON）/exit2六场景，经公开stream和两次aclose。
结果 **2 failed / 4 passed / 1.23s**（df164c）：两个root用例均未执行脚本；四个
SubAgent阴性对照通过不证明根会话分发已实现。root后续待达断言约束payload字段集合、
session/reason、transcript包含最后输入、只执行一次且没有额外模型采样。
新测试无xfail/skip，当前不能宣称测试全绿。本批未修改生产代码。

## 下一步实施及验收

1. 增加SessionEnd专门发现规则：保留实际信任身份与来源策略，执行时间1～3秒、
   async强制同步，拒绝MCP类型并报告原因。不要复用普通600秒默认值或context解析器。
2. 在执行服务完成关闭之后、持久存储关闭之前接入独立执行器；保持已有关闭错误
   聚合、join和writer所有权。外部命令本身失败只诊断，不使其他资源关闭路径短路。
3. 使用会话关闭调用的稳定身份和执行账本，不能虚构新用户Turn；重复aclose/重试
   storage不得重放已执行或结果未知的关闭效果。新Runtime真正关闭属于新的关闭事件。
4. 核心Hook通知必须有宿主可消费的出口；当前Runtime事件主要从Turn流交付，aclose
   返回None，没有现成关闭事件流。不能把关闭通知写入已经结束的Turn sink或仅做空emit。
   应结合现有宿主接口设计必要的核心适配，不恢复CLI页面/交互风格对齐。
5. 补验真实超时进程回收、取消close等待者、资源提前失败、未完成初始化、transcript
   失败、配置撤销/来源隔离与账本故障。先解决上述实际根入口反例，不靠更多元数据
   单测代替Runtime接入。

CLI对齐继续暂停，官方服务/原生模型协议仍明确排除；A4/E7未整体关闭。
