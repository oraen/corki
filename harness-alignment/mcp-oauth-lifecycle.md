# 普通 MCP OAuth：显式 File 首次授权已接入，完整生命周期仍待完成

最新双服务隔离实证：test_mcp_oauth_refresh 原仅注入脚本模型，不能直接证明模型HTTP
请求未携带MCP凭据。本轮增加实际Runtime创建Responses适配器，同一MockTransport
白名单分别校验model.fixture.invalid/v1/responses与mcp.fixture.invalid的RPC/发现/token。
模型只带model-secret，禁止MCP access/refresh token、无关服务凭据及私有头；MCP侧
禁止模型key；token请求禁止Bearer与MCP私有头。provider/server名docs/openai与
fixture-model/gpt-5均覆盖，启动过期/调用前过期、刷新拒绝/成功、慢刷新、冷恢复验证。
全部工具定义为平铺function，响应回灌为普通function_call_output，未使用官方账户。

测试初稿按“read”字面选工具，误选read_mcp_resource导致server参数错误，不能算生产
缺陷；改为匹配兼容命名映射且断言真实请求中存在对应schema。双服务与HTTP重试层、
连接恢复、流可靠性、OAuth锁/store组合151通过（93d188，23.49秒）；随后将access
token替换为独有哨兵字符串，补模型payload不泄漏断言，最终结果见current-scope。
本轮没有修改生产OAuth或模型协议。仅Responses与File双服务新增证据，不冒充Chat
与全部凭据后端、全部首次授权方式都已联合验证。

源码归属核对：Runtime._create_model只将settings.api_key/api_base传给选定普通适配器；
mcp/oauth_refresh._transaction独立客户端、锁内重读固定服务凭据、固定issuer并禁跳转。
原生rmcp-client/oauth/refresh_transaction.rs也是托管读/刷新/保存事务，模型请求不是
其凭据来源；oauth_refresh_mode有Legacy/Coordinated选择，不能把这些通用MCP能力
与CodexAuth官方账户混为一谈。原生超时后旋转结果未知仍可能需要重新授权，Corki也
不承诺对供应商已消费但响应丢失的refresh token进行无风险恢复。

基准：Codex `ddf04ad26789d040f9ef6a96736f76602e35a6cc`。以下最新状态优先于历史批次。
File/Auto/Keyring公共客户端首次授权、保存、读取刷新已接入安装/独立CLI/Runtime；
完整CLI与授权变体及更全面故障验收仍未完成。

最新刷新取消证据：rmcp-client/oauth/refresh_transaction.rs在等待锁前托管完整
事务，非登录准入语义。Corki同样托管，并在传播调用者取消前join清理。真实OS锁
争用后重复取消，持锁更新为可用/过期新凭据或删除，三变体验证锁内重读、0/1 POST、
不重放旧token、保存完成和锁释放；相关40通过6.41秒。此项无需生产修复，不应再
把“刷新等待锁期间仍继续事务”单独作为缺陷。其他授权变体仍待审计；旧全项目
80481、74071均已终态，以下历史运行状态不应触发重新poll或重启。
Apps重新连接提示、显式Bearer/Header、HTTP header helper刷新
都不是本项的登录/凭据生命周期。以下包含历史审计，最新验证不代表完整OAuth完成。

## 最新实现与验证：File 安装授权纵向链路

最新首次保存：save_login_credentials按配置选择File/Auto/Keyring，写入成功后清理
匹配的旧File凭据；清理失败按Codex best-effort告警，不改写新Keyring、不重新兑换。
Auto写入后端失败回退File，显式Keyring不回退。CLI和安装已传store_mode接通真实写入，
不再停留读取/刷新。三种store均实际首次授权、工具调用和冷恢复，2263通过20.98秒。
全项目80481正在跑；旧记录中的“非File拒绝/首次写入缺失”是历史状态，不代表现状。
仍待存储变体完整取消、插件CLI、header-helper及其他回调/client/scope矩阵。

最新Auto/Keyring增量：oauth_store实现启动选源与source固定，直接系统keyring使用
Corki服务标识；记录类型/身份校验、刷新CAS与未知字段保留。manager/runtime接入读取
和原有刷新事务，显式File保持原路径。内存keyring和null隔离测试，绝不读取真实钥匙串。
冷Runtime三store工具调用+单位2254通过30.91秒；两个keyring客户端并发刷新仅一个POST，
另一方采用新token，File副本不动；store/refresh联合19通过0.65秒。新增keyring依赖及
uv.lock，静态1050文件/77包通过。首次登录keyring写入和旧文件清理尚未接入，CLI的
非File拒绝继续保留，不能把读取刷新称完整Auto/Keyring支持。

真实PTY验证新增：40/100列×Ctrl+C/拒绝/接受，六组合6通过6.69秒，真实main/回调/
token保存链路，供应商网络与浏览器模拟。退出130/1/0；取消/拒绝不兑换token不保存，
成功才报成功；无秘密输出、无history/session，退出前HTTP关闭且callback端口关闭。
原生resolved_store全读：Auto只在生命周期开始解析source，已选中后故障不能热切File；
登录写入的backend失败与aggregate lock失败必须区分。Auto/Keyring仍未实现。

CLI增量：main新增mcp login NAME --scopes，cli/mcp_login.py加载本地配置并应用
managed MCP约束，显式File本地HTTP复用真实授权。浏览器失败打印手动URL，保存成功
才报成功；没有模型key或会话DB。真实main+MockTransport+loopback覆盖成功/失败及
禁用/管理策略/未知/stdio/Auto联网前拒绝。与现有CLI交互/OAuth联合134通过6.08秒。
CLI目前不合成插件目录、不支持header helper/远端身份/非File，真实PTY登录取消仍待验收。

CLI下一实现批次（开始前登记）：Codex mcp_cmd::run_login独立加载配置/选定server，
scopes显式→配置→发现，浏览器失败提供URL，保存完成后才输出成功。Corki main目前
仅resume入口。本批接入mcp login NAME --scopes，复用已实现登录模块，不构造模型会话。
先支持显式File和本地配置HTTP；Auto/Keyring、插件目录合成、header helper、全局回调
配置及scope降级仍为待补齐，不可当作最终CLI一致。未知/禁用/被管理策略禁止的服务器
必须在HTTP和浏览器之前拒绝，凭据及模型key不得打印。

最新增量：Runtime撤销服务后接受旧OAuth提示，修复前仍发token，现以固定
context/home/settings和动态准入检查阻止；覆盖发现/DCR/callback/凭据锁等待之后。
等待锁尚未提交时重复cancel，修复前两issuer模式都继续token请求，现检查owner新取消
阻止提交。token已提交或save已开始时则join完成保存后抛取消，凭据不改投新身份。
token/save/lock取消矩阵、实际撤销probe与完整MCP/skills单位共2153通过16.47秒。
原全项目终态11867通过640跳过，但采集早于本批修改；不作为本批全项目证明。

