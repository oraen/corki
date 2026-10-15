# 普通工具搜索、压缩与 provider 隔离刷新

## 2026-10-12：agent-reach 可选依赖误调用复核

对照本地 Codex `core/src/session/turn.rs:868` 与
`core/src/mcp_skill_dependencies.rs:475`：显式选择技能时处理声明的
`dependencies.tools`，仅安装 `type=mcp` 且带连接信息的依赖；不从 Markdown
示例推断 CLI 安装命令或 mcporter 服务器配置。Corki 的
`skills/mcp_dependencies.py`、`skills/policy.py` 在本问题涉及的边界上已对齐。

本机 agent-reach 只有技能正文与 references，没有 `agents/openai.yaml` 依赖声明。
失败来自通过 exec_command 运行的 mcporter/CLI，并非原生 MCP 工具目录误暴露。
日志中模型尚未等待 mcporter list 完成就调用未配置的 web-reader；
check-update 则因 agent-reach 可执行文件不可用而失败，不能据此判断该版本
是否支持此子命令。同一环境与技能下，Codex 的上述安装机制也不能自动补齐。

此次只修正共享用户技能 `~/.codex/skills/agent-reach/SKILL.md`、
`SKILL_en.md` 与 `references/web.md`：按需检查 CLI/MCP、等待检查完成，
明确 web-reader 为可选后端，检查更新仅在 CLI 和子命令存在时独立执行。
没有修改 Corki 运行时代码、provider 配置或安装依赖。更改前副本位于
`/tmp/corki-agent-reach-before/`。

验证：Corki 实际解析器可加载中英文技能，原 frontmatter 保持不变，Markdown
代码围栏配对。skill-creator 的通用校验器在修改前后均拒绝已有的 `triggers`
扩展字段；为保留第三方元数据没有删除该字段。本次未重新执行线上调研任务，
规则修改能否减少模型误调用仍需下一次运行观察。

## 2026-10-12：跨 provider 切换后的历史模型元数据回退修复

切换到 DeepSeek 后，pre-turn 压缩检查仍用当前 provider 的 catalog 查找上一轮
`gpt-6-astra`，产生 `Unknown model ... fallback window metadata (272000 tokens)`。
这里的 272000 属于历史模型查询回退，并没有把当前模型窗口修改成 272K。长历史
还可能沿此分支用新 provider 的客户端提交旧 provider 的模型名。

参考本地 Codex `codex-rs/core/src/session/turn.rs` 的
`maybe_run_previous_model_inline_compact`（约 1155–1240 行）：同一 provider 内仍保留
compatibility hash 与窗口缩小触发的 previous-model compaction。Corki 的额外
跨 provider 切换需保留 provider 身份，不能仅凭模型名沿用当前客户端。

- 历史模型标记增加不含凭据的 canonical provider 身份；跨 provider 时跳过
  previous-model compaction，继续当前 provider 的普通上下文预算与压缩流程。
- 针对旧标记没有 provider 的情况，嵌套 provider catalog 只解析其可用历史模型；
  不可用时返回无目标，避免将其他 provider 的模型误判为 272K 的本地模型。
- 当前模型的元数据解析与窗口设置不变，用户 settings.json 未修改。
- 回归：204 passed（跨 provider 的不同/同名模型、强制压缩、旧标记、同 provider
  downshift、reasoning effort、metadata ownership、checkpoint）及 527 passed
  （context/config、指令生命周期、personality、线程设置和恢复）。Ruff 和
  git diff --check 通过。实际 HTTP mock 验证切换后的普通请求及压缩请求均使用
  新 provider 的模型名、URL 和凭据，且不再产生旧模型 fallback 警告。

## 2026-10-12：provider / model 嵌套配置与成对切换

当前用户配置已迁移为 `default: {provider, model}` 与
`providers.<provider>.models.<model>`。连接和凭据保留在 provider，推理等级、
上下文与截断元数据归属各自模型。同名模型在不同 provider 下独立解析。
旧 inline provider、agent.model 与全局 catalog 保持读取兼容；本机 codex-local
保留 default 别名用于旧会话恢复。原 DeepSeek 密钥及注释保留，当前默认仍为
`gpt-6-astra(codex-local)` / medium / 524288 tokens。迁移前 settings.json 已作
0600 私有备份；迁移后逐项比对凭据、请求参数、能力、上下文、工具、权限与记忆配置。

