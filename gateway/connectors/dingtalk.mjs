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
  let reported = false;
  const endpoint = "https://api.dingtalk.com/v1.0/gateway/connections/open";
  function report(error, address = endpoint, stage = "连接钉钉 Stream", method = "POST") {
    reported = true;
    const redact = value => {
      let text = typeof value === "string" ? value : JSON.stringify(value ?? "");
      for (const secret of [config.client_id, config.client_secret, token, client.config?.access_token,
        client.config?.endpoint?.ticket].filter(Boolean)) text = text.split(secret).join("[已隐藏]");
      return text.replace(/([?&](?:ticket|session|token|appsecret|appkey)=)[^&\s"']*/gi, "$1[已隐藏]").slice(0,8192);
    };
    const url = new URL(address);
    for (const key of [...url.searchParams.keys()]) url.searchParams.set(key,"[已隐藏]");
    status("error", {code:"network", detail:{url:url.toString(),method,stage,
      status:error.response?.status,
      response:error.response?.data === undefined ? "" : redact(error.response.data),
      error:redact(error.code || error.message || "连接失败")}});
  }
  // SDK 2.1.5 catches getEndpoint/_connect failures inside connect(). Capture
  // them at their source, keeping the SDK's handshake and reconnect semantics.
  for (const [method, stage] of [["getEndpoint","获取钉钉连接地址"],["_connect","建立钉钉 WebSocket"]]) {
    if (typeof client[method] !== "function") continue;
    const original=client[method].bind(client);
    client[method]=async (...args)=>{
      try {return await original(...args);}
      catch(error) {report(error,method === "_connect" ? client.dw_url || endpoint : endpoint,stage,
        method === "_connect" ? "GET" : "POST");throw error;}
    };
  }
  const abort = new AbortController();
  async function post(url, body, headers = {}) {
    const response = await fetch(url, {
      method: "POST", redirect: "error",
      signal: AbortSignal.any([abort.signal, AbortSignal.timeout(15000)]),
      headers: { "Content-Type": "application/json", ...headers },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const error = new Error("DingTalk HTTP request failed");
      error.response={status:response.status,data:(await response.text()).slice(0,8192)};
      report(error,url,"调用钉钉接口");
      throw error;
    }
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
  async function receive(data) {
    const activity = phase => status("online", {activity:{phase,
      message_type:data.msgtype, conversation_type:String(data.conversationType)}});
    activity("received");
    if (String(data.conversationType) === "1" && data.msgtype === "text" &&
        typeof data.text?.content === "string" && data.text.content.trim()) {
      const webhook = replyURL(data.sessionWebhook);
      await incoming({
        id:data.msgId, sender:data.senderId, conversation:data.conversationId,
        name:data.senderNick || data.senderId, kind:"direct", text:data.text.content,
        session_webhook:webhook, webhook_expires:data.sessionWebhookExpiredTime,
      });
      activity("accepted");
    } else activity(String(data.conversationType) !== "1" ? "ignored_group" : "ignored_type");
  }
  client.registerCallbackListener(TOPIC_ROBOT, async frame => {
    if (stopped || pending >= 100) return; // No ACK: platform may redeliver.
    pending++;
    try {
      await receive(JSON.parse(frame.data));
      // ACK durable admission, never wait for Agent completion.
      if (!stopped) client.socketCallBackResponse(frame.headers.messageId, { errcode:0 });
    } catch {
      if (!stopped) status("error", {code:"protocol"});
    } finally { pending--; }
  });
  return {
    async start() {
      reported = false;
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
      } catch(error) {
        if(!reported) report(error);
        throw error;
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