增量验证：41通过2.35秒（83c290），静态1045文件/72包通过。新增连续cancel在
listener wait_closed期间的确定性故障注入，修复前1失败6通过，修复后owner必须等
完整关闭才抛CancelledError。oauth_owned共享join，accept同步登记writer/tasks，关闭
先中止连接再等server。安装probe新增冷Runtime/独立cache，用相同凭据实际调用，
register/token均仍仅一次，tools/call共两次，文件bytes不变。预注册client_id分别
验证issuer-bound与callback-specific，均无DCR；直接真实login函数测试，非CLI验收。

生产入口 skills/mcp_dependencies.py 调用 oauth_login.login_oauth；独立HTTP客户端完成
预注册client_id或DCR、PKCE、绑定loopback回调、token交换及与刷新共用锁的首次原子保存。
oauth_callback 的 state 比较改为 bytes，避免非ASCII参数触发TypeError；错误state、
issuer、缺失issuer、重复state及路径均不得消耗回调结果。拒绝不得兑换token。
不使用官方CIMD、不读取模型API key，非File不静默回退File。

证据：安装probe显式改为File（原Auto失败基线保留在前文历史记录，不能外推Auto已修复），
六组合含DCR授权接受、拒绝、旧式issuer可选服务、公开服务与Bearer。
接受场景用真实127.0.0.1回调，模拟DCR/token/MCP，校验S256与resource、首次保存、
下一Turn定义曝光、实际tools/call及Observation回灌、凭据未进入模型请求、HTTP关闭。
首次Turn的工具集合保持冻结，不让安装中新增工具提前可执行。
与callback、file、refresh单位联合38通过2.23秒（5c1f6e）。先前工具调用探针在错误
的首次Turn调用未曝光工具而失败，改为下一Turn真实广告之后调用，并保留曝光断言。
静态门禁通过1043文件/72包。全项目74969仍live，启动早于本次生产修改，不是本批证明。

仍待验收/实现：预注册客户端CLI/配置入口矩阵、store I/O失败等剩余故障矩阵、
Auto/Keyring、CLI入口、远端凭据身份、
confidential client、自定义回调完整行为、发现scopes失败后的受控重试。
当前仅显式File公共客户端垂直路径，不得宣称完整MCP OAuth或A–F完成。

## 最新现场复核：首次授权的实际接入位置

本批实现范围（开始前登记）：先交付显式File、本地HTTP、公共客户端的完整安装授权
纵向链路，预注册client_id或DCR，两者都必须经过PKCE/state/issuer校验及原子保存。
复用当前选中HTTP与elicitation所有权，不调用真实官方服务。非File、远端凭据身份、
confidential client、非loopback回调、scope降级与CLI入口仍需后续实现，不能称完整OAuth。
失败必须明确隔离，尤其不能把Auto静默当File或把收到callback当成已经保存成功。

补正探针隔离：manager默认使用SHARED_TOOL_CATALOG_CACHE，按server/URL等定义来源
共享快照。多个fixture模式使用同一URL但模拟不同服务状态，public_oauth先发布lookup
定义，后续legacy_oauth即使401仍可能观察缓存定义；不是已证明凭据泄漏或登录成功。
给每个探针Runtime显式注入MCPToolCatalogCache后，e2f9fc五组合3失败2通过1.68秒，
三个OAuth模式均在宿主授权断点失败，Bearer/public控制通过。保留缓存生产逻辑，
不为了测试隔离删除真实缓存。此修订只建立可靠失败基线，首次授权实现仍未交付。

完整重读当前perform_oauth_login生产部分、oauth_client_registration、oauth_callback、
issuer_binding及CLI run_login/scopes retry，Codex commit与干净状态重新确认。
Corki对应oauth_discovery/oauth_file/oauth_refresh、CLI main及客户端消费链也已读取。
此前“无凭据对象/无刷新”的历史表格不再代表现状：

- Runtime只在显式file模式传capability_home给MCPManager；manager给本地客户端绑定
  FileOAuthAuthority。HttpMCPClient._prepare_oauth读取固定源、发现issuer，过期时调用
  refresh_oauth。后者持server/URL刷新锁，重读最新值、验证issuer、独立token请求、
  compare-and-save，并在取消后join可能已旋转token的写入。这不是首次登录流程。
- FileOAuthAuthority._save要求已存在且与previous匹配的entry；不能直接用于首次授权。
  登录写入需同一刷新锁与聚合文件锁，避免旧刷新覆盖新授权；还需Auto/Keyring策略。
- CLI main仅有resume位置参数，没有mcp login入口；skills/mcp_dependencies.py安装后
  调用discover_oauth，支持时仍仅输出“login flow is not yet available”，未调用宿主授权。
  这是下一实现的真实接入点，不能只新增脱离Runtime的URL生成函数。
- 参考代码的Auto/Cimd会构造chatgpt.com托管的Codex客户端元数据URL；此分支明确排除。
  Corki使用宿主显式合法client_id或服务广告的DCR注册，客户端名称为Corki，不借用
  官方CIMD，也不将模型服务key用于MCP授权。当前src检索未发现该官方CIMD入口。
- 首次授权必须整体连通：选中HTTP authority→发现→端点/issuer校验→预注册或DCR→
  PKCE/state与绑定回调→宿主交互→先校验state/issuer再兑换code→所选store保存→
  MCP重新建立认证连接→工具调用与Observation。拒绝/取消需释放listener和HTTP任务。
  注册可能返回client_secret，不能用当前只存client_id的FileOAuthToken假装全部支持。
- OAuthDiscovery.callback_mode的宽容投影不能替代登录校验：iss支持但issuer缺失时
  登录应拒绝；无issuer旧式服务允许满足端点绑定后使用callback-specific路径。
  endpoint绑定规则不是一律同origin；具体分支仍以原生issuer_binding为参考。

重新运行既有Runtime安装探针：29bd57为3失败2通过1.71秒。oauth/public_oauth两项
明确未到宿主授权；legacy_oauth先在工具曝光预期失败，需单独定位，不能将其误称
已验证同一登录断点。单独重跑legacy_oauth在宿主授权断点失败（cb7423，0.86秒），
说明它也缺首次授权；组合运行的曝光差异另需查缓存/顺序隔离，不能以单跑掩盖。
Bearer/public对照通过。全部MockTransport/临时凭据，无真实
浏览器、外部注册或token交换。完整成功授权RED/GREEN仍待实现，不能靠回归绿替代。

## 第292批：纠正 404 session 重握手与 OAuth transport 重建的边界

