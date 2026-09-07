import pytest

from corki.config import CorkiSettings
from corki.context.usage import BodyPrefixWindow
from corki.protocol.ids import ItemId
from corki.sessions.models import ContextUsage


def test_estimate_replaced_by_first_server_input_then_frozen_until_reset():
    window = BodyPrefixWindow()

    def measure(usage=None, *, local=100, key=None):
        return window.measure(
            window_id=key,
            estimated_prefill=100,
            local_tokens=local,
            active_tokens=usage.total_tokens if usage else local,
            usage=usage,
        )

    assert measure() == (100, 0)
    first = ContextUsage(170, ItemId("same-anchor"), input_tokens=120, sample_id="one")
    assert measure(first) == (170, 50)
    second = ContextUsage(180, ItemId("same-anchor"), input_tokens=130, sample_id="two")
    assert measure(second) == (180, 60)
    assert window.prefill_tokens == 120 and window.server_observed
    # A replacement makes old usage stale, even when its reported total is larger.
    assert measure(second, key="replacement", local=110) == (110, 10)
    assert not window.server_observed
    third = ContextUsage(350, ItemId("new-anchor"), input_tokens=300, sample_id="three")
    assert measure(third, key="replacement") == (350, 50)


def test_missing_usage_uses_fixed_estimated_prefix_not_a_growing_prefix():
    window = BodyPrefixWindow()
    assert window.measure(
        window_id=None, estimated_prefill=100, local_tokens=100, active_tokens=100, usage=None
    ) == (100, 0)
    assert window.measure(
        window_id=None, estimated_prefill=900, local_tokens=250, active_tokens=250, usage=None
    ) == (250, 150)


@pytest.mark.parametrize("scope", ["total", "body_after_prefix"])
def test_scope_configuration_loads_actual_toml(tmp_path, scope):
    config = tmp_path / "config.toml"
    config.write_text(f'[agent]\nauto_compact_token_limit_scope = "{scope}"\n')
    assert (
        CorkiSettings.for_directory(tmp_path, config_file=config).auto_compact_token_limit_scope
        == scope
    )
    assert CorkiSettings(working_directory=tmp_path).auto_compact_token_limit_scope == "total"


@pytest.mark.parametrize("invalid", [None, "body", True, 0, []])
def test_invalid_scope_is_rejected(tmp_path, invalid):
    with pytest.raises(ValueError, match="auto_compact_token_limit_scope"):
        CorkiSettings(working_directory=tmp_path, auto_compact_token_limit_scope=invalid)
