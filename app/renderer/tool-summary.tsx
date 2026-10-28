import React, { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/** The process row's full command, independent of its clipped scroll container. */
export function ToolSummary({ title, streaming, status, children }: {
  title: string; streaming?: boolean; status: string; children: React.ReactNode;
}) {
  const id = useId();
  const anchor = useRef<HTMLElement>(null);
  const tooltip = useRef<HTMLDivElement>(null);
  const showTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);
  function clearTimers() {
    if (showTimer.current) clearTimeout(showTimer.current);
    if (hideTimer.current) clearTimeout(hideTimer.current);
  }
  function close() { clearTimers(); setOpen(false); setPosition(null); }
  function show(immediate = false) {
    clearTimers();
    if (immediate) setOpen(true);
    else showTimer.current = setTimeout(() => setOpen(true), 350);
  }
  function leave() {
    clearTimers();
    hideTimer.current = setTimeout(close, 100);
  }
  useEffect(() => clearTimers, []);
  useLayoutEffect(() => {
    if (!open || !anchor.current || !tooltip.current) return;
    const rect = anchor.current.getBoundingClientRect();
    const box = tooltip.current.getBoundingClientRect();
    const inset = 12, gap = 8;
    const top = rect.top >= box.height + gap + inset
      ? rect.top - box.height - gap : rect.bottom + gap;
    setPosition({
      left: Math.max(inset, Math.min(rect.left + 13, window.innerWidth - box.width - inset)),
      top: Math.max(inset, Math.min(top, window.innerHeight - box.height - inset)),
    });
    function scrolled(event: Event) {
      if (!tooltip.current?.contains(event.target as Node)) close();
    }
    function key(event: KeyboardEvent) { if (event.key === "Escape") close(); }
    window.addEventListener("scroll", scrolled, true);
    window.addEventListener("resize", close);
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("scroll", scrolled, true);
      window.removeEventListener("resize", close);
      window.removeEventListener("keydown", key);
    };
  }, [open, title]);
  return <>
    <summary ref={anchor} aria-describedby={open ? id : undefined}
      onMouseEnter={() => show()} onMouseLeave={leave}
      onFocus={() => show(true)} onBlur={close} onClick={close}>
      <span className={streaming ? "pulse" : ""} />
      <span className="activity-title">{title}</span>
      <span className="activity-end">{status}</span>
      {children}
    </summary>
    {open && createPortal(<div ref={tooltip} id={id} role="tooltip"
      className="tool-command-tooltip" style={{ left: position?.left ?? 0,
        top: position?.top ?? 0, visibility: position ? "visible" : "hidden" }}
      onMouseEnter={clearTimers} onMouseLeave={leave}>
      {title}
    </div>, document.body)}
  </>;
}