291 将 `_fresh_session` 一律视为需要重新加载凭据，这个推断不成立，已通过源码和新
Runtime 故障注入纠正。参考证据：

- RMCP 3.2.0 `transport/streamable_http_client.rs:947` 的 `perform_reinitialization`
  克隆同一个 client，复用 auth header 与 saved initialize，只更换 session/protocol headers。
  `:2030` 明确只重试收到 SessionExpired 的 ordinary POST 一次，其他 POST 错误不重放。
- `transport/auth.rs:508` AuthClient 的克隆共享 Arc<Mutex<AuthorizationManager>>；
  `:2184` 请求读取 credential store，并不无条件重新发现 OAuth metadata。
  原生 `oauth/credential_store.rs:165` 普通 load 读取 last_credentials 缓存。
- `rmcp_client.rs:1090` 固定 store 的重新读取属于真正的 transport construction/rebuild，
  不是内层 404 session 重握手。不能用这一段外层代码推断内层网络恢复行为。

修复：HttpMCPClient 使用 `_oauth_prepared` 标记完整启动阶段；已 ready 的逻辑客户端
在 404 创建 HTTP generation 时，从 current generation 继承 token、bearer 与准备状态，
不再次读取 File 或发现 metadata。初次构造/启动重试仍使用固定来源加载，新建 Runtime
客户端重新加载当前文件。过期/刷新契约仍是291明确的未完成部分。

新增真实 Runtime 两阶段验证（同一模型调用的 Observation 回灌 + 后续冷 Runtime）：

1. 首次 tools/call 返回404期间，凭据文件分别更新、删除、损坏：恢复仍使用有效的原 token，
   不重新发现 metadata；新建客户端再读取变化后的文件。
2. ReadError 表示工具结果未知：只有一次 tools/call，不触发404重建或自动重放，
   错误 Observation 回到模型，单个 ToolCallCompleted。
3. 所有 HTTP 为 MockTransport，凭据是临时 synthetic 值，全部 carrier 最终关闭。

`a51819` 修改前 **3 failed, 1 passed**；`f45b2e` 修复后 **4 passed**。
`952436` session/environment recovery、HTTP cleanup、OAuth File/discovery 和冷启动探针
共 **103 passed in 7.74s**。`55ea2d` 全仓 ruff、1032 文件格式、diff-check、compileall、
72 包依赖检查通过。本批未运行 Rust 原生测试、wheel 安装或真实 OAuth。
本节覆盖291错误的“404应重读文件”描述，不构成完整 OAuth 或 A–E 完成声明。

## 第291批：显式 File 的本地冷启动消费接入（不等于完整 OAuth）

- `CorkiSettings.mcp_oauth_credentials_store` 保存全局配置枚举，默认仍是 auto。
  本批只将显式 file 的 capability_home 传入 MCPManager，并为本地 HttpMCPClient 绑定
  `FileOAuthAuthority`。auto/keyring 没有被别名成 file，仍未实现；远端 environment 不读取
  宿主的普通 File 凭据，executor/escaped/EMA 身份尚未接入。
- `oauth_file.py` 在 File 聚合共享锁下读取完整类型化文档，坏的无关 entry 也使读取失败；
  普通 host 按名字和原始 URL 匹配，不取 executor_owned 凭据。token repr 不包含 secret。
  第一次读取失败仅输出错误类型并返回无凭据；成功后固定来源，后续错误传播，删除不解除固定。
  文件 worker 受取消时必须 join，重复取消和晚到异常不改变取消终态。
- `HttpMCPClient._initialize_session` 在普通初始化前执行 `_prepare_oauth`；显式 bearer、
  Authorization header、header helper 优先。初版 HTTP 恢复 generation 共享 File authority，
  但一律重读文件的行为错误，已由292修正为区分404重握手与真实transport重建。
- 读取 token 后通过真实发现路径加载 metadata，本地启动请求采用 30 秒 Requested，
  技能安装主动发现仍保留 5 秒 Capped。metadata 异常不降级为发送 token。
  refresh issuer 使用精确比较，变化/缺失时只清除当前 generation 的 refresh 能力；
  可用 access 可继续，过期则明确认证失败，不修改原始凭据文件。
- 当前没有刷新实现，即使 issuer 一致，进入 30 秒 skew 后也明确失败；不能因此宣称
  已对齐原生 OAuth transport。后续还需区分 SDK 实际过期/刷新边界，接入刷新事务、
  NoAuthorizationSupport/legacy metadata 完整分支、登录写入及默认 Auto/Keyring 后端。
  File 读取器本身的 Windows 共享锁语义未实机验证；本次不作跨平台完成声明。

验证：

- 290 的原始 Runtime RED 在生产接入后 `d7e309`：5 passed；随后增加 metadata 503
  不得发送 stored bearer 的场景。文件读取/identity/取消、配置及最新探针 `c3572f`：
  **47 passed in 1.29s**。
- `7f73cf`：MCP/config/storage 单元和选定 Runtime/executor 集成 **2797 passed, 1 skipped
  in 19.90s**（在最后远端隔离 guard 与新增测试前）；不能冒充最终全仓回归。
- `294037`：最终远端隔离 guard 后，session/environment recovery、HTTP cleanup、
  File 单元与最新 6 场景 Runtime 探针 **37 passed in 7.36s**。
- `c3572f`：全仓 ruff、1032 文件格式检查、compileall、72 包依赖检查通过。
  本次未执行 wheel 安装、Rust OAuth 测试、真实 token/browser/keyring 或 Windows 验证。
- 仍需独立加入真实 OAuth 凭据变化的 404 恢复场景；现有普通 session recovery 回归
  不能替代该特定场景证据。第290批 RED 是历史记录，已由本批上述探针结果更新。

## 第289批：发现结果契约补齐，凭据后端条件核实

- 原生证据：`rmcp-client/src/auth_status.rs:264` 从真正发现的 metadata 返回
  normalized scopes 和 callback mode；`normalize_scopes` 使用 Rust trim、去空、稳定去重，
  空结果为 None。`oauth_callback.rs:52` 仅接受实际 bool true；issuer 缺失时报错，
  但 auth_status 将此错误回退为 CallbackSpecific。此回退不代表登录可跳过 issuer 校验。
- Corki 原先的 `OAuthDiscovery` 只有 raw metadata/source/resource，缺少以上投影。
  本批在真实 `discover_oauth` 返回对象添加只读 `scopes_supported` / `callback_mode` 属性。
  保留原始 metadata，不把发现值覆盖到配置或 SDK 注册输入，避免破坏空 scopes 的来源语义。
  安装路径已经调用该发现函数，但尚无登录消费者使用这些属性；不宣称登录链路完成。
