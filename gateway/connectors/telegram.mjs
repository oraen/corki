import { emit, status, chunks, sleep, unsupportedContent } from "./protocol.mjs";

export async function create({ config, cursor, incoming }) {
  let stopped = false,
    offset = Number(cursor) || 0;
  const controller = new AbortController();
  async function api(method, body, timeout = 20000) {
    const response = await fetch(
      `https://api.telegram.org/bot${config.token}/${method}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: AbortSignal.any([
          controller.signal,
          AbortSignal.timeout(timeout),
        ]),
        redirect: "error",
      },
    );
    const data = await response.json();
    if (!response.ok || !data.ok) {
      if ([401, 403].includes(response.status))
        status("error", { code: "credentials" });
      if (response.status === 429)
        await sleep(Math.min(60, data.parameters?.retry_after || 5) * 1000);
      throw new Error("telegram request failed");
    }
    return data.result;
  }
  return {
    async start() {
      const me = await api("getMe", {});
      emit({ method: "account", params: { id: String(me.id) } });
      const webhook = await api("getWebhookInfo", {});
      // Never delete another client's webhook silently.
      if (webhook.url)
        throw new Error("active webhook must be removed by owner");
      status("online");
      let failures = 0;
      while (!stopped) {
        try {
          const updates = await api(
            "getUpdates",
            { offset, timeout: 30, allowed_updates: ["message"] },
            40000,
          );
          for (const update of updates) {
            const message = update.message;
            const media=['video','photo','audio','voice','document','sticker','animation','video_note',
              'contact','location','venue','poll','dice'].find(key=>message?.[key]);
            if (
              message?.chat?.type === "private" &&
              !message.from?.is_bot &&
              message.from?.id && (message.text || media)
            ) {
              await incoming({
                id: String(update.update_id),
                sender: String(message.from.id),
                conversation: String(message.chat.id),
                name: message.from.first_name || String(message.from.id),
                kind: "direct",
                ...(media?unsupportedContent(media,message.caption):{text:message.text}),
              });
            }
            offset = update.update_id + 1;
            emit({ method: "cursor", params: { value: String(offset) } });
          }
          failures = 0;
          status("online");
        } catch {
          if (stopped) break;
          status("error", { code: "network" });
          await sleep(Math.min(60, 2 ** Math.min(++failures, 6)) * 1000);
        }
      }
    },
    async send({ message, text }) {
      for (const part of chunks(text))
        await api("sendMessage", { chat_id: message.conversation, text: part });
    },
    async close() {
      stopped = true;
      controller.abort();
    },
  };
}
