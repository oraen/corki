"""Project explicit shell status into display events without changing tool success semantics."""


def shell_display_status(name, result):
    if name not in {"exec_command", "write_stdin"} or result.code_mode_output is None:
        return {}
    value = result.code_mode_output.value
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in ("exit_code", "session_id") if type(value.get(key)) is int}
