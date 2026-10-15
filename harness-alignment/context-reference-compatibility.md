# C1/C2：旧 reference 的行为映射与格式边界

结论：独立 Codex `TurnContextItem/reference_context_item` 文件格式导入不在本目标
范围内；Corki 自身旧数据库/Message/ContextItem/已准入设置的恢复仍是必需能力，
不能以格式不同免除。本项不是按语言差异判定可接受，也没有移除核心恢复要求。

## 源码追踪

基准 Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc。
session/rollout_reconstruction.rs:239 从 RolloutItem::TurnContext 按 Turn 身份
重建 reference 和 previous_turn_settings；context_manager/history.rs保存该reference。
session/world_state.rs:105 为 personality 传入 reference 的 model/personality，
缺reference时才回退previous_model与空personality。
context/world_state/personality.rs：Known直接比较确切snapshot；Unknown使用
previous参考；Absent仅在未烘焙且没有跨模型重复注入时发布。这些是核心行为；
它们不需要官方服务，也不能把本地reference误称官方API。

Corki当前HEAD a7c97ecf984dc63ef63a59640cf352f0ee0f99c7加现有工作区：
storage/sqlite.py:364迁移legacy Message，:930等从kind/payload恢复canonical项；
protocol/items.py::items_from_messages保留ID/时间/角色，developer转换成
legacy.developer；item_from_payload没有接收Codex RolloutItem的入口。
检查本地所有已有提交的reference_context变更历史及HEAD的personality/reference
源码均无对应旧字段；HEAD的ContextItem已支持snapshot_state，但没有原生独立
TurnContext格式。当前新增选择格式在protocol/settings.py，不能把后来新增的
Thread默认值当成旧窗口确切选择的证据。

## 按语义映射，而非复制字段

| 状态用途 | Corki所有者与验证 |
|---|---|
| 窗口中确切选择，用于增量比较 | ContextItem.snapshot_state；Known优先于后来的旧文字标签，test_personality_updates |
| 有旧标签、缺确切选择 | items_from_messages → legacy.developer → world_state的Unknown；仅使用已知model_transition，不从风格正文或新宿主默认推断旧选择。test_personality_legacy涵盖无旧模型/同模型/不同模型，旧正文与历史前缀不变 |
| 下次启动的默认选择 | thread_model_settings.personality，缺旧列迁移为None；None/none/friendly/pragmatic四值分别保留。test_personality_migration；隐式启动配置组恢复另有test_resume_model_settings已有全量证据 |
| 已准入Turn/Step，禁止宿主新默认覆盖 | TurnRecord.model_settings与checkpoint turn_model_settings/step_model_settings；resume_pending校验身份、类型及账本冲突后绑定。test_personality_selection_recovery四种选择×门控，冷恢复仍只采样一次，再resume为空 |
| 压缩后重建与普通供应商请求 | test_personality_compaction/wire验证窗口重建、跨模型去重、普通两接口和标签隔离，不新增原生请求字段 |

复核七文件：personality_legacy、personality_selection_recovery、personality_updates、
personality_compaction、personality_wire、storage/test_personality_migration、
context/test_personality_history。**43 passed，4.64秒**（e3fdab），12737退出0，
4 workers/loadfile/禁重启、null keyring、当前sandbox compiler。逐项断言已阅读，
没有用“找不到同名字段”单独充当一致证明。旧全量35013也包含这些测试。

本轮只作源码/适用性核定和现有回归，未修改生产或测试。核销的是旧审计里
“独立reference格式仍需核定”的子项，不宣称全部C1/C2 section、任意损坏格式、
跨Hook故障或第三方rollout迁移都已验证。后续转向具体其它section/提交窗口，
不再把新增Codex历史导入器当核心对齐完成条件。CLI对齐与同步create兼容决策均不变。
