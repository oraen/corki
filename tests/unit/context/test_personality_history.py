"""History projection is tolerant; newly generated snapshots remain strict."""

import pytest

from corki.context.personality import render_update
from corki.context.world_state import render_context_history
from corki.protocol.ids import new_turn_id
from corki.protocol.items import ContextItem, ContextRole


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        "personality.legacy_unknown",
        "{",
        '{"model":"large"}',
        '{"model":"large","personality":"friendly","baked":"invalid"}',
    ],
)
def test_old_visible_message_without_renderable_snapshot_is_preserved(snapshot):
    item = ContextItem(
        "personality",
        ContextRole.DEVELOPER,
        "<personality_spec>retained style</personality_spec>",
        new_turn_id(),
        snapshot_state=snapshot,
    )
    assert render_context_history((item,)) == (item,)


def test_new_invalid_snapshot_is_not_silently_accepted():
    item = ContextItem(
        "personality",
        ContextRole.DEVELOPER,
        "new style",
        new_turn_id(),
        snapshot_state="{",
    )
    with pytest.raises(ValueError):
        render_update(item, None)
