import { aiCards } from "./dingtalk-cards.mjs";
import {messageContent,imageDownloads} from './dingtalk-media.mjs';
import { chunks, emit, status, unsupportedContent } from "./protocol.mjs";

// Session callbacks are bearer credentials. Never accept arbitrary reply destinations.
export function replyURL(value) {
  const url = new URL(value);
  if (url.protocol !== "https:" || url.hostname !== "oapi.dingtalk.com" ||
      (url.port && url.port !== "443") || url.username || url.password ||
      url.pathname !== "/robot/sendBySession" || url.hash)
    throw new Error("invalid DingTalk reply URL");
  return url.href;
}

function expiredReply(error) {
  if(error.code === "ReplySessionExpired") return true;
  // Do not guess undocumented numeric codes. Require an explicit rejection of
  // the session credential; a timeout or token/permission error is ambiguous.
  let result={};
  try {result=JSON.parse(error.response?.data || "{}");}catch {}
  const reason=[result.code,result.errcode,result.message,result.errmsg,error.code,error.message].join(" ");
  return /session|webhook|会话|回复凭据/i.test(reason) && /expir|invalid|过期|失效|无效/i.test(reason);
}

export async function create({ config, require, incoming, state_directory, fatal = () => {} }) {
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
  async function post(url, body, headers = {}, method = "POST", reportErrors = true) {
    let response;
    try {response = await fetch(url, {
      method, redirect: "error",
      signal: AbortSignal.any([abort.signal, AbortSignal.timeout(15000)]),
      headers: { "Content-Type": "application/json", ...headers },
      body: JSON.stringify(body),
    });}catch(error) {error.requestPath=new URL(url).pathname;throw error;}
    if (!response.ok) {
      const error = new Error("DingTalk HTTP request failed");
      error.response={status:response.status,data:(await response.text()).slice(0,8192)};
      error.requestPath=new URL(url).pathname;
      if(reportErrors) report(error,url,"调用钉钉接口");
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
  const cards = aiCards({clientId:config.client_id,directory:state_directory,
    request:async(method,route,body)=>{
      const access=await accessToken();
      const result=await post("https://api.dingtalk.com"+route,body,
        {"x-acs-dingtalk-access-token":access},method,false);
      if(result.success===false || (result.errcode != null && result.errcode!==0) ||
         (result.code && result.code!=="OK" && result.code!=="Success"))
        {
          const error=new Error("DingTalk card API failed");
          if(typeof result.code==='string' && /^[A-Za-z0-9_.-]{1,100}$/.test(result.code)) error.code=result.code;
          throw error;
        }
      return result;
    }});
  const downloadImages=imageDownloads({directory:state_directory,signal:abort.signal,request:async code=>{
    const access=await accessToken();
    return post('https://api.dingtalk.com/v1.0/robot/messageFiles/download',
      {downloadCode:code,robotCode:config.client_id},{'x-acs-dingtalk-access-token':access},'POST',false);
  }});
  let cardFailed=false;
  function cardUnavailable(error) {
    cardFailed=true;
    status("online",{card:{state:"fallback",status:error.response?.status,
      code:typeof error.code==='string' && /^[A-Za-z0-9_.-]{1,100}$/.test(error.code)?error.code:"CardUnavailable"}});
  }
  function cardAvailable() {
    if(cardFailed){cardFailed=false;status("online",{card:{state:"ready"}});}
  }
  async function receive(data) {
    const activity = phase => status("online", {activity:{phase,
      message_type:data.msgtype, conversation_type:String(data.conversationType)}});
    activity("received");
    const content=messageContent(data) || (typeof data.msgtype==='string' && data.msgtype
      ? {...unsupportedContent(data.msgtype),image_refs:[],image_positions:[]} : null);
    if (String(data.conversationType) === "1" && content?.text.trim()) {
      const webhook = replyURL(data.sessionWebhook);
      await incoming({
        id:data.msgId, sender:data.senderId, conversation:data.conversationId,
        name:data.senderNick || data.senderId, kind:"direct", ...content,
        session_webhook:webhook, webhook_expires:data.sessionWebhookExpiredTime,
        staff_id:data.senderStaffId || "",
        robot_code:data.robotCode || config.client_id,
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
  function deliveryDiagnostic(error, message={}, path="/v1.0/robot/otoMessages/batchRecall") {
        let response;
        try {response=JSON.parse(error.response?.data || "{}");}catch {response={};}
        const secrets=[config.client_id,config.client_secret,token,message.session_webhook].filter(Boolean);
        try {secrets.push(...new URL(message.session_webhook).searchParams.values());}catch {}
        const clean=value=>{
          let text=String(value ?? "");
          for(const secret of secrets)text=text.split(secret).join("[已隐藏]");
          return text.replace(/([?&](?:session|token|access_token|ticket)=)[^&\s"']*/gi,"$1[已隐藏]").slice(0,1000);
        };
        error.delivery={provider:"dingtalk",operation:path.includes("batchRecall")?"recall":"send",path:error.requestPath || path,status:error.response?.status,
          code:clean(response.code || response.errcode || error.code || error.name),
          message:clean(response.message || response.errmsg || error.message || "发送失败"),
          request_id:clean(response.requestid || response.requestId || "")};
    return error;
  }
  return {
    downloadImages,
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
    async progress(params) {
      try {const result=await cards.progress(params);if(result.supported)cardAvailable();return result;}
      catch(error) {cardUnavailable(error);return {supported:false};} // Presentation failure must not stop the Agent.
    },
    async send({ message, text, final, card, proactive, fallback_proactive }) {
      let path = "/robot/sendBySession", deliveredChars = 0, missingReceipt=false;
      const keys=[], robot_code=message.robot_code || config.client_id;
      const resultReceipt=()=>({receipt:{robot_code,keys:[...new Set(keys)],complete:!missingReceipt && keys.length>0}});
      const remember=key=>{if(typeof key==='string' && key)keys.push(key);else missingReceipt=true;};
      try {
        if(proactive===true) {
          path="/v1.0/robot/oToMessages/batchSend";
          if(typeof message.staff_id!=="string" || !message.staff_id.trim())
            throw Object.assign(new Error("缺少接收者 userId，请让该用户重新向机器人发一条消息后再重发"),{code:"MissingStaffId"});
          const access=await accessToken();
          for(const part of chunks(text,1000)) {
            const result=await post("https://api.dingtalk.com"+path,{
              robotCode:message.robot_code || config.client_id,userIds:[message.staff_id],
              msgKey:"sampleText",msgParam:JSON.stringify({content:part}),
            },{"x-acs-dingtalk-access-token":access},"POST",false);
            if(result.invalidStaffIdList?.length)
              throw Object.assign(new Error("接收者 userId 无效或不在机器人可用范围内"),{code:"InvalidStaffId"});
            if(result.flowControlledStaffIdList?.length)
              throw Object.assign(new Error("钉钉已对接收者限流，请稍后重发"),{code:"RecipientFlowControlled"});
            if(typeof result.processQueryKey!=="string" || !result.processQueryKey)
              throw Object.assign(new Error(result.message || result.errmsg || "钉钉未返回消息受理回执"),{code:result.code || result.errcode || "MissingDeliveryAck"});
            remember(result.processQueryKey);
          }
          return resultReceipt();
        }
        if(final===true && card?.enabled===true) {
          try {
            const finished=await cards.finish({message,text,card});
            const receipt=await cards.receipt(message);
            if(receipt?.complete)keys.push(...receipt.keys);else missingReceipt=true;
            if(finished){cardAvailable();return resultReceipt();}
          }
          catch(error) {cardUnavailable(error); const receipt=await cards.receipt(message); if(receipt?.complete)keys.push(...receipt.keys); /* Preserve the final answer through ordinary text. */}
        }
        const url = replyURL(message.session_webhook);
        const expires = Number(message.webhook_expires);
        if (!Number.isFinite(expires) || expires <= Date.now())
          throw Object.assign(new Error("回复凭据过期"),{code:"ReplySessionExpired"});
        const access = await accessToken();
        for (const part of chunks(text, 1000)) {
          const result = await post(url, { msgtype: "text", text: { content: part } },
            { "x-acs-dingtalk-access-token": access }, "POST", false);
          if (result.errcode !== 0) throw Object.assign(new Error(result.errmsg || "钉钉消息发送失败"),{code:result.errcode});
          remember(result.processQueryKey);
          deliveredChars += part.length;
        }
        return resultReceipt();
      } catch(error) {
        if(fallback_proactive===true && path==="/robot/sendBySession" && expiredReply(error)) {
          // Already acknowledged chunks must not be delivered a second time.
          const rest=await this.send({message,text:text.slice(deliveredChars),proactive:true});
          if(rest?.receipt?.complete)keys.push(...rest.receipt.keys);else missingReceipt=true;
          return resultReceipt();
        }
        deliveryDiagnostic(error,message,path);
        throw error;
      }
    },
    async recall(receipt) {
      const path='/v1.0/robot/otoMessages/batchRecall', recalled=[];
      try {
        if(!receipt || receipt.robot_code!==config.client_id || receipt.complete!==true ||
           !Array.isArray(receipt.keys) || !receipt.keys.length || receipt.keys.length>200 ||
           receipt.keys.some(k=>typeof k!=='string' || !k || k.length>1024))
          throw Object.assign(Error('消息撤回凭据缺失或不属于当前机器人'),{code:'InvalidRecallReceipt'});
        const access=await accessToken(), errors=[];
        // Bounded batches and a deadline keep work inside the caller's timeout.
        const keys=[...new Set(receipt.keys)],deadline=Date.now()+30000;
        for(let offset=0;offset<keys.length;offset+=20) {
          if(Date.now()>=deadline){errors.push('撤回超时，剩余消息尚未确认');break;}
          const batch=keys.slice(offset,offset+20);
          const response=await post('https://api.dingtalk.com'+path,
            {robotCode:receipt.robot_code,processQueryKeys:batch},
            {'x-acs-dingtalk-access-token':access},'POST',false);
          for(const key of batch) {
            if(Array.isArray(response.successResult) && response.successResult.includes(key) && !response.failedResult?.[key])recalled.push(key);
            else errors.push(String(response.failedResult?.[key] || '钉钉未确认撤回成功').slice(0,1000));
          }
        }
        return {recalled,error:errors.length?deliveryDiagnostic(Error(errors.join('；')),{},path).delivery.message:''};
      } catch(error) {
        const detail=deliveryDiagnostic(error,{},path);
        if(recalled.length)return {recalled,error:detail.delivery.message};
        throw detail;
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
