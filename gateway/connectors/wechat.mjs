// iLink wire behavior studied from Tencent's MIT-licensed openclaw-weixin 2.4.8.
// Independent text and image transport.
import { readFile, writeFile, rename } from "node:fs/promises";
import path from "node:path";
import { randomBytes, randomUUID } from "node:crypto";
import { status, emit, chunks, sleep } from "./protocol.mjs";
import { createMedia, imageRef, fileRef } from "./wechat-media.mjs";

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
  let loginRequested = false, loginInProgress = false, wakeLogin, loginController;
  let errorRevision = 0;
  const connectionError = extra => { errorRevision++; status("error", extra); };
  const controller = new AbortController();
  const typingOwners = new Map(), typingTickets = new Map();
  const media = createMedia({directory:state_directory,signal:controller.signal,
    api:body=>api(account.base,"ilink/bot/getuploadurl",body,account.token,30000)});
  const fileMedia = createMedia({directory:state_directory,signal:controller.signal,attachments:true,
    api:body=>api(account.base,"ilink/bot/getuploadurl",body,account.token,30000)});
  try {
    account = JSON.parse(await readFile(loginFile, "utf8"));
    account.base = trustedBase(account.base);
  } catch {
    account = null;
  }
  async function api(base, endpoint, body, token, timeout = 35000, signal) {
    const sending = endpoint === "ilink/bot/sendmessage";
    const statusOnly = endpoint === "ilink/bot/getconfig" || endpoint === "ilink/bot/sendtyping";
    const outbound = sending || endpoint === "ilink/bot/getuploadurl" || statusOnly;
    const failure = (message, extra = {}) => {
      const error = new Error(message);
      if(outbound)error.delivery={provider:"wechat",message,path:"/"+endpoint,...extra};
      return error;
    };
    const target = new URL(trustedBase(base) + "/" + endpoint);
    for (const key of [...target.searchParams.keys()]) {
      if (/token|qrcode|verify|key|secret/i.test(key)) target.searchParams.set(key, "[已隐藏]");
    }
    const detail = {url:target.toString(), method:body === undefined ? "GET" : "POST",
      stage:endpoint.includes("get_bot_qrcode") ? "获取登录二维码" :
        endpoint.includes("get_qrcode_status") ? "检查扫码状态" :
        endpoint.includes("getupdates") ? "接收消息" : endpoint.includes("getuploadurl") ? "上传图片" : "发送回复"};
    const redact = text => {
      for (const secret of [token, verifyCode, account?.token, body?.msg?.context_token, body?.context_token, body?.typing_ticket, body?.aeskey, body?.filekey].filter(Boolean))
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
        ...(signal ? [signal] : []),
        AbortSignal.timeout(timeout),
      ]),
    }); } catch(error) {
      if (!outbound && !stopped && !signal?.aborted) connectionError({code:"network", detail:{...detail, error:redact(error.name + ": " + (error.cause?.code || error.message))}});
      throw outbound?failure("发送未获确认，请检查网络；消息未自动重发"):error;
    }
    signal?.throwIfAborted();
    if (!response.ok) {
      const reader=response.body?.getReader();
      let text="";
      if(reader) {
        const decoder=new TextDecoder();
        try { while(text.length<8192) { const {done,value}=await reader.read();if(done)break;text+=decoder.decode(value,{stream:true}); } }
        finally { await reader.cancel().catch(()=>{}); }
      }
      if(!outbound)connectionError({code:"http", detail:{...detail,status:response.status,
        content_type:response.headers.get("content-type") || "",response:redact(text)}});
      throw failure("微信接口拒绝了此次发送", {status:response.status});
    }
    if(endpoint==="ilink/bot/sendtyping"){
      await response.body?.cancel().catch(()=>{});
      return {};
    }
    let value;
    try {value = await response.json();}
    catch(error) {
      if(outbound)throw failure("微信返回的发送结果无效，送达情况未知；消息未自动重发");
      throw error;
    }
    signal?.throwIfAborted();
    if(sending && (!value || typeof value!=="object" || Array.isArray(value) ||
        ["ret","errcode"].some(key=>value[key]!==undefined &&
          (typeof value[key]!=="number" || !Number.isFinite(value[key])))))
      throw failure("微信返回的发送结果无效，送达情况未知；消息未自动重发");
    if (value.ret || value.errcode) {
      if (!statusOnly && (value.errcode === -14 || value.ret === -14)) {
        account = null;
        status("login", { code: "credentials" });
      }
      const code=String(value.errcode || value.ret);
      throw failure(value.errcode===-14 || value.ret===-14
        ? "微信登录已失效，请重新连接"
        : "微信未接受此次发送，请让对方发一条消息后重试",
        /^[A-Za-z0-9_.-]{1,80}$/.test(code)?{code}:{});
    }
    // Tencent SendMessageResp has optional status fields: HTTP success with {}
    // is also success. Only present, nonzero codes indicate rejection.
    return value;
  }
  async function login(signal) {
    verifyCode = "";
    const base = "https://ilinkai.weixin.qq.com";
    const qr = await api(base, "ilink/bot/get_bot_qrcode?bot_type=3", {
      local_token_list: [],
    }, undefined, 15000, signal);
    const image = await require("qrcode").toDataURL(qr.qrcode_img_content, {
        width: 260,
        margin: 2,
      });
    if (stopped || signal.aborted) return;
    status("qr", { qr: image });
    let pollBase = base;
    const deadline = Date.now() + 300000;
    while (!stopped && !signal.aborted && Date.now() < deadline) {
      let value;
      const submittedCode = verifyCode;
      try {
        value = await api(
          pollBase,
          `ilink/bot/get_qrcode_status?qrcode=${encodeURIComponent(qr.qrcode)}${submittedCode ? "&verify_code=" + encodeURIComponent(submittedCode) : ""}`,
          undefined, undefined, 35000, signal,
        );
      } catch {
        if (stopped || signal.aborted) return;
        await sleep(2000);
        continue;
      }
      if (value.status === "scaned_but_redirect")
        pollBase = trustedBase(`https://${value.redirect_host}`);
      if (value.status === "need_verifycode") {
        if (verifyCode === submittedCode) verifyCode = "";
        status("verification", { code: submittedCode ? "verify_retry" : "verify" });
      }
      if (signal.aborted || stopped) return;
      if (value.status === "scaned") {
        verifyCode = "";
        status("scanned", { qr: image });
      }
      if (value.status === "binded_redirect") {
        status("login", { code: "already_bound" });
        return;
      }
      if (
        ["expired", "verify_code_blocked"].includes(
          value.status,
        )
      ) {
        status("login", { code: "expired" });
        return;
      }
      if (value.status === "confirmed") {
        if (stopped || signal.aborted) return;
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
      if (!stopped && !signal.aborted) status("login", { code: "expired" });
    }
  }
  async function waitForLogin() {
    while (!account && !stopped) {
      if (!loginRequested) {
        await new Promise(resolve => {
          wakeLogin = resolve;
          if (loginRequested || stopped) resolve();
        });
        wakeLogin = null;
      }
      if (stopped) return;
      loginRequested = false;
      loginInProgress = true;
      const attempt = loginController = new AbortController();
      const beforeError = errorRevision;
      try { await login(attempt.signal); }
      catch {
        // Preserve the actual HTTP/network failure and its diagnostic details.
        if (!stopped && !attempt.signal.aborted && errorRevision === beforeError)
          connectionError({ code: "login_failed" });
      } finally { loginInProgress = false; }
    }
  }
  return {
    async start() {
      if (!account) {
        status("login");
        await waitForLogin();
      }
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
            const parts=[],image_refs=[],image_positions=[],file_refs=[],file_positions=[];
            let unsupported=false,media_error="";
            for(const content of item.item_list || []) {
              if(content.type===1) {
                if(content.text_item?.text)parts.push(content.text_item.text);
              } else if(content.type===2) {
                try {
                  if(image_refs.length+file_refs.length>=8)throw new Error("一次最多接收 8 个附件");
                  const ref=imageRef(content);
                  image_positions.push(Array.from(parts.join("\n")).length+(parts.length?1:0));
                  image_refs.push(ref);parts.push(`[Image #${image_refs.length}]`);
                }catch{media_error="图片下载信息无效或数量过多，请重发图片";parts.push("[图片]");}
              } else if(content.type===4 || content.type===5) {
                try {
                  if(image_refs.length+file_refs.length>=8)throw new Error('附件数量过多');
                  const ref=fileRef(content);
                  file_positions.push(Array.from(parts.join('\n')).length+(parts.length?1:0));
                  file_refs.push(ref);parts.push(`[File #${file_refs.length}]`);
                }catch{media_error='附件下载信息无效或数量过多，请重新发送';parts.push('[附件]');}
              } else unsupported=true;
            }
            const text=parts.join("\n");
            if (!text.trim() && !unsupported) continue;
            await incoming({
              id: String(item.message_id),
              sender: item.from_user_id,
              conversation: item.from_user_id,
              kind: "direct",
              ...(unsupported?{text:(text?text+'\n':'')+'[非文本消息]',unsupported:true}:{text}),
              ...(image_refs.length?{image_refs,image_positions}:{}),
              ...(file_refs.length?{file_refs,file_positions}:{}),
              ...(media_error?{media_error}:{}),
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
          if (!account) {
            await waitForLogin();
            if (stopped || !account) return;
            emit({ method: "account", params: { id: account.id } });
            status("online");
            failures = 0;
            continue;
          }
          status("error", { code: "network" });
          await sleep(Math.min(60, 2 ** Math.min(++failures, 6)) * 1000);
        }
      }
    },
    async login({ refresh = false } = {}) {
      if (stopped) throw new Error("connector stopped");
      if (account || loginRequested || (loginInProgress && !refresh)) return { requested: false };
      if (refresh) loginController?.abort();
      loginRequested = true;
      status("connecting");
      wakeLogin?.();
      return { requested: true };
    },
    async downloadImages(images) {return media.download(images);},
    async downloadFiles(files) {return fileMedia.download(files);},
    async typing({message,active}) {
      const peer=message?.conversation,id=message?.id;
      if(!account || stopped || typeof peer!=="string" || !peer || typeof id!=="string")return {supported:false};
      let owner=typingOwners.get(peer);
      if(!active && owner?.id!==id)return {supported:true};
      if(active && owner?.id!==id){owner={id};typingOwners.set(peer,owner);}
      if(!active)typingOwners.delete(peer);
      const bot=account.id,cacheKey=bot+":"+peer;
      try {
        let cached=typingTickets.get(cacheKey);
        if(active && (!cached || cached.expires<Date.now())) {
          const config=await api(account.base,"ilink/bot/getconfig",{
            ilink_user_id:peer,...(message.context_token?{context_token:message.context_token}:{})
          },account.token,3000);
          if(typeof config.typing_ticket!=="string" || !config.typing_ticket || config.typing_ticket.length>16384)return {supported:false};
          cached={ticket:config.typing_ticket,expires:Date.now()+300000};
          if(typingTickets.size>=100)typingTickets.delete(typingTickets.keys().next().value);
          typingTickets.set(cacheKey,cached);
        }
        if(!cached || !account || account.id!==bot || stopped ||
          (active ? typingOwners.get(peer)!==owner : typingOwners.has(peer)))return {supported:false};
        await api(account.base,"ilink/bot/sendtyping",{
          ilink_user_id:peer,typing_ticket:cached.ticket,status:active?1:2
        },account.token,3000);
        return {supported:true};
      }catch{return {supported:false};}
    },
    async send({ message, text, images = [], files = [] }) {
      if (!account || typeof message.conversation !== "string" || !message.conversation.trim())
        throw new Error("missing recipient or login");
      let delivered=0;
      try {
        // Bound the complete image operation below the host RPC timeout; do not
        // continue posting images after the caller has recorded a timeout.
        if(!Array.isArray(images) || !Array.isArray(files) || images.length+files.length>8)throw new Error('附件数量无效');
        const signal=images.length+files.length?AbortSignal.any([controller.signal,AbortSignal.timeout(150000)]):undefined;
        const sender=images.length?createMedia({directory:state_directory,signal,
          api:body=>api(account.base,"ilink/bot/getuploadurl",body,account.token,30000,signal)}):media;
        // Finish uploads before posting anything to the conversation.
        const pictureItems=await sender.upload(images,message.conversation);
        const fileSender=createMedia({directory:state_directory,signal:signal || controller.signal,attachments:true,
          api:body=>api(account.base,"ilink/bot/getuploadurl",body,account.token,30000,signal)});
        const fileItems=await fileSender.upload(files,message.conversation);
        const items=[...chunks(text).map(part=>({type:1,text_item:{text:part}})),...pictureItems,...fileItems];
        if(!items.length)throw new Error("请输入消息或选择图片");
        for (const content of items) {
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
              ...(message.context_token ? {context_token:message.context_token} : {}),
              item_list: [content],
            },
          },
          account.token,
          20000,
          signal,
          );
          delivered++;
        }
      }catch(error) {
        if(!error.delivery)error.delivery={provider:"wechat",message:"图片上传或发送失败，请检查网络后重试"};
        if(delivered)error.delivery.message="部分消息可能已发送，请勿重复提交。"+error.delivery.message;
        throw error;
      }
    },
    async verify(code) {
      verifyCode = code;
    },
    async close() {
      stopped = true;
      typingOwners.clear();typingTickets.clear();
      wakeLogin?.();
      controller.abort();
    },
  };
}
