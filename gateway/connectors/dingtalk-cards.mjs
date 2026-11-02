// DingTalk's official CardReplier/AICardReplier wire contract, without another SDK.
import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, writeFile, rename } from 'node:fs/promises';
import path from 'node:path';

export function aiCards({ clientId, directory, request }) {
  const states = new Map(), locks = new Map();
  const digest = text => createHash('sha256').update(text).digest('hex');
  const keyFor = message => digest(clientId + '\0' + message.id);
  const filename = key => path.join(directory, 'cards', key + '.json');
  async function load(key) {
    if(states.has(key)) return states.get(key);
    if(!directory) return null;
    try {
      const value=JSON.parse(await readFile(filename(key),'utf8'));
      if(value.id!=='corki-'+key || typeof value.template!=='string') return null;
      states.set(key,value); return value;
    } catch(error) { if(error.code==='ENOENT') return null; throw error; }
  }
  async function save(key,value) {
    states.set(key,value);
    while(states.size>100) states.delete(states.keys().next().value);
    if(!directory) return;
    await mkdir(path.dirname(filename(key)),{recursive:true,mode:0o700});
    const temporary=filename(key)+'.'+randomUUID()+'.tmp';
    await writeFile(temporary,JSON.stringify(value),{mode:0o600});
    await rename(temporary,filename(key));
  }
  async function serial(message,fn) {
    const key=keyFor(message), previous=locks.get(key) || Promise.resolve();
    const task=previous.catch(()=>{}).then(()=>fn(key)); locks.set(key,task);
    try{return await task;} finally{if(locks.get(key)===task)locks.delete(key);}
  }
  async function ensure(key,message,options) {
    let value=await load(key);
    if(!value) {
      if(options?.enabled!==true || !message.staff_id || !options.template) return null;
      value={id:'corki-'+key,template:options.template,field:options.field || 'content',delivered:false};
      await request('POST','/v1.0/card/instances',{
        cardTemplateId:value.template,outTrackId:value.id,
        cardData:{cardParamMap:{flowStatus:'1',[value.field]:'Corki 正在处理…'}},
        callbackType:'STREAM',imRobotOpenSpaceModel:{supportForward:true},
      });
      await save(key,value);
    }
    if(!value.delivered) {
      const delivery=await request('POST','/v1.0/card/instances/deliver',{
        outTrackId:value.id,userIdType:1,
        openSpaceId:'dtv1.card//IM_ROBOT.'+message.staff_id,
        imRobotOpenDeliverModel:{spaceType:'IM_ROBOT'},
      });
      if(delivery?.success===false || delivery?.result?.some(r=>r.success===false))
        throw Error('DingTalk card delivery rejected');
      const carriers=(delivery?.result || []).filter(r=>r.success===true && r.spaceType==='IM_ROBOT' && typeof r.carrierId==='string' && r.carrierId);
      const receipt=carriers.length===1?{robot_code:clientId,keys:[carriers[0].carrierId],complete:true}:null;
      value={...value,delivered:true,...(receipt?{receipt}:{})};await save(key,value);
    }
    return value;
  }
  async function update(value,state,text) {
    await request('PUT','/v1.0/card/instances',{
      outTrackId:value.id,cardData:{cardParamMap:{flowStatus:state,[value.field]:text}},
      cardUpdateOptions:{updateCardDataByKey:true},
    });
  }
  return {
    async receipt(message) {return (await load(keyFor(message)))?.receipt || null;},
    async progress({message,card,phase,text=''}) {
      return serial(message,async key=>{
        const value=await ensure(key,message,card);
        if(!value || value.finished) return {supported:!!value,...(value?.receipt?{receipt:value.receipt}:{})};
        if(phase==='failed' || phase==='stopped') {
          // Streaming components need a final content frame even when the model
          // produced no deltas. Updating cardParamMap alone can leave them blank.
          await request('PUT','/v1.0/card/streaming',{
            outTrackId:value.id,guid:randomUUID(),key:value.field,content:text || '回答已停止',
            isFull:true,isFinalize:true,isError:phase==='failed',
          });
          await update(value,phase==='failed'?'5':'3',text || (phase==='failed'?'本次任务未完成，请稍后重试。':'回答已停止'));
          await save(key,{...value,streaming:true,finished:true});
        } else if(phase==='answer' && text && text.length<=16000) {
          await request('PUT','/v1.0/card/streaming',{
            outTrackId:value.id,guid:randomUUID(),key:value.field,content:text,
            isFull:true,isFinalize:false,isError:false,
          });
          if(!value.streaming) await save(key,{...value,streaming:true});
        } else if(phase!=='start') {
          const content=phase==='waiting'?'等待补充信息或确认，请在 Corki 管理页面处理。':'Corki 正在处理…';
          await update(value,'1',content);
        }
        return {supported:true,...(value.receipt?{receipt:value.receipt}:{})};
      });
    },
    async finish({message,card,text}) {
      return serial(message,async key=>{
        const value=await ensure(key,message,card);
        if(!value) return false;
        const hash=digest(text);
        if(value.sent===hash) return true;
        // Keep long replies on the existing bounded text transport.
        if(text.length>16000) {
          await request('PUT','/v1.0/card/streaming',{
            outTrackId:value.id,guid:randomUUID(),key:value.field,
            content:'回复较长，完整内容见下方消息。',isFull:true,isFinalize:true,isError:false,
          });
          await update(value,'3','回复较长，完整内容见下方消息。');
          await save(key,{...value,streaming:true,finished:true});return false;
        }
        await request('PUT','/v1.0/card/streaming',{
          outTrackId:value.id,guid:randomUUID(),key:value.field,content:text,
          isFull:true,isFinalize:true,isError:false,
        });
        await update(value,'3',text);
        await save(key,{...value,streaming:true,finished:true,sent:hash});
        return true;
      });
    },
  };
}
