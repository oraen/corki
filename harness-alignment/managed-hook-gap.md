# 宿主必需 Hook：来源与加载失败边界

状态：缺失（配置控制的核心路径），不是默认路径，也不是官方服务依赖。

## 当前实现：宿主策略基本链已接入（覆盖上方旧状态）

宿主输入边界专项已补：test_managed_hook_policy_validation 的 19 例覆盖事件/
分组结构、非法 JSON/长度、严格 bool，以及 settings 不将 dict/string 升格为
宿主对象。test_managed_hooks 的 user/project 两例把普通来源路径伪装成
requirements.toml、注入 is_managed/managed_hook_policy/allow_managed_hooks_only，
真实 Runtime 构造及刷新后仍无宿主策略，也无可执行 SessionStart，Turn 正常完成。
依据 Codex discovery::append_managed_requirement_handlers 从 requirements 而非
普通 handler 字段赋予权限，以及 Corki discover 的独立 managed_policy 参数。
关联四文件 64 通过、3.41 秒、4 worker、退出 0（21aa41）。首轮同名测试文件
收集冲突（3ed559），随后新增夹具误用异步重载接口及遗漏模型 aclose（d6ac86），
均已修正；没有改生产或放宽行为断言。本批不是全量回归或所有来源组合的证明。
这两项不再列为完全未验；下一步回到 A–E 验收索引核对剩余实质差异。

必需 Hook 构造回滚已增加真实 graph 验证：SessionEnd 的不支持 MCP handler 与
缺失 command 两种非法定义，分别覆盖持久/临时存储和自建/借用模型，共 8 例。
acreate 拒绝后注册表恢复为空，自建模型关闭、借用模型不关闭，进程管理器收尾，
无新后台任务/checkpoint/写锁目录；临时 SQLite 连接确已关闭且无数据库落盘。
本批未改生产。首次运行因新增测试插入位置挪错原测试末尾断言而 8 失败/33 通过
（9284d5），已将断言移回原测试，未删除断言或放宽标准。最终关联三个文件
43 通过、3.29 秒、4 worker、退出 0（95fd94），Ruff 通过。
这不证明运行中同步 create 的回滚，也不覆盖所有宿主策略结构或伪造输入；这些
仍须单独核对。全量结果未刷新，不以本轮局部通过宣称 A–E 完成。

项目/插件来源策略已补真实 Runtime 验证：test_managed_hooks 将原 user 来源扩展
为 user/project/plugin × only_managed × enabled，项目来自已准入 ConfigLayer，
插件使用实际 manifest/hooks.json 与用户指纹授权；先刷新配置再采样。普通模式
保留已授权来源，only-managed 屏蔽，关闭总开关不执行任何来源；宿主 start/end
仍不受用户 enabled=false 撤销。16 专项通过（78d54d），关联来源/身份/配置
刷新/插件重载 40 通过、3.90 秒（2739ca），Ruff/diff 通过。
本批仅测试，证明的是已授权普通来源被过滤，不代表项目伪造宿主对象或所有
malformed JSON 的反例已验；下一批关注输入验证与失败时资源/注册回滚。

在途异步任务与开关关闭已直接补验：test_async_stop_hooks 的真实 Python 转录
读取进程，在首 Turn 已完成、后台仍等待时发布 features.hooks=false（保留定义），
与原“移除定义”对照。两者均保持同一 AsyncHooks owner 与精确任务身份，后续
Turn/可选手动压缩不启动新 Hook，释放原子进程后它可读到新转录，唯一执行记录
有完成结果。4 专项通过（7d9a2c），关联 33 通过/6.34 秒/退出 0（caef5a）。
本批仅测试；此证据是原任务正常完成，不冒称开关切换后取消/OS 强杀的全部组合。
源码依据为 Codex Hooks::reconfigured 保留 command_runtime，Corki publish
只替换发现快照、AsyncHooks 仍由 Runtime 关闭流程持有。

