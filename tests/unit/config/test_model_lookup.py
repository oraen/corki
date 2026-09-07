from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.protocol.context import ModelContextInfo


@pytest.mark.parametrize(
    "requested,expected",
    [
        ("model", 1000),
        ("model-v2", 2000),
        ("model-v2-preview", 2000),
        ("model-v2anything", 2000),
        ("custom/model-v2", 2000),
        ("openai-codex/model-v2", 2000),
        ("provider_2/model-v2-preview", 2000),
        ("P123/model", 1000),
        ("provider.test/model-v2", 272000),
        ("/model-v2", 272000),
        ("a/b/model-v2", 272000),
        ("中文/model-v2", 272000),
        ("vendor space/model-v2", 272000),
        ("ｐ/model-v2", 272000),
        ("MODEL-v2", 272000),
        ("model-other", 1000),
    ],
)
def test_longest_prefix_and_scoped_namespace_matching(tmp_path, requested, expected):
    catalog = (ModelContextInfo("model", 1000), ModelContextInfo("model-v2", 2000))
    settings = CorkiSettings(working_directory=tmp_path, model="main", model_contexts=catalog)
    result = settings.model_context_info(requested)
    assert result.resolved_context_window == expected
    assert result.model == requested
    assert settings.model_contexts == catalog


def test_full_name_match_takes_priority_over_more_specific_suffix(tmp_path):
    settings = CorkiSettings(
        working_directory=tmp_path,
        model_contexts=(
            ModelContextInfo("vendor", 3000),
            ModelContextInfo("model-v2", 1000),
        ),
    )
    assert settings.model_context_info("vendor/model-v2-preview").context_window == 3000


def test_explicit_multisegment_catalog_entry_is_not_rejected_as_namespace(tmp_path):
    settings = CorkiSettings(
        working_directory=tmp_path, model_contexts=(ModelContextInfo("one/two/model", 3000),)
    )
    assert settings.model_context_info("one/two/model-preview").context_window == 3000


def test_matched_maximum_applies_to_override_without_mutating_catalog(tmp_path):
    info = ModelContextInfo("model", 1000, 5000, 80)
    settings = CorkiSettings(
        working_directory=tmp_path, model_contexts=(info,), model_context_window_override=20000
    )
    resolved = settings.model_context_info("vendor/model-preview")
    assert resolved == replace(info, model="vendor/model-preview", context_window=5000)
    assert settings.model_contexts == (info,)


def test_equal_length_matches_keep_first_candidate():
    from corki.config.model_context import match_model_context

    first, second = ModelContextInfo("model", 1000), ModelContextInfo("model", 2000)
    assert match_model_context("model-v2", (first, second)) == replace(first, model="model-v2")
