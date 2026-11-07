import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {mkdtemp,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const tmp=await mkdtemp(path.join(os.tmpdir(),'corki-sound-tests-'));
await build({entryPoints:['electron/sound-notifications.ts'],outfile:path.join(tmp,'sounds.mjs'),bundle:true,format:'esm',platform:'node'});
const {SoundNotifications}=await import(pathToFileURL(path.join(tmp,'sounds.mjs')));
test.after(()=>rm(tmp,{recursive:true,force:true}));
const wait=()=>new Promise(resolve=>setTimeout(resolve,15));
function fixture(play){
 const heard=[];const sounds=new SoundNotifications(play||((kind)=>heard.push(kind)),3);
 let sequence=0;
 const emit=(type,data={},threadId='chat',instance='host')=>sounds.accept({instance,sequence:++sequence,type,threadId,data});
 return {sounds,heard,emit};
}
test('one completion for repeated terminal and settled events',async()=>{
 const {sounds,heard,emit}=fixture();
 emit('TurnStarted',{turn_id:'one'});emit('TurnCompleted',{turn_id:'one'});emit('run.settled');
 emit('TurnCompleted',{turn_id:'one'});await wait();assert.deepEqual(heard,['complete']);sounds.dispose();
});
test('failure supersedes success and host error duplicates do not ring again',async()=>{
 const {sounds,heard,emit}=fixture();emit('TurnStarted',{turn_id:'one'});
 emit('TurnCompleted',{turn_id:'one'});emit('TurnFailed',{turn_id:'one'});emit('host.error');
 await wait();emit('host.error');await wait();assert.deepEqual(heard,['error']);sounds.dispose();
});
test('approval, elicitation and questions remind once per request; resolving allows completion',async()=>{
 const {sounds,heard,emit}=fixture();emit('TurnStarted',{turn_id:'one'});
 emit('approval',{request_id:'approval'});await wait();emit('approval',{request_id:'approval'});
 emit('interaction.resolved',{requestId:'approval'});await wait();
 emit('UserInputRequested',{call_id:'question',is_blocking:false});await wait();
 emit('interaction.resolved',{requestId:'question'});emit('TurnCompleted',{turn_id:'one'});
 await wait();assert.deepEqual(heard,['attention','attention','complete']);sounds.dispose();
});
test('unanswered async input keeps attention instead of success',async()=>{
 const {sounds,heard,emit}=fixture();emit('UserInputRequested',{call_id:'q'});emit('TurnCompleted',{turn_id:'one'});
 await wait();assert.deepEqual(heard,['attention']);sounds.dispose();
});
test('manual cancellation and model/tool retry stay silent',async()=>{
 const {sounds,heard,emit}=fixture();emit('TurnStarted',{turn_id:'one'});
 emit('ModelRetryScheduled');emit('model.health',{warning:true,reason:'connection'});emit('ToolCallCompleted',{is_error:true});
 emit('TurnCancelled',{turn_id:'one'});emit('run.settled');await wait();assert.deepEqual(heard,[]);sounds.dispose();
});
test('active Goal iterations stay silent; completion and blocked/budget/usage states notify',async()=>{
 for(const [status,expected] of [['active',[]],['paused',[]],['complete',['complete']],['blocked',['attention']],['budget_limited',['attention']],['usage_limited',['attention']]]){
  const {sounds,heard,emit}=fixture();emit('GoalUpdated',{status});emit('TurnCompleted',{turn_id:'one'});
  await wait();assert.deepEqual(heard,expected,status);sounds.dispose();
 }
});
test('concurrent bursts coalesce by priority, later completions still notify',async()=>{
 const {sounds,heard,emit}=fixture();emit('TurnCompleted',{turn_id:'one'},'a');emit('approval',{request_id:'r'},'b');emit('TurnFailed',{turn_id:'one'},'c');
 await wait();emit('TurnCompleted',{turn_id:'two'},'d');await wait();assert.deepEqual(heard,['error','complete']);sounds.dispose();
});
test('new turn cancels stale queued completion of same conversation',async()=>{
 const {sounds,heard,emit}=fixture();emit('TurnStarted',{turn_id:'one'});emit('TurnCompleted',{turn_id:'one'});
 emit('TurnStarted',{turn_id:'two'});await wait();assert.deepEqual(heard,[]);
 emit('TurnCompleted',{turn_id:'two'});await wait();assert.deepEqual(heard,['complete']);sounds.dispose();
});
test('duplicate/out-of-order events are ignored and new host epochs reset deduplication',async()=>{
 const {sounds,heard}=fixture();const event={instance:'a',sequence:3,threadId:'chat',type:'TurnCompleted',data:{turn_id:'one'}};
 sounds.accept(event);sounds.accept(event);sounds.accept({...event,sequence:2,type:'TurnFailed'});await wait();
 sounds.accept({...event,instance:'b',sequence:1});await wait();assert.deepEqual(heard,['complete','complete']);sounds.dispose();
});
test('backend disconnect rings once and supersedes pending completion',async()=>{
 const {sounds,heard,emit}=fixture();emit('TurnCompleted',{turn_id:'one'});sounds.disconnected();sounds.disconnected();
 await wait();sounds.disconnected();await wait();assert.deepEqual(heard,['error']);
 emit('TurnStarted',{turn_id:'two'});sounds.disconnected();await wait();assert.deepEqual(heard,['error','error']);sounds.dispose();
});
test('mute discards pending sound and never replays events after unmuting',async()=>{
 const {sounds,heard,emit}=fixture();emit('TurnCompleted',{turn_id:'one'});sounds.setEnabled(false);await wait();
 emit('TurnFailed',{turn_id:'two'});sounds.setEnabled(true);await wait();assert.deepEqual(heard,[]);
 emit('TurnStarted',{turn_id:'three'});emit('TurnCompleted',{turn_id:'three'});await wait();assert.deepEqual(heard,['complete']);sounds.dispose();
});
test('failed queued messages and rejected submissions are deduplicated',async()=>{
 const {sounds,heard,emit}=fixture();const message={id:'q',state:'failed'};
 emit('queue.changed',{messages:[message]});await wait();emit('queue.changed',{messages:[message]});await wait();
 sounds.submissionFailed('one');sounds.submissionFailed('one');await wait();assert.deepEqual(heard,['error','error']);sounds.dispose();
});
test('audio failure is contained and disposal drops queued notifications',async()=>{
 const {sounds,emit}=fixture(()=>{throw new Error('device unavailable');});emit('TurnCompleted',{turn_id:'one'});await wait();
 sounds.dispose();const next=fixture();next.emit('TurnCompleted',{turn_id:'one'});next.sounds.dispose();await wait();assert.deepEqual(next.heard,[]);
});
