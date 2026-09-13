"""Recoverable local JSONL projection; SQLite remains the canonical authority."""

import json
import os
import stat
from hashlib import sha256


def transcript_name(thread_id):
    return sha256(str(thread_id).encode()).hexdigest() + ".jsonl"


def _lines(connection, thread_id):
    thread = connection.execute(
        "SELECT id, session_id, created_at FROM threads WHERE id=?", (str(thread_id),)
    ).fetchone()
    if thread is None:
        raise ValueError("transcript thread does not exist")
    yield (
        json.dumps(
            {
                "type": "session_meta",
                "version": 1,
                "thread_id": thread["id"],
                "session_id": thread["session_id"],
                "timestamp": thread["created_at"],
            },
            ensure_ascii=False,
        )
        + "\n"
    ).encode()
    for row in connection.execute(
        "SELECT sequence, kind, payload_json, created_at FROM conversation_items "
        "WHERE thread_id=? ORDER BY sequence",
        (str(thread_id),),
    ):
        # Preserve the stored JSON verbatim, including exact numeric spelling.
        prefix = json.dumps(
            {"type": row["kind"], "sequence": row["sequence"], "timestamp": row["created_at"]},
            ensure_ascii=False,
        )
        yield (prefix[:-1] + ', "payload": ' + row["payload_json"] + "}\n").encode()


def flush_transcript(repository, thread_id):
    """Append only a verified canonical suffix, including recovery of a partial line.

    The database write lock serializes writers across repository instances and
    processes. Existing complete or partial bytes must match canonical records;
    unrelated content is never truncated or overwritten. A reader holding the
    file open continues to observe new appended records on the same inode.
    """
    if os.name != "posix":
        raise OSError("secure local transcript publication requires POSIX")
    root = repository.path.with_name(repository.path.name + ".transcripts").absolute()
    name = transcript_name(thread_id)
    with repository._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if (
            connection.execute("SELECT 1 FROM threads WHERE id=?", (str(thread_id),)).fetchone()
            is None
        ):
            raise ValueError("transcript thread does not exist")
        root.mkdir(mode=0o700, exist_ok=True)
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(directory)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise OSError("transcript directory is not private")
            descriptor = os.open(
                name,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
                dir_fd=directory,
            )
            try:
                info = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) & 0o077
                ):
                    raise OSError("transcript file is not a private regular file")
                with os.fdopen(descriptor, "r+b", closefd=False) as output:
                    for expected in _lines(connection, thread_id):
                        actual = output.read(len(expected))
                        if not expected.startswith(actual):
                            raise OSError("transcript contents differ from canonical history")
                        if len(actual) != len(expected):
                            output.write(expected[len(actual) :])
                    if output.read(1):
                        raise OSError("transcript contains noncanonical trailing data")
                    output.flush()
                    os.fsync(descriptor)
                # Do not return a path which was swapped out while writing.
                current = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                    raise OSError("transcript path changed during publication")
                current_root = root.stat(follow_symlinks=False)
                directory_info = os.fstat(directory)
                if (current_root.st_dev, current_root.st_ino) != (
                    directory_info.st_dev,
                    directory_info.st_ino,
                ):
                    raise OSError("transcript directory changed during publication")
                os.fsync(directory)
                connection.execute(
                    "DELETE FROM transcript_pending WHERE thread_id=?", (str(thread_id),)
                )
            finally:
                os.close(descriptor)
        finally:
            os.close(directory)
    return root / name
