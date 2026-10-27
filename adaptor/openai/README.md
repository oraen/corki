# 本地 OpenAI 协议适配网关

`src/corki_openai_gateway/gateway.py` 提供只监听 `127.0.0.1` 的 HTTP 服务；`src/corki_openai_gateway/chat.py` 转换
Chat Completions 请求和 SSE 响应。支持 `/openai/responses`、`/openai/chat/completions`
及它们的 `/openai/v1` 前缀。模型和 reasoning effort 由请求指定。

这是独立 Python 项目，运行依赖为 httpx、truststore 和 cryptography；不依赖或导入 Corki。
安装 Corki 不会安装此网关，网关也不负责启动 Corki。可以单独复制本目录部署。

## 安装与运行

在仓库根目录运行：

```bash
./adaptor/openai/bin/install.sh
```

脚本通过独立 `pyproject.toml` 和 `uv.lock` 安装到 `adaptor/openai/.venv`。
也可使用标准 Python 安装：在本目录创建 `.venv` 后运行 `.venv/bin/pip install .`。

进入 `adaptor/openai` 后，可以直接前台运行：

```bash
.venv/bin/python -m corki_openai_gateway --serve --port 17001 --key-file ~/.corki/gateway/local.key
```

`--key-file` 是下游客户端连接本地网关的密钥文件，须与客户端配置的
`api_key` 一致；它不是上游登录 token。管理页使用固定的本地协议密钥；它不是 OpenAI 登录凭据。

## 本机后台服务与管理页

可在管理页「主页 → 工具箱 → 套餐转发」启用 OpenAI，或直接执行：

```bash
./adaptor/openai/bin/start.sh
./adaptor/openai/bin/stop.sh
```

脚本与管理页使用同一个独立服务控制器，不依赖 Corki Agent 或管理服务持续运行。
安装脚本将独立解释器位置记录到 `CORKI_HOME/gateway/installation.json`；也可通过
`CORKI_GATEWAY_PYTHON` 指定已安装本项目的解释器。默认端口为 17001。

控制器动态读取 Codex 登录文件，并创建本地客户端密钥文件。管理页生成的供应商名为
`adaptor-openai`，使用 Responses 协议和 `/openai` 地址前缀。本地协议密钥为固定值，
不是 OpenAI 平台密钥，不能用于上游认证。Token 不复制到 Corki 配置或前端。

管理页通过 `start-auto` 开启：优先绑定 17001，占用时由系统原子分配空闲端口；
返回实际端口后由管理页更新供应商地址。正在运行的本实例保持现有端口。关闭供应商时保留模型配置；
网关停止使用数据目录专属的控制凭据，校验实例归属，不按进程名称杀进程。
元数据和控制凭据保存在 `CORKI_HOME/gateway/`，日志为 `service.log`。

旧 macOS launchd 部署首次迁移时会先 `bootout`，备份私有 plist 到上述目录并移除原启动项，
防止旧 KeepAlive 服务重新占用端口。原来使用登录启动的部署，会迁移到按数据目录归属区分的新启动项；
关闭或切换端口时先卸载该启动项，关闭后不会在下次登录时自动拉起。其余部署保持独立后台运行。

## 上游凭据

默认读取 `$CODEX_HOME/auth.json`，未设置 CODEX_HOME 时读取 `~/.codex/auth.json`。
也可显式指定文件：

```bash
.venv/bin/python -m corki_openai_gateway --serve --key-file ~/.corki/gateway/local.key \
  --auth-file /path/to/codex/auth.json
```

每次请求重新读取文件；遇到 401 时，如果文件 token 已更新，最多重试一次。
网关不写入 auth.json，也不自行刷新 OAuth token；登录和刷新仍由 Codex 管理。
账号 ID 改变时拒绝继续转发，需要重启网关确认新账号。

也可以由启动环境注入 token 和账号 ID，然后指定变量名（命令行不携带密钥值）：

```bash
# 启动前由你的环境或密钥管理器提供 CODEX_ACCESS_TOKEN、CODEX_ACCOUNT_ID。
.venv/bin/python -m corki_openai_gateway --serve --key-file ~/.corki/gateway/local.key \
  --access-token-env CODEX_ACCESS_TOKEN --account-id-env CODEX_ACCOUNT_ID
```

`--access-token-env` 与 `--auth-file` 互斥。指定环境变量后，缺失凭据会报错，
不会悄悄回退到本机账号。外部 shell 修改环境变量不会更新已运行进程的环境；
需要热更新时使用文件模式，或在 Python 中传入每次请求调用的凭据提供函数：

```python
from corki_openai_gateway.gateway import Credentials, Gateway


def credentials():
    # 从调用方自己的凭据管理器读取；不要把实际 token 写在源码中。
    token, account_id = credential_store.current()
    return Credentials(token, account_id)


gateway = Gateway(None, local_key, port=17001, credential_provider=credentials)
try:
    gateway.serve_forever()
finally:
    gateway.server_close()
```

