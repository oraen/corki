# Harness 提示词对齐验收汇总

状态：完成。8项均有本地源码对照、实际请求/流程验证及相关回归证据。

参考 Codex 提交：`ddf04ad26789d040f9ef6a96736f76602e35a6cc`。
详细来源、调用链、修复和各批测试记录见 [prompt-alignment-progress.md](prompt-alignment-progress.md)。
独立验收清单见 [prompt-alignment-goal.md](prompt-alignment-goal.md)。

## 2026-10-12 长任务续行优化

只读观察发现：一次长任务在 57 个模型步骤内发生 6 次压缩，反复读取评分规则，
并在压缩后重新介绍调查计划；摘要已经记录了已完成调查和待写报告。
这支持“模型没有充分利用进度”的判断，尚不能归因为历史丢失或恢复器重复执行。

- `prompts/agent/base.md` 引入同一参考提交 `codex-rs/models-manager/models.json`
  中现代模型的续行规则：压缩不结束任务、不从头重做、不重复已发进度；保留原目标与用户修正。
- 针对实测问题补充读取指导：发现截断后定位缺失行段，不能把部分读取当完整读取，
  不靠反复增大输出请求重读全文，不为恢复输出重复执行有副作用的命令。
- `prompts/tasks/compact.md` 在原生摘要模板基础上增加明确的完成状态、下一步动作、
  评分关键规则与证据、未读内容、文件修改/删除和备份状态。这是 Corki 的提示适配，
  并非声称原生 Codex 的摘要模板包含这些新增文字。
- `prompts/memory/read_path.md` 明确无命中不等于首次任务；压缩本身不要求重做记忆检索。

输出预算保留原生 `codex-rs/core/src/tools/context.rs` 的请求值与模型策略取较小值语义；
未放大预算、增加循环硬上限或强制拦截重复工具调用。摘要仍为 user 级数据，
自定义基础指令和 compact prompt 保持原样。没有修改活动进程或会话数据库。

生效边界：通用基础指令由会话持久化，新建会话才能完整采用新版 fallback；
旧会话恢复保留旧基础指令，自定义/模型目录指令优先级不变。运行中进程可能缓存模板，
需要重启才能可靠加载新版模板。这些指令降低重复调查风险，不保证任意模型的每次决策正确。

测试扩展覆盖默认/自定义摘要提示的真实 Runtime HTTP 请求、手动/轮前/轮中压缩、
两次压缩与冷恢复、摘要角色、工具副作用只执行一次，以及记忆规则的条件注入。
HTTP 使用本地模拟模型验证 Harness 合同，不作为真实供应商模型行为改善的实测结论。
上下文、提示、输出策略、压缩重试、指令恢复和无步数上限相关回归共 570 项通过，
耗时 26.04 秒；日志 `/tmp/corki-continuity-regression.log`。修改的三个 Python 测试文件
通过 Ruff 检查与格式检查。

## 范围与证据

