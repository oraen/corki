"""Real SQLite transaction and OS handle lifetime, independent of garbage collection."""

import sqlite3

import pytest

from corki.storage.sqlite import SQLiteSessionRepository


@pytest.mark.parametrize("fail", [False, True])
def test_transaction_closes_connection_and_releases_database(tmp_path, fail):
    path = tmp_path / "会话 database.db"
    repository = SQLiteSessionRepository(path)
    with repository._connect() as connection:
        connection.execute("CREATE TABLE lifetime_probe(value TEXT)")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")

    class Abort(Exception):
        pass

    try:
        with repository._connect() as connection:
            connection.execute("INSERT INTO lifetime_probe VALUES ('transaction')")
            if fail:
                raise Abort
    except Abort:
        pass
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    with repository._connect() as reader:
        assert reader.execute("SELECT COUNT(*) FROM lifetime_probe").fetchone()[0] == (not fail)
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        reader.execute("SELECT 1")
    # Keep Python references alive: this must not depend on finalizers or gc.collect().
    # In particular, Windows fails this operation if SQLite still owns a file handle.
    moved = path.with_name("renamed.db")
    path.rename(moved)
    moved.unlink()
    assert not path.exists() and not moved.exists()
