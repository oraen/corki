# Reference implementations and dependencies

The gateway is independently implemented. Architecture and lifecycle references:

- OpenClaw, MIT: https://github.com/openclaw/openclaw
- Tencent Weixin `@tencent-weixin/openclaw-weixin` 2.4.8, MIT:
  https://www.npmjs.com/package/@tencent-weixin/openclaw-weixin
  (iLink QR login, headers, cursor and context-token wire behavior.)
- Telegram Bot API: https://core.telegram.org/bots/api
- Lark official SDK: https://github.com/larksuite/node-sdk
- Baileys: https://github.com/WhiskeySockets/Baileys

Pinned optional runtime dependencies and their transitive dependencies are listed
in `connectors/packages/*/package-lock.json`; their individual licenses accompany npm packages.
No OpenClaw application bundle or account data is redistributed.
Channel identification artwork sources are documented in
`manager/static/channel-icons/SOURCES.md`.
