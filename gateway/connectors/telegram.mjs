import { emit, status, chunks, unsupportedContent } from "./protocol.mjs";
import {
  cache,
  cached,
  wait,
  failure,
  serial,
  IMAGE_LIMIT,
} from "./direct-media.mjs";

export async function create({
  config,
  cursor,
  incoming,
  state_directory,
  fatal,
}) {
  const controller = new AbortController(),
    media = cache(state_directory),
    outbox = serial();
  let stopped = false,
    offset =
      Number.isSafeInteger(Number(cursor)) && Number(cursor) >= 0
        ? Number(cursor)
        : 0;
  const lastSent = new Map(),
    owners = new Map();
  async function api(
    method,
    body,
    timeout = 20000,
    signal = controller.signal,
  ) {
    let response, data;
    try {
      response = await fetch(
        `https://api.telegram.org/bot${config.token}/${method}`,
        {
          method: "POST",
          ...(body instanceof FormData
            ? { body }
            : {
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
              }),
          signal: AbortSignal.any([signal, AbortSignal.timeout(timeout)]),
          redirect: "error",
        },
      );
      data = await response.json();
    } catch {
      throw failure(
        "telegram",
        "Telegram 请求未获确认，请检查网络；消息未自动重发",
      );
    }
    if (!response.ok || data?.ok !== true) {
      const code = Number(data?.error_code || response.status),
        error = failure(
          "telegram",
          code === 403
            ? "对方已阻止 Bot 或尚未发起对话"
            : code === 429
              ? "Telegram 请求限流，请稍后重试"
              : code === 409
                ? "此 Bot 正被其他程序轮询"
                : code === 401
                  ? "Bot Token 无效"
                  : "Telegram 拒绝了请求",
          code,
        );
      error.retryAfter = Math.min(
        60,
        Math.max(1, Number(data?.parameters?.retry_after) || 5),
      );
      if (["getMe", "getUpdates", "getWebhookInfo"].includes(method)) {
        status("error", {
          code:
            code === 401
              ? "telegram_credentials"
              : code === 409
                ? "telegram_conflict"
                : "network",
        });
        error.reported = true;
      }
      throw error;
    }
    return data.result;
  }
  async function download(file, kind) {
    try {
      const limit = kind === "image" ? IMAGE_LIMIT : 20 * 1024 * 1024;
      if (Number(file.file_size) > limit) throw new Error("large");
      const info = await api("getFile", { file_id: file.file_id });
      if (
        Number(info.file_size) > limit ||
        typeof info.file_path !== "string" ||
        !/^[A-Za-z0-9_./-]+$/.test(info.file_path) ||
        info.file_path.split("/").includes("..") ||
        info.file_path.startsWith("/")
      )
        throw new Error("invalid");
      const signal = AbortSignal.any([
        controller.signal,
        AbortSignal.timeout(60000),
      ]);
      const response = await fetch(
        `https://api.telegram.org/file/bot${config.token}/${info.file_path}`,
        { redirect: "error", signal },
      );
      if (
        !response.ok ||
        Number(response.headers.get("content-length")) > limit
      )
        throw new Error("download");
      return await media.save(response.body, kind, signal);
    } catch {
      return { error: "附件下载失败或超过大小限制，请重新发送" };
    }
  }
  async function admit(update) {
    const m = update.message;
    if (m?.chat?.type !== "private" || m.from?.is_bot || !m.from?.id) return;
    const kind = m.photo
      ? "image"
      : m.video
        ? "video"
        : m.document
          ? "file"
          : null;
    let text = m.text || m.caption || "",
      extra = {};
    if (kind) {
      const file =
        kind === "image"
          ? m.photo.at(-1)
          : kind === "video"
            ? m.video
            : m.document;
      const result = await download(file, kind);
      if (text) text += "\n";
      const position = Array.from(text).length;
      if (kind === "image") {
        text += "[Image #1]";
        extra = { image_refs: [result], image_positions: [position] };
      } else {
        text += "[File #1]";
        extra = {
          file_refs: [
            {
              ...result,
              kind,
              name:
                file.file_name ||
                (kind === "video" ? "video.mp4" : "attachment"),
            },
          ],
          file_positions: [position],
        };
      }
    } else {
      const type = [
        "audio",
        "voice",
        "sticker",
        "animation",
        "video_note",
        "contact",
        "location",
        "venue",
        "poll",
        "dice",
      ].find((k) => m[k]);
      if (type) {
        extra = unsupportedContent(type, m.caption);
        text = extra.text;
      } else if (!text) return;
    }
    const quoted = m.reply_to_message?.text || m.reply_to_message?.caption;
    if (quoted) {
      text += "\n\n引用消息：\n" + String(quoted).slice(0, 4000);
    }
    if (typeof m.media_group_id === "string")
      extra.media_group_id = m.media_group_id.slice(0, 300);
    const accepted = await incoming({
      id: String(update.update_id),
      sender: String(m.from.id),
      conversation: String(m.chat.id),
      message_id: m.message_id,
      name:
        [m.from.first_name, m.from.last_name].filter(Boolean).join(" ") ||
        m.from.username ||
        String(m.from.id),
      kind: "direct",
      text,
      ...extra,
    });
    if (accepted?.accepted !== true) throw new Error("Message not admitted");
  }
  return {
    async start() {
      const me = await api("getMe", {});
      if (!me?.id) throw new Error("invalid bot");
      emit({ method: "account", params: { id: String(me.id) } });
      const webhook = await api("getWebhookInfo", {});
      if (webhook?.url) {
        status("error", { code: "telegram_webhook" });
        const e = new Error("webhook active");
        e.reported = true;
        throw e;
      }
      status("online");
      let failures = 0;
      while (!stopped) {
        try {
          const updates = await api(
            "getUpdates",
            { offset, timeout: 30, allowed_updates: ["message"] },
            40000,
          );
          if (!Array.isArray(updates)) throw new Error("invalid updates");
          for (const update of updates) {
            if (!Number.isSafeInteger(update.update_id)) continue;
            if (update.update_id < offset) continue;
            await admit(update);
            offset = update.update_id + 1;
            emit({ method: "cursor", params: { value: String(offset) } });
          }
          failures = 0;
          status("online");
        } catch (e) {
          if (stopped) break;
          if (!e.reported) status("error", { code: "network" });
          try {
            await wait(
              e.delivery?.code === 429
                ? e.retryAfter * 1000
                : Math.min(60, 2 ** Math.min(++failures, 6)) * 1000,
              controller.signal,
            );
          } catch {
            break;
          }
        }
      }
    },
    async send({ message, text, images = [], files = [] }) {
      const peer = message?.conversation;
      if (typeof peer !== "string" || !/^[1-9][0-9]{0,18}$/.test(peer))
        throw failure("telegram", "收件人无效");
      if (
        !Array.isArray(images) ||
        !Array.isArray(files) ||
        images.length + files.length > 8
      )
        throw failure("telegram", "附件数量无效");
      const signal = AbortSignal.any([
        controller.signal,
        AbortSignal.timeout(150000),
      ]);
      return outbox(peer, async () => {
        let delivered = 0;
        try {
          const prepared = [];
          for (const image of images)
            prepared.push({
              kind: "image",
              item: image,
              raw: await media.read(image, "image"),
            });
          for (const file of files)
            prepared.push({
              kind: file.kind,
              item: file,
              raw: await media.read(file, "file"),
            });
          const actions = chunks(text).map((part) => ({
            method: "sendMessage",
            body: { chat_id: peer, text: part },
          }));
          for (const { kind, item, raw } of prepared) {
            if (!["image", "video", "file"].includes(kind))
              throw failure("telegram", "附件类型无效");
            // Bot API photos are limited to 10 MB; preserve larger images as documents.
            const photo =
              kind === "image" &&
              raw.length <= 10 * 1024 * 1024 &&
              !["image/gif", "image/webp"].includes(item.mime);
            const imageName =
              "image." +
              ({
                "image/jpeg": "jpg",
                "image/gif": "gif",
                "image/webp": "webp",
              }[item.mime] || "png");
            const field = photo
                ? "photo"
                : kind === "video"
                  ? "video"
                  : "document",
              body = new FormData();
            body.append("chat_id", peer);
            body.append(
              field,
              new Blob([raw], {
                type: item.mime || "application/octet-stream",
              }),
              item.name ||
                (kind === "image"
                  ? imageName
                  : kind === "video"
                    ? "video.mp4"
                    : "attachment"),
            );
            actions.push({
              method: photo
                ? "sendPhoto"
                : kind === "video"
                  ? "sendVideo"
                  : "sendDocument",
              body,
            });
          }
          if (!actions.length)
            throw failure("telegram", "请输入消息或添加附件");
          for (const action of actions) {
            const delay = 1050 - (Date.now() - (lastSent.get(peer) || 0));
            if (delay > 0) await wait(delay, signal);
            const result = await api(action.method, action.body, 45000, signal);
            if (!result?.message_id)
              throw failure(
                "telegram",
                "Telegram 未返回发送确认，送达情况未知",
              );
            delivered++;
            lastSent.set(peer, Date.now());
            if (lastSent.size > 500)
              lastSent.delete(lastSent.keys().next().value);
          }
        } catch (e) {
          const error = e.delivery
            ? e
            : failure("telegram", "发送失败或超时，送达情况未知；未自动重发");
          if (delivered)
            error.delivery.message =
              "部分消息已发送，请勿重复提交。" + error.delivery.message;
          throw error;
        }
      });
    },
    async typing({ message, active }) {
      const peer = message?.conversation,
        id = message?.id;
      if (!active) {
        if (owners.get(peer) === id) owners.delete(peer);
        return { supported: true };
      }
      if (typeof peer !== "string" || !/^[1-9][0-9]{0,18}$/.test(peer))
        return { supported: false };
      owners.set(peer, id);
      try {
        await api("sendChatAction", { chat_id: peer, action: "typing" }, 3000);
        return { supported: true };
      } catch {
        return { supported: false };
      }
    },
    downloadImages: async (refs) => cached(refs),
    downloadFiles: async (refs) => cached(refs),
    async close() {
      stopped = true;
      controller.abort();
      owners.clear();
    },
  };
}
