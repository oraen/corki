// Requires app/ development dependencies. Uses intercepted responses only.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const require=createRequire(new URL('../../app/package.json',import.meta.url));
const {chromium}=require('playwright');
const browser=await chromium.launch({headless:true,...(process.env.CORKI_TEST_BROWSER?{executablePath:process.env.CORKI_TEST_BROWSER}:{})});
try {
 const page=await browser.newPage({acceptDownloads:true});
 page.setDefaultTimeout(30000);
 await page.route('http://127.0.0.1:17003/**',route=>route.fulfill({
  status:200,contentType:route.request().url().includes('/api/')?'application/zip':'text/html',
  body:route.request().url().includes('/api/')?'fixture archive':'<!doctype html><html><body></body></html>',
 }));
 await page.goto('http://127.0.0.1:17003/');
 await page.addScriptTag({path:fileURLToPath(new URL('../static/skill-workspace.js',import.meta.url))});
 for(const root of ['/home/example/skills/示例技能','C:\\Users\\Example User\\skills\\示例技能','\\\\server\\share\\skills\\示例技能\\']) {
  await page.evaluate(async root=>{
   await window.CorkiSkills.open('fixture',{
    api:async endpoint=>endpoint.startsWith('skill-package')
     ?{name:'示例技能',source:'扩展',root,writable:true,entries:[{path:'SKILL.md',type:'file'}]}
     :{type:'text',content:'---\nname: example\n---\n',writable:true,revision:'fixture'},
    toast:()=>{},reload:()=>{},
   });
  },root);
  const downloadPromise=page.waitForEvent('download');
  await page.locator('[data-sw="export"]').click();
  const download=await downloadPromise;
  assert.equal(download.suggestedFilename(),'示例技能.zip');
  assert.equal(await download.failure(),null);
  await download.delete();
  await page.waitForFunction(()=>!window.CorkiSkills.exporting);
  await page.locator('[data-sw="close"]').click();
 }
 console.log('PASS actual Skills ZIP downloads for POSIX, Windows drive and UNC paths');
 await page.addScriptTag({path:fileURLToPath(new URL('../static/hooks-workspace.js',import.meta.url))});
 for(const path of ['/home/example/skills/example/SKILL.md','C:\\Users\\Example\\skills\\example\\SKILL.md','\\\\server\\share\\skills\\example\\SKILL.md']) {
  await page.evaluate(async path=>{
   window.api=async()=>({enabled:true,entries:[{enabled:true,trusted:true,event:'SessionStart',handler:{type:'skill',path}}]});
   let host=document.querySelector('#hooks-fixture');
   if(!host){host=document.createElement('div');host.id='hooks-fixture';document.body.append(host);}
   window.CorkiHooks.root=host;
   await window.CorkiHooks.load();
  },path);
  assert.equal(await page.locator('.hooks-command').textContent(),'example');
 }
 console.log('PASS Hook Skill names for POSIX, Windows drive and UNC paths');
} finally {await browser.close();}
