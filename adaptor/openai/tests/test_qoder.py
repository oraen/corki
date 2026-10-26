"""Synthetic credentials and HTTP; never access the user's OS credential store."""

import json
import threading
from contextlib import contextmanager

import httpx
import pytest

from corki_openai_gateway.gateway import CredentialsUnavailable, Gateway
from corki_openai_gateway.qoder import UPSTREAM, collect, events
from corki_openai_gateway.qoder_credentials import QoderCredentialReader, QoderCredentials
from corki_openai_gateway.qoder_models import MODELS


def stream_bytes(*rows, done=True):
    result = b"".join(
        b"data: " + json.dumps(row, ensure_ascii=False).encode() + b"\r\n\r\n" for row in rows
    )
    return result + (b"data: [DONE]\r\n\r\n" if done else b"")


def test_sse_preserves_unicode_line_separators_and_fragmented_utf8():
    value = {"choices": [], "metadata": "a\u0085b\u2028c\u2029中文"}
    raw = stream_bytes(value)
    assert list(events(raw[i : i + 1] for i in range(len(raw)))) == [value]


def test_model_server_folded_json_lines_remain_in_one_event():
    raw = b'data: {"id":"chatcmpl-fixture-\ncontinued","choices":[]}\n\ndata: [DONE]\n\n'
    assert list(events([raw])) == [{"id": "chatcmpl-fixture-continued", "choices": []}]
    # Never repair by consuming a new event after a genuinely truncated frame.
    with pytest.raises(ValueError):
        list(events([b'data: {"id":"bad\n\ndata: {}\n\ndata: [DONE]\n\n']))


def test_collect_joins_parallel_tool_arguments_and_reasoning():
    rows = [
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "reasoning_content": "Check",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-a",
                                "function": {"name": "lookup", "arguments": '{"city":'},
                            },
                            {
                                "index": 1,
                                "id": "call-b",
                                "function": {"name": "lookup", "arguments": '{"city":"B"}'},
                            },
                        ],
                    },
                }
            ]
        },
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": '"A"}'}},
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"total_tokens": 20},
        },
    ]
    result = collect(events([stream_bytes(*rows)]), "auto")
    message = result["choices"][0]["message"]
    assert [json.loads(c["function"]["arguments"])["city"] for c in message["tool_calls"]] == [
        "A",
        "B",
    ]
    assert message["reasoning_content"] == "Check"
    assert result["usage"]["total_tokens"] == 20


@pytest.mark.parametrize("raw", [b"data: {}\n\n", b"data: {broken}\n\n", b"data: [DONE]\n\n"])
def test_incomplete_or_invalid_stream_never_becomes_success(raw):
    with pytest.raises(ValueError):
        collect(events([raw]), "auto")


def test_explicit_credentials_are_reread_without_writing_or_keychain(tmp_path, monkeypatch):
    path = tmp_path / "credentials.json"
    original = json.dumps({"token": "fixture-a", "id": "fixture-account"})
    path.write_text(original)
    monkeypatch.delenv("QODER_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("QODER_AUTH_FILE", str(path))
    reader = QoderCredentialReader()
    assert reader().token == "fixture-a"
    assert path.read_text() == original
    path.write_text(json.dumps({"token": "fixture-b", "id": "fixture-account"}))
    assert reader().token == "fixture-b"
    path.write_text(json.dumps({"token": "fixture-c", "id": "different-account"}))
    with pytest.raises(CredentialsUnavailable):
        reader()
    path.write_text('{"token": ["not-a-token"]}')
    with pytest.raises(CredentialsUnavailable):
        reader()


@contextmanager
def gateway(tmp_path, upstream):
    auth = tmp_path / "openai.json"
    auth.write_text(json.dumps({"tokens": {"access_token": "fixture", "account_id": "fixture"}}))
    client = httpx.Client(transport=httpx.MockTransport(upstream))
    server = Gateway(
        auth,
        "fixture-key",
        client=client,
        qoder_credentials=lambda: QoderCredentials("fixture-qoder-token", "fixture"),
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_port}",
            trust_env=False,
            headers={"Authorization": "Bearer fixture-key"},
        ) as downstream:
            yield downstream
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        client.close()


