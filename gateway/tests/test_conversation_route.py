"""Stable management URLs resolve only a single user's entry in the selected account."""
import pytest
from corki_gateway.history import history
from corki_gateway.store import Store


def test_conversation_route_is_scoped_stable_and_survives_context_reset(tmp_path):
    store = Store(tmp_path)
    identity = store.save_connection({'channel':'telegram','name':'fixture',
                                     'values':{'token':'fixture','session_validation':'true'}})
    other = store.save_connection({'channel':'telegram','name':'other',
                                  'values':{'token':'other','session_validation':'true'}})
    message = {'id':'first','sender':'alice','name':'Alice','text':'Hello','conversation':'alice'}
    store.allows_message(store.connection(identity), message)
    with store.db() as db:
        db.execute("UPDATE access_requests SET status='approved' WHERE channel=?", (identity,))
    message['id'] = 'approved-input'
    first = store.admit(identity, message, str((tmp_path/'main/default').resolve()))
    row = store.applications(identity)['requests'][0]
    conversation = row['conversation_id']
    assert history(store, identity, {'conversation_id':conversation}, {})['thread'] == first
    with pytest.raises(ValueError, match='不属于此接入'):
        history(store, other, {'conversation_id':conversation}, {})
    second = store.reset_current_session(identity, first)
    assert second != first
    assert store.applications(identity)['requests'][0]['conversation_id'] == conversation
    assert history(store, identity, {'conversation_id':conversation}, {})['thread'] == second
    for invalid in ('0', '-1', '1 OR 1=1', '9223372036854775808', '١', 1):
        with pytest.raises(ValueError, match='会话 ID 无效'):
            history(store, identity, {'conversation_id':invalid}, {})
