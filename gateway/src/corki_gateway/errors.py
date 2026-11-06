"""Diagnostics from first-party transports, distinct from arbitrary plugin errors."""


class DeliveryError(ValueError):
    """A bounded transport diagnostic, already stripped of credentials by its adapter."""


def delivery_error(detail):
    if not isinstance(detail, dict) or detail.get("provider") not in ("dingtalk", "wechat"):
        return None
    lines = [
        "微信消息发送失败"
        if detail["provider"] == "wechat"
        else "钉钉消息撤回失败"
        if detail.get("operation") == "recall"
        else "钉钉消息发送失败"
    ]
    for key, label, limit in (
        ("message", "原因", 1000),
        ("code", "错误码", 100),
        ("status", "HTTP 状态码", 3),
        ("path", "接口", 160),
        ("request_id", "请求 ID", 128),
    ):
        value = detail.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and value != "":
            lines.append(f"{label}：{str(value)[:limit]}")
    return DeliveryError("\n".join(lines))
