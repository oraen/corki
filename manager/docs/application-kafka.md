# 应用 HTTP / Kafka 接入

应用详情 → **接入配置**。这里的保存立即生效，独立于提示词版本；回滚提示词不会回滚接入设置。

- **HTTP 调用**：新旧应用默认开启。关闭后，公共应用 HTTP 接口返回 HTTP 403，
  `error.message` 为 `appid 100000 不支持http调用`（ID 替换为实际值）。工作台内对话不受影响。
- **接入全局 Kafka**：默认不勾选。只处理全局输入 Topic 中属于该应用的消息；
  未勾选的应用消息被忽略，不启动模型、不生成回复。
- **独立 Kafka 接入**：一个应用可添加多个集群，每条接入可订阅多个输入 Topic，
  输出到一个 Topic。它们不依赖全局 Kafka 或 HTTP 网关开关。

应用列表顶部的 **Kafka 接入** 打开全局配置页。全局总开关控制全部全局连接，
每条连接仍有自己的开关。独立连接始终由对应应用管理。
关闭连接或全局开关暂停消费和发送，不取消已接收的任务；再次开启后继续处理。
删除连接则停止该连接已接收的任务，并清理其待处理消息和待发送结果，应用会话仍保留。

## 配置

- Kafka 地址：`broker.example.com:9092`，多个 bootstrap 地址用逗号分隔，支持 IPv6。
- 输入 Topic：输入名称后回车添加，也支持粘贴逗号或换行分隔的多个名称。
- 输出 Topic：不能与本条连接的输入 Topic 相同，避免直接循环调用。
- 消费组：留空自动生成独立消费组；新消费组从可用的最早消息开始。
  多个消费者使用相同消费组时会分摊消息，不是各消费一份。
- 认证与安全设置：支持 PLAINTEXT、SASL、TLS、SASL + TLS；
  SASL 支持 PLAIN / SCRAM-SHA-256 / SCRAM-SHA-512。
  证书路径位于运行管理服务的机器，TLS 保留证书验证。
- 用户名、密码只用于连接；密码保存在本机接入库，不返回普通列表或写入提示词版本。
  编辑时密码留空保留已有值。
- 同一接入存在未完成或未发送消息时，不能更换集群或消费组；先处理或明确删除该接入。
  修改输出 Topic 只影响之后接收的新消息，已有结果仍发往原 Topic。

Kafka 是 Broker 协议，不占用新的 Corki HTTP 监听端口。
运行依赖 `confluent-kafka>=2.16,<3`（基于 librdkafka，异步客户端），已包含于项目依赖。
管理服务只在有启用的连接时启动 Kafka 工作线程；页面只在显示活动接入时每 5 秒刷新状态。

## 文本调用协议

每条 Kafka record 的 value 是 UTF-8 JSON 对象。不要把供应商凭据放进消息。

全局输入必须带 `appid`，可用数字或纯数字字符串：

```json
{"appid":100000,"input":"你好","id":"order-42"}
```

独立接入固定属于一个应用，`appid` 可省略；提供时必须与所属应用一致：

```json
{"input":"你好","id":"order-42"}
```

最终输出（同一集群的配置输出 Topic）：

```json
{
  "appid":100000,
  "id":"order-42",
  "event":"result",
  "event_id":"74ed03a020e44cd48c9123487f977fad",
  "status":"completed",
  "session_id":"8851f7f9a3584996b41daf958f9561ed",
  "run_id":"0c7c1db9cfbb4e2d88173099ad60e5c93",
  "output":"你好！",
  "error":null
}
```

- `id` 仅用于请求关联，按原始 JSON 类型原样返回；`0`、`""`、`null` 均保留。
  没有该字段，输出也没有。最大 4 KiB；不凭空补上客户端 ID。
