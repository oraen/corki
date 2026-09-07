"""Pinned bm25 2.3.2 scoring cases, isolated from tokenizer differences."""

from dataclasses import replace

import pytest

from corki.protocol.tools import ToolExposure, ToolSpec
from corki.tools import search as search_module
from corki.tools.bm25 import BM25Scorer
from corki.tools.search import ToolSearchIndex


def test_upstream_scoring_example_with_explicit_normalized_tokens():
    scorer = BM25Scorer(
        (
            ("rabbit", "munch", "orang", "carrot"),
            ("snake", "hug", "green", "lizard"),
            ("hedgehog", "impal", "orang", "orang"),
            ("squirrel", "buri", "brown", "nut"),
        )
    )
    ranked = scorer.rank(("orang",), 3)
    assert [index for index, _ in ranked] == [2, 0]
    # bm25 2.3.2 src/lib.rs Search example, not a freshly generated Python oracle.
    assert [score for _, score in ranked] == pytest.approx([0.9530774, 0.6931472], abs=1e-7)
    assert scorer.rank(("absent",), 3) == ()
    assert scorer.rank((), 3) == ()
    assert scorer.rank(("orang",), 0) == ()


def test_inverted_index_only_scores_matching_documents():
    scorer = BM25Scorer([("amber",)] + [("cobalt",)] * 1000)
    visited = []

    class Weights(list):
        def __getitem__(self, index):
            visited.append(index)
            return super().__getitem__(index)

    scorer._weights = Weights(scorer._weights)
    assert [index for index, _ in scorer.rank(("amber", "absent"), 8)] == [0]
    assert visited == [0]


def test_returned_definition_does_not_alias_the_cache():
    specs = specs_for("amber")
    index = ToolSearchIndex()
    result = index.search(specs, "amber", 1)
    result[0].parameters["description"] = "mutated externally"
    index.specs[0].parameters["description"] = "another mutation"
    assert index.search(specs, "amber", 1) == specs
    assert index.generation == 1


def specs_for(*texts):
    return tuple(
        ToolSpec(
            f"tool_{i}",
            "fixture",
            {"type": "object"},
            exposure=ToolExposure.DEFERRED,
            search_text=text,
        )
        for i, text in enumerate(texts)
    )


def test_pinned_k1_changes_top_candidate():
    specs = specs_for(
        "amber cobalt cobalt cobalt filler",
        "amber amber cobalt cobalt cobalt filler filler filler",
        "filler filler filler",
    )
    assert ToolSearchIndex().search(specs, "amber cobalt", 1) == (specs[1],)


@pytest.mark.parametrize("query,chosen", [("amber amber cobalt", 0), ("amber cobalt cobalt", 1)])
def test_query_term_repetition_is_not_discarded(query, chosen):
    specs = specs_for("amber", "cobalt")
    assert ToolSearchIndex().search(specs, query, 1) == (specs[chosen],)


def test_failed_index_build_never_publishes_partial_generation(monkeypatch):
    first, second = specs_for("amber", "cobalt")
    index = ToolSearchIndex()
    assert index.search((first,), "amber", 1) == (first,)
    before = index.generation
    tokenize = search_module._tokens

    def fail(text):
        if text == "cobalt":
            raise ValueError("fixture tokenizer failure")
        return tokenize(text)

    monkeypatch.setattr(search_module, "_tokens", fail)
    for _ in range(2):
        with pytest.raises(ValueError, match="fixture tokenizer failure"):
            index.search((second, first), "amber", 1)
        assert index.generation == before
        assert index.specs == (first,)
    monkeypatch.setattr(search_module, "_tokens", tokenize)
    assert index.search((second, first), "cobalt", 1) == (second,)
    assert index.generation == before + 1


def test_input_schema_mutation_cannot_change_cached_generation_by_alias():
    original = replace(
        specs_for("unused")[0], search_text=None, parameters={"description": "amber"}
    )
    index = ToolSearchIndex()
    assert index.search((original,), "amber", 1) == (original,)
    generation = index.generation
    original.parameters["description"] = "cobalt"
    assert index.search((original,), "cobalt", 1) == (original,)
    assert index.generation == generation + 1


def test_explicit_empty_search_text_is_not_replaced_by_tool_name():
    spec = replace(specs_for("unused")[0], name="hidden_metadata", search_text="")
    assert ToolSearchIndex().search((spec,), "hidden_metadata", 1) == ()


def test_freeform_search_includes_syntax_but_not_full_grammar_definition():
    spec = ToolSpec(
        "raw",
        "fixture",
        {},
        exposure=ToolExposure.DEFERRED,
        input_kind="freeform",
        freeform_format={"type": "grammar", "syntax": "lark", "definition": "SECRET_GRAMMAR"},
    )
    index = ToolSearchIndex()
    assert index.search((spec,), "lark", 1) == (spec,)
    assert index.search((spec,), "SECRET_GRAMMAR", 1) == ()