- `/model` 显示 `模型名(provider名)`，按模型、provider 排序；确认后同时更新会话
  provider、模型和推理选择，原子保存用户默认组合与模型推理偏好。取消不发布，
  全局写入失败保留已提交的会话选择并明确提示；文件中的 provider 被移除时不重建
  一个缺少凭据的配置。注释与符号链接目标继续由既有配置编辑器保护。
- 新 Turn 绑定自己的客户端及压缩客户端；旧 Turn 不随未来默认变动，断点恢复
  重新解析保存的 provider 身份。缓存的连接统一关闭，环境连接覆盖只跟随最初
  provider，切到其他 provider 不复用旧密钥。SDK 注入的 transport 保持主机控制。
- 图片/音频处理和 view_image 能力随 provider 更新，旧图与工具视图不被改写；
  从 Plan 返回默认模式时恢复完整 provider/model 组合。可信项目原有 agent.model
  覆盖与显式 CLI 优先级保留，项目不能重定向 provider/default。
- 新配置模板及 README 已同步。最终相关回归：2036 passed / 15 skipped，涵盖配置、
  CLI、菜单排序、同名模型隔离、实际模拟 HTTP 协议/鉴权切换、并发旧任务、压缩、
  断点恢复、失败回滚、Plan、媒体及项目权限。较大范围回归（unit/config、cli、context、
  models、core 及全部 integration）12109 passed / 690 skipped，耗时 710.23 秒；
  测试运行期间完成的媒体与配置边界修改由上述最终相关回归再次覆盖。Ruff、
  git diff --check、12 个 README JSONC 示例及 /tmp 下全局 corki --help 均通过。

## 2026-10-12：配置入口迁移为 settings.json

用户要求把当前配置及代码切换为 JSON，简化配置并保持其他功能。默认用户入口
现在为 `~/.corki/settings.json`（支持 CORKI_HOME），项目入口为 `.corki/settings.json`。
支持 JSONC 行/块注释和尾逗号；重复键、非有限数字、非对象根和非法语法会报错，
解析错误不回显可能包含凭据的源行。同目录 JSON 优先且不会与 TOML 混合，JSON
无效时也不会静默回退。旧 TOML 仅保留迁移兼容；插件清单与托管 requirements.toml
不属于此次用户配置格式替换。

- `config/document.py` 集中解析、文件选择和保留注释的局部 JSON 编辑；
  `config/edits.py` 接入既有锁、原子写入、0600 权限和符号链接目标规则。
- `config/paths.py` 生成精简模板；`config/migration.py` 在首次 CLI 启动时迁移已有
  用户 TOML，保留原文件及注释。路径相关的 Hook 信任记录增补新文件路径身份，
  不覆盖显式的新路径撤销/禁用状态；原信任哈希不变。
- 同步用户/项目分层、MCP 授权及技能依赖安装、插件选择/已安装插件加载、技能规则、
  Hook 源与恢复目录偏好读写。项目信任门控、provider 隔离、相对路径和环境覆盖不变。
- README 的 13 个 TOML 配置示例均转换为可解析 JSONC，更新默认路径和迁移说明。

本机配置已迁移，文件权限0600。完整迁移后逐字段比较所有有效 CorkiSettings
（仅排除描述源文件的 configuration），结果一致；随后删去本机两个未被代码读取的
旧键 `agent.context_char_budget`、`memory.recall_messages`，以及六个重复默认值，
再次比较有效设置仍一致。模型/medium、网关地址与 key、512K 上下文、tokens/10000
工具截断策略均保持；旧 DeepSeek 凭据仍在注释中。原 config.toml 和带时间戳的
`config.toml.bak-json-migration-*` 私有备份保留，以后编辑 settings.json 即可。

验证覆盖 JSONC 字符串/注释边界、拒绝错误配置、符号链接、写入失败原件不变、并发
MCP 安装、迁移前后设置一致、Hook 信任与撤销、JSON 项目分层/隔离、恢复目录、
真实 Runtime 的 Hook/MCP 授权，以及本机 HTTP 网关的模型/effort 转发。
最终专项回归 **933 passed、1 skipped / 57.76s**，日志
`/tmp/corki-json-final-regression.log`；ruff、git diff --check、13 个 README JSONC
示例解析及从 /tmp 调用全局 corki --help 均通过。

