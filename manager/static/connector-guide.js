/* Static documentation for the implemented Gateway v1 contract. */
window.CorkiConnectorGuide = (() => {
  const escape = text => String(text).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const manifest = {
    protocol:1,id:"local-webhook",name:"本地 Webhook 示例",runtime:"python",entry:"main.py",
    fields:[{key:"token",label:"访问密钥",secret:true,required:true},
      {key:"port",label:"监听端口（留空自动分配）"}],
  };
  const sections = [
    ["start","从这里开始"],
    ["package","ZIP 与清单"],
    ["protocol","通信协议"],
    ["test","测试与排错"],
  ];
  function mount(root, {back,toast}) {
    let viewers=[];
    const codes=[];
    const block=(label,value,json=false)=>{
      const index=codes.push({value,json})-1;
      return `<div class="connector-code"><div class="connector-code-head"><span>${escape(label)}</span><button type="button" class="text-button" data-copy="${index}">复制</button></div><div data-code="${index}"></div></div>`;
    };
    const table=(rows)=>`<div class="connector-guide-table"><table><thead><tr><th>字段</th><th>要求</th><th>说明</th></tr></thead><tbody>${rows.map(r=>`<tr><td><code>${escape(r[0])}</code></td><td>${escape(r[1])}</td><td>${escape(r[2])}</td></tr>`).join("")}</tbody></table></div>`;
    const post="curl -X POST http://127.0.0.1:18080/ -H 'Content-Type: application/json' -H 'Authorization: Bearer <你设置的访问密钥>' --data '{\"id\":\"sample-1\",\"sender\":\"local-user\",\"conversation\":\"local-chat\",\"kind\":\"direct\",\"text\":\"你好\"}'";
    const content={
      start:()=>`<h2>先跑通示例，再接入自己的渠道</h2><p>ZIP 里需要放一个可执行的连接器程序。它负责收取平台消息、交给 Corki，并将回复发送回原会话；只有配置文件无法完成接入。</p>
      <ol class="connector-guide-steps">
        <li><strong>下载示例包</strong><p>内含清单、完整 Python 程序和使用说明。仅使用标准库，无需额外依赖，也不包含任何密钥。</p><a class="button primary" href="/connector-example.zip" download="corki-local-webhook.zip">下载可运行示例</a></li>
        <li><strong>安装并创建账号接入</strong><p>返回安装窗口上传 ZIP。在「对话接入」新增「本地 Webhook 示例」，填写自己设置的访问密钥和一个空闲监听端口。</p></li>
        <li><strong>发送一条测试消息</strong><p>开启网关与该接入，按「测试与排错」中的示例发送。接入开启后即可处理消息，无需首次审批。</p></li>
        <li><strong>替换平台收发逻辑</strong><p>按你的平台实现轮询或长连接，验证发送者身份；保留与 Corki 的通信协议。修改清单 id 和 name 后，打包成自己的连接器。</p></li>
      </ol><div class="connector-guide-note">这是本地程序，不是 MCP，也不能直接导入 OpenClaw 插件。连接器以当前用户权限运行，只安装可信代码。</div>`,
      package:()=>`<h2>ZIP 里放什么</h2><p>推荐只压缩下列文件。清单可直接位于 ZIP 根目录，也可位于唯一的顶层文件夹内。</p>
      ${block("目录结构","local-webhook.zip\n└── local-webhook/\n    ├── connector.json    # 必须：连接器清单\n    ├── main.py           # 必须：清单 entry 指向的程序\n    └── README.md         # 可选：使用说明")}
      <h3>connector.json</h3>${block("JSON · 可复制",manifest,true)}
      ${table([["protocol","必填 · 整数","固定为 1。"],["id","必填 · 字符串","1–48 位，小写字母开头，其余可用小写字母、数字、横线、下划线。不能与已有或内置渠道重名。"],["name","必填 · 字符串","1–60 字的显示名称。"],["runtime","必填 · 枚举","node 或 python；Python 使用 Corki 所在解释器，Node 需本机可用。"],["entry","必填 · 字符串","包内相对路径，例如 main.py、index.mjs 或 src/main.py；使用 /，文件必须存在。"],["fields","可选 · 数组","最多 24 项，在新增账号时生成配置表单，见下方。"],["description","可选 · 字符串","连接器用途说明。"]])}
      <h3>配置表单 fields</h3><p>每个字段的实际值都是字符串，通过 initialize.params.config 交给程序。真实密钥在页面填写，不要写入 ZIP。</p>
      ${table([["key","必填 · 字符串","与 id 使用相同命名规则；不能重复，workdir 为保留字段。"],["label","必填 · 字符串","1–60 字的表单标题。"],["secret","可选 · 布尔值","true 时作为密钥输入，管理页面不回显明文。"],["required","可选 · 布尔值","true 时必须填写才能开启接入。"],["options","可选 · 字符串数组","提供下拉选项；1–30 项，每项最多 100 字符。"]])}
      <h3>打包边界</h3><ul><li>ZIP 不超过 16 MB，解压总量不超过 64 MB，最多 2000 个条目（含目录）。</li><li>禁止符号链接、重复路径、绝对路径、上级路径与 Windows 保留文件名。</li><li>宿主不会运行 npm install、pip install 或安装脚本。优先使用标准库；有依赖时由作者提供可用资源或说明所需环境。</li><li>使用 ZIP 格式，不是 RAR、7z，也不是只把扩展名改成 .zip。</li></ul>`,
      protocol:()=>`<h2>程序如何与 Corki 通信</h2><p>Corki 启动你的入口程序，通过标准输入和标准输出双向传递 UTF-8 JSON Lines：每行一个 JSON，单行不超过 1 MB。日志写入 stderr，不能混入 stdout。</p>
      <div class="connector-guide-flow">平台消息 → 连接器 → Corki Agent → 连接器 → 原会话</div>
      <h3>1. 初始化 · Corki → 连接器</h3>
      ${block("收到的请求",{id:"host-1",method:"initialize",params:{protocol:1,config:{token:"页面填写的值",port:"18080"},state_directory:"该账号的独立数据目录",cursor:""}},true)}
      ${block("初始化响应",{id:"host-1",result:{protocol:1}},true)}
      <p>20 秒内返回响应，然后开始监听。state_directory 用于保存该账号的登录状态；cursor 是上次确认的轮询游标。不要用固定全局目录混存多个账号。宿主可能额外传入 dependencies，它不是自定义连接器的自动依赖安装机制。</p>
      <h3>2. 报告身份与真实连接状态</h3>
      ${block("身份通知",{method:"account",params:{id:"稳定的平台账号ID"}},true)}
      ${block("连接成功",{method:"status",params:{state:"online"}},true)}
      <p>身份不能使用密钥，变更会撤销旧授权和路由。仅在平台已连接时发送 online；初始化完成不等于登录成功。</p>
      <details><summary>其他状态与扫码验证</summary><p>state 支持 connecting、online、qr、verification、error。扫码状态使用 qr 字段传 PNG data URL。错误 code 支持 credentials、network、verify、expired、dependencies、protocol；不要上传包含密钥的原始异常。</p><p>需要手机验证码时发送 verification，宿主随后请求 method: verify，params: {code: "123456"}；处理后使用相同请求 id 返回 result。</p></details>
      <h3>3. 上报消息 · 连接器 → Corki</h3>
      ${block("私聊文本",{id:"request-1",method:"inbound",params:{id:"平台稳定消息ID",sender:"已验证的发送者ID",conversation:"平台会话ID",kind:"direct",name:"显示名",text:"你好"}},true)}
      <p>外层 id 关联请求响应；内层 params.id 用于去重。发送者身份必须由平台认证数据取得，不能相信正文中自称的 sender。首版只支持私聊文本，不上报群聊、机器人自己的消息或旧历史同步。</p>
      ${block("已接收",{id:"request-1",result:{accepted:true,thread:"Corki会话ID"}},true)}
      <p>accepted:true 表示已接收，不表示 Agent 已完成。重复消息会返回 duplicate:true。繁忙或传输失败用同一个平台消息 ID 重试，不能生成新 ID 绕过去重。</p>
      <p>身份字段最多 300 字符，文本最多 100000 字符。可附带平台回复上下文，但不要附带长期访问密钥。收到接收确认或拒绝后才推进游标：</p>
      ${block("游标通知",{method:"cursor",params:{value:"已确认的游标"}},true)}
      <h3>4. 发送回答 · Corki → 连接器</h3>
      ${block("发送请求",{id:"host-2",method:"send",params:{message:{id:"平台稳定消息ID",sender:"发送者ID",conversation:"平台会话ID",kind:"direct",text:"你好"},text:"你好，有什么需要帮助的？"}},true)}
      ${block("确认真实投递成功",{id:"host-2",result:{sent:true}},true)}
      <p>message 为原始消息上下文。程序需处理平台长度限制并返回成功；失败时返回同一 id 的 error。宿主最多等待 120 秒，不自动重发回复，避免重复投递。结果不明时不能声称 sent:true。</p>
      <h3>5. 关闭与异常</h3><p>收到 method: shutdown 后停止监听、关闭连接、释放子进程并响应。stdin EOF 也必须退出。不要留下脱离宿主的后台进程。所有请求响应都必须原样返回 id。</p>`,
      test:()=>`<h2>从安装到第一条回复</h2><ol class="connector-guide-steps"><li><strong>配置测试接入</strong><p>下载并导入示例，新增账号，设置自己的访问密钥。监听端口可填 18080（需未占用）；留空则自动分配，查看该账号数据目录中的 listen.json。这里是示例的 HTTP 端口，与网关管理端口 17003 无关。</p></li><li><strong>开启网关和账号</strong><p>等待接入状态显示「已连接」。确保 Corki 已配置可用的默认模型。</p></li><li><strong>发送测试消息</strong><p>替换下方端口与密钥后执行。macOS/Linux 使用 curl；Windows PowerShell 使用 curl.exe，或用 HTTP 客户端发送相同 JSON。</p></li></ol>
      ${block("POST · 发送测试消息",post)}
      <p>HTTP 202 只表示示例收到请求；是否接收由下方 GET 返回的 admission 回执判断。后续每个新问题都应换一个消息 id。</p>
      ${block("GET · 查看回执和回复","curl http://127.0.0.1:18080/ -H 'Authorization: Bearer <你设置的访问密钥>'")}
      <p>示例仅监听本机，最多保留 50 条内存记录，重启即清空；它用于验证协议，不是生产消息服务。完整 Agent 历史仍由 Corki 保存。</p>
      <h3>常见问题</h3>
      ${[["找不到清单","检查 ZIP 根目录或唯一顶层文件夹中是否有 connector.json，不要多包两层目录。"],["同名连接器已存在","修改清单 id 后再导入，或者先删除已有接入和旧连接器；当前不支持覆盖升级。"],["入口文件不存在","entry 必须与 ZIP 内文件路径一致，注意大小写；使用相对路径与 /。"],["已连接，但 Agent 没有执行","检查接入是否开启，以及消息 kind 是否为 direct。"],["消息不断重复","用平台稳定消息 ID 去重；上报请求收到宿主确认后再向平台 ACK 或提交游标。"],["有结果，但平台没收到","检查 send 的目标会话、平台返回码、密钥或回复窗口是否过期；不要在仅收到发送请求时就返回 sent:true。"],["连接异常或无法启动","确认 node/python 环境与依赖齐全，stdout 只能输出 JSON，初始化响应需在 20 秒内返回；详细调试日志写 stderr。"]].map(([q,a])=>`<details><summary>${q}</summary><p>${a}</p></details>`).join("")}`,
    };
    root.innerHTML=`<div class="connector-guide-toolbar"><button type="button" class="button" data-guide-back>‹ 返回安装</button><a class="button" href="/connector-example.zip" download="corki-local-webhook.zip">下载示例 ZIP</a></div><div class="connector-guide-layout"><nav aria-label="指引章节">${sections.map(([id,title],i)=>`<button type="button" data-section="${id}"><span>0${i+1}</span>${title}</button>`).join("")}</nav><article class="connector-guide-content"></article></div>`;
    const dispose=()=>{viewers.forEach(v=>v.destroy());viewers=[];};
    function show(id) {
      dispose(); codes.length=0;
      root.querySelectorAll("[data-section]").forEach(b=>b.setAttribute("aria-current",b.dataset.section===id?"page":"false"));
      const article=root.querySelector("article"); article.innerHTML=content[id]();article.scrollTop=0;
      article.querySelectorAll("[data-code]").forEach(el=>{
        const item=codes[Number(el.dataset.code)];
        if(item.json && window.CorkiJsonViewer) viewers.push(window.CorkiJsonViewer(el,item.value));
        else el.innerHTML=`<pre>${escape(item.json?JSON.stringify(item.value,null,2):item.value)}</pre>`;
      });
      article.querySelectorAll("[data-copy]").forEach(b=>b.onclick=async()=>{
        const item=codes[Number(b.dataset.copy)];
        try {await navigator.clipboard.writeText(item.json?JSON.stringify(item.value,null,2):item.value);toast("已复制");}
        catch {toast("复制失败，请选中文字手动复制");}
      });
    }
    root.querySelector("[data-guide-back]").onclick=back;
    root.querySelectorAll("[data-section]").forEach(b=>b.onclick=()=>show(b.dataset.section));
    show("start");
    return {destroy:dispose};
  }
  return {mount};
})();
