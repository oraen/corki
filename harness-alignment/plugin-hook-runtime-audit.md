# 插件 Stop hook 执行来源与发布边界

状态：Legacy 同步 Stop 来源已接入；插件三窗口冷恢复与基础热刷新已验证。
新格式 Agent Plugin 在参考默认路径不执行 hooks。
参考 commit ddf04ad26789d040f9ef6a96736f76602e35a6cc，本次参考工作树干净。
本批接续本地 TOML/JSON Stop hook，不涉及 Apps、官方插件服务或认证分支。

## 当前实现与验证

最新整次调用快照验证：热刷新测试扩展为两个真实命令，分别在 Stop 开始前、
第一个命令完成但第二个命令尚未执行时发布新配置，覆盖重新批准/旧指纹/撤销。
执行中刷新时同一次 Stop 的两个命令均保持旧快照；下一次 Stop 按新定义/授权
执行或跳过。测试包装真实 run_command 的返回边界，没有伪造命令结果；六项通过
（b7b269，1.58 秒）；联合 hook/插件/冷恢复/evaluation/账本 310 passed
（ad2845，11.79 秒），静态检查通过。本轮未改生产逻辑。并发刷新顺序、命令进程尚未退出时
的取消/刷新交错仍未以此项证明完成。

最新纠正：前轮把插件能力的 Turn 快照准入误套到 hook 生命周期。进一步追踪
core/src/session/mod.rs::refresh_hooks（1916 起）及 hook_runtime.rs::run_turn_stop_hooks
（446 起）：刷新后原子发布 Hooks；每次 Stop 获取 sess.hooks()，再用该捕获对象
执行整次调用。因此不能把 hook 更新推迟到下一 Turn，也不能用旧 Step 配置去核验
新插件定义。此前文档和 graph._plugins 方案在这一点上不一致，现已修正。

StopHooks.prepare 在配置发布前生成不可变 commands/warnings；publish 单次赋值
发布完整快照，旧 graph 与新 graph 共用该 session 服务。Runtime 配置刷新在原有
准备/校验阶段生成 hook 快照，发布配置时同步发布；run 先捕获快照，再进行异步
操作。插件技能/上下文原有新 Turn 准入不变，删除错误的 graph.with_plugins 路径。

test_stop_hook_plugin_reload.py 在模型采样中修改实际文件并调用宿主配置准备/发布：
重新批准、保留旧哈希、显式撤销三种情况，当前 Stop 及下个 Turn 都使用对应的新
授权结果。正确场景修复前 3 failed（d21ac1），修复后与所有 hook 集成联合
53 passed（23c839，9.20 秒）；再与完整 plugin_config_reload、冷恢复、插件单元、
evaluation 和账本联合 461 passed（17a7b5，103.08 秒）。ruff/format、compileall、
依赖和 diff 检查通过。已执行中的整次调用快照由顶部后续验证补充，仍需覆盖并发刷新顺序；
不可用这个三项测试宣称所有刷新边界已经完成。

最新冷恢复证据：test_stop_hook_recovery.py 增加真实 Legacy 插件来源，经插件
加载→graph _finalize→SQLite 在命令结果未知/完成提交/反馈追加三个窗口挂起，
取消并关闭 warm Runtime 后重建 cold Runtime。原路径下未知结果失败且不重跑，
完成结果复用并继续模型，反馈仅一份；无重复用户输入，二次 resume_pending 为空。
账本 request 中 PLUGIN_ROOT 明确断言，确保测试确实经过插件环境而非本地来源。

同三个窗口额外移动临时插件目录后冷恢复：manifest 安装身份/来源相同，但实际
环境已变化，因此执行账本报告 identity collision 并终止，不重放旧操作、不继续
模型采样。已追加反馈保留；未追加反馈不凭变更后的来源重新生成。本轮不修改
生产代码，只增加该故障窗口证据。联合插件/恢复/evaluation/账本 276 passed
（a1b26d，6.38 秒），ruff/format/compileall/依赖检查通过。

plugins/hooks.py 捕获 Legacy 默认文件、路径/路径列表、内联对象/列表为不可变
PluginHookFile，PluginManifest 保留快照；坏文件隔离、保留有效兄弟来源。
本地 Legacy 和 installed_manifest 均接入，新 AgentPlugin 分支仍不加载 hooks。
安装数据目录使用安装身份的现有 data_root，而非展示名称。

最初实现曾让 Runtime 初建图绑定 plugins 元组并在新 Turn 更换；此方案已由顶部
session hook 快照发布修正。Stop discovery 将插件追加在本地来源后，
key 使用 manifest.identity:relative_source:event:group:handler，继续只读用户授权。
命令模板先做规范化哈希，再展开四个路径变量并注入命令环境；账本 request 额外
记录插件环境，旧本地命令 request 不变，因此不破坏原有执行身份。

