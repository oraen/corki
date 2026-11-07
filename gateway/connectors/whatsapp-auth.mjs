// Credentials and Signal key updates commit atomically before returning to Baileys.
import { mkdir, readdir, readFile, chmod, lstat } from "node:fs/promises";
import path from "node:path";

export async function createAuth(directory, api) {
  const { DatabaseSync } = await import("node:sqlite");
  await mkdir(directory, { recursive: true, mode: 0o700 });
  const file = path.join(directory, "auth.sqlite");
  try {
    if ((await lstat(file)).isSymbolicLink())
      throw new Error("Invalid auth path");
  } catch (e) {
    if (e.code !== "ENOENT") throw e;
  }
  const db = new DatabaseSync(file);
  try {
    db.exec(
      "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA busy_timeout=5000; CREATE TABLE IF NOT EXISTS auth(type TEXT NOT NULL,id TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(type,id))",
    );
    db.exec(
      "CREATE TABLE IF NOT EXISTS inbox(id TEXT PRIMARY KEY,payload TEXT NOT NULL); CREATE TABLE IF NOT EXISTS aliases(id TEXT PRIMARY KEY,canonical TEXT NOT NULL)",
    );
    await chmod(file, 0o600);
    const get = db.prepare("SELECT value FROM auth WHERE type=? AND id=?"),
      put = db.prepare("INSERT OR REPLACE INTO auth VALUES(?,?,?)"),
      del = db.prepare("DELETE FROM auth WHERE type=? AND id=?");
    const encode = (value) => JSON.stringify(value, api.BufferJSON.replacer),
      decode = (value) => JSON.parse(value, api.BufferJSON.reviver);
    const legacyId = (id) => id.replace(/\//g, "__").replace(/:/g, "-");
    const transaction = (fn) => {
      db.exec("BEGIN IMMEDIATE");
      try {
        fn();
        db.exec("COMMIT");
      } catch (e) {
        db.exec("ROLLBACK");
        throw e;
      }
    };
    let saved = get.get("creds", "self");
    if (!saved) {
      // Upgrade existing local installations once, leaving original files intact.
      let old;
      try {
        const legacy = path.join(directory, "creds.json"),
          stat = await lstat(legacy);
        if (
          !stat.isFile() ||
          stat.isSymbolicLink() ||
          stat.size > 8 * 1024 * 1024
        )
          throw new Error("Invalid legacy credentials");
        old = decode(await readFile(legacy, "utf8"));
      } catch (e) {
        if (e.code !== "ENOENT") {
          throw e;
        }
      }
      if (old) {
        const keys = [],
          types = [
            "app-state-sync-version",
            "app-state-sync-key",
            "sender-key-memory",
            "lid-mapping",
            "device-list",
            "sender-key",
            "pre-key",
            "session",
            "tctoken",
          ];
        for (const name of await readdir(directory)) {
          const type = types.find(
            (t) => name.startsWith(t + "-") && name.endsWith(".json"),
          );
          if (!type) continue;
          const entry = path.join(directory, name),
            stat = await lstat(entry);
          if (
            !stat.isFile() ||
            stat.isSymbolicLink() ||
            stat.size > 8 * 1024 * 1024
          ) {
            throw new Error("Invalid legacy auth file");
          }
          keys.push([
            "legacy:" + type,
            name.slice(type.length + 1, -5),
            encode(decode(await readFile(entry, "utf8"))),
          ]);
        }
        transaction(() => {
          put.run("creds", "self", encode(old));
          for (const key of keys) put.run(...key);
        });
        saved = get.get("creds", "self");
      }
    }
    const state = {
      creds: saved ? decode(saved.value) : api.initAuthCreds(),
      keys: {
        get: async (type, ids) =>
          Object.fromEntries(
            ids.map((id) => {
              let row = get.get(type, id);
              if (!row) {
                row = get.get("legacy:" + type, legacyId(id));
                if (row) put.run(type, id, row.value);
              }
              let value = row ? decode(row.value) : undefined;
              if (type === "app-state-sync-key" && value)
                value = api.proto.Message.AppStateSyncKeyData.fromObject(value);
              return [id, value];
            }),
          ),
        set: async (data) =>
          transaction(() => {
            for (const [type, values] of Object.entries(data))
              for (const [id, value] of Object.entries(values)) {
                if (value == null) del.run(type, id);
                else put.run(type, id, encode(value));
                del.run("legacy:" + type, legacyId(id));
              }
          }),
      },
    };
    if (!saved) put.run("creds", "self", encode(state.creds));
    let closed = false;
    return {
      state,
      saveCreds: async () => put.run("creds", "self", encode(state.creds)),
      enqueue(item) {
        const id = item.key.remoteJid + ":" + item.key.id;
        if (
          db.prepare("SELECT count(*) AS n FROM inbox").get().n >= 1000 &&
          !db.prepare("SELECT 1 FROM inbox WHERE id=?").get(id)
        )
          throw new Error("Inbox full");
        db.prepare("INSERT OR IGNORE INTO inbox VALUES(?,?)").run(
          id,
          encode(item),
        );
      },
      next() {
        const row = db
          .prepare("SELECT id,payload FROM inbox ORDER BY rowid LIMIT 1")
          .get();
        return row ? { id: row.id, item: decode(row.payload) } : null;
      },
      acknowledge(id) {
        db.prepare("DELETE FROM inbox WHERE id=?").run(id);
      },
      identity(candidates) {
        let canonical;
        transaction(() => {
          for (const id of candidates) {
            const row = db
              .prepare("SELECT canonical FROM aliases WHERE id=?")
              .get(id);
            if (row) {
              canonical = row.canonical;
              break;
            }
          }
          canonical ||=
            candidates.find((id) => id.endsWith("@s.whatsapp.net")) ||
            candidates[0];
          for (const id of candidates)
            db.prepare("INSERT OR IGNORE INTO aliases VALUES(?,?)").run(
              id,
              canonical,
            );
        });
        return canonical;
      },
      reset() {
        const creds = api.initAuthCreds();
        transaction(() => {
          db.exec("DELETE FROM auth; DELETE FROM inbox; DELETE FROM aliases");
          put.run("creds", "self", encode(creds));
        });
        state.creds = creds;
      },
      close() {
        if (!closed) {
          closed = true;
          db.close();
        }
      },
    };
  } catch (error) {
    try {
      db.close();
    } catch {}
    throw error;
  }
}
