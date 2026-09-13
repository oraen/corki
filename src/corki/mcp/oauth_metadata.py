"""Typed metadata and URL admission for the pinned RMCP OAuth discovery path."""

import ipaddress
from urllib.parse import urlsplit, urlunsplit

from corki.config.mcp_headers import RUST_WHITESPACE
from corki.config.mcp_url import http_url, join_http_url
from corki.protocol.wire_json import check_fields, loads_wire, materialize


class OAuthDiscoveryError(ValueError):
    """Discovery is unknown, not proof that the server does not support OAuth."""


def origin(url):
    parts = urlsplit(url)
    return (
        parts.scheme,
        parts.hostname,
        parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80),
    )


def with_path(url, path):
    parts = urlsplit(url)
    return http_url(urlunsplit((parts.scheme, parts.netloc, path, "", "")))


def metadata_document(body, *, authorization):
    """Malformed typed JSON means try the next candidate; unknown Value fields are retained."""
    strings = (
        ("authorization_endpoint", "token_endpoint", "registration_endpoint", "issuer", "jwks_uri")
        if authorization
        else ("resource", "authorization_server")
    )
    arrays = (
        ("scopes_supported", "response_types_supported", "code_challenge_methods_supported")
        if authorization
        else ("authorization_servers", "scopes_supported")
    )
    try:
        value = loads_wire(body)
        check_fields(value, {*strings, *arrays})
        value = materialize(value)
        for key in strings:
            if value.get(key) is not None and not isinstance(value[key], str):
                return None
        for key in arrays:
            item = value.get(key)
            if item is not None and (
                not isinstance(item, list) or not all(isinstance(v, str) for v in item)
            ):
                return None
        if authorization and any(not isinstance(value.get(k), str) for k in strings[:2]):
            return None
        return value
    except (ValueError, UnicodeError):
        return None


def resource_matches(base, resource):
    try:
        actual = http_url(resource)
    except ValueError:
        return False
    if "#" in actual or origin(base) != origin(actual):
        return False
    expected_path, actual_path = urlsplit(base).path, urlsplit(actual).path
    return expected_path == actual_path or (
        expected_path.startswith(actual_path)
        and (actual_path.endswith("/") or expected_path[len(actual_path) :].startswith("/"))
    )


def issuers_match(received, expected):
    def root_slash(value):
        if value.endswith("/") and urlsplit(value).path in ("", "/"):
            return value[:-1]
        return value

    return received == expected or root_slash(received) == root_slash(expected)


def expected_issuer(url):
    path = urlsplit(url).path
    for prefix in ("/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"):
        if path == prefix:
            return with_path(url, "/")
        if path.startswith(prefix + "/"):
            return with_path(url, "/" + path[len(prefix) + 1 :])
    suffix = "/.well-known/openid-configuration"
    if path.endswith(suffix):
        return with_path(url, path[: -len(suffix)] or "/")
    return None


def authorization_urls(base):
    path = urlsplit(base).path.strip("/")
    oauth, oidc = "/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"
    paths = (
        (oauth, oidc)
        if not path
        else (f"{oauth}/{path}", f"{oidc}/{path}", f"/{path}{oidc}", oauth)
    )
    return tuple(with_path(base, p) for p in paths)


def resource_urls(base):
    path = urlsplit(base).path.strip("/")
    canonical = "/.well-known/oauth-protected-resource"
    paths = (canonical,) if not path else (f"{canonical}/{path}", f"/{path}{canonical}", canonical)
    return tuple(with_path(base, p) for p in paths)


def _loopback(host):
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def allowed_authorization_server(base, url):
    host = (urlsplit(url).hostname or "").rstrip(".").lower()
    if not host or urlsplit(url).scheme not in {"http", "https"}:
        return False
    forbidden = host in {
        "metadata",
        "metadata.google.internal",
        "metadata.azure.internal",
    } or _loopback(host)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if isinstance(address, ipaddress.IPv4Address):
        forbidden |= any(
            address in ipaddress.ip_network(network)
            for network in (
                "0.0.0.0/8",
                "10.0.0.0/8",
                "127.0.0.0/8",
                "169.254.0.0/16",
                "172.16.0.0/12",
                "192.168.0.0/16",
                "224.0.0.0/4",
                "255.255.255.255/32",
                "100.64.0.0/10",
                "198.18.0.0/15",
            )
        )
    elif isinstance(address, ipaddress.IPv6Address):
        forbidden |= (
            address.is_unspecified
            or address.is_multicast
            or address.is_link_local
            or address in ipaddress.ip_network("fc00::/7")
        )
    return not forbidden or (
        _loopback((urlsplit(base).hostname or "").rstrip(".").lower()) and _loopback(host)
    )


def challenge_resource(header, base):
    """RMCP's parameter scan accepts quoted escapes and tries later invalid pointers."""
    lowered = header.translate(
        str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
    )
    offset, key = 0, "resource_metadata="
    while (start := lowered.find(key, offset)) >= 0:
        start += len(key)
        remaining = header[start:]
        trimmed = remaining.lstrip(RUST_WHITESPACE)
        prefix = len(remaining) - len(trimmed)
        value = ""
        if trimmed.startswith('"'):
            escaped, consumed = False, None
            for index, char in enumerate(trimmed[1:], 1):
                if escaped:
                    value += char
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    consumed = index + 1
                    break
                else:
                    value += char
            if consumed is None:
                return None
        else:
            consumed = next(
                (i for i, c in enumerate(trimmed) if c in ",;" or c in RUST_WHITESPACE),
                len(trimmed),
            )
            value = trimmed[:consumed]
        offset = start + prefix + consumed
        if not value.strip(RUST_WHITESPACE):
            continue
        try:
            candidate = join_http_url(base, value.strip(RUST_WHITESPACE))
        except ValueError:
            continue
        if origin(candidate) == origin(base):
            return candidate
    return None
