# 本地 Webhook 示例

将 `local-webhook/` 内的 `connector.json` 和 `main.py` 压缩成一个 ZIP，通过管理页安装。
这是使用 Python 标准库的回环 HTTP 连接器示例，不需要 npm 依赖。
安装连接器后，在「对话接入 → 新增接入」选择「本地 Webhook 示例」，自行设置访问密钥和监听端口，再开启网关和该账号接入。
可将监听端口设为一个未占用端口，例如 18080；与网关的管理端口 17003 无关。

实际监听端口写在 `$CORKI_HOME/gateway/accounts/<接入ID>/listen.json`。
使用相同密钥作为 `Authorization: Bearer ...`，向本地端口 POST JSON：

```json
{"id":"unique-platform-message-id","sender":"example-user","conversation":"example-dm","kind":"direct","text":"你好"}
```

接入开启后即可发送；相同 ID 被接受后不会再次执行。
GET 同一地址（也需要认证头）可读取接收回执及最多 50 条回复，示例回复队列仅在内存中保存。
它只监听 `127.0.0.1`，不为公网 Webhook 配置 HTTPS、签名校验或反向代理。
生产连接器需要自己实现对应平台的身份验证和可靠投递，不能直接信任公网上传的 sender 字段。


## 本机测试
将下方端口替换为你配置的监听端口，访问密钥替换为新增接入时填写的值。
在 macOS/Linux 终端使用 curl；Windows PowerShell 使用 curl.exe，或通过任意 HTTP 客户端发送同等请求。

```sh
curl -X POST http://127.0.0.1:18080/ -H 'Content-Type: application/json' -H 'Authorization: Bearer <你设置的访问密钥>' --data '{"id":"sample-1","sender":"local-user","conversation":"local-chat","kind":"direct","text":"你好"}'
curl http://127.0.0.1:18080/ -H 'Authorization: Bearer <你设置的访问密钥>'
```

接入开启后直接处理首次消息。HTTP 202 仅表示示例收到请求，不代表 Agent 成功。
GET 返回列表包含接收回执和模型回复；需要已经配置有效的 Corki 默认模型。
每个新问题换一个 id；已接收的相同 id 会被去重。
不在 ZIP 中写入真实密钥：密钥由管理页面填写。
