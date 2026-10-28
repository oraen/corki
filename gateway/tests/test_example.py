"""The downloadable example must match source and actually speak the protocol."""

import asyncio
import base64
import json
import urllib.request
import zipfile
from pathlib import Path

from corki_gateway.connector import Connector
from corki_gateway.plugins import install
from corki_gateway.service import Host

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "manager/static/connector-example.zip"


def test_downloaded_example_matches_sources():
    with zipfile.ZipFile(ARCHIVE) as archive:
        for file, source in {
            "connector.json": ROOT / "gateway/examples/local-webhook/connector.json",
            "main.py": ROOT / "gateway/examples/local-webhook/main.py",
            "README.md": ROOT / "gateway/examples/README.md",
        }.items():
            assert archive.read("local-webhook/" + file) == source.read_bytes()


def test_downloaded_example_runs_and_delivers(tmp_path):
    async def scenario():
        host = Host(tmp_path, online=False)
        identity = install(host.store, base64.b64encode(ARCHIVE.read_bytes()).decode())
        account = host.store.save_connection(
            {
                "channel": identity,
                "name": "Sample",
                "values": {"token": "fixture-secret", "port": ""},
            }
        )
        received = []

        async def inbound(connection, message):
            received.append((connection, message))
            return {"accepted": True, "thread": "fixture"}

        host.inbound = inbound
        connector = Connector(host, host.store.connection(account))

        def request(port, body=None):
            raw = json.dumps(body).encode() if body else None
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/",
                data=raw,
                headers={
                    "Authorization": "Bearer fixture-secret",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=3) as response:
                return json.load(response)

        try:
            await asyncio.wait_for(connector.start(), 5)
            directory = tmp_path / "gateway/accounts" / account
            port = json.loads((directory / "listen.json").read_text())["port"]
            message = {
                "id": "one",
                "sender": "person",
                "conversation": "dm",
                "kind": "direct",
                "text": "Hello",
            }
            assert await asyncio.to_thread(request, port, message) == {"pending": True}
            for _ in range(100):
                if received:
                    break
                await asyncio.sleep(0.01)
            assert received == [(account, message)]
            assert await connector.call("send", {"message": message, "text": "Reply"}) == {
                "sent": True
            }
            replies = await asyncio.to_thread(request, port)
            assert any(r.get("text") == "Reply" for r in replies)
        finally:
            await connector.close()
        assert connector.process.returncode is not None

    asyncio.run(scenario())
