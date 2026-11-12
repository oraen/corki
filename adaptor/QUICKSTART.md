# 安装、启动与客户端接入指南

本项目在本机启动一个 HTTP 网关，读取已有的 Codex 或 Qoder 登录信息，向客户端提供 OpenAI 兼容接口。
Hermes Agent、OpenCode 和自己的程序都可以连接；无需安装或启动 Corki 主程序。

调用链：`客户端 → 本机网关 → Codex / Qoder 模型服务`。模型推理需要联网，并受上游账号权限和额度限制。

## 1. 准备环境与代码

需要 Git、[uv](https://docs.astral.sh/uv/getting-started/installation/) 和 Python 3.11 或以上版本。
uv 可管理 Python；如果没有合适的解释器，可以先执行 `uv python install 3.11`。

拉取完整仓库后，进入网关目录：

```bash
git clone https://github.com/oraen/corki.git
cd corki/adaptor
```

如果已经拉取代码，直接进入 `adaptor`。如果单独分发网关，请复制完整的 `adaptor` 目录，包含公共 `src`、`bin`、依赖和锁文件，不要只复制供应商子目录。
**下文所有安装和控制命令，均在包含 `pyproject.toml` 的网关目录执行。**

准备至少一种上游登录：

| 使用的上游 | 准备方式 |
| --- | --- |
| Codex / ChatGPT | 在网关所在机器使用 Codex 的 ChatGPT 登录方式登录，例如 `codex login`；默认读取 `~/.codex/auth.json`，设置 `CODEX_HOME` 时读取其下的 `auth.json` |
| Qoder | 在网关所在机器登录 Qoder；macOS 可读取 IDE 登录缓存，首次使用可能需要钥匙串授权；Windows / Linux 使用显式凭据文件或 Token，见第 7 节 |

Codex 路由需要 ChatGPT 登录凭据，普通 OpenAI 平台 API key 不能代替它。
网关不会替 Codex / Qoder 登录或刷新凭据；登录过期后应先在对应客户端恢复登录。

## 2. 安装并启动

### macOS / Linux

```bash
# 安装独立依赖环境，并登记解释器位置
bash ./bin/install.sh

# 后台启动，默认监听 127.0.0.1:17001
bash ./bin/start.sh

# 查询运行状态
.venv/bin/python -m corki_adaptor.control status --home "${CORKI_HOME:-$HOME/.corki}"
```

启动成功会输出 JSON，其中 `running` 为 `true`，`port` 为实际监听端口。
启动脚本默认优先检查 Codex 登录，缺失时尝试 Qoder；**登录检查通过只表示凭据可读取，实际可用性请用第 4 节的 HTTP 请求确认。**

停止服务：

```bash
bash ./bin/stop.sh
```

这是独立后台进程，关闭启动终端后可继续运行；普通首次安装不会自动配置开机启动。

### Windows PowerShell

```powershell
.\bin\install.ps1
.\bin\start.ps1
.\.venv\Scripts\python.exe -m corki_adaptor.control status --home "$HOME\.corki"

# 停止时执行
.\bin\stop.ps1
```

以上使用默认数据目录。自定义目录时，给所有控制命令传入同一个 `--home`，并设置脚本使用的 `CORKI_HOME`。
如果系统阻止执行 `.ps1`，可以直接执行等价命令：

```powershell
uv sync --frozen --no-dev
.\.venv\Scripts\python.exe -m corki_adaptor.control start --home "$HOME\.corki"
.\.venv\Scripts\python.exe -m corki_adaptor.control status --home "$HOME\.corki"
# 停止时执行：
# .\.venv\Scripts\python.exe -m corki_adaptor.control stop --home "$HOME\.corki"
```

Windows 后台服务相关功能尚需真实 Windows 机器验收。

### 端口冲突或需要指定上游

`start.sh` / `start.ps1` 默认使用 17001，端口被占用时会报错。可在 macOS / Linux 指定其他端口：

```bash
CORKI_GATEWAY_PORT=18001 bash ./bin/start.sh
```

也可让系统自动选择空闲端口，启动后以返回的 `port` 为准：

```bash
.venv/bin/python -m corki_adaptor.control start-auto --home "${CORKI_HOME:-$HOME/.corki}"
```

需要明确检查、使用 Codex 凭据启动时：

```bash
.venv/bin/python -m corki_adaptor.control check --home "${CORKI_HOME:-$HOME/.corki}" --provider openai
.venv/bin/python -m corki_adaptor.control start --home "${CORKI_HOME:-$HOME/.corki}" --provider openai --port 17001
```

`--provider` 选择启动时验证的凭据，服务启动后仍提供两种供应商路由；每条路由都需要对应的有效登录。
不要使用保留的 17002 端口。更改端口后，HTTP 示例和客户端配置中的端口也要一起修改。

## 3. 客户端需要填什么

| 项目 | Codex / ChatGPT | Qoder |
| --- | --- | --- |
| Base URL | `http://127.0.0.1:17001/openai/v1` | `http://127.0.0.1:17001/qoder/v1` |
| API key | 数据目录下 `gateway/local.key` 文件的内容 | 同左 |
| 协议 | Chat Completions 或 Responses | Chat Completions |
| 示例模型 | `gpt-6-astra`，需上游账号有权限 | `auto`、`ultimate`、`Kimi-K2.8-Preview`、`GLM-5.3`、`DeepSeek-V4-Pro` |

数据目录默认为 `~/.corki`，可通过启动环境的 `CORKI_HOME` 修改。
后台启动时会自动创建本地 key 文件。它用于客户端访问本网关，**客户端不需要填写 Codex / Qoder 的登录 Token**。

macOS / Linux 可以在当前终端准备后面示例使用的变量：

```bash
export CORKI_ADAPTOR_URL="http://127.0.0.1:17001"
export CORKI_ADAPTOR_KEY="$(cat "${CORKI_HOME:-$HOME/.corki}/gateway/local.key")"
```

Windows PowerShell 对应为：

```powershell
$env:CORKI_ADAPTOR_URL = 'http://127.0.0.1:17001'
$gatewayDataDir = if ($env:CORKI_HOME) { $env:CORKI_HOME } else { Join-Path $HOME '.corki' }
$env:CORKI_ADAPTOR_KEY = (Get-Content -Raw (Join-Path $gatewayDataDir 'gateway/local.key')).Trim()
```

注意：

- Base URL 需要包含 `/openai/v1` 或 `/qoder/v1`，不要只填主机和端口，也不要再加 `/chat/completions`。
- 网关没有实现 `GET /models`。客户端需要手动配置模型；自动发现返回 404 不代表转发接口不可用。
- 模型名由上游解释，网关不会自动给账号增加模型权限。示例模型不可用时，请换成自己的上游账号支持的模型。
- 服务只监听 `127.0.0.1`。以下配置适用于客户端和网关处于同一台机器、同一网络环境；容器、WSL 或远程服务器中的 `127.0.0.1` 可能指向不同环境。

## 4. HTTP 调用示例

以下 curl 示例使用 Bash / Zsh，并依赖第 3 节导出的变量。

### 健康检查

```bash
curl -sS "$CORKI_ADAPTOR_URL/__corki_gateway/health"
```

返回 `service: "corki-adaptor"` 表示网关进程可访问；该接口不验证上游登录或模型权限。

### Chat Completions：一次性返回 JSON

```bash
curl -sS "$CORKI_ADAPTOR_URL/openai/v1/chat/completions" \
  -H "Authorization: Bearer $CORKI_ADAPTOR_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "gpt-6-astra",
    "messages": [{"role": "user", "content": "只回复 OK"}],
    "reasoning_effort": "low",
    "stream": false
  }'
```

成功时返回 `object: "chat.completion"`，回答位于 `choices[0].message.content`，用量位于 `usage`。

### Chat Completions：SSE 流式返回

```bash
curl -N "$CORKI_ADAPTOR_URL/openai/v1/chat/completions" \
  -H "Authorization: Bearer $CORKI_ADAPTOR_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "gpt-6-astra",
    "messages": [{"role": "user", "content": "用三句话介绍 Python"}],
    "stream": true
  }'
```

返回多条 `data: {...}` 事件，正常完成以 `data: [DONE]` 结束；curl 的 `-N` 用于关闭输出缓冲。

### Responses API

```bash
curl -sS "$CORKI_ADAPTOR_URL/openai/v1/responses" \
  -H "Authorization: Bearer $CORKI_ADAPTOR_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "gpt-6-astra",
    "instructions": "请用中文简短回答。",
    "input": [{"role": "user", "content": "什么是 HTTP？"}],
    "reasoning": {"effort": "low"},
    "stream": false
  }'
```

回答文本在返回对象的 `output` 消息内容中。设置 `stream: true` 可接收 Responses SSE 事件。
该路由按请求转发上下文；多轮调用请携带历史消息，网关不保存会话。

### Qoder Chat Completions

```bash
curl -sS "$CORKI_ADAPTOR_URL/qoder/v1/chat/completions" \
  -H "Authorization: Bearer $CORKI_ADAPTOR_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "只回复 OK"}],
    "stream": false
  }'
```

同样支持 `stream: true`。模型调用直接访问 Qoder 模型服务，不会启动 Qoder Agent 或执行 Qoder 工具。

### Windows PowerShell 请求

准备好第 3 节的环境变量后执行：

```powershell
$requestBody = @{
    model = 'gpt-6-astra'
    messages = @(@{ role = 'user'; content = 'Reply only OK' })
    stream = $false
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post `
    -Uri "$env:CORKI_ADAPTOR_URL/openai/v1/chat/completions" `
    -Headers @{ Authorization = "Bearer $env:CORKI_ADAPTOR_KEY" } `
    -ContentType 'application/json; charset=utf-8' `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($requestBody))
```

## 5. Hermes Agent 配置

本节指 NousResearch 的 Hermes Agent。先安装 Hermes，并确认第 4 节的 HTTP 调用成功。
配置方式依据 [Hermes 官方自定义供应商文档](https://hermes-agent.nousresearch.com/docs/integrations/providers#named-custom-providers)。

编辑 `~/.hermes/config.yaml`，合并以下内容；保留自己已有的其他设置，不要重复定义顶层 `providers` 或 `model`：

```yaml
providers:
  adaptor-openai:
    api: http://127.0.0.1:17001/openai/v1
    key_env: CORKI_ADAPTOR_KEY
    transport: chat_completions
    discover_models: false
    default_model: gpt-6-astra
  adaptor-qoder:
    api: http://127.0.0.1:17001/qoder/v1
    key_env: CORKI_ADAPTOR_KEY
    transport: chat_completions
    discover_models: false
    default_model: auto

model:
  provider: custom:adaptor-openai
  default: gpt-6-astra
  context_length: 32768
```

`context_length: 32768` 是便于首次接入的保守客户端压缩阈值，按实际模型能力调整；它不是网关承诺的模型窗口。
在已经导出 `CORKI_ADAPTOR_KEY` 的终端启动：

```bash
hermes
```

改用 Qoder 时，把上面的 `model` 部分改为：

```yaml
model:
  provider: custom:adaptor-qoder
  default: auto
  context_length: 32768
```

环境变量只对当前终端及其子进程生效。若通过后台服务运行 Hermes，需要为该服务配置同名变量；
也可把供应商项的 `key_env` 换成 `api_key`，值填写 `gateway/local.key` 的内容。
这里选择自定义供应商；选择 Hermes 自带的 Codex 登录入口会走另一条接入路径。

旧版 Hermes 如果不识别 `providers` 格式，可运行 `hermes model`，选择 Custom endpoint，
手动填写第 3 节的 Base URL、本地 key 和模型名；API 模式选择 Chat Completions。
模型列表发现失败时手动填写模型，先用 curl 确认接口是否可调用。

## 6. OpenCode 配置

先执行 `opencode --version`，根据安装版本选择下面一种格式。
V1 和 V2 的字段不同，不要混用；示例均使用网关已支持的 Chat Completions 接口。

### OpenCode V1

在要使用 OpenCode 的项目根目录创建或合并 `opencode.json`：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "adaptor-openai/gpt-6-astra",
  "provider": {
    "adaptor-openai": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Adaptor Codex",
      "options": {
        "baseURL": "http://127.0.0.1:17001/openai/v1",
        "apiKey": "{env:CORKI_ADAPTOR_KEY}"
      },
      "models": {
        "gpt-6-astra": { "name": "Codex via Adaptor" }
      }
    },
    "adaptor-qoder": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Adaptor Qoder",
      "options": {
        "baseURL": "http://127.0.0.1:17001/qoder/v1",
        "apiKey": "{env:CORKI_ADAPTOR_KEY}"
      },
      "models": {
        "auto": { "name": "Qoder Auto" },
        "Kimi-K2.8-Preview": { "name": "Qoder Kimi" }
      }
    }
  }
}
```

格式依据 [OpenCode V1 自定义供应商文档](https://opencode.ai/docs/providers#custom-provider)。

### OpenCode V2

在项目根目录创建或合并 `opencode.jsonc`（以下内容本身也是合法 JSON）：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "providers": {
    "adaptor-openai": {
      "name": "Adaptor Codex",
      "env": ["CORKI_ADAPTOR_KEY"],
      "package": "@opencode/ai/providers/openai-compatible",
      "settings": {
        "baseURL": "http://127.0.0.1:17001/openai/v1"
      },
      "models": {
        "gpt-6-astra": { "name": "Codex via Adaptor" }
      }
    },
    "adaptor-qoder": {
      "name": "Adaptor Qoder",
      "env": ["CORKI_ADAPTOR_KEY"],
      "package": "@opencode/ai/providers/openai-compatible",
      "settings": {
        "baseURL": "http://127.0.0.1:17001/qoder/v1"
      },
      "models": {
        "auto": { "name": "Qoder Auto" },
        "Kimi-K2.8-Preview": { "name": "Qoder Kimi" }
      }
    }
  }
}
```