全量首轮结果为 **16886 passed、718 skipped、32 failed / 936.48s**，日志
`/tmp/corki-json-full-tests.log`。其中18项为旧测试仍写/读旧默认文件或向新文件写TOML，
已改为JSON并保留实际冷恢复、凭据轮换、覆盖及MCP业务断言；另外14项历史保留测试
用普通单词 current 搜索整个请求，误命中此前已有压缩提示词。改用同为7字节的独有
输入标记，保持边界预算不变，未改压缩实现。这32项及所在测试文件均包含在最终
933项专项回归中，全部通过。未声称全量首轮零失败，也未把本地验证当作真实上游
任务重跑；718项平台/条件跳过仍不是通过证明。

## 2026-10-12：系统信任库与缺失 SSE 响应头修复，真实调用接通

连续502重试源于两个网关问题。首先，HTTPX默认CA库不包含电脑系统钥匙串中
已信任的企业网络根证书；系统curl可验证TLS，而默认Python客户端失败。
本地Codex `http-client/src/tls_backend_fallback.rs` 明确默认使用native TLS，
且证书错误不触发后端回退。网关改用 truststore.SSLContext 系统信任库，仍要求
证书及主机名校验。依赖显式加入pyproject/uv.lock，没有安装或信任新的根证书。
证书校验异常沿因果链识别后返回424，Corki现有规则不重试；暂时网络错误仍为502。

其次，真实上游200响应正文为SSE（首事件response.created），但当前网络路径
缺少Content-Type，网关原先无条件拒绝。参考Codex `codex-api/src/sse/responses.rs`
直接按事件解析正文，允许缺失响应头；仍拒绝明确的非SSE类型，并由下游解析器
要求response.completed，不将断流/HTML当作完成。

联合57 passed / 12.74s（`/tmp/corki-gateway-system-tls.log`），新增系统TLS验证
开关、证书错误不重试及缺失响应头的真实适配器回归；ruff/diff/离线锁检查通过。
部署重启后，通过实际config加载的Corki Responses adapter → 固定17001网关 →
真实Codex服务，gpt-6-astra/medium请求收到ModelCompleted，虚构问候回复OK。
重启紧接探测曾遇到本地端口尚未就绪，服务启动后再次探测成功；不属于上游TLS失败。
此次没有改模型、key、地址或上下文配置，也没有执行用户项目任务或工具。
本节覆盖此前“真实模型尚未接通”的状态；尚不代表完整复杂任务已验证。

## 2026-10-12：模型与推理等级由配置传递

用户要求可在 Corki 配置选择 Astra medium 或其他模型。网关移除 Astra 单模型
限制，验证 model 是非空字符串后原样转发；Chat 转换路径使用请求 model 和
reasoning_effort，无 effort 时不注入默认值；响应模型名优先使用上游返回值。
临时启动器也不再强制覆盖 model/effort。

源码核验：普通 Chat profile 的 supports_reasoning_effort=false，因此仅写入
reasoning_effort 配置并不能让 Corki Chat adapter 发出此字段；现有 Responses
profile 和 request_settings 已支持传递。采用已有能力，不更改通用 Chat profile。
本机配置改为 agent.model=gpt-6-astra、provider.api_mode=responses、
provider.reasoning_effort=medium；网关地址/key/512K容量保持原值。
备份 `~/.corki/config.toml.bak-model-forward-20261012-160456`。

新增配置加载 → Responses adapter → 真实本机HTTP → mock upstream 的双模型/
双等级验证，以及 Chat 模型/effort转发、未指定不注入、响应真实模型名验证。
联合54 passed / 11.49s（`/tmp/corki-gateway-model-routing.log`），ruff/diff通过，
固定端口服务已重启。并未据此宣称账号拥有任意模型权限；既有TLS问题仍独立存在。
旧会话的模型选择按 Corki 既有恢复语义保留，可由显式CLI参数覆盖。

## 2026-10-12：修复真实首轮 system 文本数组被误拒绝

用户发送“在吗”后收到400。只读失败会话确认 turn
`a589973a-2e3b-413d-a812-5da9ec6f5e18` 命中网关转换错误；其前置开发者上下文
包含同组5段 ContextItem。Corki `_convert_items` 合成 system.content 文本数组，
网关此前却要求前置 system.content 必须是字符串，因此在访问上游前拒绝。
此前测试只有字符串指令，没有覆盖真实 Runtime 的指令分组，是网关与测试遗漏。

