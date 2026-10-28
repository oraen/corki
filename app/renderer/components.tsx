import React, { useEffect, useRef, useState } from "react";
import { ToolSummary } from "./tool-summary";
import { useMessageLines } from "./message-lines";
import { markdown, highlightedText } from "./markdown";
import { toolTitle, formatToolText, formatElapsed, formatMessageTime } from "./transcript";
import { defaults, choices, formContent } from "./interactions";
import type { Message } from "./state";
export function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, React.ReactNode> = {
    model: (
      <>
        <rect x="4" y="4" width="16" height="16" rx="4" />
        <path d="M9 9h6v6H9Z" />
      </>
    ),
    trash: <><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v5M14 11v5" /></>,
    plus: <path d="M12 5v14M5 12h14" />,
    send: <path d="m5 12 7-7 7 7M12 5v14" />,
    stop: <rect x="6" y="6" width="12" height="12" rx="2" />,
    chat: (
      <>
        <path d="M20 11v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h7" />
        <path d="m11 13 1-4 7-7 3 3-7 7-4 1Z" />
      </>
    ),
    gear: (
      <>
        <polygon points="18.47,9.32 21.78,9.92 21.78,14.08 18.47,14.68 18.47,14.68 20.39,17.45 17.45,20.39 14.68,18.47 14.68,18.47 14.08,21.78 9.92,21.78 9.32,18.47 9.32,18.47 6.55,20.39 3.61,17.45 5.53,14.68 5.53,14.68 2.22,14.08 2.22,9.92 5.53,9.32 5.53,9.32 3.61,6.55 6.55,3.61 9.32,5.53 9.32,5.53 9.92,2.22 14.08,2.22 14.68,5.53 14.68,5.53 17.45,3.61 20.39,6.55 18.47,9.32" />
        <circle cx="12" cy="12" r="3" />
      </>
    ),
    history: (
      <>
        <path d="M3 11a9 9 0 1 1 2.6 7M3 4v7h7" />
        <path d="M12 7v5l3 2" />
      </>
    ),
    folder: <path d="M3 7V5h6l2 2h10v12H3V7Z" />,
    chevron: <path d="m8 10 4 4 4-4" />,
    close: <path d="m6 6 12 12M18 6 6 18" />,
    sidebar: (
      <>
        <rect x="3" y="4" width="18" height="16" rx="3" />
        <path d="M9 4v16" />
      </>
    ),
    copy: (
      <>
        <rect x="8" y="8" width="12" height="12" rx="2" />
        <path d="M15 8V4H4v11h4" />
      </>
    ),
    more: (
      <>
        <circle cx="5" cy="12" r="1" />
        <circle cx="12" cy="12" r="1" />
        <circle cx="19" cy="12" r="1" />
      </>
    ),
    arrow: <path d="M12 5v14m-6-6 6 6 6-6" />,
    check: <path d="m5 12 4 4L19 6" />,
    refresh: (
      <>
        <path d="M20 8a8 8 0 1 0 0 8M20 3v5h-5" />
      </>
    ),
    file: (
      <>
        <path d="M5 3h9l5 5v13H5Z" />
        <path d="M14 3v6h5" />
      </>
    ),
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name] || paths.chat}
    </svg>
  );
}
export function Picker({
  label,
  value,
  items,
  onChange,
  disabled = false,
  iconFallback,
}: {
  label: string;
  value: string;
  items: { id: string; label: string; icon?: string }[];
  onChange: (id: string) => void;
  disabled?: boolean;
  iconFallback?: "initial" | "model";
}) {
  const [open, setOpen] = useState(false),
    [active, setActive] = useState(0),
    ref = useRef<HTMLDivElement>(null);
  const listId = React.useId();
  const selected = items.find((i) => i.id === value);
  const itemIcon = (item?: { icon?: string; label: string }) =>
    item?.icon ? (
      <img className="picker-icon" src={item.icon} alt="" />
    ) : (
      iconFallback && (
        <span className="picker-icon picker-fallback" aria-hidden="true">
          {iconFallback === "initial" ? (
            Array.from(item?.label || "?")[0].toUpperCase()
          ) : (
            <Icon name="model" size={17} />
          )}
        </span>
      )
    );
  useEffect(() => {
    if (open)
      ref.current
        ?.querySelectorAll<HTMLButtonElement>('[role="option"]')
        [active]?.focus();
  }, [open, active]);
  useEffect(() => {
    if (!open) return;
    const close = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [open]);
  return (
    <div
      className="picker"
      ref={ref}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          setOpen(false);
          ref.current?.querySelector("button")?.focus();
        } else if (
          ["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key) &&
          !disabled &&
          items.length
        ) {
          event.preventDefault();
          setActive(
            event.key === "Home"
              ? 0
              : event.key === "End"
                ? items.length - 1
                : !open
                  ? Math.max(
                      0,
                      items.findIndex((i) => i.id === value),
                    )
                  : (active +
                      (event.key === "ArrowDown" ? 1 : -1) +
                      items.length) %
                    items.length,
          );
          setOpen(true);
        }
      }}
    >
      <button
        className="picker-trigger"
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        disabled={disabled || !items.length}
        onClick={() => {
          setActive(
            Math.max(
              0,
              items.findIndex((i) => i.id === value),
            ),
          );
          setOpen(!open);
        }}
      >
        {itemIcon(selected)}
        {selected?.label || label}
        <Icon name="chevron" size={15} />
      </button>
      {open && (
        <div
          id={listId}
          className="picker-menu"
          role="listbox"
          aria-label={label}
        >
          {items.map((i, index) => (
            <button
              role="option"
              tabIndex={index === active ? 0 : -1}
              onFocus={() => setActive(index)}
              key={i.id}
              aria-selected={i.id === value}
              onClick={() => {
                onChange(i.id);
                setOpen(false);
                ref.current?.querySelector("button")?.focus();
              }}
            >
              {itemIcon(i)}
              <span className="picker-label">{i.label}</span>
              {i.id === value && <Icon name="check" size={15} />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
export function Modal({
  className = "",
  title,
  children,
  onClose,
}: {
  className?: string;
  title: React.ReactNode;
  children: React.ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const opener = useRef(document.activeElement);
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    dialog
      ?.querySelector<HTMLElement>(
        "[autofocus], input:not([disabled]), textarea:not([disabled])",
      )
      ?.focus();
    return () => {
      dialog?.close();
      if (opener.current instanceof HTMLElement && opener.current.isConnected)
        opener.current.focus();
    };
  }, []);
  return (
    <dialog
      className={`modal ${className}`}
      ref={ref}
      onKeyDown={(event) => {
        if (event.key !== "Tab") return;
        const controls = Array.from(
          event.currentTarget.querySelectorAll<HTMLElement>(
            "button:not([disabled]), input:not([disabled]), textarea:not([disabled]), a[href], [tabindex]",
          ),
        ).filter(
          (element) =>
            element.tabIndex >= 0 && element.getClientRects().length > 0,
        );
        const first = controls[0],
          last = controls.at(-1);
        if (!first || !last) {
          event.preventDefault();
          return;
        }
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <header>
        <h2>{title}</h2>
        <button className="icon-button" aria-label="关闭" onClick={onClose}>
          <Icon name="close" />
        </button>
      </header>
      {children}
    </dialog>
  );
}
function ExecutionDuration({
  message,
  output,
}: {
  message: Message;
  output?: Message;
}) {
  const [now, setNow] = useState(Date.now);
  const elapsed = message.elapsedSeconds ?? output?.elapsedSeconds;
  const start = message.startedAt ?? output?.startedAt;
  const running = message.streaming && elapsed == null && start != null;
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running, start]);
  const seconds = elapsed ?? (running ? (now - start!) / 1000 : undefined);
  if (seconds == null || !Number.isFinite(seconds)) return null;
  return (
    <span className="execution-duration">（耗时{formatElapsed(seconds)}）</span>
  );
}

export const MessageRow = React.memo(function MessageRow({
  message,
  onError,
  output,
  commentary = false,
  showAvatar = true,
  collapsible = false,
  active = false,
}: {
  output?: Message;
  commentary?: boolean;
  showAvatar?: boolean;
  collapsible?: boolean;
  active?: boolean;
  message: Message;
  onError: (e: unknown) => void;
}) {
  const [folded, setFolded] = useState(false);
  const [expanded, setExpanded] = useState<{
    base: string;
    content: string;
    next: number | null;
  } | null>(null);
  const [expanding, setExpanding] = useState(false),
    [copying, setCopying] = useState(false);
  const visible =
    expanded?.base === message.content ? expanded.content : message.content;
  const next =
    expanded?.base === message.content ? expanded.next : message.nextText;
  const { body, multiline } = useMessageLines(
    collapsible && !commentary && ["user", "assistant"].includes(message.role),
    visible,
    message.imageCount,
  );
  const canFold = collapsible && multiline;
  const isFolded = canFold && folded;
  useEffect(() => {
    if (!multiline) setFolded(false);
  }, [multiline]);
  async function loadMore() {
    if (expanding || next == null || !message.sourceId) return;
    setExpanding(true);
    try {
      const result: { content: string; next: number | null } =
        await window.corki.request("messages.text", {
          threadId: message.threadId,
          messageId: message.sourceId,
          offset: next,
        });
      setExpanded({
        base: message.content,
        content: visible + result.content,
        next: result.next,
      });
    } catch (error) {
      onError(error);
    } finally {
      setExpanding(false);
    }
  }
  async function copyOriginal() {
    if (copying) return;
    setCopying(true);
    try {
      let content = message.content;
      if (message.truncated && message.sourceId) {
        content = "";
        let offset: number | null = 0;
        while (offset !== null) {
          const result: { content: string; next: number | null } =
            await window.corki.request("messages.text", {
              threadId: message.threadId,
              messageId: message.sourceId,
              offset,
            });
          content += result.content;
          if (content.length > 16000000)
            throw new Error("回答超过剪贴板大小限制");
          if (result.next !== null && result.next <= offset)
            throw new Error("消息分页无效");
          offset = result.next;
        }
      }
      await copy(content);
    } catch (error) {
      onError(error);
    } finally {
      setCopying(false);
    }
  }
  const more = next != null && (
    <button
      className="load-more-text"
      disabled={expanding}
      onClick={() => void loadMore()}
    >
      {expanding ? "正在加载…" : "展开更多"}
    </button>
  );
  const [copied, setCopied] = useState(false),
    timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  async function copy(content: string) {
    try {
      await window.corki.copy(content);
      setCopied(true);
      timer.current = setTimeout(() => setCopied(false), 1500);
    } catch (e) {
      onError(e);
    }
  }
  if (message.role === "tool") {
    const title = toolTitle(message);
    return (
      <details
        className={`activity tool-activity ${message.error || output?.error ? "failed" : ""}`}
      >
        <ToolSummary title={title} streaming={message.streaming}
          status={message.error || output?.error ? "失败" : message.streaming
            ? "执行中" : output || message.completed ? "已结束" : "未收到结果"}>
          <Icon name="chevron" size={14} />
        </ToolSummary>
        <div className="tool-sections">
          <section>
            <header>调用参数</header>
            <MessageRow
              message={{ ...message, role: "tool-parameters" }}
              onError={onError}
            />
          </section>
          <section>
            <header>
              执行输出
              <ExecutionDuration message={message} output={output} />
            </header>
            {output?.content ? (
              <MessageRow
                message={{ ...output, role: "tool-output" }}
                onError={onError}
              />
            ) : (
              <div className="tool-empty">
                {message.streaming ? "等待中" : "暂无输出"}
              </div>
            )}
          </section>
        </div>
      </details>
    );
  }
  if (["tool-parameters", "tool-output"].includes(message.role)) {
    const formatted = formatToolText(visible);
    let command = "";
    if (
      message.role === "tool-parameters" &&
      message.name?.split(".").at(-1) === "exec_command"
    ) {
      try {
        const args = JSON.parse(visible);
        const value = args.cmd || args.command;
        if (typeof value === "string") command = value;
      } catch {}
    }
    return (
      <div className="tool-text">
        {command && (
          <pre className="tool-command">
            <code
              className="hljs"
              dangerouslySetInnerHTML={{
                __html: highlightedText(command, "bash"),
              }}
            />
          </pre>
        )}
        <pre>
          <code
            className="hljs"
            dangerouslySetInnerHTML={{
              __html: highlightedText(formatted.text, formatted.language),
            }}
          />
        </pre>
        {more}
        <button
          className="tool-copy"
          onClick={() => void copyOriginal()}
          disabled={copying}
          aria-label={
            message.role === "tool-parameters" ? "复制调用参数" : "复制执行输出"
          }
        >
          <Icon name="copy" size={13} />
          {copied ? "已复制" : "复制"}
        </button>
      </div>
    );
  }
  if (["tool-result", "reasoning"].includes(message.role))
    return (
      <details className={`activity ${message.error ? "failed" : ""}`}>
        <summary>
          <span />
          <span className="activity-title">
            {message.role === "reasoning" ? "思考摘要" : "执行输出"}
          </span>
          <Icon name="chevron" size={14} />
        </summary>
        <MessageRow
          message={{ ...message, role: "tool-output" }}
          onError={onError}
        />
      </details>
    );
  if (commentary)
    return (
      <div className="agent-progress">
        {showAvatar && (
          <img
            className="avatar turn-avatar"
            src="resources/corki.png"
            alt="Corki"
          />
        )}
        <div>
          <MarkdownBody content={visible} onError={onError} />
          {more}
        </div>
      </div>
    );
  if (message.role === "stopped")
    return (
      <div className={`message-stopped ${showAvatar ? "with-avatar" : ""}`}>
        {showAvatar && (
          <img
            className="avatar turn-avatar"
            src="resources/corki.png"
            alt="Corki"
          />
        )}
        <Icon name="stop" size={13} />
        回答已停止
      </div>
    );
  if (message.role === "error")
    return (
      <div className="message-error" role="alert">
        {message.content}
      </div>
    );
  return (
    <article className={`message ${message.role}`}>
      {message.role === "user" || showAvatar ? (
        <img
          className="avatar"
          src={
            message.role === "user"
              ? "resources/user.jpg"
              : "resources/corki.png"
          }
          alt={message.role === "user" ? "我" : "Corki"}
        />
      ) : (
        <span className="avatar avatar-spacer" aria-hidden="true" />
      )}
      <div className={`message-content ${isFolded ? "is-folded" : ""}`}>
        {message.role === "user" ? (
          <div className="user-message-meta">
            {formatMessageTime(message.createdAt) && (
              <time className="message-time" dateTime={message.createdAt} title="提问时间">
                {formatMessageTime(message.createdAt)}
              </time>
            )}
            {canFold && (
          <button
            className="message-fold"
            aria-expanded={!isFolded}
            aria-label={`${isFolded ? "展开" : "收起"}${message.role === "user" ? "提问" : "回答"}`}
            onClick={() => setFolded(!folded)}
          >
            <Icon name="chevron" size={14} />
            {isFolded ? "展开" : "收起"}
          </button>
        )}
          </div>
        ) : (
          <>
        {canFold && (
          <button
            className="message-fold"
            aria-expanded={!isFolded}
            aria-label={`${isFolded ? "展开" : "收起"}${message.role === "user" ? "提问" : "回答"}`}
            onClick={() => setFolded(!folded)}
          >
            <Icon name="chevron" size={14} />
            {isFolded ? "展开" : "收起"}
          </button>
        )}
          </>
        )}
        {isFolded && (
          <button className="folded-preview" onClick={() => setFolded(false)}>
            {visible.trim().replace(/\s+/g, " ") || "附件消息"}
          </button>
        )}
        <div ref={body} className={`message-body${isFolded ? " folded-body" : ""}`}
          aria-hidden={isFolded || undefined} inert={isFolded}>
          {message.role === "user" ? (
            <div className="user-text">{visible}</div>
          ) : (
            <MarkdownBody content={visible} onError={onError} />
          )}
        </div>
        {!isFolded && (
          <>
            {!!message.imageCount && message.sourceId && message.threadId && (
              <div className="message-images">
                {Array.from({ length: message.imageCount }, (_, index) => (
                  <HistoryImage
                    key={index}
                    threadId={message.threadId!}
                    messageId={message.sourceId!}
                    index={index}
                  />
                ))}
              </div>
            )}
            {more}
            {message.streaming && <span className="typing-dot" />}
          </>
        )}
        {message.role === "assistant" && !message.streaming && !active && (
          <div className="assistant-message-meta">
            {message.role === "assistant" &&
              !message.streaming &&
              message.content && (
                <button
                  className="copy-button"
                  onClick={() => void copyOriginal()}
                  disabled={copying}
                  aria-label="复制回答原文"
                >
                  <Icon name={copied ? "check" : "copy"} size={15} />
                  {copied ? "已复制" : "复制"}
                </button>
              )}
            {formatMessageTime(message.createdAt) && (
              <time className="message-time" dateTime={message.createdAt} title="回复时间">
                {formatMessageTime(message.createdAt)}
              </time>
            )}
          </div>
        )}
      </div>
    </article>
  );
});
function HistoryImage({
  threadId,
  messageId,
  index,
}: {
  threadId: string;
  messageId: string;
  index: number;
}) {
  const root = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false),
    [data, setData] = useState(""),
    [error, setError] = useState(""),
    [opened, setOpened] = useState(false),
    [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      {
        root: root.current?.closest(".conversation-scroll"),
        rootMargin: "200px",
      },
    );
    if (root.current) observer.observe(root.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!visible) return;
    let disposed = false;
    setError("");
    window.corki
      .request("messages.image", { threadId, messageId, index })
      .then((result) => {
        if (!disposed) setData(result.data);
      })
      .catch((error) => {
        if (!disposed) setError(error.message);
      });
    return () => {
      disposed = true;
    };
  }, [visible, threadId, messageId, index, attempt]);
  return (
    <div ref={root} className="history-image">
      {data ? (
        <button
          onClick={() => setOpened(true)}
          aria-label={`查看图片 ${index + 1}`}
        >
          <img src={data} alt={`图片附件 ${index + 1}`} />
        </button>
      ) : error ? (
        <button title={error} onClick={() => setAttempt(attempt + 1)}>
          重新加载图片
        </button>
      ) : (
        <span>图片加载中…</span>
      )}
      {opened && (
        <Modal title="图片预览" onClose={() => setOpened(false)}>
          <img className="image-preview" src={data} alt="图片预览" />
        </Modal>
      )}
    </div>
  );
}
function MarkdownBody({
  content,
  onError,
}: {
  content: string;
  onError: (error: unknown) => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const latest = useRef(content),
    rendered = useRef(content);
  latest.current = content;
  const [html, setHtml] = useState(() => markdown(content));
  const markup = React.useMemo(() => ({ __html: html }), [html]);
  function hasSelection() {
    const selection = document.getSelection();
    return (
      !!root.current &&
      !!selection &&
      !selection.isCollapsed &&
      selection.rangeCount > 0 &&
      selection.getRangeAt(0).intersectsNode(root.current)
    );
  }
  useEffect(() => {
    // Replacing innerHTML during a drag/copy destroys the browser's selection.
    // The authoritative answer continues streaming; only this selected body is frozen.
    if (rendered.current !== content && !hasSelection()) {
      rendered.current = content;
      setHtml(markdown(content));
    }
  }, [content]);
  useEffect(() => {
    const refresh = () => {
      if (rendered.current !== latest.current && !hasSelection()) {
        rendered.current = latest.current;
        setHtml(markdown(latest.current));
      }
    };
    document.addEventListener("selectionchange", refresh);
    return () => document.removeEventListener("selectionchange", refresh);
  }, []);
  useEffect(() => {
    const cleanups: (() => void)[] = [];
    // Controls are created only after sanitization; model-provided buttons remain forbidden.
    for (const block of Array.from(root.current?.querySelectorAll<HTMLElement>(".code-block") || [])) {
      const code = block.querySelector("pre > code");
      if (!code) continue;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "code-block-copy";
      button.textContent = "复制代码";
      button.setAttribute("aria-label", "复制代码");
      let timer: ReturnType<typeof setTimeout> | undefined;
      let disposed = false;
      const click = async () => {
        button.disabled = true;
        try {
          await window.corki.copy(code.textContent || "");
          if (disposed) return;
          button.textContent = "已复制";
          clearTimeout(timer);
          timer = setTimeout(() => {
            button.textContent = "复制代码";
          }, 1500);
        } catch (error) {
          if (!disposed) onError(error);
        } finally {
          if (!disposed) button.disabled = false;
        }
      };
      button.addEventListener("click", click);
      block.append(button);
      cleanups.push(() => {
        disposed = true;
        clearTimeout(timer);
        button.removeEventListener("click", click);
        button.remove();
      });
    }
    return () => cleanups.forEach((cleanup) => cleanup());
  }, [html, onError]);
  return (
    <div
      ref={root}
      className="markdown"
      onClick={(e) => {
        const anchor = (e.target as HTMLElement).closest("a");
        if (anchor) {
          e.preventDefault();
          window.corki.openLink(anchor.href).catch(onError);
        }
      }}
      dangerouslySetInnerHTML={markup}
    />
  );
}
export function Interaction({
  item,
  onDone,
  onError,
}: {
  item: any;
  onDone: () => void;
  onError: (e: unknown) => void;
}) {
  const fields = item.params?.requestedSchema?.properties || {};
  const [answers, setAnswers] = useState<Record<string, any>>(() =>
      defaults(fields),
    ),
    [busy, setBusy] = useState(false);
  const localApproval = ["shell_approval", "patch_approval"].includes(
    item.kind,
  );
  const toolApproval = localApproval || item.kind === "tool_approval";
  const scopeNames: Record<string, string> = {
    once: "仅本次",
    session: "本会话",
    rule: "保存规则",
    always: "始终允许",
  };
  const scope = answers.scope || "once";
  const urlMode = item.params?.mode === "url";
  const meta = item.params?._meta || {};
  async function answer(action: string) {
    if (busy) return;
    setBusy(true);
    try {
      const payload: Record<string, unknown> = {
        threadId: item.threadId,
        requestId: item.request_id,
        action,
      };
      if (item.kind === "question") {
        payload.response = {
          answers: Object.fromEntries(
            (item.questions || []).map((q: any) => [
              q.id,
              { answers: answers[q.id] ? [answers[q.id]] : [] },
            ]),
          ),
        };
      } else if (action === "accept" && !urlMode)
        payload.content = formContent(fields, answers);
      await window.corki.request("interaction.respond", payload);
      onDone();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="interaction">
      <h3>
        {item.kind === "question"
          ? "需要你的补充"
          : localApproval
            ? "执行确认"
            : "需要你的确认"}
      </h3>
      {item.kind === "question" ? (
        (item.questions || []).map((q: any) => (
          <div className="question" key={q.id}>
            <p>{q.question}</p>
            <div className="answer-options">
              {q.options?.map((o: any) => (
                <button
                  key={o.label}
                  title={o.description}
                  className={answers[q.id] === o.label ? "selected" : ""}
                  aria-pressed={answers[q.id] === o.label}
                  onClick={() => setAnswers({ ...answers, [q.id]: o.label })}
                >
                  {o.label}
                </button>
              ))}
            </div>
            <input
              aria-label={q.question}
              type={q.is_secret ? "password" : "text"}
              placeholder="填写回答"
              value={answers[q.id] || ""}
              onChange={(e) =>
                setAnswers({ ...answers, [q.id]: e.target.value })
              }
            />
          </div>
        ))
      ) : (
        <>
          <p>{item.params?.message || item.kind}</p>
          {toolApproval && meta.tool_params != null && (
            <details className="approval-details" open>
              <summary>
                {item.kind === "patch_approval" ? "文件变更" : "操作详情"}
              </summary>
              <pre>{JSON.stringify(meta.tool_params, null, 2)}</pre>
            </details>
          )}
          {localApproval && meta.patch_retry && (
            <details className="approval-details" open>
              <summary>上次执行结果（已有修改未回滚）</summary>
              <pre>{JSON.stringify(meta.patch_retry, null, 2)}</pre>
            </details>
          )}
          {urlMode && (
            <div className="approval-url">
              <span title={item.params.url}>{item.params.url}</span>
              <button
                onClick={() =>
                  window.corki.openLink(item.params.url).catch(onError)
                }
              >
                打开链接
              </button>
            </div>
          )}
          {Object.entries(fields).map(([key, raw]) => {
            const field = raw as any;
            const label =
              toolApproval && ["scope", "persist"].includes(key)
                ? "批准范围"
                : field.title || key;
            const options = choices(field).map((option) => ({
              ...option,
              label:
                toolApproval && ["scope", "persist"].includes(key)
                  ? scopeNames[option.id] || option.label
                  : option.label,
            }));
            const required =
              item.params?.requestedSchema?.required?.includes(key);
            return (
              <div className="interaction-field" key={key}>
                <span className="field-label">
                  {label}
                  {required && <span className="required"> *</span>}
                </span>
                {field.description && (
                  <p className="field-description">{field.description}</p>
                )}
                {field.type === "array" ? (
                  <div
                    className="answer-options"
                    role="group"
                    aria-label={label}
                  >
                    {choices(field.items).map((option) => {
                      const values: string[] = answers[key] || [];
                      const selected = values.includes(option.id);
                      return (
                        <button
                          key={option.id}
                          aria-pressed={selected}
                          className={selected ? "selected" : ""}
                          onClick={() =>
                            setAnswers({
                              ...answers,
                              [key]: selected
                                ? values.filter((v) => v !== option.id)
                                : [...values, option.id],
                            })
                          }
                        >
                          {option.label}
                        </button>
                      );
                    })}
                  </div>
                ) : field.type === "boolean" ? (
                  <Picker
                    label={label}
                    value={String(answers[key] ?? "")}
                    items={[
                      { id: "", label: "请选择" },
                      { id: "true", label: "是" },
                      { id: "false", label: "否" },
                    ]}
                    onChange={(value) =>
                      setAnswers({ ...answers, [key]: value })
                    }
                  />
                ) : options.length ? (
                  <Picker
                    label={label}
                    value={String(answers[key] ?? "")}
                    items={[{ id: "", label: "请选择" }, ...options]}
                    onChange={(value) =>
                      setAnswers({ ...answers, [key]: value })
                    }
                  />
                ) : (
                  <input
                    aria-label={label}
                    value={answers[key] ?? ""}
                    type={
                      field.type === "number" || field.type === "integer"
                        ? "number"
                        : "text"
                    }
                    step={field.type === "integer" ? 1 : "any"}
                    onChange={(event) =>
                      setAnswers({ ...answers, [key]: event.target.value })
                    }
                  />
                )}
              </div>
            );
          })}
        </>
      )}
      <footer>
        {item.kind !== "question" && (
          <>
            <button disabled={busy} onClick={() => void answer("cancel")}>
              取消请求
            </button>
            <button disabled={busy} onClick={() => void answer("decline")}>
              拒绝
            </button>
          </>
        )}
        <button
          className="primary"
          disabled={busy}
          onClick={() => void answer("accept")}
        >
          {item.kind === "question"
            ? "提交回答"
            : urlMode
              ? "已完成"
              : item.kind === "skill_dependency_install"
                ? "确认安装"
                : localApproval
                  ? scope === "session"
                    ? "本会话允许"
                    : scope === "rule"
                      ? "保存规则并允许"
                      : "允许本次"
                  : "确认"}
        </button>
      </footer>
    </section>
  );
}
