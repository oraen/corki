"""Follow typed child provenance without inspecting titles or message contents."""


def thread_family(db, root):
    found, pending, seen = [], [root], set()
    while pending:
        parent = pending.pop()
        if parent in seen:
            continue
        seen.add(parent)
        found.append(parent)
        pending.extend(
            row[0]
            for row in db.execute(
                "SELECT id FROM threads WHERE json_valid(source) AND "
                "json_extract(source,'$.subagent.thread_spawn.parent_thread_id')=?",
                (parent,),
            )
        )
    return found
