"""Standalone gateway tests with synthetic credentials and upstream."""

import json
import threading
from contextlib import contextmanager

import httpx
import pytest

from corki_openai_gateway.gateway import UPSTREAM, Gateway, main


def test_service_cli_reads_environment_without_auth_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "missing-home"))
    monkeypatch.setenv("GATEWAY_TEST_TOKEN", "provided-token")
    monkeypatch.setenv("GATEWAY_TEST_ACCOUNT", "provided-account")
    key = tmp_path / "local.key"
    key.write_text("local-key")
    seen = []

    def serve(server):
        credentials = server.credentials()
        seen.append((credentials.access_token, credentials.account_id, server.key))

    monkeypatch.setattr(Gateway, "serve_forever", serve)
    assert (
        main(
            [
                "--key-file",
                str(key),
                "--port",
                "0",
                "--access-token-env",
                "GATEWAY_TEST_TOKEN",
                "--account-id-env",
                "GATEWAY_TEST_ACCOUNT",
            ]
        )
        == 0
    )
    assert seen == [("provided-token", "provided-account", "local-key")]


def test_missing_environment_credentials_have_safe_startup_error(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GATEWAY_TEST_TOKEN", raising=False)
    monkeypatch.setenv("GATEWAY_TEST_ACCOUNT", "private-account")
    key = tmp_path / "local.key"
    key.write_text("local-key")
    assert (
        main(
            [
                "--key-file",
                str(key),
                "--port",
                "0",
                "--access-token-env",
                "GATEWAY_TEST_TOKEN",
                "--account-id-env",
                "GATEWAY_TEST_ACCOUNT",
            ]
        )
        == 1
    )
    assert "private-account" not in capsys.readouterr().err


def write_auth(path, token="oauth-secret", account="account-a"):
    path.write_text(
        json.dumps(
            {"auth_mode": "chatgpt", "tokens": {"access_token": token, "account_id": account}}
        )
    )


@contextmanager
def running(tmp_path, handler, *, credential_provider=None):
    auth = tmp_path / "auth.json"
    write_auth(auth)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    server = Gateway(auth, "local-secret", client=client, credential_provider=credential_provider)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_port}",
            trust_env=False,
            headers={"Authorization": "Bearer local-secret"},
        ) as downstream:
            yield server, downstream, auth
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        client.close()


def body():
    return {
        "model": "gpt-6-astra",
        "stream": True,
        "store": True,
        "instructions": "fixture",
        "reasoning": {"effort": "medium"},
        "input": [{"type": "function_call_output", "call_id": "c1", "output": "ok"}],
        "tools": [{"type": "function", "name": "probe", "parameters": {"type": "object"}}],
        "include": ["reasoning.encrypted_content"],
    }


def sse():
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text=('data: {"type":"response.completed","response":{"id":"r1","output":[]}}\n\n'),
    )


def test_gateway_replaces_auth_and_preserves_responses_payload(tmp_path):
    requests = []

    def upstream(request):
        requests.append(request)
        assert str(request.url) == UPSTREAM
        assert request.headers["Authorization"] == "Bearer oauth-secret"
        assert request.headers["ChatGPT-Account-ID"] == "account-a"
        expected = body()
        expected["store"] = False
        assert json.loads(request.content) == expected
        assert "local-secret" not in str(request.headers)
        return sse()

    with running(tmp_path, upstream) as (_, client, auth):
        original = auth.read_bytes()
        result = client.post("/openai/v1/responses", json=body())
        assert result.status_code == 200
        assert "response.completed" in result.text
        assert auth.read_bytes() == original
    assert len(requests) == 1


@pytest.mark.parametrize(
    "variant,status",
    [
        ("unauthorized", 401),
        ("path", 404),
        ("stream", 400),
        ("model", 400),
        ("json", 400),
    ],
)
def test_rejected_requests_never_reach_upstream(tmp_path, variant, status):
    def forbidden(request):
        pytest.fail("unexpected upstream request")

    with running(tmp_path, forbidden) as (_, client, _):
        payload = body()
        if variant == "stream":
            payload["stream"] = "false"
        if variant == "model":
            payload["model"] = ""
        result = client.post(
            "/unsupported" if variant == "path" else "/openai/v1/responses",
            headers={"Authorization": "Bearer wrong"} if variant == "unauthorized" else {},
            content=b"invalid" if variant == "json" else json.dumps(payload).encode(),
        )
        assert result.status_code == status


