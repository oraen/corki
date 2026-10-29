// Disposable renderer fixture; no actual user home or model requests.
import assert from 'node:assert/strict';
import http from 'node:http';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
const run=promisify(execFile),session='robot-history-'+process.pid;
const browser=(...args)=>run('agent-browser',['--session',session,...args],{maxBuffer:1024*1024});
const value=async code=>JSON.parse((await browser('eval',code)).stdout.trim());
const wait=async code=>{for(let i=0;i<60;i++){if(await value(`Boolean(${code})`))return;await new Promise(r=>setTimeout(r,100));}throw Error(code);};
const fixture=`window.robotLoads=[];window.corki={request:async(method,p={})=>{
 if(method==='initialize')return{instance:'fixture',sequence:0,threadManagementVersion:2};
 if(method==='state.read')return null;
 if(method==='state.write')return{};
 if(method==='config.read')return{valid:true,default:{provider:'fixture',model:'fixture'},providers:[{id:'fixture',models:[{id:'fixture',reasoningLevels:[],images:false}]}]};
 if(method==='threads.list'){
  if(p.group==='robot'){window.robotLoads.push(p);return{threads:[{id:p.offset?'robot-tail':'robot-first',title:p.offset?'机器人旧会话':'机器人新会话',cwd:'/tmp/shared'}],next:p.offset?null:40,directories:[]};}
  return{threads:[{id:'manual',title:'普通会话',cwd:'/tmp/shared'}],next:null,directories:[{path:'/tmp/shared',name:'shared'}]};
 }
 if(method==='threads.read')return{threadId:p.threadId,instance:'fixture',sequence:0,messages:[],next:null,turns:[],live:[],pending:[],running:false,directory:'/tmp/shared'};
},subscribe:()=>()=>{},copy:async()=>{},openLink:async()=>{}};`;
const root=path.resolve('dist');
const server=http.createServer(async(req,res)=>{
 try{
  if(req.url==='/fixture-api.js'){res.setHeader('Content-Type','text/javascript');res.end(fixture);return;}
  const file=path.resolve(root,'.'+new URL(req.url,'http://localhost').pathname);
  if(!file.startsWith(root+path.sep))throw Error();
  let body=await readFile(file);
  if(file.endsWith('index.html'))body=body.toString().replace('<script src="renderer.js">','<script src="fixture-api.js"></script><script src="renderer.js">');
  res.setHeader('Content-Type',file.endsWith('.html')?'text/html':file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.svg')?'image/svg+xml':file.endsWith('.png')?'image/png':'application/octet-stream');res.end(body);
 }catch{res.writeHead(404).end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
try{
 await browser('open',`http://127.0.0.1:${server.address().port}/index.html`);
 await browser('set','viewport','1440','900');
 await wait(`Array.from(document.querySelectorAll('.history-heading')).some(n=>n.textContent.includes('机器人会话'))`);
 assert.equal(await value('window.robotLoads.length'),0);
 const selector='[aria-label="机器人会话"]';
 await browser('click',selector+' .history-heading');
 await wait(`document.querySelector('${selector}').textContent.includes('机器人新会话')`);
 assert.equal(await value('window.robotLoads.length'),1);
 assert.equal(await value(`document.querySelector('[aria-label="最近会话"]').textContent.includes('机器人新会话')`),false);
 await browser('click',selector+' .more-history');
 await wait(`document.querySelector('${selector}').textContent.includes('机器人旧会话')`);
 assert.deepEqual(await value('window.robotLoads.map(p=>p.offset)'),[0,40]);
 if(process.env.ROBOT_HISTORY_SCREENSHOT)await browser('screenshot',process.env.ROBOT_HISTORY_SCREENSHOT);
 await browser('click',selector+' .history-heading');
 assert.equal(await value(`Boolean(document.querySelector('${selector} .history-children'))`),false);
 await browser('click','[title="/tmp/shared"]');
 await wait(`document.querySelector('[aria-label="/tmp/shared"]').textContent.includes('普通会话')`);
 assert.equal(await value(`document.querySelector('[aria-label="/tmp/shared"]').textContent.includes('机器人新会话')`),false);
 console.log('PASS robot fold, lazy loading, pagination, separate recent/workspace groups');
}finally{await browser('close').catch(()=>{});await new Promise(resolve=>server.close(resolve));}