删除开关键的热/冷差异已修复：for_directory 默认 true，但此前刷新 fallback
使用当前生效值，false 后删除键仍关闭。两个不同初始值的真实文件/Runtime
反例失败（c6eae5）。现在目录加载保存独立 _hooks_reload_default，Runtime
固定该初始缺省值用于后续缺键解析；直接构造 settings 无目录缺省记录时保留
宿主初始 hooks_enabled 作为缺省，不让一次动态覆盖污染默认。
false→删键恢复→false→true 的实际 Stop 次数与冷解析一致，含初始 false/true；
关联 managed/插件重载/异步 Stop 31 通过、5.56 秒/退出 0（6cb921），Ruff/diff
通过（bd7dfb）。本次有生产修改，旧全量结果不能代表当前全量。

运行中显式 true→false→true 与真实文件解析已补验：test_hook_feature_reload.py
使用 for_directory/load_local_config 和 Runtime 刷新后真实跨 Turn 的 Stop 调用，
宿主策略身份保留，关闭期间无新调用，恢复后再调用。非法字符串值在文件解析及
刷新均拒绝，旧快照保持。专项 1 通过（5b175a）。初次关联 29 通过/1 失败
（63dcf6），原因是 managed fixture 错把并发 SessionStart runner 顺序当配置顺序；
start_hooks.py:231 create_task/gather 明确并发。改为分别断言配置快照顺序、实际
调用集合/次数以及 SessionEnd 最后执行，不改生产调度。最终 30 通过/5.61 秒
（df1bcd），Ruff/格式/diff 通过（d821ac）。
本批仅测试；删除配置键后的默认恢复、在途异步义务及项目/插件来源仍需收敛，
不把相邻异步测试通过当作开关切换交错已直接验证。

总开关已接入：Codex core/session/mod.rs::build_hooks_config 读取
Feature::CodexHooks，features/src/lib.rs 的 key 为 hooks、Stable、默认 true。
CorkiSettings.hooks_enabled 默认 true，从 features.hooks 解析并严格校验 bool；
graph 与配置刷新 prepare 均传递开关。关闭时返回各事件空快照，不执行发现、不
因普通/必需 handler 加载错误拒绝启动；没有引入官方 builtin/notify 服务。
新增 enabled false/true × 来源策略/非法定义共 8 个场景先失败于缺字段
（d187a5），修复后 8 通过（8df1f4）；关联来源/身份/start/end/重载 84 通过，
12.57 秒/退出 0（6c5852），Ruff/diff 通过（6a711c）。
当前专项验证初始值在刷新中保持；运行中开关转换、配置文件解析及已执行异步
义务收尾仍待专项，不把关闭开关理解为撤销已经发生的副作用。

新增 config/hooks.py::ManagedHookPolicy（绝对来源、不可变 JSON 定义、only_managed），
仅由宿主 CorkiSettings 显式传入，不从普通配置反序列化。graph 初始 prepare 与
Runtime 配置刷新 prepare 传递同一策略，原 settings.replace 保留宿主值。
discover 先加入宿主来源；宿主项跳过普通用户 enabled/hash，普通来源仍走原授权。
only_managed 移除普通来源；必需 command_identity 错误抛出构造异常，普通错误
继续为警告。SessionEnd MCP 仍不支持，不在关闭后重新连接执行。
两原入口反例转绿（286b64）；追加真实刷新与非法必需/普通对照，4 专项通过
（332438）。功能总开关、项目/插件来源交叉、完整回滚资源与更多 malformed 定义
仍需补齐，不将本批基本链称全量策略对齐。全量回归早于此次生产变更。
首次关联命令含不存在的 tests/unit/core/test_stop_hooks.py，xdist 无收集、退出 5
（d063f0），不是通过；已移除错误路径重新执行真实存在的六个集成文件。

## 当前反例与接入位置

