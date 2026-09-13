# 普通工具搜索、压缩与 provider 隔离刷新

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
