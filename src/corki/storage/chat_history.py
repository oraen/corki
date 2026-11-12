"""Independent transcript payloads; model compaction never owns these records.

Both stores accept the same original events in one SQLite transaction. There are
no references, cascades or read-time joins from transcript bodies to model items.
The ingest counter only distinguishes internal replacement writes at admission,
including older running writers. It is never used by display readers.
"""

VERSION = "chat_history_v1"

INGEST_TRIGGER = """CREATE TRIGGER chat_history_insert AFTER INSERT ON conversation_items
    BEGIN
        INSERT OR IGNORE INTO chat_history_ingest VALUES(NEW.thread_id,0);
        SELECT CASE WHEN
            (SELECT remaining FROM chat_history_ingest WHERE thread_id=NEW.thread_id)=0
            AND NEW.kind='compaction'
            AND (COALESCE(json_type(NEW.payload_json,'$.replacement_item_count'),
                          'integer')!='integer'
                 OR COALESCE(json_extract(NEW.payload_json,'$.replacement_item_count'),0)<0)
            THEN RAISE(ABORT,'invalid compaction replacement count') END;
        INSERT OR IGNORE INTO chat_history
            (id,thread_id,turn_id,sequence,kind,payload_json,created_at)
        SELECT NEW.id,NEW.thread_id,NEW.turn_id,
               (SELECT COALESCE(MAX(sequence),-1)+1
                FROM chat_history WHERE thread_id=NEW.thread_id),
               NEW.kind,NEW.payload_json,NEW.created_at
        WHERE NEW.kind NOT IN ('context','compaction')
          AND (NEW.kind!='user_message'
               OR json_extract(NEW.payload_json,'$.retained_from_id') IS NULL)
          AND ((SELECT remaining FROM chat_history_ingest WHERE thread_id=NEW.thread_id)=0
               OR NEW.kind='user_message');
        UPDATE chat_history_ingest SET remaining=CASE WHEN remaining>0 THEN remaining-1
            WHEN NEW.kind='compaction'
                THEN COALESCE(json_extract(NEW.payload_json,'$.replacement_item_count'),0)
            ELSE 0 END WHERE thread_id=NEW.thread_id;
    END"""


def visible_rows(connection, thread=None):
    """Stream legacy metadata only; never decode message/tool/image payloads."""
    query = """SELECT id,thread_id,sequence,kind,
        json_extract(payload_json,'$.replacement_item_count') AS replacements,
        json_extract(payload_json,'$.retained_from_id') AS retained
        FROM conversation_items"""
    args = ()
    if thread is not None:
        query += " WHERE thread_id=?"
        args = (str(thread),)
    query += " ORDER BY thread_id,sequence"
    previous, remaining = None, 0
    for row in connection.execute(query, args):
        identity, owner, sequence, kind, count, retained = row
        if owner != previous:
            previous, remaining = owner, 0
        visible = kind not in ("context", "compaction") and not (
            kind == "user_message" and retained is not None
        )
        if remaining:
            remaining -= 1
            visible = kind == "user_message" and retained is None
        elif kind == "compaction":
            count = 0 if count is None else count
            if type(count) is not int or count < 0:
                raise ValueError("invalid compaction replacement count in history")
            remaining = count
        yield identity, owner, sequence, visible, remaining


def _install(connection):
    if connection.execute("SELECT 1 FROM schema_migrations WHERE name=?", (VERSION,)).fetchone():
        return
    connection.execute("""CREATE TABLE chat_history (
        id TEXT NOT NULL UNIQUE,
        thread_id TEXT NOT NULL ,
        turn_id TEXT NOT NULL, sequence INTEGER NOT NULL, kind TEXT NOT NULL,
        payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(thread_id,sequence)) WITHOUT ROWID""")
    connection.execute("""CREATE TABLE chat_history_ingest (
        thread_id TEXT PRIMARY KEY ,
        remaining INTEGER NOT NULL) WITHOUT ROWID""")
    connection.execute("""CREATE TABLE chat_turns (
        id TEXT PRIMARY KEY, thread_id TEXT NOT NULL ,
        status TEXT NOT NULL,user_input TEXT NOT NULL,final_answer TEXT,error TEXT,
        updated_at TEXT NOT NULL,
        model_settings_json TEXT)""")
    connection.execute("CREATE INDEX chat_turns_thread ON chat_turns(thread_id)")
    for identity, owner, _sequence, visible, remaining in visible_rows(connection):
        if visible:
            connection.execute(
                """INSERT INTO chat_history
                SELECT id,thread_id,turn_id,sequence,kind,payload_json,created_at
                FROM conversation_items WHERE id=?""",
                (identity,),
            )
        connection.execute(
            "INSERT OR REPLACE INTO chat_history_ingest VALUES(?,?)", (owner, remaining)
        )
    connection.execute("""INSERT INTO chat_turns
        SELECT id,thread_id,status,user_input,final_answer,error,updated_at,model_settings_json
        FROM turns ORDER BY rowid""")
    connection.execute(INGEST_TRIGGER)
    connection.execute("""CREATE TRIGGER chat_history_session_delete AFTER DELETE ON threads
        BEGIN
            DELETE FROM chat_history WHERE thread_id=OLD.id;
            DELETE FROM chat_turns WHERE thread_id=OLD.id;
            DELETE FROM chat_history_ingest WHERE thread_id=OLD.id;
        END""")
    columns = "id,thread_id,status,user_input,final_answer,error,updated_at,model_settings_json"
    values = ",".join("NEW." + column for column in columns.split(","))
    for action in ("INSERT", "UPDATE"):
        # DO UPDATE preserves original display ordering; REPLACE would change rowid.
        connection.execute(f"""CREATE TRIGGER chat_turns_{action.lower()} AFTER {action} ON turns
            BEGIN INSERT INTO chat_turns({columns}) VALUES({values})
            ON CONFLICT(id) DO UPDATE SET status=excluded.status,user_input=excluded.user_input,
                final_answer=excluded.final_answer,error=excluded.error,updated_at=excluded.updated_at,
                model_settings_json=excluded.model_settings_json;
            END""")
    connection.execute("INSERT INTO schema_migrations(name) VALUES(?)", (VERSION,))


def install_chat_history(connection):
    connection.execute("SAVEPOINT chat_history_migration")
    try:
        _install(connection)
    except BaseException:
        connection.execute("ROLLBACK TO chat_history_migration")
        raise
    finally:
        connection.execute("RELEASE chat_history_migration")
