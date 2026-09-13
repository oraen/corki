# 技能 MCP 依赖安装：第278批审计、第280批基础实现（部分一致）

## 第284批：安装后的全局配置合并差异（修复前证据）

重读`core/src/mcp_skill_dependencies.rs::maybe_install_mcp_dependencies`：先加载全局
servers，新增且保存成功后，将整个servers快照以当前配置同名优先的方式合并再refresh；
added为空则提前返回，不触发合并。Corki writer只返回added名称，安装器只publish这些
候选，丢失本次已读取/保存的其他全局声明。该差异影响下一Step工具目录，优先级B/E。

新增真实Runtime测试`tests/integration/test_skill_dependency_global_merge.py`，临时home、
两Turn、fake MCP客户端、实际工具调用及Observation。66312d修复前为2failed3passed：
正常合并及当前同名disabled两场景缺少global工具；blocked/disabled/no_added控制通过。
拟让原子写入返回本次解析的全局快照及added名称，不在await后重新读取一个不同版本；
保留现有配置优先、管理约束、取消join及没有新增时不refresh的语义。
OAuth登录仍缺失，此修复不作为OAuth完成证据。

实现已接入：`config/toml_edits.py::MCPServerInstallResult`返回added及同一次锁内解析的
servers；无新增不写，坏文件/写入失败不返回成功结果。安装器无新增直接返回，有新增
时合并这个快照，不在写入await之后重新读取文件；新增候选仍复查准入，全局旧声明
（包括disabled）保留，manager在capture时统一constrain requirements/environment。
已有当前名字（包括disabled）优先，健康连接不重启，插件基础catalog同步扩展。
取消路径仍join写线程，取消后不发布；配置写入只保证进程内互斥，未新增跨进程编辑锁。

最终测试扩大为direct/compatible两路径共10个Runtime场景；compatible先检查global
定义未曝光，再调用tool_search，确认定义进入后续请求、实际调用、Observation回灌。
另新增无新增不写及竞争编辑不能改变返回快照两个unit场景。
cc02a2源码定向32passed5.90s；旧281安装555295配对4failed6passed2.88s（348模块来源）；
新安装8e4a59定向32passed7.39s（348模块来源）。fake客户端不执行真实命令，脚本模型
不证明真实模型选择质量。当前OAuth probe628868仍1failed2passed1.37s，未掩盖开放RED。

新产物`/tmp/corki-skill-global-merge.XBjwBh`，wheel/sdist构建950d48成功，隔离安装b0d3e4。
3e99d3核369个Python源码/wheel/安装文件字节一致；wheel SHA256
`daa62ea635b5c2dc464785dd557d8f12fe649afc10a1ff0088ffeb2aba15c627`。
e175d1全仓ruff、1023文件format、compileall、diff、72依赖检查通过；175932定向含probes
检查通过。a7aa14两仓HEAD不变、Codex clean、teach散列不变。未重建native或运行Rustsuite。
范围更广的config/mcp/skills/plugins及相关Runtime回归session **60158**在285批收取终态：
f5ccbf为5859passed1skipped717.90s，f6e3ae核skip为文件系统拒绝非UTF8文件名。
该选集不包含普通OAuth开放RED；全A–E仍未完成。

基准：Codex `ddf04ad26789d040f9ef6a96736f76602e35a6cc`；Corki
`a7c97ecf984dc63ef63a59640cf352f0ee0f99c7` 加现有工作区修改。
第278批本项为 **缺失**；第280批接入基础安装后为 **部分一致**，仍有下述未闭合分支。
不是第277批 connector 冲突修复的已验收能力。
本文件记录源码证据，不宣称运行过原生测试或新行为测试。

## 原生入口与适用条件

`codex-rs/core/src/session/turn.rs::build_skills_and_plugins`：

1. basic Guardian 直接返回，不解释父对话中的技能提及。
2. 从当前 Step MCP 工具生成 connector 名称冲突视图，调用宿主技能选择器。
3. 对选中的技能调用 `maybe_prompt_and_install_mcp_dependencies`。
4. 然后加载技能正文。安装入口不是前置 required_servers 收集器。