def test_reload_rotated_token_once_on_401_without_refreshing(tmp_path):
    requests = []

    def upstream(request):
        requests.append(request)
        if len(requests) == 1:
            write_auth(tmp_path / "auth.json", token="new-oauth-secret")
            return httpx.Response(401)
        assert request.headers["Authorization"] == "Bearer new-oauth-secret"
        return sse()

    with running(tmp_path, upstream) as (_, client, _):
        assert client.post("/openai/v1/responses", json=body()).status_code == 200
    assert len(requests) == 2


def test_account_change_does_not_forward_new_account(tmp_path):
    def forbidden(request):
        pytest.fail("account change must not reach upstream")

    with running(tmp_path, forbidden) as (_, client, auth):
        write_auth(auth, account="account-b")
        result = client.post("/openai/v1/responses", json=body())
        assert result.status_code == 401
        assert "account changed" in result.text


@pytest.mark.parametrize("status", [401, 403, 429, 302, 500])
def test_errors_are_bounded_and_do_not_leak_upstream_secrets(tmp_path, status):
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(
            status, text="oauth-secret account-a", headers={"location": "https://untrusted.invalid"}
        )

    with running(tmp_path, upstream) as (_, client, _):
        result = client.post("/openai/v1/responses", json=body())
        assert result.status_code == (502 if status == 302 else status)
        assert "oauth-secret" not in result.text
        assert "account-a" not in result.text
        assert len(calls) == 1


def test_reload_credentials_between_requests(tmp_path):
    seen = []

    def upstream(request):
        seen.append(request.headers["Authorization"])
        return sse()

    with running(tmp_path, upstream) as (_, client, auth):
        assert client.post("/openai/v1/responses", json=body()).status_code == 200
        write_auth(auth, token="renewed-token")
        assert client.post("/openai/v1/responses", json=body()).status_code == 200
    assert seen == ["Bearer oauth-secret", "Bearer renewed-token"]


def test_environment_provider_rotates_token_and_fails_closed(tmp_path, monkeypatch):
    from corki_openai_gateway.gateway import environment_credentials

    monkeypatch.setenv("GATEWAY_TEST_TOKEN", "first-token")
    monkeypatch.setenv("GATEWAY_TEST_ACCOUNT", "account-a")
    seen = []

    def upstream(request):
        seen.append(request.headers["Authorization"])
        assert request.headers["ChatGPT-Account-ID"] == "account-a"
        return sse()

    def provider():
        return environment_credentials("GATEWAY_TEST_TOKEN", "GATEWAY_TEST_ACCOUNT")

    with running(tmp_path, upstream, credential_provider=provider) as (_, client, _):
        assert client.post("/openai/responses", json=body()).status_code == 200
        monkeypatch.setenv("GATEWAY_TEST_TOKEN", "second-token")
        assert client.post("/openai/responses", json=body()).status_code == 200
        monkeypatch.delenv("GATEWAY_TEST_TOKEN")
        # A valid auth.json exists, but an explicitly selected provider never falls back.
        assert client.post("/openai/responses", json=body()).status_code == 401
        monkeypatch.setenv("GATEWAY_TEST_TOKEN", "third-token")
        monkeypatch.setenv("GATEWAY_TEST_ACCOUNT", "account-b")
        result = client.post("/openai/responses", json=body())
        assert result.status_code == 401
        assert "account changed" in result.text
    assert seen == ["Bearer first-token", "Bearer second-token"]


def test_chat_conversion_preserves_images_and_structured_output():
    from corki_openai_gateway.chat import to_responses

    payload = to_responses(
        {
            "messages": [
                {"role": "system", "content": "instruction"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "look"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
                    ],
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "answer", "strict": True, "schema": {"type": "object"}},
            },
        }
    )
    assert payload["instructions"] == "instruction"
    assert payload["input"][0]["content"][1]["type"] == "input_image"
    assert payload["text"]["format"]["strict"] is True


