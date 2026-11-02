import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {aiCards} from '../connectors/dingtalk-cards.mjs';
const message={id:'fixture-message',staff_id:'fixture-staff'};
const card={enabled:true,template:'fixture-template.schema',field:'content'};

test('AI card creates one DM card, streams, finalizes, and survives connector restart',async()=>{
 const directory=await mkdtemp(path.join(os.tmpdir(),'corki-card-'));
 const calls=[];const request=async(method,route,body)=>{calls.push({method,route,body});return {}};
 try{
  const adapter=aiCards({clientId:'fixture',directory,request});
  const start={message,card,phase:'start'};
  assert.deepEqual(await adapter.progress(start),{supported:true});
  await Promise.all([adapter.progress(start),adapter.progress(start)]);
  assert.equal(calls.filter(c=>c.method==='POST').length,2);
  assert.equal(calls[1].body.openSpaceId,'dtv1.card//IM_ROBOT.fixture-staff');
  await adapter.progress({message,card,phase:'answer',text:'Hello'});
  assert.equal(calls.at(-1).body.isFinalize,false);
  const restarted=aiCards({clientId:'fixture',directory,request});
  assert.equal(await restarted.finish({message,card,text:'Hello world'}),true);
  assert.equal(calls.at(-2).body.isFinalize,true);
  assert.equal(calls.at(-1).body.cardData.cardParamMap.flowStatus,'3');
  const count=calls.length;
  await restarted.finish({message,card,text:'Hello world'});
  assert.equal(calls.length,count);
  assert.equal(calls.filter(c=>c.method==='POST').length,2);
 }finally{await rm(directory,{recursive:true,force:true});}
});

test('AI cards stay isolated by message and settle failure and stop states',async()=>{
 const calls=[];const adapter=aiCards({clientId:'fixture',request:async(method,route,body)=>calls.push({method,route,body})});
 for(const [id,phase,expected] of [['first','failed','5'],['second','stopped','3']]){
  const m={...message,id};await adapter.progress({message:m,card,phase:'start'});
  await adapter.progress({message:m,card,phase,text:'fixture outcome'});
  assert.equal(calls.at(-1).body.cardData.cardParamMap.flowStatus,expected);
 }
 const created=calls.filter(c=>c.method==='POST' && c.route==='/v1.0/card/instances');
 assert.equal(created.length,2);
 assert.notEqual(created[0].body.outTrackId,created[1].body.outTrackId);
});

test('missing staff IDs and disabled cards leave text delivery available',async()=>{
 const adapter=aiCards({clientId:'fixture',request:async()=>{throw Error('must not call');}});
 assert.equal(await adapter.finish({message:{id:'no-staff'},card,text:'answer'}),false);
 assert.equal(await adapter.finish({message,card:{enabled:false},text:'answer'}),false);
});

test('long replies finish the card then use bounded text delivery',async()=>{
 const calls=[];const adapter=aiCards({clientId:'fixture',request:async(method,route,body)=>calls.push(body)});
 assert.equal(await adapter.finish({message,card,text:'x'.repeat(16001)}),false);
 assert.equal(calls.at(-1).cardData.cardParamMap.flowStatus,'3');
});

test('connector falls back to ordinary text when template or card permission fails',async()=>{
 const {create}=await import('../connectors/dingtalk.mjs');
 const original=globalThis.fetch,calls=[];
 class Client {config={};registerCallbackListener(){}disconnect(){}}
 const adapter=await create({config:{client_id:'fixture-app',client_secret:'fixture-secret'},require:()=>({DWClient:Client,TOPIC_ROBOT:'robot'}),incoming:async()=>{}});
 globalThis.fetch=async(url,request)=>{
  calls.push(url);
  if(url.includes('/oauth2/'))return {ok:true,json:async()=>({accessToken:'fixture-access',expireIn:7200})};
  if(url.includes('/card/'))return {ok:false,status:403,text:async()=>'{"code":"Forbidden"}'};
  return {ok:true,json:async()=>({errcode:0})};
 };
 try{
  const m={...message,session_webhook:'https://oapi.dingtalk.com/robot/sendBySession?session=fixture',webhook_expires:Date.now()+60000};
  assert.deepEqual(await adapter.progress({message:m,card,phase:'start'}),{supported:false});
  await adapter.send({message:m,card,text:'Fixture answer',final:true});
  assert.ok(calls.at(-1).includes('/robot/sendBySession'));
 }finally{globalThis.fetch=original;await adapter.close();}
});

for(const phase of ['stopped','failed','finish','long']) {
 test(`AI card writes terminal streaming content without prior deltas: ${phase}`,async()=>{
  const calls=[];let visibleContent='';
  const adapter=aiCards({clientId:'fixture',request:async(method,route,body)=>{
   calls.push({method,route,body});
   // A streaming component's content is separate from ordinary cardParamMap.
   if(route==='/v1.0/card/streaming')visibleContent=body.content;
   return {};
  }});
  const options={...card,field:'custom_content'};
  await adapter.progress({message,card:options,phase:'start'});
  const text=phase==='stopped'?'回答已被管理员终止':phase==='long'?'x'.repeat(16001):'Fixture final content';
  if(phase==='finish' || phase==='long')await adapter.finish({message,card:options,text});
  else await adapter.progress({message,card:options,phase,text});
  const frames=calls.filter(c=>c.route==='/v1.0/card/streaming');
  assert.equal(frames.length,1);
  assert.equal(frames[0].body.key,'custom_content');
  assert.equal(frames[0].body.isFull,true);
  assert.equal(frames[0].body.isFinalize,true);
  assert.equal(frames[0].body.isError,phase==='failed');
  assert.equal(visibleContent,phase==='long'?'回复较长，完整内容见下方消息。':text);
  assert.equal(calls.at(-1).body.cardData.cardParamMap.flowStatus,phase==='failed'?'5':'3');
  assert.equal(calls.filter(c=>c.method==='POST').length,2); // One create, one delivery.
  if(phase==='stopped'){
   const count=calls.length;
   await adapter.progress({message,card:options,phase,text});
   assert.equal(calls.length,count);
  }
 });
}

test('terminal stream failure is surfaced instead of marking an empty card finished',async()=>{
 const calls=[];
 const adapter=aiCards({clientId:'fixture',request:async(method,route,body)=>{
  calls.push({method,route,body});
  if(route==='/v1.0/card/streaming')throw Error('fixture stream failure');
  return {};
 }});
 await adapter.progress({message,card,phase:'start'});
 await assert.rejects(adapter.progress({message,card,phase:'stopped',text:'回答已被管理员终止'}),/fixture stream failure/);
 assert.equal(calls.some(c=>c.body.cardData?.cardParamMap.flowStatus==='3'),false);
});
