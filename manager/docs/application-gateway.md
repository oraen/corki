# 应用调用网关（协议 v1）

入口：主页 → 服务开发 → 应用管理，页面顶部的“应用网关”。

与套餐适配网关相同，网络层使用 aiohttp。应用网关独立监听，不转发模型协议；
它执行已经创建的 LLM / Agent 应用，复用应用配置、运行任务、历史和用量机制。

每个应用的“接入配置”可单独关闭 HTTP 调用，关闭时返回 403 和
`appid xxx 不支持http调用`。Kafka 的全局与独立接入不依赖 HTTP 网关，见
[应用 Kafka 接入](application-kafka.md)。

## 启停、端口和数据

- 自动模式优先使用 **17004**；只有端口被占用时才让系统分配可用端口。
  17001 为套餐适配网关，17002 为管理工作台，17003 为消息渠道网关。
- 可以指定 1–65535 的整数端口。指定端口不可用时明确失败，不擅自换端口。
- 标题右侧显示灰色端口号，悬停问号查看访问路径。修改运行中网关的端口会先停止旧监听，再启动新监听。
- 启停与保存异步进行；页面显示开启中 / 关闭中，可以继续使用其他功能。
- 关闭网关不会取消已经接收的应用任务；重开后仍能查询。退出管理服务会按原应用
  生命周期停止任务，重启后遗留任务按失败恢复，不自动重跑工具。
- 设置与随机生成的 32 位无前缀访问密钥保存在 `CORKI_HOME/applications/gateway.json`。
  未配置 CORKI_HOME 时为 `~/.corki/applications/gateway.json`。不写进 settings.json。
  正常服务重启会恢复上次启用状态及端口策略。
- 网关监听 IPv4 `0.0.0.0`，本机可用 127.0.0.1，局域网可使用本机 IP。
  本身提供 HTTP；跨不可信网络时应使用受信任的 HTTPS 反向代理。
- API Key 对该网关的所有应用具有调用、查询、停止和交互权限，不是多租户用户身份。
  管理服务仍仅接受本机同源请求，不会随此网关对外开放管理接口。

## 最小调用

在“接入设置”复制密钥并查看 curl 示例；将 `#{appid}` 替换为应用 ID。不要把密钥放进 URL。

```sh
export CORKI_APP_API_KEY='从接入设置复制的密钥'
curl 'http://127.0.0.1:17004/app?appid=100000' \
  -H "Authorization: Bearer $CORKI_APP_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"input":"你好","request_id":"demo-001"}'
```

`appid` 是应用卡片上从 100000 起递增的数字 ID。与内部会话/应用主键无关。

### POST /app?appid=100000

| JSON 字段 | 含义 |
| --- | --- |
| `input` | 必填，非空文本，沿用应用单条输入上限 256K 字符 |
| `session_id` | 可选；省略就新建会话，填写上次返回的值继续同一应用的会话 |
| `request_id` | 可选，1–64 个字母、数字、点、横线、下划线、冒号。生产调用建议主动提供 |
| `stream` | 布尔值，默认 false；true 返回 SSE |
| `wait` | 布尔值，默认 true；非流式时 false 立即返回任务，true 最多等待约 30 秒 |

也可用 `Idempotency-Key` 请求头传递 request_id；同时提供时必须相同。
同一应用重复提交相同请求 ID 和输入会返回原任务，不再次执行。不同输入使用相同
ID 会返回 409；不同应用的请求 ID 相互隔离。后续新问题必须换一个 request_id。
如果由服务器生成请求 ID，而最初响应完全丢失，客户端无法凭空恢复该 ID，因此不
声称这种情况具有跨故障“恰好执行一次”保证。

未完成或等待交互返回 **HTTP 202**；已完成 / 已取消返回 **200**；执行失败返回
**502** 并保留部分输出和真实失败状态。参数错误、认证错误、资源不存在、占用冲突
分别使用 400 / 401 / 404 / 409。输入格式不支持返回 415，请求体过大返回 413，
连接过多返回 429。运行失败不是 HTTP 连接关闭推断出来的成功。

```json
{
  "appid": 100000,
  "session_id": "服务端返回的会话ID",
  "run_id": "服务端返回的任务ID",
  "request_id": "demo-001",
  "status": "completed",
  "output": "你好！",
  "messages": [{"id": "任务ID:0", "content": "你好！"}],
  "interaction": null,
  "error": null,
  "provider": "实际供应商",
  "model": "实际模型",
  "version": 1,
  "created_at": "ISO-8601 UTC",
  "updated_at": "ISO-8601 UTC"
}
```