test_stop_hook_plugin.py 真实 Runtime 覆盖五种来源 × 有/无用户批准，共 10 项，
批准后产生 PLUGIN_CHECK 反馈并继续，脚本验证 PLUGIN_ROOT/DATA 与 CLAUDE
别名一致。test_hook_sources.py 验证坏文件保留兄弟、快照不随磁盘变动、安装身份
数据根、禁用插件和 AgentPlugin 的零来源。联合本地 hook/冷恢复、插件、evaluation、
账本 298 passed in 9.40s（257d4a）；ruff/format/compileall/依赖/diff 检查通过。

注意首次集成夹具错把 Legacy manifest 放到根 plugin.json，10 个失败发生在插件
发现之前（087103/e325f5），不能作为旧实现功能反例。修正到 .codex-plugin/plugin.json
后 10 项通过（d413c7）。插件专属刷新/冷恢复、撤销、安装包实际执行链路仍需补齐；
已有本地 hook 冷恢复通过不能替代它们。下文“缺失”为实施前审计记录。

## 真实调用链与格式边界

core-plugins/src/loader.rs::load_plugin_hooks_from_layer_stack 以 HooksOnly 范围加载
已启用插件，向上返回 effective_plugin_hook_sources；load_outcome.rs 仅投影有效
插件。loader.rs 加载单插件的 954–965 行明确区分格式：AgentPlugin 返回两个空
Vec，只有 Legacy 调用 load_plugin_hooks。不能因 Agent Plugin overlay 校验过
hooks 元数据，就执行 overlay 中的命令。

Legacy 的 load_plugin_hooks（1189 起）支持 manifest 指定文件/文件列表或内联
文件对象/对象列表；没有指定时读取 hooks/hooks.json。显式路径由 manifest.rs
resolve_manifest_hooks 处理。文件读/解析失败仅生成 hook 警告，不撤销插件其他
能力；源顺序按声明保留。内联来源的相对身份是 plugin.json#hooks[index]，文件
来源为根目录相对路径（分隔符归一化）；不是直接使用安装缓存绝对路径做授权。

hooks/src/engine/discovery.rs::append_plugin_hook_sources 在普通配置来源之后追加。
declarations.rs::plugin_hook_key_source 是 plugin_id:source_relative_path，再附加
event/group/handler。plugin_id 是安装身份，不一定等于 manifest name。

源码在 command 规范化/哈希后才替换 ${PLUGIN_ROOT}/${PLUGIN_DATA} 及 CLAUDE
别名，同时携带对应环境变量给命令。这使授权指纹依赖命令模板，不依赖缓存目录
版本；但执行账本的 request 仍必须包含实际执行内容，禁止未知结果被换路径重放。
插件不提供用户授权，不能因 enabled、安装成功或市场来源而自动 bypass trust。

## Corki 当前链路与实际差异

- plugins/agent_manifest.py + agent_overlay.py + agent_hooks.py：只验证/展示 overlay
  元数据。对 AgentPlugin 不执行 hooks 与参考一致，不在这里新增执行入口。
- plugins/installed_manifest.py：Legacy 已解析 hooks 元数据，但 PluginManifest 无
  hook 快照字段，hooks 在返回模型时丢失。此处是真实功能缺口。
- plugins/manager.py::discover_manifests / prepare_reload / PluginReload.publish：已
  有隔离准备与原子发布机制，可携带不可变 hook 来源；无需另建文件轮询器。
- core/runtime.py::_admit_skill_configuration 在新 Turn 边界发布插件能力视图。
  此规则不适用于 hooks：必须通过独立 session hook 快照原子发布定义与授权，
  每次调用捕获同一个完整快照，而不是单独读取可变 manager.plugins。
- core/graph.py::_finalize → StopHooks.run 目前只从 settings.configuration 发现本地
  来源，未消费 PluginManager 的任何来源。因此 metadata-only 测试不证明插件执行。

## 实现顺序与验收要求

1. Legacy manifest 加载阶段捕获文件/内联 hook 来源、稳定相对身份、实际根与数据根；
   读取失败只隔离 hook。不得激活 AgentPlugin overlay hooks 或官方 executor Apps hooks。
2. PluginManifest/LoadedPlugin 保留不可变来源，复用原子发布；session hook 服务接收
   定义/授权的完整快照，每次调用捕获，已执行中的调用不能偷换；关闭无后台加载遗留。
3. Stop discovery 在本地来源之后追加插件，重用用户状态/指纹/独立账本；模板哈希与
   展开后的执行命令、环境分开。不得以 manifest name 替代安装身份授予权限。
4. 真实 Runtime 覆盖默认文件、显式文件列表、内联列表、缺失/坏文件、无批准/撤销、
   disabled/error 插件、路径变量及安装身份；模型收到 block 后继续。
5. 真实刷新与冷恢复覆盖改动前后 Turn 快照、已提交/结果未知窗口、不重跑旧命令；
   Agent Plugin 含 overlay hook 仍不执行，并验证插件其他能力未被失败 hook 撤销。

本文件保留实施前差异与完整验收要求，当前进展见顶部；尚不能宣称插件 hook 完整
对齐，本地 hooks.json 的 128 项也不能替代插件专属刷新/恢复验证。
