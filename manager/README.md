# Corki Manager

本机 H5 管理工作台，默认地址 **http://127.0.0.1:17002**。
前端为 HTML / CSS / JavaScript，配置编辑器使用本地打包的 CodeMirror，无 CDN；后端是 Python
HTTP 服务，复用 Corki 的 JSONC 配置解析器、技能解析器和 MCP 配置校验。
主动点击模型测试时会发起临时模型请求；点击 MCP 测试连接时会初始化服务并读取工具列表。

## 启动

先安装 Corki 开发环境，然后在仓库中运行：

```bash
./manager/bin/start.sh
```

脚本也支持绝对路径，从任意目录运行。使用仓库 `.venv/bin/python`；可通过
`CORKI_MANAGER_PYTHON` 指定已安装 Corki 的 Python。前台运行，Ctrl+C 停止。

```bash
./manager/bin/start.sh --port 17002 --home ~/.corki --workspace /path/to/project
```

只监听 `127.0.0.1`，没有远程登录功能；手机适配指移动宽度布局，不代表已开放
局域网访问。无需改变 Corki 的 provider 配置，管理服务和模型网关分别使用
17002 与 17001 端口。

## 已实现

| 页面 | 能力 |
| --- | --- |
| 主页 | 用量、默认模型、近期会话；快捷工具包含使用指引、快速配置与快速启动 |
| 模型管理 | 供应商卡片 → 模型卡片 → 配置详情；连接管理、模型常用参数编辑、默认模型切换 |
| Skills | Corki 专属 / 公共 / 内置三分区；目录树、CodeMirror 多文件查看和编辑、ZIP 导出；文件夹 / ZIP / SKILL.md 导入公共目录 |
| 知识库 | 用户与内置文档分组、启停、Markdown/文本导入、搜索、编辑、移入回收目录 |
| 工具 | MCP HTTP/stdio 管理与连接测试；独立 JSON 自定义工具的添加、编辑、启停、删除与参数测试；内置工具说明 |
| 简单对话 | 使用默认模型或手动切换供应商与模型，流式对话并渲染 Markdown；可复制回复原文，管理页内部切换不打断回复；本地保存记录，清空聊天保留上下文，新会话重置上下文 |
| 记忆 | 本机 Markdown 记忆浏览、搜索、编辑；含被脱敏内容的文档只读 |
| 配置文件 | 编辑完整 settings.json，自动 JSON/JSONC 校验、Corki 加载校验、历史版本查看与回滚；只读配置手册展示原始 JSON 字段树、类型、必填条件、默认值与示例 |
| 运行日志 | 最近 40 个会话、80 次工具执行和输出，15 秒刷新，敏感凭据脱敏 |

用户知识库文档保存在 `~/.corki/manager/knowledge/`，内置知识库目录为其中的
`.system/`，内置文档只读。停用状态保存在 `~/.corki/manager/knowledge-state.json`；
**知识库尚未接入 Agent 自动检索或向量索引**，开关只管理文档状态。
搜索针对标题、摘要、路径等列表信息；编辑器可以查看全文。
不将“已保存”显示为“已索引”。MCP 列表目前只管理用户配置，不包含 mcporter、
插件注入、运行时临时工具；已配置不代表连通。网关指示仅检测端口，不验证账号。

配置更改供新的 Corki 会话使用，不强制刷新正在运行的会话。Skills 清单读取
Corki 用户与内置目录、通用 Agents 目录，以及选定项目中的对应 skills 目录，不表示已经
覆盖插件缓存或所有上级项目目录。会话状态来自数据库，不表示进程实时存活。

## 写入与数据保护

- 所有测试使用临时目录；真实配置不作为测试数据修改。
- 修改前比对 SHA-256 版本，避免覆盖已变化的文件；原文件保存在同目录
  `.manager-backups/`。这能发现写入前的外部更新，不是跨进程事务锁。
- 原子替换文件，文件权限 0600；保留未修改配置区块及其注释。修改的区块会
  重新序列化，区块内部原注释可从备份恢复。
- 知识库删除移入 `~/.corki/manager/trash/`，可从该目录手动恢复。
- 仅接收本机合法 Host、同源 Origin 和管理页自定义请求头的写请求。
- 普通列表不回显供应商密钥；完整配置编辑器及历史预览会展示原始配置（含密钥），只在本机使用。文本日志输出隐藏已知配置凭据和常见 token 格式。
  日志源仍应避免主动记录秘密，模式匹配无法识别所有任意敏感内容。
