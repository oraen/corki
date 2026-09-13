# B1：注册来源与失败发布复核

## 当前 B1 验收结论

重新对照固定参考 spec_plan.rs 的 core → MCP → extension → dynamic 构建顺序，
registry.rs::register_external_with_exposure 的先到获胜、首冲突记录与终端保留名。
Corki 显式 ToolSource 来源目录、隔离 schema、owner 撤销及 Step snapshot 对应
相同选择行为；不是依赖 ToolSpec.source，也不是按异步加载完成顺序争抢名称。
PluginReload.publish 在同步发布前检查 generation/closed/finished，失败候选不
接管现有工具。源码与此前 Runtime 来源组合证据一致，未发现需新增的生产分支。

本轮实际复核：external_tool_collisions、external_registry、registry_publication、
step_registry_snapshot 四文件 48 passed/3.18秒（53f11c）；实际 Step 快照与
plugin_config_reload 两文件 213 passed/111.08秒（54ff9a），均退出0、无跳过。
后组包含 Step 内 handler/schema 捕获、重试保留 router、插件/skill/清单/代码变化、
撤销与重新启用、失败候选、宿主目录覆盖及慢关闭。历史 native/codex_apps 标签
是旧配置兼容与禁用入口负例，不代表启用已排除的官方能力。

B1 注册、来源优先级、撤销回退、候选失败原子性与 Step 发布生命周期已核验。
不再因 E4 的同步构造资源接管待用户决策而把 B1 全部维持为“各来源待收敛”；
借用 registry 回滚和分配资源的异步清理是不同要求。完整 MCP 传输/Code Mode
任务关闭归 B9/B10/E7，第三方注册函数任意外部副作用不承诺自动撤销。

## 参考与当前调用链

参考仍为干净的 Codex ddf04ad26789d040f9ef6a96736f76602e35a6cc
（969c17）。core/src/tools/spec_plan.rs::build_tool_router 每 Step 新建注册表，
先 add_core_tool_sources，再 append_mcp_tools、append_extension_tool_executors、
append_dynamic_tool_runtimes，最后 finalize_tool_router。registry.rs 的
register_external_with_exposure 保留先到的获胜者，并记录首个冲突；普通终端
保留名称不允许外部工具接管。原生 namespace/hosted 相关分支不属于实现范围。

Corki 的对应来源不是依据 ToolSpec.source 字符串猜测：

- 内置及宿主可信工具走 ToolRegistry.register，Runtime 初始化后 seal。
- MCPManager 创建 ToolSource.MCP owner；_publish_generation 按捕获目录顺序
  构造候选，replace_owned 完成 schema 捕获后才同步发布 registry/manager 状态。
- PluginManager 创建 EXTENSION owner；prepare_reload 用隔离的 PluginRegistrar
  收集候选，prepare_external 完整准备后由 PluginReload.publish 校验 generation
  并发布。失败模块仍归 manager/candidate 清理，失败的插件注册不污染其他来源。
- 嵌入宿主使用公开的 create_owner(source=ToolSource.DYNAMIC) 与 replace_owned；
  该能力在启动前取得，运行中可发布新目录。它真实接入 Runtime，不是仅预留枚举。
- _RegistrySources.resolve 固定可信/MCP/扩展/动态优先级，撤销获胜者后恢复候选；
  旧 Step 持有旧 handler/schema，后续 Step 才观察新发布，不按完成顺序抢占。

借入宿主注册表的构造失败通过 construction.rollback_registry/composition
同步恢复，再进入异步关闭；不在关闭结束后覆盖宿主的新变更。既有详细证据见
mcp-startup-review.md，包含插件注册成功后后期构造失败、关闭挂起时宿主追加。

## 本批验收补强

扩展 test_external_tool_collisions.py 的实际 Runtime 组合：MCP 获胜执行 →
撤销 MCP → 插件 fallback 执行 → 关闭并冷恢复 → 动态 fallback 执行 →
注入第二个候选 schema 捕获失败 → 已封存快照、原 handler、owner 名称不变 →
下一 Turn 再执行旧动态工具并收到 Observation。最终请求保留三个来源的原始结果。
覆盖 disabled/compatible/旧 native 配置别名；native 在当前配置中归一化为普通路径，
不代表执行了原生协议。模型为脚本，MCP 为本地 mock，插件为实际 Python 模块。

初版把快照 identity 断言放在冷构造但尚未初始化的位置，三例失败
（3c5693：3失败、290通过）；未封存 registry 每次 snapshot 新建，属于夹具错误，
不是生产回滚缺陷。改在首次动态 Turn 完成后注入，没有放宽 identity 断言。
最终来源组合/来源排序/外部注册/发布四文件 44通过，4.88秒（289b3c，23789退出0）。
上轮联合中的构造回滚、初始化、插件重载及配置重载均通过，但不能将其写成
同一次全绿293项，也不把本次称为生产修复的先红后绿。

README 的旧段落仍声称 native search 输出及 namespace wire 行为，且未区分
归档结果和当前调用资格；已按 models/namespaces.py、discovery.py 的实际边界修正。
没有恢复任何排除协议，没有修改 CLI 或 teach.md。
最终静态检查通过（a7431c）：ruff check、1190文件格式、compileall、77包依赖
与 git diff --check；本批测试进程均已退出，无需继续轮询。

## 状态与剩余边界

B1 来源注册、冲突选择、撤销回退与失败候选发布已有真实 Runtime 组合证据。
整体资源生命周期仍不能随之关闭：运行中事件循环调用同步 Runtime.create 且
构造失败时缺少可等待 cleanup owner 的已知接口边界仍见 mcp-startup-review.md；
本批未修改该接口，也不承诺回滚第三方 handler 内部任意副作用或跨线程注册。
B10 cell 生命周期、A6/E7 全部关闭路径仍独立验收，CLI 对齐继续暂停。
