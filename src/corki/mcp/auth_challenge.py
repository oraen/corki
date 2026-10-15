"""Bearer insufficient_scope recognition mapped from Codex's HTTP challenge parser."""

from corki.mcp.http_headers import _HEADER_NAME, _WHITESPACE


def _parameter(segment: str):
    name, separator, value = segment.strip(_WHITESPACE).partition("=")
    name, value = name.strip(_WHITESPACE), value.strip(_WHITESPACE)
    if not separator or not _HEADER_NAME.fullmatch(name):
        return None
    if value.startswith('"'):
        if not value.endswith('"') or len(value) < 2:
            return name, None
        decoded, escaped = [], False
        for char in value[1:-1]:
            if escaped:
                decoded.append(char)
                escaped = False
            elif char == "\\":
                escaped = True
            else:
                decoded.append(char)
        return name, None if escaped else "".join(decoded)
    return name, value if _HEADER_NAME.fullmatch(value) else None


def insufficient_scope(header: str) -> bool:
    segments, start, quoted, escaped = [], 0, False, False
    for index, char in enumerate(header):
        if escaped:
            escaped = False
        elif char == "\\" and quoted:
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif char in ",;" and not quoted:
            segments.append(header[start:index])
            start = index + 1
    if quoted or escaped:
        return False
    segments.append(header[start:])
    bearer, errors = False, []
    for segment in segments:
        parameter = _parameter(segment)
        if parameter is None:
            if bearer and errors == ["insufficient_scope"]:
                return True
            segment = segment.strip(_WHITESPACE)
            split = next((i for i, c in enumerate(segment) if c in _WHITESPACE), len(segment))
            scheme = segment[:split]
            if not _HEADER_NAME.fullmatch(scheme):
                return False
            bearer, errors = scheme.lower() == "bearer", []
            parameter = _parameter(segment[split:])
        if bearer and parameter is not None and parameter[0].lower() == "error":
            errors.append(parameter[1])
    return bearer and errors == ["insufficient_scope"]
