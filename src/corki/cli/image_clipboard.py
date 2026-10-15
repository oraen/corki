"""Image clipboard capture in a bounded, cancellation-owned helper process."""

import base64
import io
import json
import os
import re
import shlex
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import Image, ImageGrab

from corki.execution.owned_process import run_owned
from corki.protocol.tools import ImageAttachment

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 32_000_000

FILE_URL_SCRIPT = """ObjC.import('AppKit');
var p = $.NSPasteboard.generalPasteboard;
var a = p.readObjectsForClassesOptions($.NSArray.arrayWithObject($.NSURL),
    $({NSPasteboardURLReadingFileURLsOnlyKey: true}));
var paths = [];
if (a) for (var i = 0; i < a.count; i++) paths.push(ObjC.unwrap(a.objectAtIndex(i).path));
JSON.stringify(paths);
"""


async def read_clipboard_image(cwd):
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        raise ValueError(
            "Image paste needs a local clipboard; SSH clipboard reading is unavailable."
        )
    raw = await run_owned(
        [sys.executable, "-I", "-m", "corki.cli.image_clipboard"],
        b"",
        cwd=cwd,
        output_limit=MAX_IMAGE_BYTES,
        timeout=5,
    )
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Clipboard contains no readable image.")
    return ImageAttachment("data:image/png;base64," + base64.b64encode(raw).decode("ascii"))


def normalize_pasted_path(text):
    """Codex clipboard_paste: one shell word, quoted path, or local file URL."""
    text = text.strip()
    if not text or any(char in text for char in "\x00\r\n"):
        return None
    unquoted = text[1:-1] if text[:1] in {"'", '"'} and text[-1:] == text[:1] else text
    if unquoted.startswith("file:"):
        try:
            url = urlsplit(unquoted)
        except ValueError:
            return None
        if url.netloc not in {"", "localhost"} or url.query or url.fragment:
            return None
        path = unquote(url.path)
        return Path(path) if path.startswith("/") and "\x00" not in path else None
    if re.match(r"^[A-Za-z]:[\\/]", unquoted) or unquoted.startswith("\\\\"):
        if sys.platform == "linux" and (os.getenv("WSL_DISTRO_NAME") or os.getenv("WSL_INTEROP")):
            if re.match(r"^[A-Za-z]:[\\/]", unquoted):
                return Path("/mnt/" + unquoted[0].lower() + "/" + unquoted[3:].replace("\\", "/"))
        return Path(unquoted)
    try:
        parts = shlex.split(text)
    except ValueError:
        return None
    return Path(parts[0]) if len(parts) == 1 and parts[0] else None


async def read_path_image(path, cwd):
    """Decode only an explicitly pasted file; never fall back to system clipboard."""
    raw = await run_owned(
        [sys.executable, "-I", "-m", "corki.cli.image_clipboard", "--file", str(path)],
        b"",
        cwd=cwd,
        output_limit=MAX_IMAGE_BYTES,
        timeout=5,
    )
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Not a readable image")
    return ImageAttachment("data:image/png;base64," + base64.b64encode(raw).decode("ascii"))


def _file_image(paths):
    for name in paths:
        path = Path(name)
        try:
            if not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
                continue
            with path.open("rb") as source:
                raw = source.read(MAX_IMAGE_BYTES + 1)
            if len(raw) <= MAX_IMAGE_BYTES:
                image = Image.open(io.BytesIO(raw))
                try:
                    if image.width * image.height > MAX_PIXELS:
                        raise ValueError("Clipboard image exceeds pixel limit")
                    image.load()
                except BaseException:
                    image.close()
                    raise
                return image
        except (OSError, ValueError):
            continue
    return None


def capture_png():
    # Like Codex/arboard, prefer copied files (Finder) over clipboard pixels.
    image = None
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["/usr/bin/osascript", "-l", "JavaScript", "-e", FILE_URL_SCRIPT],
                capture_output=True,
                timeout=2,
                check=True,
            )
            image = _file_image(json.loads(result.stdout))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    if image is None:
        value = ImageGrab.grabclipboard()
        image = _file_image(value) if isinstance(value, list) else value
    if image is None:
        raise ValueError("Clipboard contains no readable image")
    return encode_png(image)


def encode_png(image):
    # Pillow's image context manager closes file handles, not necessarily the
    # decoded pixel core. Explicit close also releases memory on early failure.
    with closing(image):
        if image.width * image.height > MAX_PIXELS:
            raise ValueError("Clipboard image exceeds pixel limit")
        with closing(image.convert("RGBA")) as pixels, io.BytesIO() as output:
            pixels.save(output, format="PNG")
            raw = output.getvalue()
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Clipboard image exceeds byte limit")
    return raw


if __name__ == "__main__":
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "--file":
            image = _file_image([sys.argv[2]])
            if image is None:
                raise ValueError("Not a readable image")
            raw = encode_png(image)
        elif len(sys.argv) == 1:
            raw = capture_png()
        else:
            raise ValueError("Invalid arguments")
        sys.stdout.buffer.write(raw)
    except Exception:  # worker errors become a safe UI notice, never clipboard contents
        sys.exit(1)
