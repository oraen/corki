import ctypes as C
import os

import pytest

from corki.platforms.windows_acl import WindowsPrivateACL
from corki.platforms.windows_file_handles import WindowsFiles

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires real Windows ACLs")


def test_private_descriptor_and_existing_acl_validation(tmp_path):
    acl, files = WindowsPrivateACL(), WindowsFiles()
    with acl.descriptor() as descriptor:
        handle = files.open(
            tmp_path / "private", access=0x20000 | 0x80,
            disposition=2, directory=True, security_descriptor=descriptor,
        )
    try:
        acl.verify(handle)
        with acl.descriptor() as descriptor:
            child = files.open(
                "中文.txt", root=handle, access=0x20000 | 0xC0000000,
                disposition=2, security_descriptor=descriptor,
            )
        try:
            acl.verify(child)
        finally:
            files.close(child)
    finally:
        files.close(handle)
    from corki.platforms.windows_transcripts import delete_transcript

    delete_transcript(tmp_path / "private", "中文.txt")
    assert not (tmp_path / "private" / "中文.txt").exists()
    delete_transcript(tmp_path / "private", "中文.txt")  # Retry is harmless.


def test_broad_existing_acl_rejected_without_rewriting(tmp_path):
    acl, files = WindowsPrivateACL(), WindowsFiles()
    descriptor = C.c_void_p()
    text = f"O:{acl.user_sid}D:P(A;;FA;;;{acl.user_sid})(A;;GR;;;WD)"
    assert acl.api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        text, 1, C.byref(descriptor), None
    )
    try:
        handle = files.open(
            tmp_path / "public.txt", access=0x20000 | 0x80,
            disposition=2, security_descriptor=descriptor.value,
        )
    finally:
        acl.kernel.LocalFree(descriptor)
    try:
        for _ in range(2):
            with pytest.raises(OSError, match="another principal"):
                acl.verify(handle)
    finally:
        files.close(handle)


def test_private_transcript_regular_file_and_identity(tmp_path):
    from corki.platforms.windows_transcripts import private_transcript

    root = tmp_path / "transcripts"
    with private_transcript(root, "message.jsonl") as (fd, verify):
        os.write(fd, "你好\n".encode())
        os.fsync(fd)
        verify()
    assert (root / "message.jsonl").read_text(encoding="utf-8") == "你好\n"
    os.link(root / "message.jsonl", root / "second.jsonl")
    with (
        pytest.raises(OSError, match="private regular"),
        private_transcript(root, "message.jsonl"),
    ):
        pytest.fail("hardlinked transcript was accepted")
