# 自进化

管理页“全局配置 → 自进化”或 `features.self_evolution: true` 开启学习。
关闭后，后续轮次不再提供学习指引和工具；已经保存的技能及修改继续生效。
开关在轮次开始时读取文件签名，变化时才解析，不按固定秒数轮询。
本功能学习工作方法，不训练模型权重，也不自动修改 Corki 源码。

## 学习与修改

参考版本为 Hermes 官方仓库提交 **67e4d22d83b242ecfb2807ed39e9696ba1b1b0d5**
（2026-11-18）。下面的路径和符号均按该提交核对，不使用本机旧 study 分支作为依据。

- 优先修正本轮实际使用的 Skill，其次修正相关的现有技能，再增加主题参考文件；
  只有没有合适入口时才创建新技能。
- **已有技能原位修改**，不再按照扩展、公共、工作、内置、项目或插件目录拒绝编辑。
  不为了修改而复制出同名伴随技能，也不因编辑而移动原目录。
- 新技能保存在 `$CORKI_HOME/skills/self-improving/<name>/`；默认是
  `~/.corki/skills/self-improving/`。不会把 `~/.corki/` 或旧的
  `~/.corki/self-improving/` 当作技能发现根目录。
- 前台没有额外的“必须通过学习工具读取后才能编辑”限制。
  后台复盘必须在本次复盘中读取现有目标文件，历史会话里读过不算；新技能和新资源文件不需要先读。
  一次读取后可以连续修改，不会在每次自己修改后强迫重新读取。
- 用户锁定的技能不可自动修改；固定（pin）只阻止删除，不阻止内容改进。
  技能的调用启用状态不是内容锁，编辑不会自动开启已禁用技能。
- 支持创建、整篇编辑、补丁、资源写入与移除、历史和恢复。
  补丁使用 Hermes 的渐进匹配策略，包括空白、缩进、Unicode 和转义漂移处理。
  `revision` 是可选的并发检查参数，不再作为所有写入的强制前提。
- 新描述最多 60 字符；旧技能的长描述未改变时仍可编辑正文。
  文本写入最多 100,000 字符，支持文件同时限制 1 MiB，与参考工具的边界对应。
- 纠正原句、归纳步骤与判断条件，不堆积会话流水账，不把临时环境失败写成永久禁令。
  用户对语气、格式、流程和质量的纠正也属于技能学习信号。

管理页对内置技能的手动编辑限制不等于学习工具的来源限制。内置缓存升级采用来源指纹：
未改动的副本正常更新，已经改动的副本保留，有意删除的副本不自动补回。
历史迁移记录仍有效；此前已移至自进化空间的技能不会再次搬动。新策略产生的原位修改
使用新的记录版本，不再被旧迁移逻辑误判为待搬迁内容。

## 交付后的后台复盘

1. 累计模型循环步数，默认 10 步。`skill_manage` 任意一次调用都会重置技能计数，
   而不是只有写入成功才重置。失败、取消轮次也计入已经发生的循环步骤，但不会启动复盘；
   达到阈值后按参考实现消费该次触发。
2. 只有交付了非空最终回答、未被中断且宿主允许的持久主会话会启动自动复盘。
   子 Agent、一次性执行、定时任务、手动压缩和临时运行不递归触发学习。
3. 复用当前会话的**模型上下文快照**，包含实际模型可见的历史与压缩结果；不再从聊天展示记录
   截取最近 100 项或 24K token。副本与用户的展示历史和模型上下文相互隔离。
4. 默认使用该轮最后一次实际请求的供应商、模型和推理设置，而不是尚未执行的下一轮选择。
   复用系统前缀、工具定义与缓存身份。存储 fork 重映射的历史标识只在模型出站副本中还原，
   不改动父会话数据库。复盘自身压缩后改用独立缓存身份。
5. 广告给模型的工具定义与实际执行权限分开：只有技能读写和明确配置的额外工具能够执行。
   其他工具得到明确拒绝，不能因为保留了父会话工具定义就执行终端任务。
   Corki 的 Code Mode 复用隔离的编排引擎，嵌套调用仍经过相同执行白名单。
   后台任务范围内的交互审批直接拒绝，不弹出前台确认框，也不等待用户回答。
6. 最多 16 个模型循环步骤。累计输入预算默认 `min(600000, 模型原始窗口 × 0.75)`，
   优先按供应商真实用量计量，缺少用量时使用估算。取消原来额外的 120 秒总超时和单独的重试收紧。
   压缩使用自身的无工具请求，不会被包装器误加回整套工具或误算成一个主循环步骤。
7. 同一会话新提问优先取消所属复盘，最多等待 2 秒确认；迟迟未确认也不阻挡用户提问，
   清理所有权继续保留。其他会话的正常工作不会取消这个会话的复盘。