- 本机其他程序具有访问服务的能力，不能当作多用户或公网管理服务部署。

## 验证

```bash
.venv/bin/python -m pytest manager/tests/test_manager.py
node --check manager/static/app.js
```

浏览器测试遵循用户 `read_chrome` skill 的本地 CDP 流程，不需要 npm 依赖：

```bash
CHROME_DEBUG_PORT=9225 node manager/tests/browser_smoke.mjs
CHROME_DEBUG_PORT=9225 MANAGER_TEST_URL=http://127.0.0.1:17006 node manager/tests/simple_chat.mjs
CHROME_DEBUG_PORT=9225 MANAGER_TEST_URL=http://127.0.0.1:17006 node manager/tests/knowledge_cards.mjs
```

先在独立调试 Chrome 中打开管理页。脚本检查桌面/390px 手机布局、九个路由、
弹窗、搜索和导航。`--write` 只允许测试地址 `127.0.0.1:17003`，需用临时
`--home` 启动测试服务并配置一个测试供应商，用来验证 CRUD，不用于真实工作区。

## 配置版本

配置页直接编辑用户目录下的 `settings.json`，沿用 Corki 的 JSONC 规则（支持注释和
尾逗号，拒绝重复键及非有限数字）。输入停止后自动校验，保存前再次校验；错误不写入
文件。模型详情仅展示常用参数，保存时保留其他模型元数据。

历史版本存放于 `~/.corki/manager/config-versions/`，目录权限 0700、文件 0600，
包含完整凭据，不写入 IndexedDB/localStorage。配置表单写入同样记录版本；读取
版本列表时会记录外部修改后的当前配置。未被观察到的外部中间修改无法追溯。
保存后立即替换配置文件，回滚会追加一份新版本；旧版本保留，当前相同内容的
重复保存不创建冗余版本。未保存的编辑在页面内切换时保留，离开浏览器页面有提示。

新会话通过 Corki 正常配置加载流程读取；已有会话不会热更新。环境变量、项目配置
或会话选择仍遵循 Corki 原有优先级，并可能覆盖对应用户配置项。

### 本次生效验证

已使用 Chrome 验证模型分层导航、供应商/模型保存、模型详情参数修改、默认模型
切换、错误 JSON 拒绝保存、历史预览与回滚，并检查桌面和 390px 布局。
24 项后端测试覆盖配置保存、JSON/JSONC、版本记录、并发冲突和原有功能。

实际配置验证临时修改了默认模型推理等级，然后使用与 CLI 相同的
`CorkiPaths.discover().config_file` 和 `CorkiSettings.for_directory(...)` 加载，
确认修改生效；随后通过页面回滚，核对文件字节及实际加载值均恢复原样。
验证不发送模型请求、不重启正在运行的 Corki 会话。

## 配置编辑工作区

- 默认只读，点击「编辑配置」后修改；保存、取消编辑都有明确状态，离页保留草稿。
- JSONC 高亮、行号、结构折叠、查找、撤销和保留注释的格式化。⌘/Ctrl+S 保存。
- 自动校验与错误行列标记；并发文件更新会拒绝保存并保留草稿。
- 点击历史记录即可在主编辑区只读查看，并高亮所选版本；可将该历史版本与当前磁盘配置对比。对比不包含未保存草稿。
- 并排 / 行内差异视图、增删行高亮、差异计数与跳转、折叠未变化内容。
- 全部编辑与对比在本机完成，完整配置不会传到第三方服务或写入浏览器持久缓存。

### 前端构建

部署直接使用已包含的 `static/config-workspace.js`，不需要在运行时安装 Node 依赖。
修改 `frontend/config-workspace.js` 后执行：

```bash
npm --prefix manager ci
npm --prefix manager run build
```

依赖版本锁定于 `manager/package-lock.json`，第三方声明见 `THIRD_PARTY_NOTICES.txt`。

配置工作区的浏览器回归脚本为 `manager/tests/config_workspace.mjs`。测试服务必须
使用 `/tmp/corki-manager-editor-qa/` 下的独立 home，并监听 17003；脚本会覆盖该
测试目录中的配置。打开该地址的调试 Chrome 后执行 `node manager/tests/config_workspace.mjs`。
它覆盖高亮、折叠、撤销、格式化、草稿、保存、校验、版本对比、并发冲突和回滚。

