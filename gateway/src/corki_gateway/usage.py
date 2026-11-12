"""Durable user ownership, separate from resettable session routing."""

import sqlite3


class UserUsage:
    def __init__(self, store):
        self.store = store
        with store.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS user_usage_threads(
                    thread TEXT PRIMARY KEY, channel TEXT NOT NULL, sender TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS user_usage_owner
                    ON user_usage_threads(channel,sender,thread);
            """)
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM gateway_meta WHERE key='user-usage-v1'").fetchone():
                return
            # Receipts retain earlier thread IDs even after a route is reset.
            # Never charge a mixed historical thread to each participant in full.
            db.execute("""
                WITH candidates AS (
                    SELECT thread,channel,sender_id AS sender FROM routes
                    WHERE sender_id IS NOT NULL
                    UNION
                    SELECT p.thread,p.channel,r.sender_id FROM receipts p
                    JOIN routes r ON r.key=p.route AND r.channel=p.channel
                    WHERE r.sender_id IS NOT NULL
                    UNION
                    SELECT u.thread,c.id,u.sender FROM user_sessions u
                    JOIN connections c ON c.conversation_id=u.binding
                ), unambiguous AS (
                    SELECT thread,min(channel) AS channel,min(sender) AS sender
                    FROM candidates WHERE thread IS NOT NULL AND sender IS NOT NULL
                    GROUP BY thread HAVING count(*)=1
                )
                INSERT OR IGNORE INTO user_usage_threads
                SELECT thread,channel,sender FROM unambiguous
            """)
            db.execute("INSERT INTO gateway_meta VALUES('user-usage-v1','1')")

    def totals(self, channel, senders):
        if not senders:
            return {}
        totals = dict.fromkeys(senders, 0)
        database = self.store.home / "sessions/corki.db"
        if not database.exists():
            return totals
        try:
            with self.store.db() as db:
                db.execute("ATTACH DATABASE ? AS usage_core", (database.as_uri() + "?mode=ro",))
                rows = db.execute(
                    "SELECT u.sender,coalesce(sum(s.input_tokens+s.output_tokens),0) AS tokens "
                    "FROM usage_core.usage_threads u CROSS JOIN usage_core.usage_events s "
                    "ON s.thread_id=u.thread_id "
                    "WHERE u.channel=? AND u.sender IN (" + ",".join("?" for _ in senders) + ") "
                    "GROUP BY u.sender",
                    (channel, *senders),
                )
                totals.update((r["sender"], r["tokens"]) for r in rows)
        except sqlite3.Error:
            # An unavailable database is not zero consumption.
            return dict.fromkeys(senders, None)
        return totals
