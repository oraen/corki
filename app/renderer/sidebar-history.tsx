import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Icon } from "./components";

export function HistorySection({
  name,
  title,
  icon,
  initiallyOpen = false,
  onOpen,
  onRename,
  children,
}: {
  name: string;
  title?: string;
  icon?: string;
  initiallyOpen?: boolean;
  onOpen?: (open: boolean) => void;
  onRename?: (name: string) => Promise<void>;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const [editing, setEditing] = useState(false),
    [draft, setDraft] = useState(name);
  const [saving, setSaving] = useState(false),
    [renameError, setRenameError] = useState("");
  const input = useRef<HTMLInputElement>(null),
    heading = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null),
    submitting = useRef(false);
  useEffect(() => {
    if (editing) {
      input.current?.focus();
      input.current?.select();
    }
  }, [editing]);
  useEffect(() => {
    if (!menu) return;
    popup.current?.querySelector("button")?.focus();
    const outside = (event: PointerEvent) => {
      if (!popup.current?.contains(event.target as Node)) setMenu(null);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenu(null);
        heading.current?.focus();
      }
    };
    const close = () => setMenu(null);
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("keydown", escape);
    window.addEventListener("blur", close);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    return () => {
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("keydown", escape);
      window.removeEventListener("blur", close);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [menu]);
  async function save() {
    if (!editing || submitting.current || !onRename) return;
    submitting.current = true;
    setSaving(true);
    setRenameError("");
    try {
      await onRename(draft);
      setEditing(false);
    } catch (error) {
      setRenameError(error instanceof Error ? error.message : String(error));
    } finally {
      submitting.current = false;
      setSaving(false);
    }
  }
  const folderIcon = (
    <Icon name={icon || (title ? "folder" : "history")} size={16} />
  );
  const chevron = (
    <span className={`history-chevron ${open ? "expanded" : ""}`}>
      <Icon name="chevron" size={16} />
    </span>
  );
  return (
    <section className="history-section" aria-label={title || name}>
      <div
        onContextMenu={(event) => {
          if (!onRename || editing) return;
          event.preventDefault();
          setMenu({
            x: Math.max(8, Math.min(event.clientX, window.innerWidth - 166)),
            y: Math.max(8, Math.min(event.clientY, window.innerHeight - 56)),
          });
        }}
      >
        {editing ? (
          <div className="history-heading history-heading-editing">
            {folderIcon}
            <input
              ref={input}
              value={draft}
              maxLength={200}
              disabled={saving}
              aria-label="工作目录显示名称"
              onChange={(event) => setDraft(event.target.value)}
              onBlur={() => void save()}
              onKeyDown={(event) => {
                if (event.nativeEvent.isComposing || event.keyCode === 229)
                  return;
                if (event.key === "Enter") {
                  event.preventDefault();
                  void save();
                }
                if (event.key === "Escape") {
                  event.preventDefault();
                  submitting.current = true;
                  setEditing(false);
                  setRenameError("");
                  heading.current?.focus();
                }
              }}
            />
            {chevron}
          </div>
        ) : (
          <button
            ref={heading}
            className="history-heading"
            title={title || name}
            aria-expanded={open}
            onClick={() => {
              setOpen(!open);
              onOpen?.(!open);
            }}
          >
            {folderIcon}
            <span>{name}</span>
            {chevron}
          </button>
        )}
      </div>
      {renameError && (
        <div className="history-load-error" role="alert">
          {renameError}
        </div>
      )}
      {menu &&
        createPortal(
          <div
            ref={popup}
            className="workspace-context-menu"
            role="menu"
            aria-label="工作目录操作"
            style={{ left: menu.x, top: menu.y }}
          >
            <button
              role="menuitem"
              onClick={() => {
                submitting.current = false;
                setMenu(null);
                setDraft(name);
                setRenameError("");
                setEditing(true);
              }}
            >
              重命名
            </button>
          </div>,
          document.body,
        )}
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
  const [displayName, setDisplayName] = useState(directory.name);
  useEffect(() => setDisplayName(directory.name), [directory.name]);
  async function load(offset = 0) {
    const current = ++generation.current;
    setLoading(true);
    setError("");
    try {
      const result = await window.corki.request("threads.list", {
        ...(robot ? { group: "robot" } : { directory: directory.path }),
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
      name={displayName}
      title={robot ? undefined : directory.path}
      icon={robot ? "robot" : undefined}
      onOpen={setOpen}
      onRename={
        robot
          ? undefined
          : async (value) => {
              const alias = value.trim();
              await window.corki.request("state.write", {
                key: "directoryAlias:" + directory.path,
                value: alias,
              });
              const basename =
                directory.path.split(/[\\/]/).filter(Boolean).at(-1) ||
                directory.path;
              setDisplayName(alias || basename);
            }
      }
    >
      {rows.map(renderThread)}
      {error && (
        <div className="history-load-error" role="alert">
          {error}
          <button onClick={() => void load()}>重试</button>
        </div>
      )}
      {loading && <p className="no-history">加载中…</p>}
      {robot && !loading && !error && !rows.length && (
        <p className="no-history">暂无机器人会话</p>
      )}
      {!loading && !error && next !== null && (
        <button className="more-history" onClick={() => void load(next)}>
          更多会话
        </button>
      )}
    </HistorySection>
  );
}
