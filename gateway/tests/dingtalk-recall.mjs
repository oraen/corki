import test from 'node:test';
import assert from 'node:assert/strict';
import {create} from '../connectors/dingtalk.mjs';
import {aiCards} from '../connectors/dingtalk-cards.mjs';
const message={id:'fixture',sender:'alice',staff_id:'fixture-user',robot_code:'fixture-app',
 session_webhook:'https://oapi.dingtalk.com/robot/sendBySession?session=fixture',webhook_expires:Date.now()+60000};
async function fixture(run) {
 const original=globalThis.fetch,calls=[];
 class Client {config={};registerCallbackListener(){}disconnect(){}}
 const adapter=await create({config:{client_id:'fixture-app',client_secret:'fixture-secret'},require:()=>({DWClient:Client,TOPIC_ROBOT:'robot'}),incoming:async()=>{}});
 let respond=()=>({processQueryKey:'fixture-key'});
 globalThis.fetch=async(url,request)=>{
  const body=JSON.parse(request.body);calls.push({url,body});
  const result=url.includes('/oauth2/')?{accessToken:'fixture-access',expireIn:7200}:await respond(url,body);
  return result.ok===false?result:{ok:true,json:async()=>result};
 };
 try{await run({adapter,calls,response:fn=>{respond=fn;}});}finally{globalThis.fetch=original;await adapter.close();}
}
test('proactive chunks retain every recall key, including restart-safe robot identity',()=>fixture(async({adapter,response})=>{
 let i=0;response(()=>({processQueryKey:'key-'+(++i)}));
 const result=await adapter.send({message,text:'x'.repeat(2100),proactive:true});
 assert.deepEqual(result.receipt,{robot_code:'fixture-app',keys:['key-1','key-2','key-3'],complete:true});
}));
test('webhook replies without receipts are never advertised as recallable',()=>fixture(async({adapter,response})=>{
 response(()=>({errcode:0}));
 const result=await adapter.send({message,text:'fixture'});
 assert.equal(result.receipt.complete,false);
 assert.deepEqual(result.receipt.keys,[]);
}));
test('valid webhook receipts survive partial session expiry and proactive fallback',()=>fixture(async({adapter,response})=>{
 let sessionCalls=0;
 response(url=>url.includes('sendBySession')?(++sessionCalls===1?{errcode:0,processQueryKey:'first'}:{errcode:1,errmsg:'session expired'}):{processQueryKey:'second'});
 const result=await adapter.send({message,text:'x'.repeat(1500),fallback_proactive:true});
 assert.deepEqual(result.receipt.keys,['first','second']);assert.equal(result.receipt.complete,true);
}));
test('recall requires positive receipts and rejects a different robot without network calls',()=>fixture(async({adapter,calls,response})=>{
 const receipt={robot_code:'fixture-app',keys:['first','second'],complete:true};
 await assert.rejects(adapter.recall({...receipt,robot_code:'other'}));assert.equal(calls.length,0);
 response(()=>({successResult:['first'],failedResult:{second:'robot.processQueryKey.expired'}}));
 const result=await adapter.recall(receipt);
 assert.deepEqual(result.recalled,['first']);assert.match(result.error,/expired/);
 assert(calls.at(-1).url.endsWith('/robot/otoMessages/batchRecall'));
 assert.deepEqual(calls.at(-1).body,{robotCode:'fixture-app',processQueryKeys:['first','second']});
 response(()=>({}));assert.deepEqual((await adapter.recall(receipt)).recalled,[]);
}));
test('confirmed batches survive a later transport error',()=>fixture(async({adapter,response})=>{
 let i=0;response((_url,body)=>{if(++i===1)return {successResult:body.processQueryKeys};throw Error('fixture offline');});
 const keys=Array.from({length:21},(_,i)=>'key-'+i);
 const result=await adapter.recall({robot_code:'fixture-app',keys,complete:true});
 assert.deepEqual(result.recalled,keys.slice(0,20));assert.match(result.error,/offline/);
}));
test('card delivery keeps actual carrier receipt, never guesses from outTrackId',async()=>{
 const requests=[];
 const cards=aiCards({clientId:'fixture-app',request:async(method,url,body)=>{
  requests.push({url,body});
  return url.endsWith('/deliver')?{success:true,result:[{spaceType:'IM_ROBOT',success:true,carrierId:'platform-recall-key'}]}:{};
 }});
 const options={enabled:true,template:'fixture.schema',field:'content'};
 const progress=await cards.progress({message,card:options,phase:'start'});
 assert.deepEqual(progress.receipt,{robot_code:'fixture-app',keys:['platform-recall-key'],complete:true});
 await cards.finish({message,card:options,text:'Answer'});
 assert.deepEqual(await cards.receipt(message),progress.receipt);
 assert.notEqual(progress.receipt.keys[0],requests[0].body.outTrackId);
});
test('card response with failed delivery is not marked delivered',async()=>{
 const cards=aiCards({clientId:'fixture-app',request:async(_method,url)=>url.endsWith('/deliver')?{success:false,result:[]}: {}});
 await assert.rejects(cards.progress({message,card:{enabled:true,template:'fixture.schema'},phase:'start'}),/delivery rejected/);
 assert.equal(await cards.receipt(message),null);
});