8. 复盘用量记入父会话的独立用量台账，不往父会话插入聊天、工具或压缩记录。
   后台改动与状态可通过 `skill_manage` 的 `status`、`history` 或维护命令查看。

`auxiliary.background_review` 可配置 `enabled`、已配置的 `provider` / `model`、
`max_input_tokens`（小于等于 0 表示不限制累计输入）、`extra_tools`。
切换到不同辅助模型时，较早历史用程序摘要代替，近期 24 个消息组原样保留，并保持工具调用配对。
供应商或模型配置无效时告警并回退到原模型，不改写全局默认配置。
`skills.creation_nudge_interval` 默认 10，小于等于 0 时关闭按步数触发的自动复盘。

## 写入、审计与可选审批

写入使用技能级跨进程锁和原子替换，保留已有文件权限。技能目录中的内部链接可以使用，
资源路径不能逃出该技能。后台必须经过 Hermes 静态扫描；比较修改前后结果，只阻止本次
引入的危险内容，不因为原有技能含有示例而拒绝所有改进。扫描未通过会恢复原内容。
前台扫描由 `skills.guard_agent_created` 开启，默认关闭，遵循参考实现的选择。

修改记录位于 `$CORKI_HOME/evolution/evolution.sqlite`，正文按 SHA-256 去重；
保留旧格式历史，读取时兼容，不清空旧库。新审计元数据按约 5 MiB 轮转，过期且没有引用的
正文保留一小时后可回收，不再对每个文件任意只留 20 次修改。查询默认只返回最近 20 条。
审计故障不会独自否决正常前台编辑；后台无法确认用户锁时停止相应修改，遵循 Hermes 的区分。
整包移除采用保留完整目录的恢复归档，包含二进制资源，不假装只保存 SKILL.md 就能恢复整包。

`skills.write_approval` 默认关闭。开启后，修改先暂存，实际文件不改变；由用户确认后重放，
模型没有批准或解锁自己的工具。保存、恢复、审核状态使用同一套写入校验。
`operations` 支持一次提交最多 20 项操作：先验证参数，再按固定顺序锁定全部目标、保存快照，
失败回滚整批；审批也是整批暂存。链式补丁允许，后续整篇覆盖前面修改会被拒绝。
整包删除必须单独提交，不能混在批量写入中。

```sh
corki curator status
corki curator lock <技能名>
corki curator unlock <技能名>
corki curator pin <技能名>
corki curator unpin <技能名>
corki curator approvals
corki curator approve <审批ID>
corki curator reject <审批ID>
```

## 定期整理

默认启用定期整理，受 Corki 自进化总开关控制：空闲至少 2 小时、距离上次整理至少 7 天才运行。
首次观察只建立时间基线，不立刻整理旧库。调度使用事件循环定时器，不增加定时线程，也不每两秒检查配置。

默认只对明确标记为后台创建或用户 `adopt` 交给维护的本地技能进行生命周期维护。
不根据名称、被编辑过或所在目录推断所有权。14 天未活动标记 stale，30 天未活动可恢复归档；
固定、锁定、禁用、外部来源和定时任务引用的技能受到对应保护。
日常复盘可以改进各种来源的内容，定期整理却不能随意归档他人的资源，这是 Hermes 本来就区分的两种权限。
`curator.prune_builtins` 可选开启内置闲置清理；默认关闭，且内置不会进入模型合并候选。

`curator.consolidate` 默认关闭：默认整理不请求模型。显式开启后才运行独立的模型合并任务，
优先归纳大类技能而不是制造大量一次性技能；该任务的循环上限按 Hermes 为 9999，
不套用普通轮后复盘的 16 步预算。合并前保存快照，默认保留 2 份；失败不阻止其他整理工作。
归档保存在 `$CORKI_HOME/skills/.archive/`，不参与召回，可通过归档 ID 恢复。
快照包含本地技能库及定时任务定义，排除归档、依赖环境和缓存。合并后将任务中明确的
`$技能名` 或技能链接迁移到目标技能，普通描述文字不替换，已有运行的配置快照不改写。

```sh
corki curator run --dry-run
corki curator run
corki curator run --consolidate
corki curator adopt <技能名>
corki curator pause
corki curator resume
corki curator archives
corki curator archive <技能名>
corki curator restore <归档ID>
```

## 源码对照与宿主差异

