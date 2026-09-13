"""Keyboard-driven decisions; only explicit keys submit, never focus or rendering."""

from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import ConditionalKeyBindings, KeyBindings, merge_key_bindings
from prompt_toolkit.keys import Keys

from corki.cli.terminal_responses import frame_terminal_responses


async def choose_approval(
    session, *, execution: bool = False, scopes=None, details=None, palette=None
):
    frame_terminal_responses(session.app.input)
    labels = (
        ("Yes, proceed", "No, do not proceed", "Cancel this request")
        if execution
        else (
            "Yes, provide the requested info",
            "No, but continue without it",
            "Cancel this request",
        )
    )
    actions = ("accept", "decline", "cancel")
    if scopes is not None:
        scope_labels = {
            "once": "Yes, proceed once",
            "session": "Yes, allow this operation for this session",
            "rule": "Yes, save the proposed execution rule",
        }
        labels = tuple(scope_labels[scope] for scope in scopes) + (
            "No, continue without running it",
            "Cancel this request",
        )
        actions = tuple(("accept", {"scope": scope}) for scope in scopes) + (
            ("decline", None),
            ("cancel", None),
        )
    selected = 0
    bindings = KeyBindings()
    shortcuts = {}
    if execution or scopes is not None:
        shortcuts = {"y": actions[0], "d": actions[-2], "n": actions[-1]}
        for scope, key in (("session", "a"), ("rule", "p")):
            if scopes is not None and scope in scopes:
                shortcuts[key] = actions[scopes.index(scope)]
        for key, action in shortcuts.items():

            @bindings.add(key)
            def choose_shortcut(event, action=action):
                event.app.exit(result=action)

    for number, action in enumerate(actions[:9], start=1):

        @bindings.add(str(number))
        def choose_number(event, action=action):
            event.app.exit(result=action)

    @bindings.add("up")
    def previous(event):
        nonlocal selected
        selected = max(0, selected - 1)

    @bindings.add("down")
    def following(event):
        nonlocal selected
        selected = min(len(actions) - 1, selected + 1)

    @bindings.add("enter")
    def confirm(event):
        event.app.exit(result=actions[selected])

    @bindings.add("escape", eager=True)
    @bindings.add("c-c")
    @bindings.add("c-d")
    def cancel(event):
        event.app.exit(result=actions[-1])

    @bindings.add(Keys.BracketedPaste)
    @bindings.add(Keys.Any)
    def ignore_text(event):
        # Decisions must not populate the composer or its persistent history.
        pass

    def render():
        lines = [("", "\n")]
        for index, label in enumerate(labels):
            active = index == selected
            hint = next(
                (f" [{key}]" for key, action in shortcuts.items() if action == actions[index]), ""
            )
            lines.append(
                ("bold" if active else "", f"{'›' if active else ' '} {index + 1}. {label}{hint}\n")
            )
        lines.append(
            ("class:bottom-toolbar", "  ↑/↓ to select · Enter to confirm · Esc to cancel\n")
        )
        if details is not None:
            lines.append(("class:bottom-toolbar", "  Ctrl+A to view full details\n"))
        return lines

    pager = None
    if details is not None:
        from corki.cli.approval_details import ApprovalDetails

        pager = ApprovalDetails(session, details, palette=palette)
        bindings = merge_key_bindings(
            [
                ConditionalKeyBindings(bindings, Condition(lambda: not pager.active)),
                pager.bindings,
            ]
        )
    try:
        return await session.prompt_async(render, key_bindings=bindings, default="")
    except (KeyboardInterrupt, EOFError):
        return actions[-1]
    finally:
        if pager is not None:
            pager.restore()
        session.default_buffer.reset()
