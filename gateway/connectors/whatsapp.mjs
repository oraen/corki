import { status, chunks, emit, unsupportedContent } from "./protocol.mjs";
import {
  cache,
  cached,
  wait,
  bounded,
  failure,
  serial,
} from "./direct-media.mjs";
import { createAuth } from "./whatsapp-auth.mjs";

export async function create({
  state_directory,
  require,
  load,
  incoming,
  fatal = () => {},
  authFactory = createAuth,
}) {
  const api = await load("@whiskeysockets/baileys");
  let auth;
  try {
    auth = await authFactory(state_directory, api);
  } catch {
    status("error", { code: "whatsapp_auth" });
    throw new Error("WhatsApp authentication storage unavailable");
  }
  const logger = require("pino")({ level: "silent" }),
    media = cache(state_directory),
    outbox = serial();
  const controller = new AbortController(),
    owners = new Map();
  let socket,
    stopped = false,
    connected = false,
    generation = 0,
    retries = 0,
    reconnect,
    draining,
    needsReset = false;
  const direct = (jid) =>
    typeof jid === "string" &&
    jid.length <= 100 &&
    /^\d+(?::\d+)?@(s\.whatsapp\.net|lid)$/.test(jid);
  const normalize = (jid) =>
    api.jidNormalizedUser
      ? api.jidNormalizedUser(jid)
      : jid.replace(/:\d+@/, "@");
  const fail = (code) => {
    status("error", { code });
  };
  async function admit(item) {
    const jid = item.key.remoteJid,
      body = api.normalizeMessageContent(item.message);
    if (!body) return;
    const alt = item.key.remoteJidAlt;
    const candidates = [normalize(jid)];
    if (direct(alt)) candidates.push(normalize(alt));
    if (jid.endsWith("@lid")) {
      const pn = await socket.signalRepository?.lidMapping?.getPNForLID?.(
        normalize(jid),
      );
      if (direct(pn)) candidates.push(normalize(pn));
    }
    const sender = auth.identity(candidates);
    let text = body.conversation || body.extendedTextMessage?.text || "",
      extra = {};
    const type = ["imageMessage", "videoMessage", "documentMessage"].find(
      (k) => body[k],
    );
    if (type) {
      const kind =
          type === "imageMessage"
            ? "image"
            : type === "videoMessage"
              ? "video"
              : "file",
        content = body[type];
      let result;
      try {
        if (
          Number(content.fileLength) >
          (kind === "image" ? 16 : 32) * 1024 * 1024
        )
          throw new Error("large");
        const signal = AbortSignal.any([
          controller.signal,
          AbortSignal.timeout(60000),
        ]);
        const stream = await bounded(
          api.downloadMediaMessage(
            item,
            "stream",
            {},
            { logger, reuploadRequest: socket.updateMediaMessage },
          ),
          signal,
          (stream) => stream.destroy?.(),
        );
        const abort = () => stream.destroy?.(new Error("Download stopped"));
        signal.addEventListener("abort", abort, { once: true });
        try {
          signal.throwIfAborted();
          result = await media.save(stream, kind, signal);
        } finally {
          signal.removeEventListener("abort", abort);
          stream.destroy?.();
        }
      } catch {
        result = { error: "附件下载失败或超过大小限制，请重新发送" };
      }
      text = content.caption || text;
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
                content.fileName ||
                (kind === "video" ? "video.mp4" : "attachment"),
            },
          ],
          file_positions: [position],
        };
      }
    } else if (!text) {
      const type = [
        "audioMessage",
        "stickerMessage",
        "contactMessage",
        "contactsArrayMessage",
        "locationMessage",
        "liveLocationMessage",
        "pollCreationMessage",
        "pollCreationMessageV2",
        "pollCreationMessageV3",
      ].find((k) => body[k]);
      if (!type) return;
      extra = unsupportedContent(type);
      text = extra.text;
    }
    const quoted = body.extendedTextMessage?.contextInfo?.quotedMessage;
    const quoteText = quoted?.conversation || quoted?.extendedTextMessage?.text;
    if (quoteText)
      text += "\n\n引用消息：\n" + String(quoteText).slice(0, 4000);
    const accepted = await incoming({
      id: sender + ":" + item.key.id,
      external_id: item.key.id,
      sender,
      conversation: normalize(jid),
      name: item.pushName || sender,
      kind: "direct",
      text,
      ...extra,
    });
    if (accepted?.accepted !== true) throw new Error("Message not admitted");
  }
  function drain() {
    if (draining || stopped || !connected) return;
    draining = (async () => {
      while (!stopped && connected) {
        const entry = auth.next();
        if (!entry) break;
        try {
          await admit(entry.item);
          auth.acknowledge(entry.id);
        } catch {
          if (!stopped) {
            fail("protocol");
            await wait(2000, controller.signal).catch(() => {});
          }
        }
      }
    })()
      .catch(() => {
        if (!stopped) fail("protocol");
      })
      .finally(() => {
        draining = null;
        if (!stopped && connected && auth.next()) drain();
      });
  }
  async function open() {
    if (stopped) return;
    const version = ++generation;
    connected = false;
    if (reconnect) {
      clearTimeout(reconnect);
      reconnect = null;
    }
    socket?.end(new Error("reconnecting"));
    status("connecting");
    const current = api.default({
      auth: {
        creds: auth.state.creds,
        keys: api.makeCacheableSignalKeyStore
          ? api.makeCacheableSignalKeyStore(auth.state.keys, logger)
          : auth.state.keys,
      },
      logger,
      browser: ["Corki", "Desktop", "1.0.0"],
      markOnlineOnConnect: false,
      syncFullHistory: false,
      shouldSyncHistoryMessage: () => false,
      qrTimeout: 60000,
      connectTimeoutMs: 30000,
      defaultQueryTimeoutMs: 30000,
      generateHighQualityLinkPreview: false,
    });
    socket = current;
    current.ev.on("creds.update", () => {
      if (version !== generation || stopped) return;
      auth.saveCreds().catch(() => {
        fail("whatsapp_auth");
        fatal();
      });
    });
    current.ev.on("connection.update", (value) => {
      void (async () => {
        if (stopped || version !== generation) return;
        if (value.qr) {
          const qr = await require("qrcode").toDataURL(value.qr, {
            margin: 2,
            width: 260,
          });
          if (!stopped && version === generation) status("qr", { qr });
        }
        if (value.connection === "open") {
          if (!direct(current.user?.id)) throw new Error("missing identity");
          await auth.saveCreds();
          if (stopped || version !== generation) return;
          connected = true;
          retries = 0;
          emit({
            method: "account",
            params: { id: normalize(current.user.id) },
          });
          status("online");
          drain();
        }
        if (value.connection === "close") {
          const fence = ++generation;
          socket = null;
          connected = false;
          owners.clear();
          const code = value.lastDisconnect?.error?.output?.statusCode;
          if (
            code === api.DisconnectReason.loggedOut ||
            code === api.DisconnectReason.badSession
          ) {
            needsReset = true;
            status("login", { code: "credentials" });
            return;
          }
          if (code === api.DisconnectReason.connectionReplaced) {
            fail("whatsapp_replaced");
            return;
          }
          if (code === api.DisconnectReason.forbidden) {
            fail("whatsapp_forbidden");
            return;
          }
          if (code === api.DisconnectReason.multideviceMismatch) {
            fail("whatsapp_version");
            return;
          }
          if (!auth.state.creds.registered) {
            status("login", { code: "expired" });
            return;
          }
          const immediate = code === api.DisconnectReason.restartRequired;
          await auth.saveCreds();
          if (stopped || fence !== generation) return;
          status("connecting");
          reconnect = setTimeout(
            () => {
              reconnect = null;
              void open().catch(() => {
                fail("network");
                fatal();
              });
            },
            immediate ? 0 : Math.min(60000, 2 ** Math.min(++retries, 6) * 1000),
          );
        }
      })().catch(() => {
        if (!stopped && version === generation) {
          fail("whatsapp_auth");
          fatal();
        }
      });
    });
    current.ev.on("messages.upsert", (update) => {
      if (stopped || version !== generation || update.type !== "notify") return;
      try {
        for (const item of update.messages || []) {
          if (
            item.key?.fromMe ||
            !direct(item.key?.remoteJid) ||
            typeof item.key.id !== "string" ||
            item.key.id.length > 150 ||
            !item.message
          )
            continue;
          auth.enqueue(item);
        }
        drain();
      } catch {
        fail("whatsapp_auth");
        fatal();
      }
    });
  }
  return {
    async start() {
      if (auth.state.creds.registered) await open();
      else status("login");
    },
    async login({ refresh = false } = {}) {
      if (connected) return { requested: false };
      if (socket && !refresh && !needsReset) return { requested: false };
      if (needsReset) {
        generation++;
        socket?.end(new Error("login expired"));
        auth.reset();
        needsReset = false;
      }
      await open();
      return { requested: true };
    },
    async send({ message, text, images = [], files = [] }) {
      const peer = message?.conversation;
      if (!connected || !socket || !direct(peer))
        throw failure("whatsapp", "WhatsApp 未连接或收件人无效");
      if (
        !Array.isArray(images) ||
        !Array.isArray(files) ||
        images.length + files.length > 8
      )
        throw failure("whatsapp", "附件数量无效");
      const current = socket,
        version = generation;
      const signal = AbortSignal.any([
        controller.signal,
        AbortSignal.timeout(150000),
      ]);
      return outbox(peer, async () => {
        let delivered = 0;
        try {
          const content = chunks(text).map((text) => ({ text }));
          for (const image of images) {
            const raw = await media.read(image, "image");
            content.push(
              image.mime === "image/gif"
                ? {
                    document: raw,
                    mimetype: "image/gif",
                    fileName: "image.gif",
                  }
                : {
                    image: raw,
                    mimetype: image.mime,
                    jpegThumbnail: await media.thumbnail(image),
                  },
            );
          }
          for (const file of files) {
            const raw = await media.read(file, "file");
            content.push(
              file.kind === "video"
                ? // Suppress Baileys' optional ffmpeg subprocess on Windows.
                  {
                    video: raw,
                    mimetype: "video/mp4",
                    jpegThumbnail: Buffer.alloc(0),
                  }
                : {
                    document: raw,
                    mimetype: "application/octet-stream",
                    fileName: file.name || "attachment",
                  },
            );
          }
          if (!content.length)
            throw failure("whatsapp", "请输入消息或添加附件");
          for (const part of content) {
            signal.throwIfAborted();
            if (
              stopped ||
              !connected ||
              current !== socket ||
              version !== generation
            )
              throw new Error("disconnected");
            const result = await bounded(
              current.sendMessage(peer, part, { mediaUploadTimeoutMs: 45000 }),
              AbortSignal.any([signal, AbortSignal.timeout(45000)]),
            );
            if (!result?.key?.id)
              throw failure(
                "whatsapp",
                "WhatsApp 未返回发送确认，送达情况未知",
              );
            delivered++;
          }
        } catch (e) {
          const error = e.delivery
            ? e
            : failure(
                "whatsapp",
                "发送失败或连接中断，送达情况未知；未自动重发",
              );
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
      if (!connected || !direct(peer)) return { supported: false };
      if (!active && owners.get(peer) !== id) return { supported: true };
      if (active) owners.set(peer, id);
      else owners.delete(peer);
      try {
        await socket.sendPresenceUpdate(active ? "composing" : "paused", peer);
        return { supported: true };
      } catch {
        return { supported: false };
      }
    },
    downloadImages: async (refs) => cached(refs),
    downloadFiles: async (refs) => cached(refs),
    async close() {
      if (stopped) return;
      stopped = true;
      generation++;
      connected = false;
      if (reconnect) clearTimeout(reconnect);
      controller.abort();
      owners.clear();
      socket?.end(new Error("gateway stopped"));
      try {
        if (draining) await draining;
        await auth.saveCreds();
      } finally {
        auth.close();
      }
    },
  };
}
