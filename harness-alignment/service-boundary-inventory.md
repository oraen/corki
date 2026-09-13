# 当前服务入口与排除项盘点

本轮范围是生产入口及旧数据恢复边界，不以搜索不到域名代替调用链证据。
Codex官方服务实现属于用户明确排除项；普通工具搜索的语义参考仍是本地
ddf04ad26789d040f9ef6a96736f76602e35a6cc的tools/handlers/tool_search.rs，
不复制tools/context.rs的原生ToolSearchOutput输出协议。

| 入口 | 当前来源及可执行边界 |
|---|---|
| 模型请求 | core/runtime.py::_create_model按api_mode构造；models的两个适配器只拼接配置base下的/chat/completions或/responses，空base/key在HTTP前失败，provider标签不选官方服务 |
| 搜索与定义加载 | tools/search.py产生普通ToolResult/discovered_tools；discovery.py建立下一请求的平铺function定义，未使用原生tool_search_output |
| 专属响应 | models/responses.py在added/done、专属delta前缀及completed内容边界拒绝native search、namespace、custom/hosted工具；不会将其执行或认作正常完成 |
| 旧配置及归档 | ModelRequest/CorkiSettings把旧native选择归一化；Lite墓碑不能恢复传输；旧HostedToolItem只作为标注数据回放，opaque compact归档保留但拒绝专属回放，不删除用户历史 |
| history/notes | Runtime显式构造LocalHistoryNotesBackend，读当前Thread会话仓库及SQLite NotesStore；history_notes/service.py无官方ingestion或远程history客户实例 |
| OAuth登录 | cli/main.py的mcp login→mcp_login.run_login查找配置MCP，再执行第三方发现/授权；模型选项不适用此命令，没有模型账号登录/配额子命令 |
| MCP远程载体 | MCPRuntimeContext只接宿主显式传入的HTTP capability，默认无远程环境；ExecutorRpc.connect要求宿主URL，未解析的environment拒绝而非回退。它不执行官方账号/环境自动发现，不因provider名称启用 |
| 旧Apps/连接器入口 | Runtime/Manager/Connection已不接受旧apps_cache_context；skills不接受connector_names覆盖。普通MCP元数据可以有相似名称，但不成为官方连接器服务入口 |

生产Python/提示词的官方域名/专属头搜索仅命中memory/git_baseline.py的一段
旧本地Git提交识别字符串（noreply@openai.com）；该函数只识别内部基线所有权，
不是请求地址或新提交的署名，也没有HTTP调用。不删除它而破坏旧基线兼容。
plugins中的agent-plugins.org schema字符串同样是格式识别，不是Schema下载器。
通用MCP OAuth、显式Bearer/Header及宿主选择的远程载体不能因名称包含auth/remote
被误删；它们不等于Codex官方账户体系。

## 当前实证

native search接收、Lite、namespace、hosted旧归档、普通deferred wire及两个
旧Apps/connector入口单位测试联合154通过（a7b861，18.07秒，59271退出0）。
包含partial之后的added/done/completed/delta拒绝、不发布工具结果/成功Turn，
以及两适配器配置域名和模型标签交叉下的普通搜索→加载→调用。
旧账号cache参数在Runtime/Manager/Connection资源分配前拒绝，不能恢复旧平台。

B5按用户的普通函数路径已核验；本轮没有生产修改，不冒充新的修复红绿证据。
C9/C10的三入口摘要隔离见provider-isolation-refresh.md；通用OAuth与模型凭据
隔离也见该文件的独立实际HTTP测试，不把本次154说成包含OAuth全矩阵。

限制：这是内置代码的调用路径保证，不是对用户任意自定义ModelPort、工具代码、
shell命令、代理或所配置第三方服务内部行为的网络封锁。即使用户明确配置官方模型
地址，普通模型请求仍可访问该地址，但不会因此获得账户/配额或专属协议能力。
本批没有运行真实供应商请求；其余A–F生命周期与CLI差异不由此自动关闭。
