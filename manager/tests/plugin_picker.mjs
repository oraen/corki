// Use a disposable manager home; never create packages from personal resources.
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
const base=process.env.MANAGER_TEST_URL;assert(base);
const config=await(await fetch(base+'/api/config')).json();assert(config.path.includes('package-picker-qa-'));
const run=promisify(execFile), session='package-picker-'+process.pid;
const browser=(...args)=>run('agent-browser',['--session',session,...args],{maxBuffer:1024*1024});
const evaluate=async code=>(await browser('eval',code)).stdout.trim();
const wait=async expression=>{for(let i=0;i<60;i++){if(await evaluate(`Boolean(${expression})`)==='true')return;await new Promise(r=>setTimeout(r,100));}throw Error(expression);};
try {
 await browser('open',base+'/#plugins');
 await browser('set','viewport','1440','900');
 await wait(`!!document.querySelector('#package-create')`);
 await browser('click','#package-create');
 await wait(`document.querySelectorAll('.package-resource-section').length===4`);
 assert.equal(await evaluate(`!!document.querySelector('.package-resource-group summary, .package-create-form select')`),'false');
 assert.equal(await evaluate(`document.querySelectorAll('.package-resource-section')[0].textContent.includes('扩展 Skills')`),'true');
 assert.equal(await evaluate(`document.querySelectorAll('.package-resource-section')[1].textContent.includes('公共 Skills')`),'true');
 assert.equal(await evaluate(`document.querySelector('.package-create-form').textContent.includes('fixture-builtin')`),'false');
 await browser('click','[data-package-name="fixture-extension"]');
 assert.equal(await evaluate(`document.querySelector('[data-package-name="fixture-extension"] input').checked`),'true');
 await browser('fill','#package-resource-search','fixture-public');
 assert.equal(await evaluate(`document.querySelector('[data-package-name="fixture-extension"]').hidden`),'true');
 await evaluate(`document.querySelector('[data-package-name="fixture-public"]').scrollIntoView({block:'center'})`);
 await browser('click','[data-package-name="fixture-public"]');
 await browser('fill','#package-resource-search','');
 assert.equal(await evaluate(`document.querySelectorAll('input[name="resource:skills"]:checked').length`),'2');
 if(process.env.MANAGER_TEST_SCREENSHOT)await browser('screenshot',process.env.MANAGER_TEST_SCREENSHOT);
 await browser('fill','[name="package_name"]','Picker QA');
 await browser('click','#save');
 await wait(`!document.querySelector('#editor').open && document.querySelectorAll('.package-card').length===1`);
 const {packages}=await(await fetch(base+'/api/resource-packages')).json();
 assert.deepEqual(packages[0].skills.map(s=>s.name).sort(),['fixture-extension','fixture-public']);
 console.log('PASS grouped cards, built-in exclusion, search preserves selection and package creation');
}finally{await browser('close').catch(()=>{});}
