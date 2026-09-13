"""Per-stream Markdown parsing with a mutable last top-level block."""

import re
from copy import copy

from markdown_it import MarkdownIt

from corki.cli.markdown import AssistantMarkdown
from corki.cli.markdown_fences import unwrap_markdown_fences


class MarkdownParseCache:
    def __init__(self):
        self.parser = MarkdownIt().enable("strikethrough").enable("table")
        self.markdown = AssistantMarkdown("")
        self.source = ""
        self.stable_chars = 0
        self.stable_lines = 0
        self.stable_tokens = []
        self.global_references = False
        self.open_fence: tuple[str, int] | None = None

    def document(self, source: str) -> AssistantMarkdown:
        source = unwrap_markdown_fences(source)
        if source == self.source:
            return self.markdown
        if self._append_code(source):
            return self.markdown
        if not source.startswith(self.source):
            self.stable_chars = self.stable_lines = 0
            self.stable_tokens = []
            self.global_references = False
        # Markdown normalizes CRLF/lone CR. Keep offsets unambiguous by using
        # full parsing for that source rather than mapping normalized positions.
        if self.global_references or "\r" in source:
            tokens = self.parser.parse(source)
            self.stable_chars = self.stable_lines = 0
            self.stable_tokens = []
        else:
            pending = source[self.stable_chars :]
            env = {}
            fresh = self.parser.parse(pending, env)
            if env.get("references"):
                # A late definition can reinterpret tokens in the retained prefix.
                self.global_references = True
                tokens = self.parser.parse(source)
                self.stable_chars = self.stable_lines = 0
                self.stable_tokens = []
            else:
                starts = [
                    index
                    for index, token in enumerate(fresh)
                    if token.level == 0 and token.nesting >= 0 and token.map is not None
                ]
                boundary = starts[-1] if starts else 0
                line_boundary = fresh[boundary].map[0] if starts else 0
                char_boundary = sum(len(line) + 1 for line in pending.split("\n")[:line_boundary])
                for token in fresh:
                    self._rebase(token, self.stable_lines)
                tokens = [*self.stable_tokens, *fresh]
                self.stable_tokens.extend(fresh[:boundary])
                self.stable_chars += char_boundary
                self.stable_lines += line_boundary
        self.source = source
        self.markdown.markup = source
        self.markdown.parsed = tokens
        self.open_fence = self._detect_open_fence(source, tokens)
        return self.markdown

    def _append_code(self, source):
        fence, self.open_fence = self.open_fence, None
        if fence is None or not source.startswith(self.source):
            return False
        added = source[len(self.source) :]
        if not added.endswith("\n") or "\r" in added or "\0" in added:
            return False
        marker, length = fence
        if any(
            len(line.lstrip(" \t")) - len(line.lstrip(" \t").lstrip(marker)) >= length
            for line in added.split("\n")
        ):
            return False
        # Replace the token identity so the body-render cache cannot reuse the
        # prior code snapshot. Never mutate a retained/cache-owned token in place.
        previous = self.markdown.parsed[-1]
        token = copy(previous)
        token.content += added
        token.map = [previous.map[0], previous.map[1] + added.count("\n")]
        self.markdown.parsed = [*self.markdown.parsed[:-1], token]
        self.source = self.markdown.markup = source
        self.open_fence = fence
        return True

    def _detect_open_fence(self, source, tokens):
        if self.global_references or not tokens or "\r" in source or "\0" in source:
            return None
        token = tokens[-1]
        if token.type != "fence" or token.level or not source.endswith("\n"):
            return None
        start = sum(len(line) + 1 for line in source.split("\n")[: token.map[0]])
        raw = source[start:]
        opening, separator, code = raw.partition("\n")
        if not separator or code != token.content:
            return None
        match = re.fullmatch(r"(`{3,}|~{3,})(.*)", opening)
        if match is None:
            return None
        marker, info = match.groups()
        info = info.strip(" \t\v\f")
        if "&" in info or "\\" in info or marker[0] == "`" and "`" in info:
            return None
        language = re.split(r"[, \t]", info, maxsplit=1)[0]
        if not language or len(language.encode("utf-8")) > 4096:
            return None
        if any(
            len(line.lstrip(" \t")) - len(line.lstrip(" \t").lstrip(marker[0])) >= len(marker)
            for line in code.split("\n")
        ):
            return None
        return marker[0], len(marker)

    @staticmethod
    def _rebase(token, lines):
        if token.map is not None:
            token.map = [line + lines for line in token.map]
        for child in token.children or ():
            MarkdownParseCache._rebase(child, lines)
