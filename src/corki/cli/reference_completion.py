"""Local reference candidates and typed selection; no remote service catalog."""

import re
import shutil
from bisect import insort
from dataclasses import dataclass
from pathlib import PurePath

from prompt_toolkit.completion import Completion
from prompt_toolkit.document import Document

from corki.cli.display_text import visible_terminal_text
from corki.execution.owned_process import run_owned
from corki.protocol.input_mentions import InputMention

_TOKEN = re.compile(r"(?:^|\s)([@$])([^\s@$]*)$")


class FileSearchUnavailable(ValueError):
    """Fixed public search error; never includes subprocess or private path output."""


@dataclass(frozen=True)
class Reference:
    label: str
    description: str
    selector: InputMention | None = None
    rank: int = 2
    display_name: str | None = None
    search_terms: tuple[str, ...] = ()
    file_score: int | None = None


def plugin_mention_name(name, display):
    segments = re.findall(r"([^-_]+)([-_]?)", name)
    shown = re.findall(r"[A-Za-z0-9]+", display)
    if len(segments) == len(shown) and all(
        segment.lower() == visible.lower()
        for (segment, _), visible in zip(segments, shown, strict=True)
    ):
        return "".join(
            visible + separator for (_, separator), visible in zip(segments, shown, strict=True)
        )
    return re.sub(r"(^|[-_])([a-z])", lambda match: match[1] + match[2].upper(), name)


def target(document):
    match = _TOKEN.search(document.text_before_cursor)
    if match is None:
        return None
    return match[1], match[2], match.start(1)


def catalog(skills, plugins, sigil):
    owners = {p.identity for p in plugins if p.enabled and not p.error}
    rows = []
    for skill in skills:
        if sigil == "@" and skill.plugin_id in owners:
            continue
        rows.append(
            Reference(
                "$" + skill.qualified_name,
                str(skill.path) + " — " + (skill.short_description or skill.description),
                InputMention(skill.qualified_name, str(skill.path), "skill"),
                1,
            )
        )
    if sigil == "@":
        for plugin in plugins:
            if plugin.enabled and not plugin.error:
                canonical, _, marketplace = plugin.identity.partition("@")
                name = plugin_mention_name(canonical, plugin.display_name)
                rows.append(
                    Reference(
                        "@" + name,
                        plugin.description or "Plugin",
                        InputMention(name, "plugin://" + plugin.identity),
                        0,
                        plugin.display_name,
                        tuple(dict.fromkeys((canonical, plugin.identity, marketplace))),
                    )
                )
    return rows


def fuzzy_match(label, query):
    """Codex tool-candidate scoring, with indices in original Unicode text."""
    if not query:
        return (), 2**31 - 1
    lowered = []
    original_indices = []
    for index, char in enumerate(label):
        expansion = char.lower()
        lowered.extend(expansion)
        original_indices.extend([index] * len(expansion))
    haystack = "".join(lowered)
    needle = query.lower()
    position = 0
    indices = []
    last = 0
    for char in needle:
        found = haystack.find(char, position)
        if found < 0:
            return None
        indices.append(original_indices[found])
        last = found
        position = found + 1
    first = original_indices.index(indices[0])
    score = max(0, last - first + 1 - len(needle)) - (100 if first == 0 else 0)
    return tuple(sorted(set(indices))), score


def match_score(label, query):
    result = fuzzy_match(label, query)
    return result[1] if result is not None else None


def reference_match_score(reference, query):
    if reference.file_score is not None:
        # Native path results have already matched this query using Nucleo.
        # Re-filtering as skill labels would lose normalized/prefix matches.
        return 0, -reference.file_score
    if not query:
        return 0, 0
    display = reference.display_name or reference.label.lstrip("@$")
    score = match_score(display, query)
    if score is not None:
        return 0, score
    scores = [
        score
        for term in reference.search_terms
        if term and (score := match_score(term, query)) is not None
    ]
    return (1, min(scores)) if scores else None


