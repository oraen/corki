"""Validated desktop conversation overrides; never write the global config."""

import json
from dataclasses import replace

KEYS = {"provider", "model", "reasoning", "contextWindow", "permissions", "mode", "memories", "skills"}


def session_settings(base, value):
    if not isinstance(value, dict) or set(value) - KEYS:
        raise ValueError("无效的会话设置")
    for key in ("provider", "model", "reasoning"):
        if key in value and (not isinstance(value[key], str) or not value[key].strip() or len(value[key]) > 300):
            raise ValueError("供应商、模型和推理等级必须是有效名称")
    provider = value.get("provider", base.provider_id)
    # Changing only the supplier chooses its default, rather than another supplier's model.
    model = value.get("model", None if "provider" in value else base.model)
    configured = json.loads(base._provider_document or "{}").get("providers", {})
    profile = configured.get(provider)
    if not isinstance(profile, dict) or not profile.get("enabled", True):
        raise ValueError("供应商不可用，请重新选择")
    if model is not None and model not in profile.get("models", {}):
        raise ValueError("模型不可用，请重新选择")
    result = base.for_model(provider, model)
    info = result.model_context_info(result.model)
    changes = {}
    if "reasoning" in value:
        if value["reasoning"] not in info.supported_reasoning_levels:
            raise ValueError("当前模型不支持该推理等级")
        changes["reasoning_effort"] = value["reasoning"]
    if "contextWindow" in value:
        window = value["contextWindow"]
        if type(window) is not int or not 1024 <= window <= 16_777_216:
            raise ValueError("上下文窗口必须在 1K 到 16384K 之间")
        if info.max_context_window is not None and window > info.max_context_window:
            raise ValueError("上下文窗口不能超过当前模型的上限")
        changes["model_context_window_override"] = window
    for key, field in (("skills", "skills_enabled"), ("memories", "memories_enabled")):
        if key in value:
            if type(value[key]) is not bool:
                raise ValueError("记忆和 Skills 开关必须是布尔值")
            changes[field] = value[key]
    if "memories" in value:
        changes["memories_use"] = value["memories"]
    if "mode" in value:
        if value["mode"] not in ("default", "plan"):
            raise ValueError("无效的工作模式")
        changes["collaboration_mode"] = value["mode"]
    if "permissions" in value:
        names = {"read-only": ":read-only", "workspace": ":workspace", "full": ":danger-full-access"}
        selected = value["permissions"]
        if not isinstance(selected, str) or selected not in names:
            raise ValueError("无效的操作权限")
        if result.execution_permissions is None:
            raise ValueError("当前环境不支持修改操作权限")
        approval = "never" if selected == "full" else "on-request"
        changes["execution_permissions"] = replace(
            result.execution_permissions,
            profile_json=json.dumps({"type": "selection", "default_permissions": names[selected]}),
            active_profile=None, profile_workspace_roots=(), select_from_config=False,
            approval_policy_json=json.dumps(approval), requested_approval_policy_json=json.dumps(approval),
            approval_policy_explicit=True,
        )
    return replace(result, **changes)


def session_signature(settings):
    # Rebuild an idle host only when its effective defaults actually changed.
    return (settings.provider_id, settings.model, settings.reasoning_effort,
            settings.main_context_limits.raw_tokens, settings.collaboration_mode,
            settings.execution_permissions, settings.memories_enabled, settings.memories_use,
            settings.skills_enabled, settings._provider_connection)