版本列表首项在名称后标记「（当前）」。点击历史记录会在主编辑区显示完整只读内容，并高亮所选项；点击首项返回当前配置。历史版本可使用「对比当前」进入固定的历史 / 当前差异视图，不支持任选两个历史版本对比。切换有未保存草稿的版本前会确认。该交互的只读回归测试为 `node manager/tests/history_preview.mjs`。

管理页及 Corki 运行时均不再自动扫描用户或项目的 `.codex/skills` 目录。

## Skills 目录与导入

- 内置：`~/.corki/skills/.system/`，只读，由 Corki 更新。
- Corki 专属：`~/.corki/skills/`，可编辑；现有项目技能也保留展示并标注「项目」，项目文件只读。
- 公共：`~/.agents/skills/`，可编辑，导入默认写入此处。公共文件修改会影响共用它的 Agent；启停只修改 Corki 本地配置。

导入支持一个完整技能文件夹、ZIP 或单个 `SKILL.md`，先预览校验，再整包写入。
每包最多 200 文件、8 MB，入口最多 128 KB。使用运行时同一 frontmatter 解析器；
同目录名不覆盖，可填写其他目录名。拒绝隐藏文件、路径跳转、重复路径和 ZIP 符号链接。
ZIP 的执行位会保留，文件夹上传不携带文件权限。导入不会运行脚本、安装工具或授权账号。

### 技能文件工作区

技能详情提供可展开的目录树和文件搜索，支持 Markdown（含 YAML 头部）、Python、
JavaScript / TypeScript、JSON、YAML、Shell、XML 和 CSS 高亮，以及行号、折叠、查找与撤销。
默认只读，用户/公共技能通过「编辑」「保存」修改单个文本文件；内置和项目技能由 API 强制只读。
切换文件保留草稿、光标和滚动位置，关闭有草稿提示，Cmd/Ctrl+S 保存当前文件。
并发修改会拒绝覆盖并保留草稿；保存保留脚本执行位，旧内容写入管理备份。

ZIP 导出保存后的整个技能目录，保留文件原始字节、目录和执行权限，不包含管理备份、
Git 目录、Python 缓存及符号链接。支持常见位图预览；文本/图片在线读取上限 256 KB，
更大的文件和其他二进制可导出后查看。目录清单上限 2000 项，导出总文件大小上限 32 MB。

管理页以 `#FF6A00` 为品牌橙，深橙用于文字和主要操作，浅橙用于选中与聚焦状态；
成功、警告、错误与 diff 增删保留各自的语义颜色。编辑器全部本地打包，无 CDN。


## 自定义工具

每个文件定义一个工具，保存在 `~/.corki/tools/custom/<name>.json`。
管理页面可直接维护这些文件；不会将其写入 `settings.json`。
“导入工具”支持上传或拖入单个 JSON 文件，也支持粘贴完整 JSON；导入前校验字段，按 JSON 的 `name` 保存，拒绝覆盖同名工具。
Corki 初始化运行时时读取文件，注册为 `custom__<name>`，模型看到名称、描述和参数定义。
启停、编辑或删除将在下次初始化运行时生效，已启动的运行时保留自己的配置快照。
同名文件不可覆盖新增；更新校验文件版本，并备份原文件，删除移入管理服务回收目录。

HTTP 示例 `query_order.json`：

```json
{
  "name": "query_order",
  "description": "根据订单号查询订单信息",
  "transport": "http",
  "url": "http://127.0.0.1:18000/orders/query",
  "parameters": {
    "type": "object",
    "properties": {"order_id": {"type": "string", "description": "订单号"}},
    "required": ["order_id"]
  },
  "timeout_seconds": 30
}
```

HTTP 默认使用 POST，请求体直接为模型给出的参数对象，如 `{"order_id":"123"}`。
设置 `method: "GET"` 时，参数编码为 URL 查询参数，保留地址里已有的查询参数。
接口返回 JSON 或纯文本即可，无需 MCP 初始化或 `tools/list`。非 2xx 状态视为失败，不自动重试或跟随重定向。
可添加 `headers` 字符串对象；也支持在文件中使用 `env_headers`，其值是本机环境变量名称。

