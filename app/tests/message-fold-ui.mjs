// Focused real-browser layout checks. Run after npm run build.
import { build } from 'esbuild';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import os from 'node:os';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
const exec = promisify(execFile);
const dir = await mkdtemp(path.join(os.tmpdir(), 'corki-message-fold-'));
const session = `corki-fold-${process.pid}`;
const run = (...args) => exec('agent-browser', ['--session', session, ...args], {maxBuffer: 1024 * 1024});
await build({stdin:{contents:`
import React from 'react';
import {createRoot} from 'react-dom/client';
import {MessageRow} from ${JSON.stringify(path.resolve('renderer/components.tsx'))};
window.corki={copy:async()=>{}};
const fixtures=[
 ['user-short','user','你好'],['agent-short','assistant','**你好**，可以开始了。'],
 ['user-wrap','user','请帮我核对当前配置并详细介绍供应商与模型的选择方式。'.repeat(5)],
 ['agent-wrap','assistant','这里是一段随可用宽度自动换行的回复文字，用来检查宽度变化时是否准确。'.repeat(4)],
 ['user-newline','user','第一行\\n第二行'],['agent-paragraph','assistant','第一段\\n\\n第二段'],
 ['agent-code','assistant',String.fromCharCode(96).repeat(3)+'js\\nconst first = 1;\\nconst second = 2;\\n'+String.fromCharCode(96).repeat(3)]
];
createRoot(document.getElementById('root')).render(<main>{fixtures.map(([id,role,content])=>
<section id={id} className="reply-block" key={id}><MessageRow message={{id,role,content}} collapsible onError={e=>{throw e}}/></section>)}</main>);
`,resolveDir:process.cwd(),loader:'jsx'},outfile:path.join(dir,'fixture.js'),bundle:true,platform:'browser',format:'iife'});
const html=`<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/style.css"><style>body{display:block;overflow:auto}main{width:650px;margin:30px auto}.reply-block{border-bottom:1px solid #eee}.message.assistant .message-fold{position:static;float:right}</style></head><body><div id="root"></div><script src="/fixture.js"></script></body></html>`;
const server=http.createServer(async(req,res)=>{
 try{
  if(req.url==='/'){res.setHeader('Content-Type','text/html');return res.end(html)}
  const file=req.url==='/fixture.js'?path.join(dir,'fixture.js'):req.url==='/style.css'?path.resolve('dist/renderer.css'):path.resolve('dist','.'+req.url);
  res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'image/png');res.end(await readFile(file));
 }catch{res.writeHead(404).end()}
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
async function check(expression, label){
 await run('eval',`(async()=>{for(let i=0;i<80;i++){if(${expression})return true;await new Promise(r=>setTimeout(r,25))}throw Error(${JSON.stringify(label)})})()`);
 console.log('PASS',label);
}
try{
 await run('open',`http://127.0.0.1:${server.address().port}/`);
 await run('snapshot','-i');
 await check(`document.querySelector('#user-wrap .message-fold') && document.querySelector('#agent-wrap .message-fold')`,'wrapped user and assistant messages can fold');
 await check(`!document.querySelector('#user-short .message-fold') && !document.querySelector('#agent-short .message-fold')`,'single lines, including inline Markdown, have no fold control');
 await check(`document.querySelector('#user-newline .message-fold') && document.querySelector('#agent-paragraph .message-fold') && document.querySelector('#agent-code .message-fold')`,'explicit line breaks, paragraphs and multiline code can fold');
 await run('click','#user-wrap .message-fold');
 await run('click','#agent-wrap .message-fold');
 await check(`document.querySelectorAll('.is-folded').length===2`,'both roles fold without losing full text layout');
 await run('eval',`document.querySelector('main').style.width='5000px'`);
 await check(`!document.querySelector('#user-wrap .message-fold') && !document.querySelector('#agent-wrap .message-fold') && !document.querySelector('.is-folded')`,'widening folded messages automatically unfolds and hides controls');
 await run('eval',`document.querySelector('main').style.width='650px'`);
 await check(`document.querySelector('#user-wrap .message-fold') && document.querySelector('#agent-wrap .message-fold') && !document.querySelector('.is-folded')`,'narrowing restores controls without collapsing messages');
 await run('screenshot',path.join(os.tmpdir(),'corki-message-fold-layout.png'));
 await run('click','#agent-wrap .message-fold');
 await check(`document.querySelector('#agent-wrap .is-folded')`,'folding still works after repeated resize');
 await run('eval',`document.querySelector('main').style.width='calc(100vw - 100px)'`);
 await run('set','viewport','3840','900');
 await check(`!document.querySelector('#user-wrap .message-fold') && !document.querySelector('#agent-wrap .message-fold')`,'widening the browser window updates both roles');
 await run('set','viewport','960','800');
 await check(`document.querySelector('#user-wrap .message-fold') && document.querySelector('#agent-wrap .message-fold')`,'narrowing the browser window updates both roles');

}finally{
 await run('close').catch(()=>{});server.close();await rm(dir,{recursive:true,force:true});
}
