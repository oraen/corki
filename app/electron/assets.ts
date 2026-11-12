import { createHash, randomUUID } from "node:crypto";
import { mkdir, writeFile, rename, rm, readFile, open } from "node:fs/promises";
import path from "node:path";
const MIME = new Set([
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/gif",
  "text/plain",
]);
export const IMAGE_ASSET_LIMIT = 512 * 1024 * 1024;
export const assetLimit = (mime: string) => mime.startsWith("image/") ? IMAGE_ASSET_LIMIT : 1_000_000;
export async function saveAsset(home: string, buffer: Buffer, mime: string) {
  if (!MIME.has(mime) || buffer.length > assetLimit(mime))
    throw new Error("附件类型或大小无效");
  const id = createHash("sha256")
    .update(mime + "\0")
    .update(buffer)
    .digest("hex");
  const root = path.join(home, "app", "attachments");
  await mkdir(root, { recursive: true, mode: 0o700 });
  const temporary = path.join(root, randomUUID() + ".tmp");
  try {
    await writeFile(temporary, buffer, { mode: 0o600 });
    await rename(temporary, path.join(root, id + ".bin"));
    await writeFile(
      temporary,
      JSON.stringify({ version: 1, mime, size: buffer.length }),
      { mode: 0o600 },
    );
    await rename(temporary, path.join(root, id + ".json"));
  } finally {
    await rm(temporary, { force: true });
  }
  return id;
}
export async function readAsset(home: string, id: string) {
  if (!/^[a-f0-9]{64}$/.test(id)) throw new Error("附件标识无效");
  const root = path.join(home, "app", "attachments");
  const meta = JSON.parse(
    await readFile(path.join(root, id + ".json"), "utf8"),
  );
  if (meta.version !== 1 || !MIME.has(meta.mime) || !Number.isSafeInteger(meta.size) || meta.size < 0 || meta.size > assetLimit(meta.mime))
    throw new Error("附件记录无效");
  const buffer = await readBoundedFile(path.join(root, id + ".bin"), assetLimit(meta.mime));
  if (
    buffer.length !== meta.size ||
    createHash("sha256")
      .update(meta.mime + "\0")
      .update(buffer)
      .digest("hex") !== id
  )
    throw new Error("附件内容已改变，请重新添加");
  return { buffer, mime: meta.mime };
}

export async function readBoundedFile(file: string, limit: number) {
  const handle = await open(file, "r");
  try {
    const buffer = Buffer.alloc(limit + 1);
    let offset = 0;
    while (offset < buffer.length) {
      const {bytesRead} = await handle.read(buffer, offset, buffer.length - offset, null);
      if (!bytesRead) break;
      offset += bytesRead;
    }
    if (offset > limit) throw new Error("文件超过大小限制");
    return buffer.subarray(0, offset);
  } finally { await handle.close(); }
}
