"""Display-only command summaries. Never used for execution or authorization."""

import json
import re
import shlex
from dataclasses import dataclass


@dataclass(frozen=True)
class Activity:
    kind: str
    detail: str


def _operands(tokens, value_flags=()):
    result = []
    args = iter(tokens)
    for value in args:
        if value == "--":
            result.extend(args)
            break
        if value in value_flags:
            if next(args, None) is None:
                return None
        elif not value.startswith("-"):
            result.append(value)
    return result


def _simple(tokens):
    command, *args = tokens
    if command == "git" and args:
        if args[0] == "grep":
            return _simple(["grep", *args[1:]])
        if args[0] == "ls-files":
            paths = _operands(args[1:])
            return Activity("List", ", ".join(paths) or "git ls-files")
    if command == "cat":
        paths = _operands(args)
        if paths and len(paths) == 1:
            return Activity("Read", paths[0])
    elif command in {"head", "tail"}:
        paths = _operands(args, {"-n", "-c", "--lines", "--bytes"})
        if paths and len(paths) == 1:
            return Activity("Read", paths[0])
    elif command == "sed":
        if len(args) == 3 and args[0] == "-n" and re.fullmatch(r"\d+(,\d+)?p", args[1]):
            return Activity("Read", args[2])
    elif command in {"ls", "tree", "eza", "exa"}:
        paths = _operands(args, {"-I", "-L", "-P", "--sort", "--color", "--time-style", "--format"})
        if paths is not None:
            return Activity("List", ", ".join(paths) or command)
    elif command in {"rg", "grep", "rga", "ripgrep-all"}:
        if any(a in {"--pre", "--pre-glob", "--replace"} or a.startswith("--pre=") for a in args):
            return None
        paths = _operands(
            args,
            {
                "-g",
                "--glob",
                "--iglob",
                "-t",
                "--type",
                "--type-add",
                "--type-not",
                "-m",
                "--max-count",
                "-A",
                "-B",
                "-C",
                "--context",
                "--max-depth",
            },
        )
        if paths is None:
            return None
        if "--files" in args:
            return Activity("List", ", ".join(paths) or "rg --files")
        if paths:
            return Activity(
                "Search", paths[0] + (" in " + ", ".join(paths[1:]) if paths[1:] else "")
            )
    return None


def _formatting(tokens):
    command, *args = tokens
    if command in {"head", "tail"}:
        return (
            not args
            or (len(args) == 1 and re.fullmatch(r"-(?:[nc])?[+\-]?\d+", args[0]) is not None)
            or (
                len(args) == 2
                and args[0] in {"-n", "-c"}
                and re.fullmatch(r"[+\-]?\d+", args[1]) is not None
            )
        )
    if command == "sed":
        return len(args) == 2 and args[0] == "-n" and bool(re.fullmatch(r"\d+(,\d+)?p", args[1]))
    if command in {"wc", "sort", "uniq"}:
        flags = {"wc": "clwmL", "sort": "nrfuVh", "uniq": "cdu"}[command]
        return all(re.fullmatch("-[" + flags + "]+", arg) for arg in args)
    return False


def classify(name, arguments_preview):
    """Malformed/truncated arguments and ambiguous shell forms remain ordinary tools."""
    if name not in {"exec_command", "shell"} or len(arguments_preview) > 100_000:
        return ()
    try:
        args = json.loads(arguments_preview)
        command = args.get("cmd", args.get("command")) if isinstance(args, dict) else None
        if not isinstance(command, str) or not command:
            return ()
        # Do not let shlex erase syntax which could hide execution or redirection.
        # A future AST adapter can classify more forms without weakening this fallback.
        if any(c in command for c in "$`<>()\n\r"):
            return ()
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except (ValueError, TypeError):
        return ()
    groups, current, connector = [], [], None
    for token in tokens:
        if token in {";", "&&", "|", "||"}:
            if not current:
                return ()
            groups.append((connector, current))
            current = []
            connector = token
        elif token and all(c in ";&|" for c in token):
            return ()
        else:
            current.append(token)
    if current:
        groups.append((connector, current))
    elif connector != ";":
        return ()
    result = []
    for index, (connector, group) in enumerate(groups):
        if group[0] == "cd" and connector != "|":
            if index + 1 < len(groups) and groups[index + 1][0] == "|":
                return ()  # pipeline cwd changes do not apply to the next process
            path = group[1:]
            explicit = path[:1] == ["--"]
            if explicit:
                path = path[1:]
            if len(path) == 1 and (explicit or not path[0].startswith("-")):
                continue
            return ()
        if len(groups) > 1 and group == ["true"]:
            continue
        if connector == "|" and _formatting(group):
            continue
        activity = _simple(group)
        if activity is None:
            return ()  # Codex collapses a mixed known/unknown script into Run.
        if not result or result[-1] != activity:
            result.append(activity)
    return tuple(result)


def summary_rows(activities):
    """Coalesce consecutive reads only; searches keep their original sequence."""
    rows = []
    names = []
    for activity in (*activities, Activity("", "")):
        if activity.kind == "Read":
            if activity.detail not in names:
                names.append(activity.detail)
            continue
        if names:
            rows.append(Activity("Read", ", ".join(names)))
            names = []
        if activity.kind:
            rows.append(activity)
    return tuple(rows)


def summary_calls(calls):
    """Only coalesce whole read-only calls, as Codex's exploring display does."""
    rows, reads = [], []
    for activities in calls:
        if activities and all(a.kind == "Read" for a in activities):
            reads.extend(activities)
            continue
        rows.extend(summary_rows(reads))
        reads.clear()
        rows.extend(activities)
    rows.extend(summary_rows(reads))
    return tuple(rows)


@dataclass
class ExplorationCall:
    call_id: str
    activities: tuple
    finished: bool = False
    failed: bool = False
    output: str = ""
    exit_code: int | None = None
    session_id: int | None = None


class Exploration:
    """Bounded live display state; authoritative full output remains in transcript/storage."""

    def __init__(self):
        self.calls = {}

    def add(self, call_id, activities):
        self.calls[call_id] = ExplorationCall(call_id, activities)

    def output(self, call_id, text):
        call = self.calls.get(call_id)
        if call is None:
            return False
        call.output = (call.output + text)[-2048:]
        return True

    def complete(self, call_id, failed, exit_code=None, session_id=None):
        call = self.calls.get(call_id)
        if call is None:
            return False
        call.finished, call.failed = True, failed or exit_code not in (None, 0)
        call.exit_code, call.session_id = exit_code, session_id
        return True

    @property
    def active(self):
        return any(not c.finished for c in self.calls.values())