状态沿用真实应用运行状态：submitted、running、waiting、stopping、completed、
failed、cancelled。`output` 是最终回答或当前已生成内容；Agent 的多个可展示说明
保留在 `messages`，不会把工具输出误当作最后回答。响应不包含应用提示词、供应商
密钥或不可展示的内部推理。

### 查询和恢复

```sh
curl 'http://127.0.0.1:17004/app?appid=100000&run_id=任务ID' \
  -H "Authorization: Bearer $CORKI_APP_API_KEY"
# 初始响应丢失时，按客户端稳定 request_id 查询：
curl 'http://127.0.0.1:17004/app?appid=100000&request_id=demo-001' \
  -H "Authorization: Bearer $CORKI_APP_API_KEY"
```

查询成功为 HTTP 200，通过 JSON status 判断执行状态。同一网关的密钥不能绕过
应用与会话、任务之间的归属校验。应用关闭会阻止新请求，但已经接收的请求仍可
查询、取消或继续交互；相同请求 ID 的重试仍可获取原任务。

### SSE 流式返回

使用 `stream: true`，curl 可加 `-N`。Content-Type 为 text/event-stream。

- `run.created`：appid、session_id、run_id、request_id。
- `run.status`：任务状态及会话标识，正文暂时没有新增也不代表结束。
- `message.delta`：`id` 为消息标识，`content` 为增量，按 ID 追加。
- `message.replace`：同一消息需要修订时，content 为完整内容，按 ID 替换。
- `interaction.required`：需要补充信息或权限审批，参见下文。
- `run.completed` / `run.failed` / `run.cancelled`：明确终态，data 为最终完整快照。
- `error`：流开启后的资源异常，不能当作任务成功。
- 空闲时约每 10 秒发送一次 SSE 注释心跳 `: ping`。

连接断开不会取消任务。客户端先 GET 查询，或者使用相同 request_id 重新请求原任务。
SSE id 是连接内的事件序号，不是持久化事件日志，不支持 Last-Event-ID 逐事件重放。
新流从当前快照开始；收到 run.created 时应为该流重建消息状态，避免把重发的快照
增量重复拼接。消息 ID 使用任务 ID 和消息序号，不依赖运行时临时条目 ID 的切换。

### 取消

```sh
curl 'http://127.0.0.1:17004/app/cancel?appid=100000' \
  -H "Authorization: Bearer $CORKI_APP_API_KEY" \
  -H 'Content-Type: application/json' -d '{"run_id":"任务ID"}'
```

调用真实取消逻辑，保留已有内容。同一会话不提供静默任务队列；仍有活动轮次时
再次发送新问题返回 409。

### Agent 补充信息与审批

等待交互时，JSON 的 interaction 或 SSE interaction.required 提供 `id`、`kind`，
以及问题或审批信息。向 `/app/respond?appid=100000` 提交：

```json
{
  "run_id": "任务ID",
  "interaction_id": "interaction.id",
  "decision": "accept",
  "response": {"scope": ["当前"]}
}
```

- 问题：kind 为 question；response 是问题 ID → 字符串或字符串数组的简单映射，
  如上例。协议适配层转换成 Runtime 的标准回答结构，不把业务协议嵌入核心。
- 命令审批：decision 为 accept 或 decline，沿用 Corki 实际权限与审批。
- MCP：按请求结构提供 decision，以及需要的 response 对象。
- interaction_id 失效时拒绝操作，不错误批准下一次请求。

## 性能、边界与验证

连接由单个 aiohttp 事件循环处理，不为每条连接创建线程。同步配置、SQLite 读写及
提交适配放进受限线程池操作，网络等待和 SSE 不占用这些线程。最多同时处理 128
个经过认证的请求，数据操作并发上限 16；模型/Agent 沿用现有应用最多 4 个活动任务
的限制，不无限启动 Agent。状态采样只在调用或订阅期间发生，没有空闲定时扫描。

HTTP 断开、轮询和重放不重复计 Token；用量仍写入原独立台账，历史仍可在应用详情
的对话入口查看。没有新增第二套会话、上下文或用量事实库。

设计参考 Coze 官方 SDK 的 conversation_id 续聊、流式事件和任务状态分离：
https://github.com/coze-dev/coze-py 。本协议是本机精简应用 API，不宣称兼容 Coze API。

隔离测试：

```sh
.venv/bin/python -m pytest manager/tests/test_applications.py manager/tests/test_application_gateway.py
npm --prefix manager run build
```

测试使用临时 CORKI_HOME、假模型、真实 HTTP 连接和真实 Agent Runtime。macOS 实测；
Windows 使用 aiohttp 和现有跨平台文件锁、独占端口分支，未进行真实 Windows 验收。