@pytest.mark.parametrize("ending", ["response.failed", "response.incomplete", "eof"])
def test_chat_failed_stream_never_emits_done_or_success(ending):
    from corki_openai_gateway.chat import chat_events

    lines = ['data: {"type":"response.output_text.delta","delta":"partial"}']
    if ending != "eof":
        lines.append("data: " + json.dumps({"type": ending}))
    packets = []
    with pytest.raises(ValueError):
        for packet in chat_events(lines):
            packets.append(packet)
    assert not any(
        b"[DONE]" in packet or b'"finish_reason": "stop"' in packet for packet in packets
    )


def test_system_text_parts_preserve_order_and_late_instruction_position():
    from corki_openai_gateway.chat import to_responses

    result = to_responses(
        {
            "messages": [
                {"role": "system", "content": "base"},
                {
                    "role": "system",
                    "content": [
                        {"type": "text", "text": "policy one"},
                        {"type": "text", "text": "policy two"},
                    ],
                },
                {"role": "user", "content": "hello"},
                {"role": "system", "content": [{"type": "text", "text": "late policy"}]},
            ]
        }
    )
    assert result["instructions"] == "base\n\npolicy one\n\npolicy two"
    assert result["input"] == [
        {"role": "user", "content": [{"type": "input_text", "text": "hello"}]},
        {"role": "developer", "content": [{"type": "input_text", "text": "late policy"}]},
    ]


@pytest.mark.parametrize(
    "part",
    [
        None,
        "text",
        {"type": "text", "text": 42},
        {"type": "image_url", "image_url": {"url": "invalid"}},
    ],
)
def test_system_nontext_parts_still_rejected(part):
    from corki_openai_gateway.chat import to_responses

    with pytest.raises(ValueError, match="only text parts"):
        to_responses({"messages": [{"role": "system", "content": [part]}]})


def test_chat_conversion_forwards_model_effort_and_reports_actual_model():
    from corki_openai_gateway.chat import chat_events, to_responses

    request = to_responses(
        {
            "model": "chosen-model",
            "reasoning_effort": "high",
            "messages": [{"role": "user", "content": "hello"}],
        }
    )
    assert request["model"] == "chosen-model"
    assert request["reasoning"] == {"effort": "high"}
    packets = list(
        chat_events(
            [
                "data: "
                + json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "response-fixture",
                            "model": "chosen-model-version",
                            "output": [],
                        },
                    }
                )
            ],
            request["model"],
        )
    )
    payload = json.loads(packets[0].decode()[6:])
    assert payload["model"] == "chosen-model-version"
    absent = to_responses(
        {"model": "another-model", "messages": [{"role": "user", "content": "hello"}]}
    )
    assert "reasoning" not in absent


def test_gateway_default_client_uses_verified_system_trust(tmp_path, monkeypatch):
    import ssl

    import truststore

    auth = tmp_path / "auth.json"
    write_auth(auth)
    seen = []
    real_client = httpx.Client

    def capture(**kwargs):
        seen.append(kwargs)
        return real_client(transport=httpx.MockTransport(lambda request: sse()), **kwargs)

    monkeypatch.setattr("corki_openai_gateway.gateway.httpx.Client", capture)
    server = Gateway(auth, "local-secret")
    try:
        context = seen[0]["verify"]
        assert isinstance(context, truststore.SSLContext)
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        assert seen[0]["follow_redirects"] is False
    finally:
        server.server_close()


