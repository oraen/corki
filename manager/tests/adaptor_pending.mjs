import vm from 'node:vm';
import fs from 'node:fs';
import assert from 'node:assert/strict';
const elements=new Map();
function element(){return {disabled:false,innerHTML:'',textContent:'',dataset:{},parentElement:{classList:{toggle(){}},dataset:{},removeAttribute(){},setAttribute(){}},classList:{toggle(){},add(){},remove(){}},setAttribute(){},getAttribute(){return 'false'},focus(){},querySelector(){return element()}}}
const power=element(),status=element(),port=element();
elements.set('[data-power]',power);elements.set('.adaptor-status',status);elements.set('[data-gateway-port]',port);
const root={set innerHTML(html){this.html=html;const match=html.match(/data-power>([^<]*)/);if(match)power.textContent=match[1]},get innerHTML(){return this.html},querySelector:s=>elements.get(s)||element(),querySelectorAll:()=>[]};
let resolveMutation,mutations=0,toasts=[];
const data={running:false,port:null,revision:'test',providers:[]};
const api=async(action,body)=>{
 if(action==='adaptor')return {...data};
 mutations++;await new Promise(r=>resolveMutation=r);data.running=body.enabled;
 return {gateway:{...data},message:'done'};
};
const context={window:{},document:{hidden:false,createElement(){throw Error('blocking dialog created')}},setInterval:()=>1,clearInterval(){},console};
vm.createContext(context);vm.runInContext(fs.readFileSync('manager/static/adaptor.js','utf8'),context);
const tick=()=>new Promise(r=>setImmediate(r));
const ui=context.window.CorkiAdaptor;
ui.mount(root,{api,toast:v=>toasts.push(v),refreshSnapshot:async()=>{}});await tick();
power.onclick();await tick();assert.equal(power.disabled,true);assert.equal(power.textContent,'开启中');assert(status.innerHTML.includes('spinner'));assert.equal(mutations,1);
power.onclick();await tick();assert.equal(mutations,1);
ui.unmount();ui.mount(root,{api,toast:v=>toasts.push(v),refreshSnapshot:async()=>{}});await tick();assert.equal(power.textContent,'开启中');assert.equal(power.disabled,true);
resolveMutation();await tick();await tick();assert.equal(power.disabled,false);assert.equal(power.textContent,'关闭网关');assert.deepEqual(toasts,['done']);
power.onclick();await tick();assert.equal(power.textContent,'关闭中');resolveMutation();await tick();await tick();assert.equal(power.disabled,false);assert.equal(power.textContent,'开启网关');ui.unmount();
ui.mount(root,{api:async(action)=>{if(action==='mutate')throw Error('fixture failure');return {...data}},toast:v=>toasts.push(v),refreshSnapshot:async()=>{}});
await tick();power.onclick();await tick();await tick();assert.equal(power.disabled,false);assert.equal(power.textContent,'开启网关');assert.equal(toasts.at(-1),'fixture failure');ui.unmount();
console.log('PASS nonmodal start/stop, duplicate guard, navigation and completion');