@pytest.mark.parametrize("streaming", [False, True])
def test_qoder_routes_real_chat_protocol_and_validates_local_key(tmp_path, streaming):
    def upstream(request):
        assert str(request.url) == UPSTREAM
        assert request.headers["authorization"] == "Bearer fixture-qoder-token"
        body = json.loads(request.content)
        assert body["model"] == "auto" and body["stream"] is True
        assert body["messages"] == [{"role": "user", "content": "hello"}]
        assert body["metadata"]["context"]["client_type"] == "5"
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=stream_bytes(
                {
                    "id": "test",
                    "choices": [
                        {"index": 0, "delta": {"content": "你好"}, "finish_reason": "stop"}
                    ],
                }
            ),
        )

    with gateway(tmp_path, upstream) as client:
        response = client.post(
            "/qoder/v1/chat/completions",
            json={
                "model": "auto",
                "stream": streaming,
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert response.status_code == 200
        result = collect(events([response.content]), "auto") if streaming else response.json()
        assert result["choices"][0]["message"]["content"] == "你好"
        assert "fixture-qoder-token" not in response.text
        assert (
            client.post(
                "/qoder/v1/chat/completions", headers={"Authorization": "Bearer wrong"}, json={}
            ).status_code
            == 401
        )
        assert client.post("/qoder/v1/responses", json={}).status_code == 404


def test_upstream_error_does_not_leak_body_or_auth(tmp_path):
    with gateway(
        tmp_path, lambda request: httpx.Response(403, text="private-upstream-body")
    ) as client:
        response = client.post(
            "/qoder/v1/chat/completions",
            json={"model": "auto", "messages": [{"role": "user", "content": "hello"}]},
        )
        assert response.status_code == 403
        assert "private-upstream-body" not in response.text


def test_qoder_only_gateway_does_not_require_codex_login(tmp_path, monkeypatch):
    from corki_openai_gateway.gateway import main

    monkeypatch.setenv("QODER_ACCESS_TOKEN", "fixture-qoder-token")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-codex"))
    key = tmp_path / "local.key"
    key.write_text("fixture-key")
    seen = []
    assert (
        main(
            ["--key-file", str(key), "--port", "0", "--startup-provider", "qoder"],
            serve=lambda server: seen.append(server.qoder_credentials().token),
        )
        == 0
    )
    assert seen == ["fixture-qoder-token"]


def test_auto_controller_selects_qoder_without_modifying_credentials(tmp_path, monkeypatch):
    from corki_openai_gateway.control import Controller

    monkeypatch.setenv("QODER_ACCESS_TOKEN", "fixture-qoder-token")
    monkeypatch.setenv("CORKI_GATEWAY_AUTH_FILE", str(tmp_path / "missing-codex.json"))
    controller = Controller(tmp_path / "home", provider="auto")
    try:
        state = controller.start(0, auto_port=True)
        assert state["running"] and controller.startup_provider == "qoder"
        assert not (tmp_path / "missing-codex.json").exists()
    finally:
        controller.stop()


@pytest.mark.parametrize(
    "name,identity,description,window",
    MODELS,
)
def test_catalog_model_names_are_translated_to_wire_ids(
    tmp_path, name, identity, description, window
):
    def upstream(request):
        payload = json.loads(request.content)
        assert payload["model"] == identity
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=stream_bytes(
                {
                    "model": identity,
                    "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}],
                }
            ),
        )

    with gateway(tmp_path, upstream) as client:
        response = client.post(
            "/qoder/v1/chat/completions",
            json={
                "model": name,
                "stream": False,
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert response.status_code == 200
        assert response.json()["model"] == name


def test_qoder_check_exposes_static_catalog_without_credentials_in_response(
    tmp_path, monkeypatch, capsys
):
    from corki_openai_gateway.control import main

    monkeypatch.setenv("QODER_ACCESS_TOKEN", "fixture-private-token")
    assert main(["check", "--home", str(tmp_path), "--provider", "qoder"]) == 0
    raw = capsys.readouterr().out
    response = json.loads(raw)
    assert len(response["models"]) == 5
    assert "GLM-5.3" in response["models"] and "auto" in response["models"]
    assert "Qwen3.8-Max" not in response["models"]
    assert "fixture-private-token" not in raw
    assert not any(name.startswith(("quest-", "experts-")) for name in response["models"])


def test_null_keepalive_does_not_discard_real_chunks():
    row = {"choices": [{"index": 0, "delta": {"content": "hello"}, "finish_reason": "stop"}]}
    raw = b"data: null\n\n" + stream_bytes(row)
    assert list(events([raw])) == [row]


@pytest.mark.parametrize("streaming", [False, True])
def test_explicit_unsupported_model_is_reported_before_stream_headers(tmp_path, streaming):
    def upstream(request):
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=stream_bytes(
                {"code": "invalid_model_error", "message": "Unsupported model"}, done=False
            ),
        )

    with gateway(tmp_path, upstream) as client:
        response = client.post(
            "/qoder/v1/chat/completions",
            json={"model": "not-supported", "messages": [], "stream": streaming},
        )
        assert response.status_code == 400
