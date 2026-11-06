# 微信与飞书接入验收说明

## 产品边界

- 微信：独立的腾讯 iLink 私聊文本连接器；点击“扫码登录”才申请二维码。已保存的有效登录可以自动恢复。
- 飞书 / Lark：企业自建应用的 App ID / App Secret，通过官方 SDK 长连接接收事件，无需公网回调地址。不是微信扫码流程。
- 微信扫码后直接可用，由接入开关控制，聊天页提供人工文字发送；飞书保留访问审批流程。两者均支持会话隔离、历史记录和重置，不展示钉钉专用 AI 卡片。
- 微信手机授权页来自微信服务，可能显示 OpenClaw；本地连接器请求已明确标识 Corki，没有加载 OpenClaw。没有发现可公开使用的授权页名称设置，独立名称需要微信侧应用标识支持。不能仅替换本地文案便声称解决。

## 操作语义

- **刷新二维码**：取消当前扫码尝试，请求新的二维码。已取消尝试的返回结果不能写入登录信息。
- **断开连接**：关闭此接入并停止当前任务；保留配置、登录信息和历史。再次连接时优先使用有效登录信息。
- **重新连接**：只重启微信 / 飞书当前接入的连接器，不重置会话、不清除凭据、不重启其他渠道。
- **重置当前 / 全部会话**：沿用现有管理功能，停止相应任务并为后续消息建立新上下文，底层旧记录保留。当前接入的会话列表展示当前绑定，不提供重置前记录的归档浏览入口。
- 会话为空时引导用户先发送私聊文字；开启会话校验时需要先审批。微信和飞书的非文字输入使用配置的兜底回复。
- 接入开关和重新连接采用独立的处理中状态；等待期间可继续查看页面、切换接入和提交其他接入的开关请求。确认断开后弹窗立即关闭，状态轮询继续。网关内部依次执行配置变更，避免并发修改冲突。处理中的同一接入不允许重复切换、删除或重置；全局网关启停等待当前接入操作结束。

## 飞书配置核对

1. 创建企业自建应用、启用机器人，选择飞书或 Lark 所属区域。
2. 申请 `im:message.p2p_msg:readonly` 和 `im:message:send_as_bot`。
3. 事件订阅选择长连接，添加 `im.message.receive_v1`。
4. 发布应用并设置用户可用范围，再向机器人私聊。
5. SDK `onReady` / `onReconnected` 才表示在线；连接启动返回不等于成功。启动超过 45 秒会提供配置检查提示并交由监督器恢复。

