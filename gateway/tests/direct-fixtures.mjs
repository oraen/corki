export async function memoryAuth() {
  const inbox = new Map(),
    aliases = new Map();
  return {
    state: { creds: { registered: true }, keys: {} },
    saveCreds: async () => {},
    reset() {
      this.state.creds = { registered: false };
      inbox.clear();
      aliases.clear();
    },
    close() {},
    enqueue(item) {
      inbox.set(item.key.remoteJid + ":" + item.key.id, item);
    },
    next() {
      const entry = inbox.entries().next().value;
      return entry ? { id: entry[0], item: entry[1] } : null;
    },
    acknowledge(id) {
      inbox.delete(id);
    },
    identity(ids) {
      const canonical =
        ids.map((id) => aliases.get(id)).find(Boolean) ||
        ids.find((id) => id.endsWith("@s.whatsapp.net")) ||
        ids[0];
      for (const id of ids) aliases.set(id, canonical);
      return canonical;
    },
  };
}
