import sqlite3

from corki.storage.sqlite import SQLiteSessionRepository


def test_repository_bootstrap_does_not_scan_database_integrity(tmp_path, monkeypatch):
    statements = []
    original = sqlite3.connect

    class Connection(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.set_trace_callback(statements.append)

    def connect(*args, **kwargs):
        return original(*args, **kwargs, factory=Connection)

    monkeypatch.setattr(sqlite3, "connect", connect)
    path = tmp_path / "sessions.db"
    SQLiteSessionRepository(path)
    SQLiteSessionRepository(path)
    assert statements
    assert not any("quick_check" in s.lower() or "integrity_check" in s.lower()
                   for s in statements)
