"""Real Windows ACL checks, before and after configuration publication."""

import os

import pytest

from corki.config.paths import CorkiPaths
from corki.config.toml_edits import write_text

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires real Windows file ACLs")


def verify_private(path):
    from corki.platforms.windows_acl import WindowsPrivateACL
    from corki.platforms.windows_file_handles import WindowsFiles

    files = WindowsFiles()
    handle = files.open(path, access=0x20000)
    try:
        WindowsPrivateACL().verify(handle)
    finally:
        files.close(handle)


def test_temporary_file_is_private_before_content_is_written(tmp_path):
    import msvcrt

    from corki.platforms.windows_acl import WindowsPrivateACL
    from corki.platforms.windows_private_files import private_temporary_file

    descriptor, name = private_temporary_file(tmp_path, prefix=".fixture-")
    try:
        assert os.fstat(descriptor).st_size == 0
        WindowsPrivateACL().verify(msvcrt.get_osfhandle(descriptor))
        os.write(descriptor, "仅测试内容".encode())
    finally:
        os.close(descriptor)
    os.unlink(name)  # No leaked handle may prevent deletion.


def test_initial_config_and_atomic_replacement_keep_private_acl(tmp_path):
    paths = CorkiPaths.from_home(tmp_path / "配置 空间")
    paths.ensure_exists()
    verify_private(paths.config_file)
    write_text(paths.config_file, '{"description":"中文配置"}\n')
    assert paths.config_file.read_text(encoding="utf-8") == '{"description":"中文配置"}\n'
    verify_private(paths.config_file)
    assert not list(paths.home.glob(".corki-config-*"))


def inspect_security(target):
    import ctypes as C

    from corki.platforms.windows_acl import WindowsPrivateACL
    from corki.platforms.windows_file_handles import WindowsFiles

    files, security = WindowsFiles(), WindowsPrivateACL()
    handle = files.open(target, access=0x20000)
    owner, acl, descriptor = C.c_void_p(), C.c_void_p(), C.c_void_p()
    try:
        error = security.api.GetSecurityInfo(
            handle, 1, 1 | 4, C.byref(owner), None,
            C.byref(acl), None, C.byref(descriptor),
        )
        if error:
            raise C.WinError(error)
        assert acl.value
        entries = []
        for index in range(C.c_uint16.from_address(acl.value + 4).value):
            ace = C.c_void_p()
            assert security.api.GetAce(acl, index, C.byref(ace))
            entries.append((C.c_ubyte.from_address(ace.value).value,
                            security._sid(ace.value + 8)))
        return security._sid(owner), entries
    finally:
        if descriptor:
            security.kernel.LocalFree(descriptor)
        files.close(handle)


def test_existing_config_symlink_keeps_its_target(tmp_path):
    from corki.platforms.windows_acl import WindowsPrivateACL
    from corki.platforms.windows_private_files import restrict_file

    target = tmp_path / "实际配置.json"
    target.write_text("{}\n", encoding="utf-8")
    link = tmp_path / "settings.json"
    try:
        link.symlink_to(target)
    except OSError as error:
        if error.winerror == 1314:
            pytest.skip("Windows account cannot create symbolic links")
        raise
    security = WindowsPrivateACL()
    original_owner, _ = inspect_security(target)
    restrict_file(link)
    assert link.is_symlink() and target.read_text(encoding="utf-8") == "{}\n"
    owner, entries = inspect_security(target)
    assert owner == original_owner  # Tighten access without taking ownership.
    assert set(entries) == {(0, security.user_sid), (0, "S-1-5-18")}


def test_config_creation_under_host_selected_directory_symlink(tmp_path):
    target = tmp_path / "真实目录"
    target.mkdir()
    link = tmp_path / "配置位置"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        if error.winerror == 1314:
            pytest.skip("Windows account cannot create symbolic links")
        raise
    write_text(link / "settings.json", "{}\n")
    assert link.is_symlink()
    assert (target / "settings.json").read_text(encoding="utf-8") == "{}\n"
    verify_private(target / "settings.json")


def test_legacy_backup_retains_content_and_private_access(tmp_path):
    from corki.platforms.windows_acl import WindowsPrivateACL

    paths = CorkiPaths.from_home(tmp_path / "旧版 配置")
    paths.home.mkdir()
    legacy = paths.home / "config.toml"
    contents = 'name = "迁移测试"\n'
    legacy.write_text(contents, encoding="utf-8")
    owner_before, _ = inspect_security(legacy)
    paths.ensure_exists()
    assert legacy.read_text(encoding="utf-8") == contents
    owner_after, entries = inspect_security(legacy)
    assert owner_after == owner_before
    assert set(entries) == {(0, WindowsPrivateACL().user_sid), (0, "S-1-5-18")}
    verify_private(paths.config_file)
