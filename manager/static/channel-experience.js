/* Focused presentation for WeChat and Feishu; transport capabilities stay explicit. */
window.CorkiChannelExperience = (() => {
  const esc = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const icons = {
    check: '<path d="m5 12 4 4L19 6"/>',
    refresh:
      '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 7a7 7 0 0 1 11.6-2L20 8M4 16l2.3 3A7 7 0 0 0 18 17"/>',
    phone:
      '<rect x="6" y="2" width="12" height="20" rx="3"/><path d="M10 18h4"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
  };
  const icon = (name) =>
    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.info}</svg>`;
  const mark = (type) =>
    `<span class="channel-experience-mark"><img src="/channel-icons/${type === "wechat" ? "wechat.svg" : "feishu.png"}" alt="${type === "wechat" ? "微信" : "飞书"}"></span>`;
  function summary(row, state, running, pending = null) {
    const wechat = row.channel === "wechat";
    const online = running && row.enabled && state?.state === "online";
    const scanning =
      wechat &&
      ["login", "qr", "scanned", "verification"].includes(state?.state);
    const connecting =
      running && row.enabled && (!state || state.state === "connecting");
    const label =
      pending?.label ||
      (!running
        ? "网关未开启"
        : !row.enabled
          ? "未连接"
          : online
            ? "已连接"
            : scanning
              ? state.state === "verification"
                ? "待验证"
                : state.state === "scanned"
                  ? "待手机确认"
                  : "待扫码"
              : connecting
                ? "连接中"
                : "连接异常");
    const primary = pending
      ? "waiting"
      : !running
        ? "power"
        : !row.enabled
          ? "enable"
          : scanning
            ? "login"
            : connecting
              ? "waiting"
              : "reconnect";
    const primaryText = pending
      ? `${label}…`
      : !running
        ? "开启转发网关"
        : !row.enabled
          ? "连接"
          : scanning
            ? state.state === "verification"
              ? "完成验证"
              : state.state === "scanned"
                ? "确认登录"
                : "扫码登录"
            : connecting
              ? "正在连接…"
              : "重新连接";
    // Normal states are already clear in the badge and action. Keep actionable errors only.
    const needsAttention =
      running &&
      row.enabled &&
      !pending &&
      (state?.state === "error" ||
        (state?.state === "login" &&
          state.message &&
          state.message !== "点击扫码登录后获取二维码"));
    const error = needsAttention
      ? state.message ||
        (wechat ? "请检查网络后重新连接。" : "请检查应用凭据、权限和事件订阅。")
      : "";
    return `<div class="channel-connection-overview"><div class="channel-connection-main">${mark(row.channel)}<h2>${esc(row.name)}</h2><span class="channel-connection-pill ${pending || scanning ? "waiting" : online ? "online" : state?.state === "error" ? "error" : ""}"><i></i>${label}</span></div>
      <div class="channel-connection-buttons"><button type="button" class="button" data-connection-settings>接入设置</button>${row.enabled && running ? '<button type="button" class="button" data-connection-disconnect>断开连接</button>' : ""}<button type="button" class="button primary" data-connection-primary="${primary}" ${connecting || pending ? 'disabled aria-busy="true"' : ""}>${primaryText}</button></div>
      ${error ? `<p class="channel-connection-error" role="status">${esc(error)}</p>` : ""}</div>`;
  }
  function login({ phase, qr, message }) {
    const title = {
      scanned: "已扫码，请在手机上确认",
      verification: "手机验证",
      expired: "二维码已过期",
      error: "连接失败",
      success: "微信已连接",
    }[phase];
    const subtitle =
      phase === "error"
        ? message || "请检查网络后重试。"
        : phase === "verification" &&
            message &&
            ![
              "请输入手机上显示的验证码",
              "请输入手机微信上显示的数字验证码。",
            ].includes(message)
          ? message
          : "";
    const visual = ["qr", "scanned"].includes(phase)
      ? `<div class="channel-qr-frame"><img width="232" height="232" alt="微信登录二维码" src="${esc(qr)}">${phase === "scanned" ? `<div class="channel-qr-confirmed">${icon("phone")}</div>` : ""}</div>`
      : phase === "loading"
        ? `<div class="channel-qr-frame channel-qr-loading" aria-label="正在获取二维码"><span class="channel-qr-corner a"></span><span class="channel-qr-corner b"></span><span class="channel-qr-corner c"></span><span class="channel-qr-corner d"></span><div class="channel-qr-grid"></div><span class="channel-login-loader" aria-hidden="true"></span></div>`
        : phase === "verification"
          ? `<div class="channel-verification"><input id="wechat-verification-code" aria-label="手机验证码" inputmode="numeric" autocomplete="one-time-code" maxlength="12" placeholder="输入手机显示的验证码" aria-describedby="wechat-verification-error"><p id="wechat-verification-error" class="channel-verification-error" role="alert"></p><button type="button" class="button primary" data-wechat-verify>确认验证码</button></div>`
          : `<div class="channel-login-emblem ${phase}">${icon(phase === "success" ? "check" : phase === "expired" ? "refresh" : "info")}</div>`;
    const heading = `${title ? `<h3>${title}</h3>` : ""}${subtitle ? `<p class="channel-login-description">${esc(subtitle)}</p>` : ""}`;
    return `<section class="channel-login-scene ${phase}" aria-live="polite" aria-busy="${phase === "loading"}">
      ${phase === "verification" ? heading + visual : visual + heading}
      ${["qr", "loading"].includes(phase) ? `<button type="button" class="channel-login-refresh ${phase === "loading" ? "is-loading" : ""}" data-wechat-refresh ${phase === "loading" ? 'disabled aria-busy="true"' : ""}>${icon("refresh")}<span>刷新二维码</span></button>` : ""}
      ${["expired", "error"].includes(phase) ? '<button type="button" class="button primary" data-wechat-refresh>重新获取二维码</button>' : ""}
      ${phase === "success" ? '<button type="button" class="button primary" data-wechat-history>进入会话管理</button>' : ""}
      </section>`;
  }
  return { summary, login };
})();
