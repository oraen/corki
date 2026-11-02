import test from 'node:test';
import assert from 'node:assert/strict';
import {create} from '../connectors/dingtalk.mjs';

async function fixture(run) {
  const original=globalThis.fetch,calls=[];
  class Client {config={};registerCallbackListener(){}disconnect(){}}
  const adapter=await create({config:{client_id:'fixture-app',client_secret:'fixture-secret'},
    require:()=>({DWClient:Client,TOPIC_ROBOT:'robot'}),incoming:async()=>{}});
  let response={processQueryKey:'fixture-receipt'};
  globalThis.fetch=async(url,request)=>{
    calls.push({url,body:JSON.parse(request.body),headers:request.headers});
    if(url.includes('/oauth2/'))return {ok:true,json:async()=>({accessToken:'fixture-access',expireIn:7200})};
    const result=typeof response==='function'?await response(url,request):response;
    return result.ok===false?result:{ok:true,json:async()=>result};
  };
  try {await run({adapter,calls,respond:value=>{response=value;}});}
  finally {globalThis.fetch=original;await adapter.close();}
}
const message={id:'fixture-message',sender:'encrypted-sender',staff_id:'fixture-user',
  robot_code:'fixture-robot',session_webhook:'https://oapi.dingtalk.com/robot/sendBySession?session=fixture-session',webhook_expires:1};

test('manual resend uses proactive API despite expired webhook and validates receiver receipt',()=>fixture(async({adapter,calls})=>{
  await adapter.send({message,text:'管理员通知',proactive:true});
  const sent=calls.at(-1);
  assert.equal(sent.url,'https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend');
  assert.deepEqual(sent.body,{robotCode:'fixture-robot',userIds:['fixture-user'],msgKey:'sampleText',msgParam:JSON.stringify({content:'管理员通知'})});
  assert.equal(calls.some(c=>c.url.includes('sendBySession')),false);
}));

test('expired normal replies and old records without staffId retain exact local diagnostics',()=>fixture(async({adapter,calls})=>{
  await assert.rejects(adapter.send({message,text:'reply'}),error=>{
    assert.equal(error.delivery.message,'回复凭据过期');
    assert.equal(error.delivery.code,'ReplySessionExpired');return true;
  });
  await assert.rejects(adapter.send({message:{...message,staff_id:''},text:'notice',proactive:true}),error=>{
    assert.equal(error.delivery.code,'MissingStaffId');
    assert.match(error.delivery.message,/缺少接收者 userId/);return true;
  });
  assert.equal(calls.length,0); // Never substitute encrypted senderId or reuse expired webhook.
}));

test('HTTP 200 with invalid/limited recipient or no receipt is not delivery success',()=>fixture(async({adapter,respond})=>{
  for(const [response,code] of [
    [{processQueryKey:'query',invalidStaffIdList:['fixture-user']},'InvalidStaffId'],
    [{processQueryKey:'query',flowControlledStaffIdList:['fixture-user']},'RecipientFlowControlled'],
    [{},'MissingDeliveryAck'],
  ]) {
    respond(response);
    await assert.rejects(adapter.send({message,text:'notice',proactive:true}),e=>e.delivery.code===code);
  }
}));

test('API failures preserve status/code/reason/request ID and remove known credentials',()=>fixture(async({adapter,respond})=>{
  respond({ok:false,status:403,text:async()=>JSON.stringify({code:'Forbidden.AccessDenied',
    message:'permission denied fixture-secret fixture-access fixture-session',requestid:'fixture-request'})});
  await assert.rejects(adapter.send({message,text:'notice',proactive:true}),error=>{
    const d=error.delivery;
    assert.equal(d.status,403);assert.equal(d.code,'Forbidden.AccessDenied');
    assert.equal(d.request_id,'fixture-request');assert.match(d.message,/permission denied/);
    for(const secret of ['fixture-secret','fixture-access','fixture-session'])assert.equal(JSON.stringify(d).includes(secret),false);
    return true;
  });
}));

test('authorization notices prefer valid session replies without using proactive API',()=>fixture(async({adapter,calls,respond})=>{
  respond({errcode:0});
  await adapter.send({message:{...message,webhook_expires:Date.now()+60000},text:'notice',fallback_proactive:true});
  assert.equal(calls.filter(c=>c.url.includes('sendBySession')).length,1);
  assert.equal(calls.some(c=>c.url.includes('batchSend')),false);
}));

test('expired session notices fall back to proactive delivery exactly once',()=>fixture(async({adapter,calls})=>{
  await adapter.send({message,text:'notice',fallback_proactive:true});
  assert.equal(calls.filter(c=>c.url.includes('batchSend')).length,1);
  assert.equal(calls.some(c=>c.url.includes('sendBySession')),false);
}));

test('explicit upstream session expiry falls back, preserving already acknowledged chunks',()=>fixture(async({adapter,calls,respond})=>{
  let sessionCalls=0;
  respond(url=>{
    if(url.includes('sendBySession'))return ++sessionCalls===1?{errcode:0}:{errcode:123,errmsg:'sessionWebhook expired'};
    return {processQueryKey:'fixture-receipt'};
  });
  const text='字'.repeat(2050);
  await adapter.send({message:{...message,webhook_expires:Date.now()+60000},text,fallback_proactive:true});
  const active=calls.filter(c=>c.url.includes('batchSend'));
  assert.equal(sessionCalls,2);
  assert.equal(active.map(c=>JSON.parse(c.body.msgParam).content).join(''),text.slice(1000));
}));

test('permission failures and ambiguous network timeouts do not trigger proactive duplicates',()=>fixture(async({adapter,calls,respond})=>{
  const fresh={...message,webhook_expires:Date.now()+60000};
  for(const response of [
    {ok:false,status:403,text:async()=>'{"code":"Forbidden.AccessDenied","message":"permission denied"}'},
    ()=>{throw new DOMException('request timed out','TimeoutError');},
  ]) {
    respond(response);
    await assert.rejects(adapter.send({message:fresh,text:'notice',fallback_proactive:true}));
  }
  assert.equal(calls.some(c=>c.url.includes('batchSend')),false);
}));

test('fallback failure retains the actual proactive API error',()=>fixture(async({adapter,respond})=>{
  respond({ok:false,status:403,text:async()=>'{"code":"Forbidden.AccessDenied","message":"missing proactive permission"}'});
  await assert.rejects(adapter.send({message,text:'notice',fallback_proactive:true}),error=>{
    assert.equal(error.delivery.status,403);assert.equal(error.delivery.code,'Forbidden.AccessDenied');
    assert.equal(error.delivery.path,'/v1.0/robot/oToMessages/batchSend');return true;
  });
}));
