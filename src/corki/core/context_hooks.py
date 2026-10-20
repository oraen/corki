"""Explicit prompt/Skill hook context; never starts a nested model or agent."""

import json
from pathlib import Path

from corki.skills.frontmatter import parse

CONTEXT_EVENTS = frozenset({"SessionStart", "SubagentStart", "UserPromptSubmit", "PreToolUse", "PostToolUse"})


def normalize(handler, event):
    if event not in CONTEXT_EVENTS:
        raise ValueError("提示词和 Skills 仅支持会话开始、子 Agent 开始、提交提问及工具执行前后")
    kind = handler["type"]
    result = {"type": kind}
    if kind == "prompt":
        value = handler.get("prompt")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("请填写提示词")
        result["prompt"] = value
    else:
        value = handler.get("path")
        if not isinstance(value, str) or not Path(value).is_absolute() or Path(value).name != "SKILL.md":
            raise ValueError("请选择有效的 SKILL.md 文件")
        result["path"] = str(Path(value))
    limit = handler.get("additionalContextLimit", 2500)
    if type(limit) is not int or limit < 0:
        raise ValueError("additionalContextLimit 必须为非负整数")
    result["additionalContextLimit"] = limit
    if handler.get("statusMessage") is not None:
        if not isinstance(handler["statusMessage"], str):
            raise ValueError("Hook 名称必须为文本")
        result["statusMessage"] = handler["statusMessage"]
    return result


def render_context(handler):
    if handler["type"] == "prompt":
        return handler["prompt"]
    path = Path(handler["path"])
    with path.open("rb") as stream:
        content = stream.read(128 * 1024 + 1)
    if len(content) > 128 * 1024:
        raise ValueError("SKILL.md 超过运行时 128 KB 读取上限")
    text = content.decode("utf-8")
    name, _, _ = parse(text, path.parent.name)
    return (f"Use the following Skill for this task when applicable. Skill: {name}\n"
            f"Skill file: {path}\nSkill base directory: {path.parent}\n"
            "Resolve relative script, reference and asset paths against this directory. "
            "Use the normal tools and permission checks; loading these instructions does not execute scripts.\n\n"
            + text)


def run(handler, payload):
    event = payload.get("hook_event_name")
    if event not in CONTEXT_EVENTS:
        raise ValueError("此事件不支持上下文注入")
    text = render_context(handler)
    return {"exit_code": 0, "stderr": "", "stdout": json.dumps({"hookSpecificOutput": {
        "hookEventName": event, "additionalContext": text}}, ensure_ascii=False)}


def validate_skill(handler):
    if handler["type"] != "skill":
        return
    try:
        render_context(handler)
    except (OSError, UnicodeError) as error:
        raise ValueError("Hook Skill 文件无法读取") from error
