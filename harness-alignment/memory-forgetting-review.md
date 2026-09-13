# D2/D5：更新、遗忘与发布证据

## 当前 D5 验收结论：确定性 Harness 行为已核验

本节覆盖下方按时间保留的“其它来源信任/模型执行约束仍须核验”占位。
不把模型语义能力或绝对防提示注入作为已实现的程序保证。

| 要求 | 当前源码与行为证据 |
|---|---|
| 显式记住/更新/遗忘 | 原生 ext/memories/src/tools/ad_hoc_note.rs 以工具描述限定用户明确请求；Corki MemoryAddNoteTool 对应。真实 Runtime note→冷合并→summary/read 验证，不将追加 note 误称立即删除记忆或历史 |
| 删除/修改依据与失败恢复 | 原生 memories/write/templates/memories/consolidation.md 的增量遗忘规则；Corki Git 成功基线与 workspace diff。change_evidence/no_output 验证删除或修改、模型失败、失去 owner、成功后冷召回与去重 |
| 外部污染与宿主信任 | 普通/嵌套工具、实际 MCP 准入、模型已完成项和冷恢复，以及 Hook、明确 untrusted 项目分别按来源处理；不把远端声明当宿主策略。标记失败只告警继续，取消仍传播 |
| 记忆 worker 执行约束 | 原生 memories/write/src/phase2.rs::agent::get_config 禁用 MCP、插件、递归记忆/委派，Never 审批；managed 限 memory root 写入且禁网络，disabled/external 保留宿主选择。Corki prepare_agent 与实际 Runtime 权限/技能/插件测试对应 |

来源内容与执行指令的分界：ad_hoc_instructions 的 note 解释规则与原生一致，
要求使用数据但不执行其行动指令；ConsolidationInputs 将历史证据作为 ContextItem，
不是触发显式 skill 的当前 UserMessage。memory_agent_context 验证历史中的技能
名称不自动选择技能、父项目/祖先规则不泄入独立 memory 项目、已配置技能规则
仍生效、父插件不重复加载。该边界不是对模型理解能力的证明。

最新 11 文件联合 **190 passed / 69.01s，无跳过**（94bc40，退出 0）：
memory_pipeline_runtime、memory_change_evidence、memory_no_output、
memory_agent_context、memory_execution_permissions、external_tool_output、
mcp_memory_admission、model_external_events、unit/memory/test_pollution、
project_trust_runtime、async_hook_checkpoint。使用已构建的真实 native compiler，
权限场景覆盖 direct/nested、managed/disabled/external、旧 base 与当前 caller
差异、managed approval 约束；实际文件/本地网络效果与策略一致。

本轮无生产或测试改动，是源码、现有跨模块证据与验收项的归并。原生路径以本节
及当前仓库为准：污染数据库实现已位于 rollout/src/state_db.rs，而非旧文中的
core/src/rollout/state_db.rs。D5 关闭不代表 D1、其它 A–E 或最终回归完成。

已知限制：显式请求由模型识别，handler 不证明自然语言授权；遗忘依赖后续模型
合并，原始 note/历史不被擦除；污染标记是尽力写入，失败可能留下可提取来源；
外部沙箱由宿主负责，disabled 不承诺本地隔离；真实模型质量与全平台安全性未实测。

## Hook 上下文不是自动污染源

原生 core/context/hook_additional_context.rs 明确 role=developer，
hook_runtime.rs::record_additional_contexts 以该类型追加记录；
stream_events_utils.rs::response_item_may_include_external_context 的标记集合
不包含普通 Message。Corki hook_context.prepare_context 同样生成 developer
ContextItem（hooks.additional_context），memory.pollution.has_external_context
不将其作为外部工具事实。这个“不标记”是参考行为，不应凭“脚本可能读网络”
自行把所有 Hook 上下文改成污染源，也不应描述为内容已经语义安全审核。

实际配置准入另有边界：stop_hooks.discover 校验来源 key、enabled 与命令指纹，
变更或未授权命令不执行，原生 spelling 的撤销不能被旧兼容 key 恢复。项目规则
在 Corki instruction_manager 中按 trust_level 加载，明确 untrusted 时不读取，
缓存键包含该信任状态，不能从旧已授权快照回退。此批项目规则测试是 Corki
当前行为复核，不据此扩张成项目所有内容均可信或完整原生项目规则审计。

后续已定位原生 core/agents_md.rs 的真实 trust 门控：保留宿主全局指令、跳过
明确 untrusted 项目的文件读取。六场景补验全局正文及公开来源仍在，相关31通过，
见 context-snapshot-wire.md；此处不再只有 Corki 单侧证据。

test_async_hook_checkpoint 的六个 Pre/Post 场景现在启用真实 memory repository
与 disable_on_external_context 策略，显式将来源设为 enabled；验证普通冷恢复、
投影后再冷开、压缩替换再冷开结束后仍为 enabled。与 stop_hooks 和项目 trust
Runtime 三文件最终 49 passed/8.76 秒、无跳过（547e26）；ruff/format 通过
（959374）。首次运行缺少 native compiler 环境且选择了子集，33通过/6跳过/
10未选，不冒称完整结果；补齐当前 bundled compiler 后全三文件通过。
本批仅补测试和审计，D5 的模型理解/内容注入抵抗仍非确定性 Harness 保证。

## 污染入口与尽力写入边界复核

