import { chunks, status, emit, unsupportedContent } from "./protocol.mjs";
export async function create({ config, require, incoming, fatal = () => {} }) {
  let startupTimer, closed = false;
  const ready = () => {
    clearTimeout(startupTimer);
    lastConnectionDetail = undefined;
    if (!closed) status("online");
  };
  const sdk = require("@larksuiteoapi/node-sdk");
  let lastConnectionDetail;
  const redact = value => {
    let text = typeof value === "string" ? value : JSON.stringify(value ?? "");
    for (const secret of [config.app_id, config.app_secret].filter(Boolean))
      text = text.split(secret).join("[已隐藏]");
    return text.replace(/("(?:app_?secret|access_token|tenant_access_token|client_assertion|ticket|URL)"\s*:\s*")[^"]*/gi,
      '$1[已隐藏]').replace(/([?&][\w-]+=)[^&\s"']*/g, '$1[已隐藏]').slice(0, 8192);
  };
  function report(error, request, stage) {
    if (closed) return;
    let url;
    if (request?.url) {
      try {
        const parsed = new URL(request.url);
        parsed.username = parsed.password = "";
        for (const key of [...parsed.searchParams.keys()]) parsed.searchParams.set(key, "[已隐藏]");
        parsed.hash = "";
        url = parsed.toString();
      } catch { /* Malformed SDK addresses must not expose request credentials. */ }
    }
    const detail = {
      ...(url ? {url, method:String(request.method || "POST").toUpperCase()} : {}),
      stage: stage || (request ? "获取飞书连接地址" : "建立飞书 WebSocket"),
      error:redact(error?.code || error?.message || "连接失败"),
      ...(error?.response ? {status:error.response.status, response:redact(error.response.data),
        content_type:String(error.response.headers?.["content-type"] || "")} : {}),
    };
    lastConnectionDetail = detail;
    status("error", {code:"feishu_connection", detail});
  }
  // WSClient catches discovery failures internally. Use its public HTTP injection
  // point to capture the response before it is reduced to a retryable failure.
  const httpInstance = sdk.defaultHttpInstance && {
    async request(request) {
      try {
        const result = await sdk.defaultHttpInstance.request(request);
        if (!result || typeof result !== "object" || typeof result.code !== "number" || result.code !== 0)
          report({message:"飞书连接地址接口返回异常", response:{data:result}}, request);
        return result;
      } catch (error) { report(error, request); throw error; }
    },
  };
  const options = {
    appId: config.app_id,
    appSecret: config.app_secret,
    domain: config.region === "Lark" ? sdk.Domain.Lark : sdk.Domain.Feishu,
    loggerLevel: sdk.LoggerLevel.error,
  };
  if (!/^cli_[0-9a-fA-F]{16}$/.test(config.app_id)) {
    report({message:"App ID 格式无效，请填写飞书开放平台应用的 App ID"}, undefined, "校验飞书接入配置");
    throw new Error("invalid app id");
  }
  const client = new sdk.Client(options),
    ws = new sdk.WSClient({
      ...options,
      ...(httpInstance ? {httpInstance} : {}),
      onReady: ready,
      onReconnected: ready,
      onReconnecting: () => { if (!closed) status("connecting"); },
      onError: (error) => {
        if (closed) return;
        if (!lastConnectionDetail) report(error);
        // The SDK has stopped retrying. Let the gateway supervisor restart it.
        if (/^WebSocket reconnect exhausted after \d+ attempts?/.test(error?.message || "") ||
            error?.message === "WebSocket connect failed and autoReconnect is disabled") fatal();
      },
    });
  return {
    async start() {
      startupTimer = setTimeout(() => {
        if (closed) return;
        status("error", { code: "feishu_setup", ...(lastConnectionDetail ? {detail:lastConnectionDetail} : {}) });
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
      try { await ws.start({ eventDispatcher: dispatcher }); }
      catch (error) { if (!lastConnectionDetail) report(error); fatal(); }
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
