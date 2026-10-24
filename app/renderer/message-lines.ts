import { useLayoutEffect, useRef, useState } from "react";

// Measure rendered text, including wrapping and Markdown block layout. Ignore
// control labels (such as code-copy buttons), which are not reply content.
export function hasMultipleLines(body: HTMLElement): boolean {
  const user = body.querySelector<HTMLElement>(".user-text");
  if (user) {
    const style = getComputedStyle(user);
    const height = user.getBoundingClientRect().height -
      parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
    return height > parseFloat(style.lineHeight) + 2;
  }
  const root = body.querySelector(".markdown");
  if (!root) return false;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  let first: DOMRect | null = null;
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (!node.textContent?.trim() || node.parentElement?.closest("button")) continue;
    range.selectNodeContents(node);
    for (const rect of Array.from(range.getClientRects())) {
      if (rect.width < .5 || rect.height < .5) continue;
      if (!first) first = rect;
      // Inline emphasis and mixed font sizes can have different top positions
      // on the same line. Distinct lines do not overlap vertically.
      else if (rect.top >= first.bottom - 1 || rect.bottom <= first.top + 1)
        return true;
    }
  }
  return false;
}

export function useMessageLines(enabled: boolean, content: string, images = 0) {
  const body = useRef<HTMLDivElement>(null);
  const [multiline, setMultiline] = useState(false);
  useLayoutEffect(() => {
    const element = body.current;
    if (!enabled || !element) return;
    let frame = 0, disposed = false;
    const measure = () => {
      if (!disposed) setMultiline(images > 0 || hasMultipleLines(element));
    };
    const schedule = () => {
      if (disposed) return;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(measure);
    };
    measure();
    const resize = new ResizeObserver(schedule);
    resize.observe(element);
    if (element.firstElementChild) resize.observe(element.firstElementChild);
    // Markdown may update after its content prop, or after a held text selection
    // is released. Observe DOM changes rather than forcing Markdown to re-render.
    const changes = new MutationObserver(schedule);
    changes.observe(element, { subtree: true, childList: true, characterData: true });
    void document.fonts.ready.then(schedule);
    return () => {
      disposed = true;
      resize.disconnect();
      changes.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [enabled, content, images]);
  return { body, multiline };
}
