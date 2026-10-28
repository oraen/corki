# Corki Gateway Connector v1

连接器是用户明确安装的本地代码，拥有当前系统用户权限；不是沙箱，也不是 MCP。
OpenClaw 插件不能直接导入：它依赖另一套宿主 SDK，需要实现本协议适配。

## ZIP 包

根目录（或唯一顶层目录）包含 `connector.json` 和入口文件，不运行安装脚本：

```json
{
  "protocol": 1,
  "id": "my-channel",
  "name": "我的渠道",
  "description": "内部消息服务 · 私聊文本",
  "runtime": "node",
  "entry": "main.mjs",
  "fields": [
    {"key": "token", "label": "访问密钥", "secret": true, "required": true}
  ]
}
```

`runtime` 为 `node` 或 `python`。Python 使用 Corki 所在解释器。入口为包内相对路径。
`fields` 支持 `key`、`label`、`secret`、`required` 和文本数组 `options`，每个配置值均为字符串。
不允许保留内置 ID、同名覆盖、链接、路径穿越、重复路径和 Windows 保留名称。
ZIP 上限 16 MB，展开后上限 64 MB / 2000 文件。依赖须由插件作者自行提供，宿主不执行插件安装命令。

## 传输

stdin/stdout 双向 UTF-8 JSON Lines，一行一个 JSON，上限 1 MB。stderr 只用于日志。
请求 ID 是不透明字符串，响应必须原样返回。宿主请求：

```json
{"id":"host-1","method":"initialize","params":{"protocol":1,"config":{"token":"..."},"state_directory":"...","dependencies":"...","cursor":""}}
```

初始化必须在 20 秒内响应 `{"id":"host-1","result":{"protocol":1}}`，随后启动监听。
`state_directory` 是该连接器可持久化数据的独立目录，`cursor` 是上次确认的轮询游标。
`dependencies` 仅供内置连接器查找 SDK，自定义连接器不应依赖其未公开实现。
初始化完成不等于连接成功，真正连接成功再发送：

```json
{"method":"account","params":{"id":"stable-platform-account-id"}}
{"method":"status","params":{"state":"online"}}
```

账号标识必须稳定且不是密钥。账号变更会撤销旧授权和路由。
状态为 `connecting / online / qr / verification / error`。
二维码由连接器转为 PNG data URL，放在 `qr` 字段。错误 `code` 支持
`credentials / network / verify / expired / dependencies / protocol`，不传原始含密钥错误。

平台消息转为：

```json
{"id":"connector-1","method":"inbound","params":{"id":"platform-message-id","sender":"user-id","conversation":"chat-id","kind":"direct","name":"显示名称","text":"你好"}}
```

外层 ID 用于协议请求关联，内层 ID 用于持久化去重，须来自平台稳定消息标识。
`sender` 是发送者，不能由消息正文伪造；不要转发机器人自己的消息、历史同步消息或群聊。
各身份字段最长 300 字符，文本最长 100000 字符。
可携带回复所需的平台专有字段（例如微信 `context_token`）；不要把长期访问密钥放进消息。

宿主响应：

```json
{"id":"connector-1","result":{"accepted":true,"thread":"..."}}
{"id":"connector-1","result":{"accepted":true,"duplicate":true}}
{"id":"connector-1","result":{"accepted":true,"thread":"conversation-id"}}
{"id":"connector-1","error":"网关繁忙，请稍后重试"}
```

收到持久化接收确认/拒绝后再推进平台游标。传输失败/繁忙可用**同一消息 ID**重试接收。
`accepted:true` 只表示接收，不表示 Agent 已完成，更不表示工具执行成功。
接入启用时直接接收私聊文本，不进行首次联系人审批。接入关闭时拒绝新消息。
游标变化通知：`{"method":"cursor","params":{"value":"opaque-cursor"}}`。

回复由宿主发给连接器：

```json
{"id":"host-2","method":"send","params":{"message":{"id":"platform-message-id","sender":"user-id","conversation":"chat-id","kind":"direct","text":"你好"},"text":"你好，有什么需要帮助的？"}}
```

连接器应处理平台长度限制，成功投递后响应 `{"id":"host-2","result":{"sent":true}}`。
失败响应 `error`；结果不明不得声称成功。宿主不自动重试 send，避免重复投递。
普通 send 等待最多 120 秒。输出原文是纯文本，首版不要求流式展示。

`verify` 参数 `{code:"123456"}` 用于登录验证码。
`shutdown` 后停止监听、关闭连接并释放子资源；stdin EOF 也必须退出，不得留下独立后台进程。
网关先请求关闭，再终止自己创建的进程组/Windows Job，不按进程名全局结束进程。

详见 `examples/` 的最小连接器，它用于协议学习，不连接真实第三方渠道。
