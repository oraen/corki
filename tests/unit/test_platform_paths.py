import pytest

from corki.platforms.paths import archive_parts


@pytest.mark.parametrize("name", ["C:escape", "C:/escape", "../escape", "/escape", "x/../y",
                                  r"x\y", "x//y", "x/\0y"])
@pytest.mark.parametrize("windows", [True, False])
def test_archive_paths_cannot_escape_on_either_host(name, windows):
    with pytest.raises(ValueError):
        archive_parts(name, windows=windows)


@pytest.mark.parametrize("name", ["x/NUL.txt", "x/COM1", "x/LPT².log", "x/file:stream",
                                  "x/ends.", "x/ends ", "x/a?b", "x/a\tb"])
def test_windows_rejects_devices_aliases_and_alternate_streams(name):
    with pytest.raises(ValueError, match="Windows"):
        archive_parts(name, windows=True)


@pytest.mark.parametrize("windows", [True, False])
def test_hidden_config_and_unicode_remain_supported(windows):
    assert archive_parts("中文 skill/.config/settings.json", windows=windows) == (
        "中文 skill", ".config", "settings.json"
    )
