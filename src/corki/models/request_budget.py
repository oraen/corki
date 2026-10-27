"""Optional authenticated size preflight for the local Qoder protocol adaptor.

The independent gateway owns upstream serialization and limits. Core context
management consumes only the generic budget result, never gateway implementation.
"""

from dataclasses import dataclass
from urllib.parse import urlsplit

from corki.models.base import ModelError, ModelErrorKind
from corki.models.http_errors import http_error


@dataclass(frozen=True)
class RequestByteBudget:
    request_bytes: int
    fixed_bytes: int
    limit_bytes: int

    @property
    def exceeded(self):
        return self.request_bytes > self.limit_bytes

    def check_fixed(self):
        if self.fixed_bytes > self.limit_bytes:
            raise ModelError(
                f"系统提示词和工具定义已超过接口请求大小限制 "
                f"({self.fixed_bytes}/{self.limit_bytes} 字节)，无法通过压缩历史解决。",
                kind=ModelErrorKind.INVALID_REQUEST, status_code=413,
            )


async def local_gateway_budget(client, base_url, api_key, build_payload, request):
    url = urlsplit(base_url)
    if (
        url.scheme not in {"http", "https"}
        or url.hostname not in {"127.0.0.1", "localhost", "::1"}
        or url.path.rstrip("/") not in {"/qoder", "/qoder/v1"}
    ):
        return None
    response = await client.post(
        base_url.rstrip("/") + "/request-budget",
        headers={"Authorization": "Bearer " + (api_key or "")},
        json=build_payload(request),
        timeout=10.0,
    )
    if response.status_code == 404:
        raise ModelError("适配网关不支持请求大小预检，请更新并重启适配网关。", kind=ModelErrorKind.INVALID_REQUEST)
    if not response.is_success:
        raise http_error(response, response.text)
    try:
        data = response.json()
        values = [data[k] for k in ("request_bytes", "fixed_bytes", "limit_bytes")]
        if data.get("protocol_version") != 1 or any(
            isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in values
        ) or values[2] == 0 or values[1] > values[0]:
            raise ValueError()
        return RequestByteBudget(*values)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ModelError("适配网关返回了无效的请求大小信息。", kind=ModelErrorKind.PROTOCOL) from exc
