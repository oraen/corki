import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import test from 'node:test';

const source=await readFile(new URL('../static/skills.js',import.meta.url),'utf8');
function fixture(){
 const nodes=new Map();
 const node=selector=>{
  if(!nodes.has(selector))nodes.set(selector,{open:true,classList:{add(){},remove(){}},addEventListener(){},value:'public',textContent:'',innerHTML:''});
  return nodes.get(selector);
 };
 const requests=[];
 const context=vm.createContext({
  document:{querySelector:node,querySelectorAll:()=>[]},$:node,
  showDialog(){},esc:value=>value,size:value=>value,
  api:async(_,body)=>{requests.push(body);return {name:'fixture',description:'Fixture',files:body.files.map(f=>({path:f.path,bytes:1})),bytes:1};},
  FileReader:class{readAsDataURL(file){this.result='data:;base64,'+Buffer.from(file.body).toString('base64');queueMicrotask(()=>this.onload());}},
 });
 vm.runInContext(source,context);vm.runInContext('importSkills()',context);
 return {node,requests,drop(entries){node('#skill-dropzone').ondrop({preventDefault(){},dataTransfer:{items:entries.map(entry=>({kind:'file',webkitGetAsEntry:()=>entry})),files:[]}});}};
}
const file=(name,body='fixture')=>({name,isFile:true,file:resolve=>resolve({name,body})});
const directory=(name,batches)=>({name,isDirectory:true,createReader(){let index=0;return {readEntries:resolve=>resolve(batches[index++]||[])};}});
async function settled(condition){for(let i=0;i<30;i++){if(condition())return;await new Promise(resolve=>setImmediate(resolve));}throw Error('Import did not settle');}

test('folder drop preserves nested paths and reads every directory batch',async()=>{
 const f=fixture();
 f.drop([directory('fixture',[
  [file('SKILL.md','---\nname: fixture\n---')],
  [directory('scripts',[[file('run.py','print(1)')]]),file('.DS_Store')],
 ])]);
 await settled(()=>!f.node('#save').disabled);
 assert.deepEqual(Array.from(f.requests[0].files,f=>f.path),['fixture/SKILL.md','fixture/scripts/run.py']);
 assert.equal(f.requests[0].files[1].data,Buffer.from('print(1)').toString('base64'));
 assert.equal(f.requests[0].action,'validate-import');
});

test('directory read errors prevent import and display the reason',async()=>{
 const f=fixture();
 f.drop([{name:'fixture',isDirectory:true,createReader(){return {readEntries:(_,reject)=>reject(Error('Permission denied'))};}}]);
 await settled(()=>f.node('#form-error').textContent);
 assert.equal(f.node('#form-error').textContent,'Permission denied');
 assert.equal(f.node('#save').disabled,true);
 assert.equal(f.requests.length,0);
});

test('stale directory reads cannot replace a newer selection',async()=>{
 const f=fixture();let resolveRead;
 f.drop([{name:'old',isFile:true,file:resolve=>{resolveRead=resolve;}},directory('empty',[])]);
 f.drop([directory('new',[[file('SKILL.md')]])]);
 await settled(()=>!f.node('#save').disabled);
 resolveRead({name:'SKILL.md',body:'old'});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(f.requests.length,1);
 assert.equal(f.requests[0].files[0].path,'new/SKILL.md');
});
