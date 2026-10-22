import React, { useState } from "react";
import { Icon } from "./components";
import type { FollowUpState } from "./state";

export function FollowUps({ value, running, act }: {
  value: FollowUpState; running: boolean;
  act: (action: string, params?: Record<string, unknown>) => Promise<any>;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function action(name: string, params = {}) {
    if (busy) return;
    setBusy(true); setError("");
    try { await act(name, params); setEditing(null); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  async function edit(id: string) {
    if (busy) return;
    setBusy(true); setError("");
    try { const data = await act("detail", {id}); setEditing(id); setText(data.content); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  return <section className="followups" aria-label="追加消息">
    <div className="followups-header">
      <span>待发送 · {value.messages.length}</span>
      {value.paused && value.messages.length > 0 && <button className="queue-action" disabled={busy}
        onClick={() => void action("resume")}>恢复队列</button>}
    </div>
    {value.paused && value.messages.length > 0 && <div className="queue-note">队列已暂停</div>}
    <div className="followup-list">
      {value.messages.map((m, i) => <div className="followup-row" key={m.id}>
        <div className="followup-body">
          {editing === m.id ? <textarea className="followup-editor" aria-label="编辑待发送消息" value={text}
            onChange={e => setText(e.target.value)} maxLength={100000} autoFocus /> :
            <p title={m.content}>{m.content || m.attachments.join("、")}</p>}
          {m.attachments.length > 0 && m.content && <span className="queue-note">{m.attachments.join(" · ")}</span>}
          {m.error && <span className="queue-error">{m.error}</span>}
          <div className="followup-actions">
            {editing === m.id ? <>
              <button disabled={busy || (!text.trim() && !m.attachments.length)} onClick={() => void action("edit", {id:m.id, content:text})}>保存</button>
              <button disabled={busy} onClick={() => setEditing(null)}>取消</button>
            </> : <>
              <button disabled={busy || ["dispatching","unknown"].includes(m.state)} onClick={() => void action("send", {id:m.id})}>
                {m.state === "dispatching" ? "发送中…" : running ? "立即补充" : "立即发送"}</button>
              <button disabled={busy || ["dispatching","unknown"].includes(m.state)} onClick={() => void edit(m.id)}>编辑</button>
              <button disabled={busy || m.state === "dispatching"} onClick={() => void action("delete", {id:m.id})}>删除</button>
              {value.messages.length > 1 && <>
                <button aria-label="上移消息" disabled={busy || i === 0 || m.state === "dispatching"} onClick={() => void action("move", {id:m.id,direction:-1})}><span className="queue-up"><Icon name="arrow" size={13}/></span></button>
                <button aria-label="下移消息" disabled={busy || i === value.messages.length-1 || m.state === "dispatching"} onClick={() => void action("move", {id:m.id,direction:1})}><Icon name="arrow" size={13}/></button>
              </>}
            </>}
          </div>
        </div>
      </div>)}
    </div>
    {error && <p className="queue-error" role="alert">{error}</p>}
  </section>;
}
