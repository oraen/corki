# C1/C2：自定义扩展增量的实际能力差异

状态：第一批实现已接入并通过定向回归，尚未完成全部验收。属于核心上下文扩展契约，
不依赖 Persistent、多执行环境或 E4 的用户选择，不涉及官方协议或 CLI。

## 当前实现与验证（覆盖下方原始诊断状态）

普通 HTTP 投影已补真实 Runtime+MockTransport 两模式测试
`test_extension_world_state_wire.py`：developer→user 的生产者正文保持原角色，
silent 更新及冷恢复不重复正文，PRIVATE_COMPARE、snapshot_state 和内部 key
不进入请求。只访问配置的 fixture.invalid 普通 responses/chat/completions
路径。这里验证生产者自行控制的追加正文，不把 user 更新当作已撤销旧 developer。
四文件初批 72359 实际退出 0（9d6af2）：26 passed / 3.87s。

JSON 比较已对照原生 ExtensionWorldStateSection::snapshot 与
remove_null_object_fields：持久比较值清除对象中的 null 字段，递归对象但不
进入整体替换的数组；数组中的 null/对象 null 保留。Corki 在写入/恢复比较值
时同样规范化，保留 producer 捕获的原始 snapshot_json，不改模型正文、不
引入 merge-patch 网络协议。新增测试验证持久值、Known 回调值、数组及无变化
不追加记录。第一次 ruff 报长行，format 后重验通过，无遗留静态错误。

扩大联合为 unit/context、unit/core 与五个 integration 文件（上方新增 wire、
context_role_wire、extension_world_state_runtime、extension_context_lifecycle、
plugin_guidance_compaction_wire）；4 workers/loadfile/禁重启/空 addopts。
**608 passed / 5.61s**，82411 实际退出 0（584ce0），静态通过（f862fb）。
下一步刷新当前生产的更广回归与项目静态检查，不再笼统重复角色/JSON/请求
投影待核对；该接口仍不能代替 C1/C2 其它未实现的条件能力。

取消与来源接管补证：world_state_contributions / render_diff 两个入口抛
CancelledError，真实 Runtime 都传播取消，只有一个 TurnCancelled，无
TurnFailed/TurnCompleted、模型请求或部分 extension ContextItem。初批
50942 实际退出 0（c12c90）：21 passed / 3.46s。

继续发现同 key 旧宿主 JSON 恰好符合新版 envelope 时会被误认为 Known。
新增用例 78933 实际退出 1（79202d）：13 passed、1 failed / 0.09s。
修复为恢复比较状态也要求显式 content_kind；缺失来源按 Unknown 交给
生产者，silent renderer 仍须写入带正确来源的新比较记录，不能因 JSON
字符串相同跳过。未修改旧历史，也未把旧正文当成新来源自行删除。

修复后联合原 context/core 与三个 integration 文件，4 workers/loadfile：
**603 passed / 5.90s**，1807 实际退出 0（a6c17b）；ruff check/format 通过。
完整命令仍为下方 95694 同范围命令。下一步是角色变化、JSON 比较与请求投影
的剩余核验及更广回归，不重复这两个已验证的取消入口。

后续边界补证：旧宿主仅使用 `extension.world_state.*` key 不能据此前缀改变
撤销语义。新增测试在修复前 74426 实际退出 1（b08403，输出 0c26a4）：
12 passed / 1 failed，旧片段错误变为静默 inactive。生产改为同时检查 key 与
对应 content_kind 的显式来源；缺少该来源的旧自定义片段保持通用规则。

Runtime 集成现参数覆盖自动/手动 compact：冷恢复后最后一次模型回报高用量，
下一 Turn 真实触发自动压缩，验证 renderer 收到 Absent 且请求仅有新窗口正文。
另加重复 section、非法返回值、非法角色和 renderer 抛错四例：均只有一个
TurnFailed，没有 TurnCompleted、模型请求或部分扩展 ContextItem 提交。

联合范围同下方命令，4 workers/loadfile：4395 实际退出 0（d1ecae），
596 passed / 5.70s；补故障测试后 60078 实际退出 0（1ffa1f），
**600 passed / 5.77s**。静态检查通过。无活动测试句柄。
尚需进一步检查取消控制流、同 key 接管的来源/角色转换、JSON 比较语义与
完整请求元数据投影，再做匹配变更范围的更广回归；不把这 600 项当全量。

新增 `context/extension_world_state.py`：WorldStateContributor 异步捕获本 Step
的 WorldStateSection，包含稳定 ID、不可变 JSON 字符串、render_diff、legacy /
retained matcher。独立命名空间避免与内置权限/模型 key 混用。旧 contributor
仍使用原 PromptContribution 行为；同一宿主对象也可以实现两种贡献入口。

ContextBuilder 将 section 占位项按 WORLD_STATE_EXTENSIONS 顺序组装，并将
回调放在瞬态 ContextSnapshot；WindowManager 的普通 prepare 与自动压缩新
窗口均传递实际 sections。历史仅写带版本的比较数据和渲染后的 ContextItem，
不保存回调、不改数据库 schema。新来源可返回无正文而更新 snapshot；消失
时追加静默 inactive 状态，再次挂载看到 Absent。冷历史直接回放已渲染正文。

