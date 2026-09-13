"""File authority parsing, pinning, shared readers and owned cancellation."""

import asyncio
import json
import threading
from dataclasses import replace

import pytest

from corki.mcp.oauth_file import FileOAuthAuthority, _read
from corki.storage.file_lock import open_lock, try_lock, unlock_and_close


def _entry(**changes):
    return {
        "server_name": "fixture",
        "server_url": "https://fixture.invalid/mcp",
        "client_id": "synthetic",
        "access_token": "synthetic-access",
        **changes,
    }


@pytest.mark.parametrize("invalid", [None, [], {"entry": {}}, {"entry": _entry(expires_at=True)}])
def test_initial_invalid_store_warns_but_pinned_store_does_not_fallback(tmp_path, invalid):
    async def scenario():
        path = tmp_path / ".credentials.json"
        path.write_text(json.dumps(invalid))
        authority = FileOAuthAuthority(tmp_path)
        assert await authority.load("fixture", "https://fixture.invalid/mcp") is None
        assert not authority.pinned
        path.write_text(json.dumps({"entry": _entry()}))
        token = await authority.load("fixture", "https://fixture.invalid/mcp")
        assert token is not None and token.usable()
        assert authority.pinned
        assert "synthetic-access" not in repr(token)
        path.write_text(json.dumps(invalid))
        with pytest.raises(ValueError):
            await authority.load("fixture", "https://fixture.invalid/mcp")
        path.unlink()
        assert await authority.load("fixture", "https://fixture.invalid/mcp") is None
        assert authority.pinned, "deletion must not unpin the logical client's source"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "changes",
    [{"executor_owned": True}, {"server_url": "https://other.invalid"}, {"server_name": "other"}],
)
def test_file_identity_never_consumes_other_owner(tmp_path, changes):
    (tmp_path / ".credentials.json").write_text(json.dumps({"entry": _entry(**changes)}))
    assert _read(tmp_path, "fixture", "https://fixture.invalid/mcp") is None


def test_readers_share_the_same_aggregate_lock(tmp_path):
    (tmp_path / ".credentials.json").write_text(json.dumps({"entry": _entry()}))
    directory = tmp_path / "mcp-oauth-locks"
    directory.mkdir()
    held = open_lock(directory / "file-store.lock")
    try:
        assert try_lock(held, shared=True)
        assert _read(tmp_path, "fixture", "https://fixture.invalid/mcp") is not None
    finally:
        unlock_and_close(held)


def test_cancel_joins_reader_and_does_not_pin_late_result(tmp_path, monkeypatch):
    async def scenario():
        entered = asyncio.Event()
        release = threading.Event()
        finished = threading.Event()
        loop = asyncio.get_running_loop()

        def held_read(*args):
            loop.call_soon_threadsafe(entered.set)
            try:
                assert release.wait(5)
                raise ValueError("late read failure")
            finally:
                finished.set()

        monkeypatch.setattr("corki.mcp.oauth_file._read", held_read)
        authority = FileOAuthAuthority(tmp_path)
        task = asyncio.create_task(authority.load("fixture", "https://fixture.invalid"))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done(), "caller abandoned its filesystem worker"
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert finished.is_set()
        assert not authority.pinned

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["none", "replace", "changed", "corrupt"])
def test_atomic_refresh_save_preserves_unrelated_metadata(tmp_path, monkeypatch, failure):
    async def scenario():
        path = tmp_path / ".credentials.json"
        original = {
            "entry": _entry(future_metadata={"opaque": [1, "retained"]}),
            "other": _entry(server_name="other"),
        }
        path.write_text(json.dumps(original))
        authority = FileOAuthAuthority(tmp_path)
        previous = await authority.load("fixture", "https://fixture.invalid/mcp")
        updated = replace(
            previous, access_token="rotated", refresh_token="next", expires_at=9999999999999
        )
        if failure == "replace":

            def fail(*args):
                raise OSError("synthetic rename failure")

            monkeypatch.setattr("corki.mcp.oauth_file.os.replace", fail)
        elif failure == "changed":
            original["entry"]["access_token"] = "concurrent-winner"
            path.write_text(json.dumps(original))
        elif failure == "corrupt":
            original["other"]["expires_at"] = True
            path.write_text(json.dumps(original))
        before = path.read_bytes()
        if failure != "none":
            with pytest.raises((OSError, RuntimeError, ValueError)):
                await authority.save(previous, updated, ["new-scope"])
            assert path.read_bytes() == before
        else:
            await authority.save(previous, updated, ["new-scope"])
            assert json.loads(path.read_text()) == {
                **original,
                "entry": {
                    **original["entry"],
                    "access_token": "rotated",
                    "refresh_token": "next",
                    "expires_at": 9999999999999,
                    "scopes": ["new-scope"],
                },
            }
        assert not list(tmp_path.glob(".mcp-credentials-*"))

    asyncio.run(scenario())
