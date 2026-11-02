import readline from "node:readline";
import { randomUUID } from "node:crypto";

// Do not let SDK console logging corrupt the JSON-lines protocol.
console.log =
  console.info =
  console.debug =
    (...args) => console.error(...args);
export const emit = (value) =>
  process.stdout.write(JSON.stringify(value) + "\n");
export const status = (state, extra = {}) =>
  emit({ method: "status", params: { state, ...extra } });
export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
export function unsupportedContent(type,caption='') {
  const label=({picture:'图片消息',image:'图片消息',photo:'图片消息',video:'视频消息',
    audio:'音频消息',voice:'语音消息',file:'文件消息',document:'文件消息',
    sticker:'贴纸消息',animation:'动画消息',location:'位置消息'})[type] || '不支持的消息类型';
  return {text:(typeof caption==='string' && caption.trim()?caption+'\n':'')+'['+label+']',unsupported:true,message_type:type};
}
export function chunks(text, limit = 3000) {
  if (!Number.isInteger(limit) || limit < 2) throw new Error("invalid chunk limit");
  const output = [];
  let part = "";
  // Bound UTF-16 units too, without cutting a surrogate pair in half.
  for (const point of text) {
    if (part.length + point.length > limit) {
      output.push(part);
      part = "";
    }
    part += point;
  }
  if (part) output.push(part);
  return output;
}
export function protocol(handlers) {
  const pending = new Map();
  const lines = readline.createInterface({
    input: process.stdin,
    crlfDelay: Infinity,
  });
  process.stdin.on("end", () => process.exit(0)); // Parent loss ends transport ownership.
  lines.on("line", async (line) => {
    if (Buffer.byteLength(line) > 1024 * 1024) process.exit(2);
    let value;
    try {
      value = JSON.parse(line);
      if (!value.method) {
        const waiter = pending.get(value.id);
        if (waiter) {
          pending.delete(value.id);
          clearTimeout(waiter.timer);
          value.error
            ? waiter.reject(new Error("gateway rejected message"))
            : waiter.resolve(value.result);
        }
        return;
      }
      const handler = handlers[value.method];
      if (!handler) throw new Error("unknown method");
      const result = await handler(value.params || {});
      emit({ id: value.id, result: result ?? {} });
    } catch (error) {
      emit({ id: value?.id, error: "connector request failed",
        ...(error?.delivery ? {diagnostic:error.delivery} : {}) });
    }
  });
  return (params) =>
    new Promise((resolve, reject) => {
      if (pending.size >= 100) return reject(new Error("backpressure"));
      const id = randomUUID();
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error("admission timeout"));
      }, 15000);
      pending.set(id, { resolve, reject, timer });
      emit({ id, method: "inbound", params });
    });
}
