"""Old model metadata cannot re-enable the removed Lite transport."""

import pytest

from corki.config.model_context import parse_model_contexts
from corki.protocol.context import ModelContextInfo


@pytest.mark.parametrize("old", [True, False, None, 0, 1, "true", [], {}])
def test_legacy_lite_configuration_and_checkpoint_are_inert(old):
    assert not parse_model_contexts({"model": {"use_responses_lite": old}})[0].use_responses_lite
    assert not ModelContextInfo("model", use_responses_lite=old).use_responses_lite
