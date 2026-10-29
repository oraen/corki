"""Connection model overrides are validated without resetting conversation identity."""
import json

import pytest
from corki_gateway.store import Store


def test_model_selection_validation_and_retention(tmp_path):
    profile = {"models": {"small": {}, "large": {}}}
    (tmp_path / "settings.json").write_text(json.dumps({
        "default": {"provider": "first", "model": "small"},
        "providers": {"first": profile, "off": {**profile, "enabled": False}},
    }))
    store = Store(tmp_path)
    identity = store.save_connection({"channel": "wechat", "name": "Fixture", "values": {}})
    binding = store.connection(identity)["conversation_id"]
    store.save_connection({"id": identity, "name": "Fixture", "values": {
        "agent_provider": "first", "agent_model": "large",
    }})
    assert store.connection(identity)["conversation_id"] == binding
    assert Store(tmp_path).connection(identity)["config"]["agent_model"] == "large"
    for provider, model in [("off", "small"), ("missing", "small"), ("first", "missing"),
                            ("", "small"), ("first", "")]:
        with pytest.raises(ValueError):
            store.save_connection({"id": identity, "name": "Fixture", "values": {
                "agent_provider": provider, "agent_model": model,
            }})
        assert store.connection(identity)["config"]["agent_model"] == "large"
        assert store.connection(identity)["conversation_id"] == binding
    store.save_connection({"id": identity, "name": "Fixture", "values": {
        "agent_provider": "", "agent_model": "",
    }})
    assert store.connection(identity)["conversation_id"] == binding
