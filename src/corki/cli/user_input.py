"""Question modal: navigation is not submission; notes never use input history.

Enter commits the current answer and advances. The last question either submits
all answers or asks explicitly about unanswered questions. Esc clears notes first,
then interrupts the owning Turn. Non-blocking requests resolve with no answers
after 60 seconds of grace plus a 60-second countdown, unless the user interacts.
"""

import math
import time

from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys

from corki.cli.elicitation import display_text


async def collect_user_input(session, request, *, clock=time.monotonic):
    questions = request.questions
    if not questions:
        return {"answers": {}}
    current = 0
    selected = [0 if q.options else None for q in questions]
    committed = [False] * len(questions)
    drafts = [""] * len(questions)
    notes = False
    confirmation = None
    touched = False
    started = clock()
    restoring = False
    bindings = KeyBindings()
    options_focus = Condition(lambda: not notes and confirmation is None)
    selection_focus = Condition(lambda: not notes or confirmation is not None)

    def save():
        drafts[current] = session.default_buffer.text

    def load():
        nonlocal restoring
        restoring = True
        try:
            session.default_buffer.text = drafts[current]
            session.default_buffer.cursor_position = len(drafts[current])
        finally:
            restoring = False

    def move(offset):
        nonlocal current, notes
        save()
        current = (current + offset) % len(questions)
        notes = bool(drafts[current]) or not questions[current].options
        load()

    def payload():
        answers = {}
        for index, question in enumerate(questions):
            values = []
            choice = selected[index]
            if committed[index]:
                if choice is not None:
                    values.append(
                        question.options[choice].label
                        if choice < len(question.options)
                        else "None of the above"
                    )
                if drafts[index].strip():
                    values.append("user_note: " + drafts[index].strip())
            answers[question.id] = {"answers": values}
        return {"answers": answers}

    def advance(event):
        nonlocal confirmation
        save()
        if current + 1 < len(questions):
            move(1)
        elif not all(committed):
            confirmation = 0
        else:
            event.app.exit(result=payload())

    def clear_notes():
        nonlocal notes
        notes = False
        drafts[current] = ""
        committed[current] = False
        load()

    @bindings.add("up")
    @bindings.add("k", filter=selection_focus)
    def up(event):
        change_selection(-1)

    @bindings.add("down")
    @bindings.add("j", filter=selection_focus)
    def down(event):
        change_selection(1)

    def change_selection(offset):
        nonlocal confirmation
        if confirmation is not None:
            confirmation = (confirmation + offset) % 2
            return
        count = len(questions[current].options) + int(questions[current].is_other)
        if count:
            selected[current] = ((selected[current] or 0) + offset) % count
            committed[current] = False

    @bindings.add("c-p")
    @bindings.add("pageup")
    @bindings.add("left", filter=options_focus)
    @bindings.add("h", filter=options_focus)
    def previous(event):
        if confirmation is None:
            move(-1)

    @bindings.add("c-n")
    @bindings.add("pagedown")
    @bindings.add("right", filter=options_focus)
    @bindings.add("l", filter=options_focus)
    def following(event):
        if confirmation is None:
            move(1)

    @bindings.add("enter")
    def submit(event):
        nonlocal confirmation, notes
        if confirmation is not None:
            if confirmation == 0:
                event.app.exit(result=payload())
            else:
                confirmation = None
                move(committed.index(False) - current)
            return
        if not notes and selected[current] == len(questions[current].options):
            notes = True
            return
        save()
        committed[current] = selected[current] is not None or bool(drafts[current].strip())
        advance(event)

    @bindings.add("tab")
    def tab(event):
        nonlocal notes
        if confirmation is None:
            if notes:
                clear_notes()
            elif selected[current] is not None:
                notes = True

    @bindings.add("escape")
    @bindings.add("backspace", filter=Condition(lambda: confirmation is not None))
    def escape(event):
        nonlocal confirmation
        if confirmation is not None:
            confirmation = None
            move(committed.index(False) - current)
        elif notes:
            clear_notes()
        else:
            event.app.exit(result=None)

    @bindings.add("c-c")
    @bindings.add("c-d")
    def interrupt(event):
        if notes and session.default_buffer.text and confirmation is None:
            session.default_buffer.text = ""
            committed[current] = False
        else:
            event.app.exit(result=None)

    @bindings.add("backspace", filter=options_focus)
    @bindings.add("delete", filter=options_focus)
    def clear_selection(event):
        selected[current] = None
        clear_notes()

    @bindings.add(" ", filter=options_focus)
    def commit(event):
        committed[current] = selected[current] is not None

    @bindings.add(Keys.BracketedPaste)
    def paste(event):
        insert(event, event.data)

    @bindings.add("escape", "enter", filter=Condition(lambda: notes and confirmation is None))
    def newline(event):
        insert(event, "\n")

    @bindings.add(Keys.Any, filter=Condition(lambda: confirmation is not None))
    def confirmation_key(event):
        nonlocal confirmation
        if event.data in ("1", "2"):
            confirmation = int(event.data) - 1

    @bindings.add(Keys.Any, filter=options_focus)
    def typed(event):
        if event.data in "123456789":
            index = int(event.data) - 1
            count = len(questions[current].options) + int(questions[current].is_other)
            if index < count:
                selected[current] = index
                committed[current] = True
                advance(event)
        else:
            insert(event, event.data)

    def insert(event, text):
        nonlocal notes
        if confirmation is None:
            notes = True
            committed[current] = False
            event.current_buffer.insert_text(text)

    def interacted(event):
        nonlocal touched
        touched = True

    def changed(buffer):
        if notes and not restoring:
            committed[current] = False

    def render():
        if confirmation is not None:
            return [("", "\n  Submit with unanswered questions?\n\n")] + [
                (
                    "bold" if i == confirmation else "",
                    f"  {'›' if i == confirmation else ' '} {label}\n",
                )
                for i, label in enumerate(("1. Proceed", "2. Go back"))
            ]
        question = questions[current]
        lines = [
            (
                "",
                f"\n  Question {current + 1}/{len(questions)} "
                f"({committed.count(False)} unanswered)\n  {display_text(question.question)}\n\n",
            )
        ]
        choices = [(o.label, o.description) for o in question.options]
        if question.is_other and question.options:
            choices.append(("None of the above", "Optionally, add details in notes (tab)."))
        for i, (label, description) in enumerate(choices):
            active = selected[current] == i
            lines.append(
                (
                    "bold" if active else "",
                    f"  {'›' if active else ' '} {i + 1}. "
                    f"{display_text(label)}  {display_text(description)}\n",
                )
            )
        lines.append(("", "\n  tab to add/clear notes | enter to submit | esc to interrupt\n"))
        if not request.is_blocking and not touched:
            remaining = 120 - (clock() - started)
            if remaining <= 60:
                lines.append(("", f"  auto-resolves in {max(0, math.ceil(remaining))}s\n"))
        lines.append(("", "  Add notes: " if notes else ""))
        return lines

    def tick(app):
        if not app.is_done and not request.is_blocking and not touched and clock() - started >= 120:
            app.exit(result={"answers": {}})

    session.app.key_processor.before_key_press += interacted
    session.default_buffer.on_text_changed += changed
    session.app.before_render += tick
    try:
        return await session.prompt_async(
            render,
            key_bindings=bindings,
            default="",
            refresh_interval=1,
            is_password=Condition(lambda: questions[current].is_secret),
        )
    except (KeyboardInterrupt, EOFError):
        return None
    finally:
        session.app.key_processor.before_key_press -= interacted
        session.default_buffer.on_text_changed -= changed
        session.app.before_render -= tick
        session.default_buffer.reset()