- 测试通过实际 MockTransport → HTTP 响应解析 → discover_oauth 返回值覆盖 30 种组合，
  包括 Unicode trim 与 Python 独有控制字符差异、大小写敏感去重、非 bool 的 issuer 支持值。
  修改前 `b7ca07` 首例 AttributeError；修改后 `bfee3e` 单元发现与两组 Runtime 集成共 68 例通过。
- `c32af2`：全仓 ruff、1030 文件格式检查、diff-check、compileall、72 包依赖检查通过。
  `027580`：Codex 工作区仍干净、commit 未变，teach.md SHA256 未变。
- 凭据实现前置证据 `400732`：当前 venv 没有 keyring 包，有 cryptography；这不是系统 keyring
  不可用的证明。原生 `oauth.rs:350–585` 的 Auto 登录保存可因后端错误回退 File，
  但 Secrets 聚合锁错误不能回退；固定 store 的刷新写入不清理另一 store。
  后续必须实现明确的后端选择与锁错误分类，不得把普通配置写入冒充凭据存储。
- 未执行：Rust OAuth 测试、真实 OAuth 服务/浏览器/token、此次 wheel 安装验证。
  未关闭：完整注册、回调/state/issuer 校验、凭据保存/重新加载/刷新；全 A–E 目标仍在执行。

## 第290批：冷启动凭据消费的独立 Runtime RED

本批没有修改生产代码；增加
`probes/test_mcp_oauth_stored_credentials_probe.py`，从真实 TOML 配置、私有临时凭据文件、
真实 Runtime/MCP 客户端与 MockTransport 验证冷启动，而不是伪造 manager 的成功返回。
`mcp_oauth_credentials_store = "file"` 当前没有被 CorkiSettings 映射，HttpMCPClient
也不读取凭据文件；有可用 access token 仍以匿名请求初始化并丢失工具。

原生调用链证据（`536f79`、`be656e`）：

- `rmcp_client.rs:1084`：显式 bearer 或 Authorization header 优先于 auth provider；
  已选 auth provider 时也不加载普通 OAuth。否则首次按 store policy 读取，成功来源固定。
  首次策略读取错误警告并作为没有凭据继续；已经固定的 store 重读错误则传播，不能重选来源。
- `rmcp_client.rs:1538`：凭据安装前重新 resolve metadata；`issuer_binding.rs` 的刷新 issuer
  比较是非空值的精确比较，不是 discovery 的根路径 trailing-slash 兼容比较。
  issuer 变化且 access token 仍在 30 秒 skew 之外时，只移除运行时 refresh 能力并使用 access；
  不删除持久化 refresh token。若 access 已失效则认证失败，不能发送 refresh token。
- 只有类型化 `AuthError::NoAuthorizationSupport` 才触发 stored-bearer fallback；
  不得将任意 metadata HTTP/JSON/issuer 错误都降级为直接发送 token。
- `oauth.rs:797` 普通 File 凭据按 entry.server_name/server_url 匹配；executor_owned/escaped
  身份另有严格规则。探针仅覆盖普通 host，不构成 executor/EMA/多 store 身份验证。
- `core/src/config/mod.rs:273` 本地开发版本会将 Auto/Keyring 解析为 File；正式版本不能
  据此默认跳过 keyring。本机 Python keyring 包缺失不等于 OS keyring 不可用。

结果 `b2cfbe`：**3 failed, 2 passed in 1.66s**，失败为 fresh/fresh_changed_issuer 的工具
不可用，以及 expired_changed_issuer 根本未进行 metadata 验证；后者不能用“没有发送 token”
假装已具备 issuer 防护。显式 bearer 和没有凭据的对照组通过。
全部使用 synthetic 凭据、临时文件及内存 HTTP，无真实 token/keyring/browser；响应 carrier
在 finally 中全部确认关闭。新探针 lint 已通过，不把有意 RED 记为回归全绿。

下一实现必须同时连接配置策略、固定 store 的读取/写入所有权、metadata 验证和 HTTP
重建，不能仅在 `_ensure_headers` 中加载一个字符串。注册、登录与刷新仍未实现，整体目标未完成。

## 已追到的调用链与数据契约

Codex路径均相对`codex-rs/`。从
`core/src/mcp_skill_dependencies.rs::maybe_install_mcp_dependencies`保存配置后开始：

| 阶段 | 已读取的原生入口与行为 | Corki对应状态 |
|---|---|---|
| 宿主HTTP能力 | runtime_context.resolve_http_client；本地发现使用5秒Capped，远端Requested；技能安装传Legacy redirect mode | 287发现经同一MCPRuntimeContext.resolve，远端借用不关闭carrier；登录HTTP适配未实现 |
| 发现支持 | `codex-mcp/src/mcp/auth.rs::oauth_login_support`只考虑HTTP且未设置bearer_token_env_var；结果Supported/Unsupported/Unknown分开 | 287安装保存后主动发现：对象/None/异常区分，支持时明确警告登录仍未接入 |
| 元数据 | `rmcp-client/src/auth_status.rs::discover_streamable_http_oauth`经OAuthHttpClientAdapter交给RMCP AuthorizationManager；未真正发现的默认metadata、NoAuthorizationSupport为不支持；其他错误不冒充支持 | 287追踪资源/AS/OIDC候选与issuer/resource检查；合成legacy不视为支持，剩余callback/scopes语义未闭合 |
| scopes | 发现值trim、去空、按原序去重；选择优先级Explicit（包括空）→Configured（包括空）→非空Discovered→Empty | 286配置保留None/空/原值；289发现投影已补齐，登录选择与消费仍未实现 |
| 客户端注册 | `oauth_client_registration.rs::start_authorization` Auto有条件优先CIMD，否则DCR；预注册client走独立分支 | 无注册或预注册登录实现 |
| 回调绑定 | `oauth_callback.rs`优先issuer绑定；否则完整MCP URL（去fragment）SHA256前9字节base64url生成回调ID，附到callback路径 | 无回调监听器、state/issuer关联 |
| 等待及完成 | `perform_oauth_login.rs::OauthLoginFlow`监听回调，默认300秒，回调后调用RMCP handle_callback_with_issuer；拿到凭据才保存；guard释放监听器 | 无OAuth登录任务或完成状态 |
| 凭据 | `oauth.rs::StoredOAuthTokens`包含server_name、url、issuer、client_id、token_response、expires_at | 无此类持久化凭据对象与加载路径 |
| 写入 | `save_oauth_tokens`先获取server/URL刷新锁；Auto允许keyring→file fallback，File和Keyring有不同契约 | 有配置原子写，不能当凭据库使用 |
| 使用/刷新 | `rmcp_client.rs::create_oauth_transport_and_runtime`重新发现metadata并验证refresh issuer；绑定具体credential store，Legacy/Coordinated分支不同 | `_ensure_headers`只装载配置/env/helper，不读取OAuth凭据 |