回调需支持并发读取，凭据不可用时应抛出 `CredentialsUnavailable`，错误消息不得
包含凭据。这里只支持 Codex ChatGPT 登录 access token，不将普通 OpenAI API key
转换成 ChatGPT 登录权限。

## 流式与非流式响应

`/openai/chat/completions`、`/openai/v1/chat/completions`、`/openai/responses` 和 `/openai/v1/responses`
均接受布尔值 `stream`：`true` 返回 SSE；`false` 或省略时返回一次 JSON。
Chat Completions 返回 `chat.completion`（包含 message、tool_calls 和 usage）；
Responses 返回上游完成的 response 对象。

网关向 Codex 上游仍使用 `stream: true`。非流式请求等待 `response.completed`
后才发送成功响应；上游失败、未完整结束或格式异常返回 502，不返回半截成功结果。
非流式读取的 SSE 总量上限为 32 MiB，超限返回 502。客户端需要为完整生成预留等待时间。
流式调用行为保持不变。

```bash
curl http://127.0.0.1:17001/openai/v1/chat/completions \
  -H "Authorization: Bearer $(cat "$HOME/.corki/gateway/local.key")" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-6-astra","messages":[{"role":"user","content":"你好"}],"stream":false}'
```

## 客户端与迁移

客户端只需要配置网关地址（默认 `http://127.0.0.1:17001/openai`）和本地密钥，
无需安装 Corki 或共享 Python 环境。Responses 和 Chat Completions 的协议行为不变。
Corki 的 provider/model 配置无需改变；其他兼容客户端也可连接。

原 `python -m corki.codex_gateway` 和 `python -m adaptor.openai` 入口已移除。
使用 `.venv/bin/python -m corki_openai_gateway` 或安装后生成的
`.venv/bin/corki-openai-gateway`。旧 `--serve` 参数仍兼容，但现在始终只启动网关。
临时网关附带启动 Corki 的模式已移除，服务生命周期由 start/stop 脚本单独管理。
首次迁移先安装，再使用管理页或 start.sh；控制器会停用已识别的旧 launchd 实例。

## 验证与打包

在本目录执行：

```bash
uv sync --frozen --group dev
uv run --frozen pytest tests/test_gateway.py
uv build
```

`tests/test_corki_integration.py` 是可选的 Corki 客户端互操作测试；
没有安装 Corki 时自动跳过，不作为网关运行依赖。

## Windows 后台运行

使用已安装的 uv，在 PowerShell 中执行：

```powershell
.\adaptor\openai\bin\install.ps1
.\adaptor\openai\bin\start.ps1
.\adaptor\openai\bin\stop.ps1
```

若本机脚本执行策略阻止 `.ps1`，无需改变全局策略，可直接使用等价入口：

```powershell
uv sync --project .\adaptor\openai --frozen --no-dev
.\adaptor\openai\.venv\Scripts\python.exe -m corki_openai_gateway.control start --home "$HOME\.corki"
.\adaptor\openai\.venv\Scripts\python.exe -m corki_openai_gateway.control stop --home "$HOME\.corki"
```

启动前先在 Codex 登录；控制器会准备本地客户端密钥。支持 `CORKI_HOME`、`CODEX_HOME`、
`CORKI_GATEWAY_PORT`、`CORKI_GATEWAY_AUTH_FILE` 和 `CORKI_GATEWAY_PYTHON`。
凭据每次请求动态读取，不复制或写死 Token。新后台服务与 macOS 使用相同的控制协议，
通过私有控制凭据停止实例；旧 Windows 事件服务可在首次启用时迁移。
Windows 新增管理联动尚未在真实 Windows 机器上验收。

## 管理控制接口 v1

管理页面不导入网关 Python 模块，通过独立解释器调用以下进程接口：

```bash
python -m corki_openai_gateway.control status --home "$CORKI_HOME"
python -m corki_openai_gateway.control check --home "$CORKI_HOME"
python -m corki_openai_gateway.control start-auto --home "$CORKI_HOME"
# Standalone CLI users may still explicitly select a port:
python -m corki_openai_gateway.control start --home "$CORKI_HOME" --port 17001
python -m corki_openai_gateway.control stop --home "$CORKI_HOME"
```

stdout 只输出一个 JSON 对象，均包含 `protocol_version: 1`。成功退出码为 0；失败退出码为 1，
错误对象包含安全的 `error` 文本。status 返回 running、occupied、port；check 仅返回 ready，
不返回 Token 或账号。安装位置注册文件 `CORKI_HOME/gateway/installation.json` 只包含独立解释器路径。
网关不依赖管理页、不导入 Corki 主包，也不修改 Corki 的供应商配置；这些配置由管理页独立维护。