实现文件：`codex-rs/core/src/mcp_skill_dependencies.rs`，入口40行；
feature 定义 `features/src/lib.rs:1492` 为 Stable、默认开启。
入口仍要求 `login/src/auth/default_client.rs::is_first_party_originator`：
默认 originator、codex-tui、codex_vscode 或 `Codex ` 前缀。
因此第三方 originator 在原生实现中不走安装流程。Corki 没有这一客户端身份契约；
不能伪造 originator，也不能据此把技能依赖安装整体判为不适用。
需实现明确的宿主能力控制兼容路径，模型/技能文本不得控制此能力。

## 已追踪的数据、状态与故障契约

下表保留第278批修复前基线；第280、281批后的状态以后文实现与验收记录为准。

| 阶段 | 原生行为 | Corki 当前对应与差异 |
|---|---|---|
| 缺失判定 | `collect_missing_mcp_dependencies` 按 transport+trim后的URL/command判定已安装；已安装集不限enabled；跨技能去重 | `mcp/input_requirements.py` 只添加依赖value为required名称，无安装候选集 |
| 转换 | 默认streamable_http；stdio命令不拆分，不合成args；HTTP保留OAuth callback port；非法依赖告警并跳过 | `skills/models.py`、`skills/policy.py` 已保留这些字段，但输入入口不消费transport/URL/command |
| 会话去重 | session保存已经询问的canonical key；同URL不同名不会再次询问；不是工具session grant | 无相应依赖询问状态 |
| 首次准入 | 临时候选配置先经全局requirements约束，再经runtime catalog/attachment策略；只保留同名、同配置且enabled候选 | MCP有requirements/catalog基础设施，但未用于安装候选 |
| 决策 | `should_install_mcp_dependencies` 先算权限自动批准；不能自动批准且Never则跳过；否则专门询问安装/继续 | `MCPToolApprovals` 为单次工具执行授权，不能直接代表安装许可 |
| 等待与取消 | 取消分支产生空答复；正常答复后记录已询问key；只有Install进入安装 | 无对应宿主安装提示；ElicitationRouter现有kind只有server/tool/shell/patch |
| 二次准入 | 等待用户期间策略可变，确认后重新admit | 尚无安装候选的确认后复查 |
| 持久化 | 重新读取全局MCP配置；不覆盖已经存在的同名条目；批量保存；加载/保存失败告警返回 | toml_edits有原子写，但set_config_value不是安装事务；不能逐条直接写而绕过准入 |
| 认证 | 保存成功后逐一解析runtime HTTP client并发现OAuth；unknown/unsupported继续；支持时登录，特定scope错误允许无scope重试 | 有独立认证实现，但未接技能安装入口；尚需单独审计可复用程度 |
| 发布 | 合并全局配置到当前配置，已有当前名字优先；约束检查失败则告警；调用refresh_mcp_servers_now | request_reconcile存在，但没有安装调度者；approval配置reload不会新增普通服务器 |

权限自动批准源码：`codex-rs/codex-mcp/src/mcp/mod.rs:90`。
安装使用默认context，没有工具特有Approve覆盖；Never配合Disabled/External或具有
full-disk-write的Managed profile可自动批准；非Never不会自动批准。
不能把Corki的 `mcp_approval_policy == never` 单独解释成安装许可，必须使用当前有效权限。

`refresh_mcp_servers_now` 位于 `core/src/session/mcp.rs:645`：串行refresh semaphore，
更新session config、捕获ready selected roots/environment/capability discovery，经
runtime_config_for_step生成投影再publish。它不是简单修改registry。
该调用发生在已捕获的Step内；新增工具首次进入哪个请求还须追Step再绑定链路并测试，
不能未经证据要求“当前第一个模型请求一定包含新工具”。

原生相关测试已阅读：`core/tests/suite/codex_delegate.rs:257`
`codex_delegate_rejects_skill_mcp_dependency_installation_without_prompting`，
审查delegate使用受限profile，不应弹安装问题。未运行Rust测试。

## Corki 已确认的接入障碍

