/** Clipboard blobs have no filesystem path. Store them through the existing asset boundary. */
import {randomUUID} from "node:crypto";
import type {Attachment} from "../shared/protocol";
import {saveAsset, IMAGE_ASSET_LIMIT} from "./assets";
const TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);
export async function importPastedImages(home: string, origin: string, value: unknown): Promise<Attachment[]> {
  if (!Array.isArray(value) || !value.length || value.length > 8)
    throw new Error("最多添加 8 个附件");
  let total = 0;
  const images = value.map(image => {
    if (!image || typeof image !== "object" || !TYPES.has(image.mime) ||
        !(image.bytes instanceof ArrayBuffer) || typeof image.name !== "string" || image.name.length > 200)
      throw new Error("剪贴板图片格式不受支持");
    total += image.bytes.byteLength;
    if (!image.bytes.byteLength || image.bytes.byteLength > IMAGE_ASSET_LIMIT || total > IMAGE_ASSET_LIMIT)
      throw new Error("图片过大，无法在本机处理");
    return image;
  });
  const result: Attachment[] = [];
  for (const image of images) {
    const assetId = await saveAsset(home, Buffer.from(image.bytes), image.mime);
    result.push({id: randomUUID(), name: image.name || "粘贴图片.png", type: "image", assetId,
      data: `${origin}/attachments/${assetId}`});
  }
  return result;
}
