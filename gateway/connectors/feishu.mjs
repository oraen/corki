import { chunks, status, emit, unsupportedContent } from "./protocol.mjs";
export async function create({ config, require, incoming, fatal = () => {} }) {
  let startupTimer, closed = false;
  const ready = () => {
    clearTimeout(startupTimer);
    if (!closed) status("online");
  };
  const sdk = require("@larksuiteoapi/node-sdk");
  const options = {
    appId: config.app_id,
    appSecret: config.app_secret,
    domain: config.region === "Lark" ? sdk.Domain.Lark : sdk.Domain.Feishu,
    loggerLevel: sdk.LoggerLevel.error,
  };
  if (!/^cli_[0-9a-fA-F]{16}$/.test(config.app_id))
    throw new Error("invalid app id");
  const client = new sdk.Client(options),
    ws = new sdk.WSClient({
      ...options,
      onReady: ready,
      onReconnected: ready,
      onReconnecting: () => { if (!closed) status("connecting"); },
      onError: (error) => {
        if (closed) return;
        status("error", { code: "feishu_connection" });
        // The SDK has stopped retrying. Let the gateway supervisor restart it.
        if (/^WebSocket reconnect exhausted after \d+ attempts?/.test(error?.message || "") ||
            error?.message === "WebSocket connect failed and autoReconnect is disabled") fatal();
      },
    });
  return {
    async start() {
      startupTimer = setTimeout(() => {
        if (closed) return;
        status("error", { code: "feishu_setup" });
        fatal();
      }, 45000);
      startupTimer.unref?.();
      const dispatcher = new sdk.EventDispatcher({}).register({
        "im.message.receive_v1": async (data) => {
          const message = data.message,
            sender = data.sender;
          if (
            message?.chat_type !== "p2p" ||
            sender?.sender_type !== "user" ||
            !sender?.sender_id?.open_id || !message.message_id || !message.chat_id
          )
            return;
          let content = null;
          if (message.message_type === 'text') {
            try { content = JSON.parse(message.content || "{}"); }
            catch { return; }
            if (!content || typeof content !== 'object') return;
          }
          if (content && (typeof content.text !== "string" || !content.text.trim())) return;
          await incoming({
            id: message.message_id,
            sender: sender.sender_id.open_id,
            conversation: message.chat_id,
            name: sender.sender_id.open_id,
            ...(content?{text:content.text}:unsupportedContent(message.message_type || 'unknown')),
            kind: "direct",
          });
        },
      });
      emit({ method: "account", params: { id: config.app_id } });
      await ws.start({ eventDispatcher: dispatcher });
      // start() returns before connection: only SDK lifecycle callbacks mark online.
    },
    async send({ message, text }) {
      for (const part of chunks(text)) {
        const result = await client.im.message.create({
          params: { receive_id_type: "chat_id" },
          data: {
            receive_id: message.conversation,
            msg_type: "text",
            content: JSON.stringify({ text: part }),
          },
        });
        if (result.code !== 0) throw new Error("feishu delivery failed");
      }
    },
    async close() {
      closed = true;
      clearTimeout(startupTimer);
      ws.close({ force: true });
    },
  };
}
