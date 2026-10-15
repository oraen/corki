# Persistent 本地模式接入选择

已查明的接入点：CorkiSettings.reasoning_effort 是普通供应商参数，
ContextBuilder.with_instruction_settings 捕获准入后的配置副本，graph 每 Turn
使用该副本构建上下文。这里可以接入本地提示策略，但不能把新增模式仅做成
未使用的配置字段，也不能依赖 provider 名称启用。

Codex PersistentModeState 按 effective effort 激活，非 basic guardian 才注入；
目录覆盖/显式空值、正文 hash、替换与撤销都是本地行为。默认资产明确只允许
原授权范围内跟进，不授予额外外部写操作。再次采样由宿主提供，资产本身没有
后台调度能力。当前 Corki 没有等价本地模式 producer，仍标记缺失而非一致。

建议入口：显式独立的本地 persistent_mode 配置，默认关闭，不改变或解释
供应商 reasoning_effort 字符串；开启后注入原授权范围内跟进策略，接入现有
Turn 快照/上下文差异生命周期。它不自动建立无限循环或创建新任务，宿主仍
决定何时继续调用 Runtime。该入口与原生 effort 耦合方式不同，须用户确认，
不能把实现容易的独立开关悄悄替代用户希望的完整持续执行模式。

若用户要 Runtime 自己调度持续跟进，则需另行明确停止条件、持久状态与
取消/关闭契约，不以一段提示词冒充已实现调度。未确认前不改生产接口。
