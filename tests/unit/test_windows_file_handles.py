import os
import subprocess

import pytest

from corki.platforms.windows_file_handles import WindowsFiles, nt_path


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("C:\\用户\\a b", "\\??\\C:\\用户\\a b"),
        ("C:/a/b", "\\??\\C:\\a\\b"),
        ("\\\\?\\C:\\long", "\\??\\C:\\long"),
        ("\\\\server\\share\\file", "\\??\\UNC\\server\\share\\file"),
        ("\\\\?\\UNC\\server\\share\\file", "\\??\\UNC\\server\\share\\file"),
    ],
)
def test_nt_disk_and_unc_paths(source, expected):
    assert nt_path(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        "C:relative",
        "\\rooted",
        "relative",
        "C:\\a\\..\\b",
        "C:\\a\0b",
        "\\\\.\\PhysicalDrive0",
        "\\\\?\\GLOBALROOT\\Device\\HarddiskVolume1\\x",
        pytest.param("C:\\" + "x" * 40000, id="overlong-nt-path"),
    ],
)
def test_nt_path_rejects_ambiguous_and_device_paths(source):
    with pytest.raises(ValueError):
        nt_path(source)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows NT file API")
def test_nt_relative_file_and_handle_deletion(tmp_path):
    import msvcrt

    api = WindowsFiles()
    parent = api.open(tmp_path, access=0x80, directory=True)
    try:
        handle = api.open("记录.txt", root=parent, access=0xC0000000, disposition=2)
        fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        with os.fdopen(fd, "w+b") as stream:
            stream.write("你好".encode())
            stream.flush()
            assert api.kernel.GetFileType(handle) == 1
        assert (tmp_path / "记录.txt").read_text(encoding="utf-8") == "你好"
        handle = api.open("记录.txt", root=parent, access=0x10000)
        try:
            api.delete(handle)
        finally:
            api.close(handle)
        assert not (tmp_path / "记录.txt").exists()
    finally:
        api.close(parent)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows NT file API")
def test_nt_open_checks_entry_type_and_exclusive_creation(tmp_path):
    api = WindowsFiles()
    entry = tmp_path / "existing.txt"
    entry.write_text("preserve", encoding="utf-8")
    with pytest.raises(OSError):
        api.open(entry, access=0x80, directory=True)
    with pytest.raises(OSError):
        api.open(tmp_path, access=0x80)
    with pytest.raises(OSError):
        api.open(entry, access=0xC0000000, disposition=2)
    assert entry.read_text(encoding="utf-8") == "preserve"


@pytest.mark.skipif(os.name != "nt", reason="requires Windows NT file API")
def test_nt_relative_open_rejects_escape_from_pinned_parent(tmp_path):
    api = WindowsFiles()
    parent = api.open(tmp_path, access=0x80, directory=True)
    try:
        for name in ("..", "../outside", "..\\outside", "C:\\outside", "x:stream"):
            with pytest.raises(ValueError):
                api.open(name, root=parent, access=0xC0000000, disposition=2)
        assert list(tmp_path.iterdir()) == []
    finally:
        api.close(parent)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows NT volume handles")
@pytest.mark.parametrize("verbatim", [False, True])
def test_nt_open_plain_volume_root(tmp_path, verbatim):
    api = WindowsFiles()
    root = tmp_path.anchor
    handle = api.open("\\\\?\\" + root if verbatim else root, access=0x80, directory=True)
    api.close(handle)


@pytest.mark.skipif(os.name != "nt", reason="requires real Windows directory junctions")
@pytest.mark.parametrize("ancestor", [False, True])
@pytest.mark.parametrize("verbatim", [False, True])
def test_nt_volume_retry_does_not_follow_junctions(tmp_path, ancestor, verbatim):
    outside = tmp_path / "outside"
    outside.mkdir()
    retained = outside / "keep.txt"
    retained.write_text("preserved", encoding="utf-8")
    link = tmp_path / "linked"
    subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(outside)],
        capture_output=True, check=True, timeout=30,
    )
    try:
        selected = link / "keep.txt" if ancestor else link
        name = "\\\\?\\" + str(selected) if verbatim else selected
        with pytest.raises(OSError):
            WindowsFiles().open(name, access=0x80, directory=not ancestor)
        assert retained.read_text(encoding="utf-8") == "preserved"
    finally:
        link.rmdir()
