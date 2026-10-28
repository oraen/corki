import { chunks, emit, status } from "./protocol.mjs";

// Session callbacks are bearer credentials. Never accept arbitrary reply destinations.
export function replyURL(value) {
  const url = new URL(value);
  if (url.protocol !== "https:" || url.hostname !== "oapi.dingtalk.com" ||
      (url.port && url.port !== "443") || url.username || url.password ||
      url.pathname !== "/robot/sendBySession" || url.hash)
    throw new Error("invalid DingTalk reply URL");
  return url.href;
}

export async function create({ config, require, incoming, fatal = () => {} }) {
  const { DWClient, TOPIC_ROBOT } = require("dingtalk-stream");
  const client = new DWClient({
    clientId: config.client_id, clientSecret: config.client_secret,
    debug: false, keepAlive: true, autoReconnect: false, subscriptions: [],
  });
  let stopped = false, monitor, pending = 0, token = "", tokenUntil = 0, tokenRequest;
  const abort = new AbortController();
  async function post(url, body, headers = {}) {
    const response = await fetch(url, {
      method: "POST", redirect: "error",
      signal: AbortSignal.any([abort.signal, AbortSignal.timeout(15000)]),
      headers: { "Content-Type": "application/json", ...headers },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error("DingTalk HTTP request failed");
    return response.json();
  }
  async function accessToken() {
    if (token && Date.now() < tokenUntil) return token;
    if (!tokenRequest) tokenRequest = (async () => {
      const result = await post("https://api.dingtalk.com/v1.0/oauth2/accessToken", {
        appKey: config.client_id, appSecret: config.client_secret,
      });
      if (typeof result.accessToken !== "string" || !result.accessToken ||
          !Number.isFinite(result.expireIn) || result.expireIn <= 0)
        throw new Error("DingTalk token request failed");
      token = result.accessToken;
      tokenUntil = Date.now() + Math.max(0, result.expireIn - 60) * 1000;
      return token;
    })().finally(() => { tokenRequest = null; });
    return tokenRequest;
  }
  client.registerCallbackListener(TOPIC_ROBOT, async frame => {
    if (stopped || pending >= 100) return; // No ACK: platform may redeliver.
    pending++;
    try {
      const data = JSON.parse(frame.data);
      if (String(data.conversationType) === "1" && data.msgtype === "text" &&
          typeof data.text?.content === "string" && data.text.content.trim()) {
        const webhook = replyURL(data.sessionWebhook);
        await incoming({
          id: data.msgId, sender: data.senderId, conversation: data.conversationId,
          name: data.senderNick || data.senderId, kind: "direct", text: data.text.content,
          session_webhook: webhook, webhook_expires: data.sessionWebhookExpiredTime,
        });
      }
      // Acknowledge durable admission, not completion of the (possibly long) Agent task.
      if (!stopped) client.socketCallBackResponse(frame.headers.messageId, { errcode: 0 });
    } catch {
      if (!stopped) status("error", { code: "protocol" });
    } finally { pending--; }
  });
  return {
    async start() {
      status("connecting");
      let timer;
      try {
        // 2.1.5 connect() swallows connection errors; verify actual state explicitly.
        await Promise.race([
          client.connect(),
          new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("connect timeout")), 20000); }),
        ]);
        if (stopped || !client.connected) throw new Error("DingTalk not connected");
        emit({ method: "account", params: { id: config.client_id } });
        status("online");
        // Reconnection belongs to the gateway supervisor, avoiding stacked SDK timers.
        monitor = setInterval(() => {
          if (!stopped && !client.connected) {
            clearInterval(monitor);
            status("error", { code: "network" });
            fatal();
          }
        }, 2000);
      } finally { clearTimeout(timer); }
    },
    async send({ message, text }) {
      const url = replyURL(message.session_webhook);
      const expires = Number(message.webhook_expires);
      if (!Number.isFinite(expires) || expires <= Date.now())
        throw new Error("DingTalk reply session expired");
      const access = await accessToken();
      for (const part of chunks(text, 1000)) {
        const result = await post(url, { msgtype: "text", text: { content: part } },
          { "x-acs-dingtalk-access-token": access });
        if (result.errcode !== 0) throw new Error("DingTalk delivery failed");
      }
    },
    async close() {
      stopped = true;
      clearInterval(monitor);
      abort.abort();
      client.disconnect();
    },
  };
}