| 用户要求 | 实施与验证入口 |
| --- | --- |
| 1 基础指令 | agent/base恢复通用原规则并适配真实工具；test_base_prompt_wire、base_instruction_override/recovery检查fallback/catalog/自定义/空覆盖及两协议、压缩、冷恢复 |
| 2 工具与反馈 | 内置、Code Mode、通用搜索、MCP、技能、memory、history/notes及动态插件注册逐项清单；base_prompt_wire、code_mode_output_wire、model_search_capability、mcp_resource_registration、tool_failure_boundary、local_history_notes检查实际定义/执行/失败与回放；工具跨域6482项通过 |
| 3 上下文 | window保留自定义压缩原字节/空串；message_groups修正新窗口顺序；notes hint不注入工具可写路径；context_*、compaction_*、model_instruction/personality、local_history_notes验证角色、顺序、关闭、重复、恢复与低信任边界 |
| 4 Memory | 保留完整提取/整理/读取规则；纠正SQLite与条件注入说明；memory_*、long_term_memory、thread_memory/citation验证生成、读取、更新、污染/遗忘边界、请求与回放 |
| 5 Skills | 完整读取/触发/资源定位恢复参考语义，选择本地文件系统分支；不宣称不存在的skills.read平台；skill_catalog/input_lifecycle、memory_skill_routing等验证来源绑定、预算、错误和普通技能/记忆技能分流 |
| 6 项目与权限 | AGENTS/environment保持user上下文；宿主权限保持developer；环境/权限/指令快照生命周期及base_prompt_wire检查真实工作目录、关闭、替换和无凭据注入 |
| 7 主循环 | 重试不加UI噪声或抬升工具结果；中断为user；受信任hook附加上下文与Stop反馈角色不同；history_retry、turn_input_boundary、hook恢复、interrupt*等验证取消/排队/失败/收尾竞态 |
| 8 现有规划协作 | Default恢复完整规则，Plan保留参考模板；模式自定义值原样、变更仅一次、按真实工具能力约束；collaboration_prompt_wire、plan/request_user_input等；不新增spawn/mailbox能力 |

## 关键修复

- 补全基础、Default、压缩、技能、Code Mode和工具说明中被削弱或省略的通用规则。
- 自定义compact prompt不再被strip或把空串误当默认值。
- 新窗口把模型切换指令置于developer bundle首段，personality按初始聚合规则处理，
  宿主管理策略独立位于项目/环境上下文之后；增量及已存历史顺序不重写。
- MCP目录区分直接调用的tool_search和Code Mode的ALL_TOOLS，不要求重复发现。
- view_image只在实际能力支持时暴露original参数；get_context_remaining补真实输出schema。
- 本地notes恢复hint只含固定说明，路径/正文通过工具结果读取，防止低信任数据提升为developer。
- 恢复history/notes完整使用规则，修正ID来源、覆盖写语义和不存在的跨代理能力暗示。

## 必要保留的差异

- Corki使用Python、SQLite会话/执行ledger、本地虚拟notes；不声称Codex JSONL、远程加密历史、
  远程最终一致性或跨代理存储。当前已有本地能力照常可用，不新增官方服务依赖。
- Code Mode为QuickJS及实际JSON schema帮助；apply_patch是JSON `patch`参数，session_id为
  Corki真实类型；不能为字面相似宣称V8、freeform接口或不存在的参数。
- 普通Chat Completions供应商把宿主developer上下文映射为system；AGENTS、用户输入、
  压缩摘要仍为user，工具数据保持工具结果身份。Responses使用其实际协议字段。
- 仅有现存Plan/Default、本地skills/MCP/插件能力；不复制多代理角色目录、语音realtime、
  官方登录/额度/云任务、原生tool search或远程压缩namespace。
- 完整基础策略比原简化版本长。过小配置窗口可能无法容纳；生产不静默丢弃关键规则或
  擅自增大用户窗口。针对固定预算的旧测试夹具独立固定base或调整测试窗口，未降低生产策略。

## 验证与限制

最终工具跨域回归：6482 passed，303.88秒，4 workers，无失败或跳过。
全部修改的非PTY测试及3个新增测试文件联合回归：1124 passed，98.44秒，4 workers。
70个修改/新增Python文件ruff check、format --check及git diff --check通过。
受影响PTY补测：12 passed，34.02秒，串行；40/100列宽、完成/失败/取消、
文本steering开关组合下的压缩输入排队与恢复均通过。
前序专项已涵盖两协议真实序列化的Mock HTTP、本地工具/SQLite执行、取消和冷恢复；
各批数量有重叠，不能直接相加当作不重复测试总数。

没有使用真实用户密钥、付费模型、真实剪贴板或真实用户记忆/技能/配置做测试。
假模型能证明请求和流程满足断言，不能证明所有供应商或真实模型行为与Codex完全一致。
尚不声称全仓所有测试、所有操作系统或真实终端应用均通过。未提交/推送代码，未修改teach.md。
