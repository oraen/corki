"""Framework-independent application definitions and validation."""

import json
from datetime import UTC, datetime


class AppError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def now():
    return datetime.now(UTC).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def definition(value, previous=None):
    if not isinstance(value, dict):
        raise AppError("应用配置必须是对象")
    allowed = {
        "name",
        "description",
        "type",
        "provider",
        "model",
        "prompt",
        "context",
        "directory",
        "permissions",
        "skills",
    }
    if set(value) - allowed:
        raise AppError("应用配置包含未知字段")
    result = dict(
        name="",
        description="",
        type="llm",
        provider="",
        model="",
        prompt="",
        context=True,
        directory="",
        permissions="",
        skills=[],
    )
    result.update(value)
    for key, maximum in (
        ("name", 80),
        ("description", 1000),
        ("prompt", 262144),
        ("provider", 200),
        ("model", 200),
        ("directory", 4096),
        ("permissions", 200),
    ):
        if not isinstance(result[key], str) or len(result[key]) > maximum or "\x00" in result[key]:
            raise AppError("应用字段格式或长度不正确：" + key)
        if key != "prompt":
            result[key] = result[key].strip()
    if not result["name"] or not result["prompt"].strip():
        raise AppError("请填写应用名称和提示词")
    if result["type"] not in ("llm", "agent"):
        raise AppError("应用类型无效")
    if previous and result["type"] != previous["type"]:
        raise AppError("应用类型创建后不可修改")
    if type(result["context"]) is not bool:
        raise AppError("上下文选项必须是布尔值")
    if result["model"] and not result["provider"]:
        raise AppError("请先选择模型所属供应商")
    if (
        not isinstance(result["skills"], list)
        or len(result["skills"]) > 100
        or any(not isinstance(s, str) or not s or len(s) > 4096 for s in result["skills"])
    ):
        raise AppError("Skills 选择无效")
    result["skills"] = list(dict.fromkeys(result["skills"]))
    if result["type"] == "llm":
        result.update(directory="", permissions="", skills=[])
    return result
