import { marked } from "marked";
import DOMPurify from "dompurify";
import hljs from "highlight.js/lib/common";
const renderer = new marked.Renderer();
renderer.code = ({ text, lang }) => {
  const language = (lang || "").split(/\s/)[0];
  const code =
    language && hljs.getLanguage(language)
      ? hljs.highlight(text, { language, ignoreIllegals: true }).value
      : escape(text);
  return `<div class="code-block"><pre><code class="hljs">${code}\n</code></pre></div>`;
};
function escape(s: string) {
  return s.replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ]!,
  );
}
export function markdown(content: string) {
  return DOMPurify.sanitize(
    marked.parse(content, { renderer, gfm: true, breaks: true, async: false }),
    {
      // Markdown needs HTML formatting only. Exclude SVG/MathML resource
      // surfaces rather than depending solely on the window's network policy.
      USE_PROFILES: { html: true },
      FORBID_TAGS: [
        "img",
        "iframe",
        "video",
        "audio",
        "style",
        "form",
        "input",
        "button",
      ],
      FORBID_ATTR: ["style", "srcset"],
      ALLOW_DATA_ATTR: false,
    },
  );
}

// Highlight escaped text only; never interpret tool output as HTML or Markdown.
export function highlightedText(content: string, language: string): string {
  return language !== "plaintext" &&
    content.length <= 100000 &&
    hljs.getLanguage(language)
    ? hljs.highlight(content, { language, ignoreIllegals: true }).value
    : escape(content);
}