`codex-mcp/src/mcp/auth.rs::should_retry_without_scopes`只有scopes来源为Discovered且
错误能downcast为OAuthProviderError才返回true。不能把网络错误、超时、坏JSON、
token端点任意错误或用户显式配置的scope都当成清空scope重试的理由。

## 必须保留的安全边界

- **先校验回调再兑换code**：原生测试
  `perform_oauth_login.rs::oauth_callback_validates_rfc_9207_issuer_before_token_exchange`
  验证错误issuer、要求issuer却缺失时token请求数为0；即使未声明issuer支持，只要返回
  issuer不匹配也拒绝。RMCP拒绝错误issuer后保留授权state，允许合法回调继续处理。
- **发现容错不等于跳过登录校验**：发现阶段callback_mode错误回退CallbackSpecific；登录
  重新解析metadata并校验callback_mode与endpoint约束。登录的两条入口同样设置
  allow_missing_issuer(true)，不能概括为登录一律禁止缺失issuer；旧式同源端点可走专属
  callback ID，声明issuer响应支持的分支才要求issuer。完整分支见288节。
- **endpoint绑定不是一律同域**：`oauth/issuer_binding.rs`有issuer-bound条件以及两个
  明确兼容例外。实现前应映射具体分支，不能仅检查URL有https就发送code/token。
- **刷新issuer绑定**：缺失或变化的issuer禁止发送refresh token；若旧access token仍可用，
  原生仅移除runtime内refresh能力并继续使用access token，不将旧refresh token发给新issuer。
- **凭据存储所有权**：连接的刷新事务固定store，失败不能改读另一库的旧refresh token。
  这不等于所有快照操作永不重新解析：`StoredOAuthCredentialSnapshot.reload`对File＋Auto
  明确调用for_runtime_refresh重新解析；该快照路径与连接内部的refresh transaction必须区分。
- **CIMD身份不可照抄**：原生Auto使用ChatGPT托管的Codex client metadata URL。Corki
  不是该第一方客户端。兼容路径必须使用宿主合法配置的客户端身份/metadata或DCR，不能
  为追求表面相同而冒充Codex。这不免除实现客户端注册、issuer/state和PKCE契约的要求。
- **HTTP能力及凭据来源**：OAuthHTTP适配器保留资源origin、配置头来源和redirect mode，
  并限制响应体1MiB、重定向10次。相关完整redirect/token传送策略尚待逐段追踪；不能直接
  用不受host environment控制的全局HTTP客户端替代。
- **控制流**：登录超时/拒绝/错误不得变成成功凭据；取消应回收监听器、HTTP操作和保存
  任务；不能为重试认证而自动重放结果未知的工具副作用。

## Corki当前实际缺口

`skills/mcp_dependencies.py`保存HTTP依赖后，287先执行主动发现再publish目录。286已修复
`MCPServerSettings`丢失OAuth/client/scopes/resource的配置问题，真实HTTP客户端可收到
这些值，但仍没有注册、登录回调或凭据消费者。

`mcp/auth_elicitation.py`处理的是codex_apps工具返回的connector_auth_failure，接受后
刷新Apps目录并给模型错误结果提示重新调用；不完成普通MCP授权码登录。
`mcp/auth_challenge.py`只识别Bearer insufficient_scope；`client.py::_ensure_headers`
使用显式header/env及header helper；这些不能作为OAuth提取、保存、刷新已完成的证据。

## 下一实现前仍需补齐的源码依据

1. RMCP AuthorizationManager.resolve_metadata、PKCE生成/state校验及token交换的实际
   实现。Codex锁定rmcp **3.2.0**，Cargo.lock checksum为
   `42b6914fac0be956fe704a38239c3f44a9f841d1b06a5713d2f638065593f5b5`。
   当前任务使用的cargo registry源码目录未检索到该crate；不能把调用方签名当作其完整行为。
   第283批已在另一份既有任务临时目录找到并校验，进展见后文；不再视为源码缺失。
2. OAuthHttpClientAdapter完整头/redirect/metadata issuer-origin验证，及选中HTTP
   environment的借用、取消和关闭关系。
3. oauth.rs剩余keyring/file选择、原子刷新、版本比较与锁竞争实现；当前只完整追到登录
   保存入口和transport安装点，尚未完成Coordinated刷新状态机审计。

这三个是明确待审计项，不是“不适用”，也不阻塞继续安全的源码与离线测试工作。

## 验收要求

使用临时home、mock HTTP/OAuth provider、宿主回调交互，不读取真实凭据、不打开真实
授权页面、不向真实服务器注册客户端。必须覆盖：

- 已选择HTTP技能依赖→持久化→发现→注册/预注册→回调→token保存→MCP鉴权启动→工具
  可用→调用→Observation，以及新Runtime重新加载凭据。
- 无OAuth支持/发现错误与其它server继续启动；配置Bearer时不额外OAuth登录。
- 错误state、应提供却缺失的issuer、错误issuer、错误回调路径、过期code、缺失凭据均不能保存成功token；
  以token端点请求计数证明敏感数据没有提前发送。
- 只允许规定的scope降级；拒绝任意认证错误无限重试及未知工具副作用重放。
- keyring/file能力分支、并发刷新、issuer变化、旧access-only兼容路径。
- 取消/关闭期间监听器与HTTP任务回收、保存窗口恢复、不泄漏token到日志/模型历史。

本批没有修改生产代码或新建OAuth行为测试，没有运行Rust测试或真实登录。第281批
4067项通过不覆盖本项，不作为OAuth对齐证据。审计后续必须补充真实Runtime RED/GREEN，
不能仅新增metadata类型、空登录接口或把提示URL当作认证完成。

## 第283批：底层源码与真实 HTTP Runtime 失败基线

8e1ff4完整重读目标；上批为progress（OAuth分支审计改变接入方案），无存活作业。
扩大本地检索后找到`/tmp/corki-mcp-sse-source.bnVYzs/rmcp-3.2.0`，不是新下载或新SDK。
285f2e核rmcp.crate SHA256与上列Codex锁文件一致，171个解包文件逐一与archive相同。

已读取`src/transport/auth.rs`真实实现：

- `resolve_metadata:1503`先ProtectedResourceMetadata，再AuthorizationServerMetadata，
  最后LegacyEndpointFallback。Codex调用方将最后一种视为未发现支持，不能误弹登录。
- `generate_discovery_urls:2347`保留有路径/无路径的不同候选顺序，清除query/fragment。
  非200和不可解析metadata继续下个候选，传输异常及issuer不匹配不是这个继续分支。
- 保护资源metadata先验证resource，再记录scopes/resource，再按authorization_server(s)
  及其允许URL策略解析候选；其完整资源匹配和URL准入细节仍待读完，不能自创放宽规则。