新增真实 Runtime → Chat adapter → 本机网关 → mock upstream 用例，修复前
精确复现相同400（`/tmp/corki-gateway-400-repro.log`）。修复后按原顺序合并
前置 system 文本片段；后置 system 仍保持原消息位置并映射 developer。
继续拒绝非文本系统片段，不删除或降级指令。补充顺序/边界及非法片段回归。

联合验证51 passed / 10.29s（`/tmp/corki-gateway-400-fixed.log`）；ruff、diff检查
通过。已重启固定端口 LaunchAgent，以虚构文本数组请求验证旧400消失。
真实上游仍因既有连接/TLS问题返回502，未宣称模型已接通。没有改动用户配置。

## 2026-10-12：按用户要求改为固定端口与 Chat Completions 中转

用户进一步明确：仅注释原 provider.base_url/api_key 并替换成本机17001端口
及指定本地 key；不改变 Corki 的模型名、接口模式或其他设置。
已完成配置修改，并通过解析前后对比断言只有这两个值变化；原值作为注释保留，
完整私有备份为 `~/.corki/config.toml.bak-fixed-gateway-20261012-155732`。
DeepSeek 模型名现在只是下游兼容别名，实际上游固定为 gpt-6-astra/medium。

新增 gateway_chat.py 转换普通 Chat Completions 消息、图片、函数定义、调用
及结果、结构化输出设置，向 Codex Responses 提交。流式文本实时转换；函数调用
在上游 response.completed 后发出，映射 token usage 与 finish_reason。
失败/不完整/提前断流不会生成成功标记。Chat 路径不回传加密 reasoning，
不宣称完整支持 Responses 的全部扩展，也未修改 Corki 两种内置模型适配器。

固定服务仅监听127.0.0.1:17001，支持有/无v1前缀的 Chat Completions 端点。
LaunchAgent 为 `~/Library/LaunchAgents/local.corki.codex-gateway.plist`，已 bootstrap；
key 文件为 `~/.corki/gateway/local.key`（0600），普通 corki 无需额外启动器。
未认证请求返回401、合法认证的无效请求返回400，已实测固定端口。

验证共45 passed / 9.52s（`/tmp/corki-chat-gateway-tests.log`），包括真实本机HTTP
及 Corki Chat adapter 的两请求工具往返、用量投影、图片/结构化输出与失败流。
上游仍使用真实Codex固定端点及TLS校验；前节真实连接故障未被协议适配修复。

## 2026-10-12：显式选择的 Codex 登录网关试用

用户本轮明确要求复用当前 Codex 登录 token，通过本地网关呈现 API Key 协议。
新增独立模块 `src/corki/codex_gateway.py`，不改变两种普通模型适配器的能力解析，
也不凭 provider 名称或域名自动启用官方协议。只有 `corki-astra` 启动入口会启动
本机随机端口网关，并将一次性的本地 key/地址传给 Corki 子进程。

参考本地 Codex 固定提交的 `model-provider-info/src/lib.rs`：ChatGPT 登录使用
`https://chatgpt.com/backend-api/codex`，API Key 登录使用公共 API；
`model-provider/src/bearer_auth_provider.rs` 注入 Bearer 与 ChatGPT-Account-ID；
`core/src/client.rs` 设置 store=false/stream=true。
网关只开放带本地认证的 POST /v1/responses，限定本次试用模型 gpt-6-astra，
保留 medium 推理设置与普通函数工具/结果/加密推理项，透明传递 SSE，不伪造完成。

不修改 Codex auth.json，不复制 OAuth token 到 Corki 配置或本地日志；每次请求
重新读取当前文件，同账号 token 更新后可继续调用。401 仅在文件 token 已变化时
重试一次，不自行竞争刷新 token。账号变化时要求重启入口；仅支持文件凭据，
不宣称已实现 keychain、自动刷新、Chat Completions 转换或原生 Lark 工具。

本机新增 codex-local provider 和 Astra 模型元数据；默认 DeepSeek 及其 512K
配置不变。Astra 试用窗口同样为 524288，压缩阈值 471859，默认 medium。
最大容量 872000 来自固定 Codex 目录，不是账号实测。配置备份：
`~/.corki/config.toml.bak-codex-gateway-20261012-154705`；本机入口
`~/.local/bin/corki-astra` 保持启动目录，退出后关闭网关。

