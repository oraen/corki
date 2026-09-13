"""Keep URL-like tokens intact while wrapping surrounding plan prose."""

import re

from rich.cells import cell_len
from rich.text import Text


def is_url_token(raw):
    token = raw.strip("()[]{}<>,.;:!'\"")
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://.+", token):
        return True
    host_port = re.split(r"[/?#]", token, maxsplit=1)[0]
    if len(host_port) == len(token) and not host_port.lower().startswith("www."):
        return False
    host = host_port
    if ":" in host_port:
        host, port = host_port.rsplit(":", 1)
        if not re.fullmatch(r"[0-9]{1,5}", port) or int(port) > 65535:
            return False
    if host.lower() == "localhost":
        return True
    parts = host.split(".")
    if len(parts) == 4 and all(re.fullmatch(r"[+]?[0-9]+", p) for p in parts):
        # Bound conversion without rejecting the leading zeroes accepted by u8 parsing.
        octets = [p.removeprefix("+").lstrip("0") or "0" for p in parts]
        return all(len(p) <= 3 and int(p) <= 255 for p in octets)
    if len(parts) < 2 or not re.fullmatch(r"[A-Za-z]{2,63}", parts[-1]):
        return False
    return all(
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", p) for p in parts[:-1]
    )


def wrap_plan_text(text, console, width):
    width = max(1, width)
    for paragraph in text.plain.split("\n"):
        tokens = re.findall(r"[^ \t\r\f\v]+|[ \t\r\f\v]+", paragraph)
        if not any(is_url_token(token) for token in tokens if not token.isspace()):
            yield from Text(paragraph, style=text.style).wrap(console, width)
            continue
        current, space = "", ""
        first_word = True
        for token in tokens:
            if token.isspace():
                space = token.expandtabs(8)
                continue
            if first_word and space:
                # Source indentation is content, unlike a separator discarded
                # when a later word moves to a new row.
                if is_url_token(token):
                    current = space + token
                else:
                    chunks = Text(space + token, style=text.style).wrap(console, width)
                    yield from chunks[:-1]
                    current = chunks[-1].plain
                first_word, space = False, ""
                continue
            first_word = False
            separator = space if current else ""
            if current and cell_len(current + separator + token) > width:
                yield Text(current, style=text.style)
                current, separator = "", ""
            if not is_url_token(token) and cell_len(token) > width:
                chunks = Text(token, style=text.style).wrap(console, width)
                yield from chunks[:-1]
                current = chunks[-1].plain
            else:
                current += separator + token
            space = ""
        if current:
            yield Text(current, style=text.style)
