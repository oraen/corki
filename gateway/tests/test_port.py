import errno
import importlib
import socket
from http.server import BaseHTTPRequestHandler

import pytest

module = importlib.import_module("corki_gateway.control")


def test_preferred_port_is_used_when_available():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        preferred = probe.getsockname()[1]
    server = module.bind_server(BaseHTTPRequestHandler, preferred)
    try:
        assert server.server_address == ("127.0.0.1", preferred)
    finally:
        server.server_close()


def test_busy_port_falls_back_without_disturbing_owner():
    with socket.socket() as owner:
        owner.bind(("127.0.0.1", 0))
        owner.listen()
        preferred = owner.getsockname()[1]
        server = module.bind_server(BaseHTTPRequestHandler, preferred)
        try:
            assert server.server_port != preferred and server.server_port > 0
            assert owner.getsockname()[1] == preferred
        finally:
            server.server_close()


def test_default_is_17003_and_unrelated_errors_are_not_hidden(monkeypatch):
    addresses = []

    def denied(address, handler):
        addresses.append(address)
        raise PermissionError(errno.EACCES, "fixture")

    monkeypatch.setattr(module, "ThreadingHTTPServer", denied)
    with pytest.raises(PermissionError):
        module.bind_server(BaseHTTPRequestHandler)
    assert addresses == [("127.0.0.1", 17003)]
