import { createHash, randomUUID } from "node:crypto";
import {
  mkdir,
  writeFile,
  rename,
  unlink,
  readFile,
  lstat,
} from "node:fs/promises";
import path from "node:path";

export const IMAGE_LIMIT = 16 * 1024 * 1024,
  FILE_LIMIT = 32 * 1024 * 1024;
export function wait(ms, signal) {
  signal?.throwIfAborted();
  return new Promise((resolve, reject) => {
    const done = () => {
      signal?.removeEventListener("abort", abort);
      resolve();
    };
    const timer = setTimeout(done, ms);
    const abort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    signal?.addEventListener("abort", abort, { once: true });
  });
}
export function bounded(promise, signal, dispose = () => {}) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const abort = () => {
      if (!settled) {
        settled = true;
        reject(signal.reason);
      }
    };
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
    Promise.resolve(promise).then(
      (value) => {
        signal.removeEventListener("abort", abort);
        if (settled) {
          dispose(value);
          return;
        }
        settled = true;
        resolve(value);
      },
      (error) => {
        signal.removeEventListener("abort", abort);
        if (!settled) {
          settled = true;
          reject(error);
        }
      },
    );
  });
}
export function cache(directory) {
  return {
    async save(source, kind, signal) {
      const limit = kind === "image" ? IMAGE_LIMIT : FILE_LIMIT,
        parts = [];
      let size = 0,
        temp;
      try {
        for await (const chunk of source) {
          signal?.throwIfAborted();
          size += chunk.length;
          if (size > limit) throw new Error("附件超过大小限制");
          parts.push(Buffer.from(chunk));
        }
        const raw = Buffer.concat(parts),
          id = createHash("sha256").update(raw).digest("hex");
        const root = path.join(
          directory,
          kind === "image" ? "images" : "files",
        );
        await mkdir(root, { recursive: true, mode: 0o700 });
        temp = path.join(root, randomUUID() + ".tmp");
        await writeFile(temp, raw, { flag: "wx", mode: 0o600 });
        await rename(temp, path.join(root, id + ".bin"));
        temp = null;
        return { id };
      } finally {
        if (temp) await unlink(temp).catch(() => {});
      }
    },
    async read(item, kind) {
      if (!item || !/^[a-f0-9]{64}$/.test(item.id))
        throw new Error("附件标识无效");
      const file = path.join(
        directory,
        kind === "image" ? "images" : "files",
        item.id + ".bin",
      );
      const stat = await lstat(file),
        limit = kind === "image" ? IMAGE_LIMIT : FILE_LIMIT;
      if (!stat.isFile() || stat.isSymbolicLink() || stat.size > limit)
        throw new Error("附件不存在或已清理");
      const raw = await readFile(file);
      if (
        raw.length > limit ||
        createHash("sha256").update(raw).digest("hex") !== item.id
      )
        throw new Error("附件校验失败");
      return raw;
    },
    async thumbnail(item) {
      if (!item || !/^[a-f0-9]{64}$/.test(item.id))
        throw new Error("附件标识无效");
      try {
        const file = path.join(directory, "images", item.id + ".thumb.jpg"),
          stat = await lstat(file);
        if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 512 * 1024)
          return Buffer.alloc(0);
        return await readFile(file);
      } catch {
        return Buffer.alloc(0);
      }
    },
  };
}
export function cached(refs) {
  if (!Array.isArray(refs) || refs.length > 8) throw new Error("附件数量无效");
  return {
    images: refs.map((ref) =>
      ref.error ? { error: "附件下载失败，请重新发送" } : { id: ref.id },
    ),
  };
}
export function failure(provider, message, code) {
  const error = new Error(message);
  error.delivery = {
    provider,
    message,
    ...(Number.isSafeInteger(code) ? { code } : {}),
  };
  return error;
}
export function serial() {
  const tails = new Map();
  return (key, job) => {
    const task = (tails.get(key) || Promise.resolve())
      .catch(() => {})
      .then(job);
    tails.set(key, task);
    task
      .finally(() => {
        if (tails.get(key) === task) tails.delete(key);
      })
      .catch(() => {});
    return task;
  };
}
