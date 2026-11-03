# 微信与飞书接入验收说明

## 产品边界

- 微信：独立的腾讯 iLink 私聊文本连接器；点击“扫码登录”才申请二维码。已保存的有效登录可以自动恢复。
- 飞书 / Lark：企业自建应用的 App ID / App Secret，通过官方 SDK 长连接接收事件，无需公网回调地址。不是微信扫码流程。
- 两者均使用 Corki 的用户会话隔离、访问审批、核心对话历史及会话重置。不展示钉钉专用 AI 卡片或主动发送入口。
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

界面使用与钉钉一致的表单布局。微信和飞书表单不显示额外配置指引，微信成功页仅保留状态和会话管理按钮。正常连接状态不重复显示说明文字，错误反馈仍保留。二维码刷新保持弹窗、二维码区域、按钮、图标中心及文字坐标不变，桌面和 390px 宽度均有浏览器几何检查。

这些使用隔离的账号与 SDK 测试替身，不会向真实用户发消息。真实微信的手机确认需要账号持有人完成；真实飞书收发需要有效企业应用凭据与权限，不能以测试替身通过来代替生产环境验收。