当前源码：Codex core/tools/registry.rs::handle_any_tool 在输出声明外部内容后
标记；stream_events_utils 对已完成外部响应项标记；MCP 使用宿主绑定的 server
metadata。rollout/state_db.rs::mark_thread_memory_mode_polluted 对数据库错误
仅 warn，不使当前 Turn 失败。不能把该策略说成任何数据库故障下都能防污染。

Corki 对应 graph 的完成模型项/持久 partial 恢复/普通与嵌套结果入口，以及
mcp/connection.py 的 before_call 后、真正调用远端前的 on_external_context。
是否污染来自宿主 MCPServerMetadata，不信任远端工具 _meta 或 MCP 风格名称。
memory/pollution.py 的 feature/policy/repository 三门控及 best-effort 写入与此一致；
普通异常仅记录类型告警，CancelledError 仍传播。工具搜索的普通函数结果也声明
外部上下文，空候选不绕过标记；原生专用 hosted 协议仍拒绝，旧归档只做兼容恢复。

本轮四文件 98 passed，12.53 秒，退出 0（7d3641）：

- external_tool_output：direct/nested、错误值与成功值、policy 开关、空搜索、
  已完成结果冷恢复不执行旧工具、标记失败告警及取消。
- mcp_memory_admission：假名称、畸形输入、未曝光/删除绑定不误标记；实际远端
  调用前已标记，远端失败/超时仍保留；refresh/reconcile 的宿主策略更新生效。
- model_external_events：普通外部项持久化和失败/取消；专用协议拒绝与旧归档
  冷恢复，不以兼容历史为理由重新启用专用调用。
- pollution：已选来源标记后触发合并、不破坏当前 owner/冷却，排除合并输入；
  重复标记、缺失来源、事务失败回滚及取消等待写入。

本批没有生产修改。标记成功后，提取与合并资格会排除 polluted 来源；标记失败
可能留下 eligible 来源，这是参考既有的尽力写入限制，不宣称防提示注入保证。
显式 enable 也可重新允许来源，不能将用户主动变更误称不可逆安全标记。
本轮收敛普通工具/MCP/模型项污染入口；Hook/项目规则等其它内容并非一律带外部
标记，完整 D5 仍需结合来源信任与模型执行约束核验，不能由这 98 项推导全部安全。

参考 pinned Codex `ddf04ad26789d040f9ef6a96736f76602e35a6cc`。
本批无生产修改，不读取或调用官方远程 memories endpoint。

## 原生行为与 Corki 对应

原生 `ext/memories/src/tools/ad_hoc_note.rs` 定义 append-only 普通函数工具，描述限定
用户明确要求 remember/forget/update 后调用；解析参数后交给 backend。描述是模型
行为约束，不是 handler 对自然语言用户意图的可靠鉴别器，不能冒称程序已证明授权意图。
`memories/write/templates/extensions/ad_hoc/instructions.md` 要求合并 note、保留 note
文件、标记 `[ad-hoc note]`，同时禁止把 note 内容当作执行行动的指令。

`templates/memories/consolidation.md` 的 forgetting mechanism 依据成功基线后的
workspace diff 识别删除/修改来源，移除只由已删除来源支持的事实，保留仍有依据的
内容，再更新摘要索引；不是清空整块记忆，也不是删除原始会话。

Corki `memory/tools.py::MemoryAddNoteTool`、`backend.py::add_note` 对应追加请求，
独占创建文件并保留原文；并不即时改写 MEMORY.md。`pipeline.py` 发布在
`complete_consolidation(..., publish=...)` owner fence 下进行，成功后更新基线。
显式遗忘与源失效都需后续合并，不能把“请求已记录”报告成“所有原文已擦除”。

## 运行证据与边界

`test_memory_pipeline_runtime.py` 的真实工具调用→note→冷 Runtime 合并→summary
注入→read 链，已包含遗忘 OLD_PREFERENCE、记住新偏好及恶意行动文本作为数据。
本批加强：note 落盘后两个旧记忆文件仍保持原样；合并后 detail 与 summary 均移除
旧偏好，note 原文仍存在。这验证阶段边界，不承诺密码学擦除或历史删除。

`test_memory_change_evidence.py` 六组合：源 delete/modify × 正常/model失败/丢owner；
失败保留旧基线和摘要，后续成功再更新，冷打开仅召回当前内容，未改变不重复采样。
`test_memory_no_output.py` 三组合：新版本空提取撤销旧输入、移除 rollout summary，
完成后冷打开去重，四个实际模型流均关闭。

三文件联合命令：`PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring .venv/bin/pytest
-o addopts='' -q --tb=short tests/integration/test_memory_pipeline_runtime.py
tests/integration/test_memory_change_evidence.py tests/integration/test_memory_no_output.py`。
终态 353beb：21 passed，19.70 秒，无跳过。修改文件 Ruff/format 通过
（279303/06fadc），diff 通过（6019f0）。

状态：D2/D5 部分一致，有当前端到端证据。预设 Consolidator 输出只能证明 Harness
传递删除证据、发布及冷召回，不能证明真实模型正确遗忘或完全抵御提示注入；其他
污染入口、共享文件发布失败矩阵仍待收敛，不关闭整个 D5 或 A–F。
全量 session 34854 在 cea0d2 仍活动，已输出约 71% 测试计数；该输出截断，不能
用它证明整个区间无失败。等待最终汇总，且采集早于本批新增断言。