- `event_id` 是服务生成的输出事件标识，供下游对重复投递去重，与可选请求 `id` 不同。
- 每次调用默认新建会话；继续对话时传上次返回的 `session_id`，只能使用同一应用的会话。
- `action` 默认 `invoke`。每条消息最多 1 MiB；`input` 最长 256K 字符。
- 终态 `status` 使用现有应用运行语义，包括 `completed`、`failed`、`cancelled`。
  协议或路由不合法为 `rejected`，`error` 提供原因；不会假装执行成功。
- 不逐 token 向 Kafka 发送正文，避免大量小消息；发送最终结果和必要的交互事件。

同一会话的新请求等待前一轮释放；不同会话复用应用宿主的并发上限（4）。
多个 Topic / partition / 集群之间不保证全局到达顺序，需要顺序时由生产者使用同一
partition，并等待前一轮结果后再提交下一轮。

## Agent 交互与取消

Agent 等待批准或补充信息时，输出 `event: "interaction"`、`status: "waiting"`，
并携带 `run_id`、`session_id` 和 `interaction`（包含 `id`、类型、问题或审批内容）。
客户端必须处理这些事件，否则任务会等待用户；它们不是最终结果。

响应示例（问题回答内容按收到的交互填写）：

```json
{"appid":100000,"action":"respond","run_id":"0c7c1db9cfbb4e2d88173099ad60e5c93","interaction_id":"收到的交互ID","decision":"accept","response":{"scope":["当前"]},"id":"reply-42"}
```

审批可 `accept` 或 `decline`；补充问题须按要求回答，取消问题所在任务使用 `cancel`：

```json
{"appid":100000,"action":"cancel","run_id":"0c7c1db9cfbb4e2d88173099ad60e5c93","id":"cancel-42"}
```

控制请求返回 `event: "control"` 的受理状态，原调用仍收到自己的最终 `result`。
所有控制请求同样校验应用和运行归属。

## 可靠性与数据

数据位于 `CORKI_HOME/applications/access.db`，默认 `~/.corki/applications/access.db`：

- 接入开关、独立 revision、连接配置与凭据。
- 持久入站队列、出站结果及 Topic/partition 水位。与应用提示词、展示历史、模型上下文分开。
- 完整入站消息与水位在一个事务中写入后，才提交 Kafka offset。
- 同一接入 + 集群/消费组代次 + Topic + partition + offset 重放不会再次执行 Agent。
  请求 `id` 本身不是幂等键：生产者向不同 offset 重复发布同一个 `id`，属于两次调用。
- 提交结果未知时用稳定内部请求键对账，不自动重跑已接收的任务。
  管理进程崩溃后的已开始任务按现有宿主规则恢复为失败，不重放有副作用的工具。
- 先持久保存结果，再等待 Broker 确认，确认后才标记发送成功并清掉正文。
  网络故障总共尝试 3 次，耗尽标记回复失败并继续后续消息；页面可重试发送，**不重新执行 Agent**。
- 不能保证跨数据库与 Kafka 的无条件恰好一次：Broker 已接收但确认或本机标记丢失时可能重复发送；
  下游应按稳定 `event_id` 去重。
- 入站最多 1000 条待处理记录；未处理内容和未发送结果达到约 64 MiB 时背压暂停消费。
  已开始的最多 4 个任务仍可完成，所以结果可能临时超过该阈值。
  最多配置 32 条连接，客户端缓冲也设有上限。
- 已发送正文立即清理，只留轻量回执，回执保留 7 天；每小时清理，保留分区水位用于去重。
  新库启用增量空间回收。失败结果保留到重试成功或明确删除连接。
- 多个管理服务不能同时接管同一接入库，使用进程级文件锁保护；打开多个浏览器不会重复消费。

## 验证

默认单元测试无需 Broker：

```sh
uv run pytest manager/tests/test_application_access.py
```

真实 Kafka 集成测试使用单独下载的 Apache Kafka 3.9+ 和 Java 17，
只在临时目录启动两个 KRaft Broker，使用隔离的 Corki 数据与假模型，结束后关闭 Broker：

```sh
CORKI_TEST_KAFKA_HOME=/path/to/kafka uv run pytest manager/tests/test_application_kafka_live.py
```