参考：[飞书官方 SDK](https://github.com/larksuite/node-sdk/blob/main/README.zh.md)、腾讯 `@tencent-weixin/openclaw-weixin@2.4.8` 的接口定义和登录实现。

## 验证

```powershell
node --test gateway/tests/connectors.mjs gateway/tests/wechat-login.mjs gateway/tests/dingtalk-cards.mjs gateway/tests/dingtalk-media.mjs gateway/tests/dingtalk-delivery.mjs gateway/tests/dingtalk-recall.mjs
.\.venv\Scripts\python.exe -X utf8 -m pytest gateway/tests/test_wechat_login.py gateway/tests/test_final_history.py gateway/tests/test_user_sessions.py gateway/tests/test_reset_current.py -q
node manager/tests/wechat_login.mjs
node manager/tests/channel_experience.mjs
node manager/tests/channel_operations.mjs
.\.venv\Scripts\python.exe -X utf8 -m pytest gateway/tests/test_connection_operations.py -q
```

浏览器场景覆盖加载、二维码、扫码待确认、验证码输入及校验、过期、成功跳转、刷新、重新连接、飞书接入表单、390px 布局，以及钉钉界面隔离。截图默认保存在系统临时目录 `corki-channel-experience`，可通过 `CORKI_TEST_ARTIFACT_DIR` 指定。

界面使用与钉钉一致的表单布局。微信和飞书表单不显示额外配置指引，微信成功页仅保留状态和会话历史按钮。正常连接状态不重复显示说明文字，错误反馈仍保留。二维码刷新保持弹窗、二维码区域、按钮、图标中心及文字坐标不变，桌面和 390px 宽度均有浏览器几何检查。

这些使用隔离的账号与 SDK 测试替身，不会向真实用户发消息。真实微信的手机确认需要账号持有人完成；真实飞书收发需要有效企业应用凭据与权限，不能以测试替身通过来代替生产环境验收。

微信接入以扫码登录和接入开关控制可用性，不再要求会话审批。已有配置中的会话校验设置在读取时按关闭处理，保留历史记录；保存时写入关闭状态。微信的会话历史直接展示聊天记录，提供重置会话操作。钉钉、飞书保留原有会话管理与校验流程。

## 微信后台人工发送

- 聊天页沿用钉钉输入框，Enter 发送，Shift+Enter 换行，等待期间保留草稿并防止重复提交。
- 优先使用同一接入、同一微信绑定和同一联系人的最新 context_token；没有凭据时可按 ID 发起发送尝试。腾讯客户端允许省略该字段，但官方文档没有保证服务端一定接受或投递无凭据请求。
- 只有明确的接口成功回执才显示已发送；拒绝、未确认和超时均保留失败记录，不自动重发，也不等同于对方已读。
- 微信传输记录独立保存在 gateway.db，人工消息不追加到 Agent 模型上下文。分页延续到升级前的核心历史，旧记录仍可查看。
- 重绑后不会复用旧绑定的收件人凭据；关闭接入即禁用发送。凭据不返回管理页面。

参考：[腾讯官方协议说明](https://github.com/Tencent/openclaw-weixin/blob/main/docs/protocol_zh_CN.md)、[腾讯官方发送实现](https://github.com/Tencent/openclaw-weixin/blob/main/src/messaging/send.ts)。

## 微信 Agent 运行权限

微信 Agent 的文件、网络、命令执行和 MCP 审批权限沿用会话加载的配置，不再对微信单独强制完整权限或免审批。

## 微信图片收发

- 支持私聊图片和图文输入，保持文字与图片顺序；Agent 使用接入所选模型的图片能力。
- 会话历史显示缩略图，可点击查看原图。后台可选择、粘贴或拖入图片，支持纯图片及附带文字发送，上传和发送期间防止重复提交，失败保留草稿。
- 每条最多 8 张；后台上传单张最多 8 MB，接收与 Agent 本地图片最多 16 MB。支持 PNG、JPEG、GIF、WebP，发送前检查图片内容。
- Agent 可通过 `send_wechat_image` 发送生成的图片或截图，工具绑定当前接入和联系人，文件读取遵循会话权限。图片发送失败不会误报成功，也不会自动重发。
- 图片按接入缓存，微信凭据、CDN 临时地址和 AES 密钥不返回管理页面。传输使用腾讯 CDN 的 AES-128-ECB 图片协议；只有明确发送回执才记录成功。

验证：`node --test gateway/tests/wechat-media.mjs`、`pytest gateway/tests/test_wechat_images.py` 和 `node manager/tests/channel_experience.mjs`。使用隔离的协议和模型替身验证，不会向真实联系人发消息。

## 微信附件及连续消息

- 后台附件入口支持图片、普通文件、MP4 视频。文件卡片显示名称与大小，点击下载；视频可打开播放窗口和下载，支持进度跳转。
- 普通文件和视频缓存独立于图片，按接入隔离；接收与 Agent 发送单个最多 32 MB，后台上传最多 8 MB，每次合计最多 8 个附件。普通文件强制以下载方式返回，避免在管理页执行其内容。
- Agent 收到附件路径和原文件名，通过会话允许的工具分析；发送分别使用 `send_wechat_file`、`send_wechat_video`，文件读取沿用会话权限。
- 为处理微信图片与文字分开到达，在短窗口中将相邻附件和单个问题配对；配对完成即开始处理，后续问题进入独立队列。原始聊天记录保留，每条入站消息均保留去重与结束状态。
- Agent 运行期间尝试显示微信原生输入状态。票据缺失或状态接口失败不会阻断回复，结束和取消时清除状态。

自测覆盖文件/视频加密往返、真实 MP4 页面播放、上传与下载、跨接入隔离、权限拒绝、发送去重、连续 A/B 问题与各自图片配对、停止期间清理待处理附件。