所有路径以下列官方版本为基准：
[Hermes 67e4d22d83b2](https://github.com/NousResearch/hermes-agent/tree/67e4d22d83b242ecfb2807ed39e9696ba1b1b0d5)。

| 行为 | Hermes 依据 | Corki 实现 |
|---|---|---|
| 工作中修正技能、学习偏好、优先修改当前技能 | `agent/prompt_builder.py`、`background_review.py::_SKILL_REVIEW_PROMPT` | `guidance.md`、`review.md` |
| 步数、任意管理调用重置、交付后触发 | `agent_init.py`、`turn_iteration_prep.py`、`tool_executor.py`、`review_signals.py`、`turn_finalizer.py` | `controller.py`、`state.py` |
| 原上下文、缓存前缀、独立压缩、不同模型的 24 组回放 | `background_review.py::build_cache_parity_fork`、`_detach_fork_compression`、`_digest_history` | `worker.py`、`replay.py` |
| 16 步、75% / 600K 预算、所属会话取消、工具白名单 | `background_review.py::_REVIEW_MAX_ITERATIONS`、`_review_input_token_budget`、`cancel_background_review_for_live_turn`、`_review_tool_whitelist` | `controller.py`、`worker.py` |
| 不按技能来源禁止改进；后台重新读取；用户锁；固定仅限制删除 | `skill_manager_tool.py::_locate_for_write`、`skill_manager_guards.py` | `tools.py`、`state.py` |
| 渐进补丁、扫描与已有发现基线、失败恢复 | `fuzzy_match.py`、`escape_drift_autocorrect.py`、`skills_guard.py`、`skill_manager_scan.py` | `hermes_guard/`、`scanning.py`、`tools.py` |
| 可选审批、前台扫描默认关闭 | `write_approval.py::evaluate_gate`、`skill_manager_tool.py::_scan_required` | `commands.py`、`tools.py` |
| 20 项批量预检、整批审批、全部目标锁与失败回滚 | `skill_manager_batch.py::_skill_manage_batch`、`_validate_batch_ops`、`_restore_snapshot` | `batch.py`、`tools.py` |
| 用户锁的命令/补丁检查，后台审批不交互 | `skill_lock.py`、`background_review.py::_bg_review_auto_deny` | `locks.py`、`hermes_guard/skill_lock.py`、`mcp/elicitation.py` |
| 去重审计、5 MiB 元数据、1 小时未引用正文回收 | `skill_ledger.py` | `state.py` |
| 闲置生命周期、首次延迟、只管理明确托管资源、可选合并 | `curator.py`、`skill_usage.py` | `maintenance.py`、`commands.py` |
| 合并前备份、默认保留 2 份、排除环境缓存 | `curator_backup.py` | `maintenance.py` |
| 已改动的内置副本不被更新覆盖 | `skills_sync.py::_update_existing_skill` | `skills/bundled_sync.py`、`skills/installer.py` |

有意保留的宿主差异：

- Corki 的总开关、默认关闭和新技能专属目录来自既有产品要求；Hermes 没有完全相同的总开关与空间分类。
- Corki 用 asyncio、自己的 Runtime/fork、SQLite 和现有用量台账，Hermes 用线程、AIAgent 和 JSON/JSONL。
  协议工具名沿用 `skill_read` / `skill_list` / `skill_manage`，不复制整个 Hermes Agent。
- 记忆继续走 Corki 既有提取与合并体系，不另建 Hermes 的 MEMORY.md / USER.md，不强制开启记忆。
- 用户锁除了学习工具，也采用 Hermes 的命令和补丁路径检查；它仍是工具层策略，不是任意外部脚本的操作系统级文件保护。
- Hermes 延迟首次复盘响应前的压缩以提高缓存命中；Corki 保留现有请求窗口与字节预检，必要时首次请求前仍会压缩。
  普通情况下保留原前缀，不能承诺每次复盘都命中父会话缓存。
- Hermes 定时任务用独立的技能字段；Corki 用提示词中的显式技能引用，迁移只处理可确认的引用。
- 删除采用完整目录恢复归档，审计采用 SQLite 去重；没有照搬 Hermes 整库备份的全套回滚命令，
  可用技能修改历史与单技能归档恢复，整库快照仍保留在备份目录。
- 辅助模型使用 Corki 已配置的供应商；不引入 Hermes 的凭据池、服务商插件与独立登录配置。
- Corki 没有 Hermes 同名的 `read_file` / `search_files` 工具，复盘通过 `skill_read` 和技能资源路径读取；
  不为读取文件而默认放开通用终端。批量接口采用 Corki 字段名，兼容 Hermes 的旧文本/路径参数。
- 状态与审计提供工具和维护命令查看，后台结果不额外插入父会话正文；没有照搬 Hermes 各聊天渠道的通知界面。

扫描与补丁实现保留 Hermes MIT 许可，见 `src/corki/evolution/hermes_guard/LICENSE.txt`。
测试使用独立目录与假模型；不以测试替代真实平台和真实模型的效果验证。
