"""Read-only usage facts, independent from recoverable model submissions."""

from corki.protocol.ids import ItemId
from corki.sessions.models import ContextUsage


def model_usage_fact(row):
    from corki.storage.sqlite import _completed_from_row

    completed = _completed_from_row(row)
    anchor = row["usage_anchor_id"]
    if anchor is None and completed.items:
        anchor = str(completed.items[-1].id)
    return (completed.usage.context_tokens, anchor, completed.usage.input_tokens, row["step_id"])


def context_usage(fact):
    if fact is None or fact[0] is None or fact[1] is None:
        return None
    return ContextUsage(fact[0], ItemId(fact[1]), input_tokens=fact[2], sample_id=fact[3])


def read_usage_facts(connection, source):
    # Older inherited observations precede this source's own model submissions.
    facts = [
        tuple(row)
        for row in connection.execute(
            "SELECT total_tokens,anchor_id,input_tokens,sample_id FROM inherited_context_usage "
            "WHERE thread_id=? ORDER BY ordinal",
            (str(source),),
        )
    ]
    facts.extend(
        model_usage_fact(row)
        for row in connection.execute(
            "SELECT * FROM model_steps WHERE thread_id=? ORDER BY rowid",
            (str(source),),
        )
    )
    return tuple(facts)


def inherit_usage(connection, source, target, selected, identity, *, complete, facts=None):
    if facts is None:
        facts = read_usage_facts(connection, source)
    retained = {str(item.id) for item in selected}
    for ordinal, (total, anchor, inputs, sample) in enumerate(facts):
        if anchor not in retained and not (anchor is None and complete):
            continue
        connection.execute(
            "INSERT INTO inherited_context_usage "
            "(thread_id,ordinal,total_tokens,anchor_id,input_tokens,sample_id) "
            "VALUES (?,?,?,?,?,?)",
            (
                str(target),
                ordinal,
                total,
                identity("item", anchor) if anchor is not None else None,
                inputs,
                identity("step", sample) if sample is not None else None,
            ),
        )
