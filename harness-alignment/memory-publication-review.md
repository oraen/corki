# D2/D3/D6/E4：记忆发布与成功基线

## 兼容输出 skills 写入中断

_sync_memory_skills 先移除不再保留的生成 skill 普通文件，再逐个写新 SKILL.md。
新增 skill_write 的 source delete/modify 两例：第一轮生成 old-skill，第二轮
准备 new-skill 并在其 SKILL.md 写入前注入 OSError。这时详情/摘要均已更新、
旧 skill 已删除、新 skill 文件不存在；DB 仍 failed、旧成功基线和水位不变。
故障只在第二次采样后的发布触发，下一冷 Runtime 重新取得来源差异，补齐新
skill，随后 no-diff，最终基线不包含 OLD_FACT。两项直接通过（dcc930）。

这是对 Corki 普通 JSON 兼容结果发布的故障验证，不是原生模型协议；不宣称 Codex
恰好按相同文件顺序写 skills。对齐约束是已执行副作用不虚报回滚、失败不标成功、
基线不提前确认，下一轮依据当前目录与未消费差异重新合并。本批不验证所有文件
系统故障或真实模型合并质量，无生产修改。

三文件联合最终 39 passed、0 skipped（ada0be，29.82 秒）；Ruff/1125 文件
格式/compileall/77 包与 diff 检查通过（170475）。无活动测试进程。

## JSON 兼容输出的逐文件发布中断

artifacts.write_consolidated_artifacts 先用单文件原子替换写 MEMORY.md，再写
memory_summary.md，最后同步 skills；已有注释明确允许中途 I/O 故障留下混合版本。
这不是原生专有协议，而是普通 worker 最终 JSON 经 _consolidate 校验后的兼容
输入。owner fence 不把这些文件替换变成跨文件事务。

扩展 test_memory_change_evidence 的 delete/modify 两种来源变化：第二次合并在
summary 写入前抛 OSError，详情已是 CURRENT_ONLY、摘要仍含 OLD_FACT，基线仍为
上次成功值，DB status=failed。首次两例因错误要求 completed_watermark<input_watermark
失败（1f35c0）；实际两者均 0，因为只有扩展文件变更、没有 stage-one 新输出。
已改为验证水位与失败前相同，不修改生产实现来迎合错误测试。

之后关闭重建 Runtime、推进测试时钟重试，仍能看到未消费的来源删除/修改证据，
成功修复摘要/详情并更新基线；再冷打开走 no-diff，模型可见 summary 只含当前事实。
故障后的新一轮采样基于当前工作区与差异，不是自动重放结果未知的旧工具调用。
本批不证明掉电时文件系统持久性或所有 skills 同步故障，仅覆盖真实兼容路径的
两文件间失败、错误报告与下一次修复，不宣称完整记忆或 A–F 验收完成。

最终来源变更/worker Runtime/记忆全链三文件联合 37 通过、0 跳过（29f560，
26.86 秒），全部静态检查通过（9161f2，1125 文件/77 包）。本轮无生产修改，
无活动测试进程。

## 冷 Runtime 重建与后续分支优先级

database_success_cold 新增旧 Runtime 完整 aclose 后，重新创建 Runtime、数据库
连接和 Memory 模型对象，使用同一 Thread/数据库/记忆根；移除注入故障后下一
Turn no-diff 成功、完成水位追平、owner 清空，文件与基线不变，新记忆模型 count=0。
这证明恢复不依赖旧 Pipeline 对象；测试仍在同一 Python 进程，不等于 kill/restart
或掉电场景。新旧两种恢复单测通过（9a433e），三文件组合 37 passed、0 skipped
（ac3c13，21.14 秒），全部静态通过（3dba87，1125 文件/77 包）。无生产修改。

进一步调用链确认：Pipeline._consolidation_work 总是 prepare_agent(root=self._root)，
因此真实主 Pipeline 的文件编辑 worker 使用 shared 模式。run_agent 在调用者不提供
prepared 时才准备暂存工作区；不能把 staged AgentArtifacts 描述为主 Pipeline 默认。
但 _consolidate 仍接受 worker 最终 JSON，经验证返回 ConsolidatedMemory，由
write_consolidated_artifacts 逐文件发布，该兼容分支真实可达。下一发布中断验收应
优先覆盖它，并分别记录暂存 helper 的独立边界，不凭类型注解扩大默认路径。