- `get_authorization_url:1805`生成随机S256 PKCE与CSRF state，将expected issuer、
  require_issuer、requested scopes一起存入state store。
- `exchange_code_for_token_with_issuer:2073`先按state查找，再验证issuer，之后才删除
  state并发token请求；错误issuer保留state。token请求使用Stop redirect策略，包含PKCE
  verifier及resource。不能在校验前兑换code，也不能在回调认证失败时消耗合法state。
- 刷新响应不包含refresh_token时保留旧refresh_token；响应scope缺失时使用权威scope
  或已有scope；再保存更新后的凭据。完整Coordinated store原子性仍未审计完毕。

新增`probes/test_mcp_oauth_install_probe.py`，使用真实Runtime、真实HttpMCPClient、
临时技能文件/配置、两Turn及HTTP MockTransport。三个场景：OAuth依赖需要授权、
已有显式Bearer、public HTTP。模拟provider提供resource/AS metadata及注册响应；
宿主始终拒绝登录，不发送回调或兑换token。所有HTTP仅到fixture.invalid内存transport，
禁止真实browser打开；最终关闭Runtime后核所有transport已关闭。

最终源码27854/b8519a：1failed2passed1.32s；281隔离安装33111/907645：
1failed2passed1.17s，348个Corki模块来源全部位于安装目录。OAuth分支已加载技能正文、
运行到真实MCP HTTP并收到fixture401，但没有进入任何宿主授权请求；Bearer/public两组
都能暴露lookup工具。失败不是未知API或mock模型预设不存在的工具调用。
本用例只建立“安装后进入OAuth授权/拒绝”的开放RED，不证明成功登录、token保存、
工具调用回灌或刷新已经实现；这些仍需后续真实Runtime测试。

夹具开发错误如实排除：7d7656初版3failed，198033定位为直接替换HTTPX底层transport
后OwnedHTTPClient尝试把MockTransport当真实pool包装（缺_pool）。改为同时替换owned
transport构造，保留其余真实MCP会话/恢复逻辑，f56a92起形成有效1failed2passed。
最终修正清理断言放入finally并重跑；9ca49d的长header行lint失败已机械拆分修复，
f21a9f最终probe lint/format及diff通过，teach散列保持不变。

第283批没有修改生产代码、没有真实OAuth登录或Rust suite执行。所有测试作业终态。
下一需把发现、客户端身份/注册、宿主授权交互、callback/state验证与credential store
整批接入Runtime，不把本RED缩减成“显示一个URL”即完成；HTTP能力/Coordinated
store剩余审计也要继续。整个A–E目标仍未满足完成条件。

## 第284批：刷新事务与快照重载边界补正

本批已完整读取`oauth/refresh_transaction.rs`（b9a8bf）、`oauth/resolved_store.rs`
（1c873f、710058）、`oauth/credential_store.rs`（1c873f、04fea9），及
`oauth.rs:125–239`（a2bda8）。随后读完store_lock.rs（b2f7c8）和file读取/写入/删除/
identity key（2e15bd、fe2695、486bcf）；底层keyring与全部transport wiring仍需继续审计，
不宣称Coordinated全部链路完成。

- Legacy OAuthPersistor在需要刷新时spawn owned transaction，调用者取消不会取消事务。
  获取server/URL锁后才重读固定store；凭据被删除返回AuthorizationRequired并清空manager。
  其他进程已更新且token新鲜则校验绑定后直接采用，不再次请求provider。
- 过期且无refresh token、明确TokenRefreshRejected才归为AuthorizationRequired；网络/
  provider其他错误原样失败。provider刷新独立限45秒；超时结果未知，原生明确允许后续
  串行重试，这是OAuth轮换token的特定权衡，不可推广成工具副作用自动重放。
- 刷新响应先持久化到固定store，再公开给manager；保存失败恢复先前内存凭据并报错。
  响应缺refresh token/scopes时继承旧值，防止legacy persistence hook再写掉合并字段。
- for_runtime_refresh仅对真实锁竞争保留匹配server_name/url的旧snapshot并标记contended；
  没有条目不是竞争，其他错误也不冒充竞争。比较快照时去掉派生expires_in。
  File＋Auto reload例外已补正上文；连接refresh/store.load/save本身仍不允许故障切库。
- OAuth HTTP配置头仅给resource origin，显式请求头覆盖同名默认头；AgentPluginV1有
  配置头或Authorization则Stop。带resource-only头且Follow时由adapter手动限制同origin
  跳转；metadata成功响应的issuer origin还必须与请求origin相同。请求timeout跨跳转
  递减，proactive task-local超时包围整个HTTP操作，不包围凭据锁与保存。
- Coordinated store普通load返回内存缓存；获取guard后才在blocking线程重读固定库并
  校验client_id/issuer。save/clear必须升级有效guard，并将强引用交给blocking任务，
  避免await取消后写入仍在进行却提前释放锁。保存expiry采用SDK收到token的时间，
  不是磁盘保存完成时间；从expires_at重建receipt避免二次读取时钟延长授权。
- Auto遇到keyring不可用可以fallback File，但Secrets aggregate lock失败不允许fallback，
  否则可能越过正在更新的权威凭据。非阻塞try_resolve也区分这一错误类型。
- 509990读RMCP `AuthorizationManager.refresh_token:2233`及原生测试
  `refresh_guard_spans_load_exchange_and_completed_save`、
  `concurrent_refreshes_wait_for_save_and_use_the_latest_token`：guard跨load/provider/save；
  后一个刷新必须等前一个保存并使用新refresh token。这里只阅读，未运行这些Rust测试。
- File/Secrets aggregate锁读共享、写独占，60秒上限/50ms轮询；Direct keyring每项独立，
  不用aggregate锁。它与server/URL refresh锁是两层职责，不能用配置_WRITES线程锁替代。
- file读取区分executor_owned与host/local凭据，executor要求key、name、完整URL和ownership
  标记均匹配；缺标记的legacy host凭据不能被executor接管。写入同样拒绝executor覆盖host项。
- 原生file写不是temp+rename：Unix O_NOFOLLOW/O_NONBLOCK、regular-file检查、0600，
  然后set_len(0)/write_all。不能将事务串行化误报为断电/进程崩溃下的原子文件替换。
  Corki实现应保留身份隔离及拒绝symlink/FIFO边界；如改进崩溃原子性需单独记录行为差异。

本批生产修复是依赖安装的全局配置快照合并，详见skill-mcp-dependency-install.md。
没有借此宣布OAuth已接入；283真实HTTP登录RED仍需完整登录/保存/加载/刷新实现关闭。

## 第285批：主动发现与401恢复不是同一个入口