新增 test_managed_hooks.py 两个真实 Runtime 场景（only_managed false/true），
要求宿主 SessionStart 不被用户 enabled=false 撤销、普通授权 Hook 按来源策略
保留/屏蔽、SessionEnd 实际执行。当前均失败于 ManagedHookPolicy 模块不存在
（dc7f83，session 80548 退出 1），行为断言尚未执行，不算已验证能力。
预期宿主端口为 CorkiSettings.managed_hook_policy，使用独立不可变策略对象，
不通过 LocalConfigState 从 user/project 配置生成。初始接入在 graph.py 的
StopHooks.prepare；重载接入在 runtime.py::_apply_configuration 对应 prepare 调用
（当前约 614 行）。两个快照必须使用同一宿主权威值，不能仅补初始构造。
下一批直接实施策略值与来源选择/失败分类，不再新增同样入口缺失的反例。

## Codex 调用链与范围

- `config/src/config_requirements.rs::ConfigRequirementsToml` 接受系统
  requirements.toml/MDM 的 `hooks` 和 `allow_managed_hooks_only`。仅参考本地
  策略语义；不复制同结构中的官方登录、ChatGPT workspace 或平台管理入口。
- 约 1960 行把非空 hooks 转为带来源约束的 managed_hooks，不允许普通配置替换。
- `hooks/src/engine/discovery.rs::append_managed_requirement_handlers` 从 requirements
  注入来源，标记 is_managed 与 HookRequirement::Required。加载失败同时进入
  warnings 与 required_load_errors；同类普通用户 Hook 错误只是警告。
- `hook_enabled` / `hook_trust_status` 让 managed 项不依赖普通用户 trusted_hash，
  也不受普通 enabled=false 撤销；这是宿主权限，不是项目文件可以自称的标记。
- `HookDiscoveryPolicy::allows_source` 实施 allow_managed_hooks_only。
- `engine/mod.rs::new` 在功能关闭时只保留 builtin，清除普通 warnings 和
  required_load_errors。不能把“required”扩张成无条件启用整个 Hook 功能。
- `registry.rs::Hooks::new` 汇总 required_load_errors 并拒绝初始化。
  `Hooks::reconfigured` 调用 from_config，没有同样的 Result 拒绝门；动态重载
  必须单独核对，不能凭 new 的行为声称两者一致。
- SessionEnd 的不支持 MCP 类型、参数非法等错误也走此来源分类；这不是在
  MCP 已关闭后重连执行 SessionEnd 的理由。

## Corki 当前链

`LocalConfigState` 只接受 user/project ConfigLayer，插件来自显式 plugin manifest；
`stop_hooks.discover` 对它们都使用用户 state 指纹授权。没有 managed Hook 来源，
所有 command_identity 的 ValueError 仅变成 warnings。`StopHookService.prepare`
构造各事件快照，publish 一次发布。`ExecutionRequirementsLayer` 是另一个宿主
执行权限端口，明确拒绝 hooks 等未支持字段，不能把它当作已有 Hook 策略接口。

影响：宿主目前无法要求某个 Hook 必须可加载，也无法限制只运行宿主 Hook。
不能通过把 project 层改名为 managed 或给其自动 trusted_hash 来补齐。

## 下一批最小完整实现与验收

1. 增加显式、不可由项目配置生成的宿主 Hook 策略值，携带来源、必需 definitions
   与 only-managed 策略；接入 Runtime 构造和真实 Hook 快照，不新增官方管理服务。
2. 复用已有命令归一化/事件限制；区分普通加载告警与必需加载错误，构造失败走
   acreate 的已有资源/注册回滚。不为失败配置留下半个运行时。
3. 以真实 Runtime 反例证明：合法宿主 SessionStart/SessionEnd 可执行；普通 state
   不能撤销宿主项；only-managed 屏蔽 user/project/plugin 但不自动授权它们；
   非法必需 SessionEnd MCP 拒绝启动，而相同普通配置仅警告且无 MCP 调用。
4. 功能总开关的来源约束与重载语义先追踪后实现，不凭名称新增管理员不可绕过
   的断言，也不擅自让项目配置关闭宿主政策。无宿主政策时保留现有普通行为。

本轮只完成来源/行为差异审计，尚未实现上述入口或运行对应反例。整体 A–E 未完成，
CLI 对齐暂停；这项不是重新引入 OpenAI 官方功能。
