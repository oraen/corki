"""Install built-in source documents without generating knowledge indexes."""

import os
import tempfile
import time
import uuid
from importlib.resources import files
from pathlib import Path

from corki.knowledge.store import KnowledgeStore, digest


def bundled_document():
    """Return only source text; indexes must be generated through the C&E skill."""
    return files("corki.knowledge").joinpath("builtin/corki_docs.md").read_text("utf-8")


def install_builtin_knowledge(home: Path):
    """Serialize installers with SQLite; preserve IDs and explicit disabled state.

    File replacement is atomic, DB publication is transactional. A crash between
    them makes the old index unreadable (hash check); next startup repairs it.
    Only the owned corki_docs file/row is changed, never user documents/config.
    """
    store = KnowledgeStore(home)
    content = bundled_document()
    relative = ".system/corki_docs.md"
    destination = store.root / relative
    home = Path(home).resolve()
    for path in (store.root.parent, store.root, destination.parent, destination):
        if path.is_symlink() or not path.resolve().is_relative_to(home):
            raise ValueError("Built-in knowledge destination must stay inside Corki home")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fingerprint = digest(content)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT * FROM documents WHERE path=?", (relative,)).fetchone()
        try:
            matches = destination.read_text(encoding="utf-8") == content
        except FileNotFoundError:
            matches = False
        if (
            old
            and old["hash"] == fingerprint
            and not old["model"].startswith("builtin-reviewed:")
            and matches
        ):
            return dict(old)
        if not matches:
            descriptor, temporary = tempfile.mkstemp(prefix=".corki-docs-", dir=destination.parent)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, destination)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        identity = old["id"] if old else uuid.uuid4().hex
        # Invalidate old mechanical indexes and changed sources exactly once.
        # Unchanged C&E indexes, processing jobs and explicit disabled states survive.
        db.execute(
            """INSERT INTO documents(id,path,title,hash,enabled,status,updated)
            VALUES(?,?,?, ?,0,'pending',?)
            ON CONFLICT(path) DO UPDATE SET title=excluded.title,hash=excluded.hash,
            enabled=0,status='pending',summary='',error='',job=NULL,started=NULL,
            owner_pid=0,model='',updated=excluded.updated""",
            (identity, relative, "corki_docs", fingerprint, time.time()),
        )
        db.execute("DELETE FROM segments WHERE document_id=?", (identity,))
        db.execute("DELETE FROM batches WHERE document_id=?", (identity,))
        return dict(db.execute("SELECT * FROM documents WHERE id=?", (identity,)).fetchone())
