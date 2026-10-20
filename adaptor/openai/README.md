# 本地 OpenAI 协议适配网关

`src/corki_openai_gateway/gateway.py` 提供只监听 `127.0.0.1` 的 HTTP 服务；`src/corki_openai_gateway/chat.py` 转换
Chat Completions 请求和 SSE 响应。支持 `/responses`、`/chat/completions`
及它们的 `/v1` 前缀。模型和 reasoning effort 由请求指定。

这是独立 Python 项目，运行依赖只有 httpx 和 truststore；不依赖或导入 Corki。
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
`api_key` 一致；它不是上游登录 token。仓库不保存实际密钥。

## macOS 后台服务

在项目根目录执行，或使用脚本的绝对路径：

```bash
./adaptor/openai/bin/start.sh
./adaptor/openai/bin/stop.sh
```

脚本管理用户级 launchd 服务 `local.corki.codex-gateway`。重复启动不会重启
正在运行的服务；停止会卸载服务，避免 KeepAlive 立即拉起。配置与密钥保留。
启动后服务会在用户登录时自动运行；停止后如需恢复，重新执行 start.sh。

已有部署复用 `~/Library/LaunchAgents/local.corki.codex-gateway.plist`。
start.sh 使用本目录自己的 `.venv/bin/python`，自动迁移旧服务的 Python 和模块路径，
保留已有端口、凭据来源与日志设置。没有 plist 时创建配置，默认端口 17001，
默认本地密钥文件 `~/.corki/gateway/local.key`，默认读取 Codex auth.json。
需先运行 install.sh 并准备与客户端一致的本地密钥文件。
首次生成配置可用 `CORKI_GATEWAY_PYTHON`、`CORKI_GATEWAY_PORT`、
`CORKI_GATEWAY_KEY_FILE`、`CORKI_GATEWAY_AUTH_FILE` 指定路径或端口；
修改已有部署时编辑 plist 并依次 stop/start。脚本不保存 token 值。

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

`/chat/completions`、`/v1/chat/completions`、`/responses` 和 `/v1/responses`
均接受布尔值 `stream`：`true` 返回 SSE；`false` 或省略时返回一次 JSON。
Chat Completions 返回 `chat.completion`（包含 message、tool_calls 和 usage）；
Responses 返回上游完成的 response 对象。

网关向 Codex 上游仍使用 `stream: true`。非流式请求等待 `response.completed`
后才发送成功响应；上游失败、未完整结束或格式异常返回 502，不返回半截成功结果。
非流式读取的 SSE 总量上限为 32 MiB，超限返回 502。客户端需要为完整生成预留等待时间。
流式调用行为保持不变。

```bash
curl http://127.0.0.1:17001/v1/chat/completions \
  -H "Authorization: Bearer $(cat "$HOME/.corki/gateway/local.key")" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-6-astra","messages":[{"role":"user","content":"你好"}],"stream":false}'
```

## 客户端与迁移

客户端只需要配置网关地址（默认 `http://127.0.0.1:17001`）和本地密钥，
无需安装 Corki 或共享 Python 环境。Responses 和 Chat Completions 的协议行为不变。
Corki 的 provider/model 配置无需改变；其他兼容客户端也可连接。

原 `python -m corki.codex_gateway` 和 `python -m adaptor.openai` 入口已移除。
使用 `.venv/bin/python -m corki_openai_gateway` 或安装后生成的
`.venv/bin/corki-openai-gateway`。旧 `--serve` 参数仍兼容，但现在始终只启动网关。
临时网关附带启动 Corki 的模式已移除，服务生命周期由 start/stop 脚本单独管理。
首次迁移先安装，再 stop/start；start.sh 会更新已有 launchd 服务的执行环境。

## 验证与打包

在本目录执行：

```bash
uv sync --frozen --group dev
uv run --frozen pytest tests/test_gateway.py
uv build
```

`tests/test_corki_integration.py` 是可选的 Corki 客户端互操作测试；
没有安装 Corki 时自动跳过，不作为网关运行依赖。