`core/runtime.py::_refresh_input_tools` 当前是读取用户items → 收集required字段 →
capture_tools → 固定技能connector视图 → 更新search/code mode。没有安装阶段。

`mcp/approval_persistence.py::MCPApprovalPersistence` 写的是单个tool approval，
reload经Runtime的`_prepare_configuration_reload`协调插件/技能/manager发布。
但 `MCPManager::_prepare_approval_configuration` 明确保留普通server的materialized
settings，只刷新Apps/plugin policy；所以不能把“已有配置reload”当成安装成功后会
自动出现新server的证据。安装必须明确更新宿主声明和catalog来源，同时保留host覆盖、
managed约束、现有generation/连接所有权。

## 修复与验收边界

优先级：B/E核心缺口；涉及执行外部命令、网络认证和持久化配置的授权边界。
不能把本地工程任务本身当成允许当前助手安装任意依赖的授权；实现测试全部用临时home、
mock MCP/OAuth，不访问真实服务或修改用户全局配置。

后续实现必须整批接Runtime，不能仅提交canonical helper或无消费者的feature字段：

- 宿主能力与有效权限控制；basic Guardian/受限delegate及feature关闭不产生安装副作用。
- 当前真正选中技能候选；同endpoint去重、同名不覆盖、invalid metadata不影响正文。
- 专用宿主询问与会话去重；拒绝、取消、关闭回收；不能接受server伪造的安装审批。
- 提示前后策略复查；写入临时全局配置；加载/写失败不冒充成功、不使正常Turn致命。
- 认证故障隔离和允许的scope重试；发布声明后工具发现→曝光→调用→Observation。
- 同Thread第二Turn、重启读取、并发配置变化、等待审批时关闭及取消窗口。

必须分别验证源码及隔离安装态。第278批没有产品RED或实现GREEN；上一批3287项通过
不覆盖本安装流程，也不作为此项一致性证据。

## 第279批：真实 Runtime 开放失败用例

`probes/test_skill_dependency_install_probe.py` 覆盖missing、configured、unmentioned、
skills_disabled四种情况。临时目录创建真实SKILL.md/openai.yaml，由Runtime完成选择、
正文注入和两个Turn。Fake stdio只返回lookup；模型只在实际请求已曝光定义时调用，
不预设未知工具调用掩盖发现缺失。配置过的对照组完成工具执行及Observation回灌。

源码addc46：1failed3passed1.51s；第277批不可变隔离安装包c67c39：
1failed3passed1.36s，347个已加载Corki模块来源受检。两者均在missing分支失败：
正文确实注入、两个Turn均正常完成，但client从未创建。不是缺API/配置字段异常。
用例还要求安装配置持久化（当前失败发生更早）；该断言尚未到达，不声称已验证。

这是拟实现的Corki宿主兼容安装路径验收，不是“原生第三方originator应安装”的断言。
采用现有execution_permissions=None表示不启用本地执行限制；没有伪造Codex身份，
也没有新增未消费的feature字段。随后实现必须明确宿主能力与feature开关，补充受限
权限、用户拒绝、取消、策略变化及OAuth测试，不能只让unrestricted stdio测试通过。

6552cf补追调用时序：native在first_step_context捕获后构建技能；Corki在graph
prepare中调用_refresh_input_tools，之后才捕获registry snapshot，并在接收realtime
输入后另有刷新入口。因此安装不能每Step无条件重读旧提及/重复发问，需按当前输入和
会话canonical状态控制，同时确保不改已绑定Step的工具所有权。探针仅要求后续Turn
可用，不擅自断言原生当前第一个请求必定可用。

本批只新增行为探针及审计，未改生产代码；f08065探针lint/format及git diff check
通过，teach散列未变、Codex clean。两项测试作业均已终态。安装仍未实现，整体目标
不满足完成条件。

## 第280批：基础安装已接 Runtime，完整生命周期仍未闭合

