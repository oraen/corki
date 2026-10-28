// Actual pinned SDK against a local WebSocket peer; no platform credentials.
import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { create } from "../connectors/dingtalk.mjs";

test("installed DingTalk SDK: connect, callback, ACK and close", {
  skip: !process.env.DINGTALK_TEST_PACKAGE,
}, async () => {
  const require = createRequire(process.env.DINGTALK_TEST_PACKAGE);
  const sdk = require("dingtalk-stream");
  const { WebSocketServer } = createRequire(require.resolve("dingtalk-stream"))("ws");
  const server = new WebSocketServer({host:"127.0.0.1",port:0});
  await new Promise(resolve=>server.once("listening",resolve));
  let socket, resolveAck;
  const acknowledged = new Promise(resolve=>{resolveAck=resolve;});
  server.on("connection", peer => {
    socket=peer;
    peer.on("message",data=>resolveAck(JSON.parse(data.toString())));
  });
  const received=[];
  class LocalClient extends sdk.DWClient {
    async getEndpoint() { this.dw_url="ws://127.0.0.1:"+server.address().port; return this; }
  }
  const adapter=await create({
    config:{client_id:"fixture",client_secret:"fixture"},
    require:()=>({...sdk,DWClient:LocalClient}),
    incoming:async message=>received.push(message),
  });
  try {
    await adapter.start();
    socket.send(JSON.stringify({type:"CALLBACK",headers:{topic:sdk.TOPIC_ROBOT,messageId:"envelope"},
      data:JSON.stringify({msgId:"one",senderId:"person",conversationId:"dm",conversationType:"1",
        msgtype:"text",text:{content:"hello"},sessionWebhook:"https://oapi.dingtalk.com/robot/sendBySession?fixture=1",
        sessionWebhookExpiredTime:Date.now()+60000})}));
    let timer;
    try {
      const ack=await Promise.race([acknowledged,new Promise((_,reject)=>{
        timer=setTimeout(()=>reject(new Error("no ACK")),3000);
      })]);
      assert.equal(ack.headers.messageId,"envelope");
      assert.equal(ack.code,200);
      assert.equal(received[0].text,"hello");
    } finally { clearTimeout(timer); }
  } finally {
    await adapter.close();
    for(const peer of server.clients) peer.terminate();
    await new Promise(resolve=>server.close(resolve));
  }
});
