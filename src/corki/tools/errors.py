"""Direct-execution fatal failures and catchable Code Mode dispatch errors."""


class FatalToolError(RuntimeError):
    """Fail direct execution; never automatically retry the operation.

    Use ordinary exceptions or an error ToolResult for recoverable call errors.
    Code Mode follows the host's separate boundary: dispatch failures reject the
    cell's promise, including this channel. This is not a global revocation API.
    """


class CodeModeToolError(ValueError):
    """A committed dispatch failure or invalid bridge request, sent to JavaScript."""