`skills/mcp_dependencies.py::SkillMCPDependencies` 由Runtime拥有，位于capture_tools后、
技能正文视图构建前。按当前Turn未消费的输入选择技能，复用owned skill IO，使用当前
connector快照；并非从任意模型输出或远端metadata接受安装指令。会话内按command/URL
canonical key去重，依赖feature关闭、skills关闭、orchestrator MCP关闭和basic Guardian
直接返回。Corki的宿主兼容路径默认打开 `features.skill_mcp_dependency_install`，不
伪造Codex originator。此配置实际被Runtime消费，不是接口预留。

`MCPManager.skill_dependency_catalog` 用真实controller与environment约束预演候选；
专门的 `skill_dependency_install` host elicitation与工具执行授权区分。审批后再准入，
写入后发布前再次复核；配置更改不修改已经绑定的工具调用。`publish_skill_dependencies`
以新增config声明扩展catalog，保留已有来源，并同步Runtime插件基础catalog，避免下一次
插件reload丢失安装条目。新声明由后续capture消费。

`config/toml_edits.py::add_missing_mcp_servers` 在现有进程内写锁下重读配置、验证、
保留同名条目和无关内容、一次原子替换。不是逐server覆盖全局文件。配置写入是拥有者
追踪的线程任务，重复取消仍join；取消优先于迟到的写入异常，不发布可执行声明。
全局文件与其他进程编辑器不构成跨进程事务，不声称这一点已解决。

行为进展：原279四组均绿；扩展至9组真实Runtime，增加feature关闭、批准、拒绝、
审批中策略收紧和写入失败。批准/拒绝/策略变化第二Turn再次提及同技能，验证不会重复
发问。6项原子配置测试覆盖同名、字面点号、损坏文件、replace失败与同进程并发。
6384aa聚焦15passed2.12s。新增取消probe（两次取消×正常/异常迟到写入）5066b6
2passed1.02s：不采样模型、一个TurnCancelled、无TurnCompleted、不发布或启动客户端。

明确剩余差异（不能用上述通过宣称整条安装链路一致）：

- 当前自动批准识别Never+无本地限制/Disabled/External，Managed full-disk-write的
  原生判定在第280批尚未接入；第281批的修复与验收见后文，不作为永久降级保留。
- HTTP配置及OAuth callback port保存在文件中，但没有安装后的OAuth discovery/login、
  scopes来源/特定错误无scope重试，也未将callback port接入认证配置。必须继续实现。
- 当前只发布本批实际新增的配置；原生安装成功后还合并全局已有条目，需要进一步对齐。
- 写入完成但取消/进程退出前未发布的恢复窗口，以及新Runtime从全局配置读取、同名/同
  endpoint更复杂冲突、受限delegate、受控环境的组合证据仍需扩展。
- 现有原子写只保证同进程互斥；审批后的外部配置变化与metadata来源污染需继续故障测试。

新取消probe为额外包外选集。源码扩大回归35329/b53cda终态3947passed1skipped
220.15s；安装态69384/cd04c3同选集3947passed1skipped223.27s，352个已加载Corki
模块来源全在隔离安装目录。额外取消probe源码5066b6两项通过，安装态803b7b两项通过
0.73s并核安装模块来源。f4dcee/19e859单独核跳过原因为文件系统拒绝non-UTF-8文件名
（test_project_trust_selection.py:164），不是跳过安装回归。
5473f8新wheel/sdist成功构建，546349离线安装至
`/tmp/corki-skill-dependency-install.llP0NL/installed`。cbb08f核369个Python源码/wheel/
安装文件逐一相同；wheel SHA256
`e54a05cd12e498a4c0be0f595a3d74439fc22c244a840def006873d72a8cdcbe`。
22f0f1全仓lint/1021文件format/compileall/72依赖/diff通过，teach散列保持不变。
24cfed/7e9207最终包外probe lint/format及diff check通过，Codex保持clean。
最终版基础4组在旧277隔离安装包78721/da925b仍1failed3passed1.34s，347模块来源
受检（5个新增控制不在这份旧包配对选集）。未执行真实模型、Codex Rust suite或完整
sdist/资源逐字核验。全部作业已终态，冻结解除；整体A–E仍未完成。

