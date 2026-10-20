import type { Message } from "./state";

export interface TranscriptEntry {
  message: Message;
  output?: Message;
  commentary?: boolean;
  showAvatar?: boolean;
}
const callKey = (m: Message) =>
  m.callId ? `${m.turnId || ""}:${m.callId}` : null;

// Presentation only: keep canonical items and their IDs intact. Match outputs by
// turn/call identity, never adjacency (parallel tools can finish out of order).
export function transcriptEntries(messages: Message[]): TranscriptEntry[] {
  const calls = new Set(
    messages
      .filter((m) => m.role === "tool")
      .map(callKey)
      .filter(Boolean),
  );
  const outputs = new Map(
    messages
      .filter((m) => m.role === "tool-result")
      .map((m) => [callKey(m), m]),
  );
  const lastActivity = new Map<string, number>();
  messages.forEach((m, index) => {
    if (m.turnId && ["assistant", "tool", "tool-result"].includes(m.role))
      lastActivity.set(m.turnId, index);
  });
  // The avatar belongs to the first visible answer in each turn, not to a
  // transient waiting indicator or to whichever answer is currently last.
  const avatarOwners = new Map<string, string>();
  const groups = new Map<string, string>();
  let group = "initial";
  for (const message of messages) {
    if (message.role === "user") group = message.turnId || message.id;
    const key = message.turnId || group;
    groups.set(message.id, key);
    if (
      message.role === "assistant" &&
      message.content.trim() &&
      !avatarOwners.has(key)
    )
      avatarOwners.set(key, message.id);
  }
  for (const message of messages) {
    const key = groups.get(message.id)!;
    if (message.role === "stopped" && !avatarOwners.has(key))
      avatarOwners.set(key, message.id);
  }
  return messages.flatMap((message, index) => {
    const key = callKey(message);
    if (message.role === "tool-result" && key && calls.has(key)) return [];
    // A model may speak between tool calls. Preserve that useful progress text,
    // but don't present each intermediate message as a second final answer.
    const commentary =
      message.role === "assistant" &&
      !!message.turnId &&
      (message.phase === "commentary" || lastActivity.get(message.turnId) !== index);
    return [
      {
        message,
        commentary,
        showAvatar: avatarOwners.get(groups.get(message.id)!) === message.id,
        ...(message.role === "tool" && key ? { output: outputs.get(key) } : {}),
      },
    ];
  });
}

export function toolTitle(message: Message): string {
  if (message.name?.split(".").at(-1) !== "exec_command")
    return message.name || "执行输出";
  try {
    const args = JSON.parse(message.content);
    if (typeof args.cmd === "string" && args.cmd.trim()) return args.cmd.trim();
    if (typeof args.command === "string" && args.command.trim())
      return args.command.trim();
  } catch {
    // The runtime sends a bounded argument preview. Recover the quoted command
    // prefix for display only; never use this to execute or reconstruct arguments.
    const match = message.content.match(
      /"(?:cmd|command)"\s*:\s*"((?:\\.|[^"\\])*)/,
    );
    if (match?.[1]) {
      try {
        return JSON.parse(`"${match[1]}"`).trim() + "…";
      } catch {}
    }
  }
  return "执行命令";
}

export function formatToolText(
  content: string,
  command = false,
): { text: string; language: string } {
  try {
    return {
      text: JSON.stringify(JSON.parse(content), null, 2),
      language: "json",
    };
  } catch {
    return { text: content, language: command ? "bash" : "plaintext" };
  }
}

export function formatElapsed(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600),
    minutes = Math.floor((total % 3600) / 60);
  return [
    hours ? `${hours}h` : "",
    minutes ? `${minutes}m` : "",
    `${total % 60}s`,
  ]
    .filter(Boolean)
    .join(" ");
}

export function formatMessageTime(value?: string): string {
  if (!value) return "";
  // Legacy SQLite timestamps without an offset are UTC, like canonical ISO dates.
  const normalized = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(value)
    ? value.replace(" ", "T") + "Z" : value;
  const date = new Date(normalized);
  if (!Number.isFinite(date.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