@pytest.mark.parametrize(
    "endpoint",
    [
        "/openai/chat/completions",
        "/openai/v1/chat/completions",
        "/openai/responses",
        "/openai/v1/responses",
    ],
)
@pytest.mark.parametrize("stream", [False, None])
def test_nonstreaming_response_and_default(tmp_path, endpoint, stream):
    response = {
        "id": "resp-fixture",
        "model": "gpt-6-astra",
        "created_at": 1234,
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "你好"}]}],
        "usage": {"input_tokens": 7, "output_tokens": 2, "total_tokens": 9},
    }

    def upstream(request):
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=(
                'data: {"type":"response.output_text.delta","delta":"你好"}\n\n'
                + "data: "
                + json.dumps({"type": "response.completed", "response": response})
                + "\n\n"
            ),
        )

    chat = endpoint.endswith("completions")
    payload = (
        {"model": "gpt-6-astra", "messages": [{"role": "user", "content": "你好"}]}
        if chat
        else body()
    )
    payload.pop("stream", None)
    if stream is not None:
        payload["stream"] = stream
    with running(tmp_path, upstream) as (_, client, _):
        result = client.post(endpoint, json=payload)
        assert result.status_code == 200
        assert result.headers["content-type"].startswith("application/json")
        assert int(result.headers["content-length"]) == len(result.content)
        data = result.json()
        if chat:
            assert data["object"] == "chat.completion"
            assert data["created"] == 1234
            assert data["choices"][0]["message"] == {"role": "assistant", "content": "你好"}
            assert data["choices"][0]["finish_reason"] == "stop"
            assert data["usage"]["total_tokens"] == 9
        else:
            assert data == response


@pytest.mark.parametrize(
    "event", ["response.failed", "response.incomplete", "error", "truncated", "malformed"]
)
def test_nonstream_failure_never_returns_partial_success(tmp_path, event):
    def upstream(request):
        tail = (
            ""
            if event == "truncated"
            else "data: "
            + ("invalid" if event == "malformed" else json.dumps({"type": event}))
            + "\n\n"
        )
        return httpx.Response(
            200, text='data: {"type":"response.output_text.delta","delta":"partial"}\n\n' + tail
        )

    with running(tmp_path, upstream) as (_, client, _):
        result = client.post(
            "/openai/v1/chat/completions",
            json={
                "model": "gpt-6-astra",
                "stream": False,
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert result.status_code == 502
        assert "error" in result.json()
        assert "partial" not in result.text


def test_nonstream_tool_calls_refusal_and_buffer_bound():
    from corki_openai_gateway.chat import chat_completion, completed_response

    response = {
        "output": [
            {
                "type": "function_call",
                "call_id": "call1",
                "name": "lookup",
                "arguments": '{"q":"test"}',
            },
            {"type": "function_call", "call_id": "call2", "name": "lookup", "arguments": "{}"},
        ]
    }
    result = chat_completion(response, "test-model")
    choice = result["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] is None
    assert len(choice["message"]["tool_calls"]) == 2
    assert choice["message"]["tool_calls"][0] == {
        "id": "call1",
        "type": "function",
        "function": {"name": "lookup", "arguments": '{"q":"test"}'},
    }
    refusal = chat_completion(
        {"output": [{"type": "message", "content": [{"type": "refusal", "refusal": "No"}]}]}, "test"
    )
    assert refusal["choices"][0]["message"]["refusal"] == "No"
    with pytest.raises(ValueError, match="buffering limit"):
        completed_response(["data: " + "a" * 100], max_bytes=20)


@pytest.mark.parametrize(
    "path,message",
    [
        ("/", "路径需要加入服务商"),
        ("/v1/responses", "路径需要加入服务商"),
        ("/responses", "路径需要加入服务商"),
        ("/claude/v1/responses", "暂时不支持的供应商"),
        ("/other/responses", "暂时不支持的供应商"),
    ],
)
def test_provider_namespace_required_and_no_upstream_call(tmp_path, path, message):
    def upstream(request):
        pytest.fail("unsupported provider must never reach upstream")

    with running(tmp_path, upstream) as (_, client, _):
        for method in ("GET", "POST"):
            response = client.request(method, path, json={"model": "fixture", "input": "fixture"})
            assert response.status_code == 404
            assert message in response.json()["error"]["message"]


def test_health_contains_no_login_material(tmp_path):
    with running(tmp_path, lambda request: pytest.fail()) as (server, client, _):
        response = client.get("/__corki_gateway/health")
        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "corki-adaptor" and data["version"] == 2
        assert data["providers"] == ["openai", "qoder"]
        assert "oauth-secret" not in response.text and "account-a" not in response.text
        assert (
            client.post("/__corki_gateway/stop", headers={"X-Corki-Control": "wrong"}).status_code
            == 403
        )
