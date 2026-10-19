import {chromium} from '../../app/node_modules/playwright/index.mjs';
import assert from 'node:assert/strict';
import path from 'node:path';
const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
try {
 const page=await browser.newPage({viewport:{width:1280,height:800}});
 await page.setContent('<html><body></body></html>');
 await page.addStyleTag({path:path.resolve('manager/static/style.css')});
 await page.evaluate(()=>{
  window.icon=()=>'<svg width="18" height="18"><circle cx="9" cy="9" r="6"/></svg>';
  window.esc=s=>String(s);window.toast=()=>{};window.calls=[];
  window.api=async(method,data)=>{window.calls.push({method,data});return{message:'已启动'}};
 });
 await page.addScriptTag({path:path.resolve('manager/static/quick-tools.js')});
 await page.evaluate(()=>openQuickLaunch());
 assert(await page.locator('[name="launch-mode"][value="desktop"]').isChecked());
 await page.locator('[data-launch-mode] summary').click();
 await page.locator('.quick-option').filter({hasText:'桌面应用'}).click();
 assert.equal(await page.locator('[data-launch-mode] summary>span').innerText(),'桌面应用');
 await page.locator('#quick-directory').fill('/tmp/中文 工作目录');
 await page.screenshot({path:'/tmp/corki-manager-launch-mode.png'});
 await page.getByRole('button',{name:'启动 Corki',exact:true}).click();
 await page.waitForFunction(()=>!document.querySelector('.quick-dialog'));
 const request=await page.evaluate(()=>window.calls.at(-1).data);
 assert.equal(request.mode,'desktop');assert.equal(request.directory,'/tmp/中文 工作目录');
 await page.evaluate(()=>openQuickLaunch());
 await page.locator('[data-launch-mode] summary').click();
 await page.locator('.quick-option').filter({hasText:/^CLI$/}).click();
 await page.getByRole('button',{name:'启动 Corki',exact:true}).click();
 await page.waitForFunction(()=>!document.querySelector('.quick-dialog'));
 assert.equal(await page.evaluate(()=>window.calls.at(-1).data.mode),'cli');
 console.log('PASS quick launch: default desktop, CLI selection, directory payload and modal submission');
} finally {await browser.close();}
