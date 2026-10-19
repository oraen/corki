import type {HostEvent} from '../shared/protocol';
// Replay is a cache, never a substitute for the host's authoritative snapshot.
export class EventBuffer {
  private rows: {event: HostEvent; bytes: number}[] = [];
  private bytes = 0;
  private floor = 0;
  private latest = 0;
  private instance: string | null = null;
  clear() { this.rows=[]; this.bytes=0; this.floor=0; this.latest=0; this.instance=null; }
  push(event: HostEvent) {
    if(this.instance!==event.instance) { this.clear(); this.instance=event.instance; }
    if(event.sequence<=this.latest)return;
    if(this.latest && event.sequence!==this.latest+1) this.floor=event.sequence-1;
    this.latest=event.sequence;
    const bytes=new TextEncoder().encode(JSON.stringify(event)).length;
    this.rows.push({event,bytes});this.bytes+=bytes;
    while(this.rows.length>512 || this.bytes>1024*1024) {
      const removed=this.rows.shift()!;this.bytes-=removed.bytes;
      this.floor=Math.max(this.floor,removed.event.sequence);
    }
  }
  after(instance: string, sequence: number): HostEvent[] | null {
    if(this.instance!==null && this.instance!==instance || sequence<this.floor)return null;
    return this.rows.filter(row=>row.event.sequence>sequence).map(row=>row.event);
  }
}
