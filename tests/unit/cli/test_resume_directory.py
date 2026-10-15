import asyncio
import importlib
from types import SimpleNamespace

import pytest
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from corki.cli.resume_directory import choose_resume_directory


@pytest.mark.parametrize(("key", "mode"), [("3", "session"), ("4", "current")])
def test_remember_directory_preserves_config_and_skips_future_prompt(tmp_path, key, mode):
    from corki.cli.resume_directory import read_resume_mode

    saved = tmp_path / "saved"
    saved.mkdir()
    config = tmp_path / "config.toml"
    config.write_text('# keep comment\nmodel = "local-model"\n[tui]\nnotifications = false\n')
    expected = saved if mode == "session" else tmp_path
    with create_pipe_input() as pipe:
        pipe.send_text(key)
        assert (
            asyncio.run(
                choose_resume_directory(
                    tmp_path, saved, config_file=config, input=pipe, output=DummyOutput()
                )
            )
            == expected
        )
    assert read_resume_mode(config) == mode
    assert '# keep comment\nmodel = "local-model"' in config.read_text()
    assert "notifications = false" in config.read_text()
    with create_pipe_input() as pipe:

        async def again():
            async with asyncio.timeout(1):
                return await choose_resume_directory(
                    tmp_path, saved, config_file=config, input=pipe, output=DummyOutput()
                )

        assert asyncio.run(again()) == expected


def test_preference_write_failure_keeps_current_selection(tmp_path, monkeypatch, capsys):
    from corki.cli import resume_directory

    saved = tmp_path / "saved"
    saved.mkdir()

    def fail(*args):
        raise OSError("private data")

    monkeypatch.setattr(resume_directory, "set_config_value", fail)
    with create_pipe_input() as pipe:
        pipe.send_text("3")
        assert (
            asyncio.run(
                choose_resume_directory(
                    tmp_path,
                    saved,
                    config_file=tmp_path / "config.toml",
                    input=pipe,
                    output=DummyOutput(),
                )
            )
            == saved
        )
    assert capsys.readouterr().err == "Failed to save working directory preference.\n"


@pytest.mark.parametrize("key", ["1", "2", "\x03", "\x04"])
def test_non_remember_selection_never_creates_config(tmp_path, key):
    saved = tmp_path / "saved"
    saved.mkdir()
    config = tmp_path / "config.toml"
    with create_pipe_input() as pipe:
        pipe.send_text(key)
        asyncio.run(
            choose_resume_directory(
                tmp_path, saved, config_file=config, input=pipe, output=DummyOutput()
            )
        )
    assert not config.exists()


@pytest.mark.parametrize("content", ['[tui]\nresume_cwd="invalid"', 'tui="invalid"', "[broken"])
def test_invalid_preference_is_not_silently_overwritten(tmp_path, content):
    config = tmp_path / "config.toml"
    config.write_text(content)
    with pytest.raises(ValueError):
        asyncio.run(choose_resume_directory(tmp_path, tmp_path, config_file=config))
    assert config.read_text() == content


def test_unavailable_remembered_directory_is_not_used(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[tui]\nresume_cwd="session"')
    with pytest.raises(ValueError, match="Remembered working directory is unavailable"):
        asyncio.run(choose_resume_directory(tmp_path, tmp_path / "gone", config_file=config))


@pytest.mark.parametrize(
    ("keys", "choice"),
    [
        ("\r", "saved"),
        ("\x1b", "saved"),
        ("1", "saved"),
        ("\x1b[B\r", "current"),
        ("2", "current"),
        ("\x03", None),
        ("\x04", None),
    ],
)
def test_directory_choice_and_exit(tmp_path, keys, choice):
    current, saved = tmp_path / "current", tmp_path / "中文 saved"
    current.mkdir()
    saved.mkdir()
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        result = asyncio.run(
            choose_resume_directory(current, saved, input=pipe, output=DummyOutput())
        )
    assert result == {"saved": saved, "current": current, None: None}[choice]


def test_missing_directory_does_not_accept_enter(tmp_path):
    async def scenario(pipe):
        task = asyncio.create_task(
            choose_resume_directory(tmp_path, tmp_path / "gone", input=pipe, output=DummyOutput())
        )
        try:
            pipe.send_text("\r")
            await asyncio.sleep(0.1)
            assert not task.done()
            pipe.send_text("2")
            async with asyncio.timeout(3):
                assert await task == tmp_path
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))


def test_constructor_passes_selected_directory_to_configuration_ui_and_runtime(
    tmp_path, monkeypatch
):
    from corki.config import CorkiPaths

    main = importlib.import_module("corki.cli.main")

    selected = tmp_path / "project"
    selected.mkdir()
    seen = {}
    paths = CorkiPaths.from_home(tmp_path / "home")
    monkeypatch.setattr(main.CorkiPaths, "discover", lambda: paths)
    monkeypatch.setattr(main, "load_mcp_requirements", lambda: None)

    def settings(directory, **kwargs):
        seen["config"] = directory
        return SimpleNamespace(working_directory=directory)

    def ui(settings, history):
        seen["ui"] = settings.working_directory
        return SimpleNamespace(_settings=settings)

    def runtime(**kwargs):
        seen["runtime"] = kwargs["settings"].working_directory
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(main.CorkiSettings, "for_directory", settings)
    monkeypatch.setattr(main, "TerminalUI", ui)
    monkeypatch.setattr(main.LangGraphRuntime, "create", runtime)
    monkeypatch.setattr(main, "CorkiApplication", lambda *args: SimpleNamespace(runtime=args[2]))
    application = main.build_application(working_directory=selected)
    try:
        assert seen == {"config": selected, "ui": selected, "runtime": selected}
    finally:
        asyncio.run(application.runtime.repository.close())
