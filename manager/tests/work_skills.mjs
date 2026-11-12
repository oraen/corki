// Run only against the isolated work-skills QA home.
import assert from 'node:assert/strict';import {execFile} from 'node:child_process';import {promisify} from 'node:util';
const base=process.env.MANAGER_TEST_URL;assert(base);
assert((await(await fetch(base+'/api/config')).json()).path.includes('corki-work-skills-qa-'));
const run=promisify(execFile),session='work-skills-'+process.pid;
const browser=(...args)=>run('agent-browser',['--session',session,...args],{maxBuffer:1024*1024});
const evaluate=async text=>(await browser('eval',text)).stdout.trim();
const wait=async code=>{for(let i=0;i<60;i++){if(await evaluate(`Boolean(${code})`)==='true')return;await new Promise(r=>setTimeout(r,100));}throw Error(code);};
try{
 await browser('open',base+'/#skills');await browser('set','viewport','1440','900');
 await wait(`document.querySelectorAll('[data-skill-group]').length===4`);
 assert.equal(await evaluate(`Array.from(document.querySelectorAll('[data-skill-group]')).map(n=>n.dataset.skillGroup).join(',')`),'"corki,public,work,builtin"');
 assert.equal(await evaluate(`document.querySelector('[data-skill-group="work"] .skill-name[title="corki-text-C&E"]')!==null`),'true');
 assert.equal(await evaluate(`document.querySelector('[data-skill-group="builtin"] .skill-name[title="corki-text-C&E"]')===null`),'true');
 assert.equal(await evaluate(`document.querySelector('[data-skill-group="work"]').open`),'true');
 assert.equal(await evaluate(`document.querySelectorAll('[data-skill-group="work"] [role="switch"]').length`),'0');
 assert.equal(await evaluate(`document.querySelectorAll('[data-skill-group="corki"] [role="switch"]').length>0`),'true');
 await browser('click','[data-action="skill-import"]');
 await browser('click','input[name="skill-import-group"][value="work"]');
 await evaluate(`(()=>{const file=new File(['---\\nname: fixture-work-import\\ndescription: Work fixture\\n---\\nFixture body'],'SKILL.md',{type:'text/markdown'});const transfer=new DataTransfer();transfer.items.add(file);const input=document.querySelector('#skill-file-input');input.files=transfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));})()`);
 await wait(`!document.querySelector('#save').disabled`);await browser('click','#save');
 await wait(`!document.querySelector('#editor').open && !!document.querySelector('[data-skill-group="work"] .skill-name[title="fixture-work-import"]')`);
 if(process.env.MANAGER_TEST_SCREENSHOT)await browser('screenshot',process.env.MANAGER_TEST_SCREENSHOT);
 const snapshot=await(await fetch(base+'/api/snapshot')).json();
 const imported=snapshot.files.find(row=>row.name==='fixture-work-import');
 assert.equal(imported.group,'work');assert(imported.writable);assert(imported.path.includes('/skills/work/'));
 console.log('PASS work group order, migrated C&E placement, work import, writable classification');
}finally{await browser('close').catch(()=>{});}
