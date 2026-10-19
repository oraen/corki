// One bounded logical message at a time; Python serializes fragment sequences.
export const MAX_MESSAGE = 64 * 1024 * 1024;
export class MessageAssembler {
  private active: { id: string; index: number; bytes: number; received: number; chunks: string[] } | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  constructor(private onTimeout: () => void, private timeout = 30000) {}
  clear() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.active = null;
  }
  accept(raw: string): unknown | undefined {
    const message = JSON.parse(raw);
    if (message?.method !== 'transport.chunk') {
      if (this.active) throw new Error('Interrupted fragment sequence');
      return message;
    }
    const p = message.params;
    if (message.jsonrpc !== '2.0' || !p || typeof p.id !== 'string' ||
        !/^[a-f0-9]{32}$/.test(p.id) || !Number.isSafeInteger(p.index) || p.index < 0 || p.index >= 1024 ||
        !Number.isSafeInteger(p.bytes) || p.bytes <= 0 || p.bytes > MAX_MESSAGE ||
        typeof p.last !== 'boolean' || typeof p.text !== 'string' || !p.text.length || p.text.length > 256000)
      throw new Error('Invalid fragment');
    if (!this.active) {
      if (p.index !== 0) throw new Error('Missing first fragment');
      this.active = { id: p.id, index: 0, bytes: p.bytes, received: 0, chunks: [] };
      this.timer = setTimeout(() => { this.clear(); this.onTimeout(); }, this.timeout);
    }
    const current = this.active;
    if (p.id !== current.id || p.index !== current.index || p.bytes !== current.bytes)
      throw new Error('Fragment sequence mismatch');
    current.received += Buffer.byteLength(p.text);
    if (current.received > current.bytes) throw new Error('Fragment budget exceeded');
    current.chunks.push(p.text);
    current.index++;
    if (!p.last) {
      if (current.received === current.bytes) throw new Error('Missing final marker');
      return undefined;
    }
    if (current.received !== current.bytes) throw new Error('Incomplete message');
    const result = current.chunks.join('');
    this.clear();
    const decoded = JSON.parse(result);
    if (decoded?.method === 'transport.chunk') throw new Error('Nested fragment');
    return decoded;
  }
}