基于同一已校验RMCP3.2.0源码，29741b/cd5215/db6860/495b02读
AuthorizationManager.resolve_metadata及其资源探测、AS候选、issuer/resource检查完整链路；
677198读Codex auth_status.rs的发现适配入口。由此确定实际接入不能仅放在HTTP401处理器。

1. 安装流程先尝试发现：GET原MCP URL；没得到metadata指针时再试保护资源well-known
   路径（插入路径、附加路径、根路径），之后才直接AS/OIDC候选，最终legacy合成endpoint
   不算发现OAuth支持。200探测结果会作为metadata地址再GET一次，不是原响应直接解析。
2. 401只是一种指针来源；resource_metadata必须与原MCP同origin，坏/跨origin指针跳过。
   Header参数解析来自RMCP自身，不能复用Corki的Bearer insufficient_scope解析器冒充。
3. Resource metadata必须有合法resource、不得有fragment；同scheme/host/port下允许
   相同路径或按路径段边界的父资源，query可不同。不能用字符串startswith放过/mcp-other。
4. AS候选可来自单值或列表，也可相对metadata URL解析；先做native host准入，再按
   well-known显式文档或issuer URL走不同发现分支。准入包含私网/loopback/link-local等
   IP分类及metadata主机名，允许本地loopback服务的明确例外；这只是字面host检查，
   不能宣称已证明DNS解析层SSRF隔离。不是简单的“任意HTTPS都允许”。
5. AS issuer须匹配显式resource metadata issuer或由well-known路径还原的issuer；仅根
   trailing slash有兼容，不能对所有路径rstrip('/')。Codex发现阶段显式allow_missing_issuer
   为true，登录两入口同样允许缺失metadata issuer，但需要独立端点/回调绑定校验，
   不能解释为无条件接受；callback_mode发现回退不授权token发送。288进一步补正。
6. discovery_get使用Stop交给HTTP适配器，再由RMCP手动限制同origin跳转；最多10次GET，
   第10次仍重定向即失败。它与adapter针对Follow的10跳逻辑不是同一层；不要合并计数。
   每次GET使用协议头2024-11-05。5xx、408、425、429是发现错误，不当作404继续下一候选；
   不合法metadata JSON可继续AS候选，而合法结构的issuer/resource不匹配直接报错。
7. 7f6f35补读config/src/mcp_types.rs的OAuth配置及credential identity：client_id/
   callback_url/callback_port、scopes、oauth_resource分开；端口先server后global。
   非local凭据名编码environment＋server；local名称若碰executor/local/ema-idp保留前缀
   则先escape，不能用裸server name共享不同执行环境的凭据。

新增public_oauth Runtime用例：MCP允许匿名initialize/tools/list且GET返回405，但well-known
公布OAuth；按原生主动发现仍需宿主登录，用户拒绝后工具仍可用。已有public夹具确实
返回well-known404，无需修正，不能把它与public_oauth混为一谈。
e46503源码2failed2passed1.39s；284安装e494e9同为2failed2passed1.42s，348模块来源受检。
两失败分别为oauth与public_oauth未进入登录；Bearer/public控制通过。没有真实browser、
code/token或外部注册，finally核所有HTTP transports关闭。b4917f probe lint/format通过。

本批没有新增生产OAuth实现。新证据约束下一步接入位置和错误语义，不把额外测试或
5859项其他回归通过当作登录已完成；完整成功登录/保存/恢复/刷新验收仍开放。

## 第286批：OAuth配置在安装与加载链路丢失（修复前审计）

bbb817完整重读目标；前批为progress（收取大回归终态、主动发现源码和新增Runtime RED）。
753123完整读Corki config/features.py及Codex config/src/mcp_types.rs Raw解析映射：
原生scopes保留None/空/原序和文本，不在配置阶段归一化；oauth的client_id/callback_url
是可选原字符串，callback_port是可选u16，配置可取0，登录阶段才拒绝0。oauth及
oauth_resource不允许stdio，scopes则是shared字段、stdio也可解析。

Corki from_mapping静默忽略三字段，导致技能HTTP依赖写入的callback_port在原子写结果、
manager catalog和真实HttpMCPClient settings均消失；原生安装的候选相等性也因此无法
区分OAuth配置不同的声明。拟增加不可变值对象和严格类型解析，接入已有settings与安装
快照链路，不改登录触发、不声称已认证。callback URL有效性/issuer绑定留给登录处理，
不能在解析层自创与原生不同的字符串trim、scope去重或端口0拒绝规则。

实现：新增不可变MCPServerOAuthSettings，from_mapping/直接SDK构造统一类型验证；
MCPServerSettings保留oauth/scopes/oauth_resource，None与空对象/空数组不同，scopes
转tuple且不改文本/顺序/重复项，字典输入被转为独立不可变对象。三字段随现有settings
进入add_missing_mcp_servers快照、manager声明、真实HttpMCPClient，无额外旁路Runtime。
本批不新增auth枚举、全局OAuth配置、凭据identity或连接凭据刷新key，不暗示登录已接入。

修复前：b7bfc9中19个unit都暴露静默丢弃/缺少校验；初版Runtime夹具错用顶层
oauth_callback_port，出现KeyError不是产品证据。按真实skills/policy.py改为
oauth.callback_port后405795有效RED：文件已有18473但真实HTTP客户端settings丢失。
旧284安装cac2c2同一Runtime测试1failed0.57s，348模块来源全部位于旧安装目录。
源码首轮938aec 52passed6.26s；补4项不可变/nullable/相等性/坏配置禁止写后，
0772d3相关配置/skills/plugins/MCP reload/Runtime回归1195passed1skipped62.12s。
安装adbbd7最终定向56passed7.05s，349个Corki模块来源位于隔离安装目录。

产物`/tmp/corki-oauth-configuration.17ZMV5`，1b5c26 wheel/sdist构建成功、78218b安装；
c1c867核370个Python源码/wheel/安装文件一致，wheel SHA256
`78bdf7e0228c7c63c7c9cdb4fc555d15d348ff57490c8f37cdb7de1a0f5149f0`。
beac7c全仓lint、1026文件format、compileall、diff及72依赖检查通过；f8e6a6 Codex clean、
teach散列不变。f21474普通OAuth开放probe仍2failed2passed1.38s，不属于上述绿色选集。
所有验证使用临时配置与HTTP MockTransport，没有真实登录、端口监听或令牌操作。

## 第287批：主动发现接入计划