## 第281批：安装许可使用原生全盘写权限判定

22e445/669744追原生MCP自动批准条件与ManagedFileSystemPermissions转换。
这里的to_sandbox_policy返回FileSystemSandboxPolicy，不是legacy SandboxPolicy；
`permissions.rs::has_full_disk_write_access` 要求特殊Root写权限且没有收窄写入的条目。
8f0a2a确认字面路径`/`不等于这个特殊Root匹配条件。初版narrow控制使用字面`/`，
只能证明不会安装，不能证明narrowing分支；已经改成特殊Root+显式read条目，并增加
特殊Root单独允许安装的正向控制。最终旧280安装包c18e8d为2failed1passed1.41s，
348个安装模块来源受检；失败为Managed unrestricted与特殊Root，不是新API异常。

`native/sandbox/src/main.rs` 现返回原生profile.file_system_sandbox_policy()的
full_disk_write_access布尔值。`execution/backend.py::mcp_dependency_approval` 使用
现有owned编译请求重新解析effective权限与审批策略，决策同时依赖Never和原生写权限。
`skills/mcp_dependencies.py` 已移除Python按profile.type近似判定的分支，真实安装入口
消费此结果；SDK显式None仍是独立宿主路径。旧显式compiler缺失布尔字段时不安装，
不默认赋予写权限。分类输出不是持久化checkpoint字段，旧checkpoint无迁移要求。

733e17五项单测验证effective审批策略、原生写权限及缺字段fail-closed；daf2dc最终
12组Runtime安装/控制、2组取消、5项判定单测共19passed3.88s。新的Managed Root
正向与narrow反向走真实编译器，不是脚本伪造权限输出。

构建与资产：使用既有隔离Cargo/Rustup和1.95.0工具链，--offline/--locked，参考源码
导出到临时目录，Codex checkout未改。84661/c519bc首版构建成功1m25s；之后只修
rustfmt换行，58452/0dd7ab最终构建成功1m21s。不是因为等待超时重启作业。初次直接
rustfmt递归因reference只在导出树存在而失败，随后对当前main.rs使用skip_children
检查；d0543b最终检查通过，不声称检查了所有原生参考源码的格式。

9bbe73最终构建凭据核对源码摘要：
`2438561fd3afbd02041affe1b345d98288bb697633bfd11d6af1fab7aa049860`；
native binary SHA256：`966d3aaf44f77a78447d8c0758c0fe9594904733da52d8e49a0d2fcb89e4c3ba`。
75fd6a安装新原生资产前把旧工作区资产完整移至
`/tmp/corki-mcp-install-permissions.O0l8aH/previous-source-sandbox`，可恢复，未删除。
627fb7 wheel/sdist构建成功，234670离线安装至本批`installed`目录。
97fe75核369个Python文件与原生可执行文件/receipt在源码、wheel、安装态逐一相同；
wheel SHA256：`8beda24a8fc1a2cda14aa2529460009ab2503d42ebe5b44a7e0e302499a62445`。

扩大源码回归96825/ee3c99终态4067passed1skipped230.90s；安装态92302/05aaed
同选集4067passed1skipped233.85s，359个已加载Corki模块来源受检。选集包括前批选集、
权限判定、sandbox/执行契约与实际macOS managed/default/bundled权限集成测试。
额外包外`probes/test_native_mcp_consent_probe.py`直接检查native unrestricted、特殊
Root、Root+read narrowing、read-only、External各配Never/OnRequest的10种输出：
源码14b7dd/7f9db2 10passed0.40s；安装态79d646 10passed0.37s，Python backend与
native binary路径均在安装目录。异常在这些用例中直接失败，不能作为“拒绝安装”通过。
950403全仓lint/
1022文件format检查、包外probe lint通过；d0543b compileall/72依赖/diff通过。
3ad833最终包外probe检查、diff与teach散列通过。全部作业已终态，冻结解除。
本批关闭Managed full-write安装许可差异；没有执行Codex Rust suite或真实模型，也未
核验所有sdist资源逐字一致。OAuth安装生命周期以及其他A–E开放差异仍未完成。