## 数据库成功标记失败后的同 Runtime 重试

已扩展 database_success 故障场景：移除本测试 SQLite trigger，仅将 retry_at
设为已到期，不改失败状态或水位；真实下一主 Turn 启动后台重试。结果走
consolidation_skipped、无 failed，数据库最终 succeeded 且 completed_watermark
等于 input_watermark、owner 清空。模型调用数仍为 2，文件与已发布基线不变；
证明该路径不会重复采样或执行已确认补丁（单例 39aef3 通过，后增加显式计数）。

原生 phase2.rs 在同步输入后以 workspace_diff 无变化且 artifacts 有效为条件
执行 succeeded_no_workspace_changes。Corki _consolidation_work 调用
git_baseline.matches，后者既比较快照也调用 validate_shared_artifacts；因此只要
文件无效就不能走此跳过分支。这里验证同 Runtime 重试，不替代冷进程恢复、基线
内部破坏或暂存逐文件发布中断矩阵。本批无生产修改，原失败已被错误报告而非
虚报成功的断言仍保留。

最终 agent Runtime/基线单元/来源变更组合 36 通过、0 跳过（3bc25a，20.38 秒），
包括显式模型调用计数；Ruff/1125 文件格式/compileall/77 包及 diff 检查通过
（c44b51），无活动测试进程。

参考 Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc 的
memories/write/src/phase2.rs completion handler（390–489 行）。本审计针对共享
目录文件编辑路径，不调用官方服务，也不把用户排除的配额 guard 作为需求。

## 原生顺序与故障语义

等待 worker 终态 → shutdown worker → 验证 artifacts → 再确认 owner →
reset_memory_workspace_baseline → job::succeed。关闭失败不释放租约；无 owner
不重置基线；基线失败记 failed_workspace_commit；基线已成功但 job::succeed
失败只记录错误，不回滚已写文件或基线。失败 worker 也不会撤销已确认的文件编辑。
因此“任何失败都恢复旧记忆文件/旧基线”不是原生契约。

## Corki 对应路径

memory/agent.py 等待真实子 Runtime 终态并 join 关闭；共享工作目录通过验证后
返回 SharedAgentArtifacts。pipeline.py 的 _run_owned_consolidation 等待并 join
work/heartbeat，再调用 repository.complete_consolidation 的 publish 回调。
memory/sqlite.py 在 BEGIN IMMEDIATE 内验证 running+owner，执行 publish，再更新
selected 状态和 completed_watermark/status，最后提交。共享 publish 调用
git_baseline.reset。数据库失败会回滚数据库事务，但不是跨 Git/文件系统的回滚。
现有代码对此已有明确注释，无需为了制造“原子发布”而添加不同于参考的回滚。

## 新故障注入

扩展 test_file_editing_child_preserves_side_effects_without_false_success：真实
子 Runtime 用 apply_patch 修改 MEMORY.md 和 memory_summary.md，确认工具结果
成功后再注入故障。

- baseline_commit：基线 reset 抛 OSError。两文件保留 new、基线仍旧，report
  failed=1 且 consolidated=false，数据库 failed/完成水位 0/owner 清空。
- database_success：SQLite BEFORE UPDATE trigger 在写 succeeded 时 RAISE(ABORT)。
  文件和基线均已更新，但数据库最终 failed/完成水位 0/owner 清空；不能把新文件
  的存在当作成功任务凭证。

两项新测试通过（fef46d，1.43 秒）；测试先使主 Turn 正常完成，后台失败与主 Turn
隔离，关闭后 worker 临时状态目录均已清理。这里只断言本次故障窗口，尚未验证
后续 retry/no-diff 对基线已前进但水位未前进的恢复。暂存 AgentArtifacts 的逐文件
发布中断属于另一分支，也不能由本次共享目录测试关闭。

状态：共享发布顺序及上述失败语义有直接证据；完整 D2/D3/D6/E4 和 A–F 未完成。

最终三个记忆集成文件联合 34 passed、0 skipped（0dbc15，23.66 秒）；Ruff、
1125 文件格式、compileall、77 包兼容与 diff 检查通过（f8540e）。无生产修改，
无活动测试进程；不把这些确定性模型测试当作真实模型记忆质量验证。
