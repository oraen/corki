"""Transport-neutral, authoritative view of an application run."""

import re

from .models import AppError
from .store import ACTIVE


def run_view(apps, app, run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise AppError("run_id 无效")
    row = apps.store.run(run_id)
    session = apps.store.session(app["id"], row["session"])
    live = apps.execution.snapshot(run_id)
    if not live and row["status"] in ACTIVE and apps.execution.loop is None:
        apps.execution.start()  # Reconcile stale runs after a process restart.
        row = apps.store.run(run_id)
        live = apps.execution.snapshot(run_id)
    if live:
        row.update(live)
    elif row["status"] not in ACTIVE and app["type"] == "agent":
        # Read the authoritative display history only once, without loading
        # executable prompts or version metadata for a status query.
        row = apps.agent_run_view(row, session)
    messages = row.get("messages") or ([{"content": row["answer"]}] if row.get("answer") else [])
    messages = [
        {"id": f"{run_id}:{i}", "content": message["content"]} for i, message in enumerate(messages)
    ]
    # No settings, prompts, API keys, raw logs or model reasoning are exposed.
    return dict(
        appid=app["public_id"],
        session_id=session["id"],
        run_id=run_id,
        status=row["status"],
        output=row.get("answer") or (messages[-1]["content"] if messages else ""),
        messages=messages,
        error=row.get("error"),
        interaction=row.get("interaction"),
        provider=row.get("provider"),
        model=row.get("model"),
        version=row.get("version"),
        created_at=row.get("created"),
        updated_at=row.get("updated"),
    )
