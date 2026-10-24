"""Removal of caller-owned trees, including Windows read-only regular files."""

import os
import shutil
import stat
from pathlib import Path

from corki.platforms.paths import is_link_metadata


def remove_owned_tree(path: Path) -> None:
    """Caller must establish ownership; never change permissions on link targets.

    Git object files use the Windows read-only attribute. Clearing that attribute
    permits deletion; ACL denial and open handles still propagate to the caller.
    """

    def onerror(function, name, error):
        exception = error[1]
        metadata = Path(name).lstat()
        if (
            os.name == "nt"
            and function is os.unlink
            and isinstance(exception, PermissionError)
            and stat.S_ISREG(metadata.st_mode)
            and not is_link_metadata(metadata)
            and getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_READONLY
        ):
            os.chmod(name, stat.S_IWRITE)
            function(name)
            return
        raise exception

    shutil.rmtree(path, onerror=onerror)
