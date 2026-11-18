// Isolated fixture only: native card dragging, confirmation and persisted identity.
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';

const base=process.env.MANAGER_TEST_URL;
assert(base);
const configuration=await (await fetch(base+'/api/config')).json();
assert(configuration.path.includes('corki-applications-qa-'));
const call=promisify(execFile),session='skill-move-'+process.pid;
const browser=(...args)=>call('agent-browser',['--session',session,...args],{maxBuffer:1024*1024});
const evaluate=async code=>JSON.parse((await browser('eval',code)).stdout.trim());
const snapshot=async()=>await (await fetch(base+'/api/snapshot')).json();
const name='drag-regression-'+process.pid;
const response=await fetch(base+'/api/mutate',{method:'POST',headers:{'Content-Type':'application/json','X-Corki-Manager':'1'},body:JSON.stringify({
 kind:'skills',action:'import',group:'public',files:[{path:'SKILL.md',data:Buffer.from(`---\nname: ${name}\ndescription: Isolated drag fixture\n---\nGuide\n`).toString('base64')}]
})});
assert(response.ok,await response.text());
const item=(await snapshot()).files.find(file=>file.kind==='skills'&&file.name===name);
assert(item);
const source=`[data-drag-skill="${item.id}"] .skill-avatar`;
try{
 await browser(...(process.env.CORKI_TEST_BROWSER?['--executable-path',process.env.CORKI_TEST_BROWSER]:[]),'open',base+'/#skills');
 await browser('set','viewport','1440','1100');
 await browser('wait','--fn',`!!document.querySelector('[data-drag-skill="${item.id}"]')`);
 assert.equal(await evaluate(`document.querySelectorAll('[data-skill-group="builtin"] [draggable="true"]').length`),0);
 await browser('drag',source,'[data-skill-group="builtin"] > summary');
 assert.equal(await evaluate(`document.querySelector('#editor').open`),false);
 // Collapsed groups remain valid targets; cancellation does not move anything.
 await browser('click','[data-skill-group="work"] > summary');
 await browser('drag',source,'[data-skill-group="work"] > summary');
 await browser('wait','--fn',`document.querySelector('#editor').open`);
 assert.match(await evaluate(`document.querySelector('#fields').textContent`),/公共 Skills → 工作 Skills/);
 await browser('click','#cancel-dialog');
 assert.equal((await snapshot()).files.find(file=>file.id===item.id).group,'public');
 await browser('drag',source,'[data-skill-group="work"] > summary');
 await browser('click','#save');
 await browser('wait','--fn',`!document.querySelector('#editor').open && !!document.querySelector('[data-skill-group="work"] [data-drag-skill="${item.id}"]')`);
 assert.equal((await snapshot()).files.find(file=>file.id===item.id).group,'work');
 assert.equal(await evaluate(`document.querySelectorAll('[data-skill-group="work"] .toggle').length`),0);
 await browser('drag',source,'[data-skill-group="corki"] > summary');
 await browser('click','#save');
 await browser('wait','--fn',`!document.querySelector('#editor').open && !!document.querySelector('[data-skill-group="corki"] [data-drag-skill="${item.id}"]')`);
 await browser('reload');
 await browser('wait','--fn',`!!document.querySelector('[data-skill-group="corki"] [data-drag-skill="${item.id}"]')`);
 assert.equal((await snapshot()).files.find(file=>file.id===item.id).group,'corki');
 console.log('PASS native dragging, built-in target rejection, collapsed drop, cancel, move, work semantics and reload identity');
}finally{await browser('close').catch(()=>{});}
