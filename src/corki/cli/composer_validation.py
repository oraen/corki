"""Validate the expanded message before input history or queue admission."""

from prompt_toolkit.validation import ValidationError, Validator

MAX_USER_INPUT_TEXT_CHARS = 1 << 20


class ComposerValidator(Validator):
    def __init__(self, draft):
        self.draft = draft

    def validate(self, document):
        draft = self.draft()
        text = draft.expanded()[0] if document.text == draft.text else document.text
        actual = len(text.strip())
        if actual > MAX_USER_INPUT_TEXT_CHARS:
            raise ValidationError(
                cursor_position=document.cursor_position,
                message=(
                    f"Message exceeds the maximum length of {MAX_USER_INPUT_TEXT_CHARS} "
                    f"characters ({actual} provided)."
                ),
            )
