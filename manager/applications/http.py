"""Same-origin management HTTP boundary, without streaming request ownership."""

import json
import sqlite3

from .models import AppError


def post(handler):
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        if not 0 < length <= 2 * 1024 * 1024:
            raise AppError("应用请求为空或过大")
        data = json.loads(handler.rfile.read(length))
        if not isinstance(data, dict):
            raise AppError("应用请求格式无效")
        return handler.respond(200, handler.server.store.applications.mutate(data))
    except AppError as error:
        return handler.respond(error.status, {"error": str(error)})
    except (TypeError, ValueError, KeyError):
        return handler.respond(400, {"error": "应用请求参数无效"})
    except (OSError, sqlite3.Error):
        return handler.respond(500, {"error": "应用数据读写失败，请稍后重试"})
