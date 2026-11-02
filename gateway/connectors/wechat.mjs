// iLink wire behavior studied from Tencent's MIT-licensed openclaw-weixin 2.4.8.
// This is an independent text transport, not an OpenClaw plugin loader.
import { readFile, writeFile, rename } from "node:fs/promises";
import path from "node:path";
import { randomBytes, randomUUID } from "node:crypto";
import { status, emit, chunks, sleep } from "./protocol.mjs";

export function trustedBase(value) {
  const url = new URL(value);
  if (
    url.protocol !== "https:" ||
    url.username ||
    url.password ||
    url.port ||
    !(
      url.hostname === "ilinkai.weixin.qq.com" ||
      url.hostname.endsWith(".weixin.qq.com")
    )
  )
    throw new Error("invalid iLink host");
  return url.origin;
}
export async function create({ state_directory, cursor, require, incoming }) {
  const loginFile = path.join(state_directory, "login.json");
  let account,
    stopped = false,
    verifyCode = "",
    buffer = cursor;
  const controller = new AbortController();
  try {
    account = JSON.parse(await readFile(loginFile, "utf8"));
    account.base = trustedBase(account.base);
  } catch {
    account = null;
  }
  async function api(base, endpoint, body, token, timeout = 35000) {
    const target = new URL(trustedBase(base) + "/" + endpoint);
    for (const key of [...target.searchParams.keys()]) {
      if (/token|qrcode|verify|key|secret/i.test(key)) target.searchParams.set(key, "[已隐藏]");
    }
    const detail = {url:target.toString(), method:body === undefined ? "GET" : "POST",
      stage:endpoint.includes("get_bot_qrcode") ? "获取登录二维码" :
        endpoint.includes("get_qrcode_status") ? "检查扫码状态" :
        endpoint.includes("getupdates") ? "接收消息" : "发送回复"};
    const redact = text => {
      for (const secret of [token, verifyCode, account?.token].filter(Boolean))
        text = text.split(secret).join("[已隐藏]");
      return text.slice(0,8192);
    };
    const headers = {
      "Content-Type": "application/json",
      "iLink-App-Id": "bot",
      "iLink-App-ClientVersion": String((2 << 16) | (4 << 8) | 8),
      AuthorizationType: "ilink_bot_token",
      "X-WECHAT-UIN": Buffer.from(
        String(randomBytes(4).readUInt32BE()),
      ).toString("base64"),
    };
    if (token) headers.Authorization = `Bearer ${token}`;
    let response;
    try { response = await fetch(trustedBase(base) + "/" + endpoint, {
      method: body === undefined ? "GET" : "POST",
      headers,
      body:
        body === undefined
          ? undefined
          : JSON.stringify({
              ...body,
              base_info: { channel_version: "2.4.8", bot_agent: "Corki/0.1.0" },
            }),
      redirect: "error",
      signal: AbortSignal.any([
        controller.signal,
        AbortSignal.timeout(timeout),
      ]),
    }); } catch(error) {
      status("error", {code:"network", detail:{...detail, error:redact(error.name + ": " + (error.cause?.code || error.message))}});
      throw error;
    }
    if (!response.ok) {
      const reader=response.body?.getReader();
      let text="";
      if(reader) {
        const decoder=new TextDecoder();
        try { while(text.length<8192) { const {done,value}=await reader.read();if(done)break;text+=decoder.decode(value,{stream:true}); } }
        finally { await reader.cancel().catch(()=>{}); }
      }
      status("error", {code:"http", detail:{...detail,status:response.status,
        content_type:response.headers.get("content-type") || "",response:redact(text)}});
      throw new Error("iLink request failed");
    }
    const value = await response.json();
    if (value.ret || value.errcode) {
      if (value.errcode === -14 || value.ret === -14) {
        status("error", { code: "credentials" });
        stopped = true;
      }
      throw new Error("iLink response error");
    }
    return value;
  }
  async function login() {
    const base = "https://ilinkai.weixin.qq.com";
    const qr = await api(base, "ilink/bot/get_bot_qrcode?bot_type=3", {
      local_token_list: [],
    });
    status("qr", {
      qr: await require("qrcode").toDataURL(qr.qrcode_img_content, {
        width: 260,
        margin: 2,
      }),
    });
    let pollBase = base;
    const deadline = Date.now() + 300000;
    while (!stopped && Date.now() < deadline) {
      let value;
      try {
        value = await api(
          pollBase,
          `ilink/bot/get_qrcode_status?qrcode=${encodeURIComponent(qr.qrcode)}${verifyCode ? "&verify_code=" + encodeURIComponent(verifyCode) : ""}`,
        );
      } catch {
        if (stopped) return;
        await sleep(2000);
        continue;
      }
      if (value.status === "scaned_but_redirect")
        pollBase = trustedBase(`https://${value.redirect_host}`);
      if (value.status === "need_verifycode") {
        verifyCode = "";
        status("verification", { code: "verify" });
      }
      if (value.status === "scaned") verifyCode = "";
      if (
        ["expired", "verify_code_blocked", "binded_redirect"].includes(
          value.status,
        )
      ) {
        status("error", { code: "expired" });
        stopped = true;
        return;
      }
      if (value.status === "confirmed") {
        if (!value.bot_token || !value.ilink_bot_id)
          throw new Error("missing credentials");
        account = {
          token: value.bot_token,
          id: value.ilink_bot_id,
          base: trustedBase(value.baseurl || base),
        };
        await writeFile(loginFile + ".tmp", JSON.stringify(account), {
          mode: 0o600,
        });
        await rename(loginFile + ".tmp", loginFile);
        return;
      }
      await sleep(1000);
    }
    if (!account) {
      status("error", { code: "expired" });
      stopped = true;
    }
  }
  return {
    async start() {
      if (!account) await login();
      if (!account || stopped) return;
      emit({ method: "account", params: { id: account.id } });
      status("online");
      let failures = 0;
      while (!stopped) {
        try {
          const update = await api(
            account.base,
            "ilink/bot/getupdates",
            { get_updates_buf: buffer || "" },
            account.token,
          );
          for (const item of update.msgs || []) {
            if (item.message_type !== 1 || !item.from_user_id) continue;
            const text = (item.item_list || [])
              .filter((i) => i.type === 1)
              .map((i) => i.text_item?.text || "")
              .join("\n");
            const unsupported=(item.item_list || []).some(i=>i.type!==1);
            if (!text.trim() && !unsupported) continue;
            await incoming({
              id: String(item.message_id),
              sender: item.from_user_id,
              conversation: item.from_user_id,
              kind: "direct",
              ...(unsupported?{text:(text?text+'\n':'')+'[非文本消息]',unsupported:true}:{text}),
              context_token: item.context_token,
            });
          }
          if (typeof update.get_updates_buf === "string") {
            buffer = update.get_updates_buf;
            emit({ method: "cursor", params: { value: buffer } });
          }
          failures = 0;
          status("online");
        } catch {
          if (stopped) break;
          status("error", { code: "network" });
          await sleep(Math.min(60, 2 ** Math.min(++failures, 6)) * 1000);
        }
      }
    },
    async send({ message, text }) {
      if (!account || !message.context_token)
        throw new Error("missing reply context");
      for (const part of chunks(text))
        await api(
          account.base,
          "ilink/bot/sendmessage",
          {
            msg: {
              from_user_id: "",
              to_user_id: message.conversation,
              client_id: "corki-" + randomUUID(),
              message_type: 2,
              message_state: 2,
              context_token: message.context_token,
              item_list: [{ type: 1, text_item: { text: part } }],
            },
          },
          account.token,
          20000,
        );
    },
    async verify(code) {
      verifyCode = code;
    },
    async close() {
      stopped = true;
      controller.abort();
    },
  };
}
