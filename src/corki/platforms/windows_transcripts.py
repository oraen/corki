"""Windows-specific secure access to the local transcript projection."""

import os
import stat
from contextlib import contextmanager, suppress

from corki.platforms.windows_acl import WindowsPrivateACL
from corki.platforms.windows_file_handles import WindowsFiles


def delete_transcript(root, name):
    """Remove only a validated entry under a pinned, private directory handle."""
    files, acl = WindowsFiles(), WindowsPrivateACL()
    parent = files.open(root, access=0x20000 | 0x80, directory=True)
    try:
        acl.verify(parent)
        with suppress(FileNotFoundError):
            child = files.open(name, root=parent, access=0x20000 | 0x10000)
            try:
                acl.verify(child)
                files.delete(child)
            finally:
                files.close(child)
    finally:
        files.close(parent)


@contextmanager
def private_transcript(root, name):
    """Yield a private regular file descriptor and a path-identity verifier.

    Creation supplies a protected ACL; existing entries must already be private.
    NT relative opens pin the parent and refuse reparse points at every level.
    """
    import msvcrt

    files, acl = WindowsFiles(), WindowsPrivateACL()
    parent_fd = child_fd = None
    parent = child = None
    try:
        with acl.descriptor() as descriptor:
            parent = files.open(
                root, access=0x20000 | 0x80, disposition=3,
                directory=True, security_descriptor=descriptor,
            )
        acl.verify(parent)
        parent_fd = msvcrt.open_osfhandle(parent, os.O_RDONLY | os.O_BINARY)
        parent = None  # The CRT descriptor now owns this handle.
        parent_handle = msvcrt.get_osfhandle(parent_fd)
        with acl.descriptor() as descriptor:
            child = files.open(
                name, root=parent_handle, access=0x20000 | 0xC0000000,
                disposition=3, security_descriptor=descriptor,
            )
        acl.verify(child)
        if files.kernel.GetFileType(child) != 1:
            raise OSError("transcript file is not a disk file")
        child_fd = msvcrt.open_osfhandle(child, os.O_RDWR | os.O_BINARY)
        child = None
        info = os.fstat(child_fd)
        directory_info = os.fstat(parent_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError("transcript file is not a private regular file")

        def verify():
            # The caller has flushed the data before these checks. The projection
            # is recoverable from SQLite if a crash loses directory metadata.
            current = (root / name).stat(follow_symlinks=False)
            current_root = root.stat(follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise OSError("transcript path changed during publication")
            if (current_root.st_dev, current_root.st_ino) != (
                directory_info.st_dev, directory_info.st_ino
            ):
                raise OSError("transcript directory changed during publication")
            acl.verify(parent_handle)
            acl.verify(msvcrt.get_osfhandle(child_fd))

        yield child_fd, verify
    finally:
        try:
            if child_fd is not None:
                os.close(child_fd)
            elif child is not None:
                files.close(child)
        finally:
            if parent_fd is not None:
                os.close(parent_fd)
            elif parent is not None:
                files.close(parent)
