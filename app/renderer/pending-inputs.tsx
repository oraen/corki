import React, { useState } from "react";
import { MessageRow } from "./components";
import type { FollowUpState, Message } from "./state";

// Display-only input copies. Never insert unsent input into model history.
export function displayMessages(messages: Message[], value: FollowUpState, running: boolean): Message[] {
  if (!value.messages.length) return messages;
  const accepted = new Set(messages.flatMap(m => [m.requestId, m.submissionId].filter(Boolean)));
  const pending = value.messages.filter(m => !accepted.has(m.id));
  if (!pending.length) return messages;
  const result = [...messages];
  for (const m of pending) {
    const message: Message = {id: `pending:${m.id}`, role: "user", content: m.content || m.attachments.join("、"),
      createdAt: m.createdAt, pendingInput: {id: m.id, state: m.state, error: m.error,
        paused: value.paused, blocked: running && value.paused}};
    // Failed/stopped inputs stay before later user questions, including reloads.
    const at = m.createdAt ? result.findIndex(row => row.role === "user" && !!row.createdAt &&
      Date.parse(row.createdAt) > Date.parse(m.createdAt!)) : -1;
    result.splice(at < 0 ? result.length : at, 0, message);
  }
  return result;
}

export function DeferredMessage({ message, retry, onError }: {
  message: Message;
  retry: (id: string) => Promise<void>;
  onError: (error: unknown) => void;
}) {
  const [sending, setSending] = useState<string | null>(null);
  const m = message.pendingInput!;
  const uncertain = m.state === "unknown";
  const failed = uncertain || m.state === "failed" || m.paused;
  return <div className="pending-input">
      <MessageRow message={message} onError={onError} />
      {failed && <div className="input-delivery-error" role="status">
        <span>{uncertain ? "发送结果尚未确认，请先核对聊天记录。" : m.error || "消息尚未发送。"}</span>
        {!uncertain && <button disabled={!!sending || m.blocked} onClick={async () => {
          setSending(m.id);
          try { await retry(m.id); } catch (error) { onError(error); }
          finally { setSending(null); }
        }}>{sending === m.id ? "发送中…" : "重新发送"}</button>}
      </div>}
    </div>;
}
