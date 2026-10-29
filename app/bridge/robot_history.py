"""Read sender nicknames for desktop history without changing Agent messages."""

import sqlite3
from contextlib import closing


def sender_titles(home, threads):
    threads = tuple(threads)
    database = home / "gateway/gateway.db"
    if not threads or not database.is_file():
        return {}
    names = {}
    placeholders = ",".join("?" for _ in threads)
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            # User sessions carry the current nickname; routes also cover shared
            # sessions, where the most recently seen sender supplies the default.
            for query in (
                "SELECT thread,name FROM user_sessions WHERE thread IN (" + placeholders + ")",
                "SELECT thread,sender_name FROM routes WHERE thread IN (" + placeholders
                + ") ORDER BY last_seen DESC",
                "SELECT c.thread,r.sender_name FROM conversations c JOIN routes r "
                "ON r.binding_id=c.id WHERE c.thread IN (" + placeholders
                + ") ORDER BY r.last_seen DESC",
            ):
                try:
                    for thread, name in db.execute(query, threads):
                        if isinstance(name, str) and name.strip():
                            names.setdefault(thread, name.strip()[:80])
                except sqlite3.Error:
                    # Older or partially initialized gateway stores may lack a table.
                    continue
    except sqlite3.Error:
        pass
    return names