## Qoder 套餐适配

管理页的套餐转发支持 Qoder。开启前会由独立网关验证 Qoder 登录信息，成功后创建或启用
`adaptor-qoder`，地址为 `http://127.0.0.1:<实际端口>/qoder`，协议为 Chat Completions。
开启 Qoder 时自动补全以下静态模型，已有模型配置和用户自定义项保持不变：

| 模型 | 上游标识 |
| --- | --- |
| auto | auto |
| ultimate | ultimate |
| Kimi-K2.8-Preview | kmodel |
| GLM-5.3 | gmodel |
| DeepSeek-V4-Pro | dmodel |

Auto 和 Ultimate 由 Qoder 路由选择实际模型；其他名称映射到对应的上游标识，响应仍保留用户选择的名称。
目录来自本机 Qoder IDE 的普通 assistant 模型元数据，并经过当前 model-server 接口实际请求筛选。
IDE 目录中的 Fast、Efficient、Sonus、Qwen、更新的 Kimi 和 Flash 等选项，目前在该接口被拒绝、后端失败或返回不完整，暂不自动添加；不将它们静默替换成 Auto。
不混入 Quest / Experts 专用入口，不复制账号策略、登录信息或 GPT 模型列表。
这是静态快照；厂商升级、账户权限或服务可用性变化可能需要重新验证和更新。

支持 `/qoder/chat/completions` 和 `/qoder/v1/chat/completions`，包括 `stream=true/false`、
工具定义、并行工具调用参数拼接、工具结果回传及用量。Qoder 路由目前不提供 Responses API。
这里直接访问 Qoder 模型服务，不启动 Qoder Agent、不运行 Qoder 工具，也不读取项目上下文。

Qoder 当前入口的请求体上限实测约为 256 KiB，与模型 Token 窗口不同。
认证后的 `POST /qoder[/v1]/request-budget` 使用相同的 Chat Completions 请求体，
返回 `protocol_version=1`、`request_bytes`、`fixed_bytes`、`limit_bytes`；
它按网关转换后的完整 UTF-8 JSON 计数，不读取 Qoder 凭据、不调用上游模型。
`fixed_bytes` 包含系统／开发者提示词、工具定义和其他请求参数，不包含可压缩历史。
Corki 首次选择或切换供应商／模型后的首个请求使用该接口，超限时复用现有摘要压缩，
并检查摘要输入与压缩后的请求。后续普通轮次不增加字节预检，原有 Token 压缩保持不变。
固定部分超限时直接报错；摘要输入缩减只作用于请求副本，原始会话记录保留。
新版本 Corki 使用该功能前，需要同步更新并重启独立网关。

凭据按以下顺序读取：

1. `QODER_ACCESS_TOKEN` 环境变量。
2. `QODER_AUTH_FILE` 指定的 JSON 文件（包含 `token` 或 `access_token`，可带账号 `id`）。
3. `$QODER_HOME/credentials/auth.json`，默认根目录为 `~/.qoder`。仅有 `cleared` 标记不能代表有效登录。
4. macOS 上读取 Qoder IDE 的加密登录缓存，通过系统钥匙串正常授权解密。可用
   `QODER_IDE_STATE_DB` 指定 IDE 状态数据库。首次读取可能出现系统授权弹窗；拒绝或超时会提示失败。

不写入、迁移或刷新 Qoder 登录文件，不把密码、Token 或刷新令牌写入网关配置。
每次请求读取当前登录状态，加密内容未变化时复用内存缓存。切换账号需要重启网关。
登录过期时应由 Qoder IDE 完成刷新或重新登录；网关不会竞争使用其刷新令牌。
Windows/Linux 当前可通过显式 Token/JSON 文件接入，未实现这些平台 IDE 加密存储的自动读取。

控制命令支持 `--provider auto|openai|qoder`；启动默认 `auto` 优先使用 Codex 登录，缺失时尝试
Qoder，所以仅安装并登录 Qoder 也可以启动。单独验证登录可以执行：

```bash
.venv/bin/python -m corki_openai_gateway.control check --home ~/.corki --provider qoder
```

Qoder 上游默认 `https://api2-v2.qoder.sh/model/v1/chat/completions`。显式部署可通过
`QODER_UPSTREAM` 指定其他 HTTPS 地址；下游请求不能改变上游地址，TLS 校验保持开启。
该接口来自本机已安装 Qoder 的调用实现，属于厂商客户端协议，未来升级可能需要适配。

上游连接建立失败（连接错误或连接超时）返回 HTTP 502，并携带
`error.code: "upstream_connection_failed"`。调用方可以据此采用连接恢复策略；普通
上游 5xx 和无效响应不使用该错误码。证书校验失败返回 424，需要修复系统信任配置，
不视为临时断网。流式响应已经开始后仍以断流报告错误，不伪造成功结束。
