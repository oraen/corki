"""Codex Responses and compatible Chat Completions forwarding."""
import hmac
import json

import httpx

from corki_adaptor.common import MAX_BODY, CredentialsUnavailable, certificate_failure

from .chat import chat_completion, chat_events, completed_response, to_responses

UPSTREAM = "https://chatgpt.com/backend-api/codex/responses"

def forward(handler, endpoint):
    self = handler
    self.close_connection = True
    supplied = self.headers.get("Authorization", "")
    if not hmac.compare_digest(supplied.encode(), ("Bearer " + self.server.key).encode()):
        return self.error(401, "Invalid local gateway key.")
    chat = endpoint in {"/chat/completions", "/v1/chat/completions"}
    if not chat and endpoint not in {"/responses", "/v1/responses"}:
        return self.error(404, "Unsupported gateway endpoint.")
    lengths = self.headers.get_all("Content-Length", [])
    if self.headers.get("Transfer-Encoding") or len(lengths) != 1:
        return self.error(400, "A single Content-Length is required.")
    try:
        length = int(lengths[0])
    except ValueError:
        return self.error(400, "Invalid Content-Length.")
    if not 0 < length <= MAX_BODY:
        return self.error(413, "Request body exceeds gateway limits.")
    try:
        raw = self.rfile.read(length)
        if len(raw) != length:
            return self.error(400, "Incomplete request body.")
        payload = json.loads(raw)
    except (ValueError, UnicodeError):
        return self.error(400, "Invalid JSON request.")
    if not isinstance(payload, dict) or not isinstance(payload.get("stream", False), bool):
        return self.error(400, "stream must be a boolean.")
    streaming = payload.get("stream", False)
    if chat:
        try:
            payload = to_responses(payload)
        except (ValueError, TypeError, KeyError, AttributeError):
            return self.error(400, "Unsupported Chat Completions request.")
    if not isinstance(payload.get("model"), str) or not payload["model"].strip():
        return self.error(400, "A nonempty model name is required.")
    # Native Codex core/client.rs sets store=false and stream=true. Preserve
    # instructions, tool definitions/results, reasoning and encrypted items.
    payload["store"] = False
    payload["stream"] = True
    started = False
    try:
        credentials = self.server.credentials()
        for attempt in range(2):
            headers = {
                "Authorization": "Bearer " + credentials.access_token,
                "ChatGPT-Account-ID": credentials.account_id,
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            }
            with self.server.client.stream(
                "POST", UPSTREAM, json=payload, headers=headers
            ) as r:
                if r.status_code == 401 and attempt == 0:
                    refreshed = self.server.credentials()
                    if refreshed.access_token != credentials.access_token:
                        credentials = refreshed
                        continue
                if r.status_code != 200:
                    message = (
                        "Codex authentication expired or was rejected. Update the Codex login "
                        "and retry; the gateway does not rotate Codex refresh tokens."
                        if r.status_code == 401
                        else f"Codex upstream returned HTTP {r.status_code}."
                    )
                    status = r.status_code if 400 <= r.status_code < 600 else 502
                    return self.error(status, message)
                content_type = r.headers.get("content-type")
                # Some network relays omit Content-Type while preserving
                # valid SSE. Codex consumes the body as events; let the
                # downstream parser require response.completed as usual.
                if content_type and not content_type.startswith("text/event-stream"):
                    return self.error(502, "Codex upstream did not return an event stream.")
                if not streaming:
                    response = completed_response(r.iter_lines())
                    result = chat_completion(response, payload["model"]) if chat else response
                    body = json.dumps(result, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    started = True
                    self.wfile.write(body)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                started = True
                chunks = (
                    chat_events(r.iter_lines(), payload["model"]) if chat else r.iter_bytes()
                )
                for chunk in chunks:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                return
    except CredentialsUnavailable as exc:
        if not started:
            self.error(401, str(exc))
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
        if not started:
            if certificate_failure(exc):
                return self.error(
                    424,
                    "Codex upstream certificate verification failed. "
                    "Check the system trust store; retrying will not fix this.",
                )
            self.error(
                502,
                "Cannot reach Codex upstream; check network connectivity."
                if isinstance(exc, httpx.HTTPError)
                else "Codex upstream returned an invalid or incomplete response.",
                code="upstream_connection_failed"
                if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)) else None,
            )
        # After headers, close the incomplete SSE stream. Never synthesize
        # response.completed or retry a request that may have executed.
