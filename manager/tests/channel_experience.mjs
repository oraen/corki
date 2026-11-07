// Isolated product flow and visual checks: no real credentials or messages.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFile, mkdir } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
const require = createRequire(
  new URL("../../app/package.json", import.meta.url),
);
const { chromium } = require("playwright");
const browser = await chromium.launch({
  headless: true,
  executablePath:
    process.env.CORKI_TEST_BROWSER ||
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
});
const artifacts =
  process.env.CORKI_TEST_ARTIFACT_DIR ||
  path.join(os.tmpdir(), "corki-channel-experience");
await mkdir(artifacts, { recursive: true });
try {
  const page = await browser.newPage({
    viewport: { width: 1280, height: 1000 },
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("https://corki.test/**", async (route) => {
    const url = new URL(route.request().url());
    if(url.pathname==='/fixtures/wechat-video.mp4')return route.fulfill({body:await readFile('gateway/tests/fixtures/wechat-video.mp4'),contentType:'video/mp4'});
    if (
      url.pathname.startsWith("/channel-icons/") ||
      ["/corki-icon-ears.png", "/chat-crab.jpg"].includes(url.pathname)
    ) {
      return route.fulfill({
        body: await readFile(path.join("manager/static", url.pathname)),
        contentType: url.pathname.endsWith(".svg") ? "image/svg+xml" : url.pathname.endsWith('.jpg') ? 'image/jpeg' : "image/png",
      });
    }
    return route.fulfill({
      contentType: "text/html",
      body: '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head><body><main id="root" style="max-width:1100px;margin:40px auto;padding:16px"></main></body></html>',
    });
  });
  await page.goto("https://corki.test/");
  for (const file of ["style.css", "channels.css", "channel-experience.css"])
    await page.addStyleTag({ path: path.resolve("manager/static", file) });
  for (const file of ["channel-experience.js", "channels.js"])
    await page.addScriptTag({ path: path.resolve("manager/static", file) });
  await page.evaluate(() => {
    const interval = window.setInterval.bind(window);
    window.setInterval = (fn, ms) => interval(fn, Math.min(ms, 80));
    window.mutations = [];
    window.holdQR = false;
    window.fixture = {
      running: true,
      active: 0,
      interactions: [],
      installed: { wechat: true, feishu: true, dingtalk: true, telegram:true, whatsapp:true },
      package_jobs: {},
      channels: [
        { id: "wechat", name: "微信", builtin: true, fields: [] },
        {id:'telegram',name:'Telegram',builtin:true,fields:[{key:'token',label:'Bot Token',secret:true,required:true}]},
        {id:'whatsapp',name:'WhatsApp',builtin:true,fields:[]},
        {
          id: "feishu",
          name: "飞书",
          builtin: true,
          fields: [
            { key: "app_id", label: "App ID", required: true },
            {
              key: "app_secret",
              label: "App Secret",
              required: true,
              secret: true,
            },
            { key: "region", label: "平台", options: ["飞书", "Lark"] },
          ],
        },
        {
          id: "dingtalk",
          name: "钉钉",
          builtin: true,
          fields: [
            { key: "client_id", label: "Client ID（AppKey）", required: true },
            {
              key: "client_secret",
              label: "Client Secret（AppSecret）",
              secret: true,
              required: true,
            },
          ],
        },
      ],
      connections: [
        {id:'tg',name:'Telegram 客服',channel:'telegram',enabled:true,values:{session_validation:'true'}},
        {id:'wa',name:'WhatsApp 客服',channel:'whatsapp',enabled:true,values:{session_validation:'true'}},
        {
          id: "wx",
          name: "客户服务微信",
          channel: "wechat",
          enabled: true,
          values: { session_validation: "true" },
        },
        {
          id: "fs",
          name: "团队助手",
          channel: "feishu",
          enabled: false,
          values: {
            app_id: "cli_0123456789abcdef",
            session_validation: "true",
          },
        },
        {
          id: "dt",
          name: "钉钉",
          channel: "dingtalk",
          enabled: true,
          values: {},
        },
      ],
      statuses: { wx: { state: "login" }, dt: { state: "online" },tg:{state:'online'},wa:{state:'login'} },
    };
    window.host = {
      providers: [],
      toast: () => {},
      api: async (endpoint, body) => {
        if (endpoint === "mutate") {
          if (body.action === "applications")
            return { requests: structuredClone(window.requests || []), before: null };
          if (body.action === "conversation")
            return structuredClone(window.historyFixture || {thread:null,sessions:[],messages:[],before:null});
          if (body.action === "application-status")
            window.requests.find(r=>r.sender_id===body.sender).status=body.status;
          window.mutations.push({
            ...body,
            code: body.code ? "[redacted]" : undefined,
          });
          if (body.action === "image-upload") {
            if(window.holdUpload)await new Promise(resolve=>window.releaseUpload=resolve);
            if(window.failUpload)throw new Error('图片上传失败');
            const url='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80"><rect width="120" height="80" fill="#719d88"/></svg>');
            window.imageFixtureUrl=url;return {image:{id:'fixture-image',status:'ready',url,thumbnail:url}};
          }
          if (body.action === "file-upload") {
            const file={id:'fixture-'+body.type,status:'ready',kind:body.type,name:body.name,size:2048,
              url:body.type==='video'?'/fixtures/wechat-video.mp4':'/fixtures/document.txt'};
            window.fileFixtures ||= {};window.fileFixtures[file.id]=file;return {file};
          }
          if (body.action === "manual-send") {
            if(window.holdSend)await new Promise(resolve=>window.releaseSend=resolve);
            const row={id:'wxmsg:'+window.mutations.length,role:'assistant',kind:'manual',content:body.text,
              created_at:new Date().toISOString(),status:window.failSend?'failed':'sent',
              delivery_failed:!!window.failSend,error:window.failSend?'微信未接受此次发送':'',
              images:(body.images || []).map(id=>({id,status:'ready',url:window.imageFixtureUrl,thumbnail:window.imageFixtureUrl})),
              files:(body.files || []).map(id=>window.fileFixtures[id])};
            window.historyFixture.messages.push(row);
            return {message:row};
          }
          if (body.action === "wechat-login" || body.action==='channel-login') {
            if (window.holdQR)
              await new Promise((resolve) => (window.releaseQR = resolve));
            window.fixture.statuses[body.id] = {
              state: "qr",
              qr:
                "data:image/svg+xml," +
                encodeURIComponent(
                  '<svg xmlns="http://www.w3.org/2000/svg" width="232" height="232"><rect width="232" height="232" fill="white"/><path d="M10 10h60v60H10zM160 10h60v60h-60zM10 160h60v60H10zM100 100h30v30h-30zM160 160h60v60h-60z" fill="#31342d"/></svg>',
                ),
            };
          }
          if (body.action === "channel")
            window.fixture.connections.find((c) => c.id === body.id).enabled =
              body.enabled;
          if (body.action === "connection-reconnect")
            window.fixture.statuses[body.id] = { state: "connecting" };
        }
        return structuredClone(window.fixture);
      },
    };
    window.mount = (id,conversation) =>
      window.CorkiChannels.mount(document.querySelector("#root"), {
        ...window.host,
        id,
        conversation,
      });
    window.mount();
  });
  await page.locator('[data-login="wx"]').waitFor();
  await page.evaluate(() => (window.holdQR = true));
  await page.locator('[data-login="wx"]').click();
  await page.locator(".channel-qr-loading").waitFor();
  await page.screenshot({ path: path.join(artifacts, "wechat-loading.png") });
  await page.evaluate(() => {
    window.holdQR = false;
    window.releaseQR();
  });
  await page.locator(".channel-qr-frame>img").waitFor();
  await page.screenshot({ path: path.join(artifacts, "wechat-qr.png") });
  const scanLayout = () =>
    page.locator(".channel-wechat-login").evaluate((dialog) => {
      const rect = (node) => {
        const r = node.getBoundingClientRect();
        return { x: r.x, y: r.y, width: r.width, height: r.height };
      };
      const icon = dialog
        .querySelector("[data-wechat-refresh] svg")
        .getBoundingClientRect();
      return {
        dialog: rect(dialog),
        title: rect(dialog.querySelector("h2")),
        frame: rect(dialog.querySelector(".channel-qr-frame")),
        button: rect(dialog.querySelector("[data-wechat-refresh]")),
        text: rect(dialog.querySelector("[data-wechat-refresh] span")),
        icon: { x: icon.x + icon.width / 2, y: icon.y + icon.height / 2 },
      };
    });
  const sameLayout = (before, after) => {
    for (const part of Object.keys(before))
      for (const key of Object.keys(before[part]))
        assert(
          Math.abs(before[part][key] - after[part][key]) < 0.75,
          `${part}.${key} moved during QR refresh: ${JSON.stringify({ before, after })}`,
        );
  };
  const initialLayout = await scanLayout();
  await page.evaluate(() => (window.holdQR = true));
  await page.locator("[data-wechat-refresh]").click();
  await page.locator(".channel-qr-loading").waitFor();
  assert.equal(
    await page.locator("[data-wechat-refresh]").textContent(),
    "刷新二维码",
  );
  sameLayout(initialLayout, await scanLayout());
  await page.screenshot({
    path: path.join(artifacts, "wechat-refreshing.png"),
  });
  await page.evaluate(() => {
    window.holdQR = false;
    window.releaseQR();
  });
  await page.locator(".channel-qr-frame>img").waitFor();
  sameLayout(initialLayout, await scanLayout());
  assert.equal(
    await page.evaluate(
      () =>
        window.mutations.filter((m) => m.action === "wechat-login").at(-1)
          .refresh,
    ),
    true,
  );
  await page.evaluate(
    () =>
      (window.fixture.statuses.wx = {
        ...window.fixture.statuses.wx,
        state: "scanned",
      }),
  );
  await page.getByRole("heading", { name: "已扫码，请在手机上确认" }).waitFor();
  await page.evaluate(
    () => (window.fixture.statuses.wx = { state: "verification" }),
  );
  const input = page.locator("#wechat-verification-code");
  await input.waitFor();
  await page.locator("[data-wechat-verify]").click();
  assert.match(
    await page.locator("#wechat-verification-error").textContent(),
    /数字验证码/,
  );
  await input.fill("123456");
  await page.waitForTimeout(180);
  assert.equal(
    await input.inputValue(),
    "123456",
    "polling must preserve the entered verification code",
  );
  await input.press("Enter");
  assert.equal(
    await page.evaluate(
      () => window.mutations.filter((m) => m.action === "verification").length,
    ),
    1,
  );
  await page.evaluate(
    () =>
      (window.fixture.statuses.wx = {
        state: "login",
        message: "二维码已过期，请点击重新获取",
      }),
  );
  await page.getByRole("heading", { name: "二维码已过期" }).waitFor();
  await page.screenshot({ path: path.join(artifacts, "wechat-expired.png") });
  await page.evaluate(() => (window.fixture.statuses.wx = { state: "online" }));
  await page.locator("[data-wechat-history]").waitFor();
  assert.equal(await page.locator(".channel-login p").count(), 0);
  assert.equal(
    await page
      .locator(".channel-wechat-login .channel-dialog-foot")
      .isVisible(),
    false,
  );
  await page.screenshot({ path: path.join(artifacts, "wechat-success.png") });
  await page.locator("[data-wechat-history]").click();
  await page.evaluate(()=>window.mount('wx'));
  await page.locator(".channel-chat-panel").waitFor({ state: "visible" });
  await page.getByText("暂无会话消息", {exact:true}).waitFor();
  assert.equal(await page.locator(".channel-applications").isVisible(),false);
  assert.equal(await page.getByRole("tab",{name:"会话管理",exact:true}).count(),0);
  await page.getByRole("tab",{name:"会话设置",exact:true}).click();
  await page.getByRole("tab",{name:"会话历史",exact:true}).click();
  assert.equal(await page.locator('[name="session_validation"]').count(),0);
  assert.equal(await page.locator('#channel-validation-replies').count(),0);
  assert.equal(await page.locator('[data-request-action]').count(),0);
  await page.evaluate(()=>{
    window.historyFixture={thread:'wx-thread',can_send:true,recipient:'微信联系人',sessions:[{thread:'wx-thread',sender:'wx-user'}],before:null,messages:[
      {id:'user-1',role:'user',content:'在吗',created_at:'2026-11-05T10:00:00Z'},
      {id:'assistant-1',role:'assistant',content:'在的。',created_at:'2026-11-05T10:00:01Z'}
    ]};
  });
  await page.locator('.channel-chat-text').filter({hasText:'在的。'}).waitFor();
  assert.equal(await page.locator('.channel-application-table').count(),0);
  await page.evaluate(()=>window.mount('wx'));
  await page.locator('.channel-chat-text').filter({hasText:'在的。'}).waitFor();
  assert.equal(await page.getByRole('tab',{name:'会话历史',exact:true}).getAttribute('aria-selected'),'true');
  const composer=page.getByRole('textbox',{name:'发送微信消息',exact:true});
  const send=page.locator('.channel-message-composer button[type="submit"]');
  await page.waitForFunction(()=>!document.querySelector('.channel-message-composer textarea').disabled);
  await composer.fill('手动回复');
  await composer.press('Shift+Enter');
  await composer.type('第二行');
  assert.equal(await composer.inputValue(),'手动回复\n第二行');
  await page.evaluate(()=>window.holdSend=true);
  await composer.press('Enter');
  await page.waitForFunction(()=>window.mutations.some(m=>m.action==='manual-send'));
  assert.equal(await send.isDisabled(),true);
  await page.getByRole('tab',{name:'会话设置',exact:true}).click();
  await page.getByRole('tab',{name:'会话历史',exact:true}).click();
  assert.equal(await composer.inputValue(),'手动回复\n第二行');
  await page.evaluate(()=>{window.holdSend=false;window.releaseSend();});
  await page.locator('.channel-chat-text').filter({hasText:'手动回复'}).waitFor();
  await page.waitForFunction(()=>document.querySelector('.channel-message-composer textarea').value==='');
  assert.equal(await page.evaluate(()=>window.mutations.filter(m=>m.action==='manual-send').length),1);
  await page.evaluate(()=>window.failSend=true);
  await composer.fill('失败后保留草稿');await send.click();
  await page.locator('.channel-reply-failed:visible').click();
  assert.match(await page.locator('.channel-dialog pre').textContent(),/微信未接受此次发送/);
  await page.locator('.channel-dialog [data-close]').last().click();
  assert.equal(await composer.inputValue(),'失败后保留草稿');
  await page.evaluate(()=>window.failSend=false);
  await composer.fill('');
  await page.evaluate(()=>window.holdUpload=true);
  const fixtureImage={name:'image.png',mimeType:'image/png',buffer:Buffer.from('fixture')};
  await page.locator('[data-image-input]').setInputFiles(fixtureImage);
  await page.locator('.channel-draft-image').waitFor();
  assert.equal(await send.isDisabled(),true);
  await page.waitForFunction(()=>typeof window.releaseUpload==='function');
  await page.evaluate(()=>{window.holdUpload=false;window.releaseUpload();});
  await page.locator('.channel-draft-image img').waitFor();
  await page.getByRole('button',{name:'移除图片 1',exact:true}).click();
  assert.equal(await page.locator('.channel-draft-image').count(),0);
  assert.equal(await send.isDisabled(),true);
  await composer.evaluate(el=>{
    const transfer=new DataTransfer();transfer.items.add(new File(['fixture'],'paste.png',{type:'image/png'}));
    el.dispatchEvent(new ClipboardEvent('paste',{clipboardData:transfer,bubbles:true}));
  });
  await page.locator('.channel-draft-image img').waitFor();
  await send.click();
  await page.waitForFunction(()=>!document.querySelector('.channel-draft-image'));
  assert.deepEqual(await page.evaluate(()=>window.mutations.filter(m=>m.action==='manual-send').at(-1).images),['fixture-image']);
  assert.equal(await page.evaluate(()=>window.mutations.filter(m=>m.action==='manual-send').at(-1).text),'');
  await page.locator('.channel-image-thumb').last().click();
  await page.locator('.channel-image-preview').waitFor();
  await page.locator('.channel-image-preview [data-close]').last().click();
  await page.locator('[data-image-input]').setInputFiles(fixtureImage);
  await page.locator('.channel-draft-image img').waitFor();
  await composer.fill('图片说明');
  await page.evaluate(()=>window.failSend=true);
  await send.click();
  await page.waitForFunction(()=>!document.querySelector('.channel-message-composer button[type="submit"]').disabled);
  assert.equal(await composer.inputValue(),'图片说明');
  assert.equal(await page.locator('.channel-draft-image img').count(),1);
  await page.evaluate(()=>window.failSend=false);
  await page.screenshot({path:path.join(artifacts,'wechat-history.png')});
  await page.getByRole('button',{name:'移除图片 1',exact:true}).click();
  await composer.fill('');
  await page.locator('[data-image-input]').setInputFiles([
    {name:'演示.mp4',mimeType:'video/mp4',buffer:await readFile('gateway/tests/fixtures/wechat-video.mp4')},
    {name:'说明.txt',mimeType:'text/plain',buffer:Buffer.from('你好')}
  ]);
  await page.waitForFunction(()=>document.querySelectorAll('.channel-draft-file').length===2 && !document.querySelector('.channel-message-composer button[type="submit"]').disabled);
  await send.click();
  await page.waitForFunction(()=>!document.querySelector('.channel-draft-image'));
  await page.locator('.channel-file-card[data-view-video]').last().click();
  await page.locator('.channel-video-player').waitFor();
  await page.waitForFunction(()=>document.querySelector('.channel-video-player').readyState>=2);
  await page.locator('.channel-video-player').evaluate(video=>video.play());
  await page.waitForFunction(()=>document.querySelector('.channel-video-player').currentTime>0);
  await page.screenshot({path:path.join(artifacts,'wechat-video-preview.png')});
  await page.locator('.channel-video-preview [data-close]').last().click();
  assert.equal(await page.locator('.channel-file-card[download="说明.txt"]').count(),1);
  await page.setViewportSize({width:390,height:844});
  assert(await composer.evaluate(e=>e.getBoundingClientRect().right<=innerWidth));
  await page.screenshot({path:path.join(artifacts,'wechat-history-mobile.png'),fullPage:true});
  await page.setViewportSize({width:1280,height:1000});
  await page.evaluate(()=>{window.requests=[];window.historyFixture=null;});
  assert.equal(await page.locator('[data-connection-overview]').count(),0);
  assert.equal(await page.locator('.channel-connection-buttons').count(),0);
  await page.evaluate(() => window.mount("fs"));
  await page.locator('.channel-management').waitFor();
  assert.equal(await page.locator('[data-connection-overview]').count(),0);
  assert.equal(await page.locator('.channel-connection-buttons').count(),0);
  assert.equal(await page.getByRole('tab',{name:'会话管理',exact:true}).count(),1);
  await page.locator('[data-channel-tab="settings"]').click();
  assert.equal(await page.locator(".channel-setup-guide").count(), 0);
  assert.equal(await page.locator("[data-wechat-refresh]").count(), 0);
  await page.screenshot({
    path: path.join(artifacts, "feishu-settings.png"),
    fullPage: true,
  });
  await page.evaluate(() => window.mount("dt"));
  await page.locator(".channel-management").waitFor();
  assert.equal(await page.getByRole('tab',{name:'会话管理',exact:true}).count(),1);
  assert.equal(await page.locator("[data-connection-overview]").count(), 0);
  assert.equal(await page.locator(".channel-message-composer").count(), 1);
  await page.evaluate(() => window.mount("wx"));
  await page.locator(".channel-management").waitFor();
  await page.evaluate(() => (window.fixture.statuses.wx = { state: "login" }));
  await page.evaluate(() => window.mount());
  await page.locator('[data-login="wx"]').waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('[data-login="wx"]').click();
  await page.locator(".channel-qr-frame>img").waitFor();
  await page.screenshot({ path: path.join(artifacts, "wechat-mobile.png") });
  assert(
    await page
      .locator(".channel-dialog")
      .evaluate((el) => el.getBoundingClientRect().width <= innerWidth),
  );
  const mobileLayout = await scanLayout();
  await page.evaluate(() => (window.holdQR = true));
  await page.locator("[data-wechat-refresh]").click();
  await page.locator(".channel-qr-loading").waitFor();
  sameLayout(mobileLayout, await scanLayout());
  await page.evaluate(() => {
    window.holdQR = false;
    window.releaseQR();
  });
  await page.locator(".channel-qr-frame>img").waitFor();
  sameLayout(mobileLayout, await scanLayout());
  await page.evaluate(() => (window.fixture.statuses.wx = { state: "online" }));
  await page.locator("[data-wechat-history]").waitFor();
  await page.screenshot({
    path: path.join(artifacts, "wechat-success-mobile.png"),
  });
  await page.locator('.channel-dialog [aria-label="关闭"]').click();
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.evaluate(() => window.mount());
  await page.locator('[data-act="add-connection"]').waitFor();
  for (const type of ["wechat", "feishu", "dingtalk", "telegram", "whatsapp"]) {
    await page.locator('[data-act="add-connection"]').click();
    await page.locator(".channel-dialog .channel-select summary").click();
    await page.locator(`.channel-dialog [data-option="${type}"]`).click();
    await page.getByRole("button", { name: "下一步", exact: true }).click();
    await page.locator(".channel-connect-dialog").waitFor();
    assert.equal((await page.locator('.channel-dialog-head h2').textContent()).trim(),'接入'+({wechat:'微信',feishu:'飞书',dingtalk:'钉钉',telegram:'Telegram',whatsapp:'WhatsApp'}[type]));
    await page.screenshot({ path: path.join(artifacts, `${type}-create.png`) });
    if (["wechat", "feishu", "telegram", "whatsapp"].includes(type))
      assert.equal(
        await page
          .locator(
            ".channel-dialog .channel-setup-guide,.channel-dialog .channel-help",
          )
          .count(),
        0,
      );
    await page.locator('.channel-dialog [aria-label="关闭"]').click();
  }
  await page.locator('[data-login="wa"]').click();
  await page.locator('.channel-qr-frame img').waitFor();
  assert.equal((await page.locator('.channel-dialog h2').textContent()).trim(),'WhatsApp扫码接入');
  const whatsappLayout=await scanLayout();
  await page.evaluate(()=>window.holdQR=true);
  await page.locator('[data-wechat-refresh]').click();
  await page.locator('.channel-qr-loading').waitFor();
  sameLayout(whatsappLayout,await scanLayout());
  await page.evaluate(()=>{window.holdQR=false;window.releaseQR();});
  await page.locator('.channel-qr-frame img').waitFor();
  await page.evaluate(()=>window.fixture.statuses.wa={state:'login',message:'二维码已过期，请点击重新获取'});
  await page.getByRole('heading',{name:'二维码已过期',exact:true}).waitFor();
  await page.getByRole('button',{name:'重新获取二维码',exact:true}).click();
  await page.locator('.channel-qr-frame img').waitFor();
  await page.screenshot({path:path.join(artifacts,'whatsapp-qr.png')});
  await page.evaluate(()=>window.fixture.statuses.wa={state:'online'});
  await page.getByRole('heading',{name:'WhatsApp已连接',exact:true}).waitFor();
  await page.locator('.channel-dialog [aria-label="关闭"]').click();
  for(const id of ['tg','wa']){
    await page.evaluate(id=>{
      window.requests=[{sender_id:'1001',user_id:'1001',sender_name:'Alice',status:'approved',conversation_id:'1',first_message:'你好',created_at:1793890000},
        {sender_id:'1002',user_id:'1002',sender_name:'Bob',status:'approved',conversation_id:'2',first_message:'另一个问题',created_at:1793890000}];
      window.mount(id);
    },id);
    await page.getByRole('tab',{name:'会话管理',exact:true}).click();
    await page.locator('.channel-application-table').waitFor();
    assert.equal(await page.locator('[data-connection-overview]').count(),0);
    assert.equal(await page.locator('[data-open-chat="1001"]').count(),3);
    await page.locator('[data-open-chat="1001"]').last().click();
    assert.match(await page.evaluate(()=>location.hash),new RegExp('channels/'+id+'/1'));
    await page.evaluate(id=>{
      window.historyFixture={thread:id+'-alice',can_send:true,recipient:'Alice',sessions:[],messages:[{id:'hello',role:'user',content:'你好',created_at:'2026-11-06T10:00:00Z'}]};
      window.mount(id,'1');
    },id);
    const composer=page.locator('.channel-message-composer textarea');
    await page.waitForFunction(()=>!document.querySelector('.channel-message-composer textarea').disabled);
    await composer.fill('人工回复 '+id);await composer.press('Enter');
    await page.waitForFunction(()=>document.querySelector('.channel-message-composer textarea').value==='');
    await page.locator('[data-image-input]').setInputFiles({name:'说明.txt',mimeType:'text/plain',buffer:Buffer.from('你好')});
    await page.waitForFunction(()=>document.querySelector('.channel-draft-file') && !document.querySelector('.channel-message-composer button[type="submit"]').disabled);
    await page.locator('.channel-message-composer button[type="submit"]').click();
    await page.locator('.channel-file-card').waitFor();
    await page.screenshot({path:path.join(artifacts,id+'-chat.png')});
    await page.setViewportSize({width:390,height:844});
    assert(await composer.evaluate(e=>e.getBoundingClientRect().right<=innerWidth));
    await page.screenshot({path:path.join(artifacts,id+'-chat-mobile.png'),fullPage:true});
    await page.setViewportSize({width:1280,height:1000});
  }
  assert.deepEqual(errors, []);
  console.log(
    "WeChat/Feishu/DingTalk isolation, Telegram/WhatsApp multi-user chat, media, scan states and mobile layout passed. Screenshots: " +
      artifacts,
  );
} finally {
  await browser.close();
}
