"""Persistent hook output; running and quiet success never become transcript text."""

from rich.text import Text

from corki.cli.display_text import visible_terminal_text


def hook_output_text(run):
    """Style provenance or the status bullet, never dim the hook's full output."""
    lines = hook_output_lines(run)
    if not lines:
        return Text()
    text = Text(visible_terminal_text("\n".join(lines)))
    if lines[0].startswith("↳ Hook · "):
        text.stylize("dim", 0, len("↳ Hook · "))
    else:
        text.stylize("bold green" if run.status == "completed" else "bold red", 0, 1)
    return text


def hook_output_lines(run):
    entries = [entry for entry in run.entries if entry.kind != "context"]
    if run.status == "running" or (run.status == "completed" and not entries):
        return ()
    warning = next((entry.text for entry in entries if entry.kind == "warning"), None)
    lines = []
    if run.status == "completed" and warning is not None:
        first, *rest = warning.split("\n")
        lines.append("↳ Hook · " + first)
        lines.extend("    " + line if line else "" for line in rest)
    else:
        title = {
            "completed": "Hook completed",
            "failed": "Hook failed",
            "blocked": "Blocked by hook",
            "stopped": "Hook stopped",
        }[run.status]
        lines.append("• " + title)
        if warning is not None:
            first, *rest = warning.split("\n")
            lines.append("  └ " + first)
            lines.extend("    " + line if line else "" for line in rest)
    for entry in entries:
        if entry.kind == "warning":
            continue
        first, *rest = entry.text.split("\n")
        lines.append("  └ " + first)
        lines.extend("    " + line if line else "" for line in rest)
    return tuple(lines)
