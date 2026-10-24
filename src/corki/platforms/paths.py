"""Validate archive paths before mapping portable names onto the host filesystem."""

import os
import re
import stat
from pathlib import Path, PurePosixPath, PureWindowsPath

_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.I)


def directory_error_message(path: Path, fallback: str) -> str:
    """Explain an already rejected directory, without probing another namespace."""
    if os.name != "nt":
        return fallback
    try:
        path.stat()
    except OSError as error:
        if getattr(error, "winerror", None) == 206:
            return "目录路径超出 Windows 系统长度限制，请使用更短的目录"
        # Windows can collapse a long-path failure into ERROR_PATH_NOT_FOUND.
        # Keep that ambiguity: do not access an extended path just for a hint.
        if (
            getattr(error, "winerror", None) in {2, 3}
            and len(str(path).encode("utf-16-le", errors="surrogatepass")) >= 520
        ):
            return "Windows 无法访问此长路径，请使用更短的目录或确认目录存在"
    except ValueError:
        pass
    return fallback


def is_link_path(path: Path) -> bool:
    """Test a link or junction without following it; missing paths are not links."""
    try:
        return is_link_metadata(path.lstat())
    except FileNotFoundError:
        return False


def is_link_metadata(metadata: os.stat_result) -> bool:
    """Recognize directory junctions as links on Python 3.11 as well as 3.12+.

    Other reparse points (for example cloud files) are not directory redirects.
    Use lstat metadata; stat has already followed the redirect.
    """
    return stat.S_ISLNK(metadata.st_mode) or (
        getattr(metadata, "st_reparse_tag", 0) == 0xA0000003  # IO_REPARSE_TAG_MOUNT_POINT
    )


def archive_parts(name: str, *, windows: bool | None = None) -> tuple[str, ...]:
    if not isinstance(name, str) or "\\" in name or "\0" in name:
        raise ValueError("文件包包含无效路径")
    parts = tuple(name.split("/"))
    if (
        PurePosixPath(name).is_absolute()
        or PureWindowsPath(name).drive
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise ValueError("文件包包含不安全路径")
    if windows if windows is not None else os.name == "nt":
        for part in parts:
            if (
                part.endswith((".", " "))
                or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
                or _RESERVED.match(part)
            ):
                raise ValueError("文件包包含 Windows 不支持的文件名")
    return parts
