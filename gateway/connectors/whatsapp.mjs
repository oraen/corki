import { status, chunks, emit } from "./protocol.mjs";
export async function create({ state_directory, require, load, incoming }) {
  const api = await load("@whiskeysockets/baileys");
  const { state, saveCreds } = await api.useMultiFileAuthState(state_directory);
  const logger = require("pino")({ level: "silent" });
  let socket,
    stopped = false;
  return {
    async start() {
      socket = api.default({
        auth: state,
        logger,
        markOnlineOnConnect: false,
        syncFullHistory: false,
        shouldSyncHistoryMessage: () => false,
      });
      socket.ev.on("creds.update", saveCreds);
      socket.ev.on("connection.update", async (value) => {
        if (value.qr)
          status("qr", {
            qr: await require("qrcode").toDataURL(value.qr, {
              margin: 2,
              width: 260,
            }),
          });
        if (value.connection === "open") {
          emit({ method: "account", params: { id: socket.user.id } });
          status("online");
        }
        if (value.connection === "close" && !stopped) {
          const loggedOut =
            value.lastDisconnect?.error?.output?.statusCode ===
            api.DisconnectReason.loggedOut;
          status("error", { code: loggedOut ? "credentials" : "network" });
          // Supervisor reopens the account from persisted credentials with backoff.
          setTimeout(() => process.exit(1), 100);
        }
      });
      let chain = Promise.resolve();
      socket.ev.on("messages.upsert", (update) => {
        if (update.type !== "notify") return; // No replay of history sync/append batches.
        chain = chain
          .then(async () => {
            for (const item of update.messages) {
              const jid = item.key.remoteJid;
              if (
                item.key.fromMe ||
                !jid ||
                !(jid.endsWith("@s.whatsapp.net") || jid.endsWith("@lid"))
              )
                continue;
              const body = api.normalizeMessageContent(item.message);
              const text =
                body?.conversation || body?.extendedTextMessage?.text;
              const media=['imageMessage','videoMessage','audioMessage','documentMessage','stickerMessage',
                'contactMessage','contactsArrayMessage','locationMessage','liveLocationMessage',
                'pollCreationMessage','pollCreationMessageV2','pollCreationMessageV3'].find(key=>body?.[key]);
              if (!text && !media) continue;
              await incoming({
                id: item.key.id,
                sender: jid,
                conversation: jid,
                name: item.pushName || jid,
                kind: "direct",
                ...(media?{text:'[非文本消息]',unsupported:true}:{text}),
              });
            }
          })
          .catch(() => {
            status("error", { code: "protocol" });
          });
      });
    },
    async send({ message, text }) {
      if (!socket) throw new Error("not connected");
      for (const part of chunks(text))
        await socket.sendMessage(message.conversation, { text: part });
    },
    async close() {
      stopped = true;
      socket?.end(new Error("gateway stopped"));
    },
  };
}