格式依据 [OpenCode V2 自定义供应商文档](https://opencode.ai/v2/docs/providers#custom)。

在已导出第 3 节环境变量的终端、进入目标项目后执行：

```bash
opencode
```

进入交互界面后运行 `/models`，选择 `adaptor-openai/gpt-6-astra` 或 `adaptor-qoder/auto`。
配置已通过环境变量提供本地 key，无需再填写上游登录 Token。
同一项目如果已有 OpenCode 配置，请合并到原文件，避免同时维护互相覆盖的配置。

## 7. Qoder 显式凭据与使用限制

Qoder 凭据按以下顺序查找：`QODER_ACCESS_TOKEN` → `QODER_AUTH_FILE` →
`$QODER_HOME/credentials/auth.json`（默认 `~/.qoder`）→ macOS IDE 加密登录缓存。

在 Windows / Linux，或需要指定凭据来源时，准备自己的 JSON 文件，例如：

```json
{
  "access_token": "替换为自己的 Qoder 登录 Token",
  "id": "替换为自己的账号标识"
}
```

macOS / Linux 示例，路径替换为实际文件路径：

```bash
export QODER_AUTH_FILE="/absolute/path/to/qoder-auth.json"
.venv/bin/python -m corki_adaptor.control check --home "${CORKI_HOME:-$HOME/.corki}" --provider qoder
.venv/bin/python -m corki_adaptor.control start --home "${CORKI_HOME:-$HOME/.corki}" --provider qoder --port 17001
```

PowerShell 对应使用 `$env:QODER_AUTH_FILE = 'C:\path\to\qoder-auth.json'`，
解释器替换成 `.\.venv\Scripts\python.exe`，`--home` 使用自己的数据目录。
设置环境变量后，需要重新启动已经运行的网关才能让进程获得新变量。

Qoder 支持工具调用。本适配器的完整请求体预检预算为 **8 MiB**，工具定义、系统提示、图片和历史消息都会占用。
这是本地压缩触发预算，不是上游容量保证；实际上游限制仍取决于模型、账号及服务端。
Hermes / OpenCode 不会自动获得 Corki 专用的字节预算压缩能力。长对话需要在客户端压缩历史、减少工具或新建会话；
即使设置了较小的 Token 窗口，也不保证请求体一定不超限。
模型可用性依赖厂商服务及账号；Qoder 客户端协议更新后可能需要同步更新适配器。

## 8. 常见问题与更新

| 现象 | 处理方法 |
| --- | --- |
| 提示没有检测到登录信息 | 先登录 Codex / Qoder，再执行 `control check --provider openai` 或 `--provider qoder`；控制命令还需传入 `--home` |
| 连接被拒绝 | 查询 `control status`，核对 `running`、实际端口和客户端所处的网络环境 |
| HTTP 401 | 检查客户端 key 是否来自当前数据目录的 `gateway/local.key`；错误若指向上游认证，则重新登录对应上游 |
| HTTP 404 | 检查是否带有 `/openai/v1` 或 `/qoder/v1`；`/models` 不在实现范围内 |
| HTTP 400 / 模型不支持 | 检查模型名称、请求格式和账号权限；Qoder 可先试 `auto` |
| HTTP 413 / Qoder 请求体过大 | 减少历史消息、工具定义或系统提示，重新发起请求 |
| HTTP 424 | 上游 TLS 证书校验失败，检查本机系统证书信任和代理配置 |
| HTTP 502 或流式响应中断 | 检查上游网络、模型可用性和响应完整性；连接建立失败时错误码为 `upstream_connection_failed` |
| 切换账号后提示重启 | 停止网关，再启动，以确认新的上游账号 |

默认服务日志位置：`~/.corki/gateway/service.log`；自定义数据目录时位于 `$CORKI_HOME/gateway/service.log`。
前台排查时，先停止后台实例，再执行：

```bash
.venv/bin/python -m corki_adaptor \
  --port 17001 \
  --key-file "${CORKI_HOME:-$HOME/.corki}/gateway/local.key"
```

该命令使用已经生成的本地 key，默认验证 Codex 登录；仅有 Qoder 时额外加 `--startup-provider qoder`。
前台运行用 `Ctrl+C` 结束。

更新代码后，在网关目录重新安装并重启：

```bash
git pull
bash ./bin/install.sh
bash ./bin/stop.sh
bash ./bin/start.sh
```

Windows 使用对应的 `install.ps1`、`stop.ps1` 和 `start.ps1`。
详细的参数、环境变量与服务控制协议见 [README](README.md)。

本文客户端配置字段于 2026-11-02 按上述官方文档核对；HTTP 示例和客户端配置不代表所有上游账号均已实机验收。