验证：本机真实 HTTP + MockTransport 上游、认证/账号隔离、文件更新、错误清洗、
流中断不能变完成、真实 Responses 适配器消费，以及现有推理/响应条目测试，
共 **40 passed / 8.89s**，日志 `/tmp/corki-codex-gateway-tests.log`。
普通 --help 启动验证成功；没有把离线测试当作真实模型访问验证。

真实最小探测只发送虚构问候：现有 Codex token 调用公共 /v1/responses 返回401；
ChatGPT Codex 端点连接报 CERTIFICATE_VERIFY_FAILED（self-signed certificate
in certificate chain）。完成网关后的同类请求返回502，未收到 response.completed。
因此实现和本机入口已就绪，但当前机器网络/TLS条件下尚未接通真实推理；没有关闭
TLS 校验，也没有修改系统 DNS、代理或证书设置。

官方资料：[Astra 工具调用须使用 Responses](https://developers.openai.com/api/docs/guides/latest-model)、
[Codex 认证方式](https://learn.chatgpt.com/docs/auth)、
[独立 OAuth 应用的公共 Responses 流程](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)。
最后一种流程要求其专用授权 token，不能假定当前 Codex token 可直接替代。

## C9/C10 验收收敛：补齐自动入口的交叉证据

当前重新追踪 window.prepare/compact → _summarize → _summary_attempt → ModelPort.stream：
三种压缩时机都由 Harness 构造无工具的普通 ModelRequest，再本地安装历史替换。
resolve_capabilities 只选择 api_mode；两适配器仅向配置 base 下的普通端点发送请求。
旧设置 native/Lite/remote 值被归一化或拒绝，旧 opaque 历史不能借普通回放启用专属请求。
Codex 参考仍为 ddf04ad26789d040f9ef6a96736f76602e35a6cc，工作树干净；
参考的是 compact.rs 普通摘要及本地替换链，不把其远程分支作为待实现差异。

现有供应商/域名/模型成功矩阵仅覆盖手动压缩，自动轮前/工具后已有另一组测试，
但两组不能冒充同一个交叉矩阵。本批扩展现有真实 Runtime/HTTP 测试至三种入口，
保留冷重开、精确 URL/Bearer/模型、无专属头/字段及原始归档前缀断言；
轮中入口还应确认工具只执行一次。此处是待补验项，不是已证明的生产故障。

补验已完成：成功矩阵现在为三入口 × 两普通接口 × 四标签 × 三地址 × 两模型，
144例；另外8例缺地址/密钥的冷热启动仍保证零请求。自动入口使用响应usage真实
触发阈值，不替换window.prepare；轮中由真实普通function call及Observation进入
摘要，关闭重建后无第二次摘要/副作用。手动和轮前另核对安装前后归档前缀。
新增夹具第一轮遗漏Responses usage.total_tokens，72失败/80通过
（009eb4，24.11秒）；补齐合法usage，不改变生产校验。这不是生产缺陷的红绿证据。

当前八文件联合429通过（c5138b，75.89秒，49802退出0）：uniform能力、压缩wire、
旧压缩请求/历史、旧search/Lite、namespace接收、hosted归档普通回放。
ruff（4aa221）、1184文件格式（3d4400）、compileall（761da5）、77包依赖
（7eca2d）通过。没有修改生产传输，也没有访问真实供应商。
另独立配置单位61通过（031826，0.50秒），包括旧remote_compaction_v2配置无效化
及native search归一化；不与429合并称作单次运行。

据此C9/C10按用户限定的内置普通适配器路径判定已核验：三入口共用Harness
摘要/替换所有权，provider标签、域名和模型名不打开专属压缩或其他传输。
这不自动关闭C3预算精度、C4/C8全部故障状态、B/E其他生命周期、F视觉差异，
不保证宿主任意自定义ModelPort内部行为，也不证明实际模型摘要质量。
兼容墓碑字段/只读旧归档解码不是可启用的远程实现；全项目排除项清理仍应
按各功能入口审计，不能从压缩矩阵推断所有代码均无官方产品遗留。

## 上述死实现已清理

删除已确认无生产引用的 context/remote_compaction.py 与 context/legacy_compaction.py，
不再保留远程用户裁剪、尾部结果裁剪、远程上下文过滤/插入算法。同步删除仅依赖
这些旧算法的测试；保留同文件中旧 opaque/normalized 归档解码、非法数据拒绝、
token 成本、普通媒体投影及专属请求拒绝测试。旧 wheel 的 checkpoint→用户保留
→SQLite 回放测试改走现用 window._retained_user_messages，保留其兼容断言。

源码与测试已无上述模块/函数引用（5596f2），相关三个单位文件加成功压缩/冷恢复
矩阵86 passed（9385fa，10.85秒）；静态5596f2通过（1181文件、77包）。
删除的是已纳入 Git 索引的源码与可从差异恢复的旧测试代码，没有删除用户会话或数据。
清理后完整 context 单位集合353 passed（c3ee1b，1.65秒），无跳过，diff检查通过。
本节覆盖下节刚发现时的待清理状态，不能把下节当作当前仍未实施。

## 成功摘要矩阵补验及遗留代码清理项

当前 test_uniform_model_capabilities 的成功链扩大为两普通接口 × 四个 provider
标签 × 三个地址（含 api.openai.com）× fixture/gpt-5 两个模型名，共48例。
每例普通 Turn → Runtime.compact 成功 → 关闭重建 → 后续 Turn 引用 SUMMARY，
原始历史前缀不变。精确断言配置端点、模型、Bearer，禁止官方内部请求头与
compaction_trigger/context_management/previous_response_id，摘要请求不带工具。
所有域名均由 MockTransport 接管，未访问真实官方服务。
加配置缺失、旧请求/历史、旧 search/Lite、namespace 接收及平铺请求专项，
八文件207 passed（94c99a，47.18秒），静态510881通过（1183文件、77包）。

同时确认新的清理遗留，不能据当前网络测试通过宣称排除项清理全部完成：
`context/remote_compaction.py` 的 retain_remote_users/trim_trailing_outputs，
以及 `context/legacy_compaction.py` 的 filter_remote_history/insert_remote_context
当前只被旧单位测试调用，无生产引用（510881）。它们不访问网络，但属于已排除
远程压缩路径的遗留算法，需删除死实现与仅验证该算法的测试。应保留旧 CompactionItem/
RemoteHistoryItem 解码、拒绝专属历史回放、媒体投影与本地保留用户输入的有效测试；
不能直接删除两个旧测试文件而连带丢失这些仍有用的兼容验证。此项为下一清理动作。

## 当前调度改动后的再验证

2026-09-12重读models/capabilities.py：resolve_capabilities只依api_mode分支，
不读取provider_name/base_url来开启扩展。两adapter在HTTP提交前分别拒绝空base
与空key；settings的api_base默认空，不静默选择官方地址。OPENAI_API_KEY是可选
环境变量兼容名称，不授予官方登录、配额或专属协议能力。

普通deferred wire、压缩wire、MCP OAuth refresh、旧compact请求/历史、旧search/
Lite响应、namespace接收、hosted旧归档普通回放及uniform capabilities十文件
**405 passed，72.81秒**（38bc52，16129退出0）。该运行收集发生在下述8例新增前，
不把新增测试算入405，也不将独立集合累加成全量。生产代码未修改。

新增真实Runtime配置缺失矩阵：两普通adapter × openai/independent标签 × 缺base/
缺key，共8例；每例关闭旧Runtime再以同thread创建新Runtime发起下一Turn。
断言每Turn唯一TurnFailed、没有TurnCompleted，错误说明准确，MockTransport收到
的请求集合始终为空。固定官方风格模型名称也不能触发地址/密钥回退。这是新增
行为证据，不是修复前失败的生产缺陷。该文件**24 passed，4.63秒**（900490）。
全部HTTP由离线MockTransport接管，没有使用真实密钥或访问任何真实官方服务。
静态abf546通过：ruff、1181文件格式、compileall、77依赖与diff。无活动测试进程。
同步create在运行中loop的公开兼容决策仍待用户确认，未擅自改动；A–F其他项独立。

## 最新：OAuth 与 deferred 搜索已在同一链路组合

test_mcp_oauth_refresh.py 新增 deferred 参数，保留原 direct 对照。矩阵为
direct/deferred × scripted/真实 Responses 适配器 × docs/openai 名称 ×
startup/between_calls 过期 × refresh 成功/拒绝 × 正常/慢 refresh，共 64 例。
第一轮 64 passed（788238，17.12 秒）；下方旧“分别验证，未组合”描述是该
修改之前的状态，不再代表当前测试。

真实 HTTP 模型 deferred 分支先返回普通 tool_search function_call，首请求
没有 MCP read 定义；收到 search-once Observation 后下一请求加载平铺 read，
执行 read-once 后 READ_PROOF 或错误 Observation 回灌。显式按 call_id 检查
read 结果，避免把搜索结果误判为实际调用成功。成功冷恢复还检查 read 定义
仍然存在，refresh 和 MCP tools/call 都不重复。刷新拒绝时不改原凭据，也不
产生成功外部调用；不要求已失败启动服务器仍能提供可搜索工具。

沿用严格 HTTP host/endpoint、凭据、文件发布先于 RPC 和客户端关闭断言，
并新增官方内部请求头禁用检查。所有请求由离线 MockTransport 接管，不使用
真实 API key。预设搜索/调用仍只证明 Harness，不证明真实模型选择质量。

最后增加冷恢复定义与官方头断言后，OAuth/搜索 wire/压缩 wire/搜索错误/
恢复并发联合 287 passed、0 skipped，41.85 秒（883eed）。静态全部通过
（501f44：1133 文件、77 包），所有进程退出 0，无活动测试。生产代码未修改，
没有以本批局部链路关闭其他 A–F 差异。

2026-09-11 当前工作树验证；不将前面的构造/CLI 局部结果替代 B/C/E 与排除项证据。

## 范围与源码边界

用户明确要求即使配置官方地址也使用普通协议。原生专有 tool search、namespace、
远程 compact 不是待补能力。Corki models/capabilities.py::resolve_capabilities
只根据 api_mode 返回普通传输 profile，provider 标签和 base_url 不授予专有
能力。测试实际经过 Runtime、搜索工具、定义加载及 Responses/Chat 适配器，
不是仅比较 capability 常量。旧 native 构造值只作为迁移输入，不能恢复协议。

## 新增交叉矩阵

test_deferred_namespace_wire.py 原 12 例扩到 48 例：四组 provider/base/model
交叉包括 openai 和 fixture 标签、api.openai.com 与 fixture.invalid 地址、
gpt-5 与 fixture-model。每组保留 Responses 旧能力开关三个组合、普通 Chat
路径，以及普通工具/MCP 有描述/MCP 无描述三个来源。

每条链三次请求验证：首请求仅搜索可用，第二次选中普通函数定义出现，第三次
Observation 包含工具结果。所有 HTTP 都由 MockTransport 返回，不向真实
官方域名发送请求。断言精确配置端点、模型、Bearer，禁止 x-codex-/x-openai-
以及 chatgpt-account-id/openai-beta；工具均平铺 function，消息中无原生
tool_search_output/namespace 字段。48 passed（5c5f3d，7.89 秒）。

test_compaction_wire_contract.py 的 36 例扩到 144：provider openai/independent
× 官方域名/fixture 地址 × 旧 v2 值 × token budget × 九种非摘要响应。
每例只调用配置的普通 /responses，一次失败后原始历史完全保留；请求不带工具、
compaction_trigger 或所列官方头。没有访问 /responses/compact。144 passed
（864bd9，8.34 秒）。这是失败/拒绝边界，不据此声称所有自动压缩成功路径均完成。

## 其他当前联合证据

新搜索矩阵加 MCP OAuth refresh、压缩 wire、旧 compaction request/history、
旧 search/lite response、namespace 接收边界、hosted archive 普通回放联合
249 passed，0 skipped，53.35 秒（ce7318，早于压缩矩阵扩大，包含旧 36 例）。
不能把这次联合与随后 144 例相加称作一次全仓结果。

其中 test_mcp_oauth_refresh.py 的真实 HTTP 模型分支验证模型密钥和 MCP access/
refresh/header 凭据互不串用，MCP 只使用选中服务器/issuer，刷新持久化先于
RPC，失败不修改凭据，冷重启不重复已完成调用。其 tool_search_mode=disabled，
所以这里是分别验证 OAuth 与 deferred 搜索，不冒充同一次带 OAuth 的搜索链。
ScriptedModel 分支不算真实模型适配器或模型工具选择质量证据。

静态全部通过（282d22：1133 文件、77 包），所有测试进程退出 0。本轮只增强
验证，未修改生产传输或 OAuth。仍需按全 A–F 各条收敛；同步嵌入构造限制、
context 顺序及生命周期、完整交互矩阵等不由此次隔离通过自动关闭。