本地进程将 `transport` 改为 `process`，以 `command`、`args` 替代 `url`：

```json
{
  "name": "echo_text",
  "description": "回显输入文本",
  "transport": "process",
  "command": "python3",
  "args": ["/absolute/path/echo_tool.py"],
  "parameters": {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"]
  }
}
```

`echo_tool.py`：

```python
import json
import sys

arguments = json.load(sys.stdin)
print(json.dumps({"text": arguments["text"]}, ensure_ascii=False))
```

每次调用启动一个进程，参数对象写入 stdin 后关闭输入；stdout 为结果，stderr 为诊断，退出码非零表示失败。
命令和启动参数直接传递，不做 shell 插值。可选 `cwd`（默认为当前工作空间）、`env` 和 `timeout_seconds`。
参数定义沿用 Corki 工具执行器支持的 JSON Schema 子集，不会自动补齐 Schema 的 default 值。
`enabled` 默认为 true，设为 false 后下次启动不注册。配置文件限 256 KB，单次输出限 1 MB，超时范围 0.1–300 秒。
本地及 HTTP 工作进程均使用 Corki 的执行权限编译与审批检查；受限网络或文件访问可能被当前沙箱策略拒绝。


自定义工具可在添加/编辑窗口或卡片上点击“测试”。输入调用参数后，会通过与运行时相同的工具执行器校验参数并真实调用；不会保存或修改配置。
测试可选择 JSON 或非空文本返回格式；默认 JSON。执行异常、HTTP 非成功状态、非零退出码、超时、空输出或返回格式不符均标记失败，原始输出可以查看和复制。
测试成功只表示执行与所选格式校验通过，不代表业务语义正确。测试上限为 30 秒；关闭结果窗口不会撤销已经发出的业务请求，页面不会自动重试。

## 主页快捷工具

快速配置从 `templates/quick-setup.json` 生成完整的新配置，不与旧配置合并。单选厂商与模型、填写 API Key 后，所选组合自动设为默认，默认上下文窗口为 256K（262144 tokens）；模型按当前接口支持范围写入 `supported_reasoning_levels`，支持 `medium` 时优先使用，否则采用模型默认行为。当前接口没有可用推理等级时写入空列表，不向上游发送该参数。管理页与 CLI 按此目录显示可选等级。接口地址和协议由服务端模板固定：OpenAI 使用 Responses，其余使用官方 Chat Completions 兼容接口。写入前校验并保留旧版本，遇到并发修改拒绝覆盖。现有 Corki 会话不强制切换，新启动实例读取新配置。

模型管理首页的“快速添加”复用同一模板，可在单个供应商下多选模型并一次添加。已有供应商会保留连接设置、密钥和其他模型；任一所选模型已存在时整次添加会被拒绝。现有默认模型保持不变；在空配置中首次添加时，第一个所选模型会成为默认模型。写入仍会校验配置并创建历史版本。

模板覆盖 DeepSeek、Qwen、Kimi、OpenAI、Claude、Google Gemini、GLM、MiniMax、xAI、Mistral；按可用的最近三代组织，记录官方来源和核验日期。DeepSeek 当前仅有两代通用 API 模型；不以旧模型别名补足。MiniMax M2.7/M2.5 的新用户接入截止日为 2026-10-19，届时自动从快速配置隐藏；套餐专用预览模型未纳入。目录是版本化静态模板，需要随官方发布更新，并非实时模型发现。Qwen 使用北京地域、MiniMax 使用国内平台、Gemini 使用 AI Studio API；套餐或其他地域密钥应在模型管理中单独配置。

Claude 使用其官方 OpenAI SDK 兼容接口，支持基本聊天和工具调用，不承诺与原生 Messages 全功能等价。Chat 运行时保留同一接口、同一模型返回的推理内容及工具签名，以支持 Kimi/DeepSeek 推理回传与 Gemini 工具循环；切换接口或模型时不回传这些专有数据。

快速启动目前支持 macOS：可输入工作目录、通过系统窗口选择目录，或留空使用 Corki home 下的 `main/default`。优先使用 iTerm，否则使用 Terminal。启动命令固定为 Corki，目录经过 shell 转义，使用当前管理服务的 Corki home。首次使用可能需要系统自动化授权。