abfae2完整重读目标，上轮progress，旧作业全部终态。基于285已完整追踪的RMCP主动
发现链，新增安装保存后、目录发布前的真实发现阶段。它只用于已经准入并新增的HTTP
依赖，显式bearer配置跳过；重走当前runtime environment，不能绕回本地HTTP。
每次GET按原生LOCAL 5秒/远端SDK 30秒限制，最多10次同origin探测跳转，每响应1MiB。
正常不支持返回None，网络/临时HTTP/合法结构的issuer或resource错配保留错误语义，
安装器对单server警告继续，取消传播并join关闭。返回发现结果不生成伪登录URL或凭据；
支持登录但尚无后续流程时必须明确警告，完整OAuth仍开放，不将本阶段标为登录完成。

实现：mcp/oauth_metadata.py按typed JSON字段识别metadata，已知重复字段拒绝；处理资源
父路径段匹配、issuer根slash区别、候选顺序和WWW参数。mcp/oauth_discovery.py经选中
authority进行真实GET、限制体积/跳转/超时、持有response/client关闭任务；不应用普通MCP
重试。安装器每项重新admit，单项发现异常警告继续，最终发布前再admit。显式Bearer/
stdio在读取HTTP环境或env secret之前跳过。没有构造伪授权URL，没有保存任何令牌。

验证：ebba9e源码修复前5个真实Runtime主动发现场景全失败；旧286安装d90e6e同5failed
1.28s（349模块来源）。当前5场景覆盖supported/unsupported/503/issuer错误/resource错误，
逐条核实际GET次序，错误不阻断匿名MCP工具启动。unit覆盖跨origin不发送资源专用头、
远端借用carrier不关闭、临时状态不重试、10次跳转上限、1MiB体积、重复取消join response
close、已知JSON字段重复、Bearer/stdio早退和HeaderValue.to_str ASCII边界。

源最终a95e82相关回归1002passed1skipped19.57s；安装5db320定向62passed8.07s，351个
模块来源受检。2e5d06完整OAuth开放probe仍2failed2passed1.44s（两类OAuth尚无宿主登录）。
286配置传播测试为新发现阶段补了明确内存HTTP carrier，原OAuth probe补全附加well-known
路径响应；不允许因新阶段绕过夹具而访问真实网络。f4fbc2/d95b85出现的样式错误已修复，
d83b87全仓lint/1030文件format/compileall/diff/72依赖检查通过；f44632 Codex/teach不变。

最终包`/tmp/corki-oauth-discovery.sAeXfL`，d64a39构建wheel/sdist，beed4b隔离安装；
8f157d核372个Python源码/wheel/安装文件一致，wheel SHA256
`64a56efce5c6f21476c9f19d7c2c2c41335f2ab56baba414d5b034fc6fb45d12`。
初次打包后追加ASCII边界修复，最终包已重新构建，不沿用旧产物。

尚未闭合：scopes选择/归一化、callback_mode、HTTP POST注册/token适配、登录/存储/刷新。
AS host准入使用解析后的IPv6地址拒绝本地/私网；与原生Url.host_str字面表示在所有IPv6
分支上的等价性尚无配对测试，不能宣称整个URL准入完全一致，也不能为复刻疑似字面解析
空隙而放开访问私网。该安全差异继续登记，不影响本轮IPv4/域名及真实Runtime证据。
本批所有作业终态；未运行Rust suite、真实模型或真实OAuth登录；全A–E继续active。

## 第288批：注册调用链补正，防止实现错误的安全/降级语义

bf9616完整重读目标；前批progress（主动发现真实接入和配对验收）。本批生产未改。
644143完整重读oauth_client_registration.rs、oauth/issuer_binding.rs；27dd74完整读
oauth_callback.rs及perform_oauth_login回调解析/URI构造，b4d8d3读登录new的双分支；
d0128d/427ddb读实际原生注册测试和预注册入口，fe5113/c74b34下追RMCP Session作用。

1. **登录不是一律缺失issuer就失败。** 动态注册与预注册的metadata manager都显式
   allow_missing_issuer(true)。issuer-bound=true要求非空合法issuer；无issuer且非issuer-
   bound时，authorization/token origin必须相等，然后走callback-specific防mix-up。
   原生自动CIMD及DCR测试的默认metadata本来就没有issuer（除非启用issuer响应支持）。
   之前“登录严格”只应理解成独立检查endpoint/callback，不能套成全局强制issuer。
2. 非issuer-bound且有issuer时，原生接受authorization.origin==issuer.origin **或**
   authorization.origin==token.origin，还有Figma/Robinhood的精确例外。不是要求全部
   endpoint同域，也不是要求token.origin始终等于issuer。issuer-bound分支提前返回，
   不能把另一分支的token解析/同域检查伪装成所有路径都执行过的原生规则。
3. client_id先按trim判断是否为空，但非空值不被trim后重写；预注册callback缺专属ID且
   无issuer绑定时，使用global/default callback兼容回退，再追加ID。不是所有自定义
   callback原样使用，也不是所有预注册客户端都复用共享/callback。端口0在监听前拒绝。
4. callback query解析只解码值，重复值后者优先；同时有code/state时优先于error字段；
   provider error可以由error_description单独触发。此处不取代后续state/issuer校验。
5. **外层空scopes不等于最终无scope。** RMCP AuthorizationSession::new对空request.scopes
   再执行select_scopes：当前/挑战/resource scopes累积去重，无这些值才采用AS/default；
   非空request scopes则只按advertised情况追加offline_access。Codex预注册入口绕过new，
   直接get_authorization_url传入scopes，因此两路径不可共用一个“空就默认”的简化实现。
   外层should_retry_without_scopes的来源/错误门槛仍需保留，但不能宣称它必然让最终
   DCR授权URL没有scope；可能被SDK再次补入，这是源码实际行为，须独立映射测试。
6. RMCP正常resource参数来自protected resource metadata，否则base_url。Codex额外
   oauth_resource经trim非空后append_query_param追加到授权URL，不替换原有query项；
   此外层入口没有相同的token参数改写。不能为了看起来符合统一配置而自创覆盖所有
   authorization/token请求的行为。
7. DCR发送Follow请求，成功接受2xx并解析client_id；返回client_secret=""按无secret，
   非空secret进入OAuthClientConfig。它不同于预注册configure_client_id的无secret路径。
   仍不得借用Codex第一方CIMD身份；Corki应使用自己合法客户端身份及DCR兼容路径。

新增真实Runtime `legacy_oauth`：metadata不带issuer且不声明iss支持，同源authorize/token
满足原生旧式条件，仍应进入宿主登录而不是当成unsupported；host拒绝、不交换token。
b0f03a源码3failed2passed1.70s；287安装232a4f同3failed2passed1.73s，351模块来源受检。
三失败为oauth/public_oauth/legacy_oauth的登录缺失，Bearer/public控制继续通过；所有
HTTP为内存fixture，finally关transport，未打开浏览器、监听真实callback或获取凭据。
1c5de4 probe lint/format通过。这里是改变后续实现方案的源码/测试证据，不是登录完成。