`tests/integration/test_extension_world_state_runtime.py` 经过真实 Runtime、
SQLite、模型请求和手动 compact，验证首次、重复、生产者自定义增量、silent
更新、两次冷恢复、来源消失/再挂载、完整历史前缀和新窗口重建。
`tests/unit/context/test_extension_world_state.py` 验证损坏/旧状态 Unknown、
retained matcher、legacy matcher、非法 snapshot，以及旧通用片段仍显式撤销。

测试记录：

- 首批 69682 实际退出 0（319bec）：396 passed / 2.89s。
- 扩大批 69030 实际退出 1（e8c921）：593 passed、1 collection error / 5.28s。
  新增 unit/integration 同名模块导致 pytest 导入冲突，不能计为通过；已用
  apply_patch 将 integration 文件改名为 test_extension_world_state_runtime.py。
- 同范围重跑 95694 实际退出 0（752d61）：594 passed / 5.40s。
  命令：`PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring .venv/bin/pytest
  tests/unit/context tests/unit/core tests/integration/test_extension_world_state_runtime.py
  tests/integration/test_extension_context_lifecycle.py
  tests/integration/test_plugin_guidance_compaction_wire.py -n 4 --dist=loadfile
  --max-worker-restart=0 -o addopts='' -q --tb=short`。
  使用 4 个 worker，独立临时数据库，无供应商请求；未要求为小批启动 96 worker。

仍需完成（自动压缩、重复 ID、非法 renderer/角色已有上方后续证据）：
同 key 接管边界；原生 JSON object null 清理规则的进一步核对；
扩展移除/冷恢复与旧命名空间兼容审查，以及受影响的全量回归和静态检查。
此批已改变生产，81536 仅是此前生产基线，不能充当本实现的全量验收。

## 原生真实入口

基准 ddf04ad26789d040f9ef6a96736f76602e35a6cc。

- `core/src/session/world_state.rs::build_world_state_for_step` 调用每个扩展的
  `contribute_world_state`，将结果交给 `WorldState::add_extension_section`。
- `ext/extension-api/src/contributors/world_state.rs::WorldStateSectionContribution`
  持有稳定 ID、JSON snapshot、render_diff，以及可选 legacy/retained matcher。
  回调接收 Absent / Unknown / Known；可返回无模型正文而仍更新比较快照。
- `core/src/context/world_state/mod.rs::ExtensionWorldStateSection` 负责模型正文
  envelope，`render_history_diff` 根据快照和保留正文决定 previous 的状态。
  指定 retained matcher 后，即使有快照但正文已不在历史中，也按 Absent 渲染。
- `WorldState::render_with` 只遍历当前 sections，不为已消失的扩展凭空生成
  通用撤销正文。删除快照与向模型声明旧事实失效不是同一个动作。

上述为本地源码证据，未执行原生 Rust 测试，不能称为跨实现运行测试通过。

## Corki 的入口与实测

`context/extensions.py` 的 contributor 返回 `PromptContribution`；后者有
snapshot_state，但没有 producer-owned diff / legacy / retained 回调。
`ContextBuilder` 构造快照，`WindowManager.prepare` 调用
`changed_context_items`。后者对消失且旧正文非空的 key 调用 `_removal`；
`_render_update` 只有内置 key 特例，自定义 key 走通用旧指导失效声明。
已有 InitialContextContributor 是窗口级来源，不等于可每 Step 更新的
自定义 world-state section，不能用它绕过此缺口。

本轮离线诊断：`LangGraphRuntime.acreate`，独立临时目录和真实 SQLite，关闭
skills/plugins/memories；自定义 Source 首 Turn 返回 developer contribution
`fixture.dynamic`（WORLD_STATE_EXTENSIONS slot，snapshot_state=revision-1），
次 Turn 返回空 tuple；普通脚本模型每次完成，无网络请求。

诊断进程 64092 已实际退出 0（输出 2699af）：

```json
{"terminal_events":["TurnCompleted","TurnCompleted"],"history_prefix_preserved":true,"new_context":[{"key":"fixture.dynamic","content":"The previously provided instructions and facts for 'fixture.dynamic' no longer apply.","snapshot_content":""}],"model_request_count":2}
```

该输出证明当前自动撤销行为，不是修复通过。历史前缀保持正确；缺口在于
扩展无法表达“停止贡献但不撤销旧事实”及其它由生产者控制的增量语义。

## 下一批实现及验收边界

新增独立的 producer-owned world-state 契约，不改变旧 PromptContribution
宿主的默认替换/撤销语义，不用内置 key 白名单冒充通用接口。

1. 每 Step 捕获稳定 ID、可持久化比较状态和渲染/匹配策略，真实接入
   ContextBuilder → WindowManager → 请求历史；回调不写入数据库/checkpoint。
2. 支持 Absent/Known/Unknown、silent snapshot、自定义增量和 retained matcher；
   消失的 producer-owned section 只按其契约处理比较状态，不生成通用撤销。
3. 冷恢复必须能区分该类 section 与旧通用片段，保留原始历史；对损坏状态、
   重复 ID、角色与输入归属作校验，不让自定义回调取得额外权限。
4. 行为测试至少覆盖首次、无变化、自定义更新、silent 更新、来源消失、
   冷恢复、压缩新窗口、旧正文有而快照缺失、快照有而匹配正文缺失。
   保留现有通用 contributor、memory 初始窗口、skills/plugin 专项回归。

上述原始诊断轮没有改生产或测试，不宣称 C1/C2 已完成；此明确缺口优先于重复已有
Hook 交错排列和重复刷新不受影响的全量结果。
