import React, { useEffect, useRef, useState } from "react";
import { Icon } from "./components";

export function HistorySection({
  name,
  title,
  icon,
  initiallyOpen = false,
  onOpen,
  children,
}: {
  name: string;
  title?: string;
  icon?: string;
  initiallyOpen?: boolean;
  onOpen?: (open: boolean) => void;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  return (
    <section className="history-section" aria-label={title || name}>
      <button
        className="history-heading"
        title={title || name}
        aria-expanded={open}
        onClick={() => {
          setOpen(!open);
          onOpen?.(!open);
        }}
      >
        <Icon name={icon || (title ? "folder" : "history")} size={16} />
        <span>{name}</span>
        <span className={`history-chevron ${open ? "expanded" : ""}`}>
          <Icon name="chevron" size={16} />
        </span>
      </button>
      {open && <div className="history-children">{children}</div>}
    </section>
  );
}

export function WorkspaceHistory({
  directory,
  robot = false,
  revision,
  renderThread,
}: {
  directory: { path: string; name: string };
  robot?: boolean;
  revision: number;
  renderThread: (thread: any) => React.ReactNode;
}) {
  const [open, setOpen] = useState(false),
    [rows, setRows] = useState<any[]>([]);
  const [next, setNext] = useState<number | null>(null),
    [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  async function load(offset = 0) {
    const current = ++generation.current;
    setLoading(true);
    setError("");
    try {
      const result = await window.corki.request("threads.list", {
        ...(robot ? {group: "robot"} : {directory: directory.path}),
        offset,
        limit: 40,
      });
      if (current !== generation.current) return;
      setRows((previous) =>
        offset ? [...previous, ...result.threads] : result.threads,
      );
      setNext(result.next);
    } catch (e) {
      if (current === generation.current)
        setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (current === generation.current) setLoading(false);
    }
  }
  useEffect(() => {
    if (open) void load();
    return () => {
      generation.current++;
    };
  }, [open, directory.path, robot, revision]);
  return (
    <HistorySection
      name={directory.name}
      title={robot ? undefined : directory.path}
      icon={robot ? "robot" : undefined}
      onOpen={setOpen}
    >
      {rows.map(renderThread)}
      {error && (
        <div className="history-load-error" role="alert">
          {error}
          <button onClick={() => void load()}>重试</button>
        </div>
      )}
      {loading && <p className="no-history">加载中…</p>}
      {robot && !loading && !error && !rows.length && <p className="no-history">暂无机器人会话</p>}
      {!loading && !error && next !== null && (
        <button className="more-history" onClick={() => void load(next)}>
          更多会话
        </button>
      )}
    </HistorySection>
  );
}
