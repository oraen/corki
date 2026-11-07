import type { HostEvent } from "../shared/protocol";

export type SoundKind = "complete" | "attention" | "error";
const priority = { complete: 0, attention: 1, error: 2 };
interface Run {
  turn?: string;
  terminal?: string;
  goal?: string;
  pending: Set<string>;
  failed: boolean;
}

// Consume live host events in the main process, never snapshots or replay RPCs.
// One burst produces one sound, with errors/input taking precedence over success.
// Reference: openai/codex f5ac7a7, tui/src/chatwidget/notifications.rs:
// turn completion, approvals/elicitation, blocking and async questions; priority coalescing.
export class SoundNotifications {
  private instance = "";
  private sequence = 0;
  private offline = false;
  private runs = new Map<string, Run>();
  private seen = new Set<string>();
  private queued = new Map<string, SoundKind>();
  private timer?: ReturnType<typeof setTimeout>;
  private enabled = true;
  constructor(private play: (kind: SoundKind) => void, private delay = 250) {}

  setEnabled(enabled: boolean) {
    this.enabled = enabled;
    if (!enabled) this.clearQueue();
  }
  private clearQueue() {
    clearTimeout(this.timer);
    this.timer = undefined;
    this.queued.clear();
  }
  private remember(key: string): boolean {
    if (this.seen.has(key)) return false;
    this.seen.add(key);
    if (this.seen.size > 1024) this.seen.delete(this.seen.values().next().value!);
    return true;
  }
  private enqueue(kind: SoundKind, thread = "host") {
    if (!this.enabled) return;
    const previous = this.queued.get(thread);
    if (!previous || priority[kind] > priority[previous]) this.queued.set(thread, kind);
    if (this.timer) return;
    this.timer = setTimeout(() => {
      this.timer = undefined;
      const kinds = [...this.queued.values()];
      this.queued.clear();
      if (!this.enabled || !kinds.length) return;
      const kind = kinds.reduce((a, b) => priority[a] >= priority[b] ? a : b);
      try { this.play(kind); } catch { /* Audio must never interrupt task delivery. */ }
    }, this.delay);
  }
  submissionFailed(requestId: string) {
    if (this.remember(`submit:${requestId}`)) this.enqueue("error");
  }
  disconnected() {
    if (this.offline) return;
    this.offline = true;
    // An unavailable backend supersedes any completion still in the debounce window.
    this.clearQueue();
    this.enqueue("error");
  }
  accept(event: HostEvent) {
    if (event.instance !== this.instance) {
      this.instance = event.instance;
      this.sequence = 0;
      this.runs.clear();
      this.seen.clear();
      this.clearQueue();
    }
    if (event.sequence <= this.sequence) return;
    this.sequence = event.sequence;
    this.offline = false;
    const id = event.threadId;
    if (!id) return;
    let run = this.runs.get(id);
    if (!run) {
      run = { pending: new Set(), failed: false };
      this.runs.set(id, run);
      if (this.runs.size > 512) {
        // Retire the oldest idle entry; do not discard another active task.
        for (const [key, value] of this.runs) {
          if (value.terminal) { this.runs.delete(key); break; }
        }
      }
    }
    const data = event.data;
    const turn = String(data.turn_id || run.turn || "");
    switch (event.type) {
      case "TurnStarted":
        if (run.turn === turn) break;
        run.turn = turn;
        run.terminal = undefined;
        run.failed = false;
        this.queued.delete(id);
        break;
      case "GoalUpdated":
        run.goal = data.status;
        break;
      case "approval":
      case "UserInputRequested": {
        const request = String(data.request_id || data.call_id || `${event.type}:${turn}`);
        run.pending.add(request);
        if (this.remember(`input:${id}:${request}`)) this.enqueue("attention", id);
        break;
      }
      case "interaction.resolved":
        run.pending.delete(String(data.requestId));
        break;
      case "run.settled":
        run.pending.clear();
        break;
      case "TurnFailed":
      case "host.error":
        if (!run.failed) this.enqueue("error", id);
        run.failed = true;
        run.terminal = "failed";
        break;
      case "TurnCancelled":
        run.terminal = "cancelled";
        run.pending.clear();
        this.queued.delete(id);
        break;
      case "TurnCompleted":
        if (run.failed || !this.remember(`complete:${id}:${turn}`)) break;
        run.terminal = "completed";
        if (run.pending.size || ["blocked", "budget_limited", "usage_limited"].includes(run.goal || ""))
          this.enqueue("attention", id);
        else if (run.goal !== "active" && run.goal !== "paused")
          this.enqueue("complete", id);
        break;
      case "queue.changed":
        for (const message of data.messages || []) {
          if (["failed", "unknown"].includes(message.state) &&
              this.remember(`queue:${id}:${message.id}:${message.state}`)) this.enqueue("error", id);
        }
        break;
    }
  }
  dispose() { this.clearQueue(); }
}