class FileCandidates:
    """Bounded top matches from NUL-delimited paths, not a full project index."""

    def __init__(self, query, limit=100):
        self.query = query
        self.limit = limit
        self.pending = b""
        self.ranked = []
        self.rows = {}

    def add(self, path, kind):
        if path in self.rows:
            return
        score = match_score(path, self.query)
        if score is None:
            return
        key = (score, path)
        if len(self.ranked) == self.limit and key >= self.ranked[-1]:
            return
        insort(self.ranked, key)
        self.rows[path] = Reference(path, kind)
        if len(self.ranked) > self.limit:
            _, removed = self.ranked.pop()
            del self.rows[removed]

    def feed(self, chunk):
        values = (self.pending + chunk).split(b"\0")
        self.pending = values.pop()
        if len(self.pending) > 65536:
            raise ValueError("file search path exceeds its limit")
        for value in values:
            if len(value) > 65536:
                raise ValueError("file search path exceeds its limit")
            try:
                path = value.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if not path or any(ord(char) < 32 or ord(char) == 127 for char in path):
                continue
            self.add(path, "File")
            for parent in PurePath(path).parents:
                if parent == PurePath("."):
                    break
                self.add(str(parent), "Directory")

    def finish(self):
        if self.pending:
            raise ValueError("incomplete file search output")
        return tuple(self.rows[path] for _, path in self.ranked)


async def files(cwd, query=""):
    from corki.cli import native_file_search

    if (binary := native_file_search.executable()) is not None:
        try:
            return tuple(
                Reference(path, "Directory" if directory else "File", file_score=score)
                for path, directory, score in await native_file_search.search(binary, cwd, query)
            )
        except (OSError, ValueError):
            # Do not silently replace failed native results with different rg
            # semantics. rg is only a fallback for installations without a helper.
            raise FileSearchUnavailable(
                "File search failed or exceeded its limits. Try again."
            ) from None
    executable = shutil.which("rg")
    if executable is None:
        raise FileSearchUnavailable("File search unavailable: rg is not installed.")
    candidates = FileCandidates(query)
    try:
        await run_owned(
            [executable, "--files", "--hidden", "--follow", "-0", "-g", "!.git"],
            b"",
            cwd=cwd,
            output_limit=64_000_000,
            output_consumer=candidates.feed,
            timeout=3,
            accepted_exit_codes=(0, 1),  # rg uses 1 for an empty result, not a failure.
        )
        return candidates.finish()
    except (OSError, ValueError):
        raise FileSearchUnavailable(
            "File search failed or exceeded its limits. Try again."
        ) from None


class ReferenceCompletion(Completion):
    def __init__(self, reference, document, start):
        label = reference.label
        if reference.selector is None and any(c.isspace() for c in label) and '"' not in label:
            label = '"' + label + '"'
        super().__init__(
            label,
            start_position=start - document.cursor_position,
            display=visible_terminal_text(reference.display_name or reference.label),
            display_meta=visible_terminal_text(reference.description)[:200],
        )
        self.reference = reference
        self.source = document.text
        self.cursor = document.cursor_position
        self.start = start


def accept_reference(buffer, draft, *, on_file=None):
    state = buffer.complete_state
    selected = state.current_completion if state is not None else None
    if not isinstance(selected, ReferenceCompletion):
        return False
    if buffer.text != selected.source or buffer.cursor_position != selected.cursor:
        buffer.cancel_completion()
        return True  # stale selection must neither mutate nor submit the draft
    end = selected.cursor
    while end < len(buffer.text) and not buffer.text[end].isspace():
        end += 1
    start, label = selected.start, selected.text
    text = buffer.text[:start] + label + " " + buffer.text[end:]
    buffer.cancel_completion()
    buffer.document = Document(text, start + len(label) + 1)
    if selected.reference.selector is not None:
        draft.bind(start, label, selected.reference.selector)
    elif on_file is not None:
        on_file(selected.reference.label, label + " ", start)
    return True
