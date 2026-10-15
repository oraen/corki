"""Independent unified-exec model, log and nested-call output projections."""

from corki.context.hosted_output import truncate_output_text
from corki.protocol.tools import CodeModeOutput, ToolCall, ToolResult
from corki.protocol.truncation import TruncationPolicy
from corki.protocol.wire_numbers import dumps_wire
from corki.tools.base import ToolContext
from corki.tools.builtin.process import ProcessObservation


def _body(observation: ProcessObservation, policy: TruncationPolicy) -> str:
    text = observation.output
    size = len(text.encode("utf-8"))
    omitted = observation.output_omitted_bytes
    marker = f"... {omitted} bytes omitted ..." if omitted else ""
    if size <= policy.byte_budget:
        return f"{marker}\n{text}" if marker and marker not in text else text
    truncated = truncate_output_text(text, policy)
    count = (size + 3) // 4
    if omitted:
        count = observation.original_token_count or count
        notice = f"{marker}\n" if marker not in truncated else ""
    else:
        # Rust str::lines splits on LF/CRLF, not on all Unicode separators.
        lines = text.count("\n") + (not text.endswith("\n"))
        notice = f"Total output lines: {lines}\n"
    return f"Warning: truncated output (original token count: {count})\n{notice}\n{truncated}"


def shell_result(
    call: ToolCall, observation: ProcessObservation, context: ToolContext
) -> ToolResult:
    requested = call.arguments.get("max_output_tokens") if call.arguments is not None else None
    requested_policy = TruncationPolicy("tokens", 10000 if requested is None else requested)
    model_policy = context.model_output_policy
    policy = (
        requested_policy
        if requested_policy.byte_budget < model_policy.byte_budget
        else model_policy
    )
    sections = []
    if observation.chunk_id:
        sections.append(f"Chunk ID: {observation.chunk_id}")
    sections.append(f"Wall time: {observation.wall_time_seconds:.4f} seconds")
    if observation.exit_code is not None:
        sections.append(f"Process exited with code {observation.exit_code}")
    if observation.session_id is not None:
        sections.append(f"Process running with session ID {observation.session_id}")
    if observation.original_token_count is not None:
        sections.append(f"Original token count: {observation.original_token_count}")
    if observation.timed_out:
        sections.append("Timed out.")
    sections.append("Output:")
    header = "\n".join(sections)
    budget = max(0, model_policy.history_allowance().byte_budget - len(header.encode()) - 1)
    body = _body(observation, policy)
    while len(body.encode()) > budget and policy.limit:
        excess = len(body.encode()) - budget
        reduction = (excess + 3) // 4 if policy.mode == "tokens" else excess
        policy = TruncationPolicy(policy.mode, max(0, policy.limit - reduction))
        body = _body(observation, policy)
    log = observation.output
    if observation.output_omitted_bytes:
        marker = f"... {observation.output_omitted_bytes} bytes omitted ..."
        if marker not in log:
            log = f"{marker}\n{log}"
    structured = {
        "output": observation.output if requested is None else _body(observation, requested_policy),
        "wall_time_seconds": observation.wall_time_seconds,
    }
    for key, value in (
        ("chunk_id", observation.chunk_id or None),
        ("exit_code", observation.exit_code),
        ("session_id", observation.session_id),
        ("original_token_count", observation.original_token_count),
    ):
        if value is not None:
            structured[key] = value
    return ToolResult(
        call_id=call.id,
        tool_name=call.name,
        content=f"{header}\n{body}",
        display_content=f"{header}\n{log}",
        is_error=observation.timed_out,
        code_mode_output=CodeModeOutput(structured),
        post_tool_use_json=dumps_wire(
            {
                "version": 1,
                "tool_name": "Bash",
                "tool_use_id": observation.terminal_info.item_id or str(call.id),
                "tool_input": {"command": observation.terminal_info.command},
                "tool_response": _body(observation, model_policy),
            }
        )
        if observation.session_id is None and observation.terminal_info is not None
        else None,
    )
