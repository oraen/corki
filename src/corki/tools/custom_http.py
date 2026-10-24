"""Stdlib HTTP worker, launched inside the execution sandbox of a custom tool."""

import json
import sys
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    # The parent writes UTF-8 JSON bytes. Windows pipe text streams otherwise
    # use the user's ANSI code page and silently corrupt non-ASCII arguments.
    payload = json.load(sys.stdin.buffer)
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/plain"}
    headers.update(payload["headers"])
    method = payload.get("method", "POST")
    url = sys.argv[1]
    if method == "GET":
        parts = urlsplit(url)
        query = urlencode(
            {
                key: value
                if isinstance(value, str)
                else json.dumps(value, ensure_ascii=False, allow_nan=False)
                for key, value in payload["arguments"].items()
            }
        )
        url = urlunsplit(
            parts._replace(query="&".join(part for part in (parts.query, query) if part))
        )
        body = None
    else:
        body = json.dumps(payload["arguments"], ensure_ascii=False, allow_nan=False).encode()
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with build_opener(NoRedirect).open(request, timeout=payload["timeout"]) as response:
            data = response.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise ValueError("HTTP 返回超过 1 MB 限制")
            sys.stdout.buffer.write(data)
    except HTTPError as error:
        sys.stderr.buffer.write(f"HTTP 请求失败：{error.code}\n".encode())
        sys.exit(1)
    except Exception as error:
        sys.stderr.buffer.write(f"HTTP 请求失败：{type(error).__name__}\n".encode())
        sys.exit(1)


if __name__ == "__main__":
    main()
