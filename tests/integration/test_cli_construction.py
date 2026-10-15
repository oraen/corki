"""The console entry point owns construction cleanup before returning status."""

import asyncio
import importlib
import json

import pytest

from corki.cli.application import CorkiApplication
from corki.config import CorkiSettings
from corki.core import runtime as runtime_module
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted


@pytest.mark.parametrize(
    "outcome", ["success", "error", "io_error", "cancel", "application_error", "run_error"]
)
def test_cli_main_awaits_construction_and_rollback(tmp_path, monkeypatch, capsys, outcome):
    cli = importlib.import_module("corki.cli.main")
    home = tmp_path / "home"
    root = home / "plugins/fixture"
    manifest = root / ".codex-plugin/plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"name": "fixture", "entrypoint": "plugin.py:register"}))
    (root / "plugin.py").write_text(
        "import asyncio\nfrom pathlib import Path\n"
        "def register(api):\n    pass\n"
        "async def aclose():\n"
        "    await asyncio.sleep(0)\n"
        "    with Path(__file__).with_name('closed').open('a') as f: f.write('close\\n')\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CORKI_HOME", str(home))
    monkeypatch.setattr(
        cli.CorkiSettings,
        "for_directory",
        lambda *a, **kw: CorkiSettings(
            tmp_path,
            skills_enabled=False,
        ),
    )
    monkeypatch.setattr(cli, "TerminalUI", lambda *a: None)
    loops, closed, requests = [], [], []

    class Model:
        async def stream(self, request):
            loops.append(asyncio.get_running_loop())
            requests.append(request)
            yield ModelCompleted(())

        async def aclose(self):
            closed.append("model")

    def create_model(*args):
        if outcome == "error":
            raise ValueError("fixture model construction failed")
        if outcome == "io_error":
            raise OSError("PRIVATE_STORAGE_DETAIL")
        if outcome == "cancel":
            raise asyncio.CancelledError
        loops.append(asyncio.get_running_loop())
        return Model()

    async def run(app):
        try:
            if outcome == "run_error":
                raise ValueError("fixture running application failed")
            events = [event async for event in app._runtime.stream("hello")]
            assert isinstance(events[-1], TurnCompleted)
            return 0
        finally:
            await app._runtime.aclose()

    monkeypatch.setattr(runtime_module, "_create_model", create_model)
    monkeypatch.setattr(CorkiApplication, "run", run)
    if outcome == "application_error":

        def reject_application(*args):
            raise ValueError("fixture application construction failed")

        monkeypatch.setattr(cli, "CorkiApplication", reject_application)
    if outcome in {"error", "application_error"}:
        with pytest.raises(SystemExit) as failure:
            cli.main([])
        assert failure.value.code == 2
    elif outcome == "run_error":
        with pytest.raises(ValueError, match="running application failed"):
            cli.main([])
    elif outcome == "io_error":
        assert cli.main([]) == 1
        output = capsys.readouterr()
        assert "Corki could not start (OSError)" in output.err
        assert "PRIVATE_STORAGE_DETAIL" not in output.out + output.err
    else:
        assert cli.main([]) == (130 if outcome == "cancel" else 0)
    assert (root / "closed").read_text().splitlines() == ["close"]
    assert closed == (["model"] if outcome in {"success", "application_error", "run_error"} else [])
    assert len(requests) == (1 if outcome == "success" else 0)
    if loops:
        if outcome in {"application_error", "run_error"}:
            assert len(loops) == 1
        else:
            assert len(loops) == 2 and loops[0] is loops[1]
