"""Golden cases from the pinned BM25 pipeline, not an interchangeable tokenizer."""

import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from importlib.resources import files
from types import SimpleNamespace

import pytest

from corki.protocol.tools import ToolSpec
from corki.tools import tokenizer as tokenizer_module
from corki.tools.search import ToolSearchIndex, _tokens
from corki.tools.tokenizer import _ascii_words, _data, normalize, token_id


@pytest.mark.parametrize(
    "text,expected",
    [
        ("connection connections connective connected connecting connect", ["connect"] * 6),
        ("café CAFÉ étude", ["cafe", "cafe", "etud"]),
        ("🍕 🚀 🍋", ["pizza", "rocket", "lemon"]),
        ("the and SHOULD'VE wasn't yourselves", []),
        ("foo_bar 3.14 1,234 5;67", ["foo_bar", "3.14", "1,234", "5;67"]),
        ("北京", ["bei", "jing"]),
        ("10 able about", ["10", "abl"]),  # NLTK, NOT the default ISO table.
        ("___ ! \U0010ffff", []),
    ],
)
def test_pinned_english_pipeline(text, expected):
    assert _tokens(text) == expected


def test_stopwords_do_not_retrieve_or_load_a_tool():
    index = ToolSearchIndex()
    spec = ToolSpec("candidate", "", {"type": "object"}, search_text="the and cobalt")
    assert index.search((spec,), "the and", 8) == ()
    assert index.search((spec,), "cobalt", 8) == (spec,)


def test_underscore_and_decimal_boundaries_are_not_partial_word_hits():
    index = ToolSearchIndex()
    spec = ToolSpec("candidate", "", {"type": "object"}, search_text="foo_bar 3.14")
    assert index.search((spec,), "foo 14", 8) == ()
    assert index.search((spec,), "foo_bar", 8) == (spec,)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Æneid", "AEneid"),
        ("étude", "etude"),
        ("北亰", "Bei Jing"),
        ("ᔕᓇᓇ", "shanana"),
        ("げんまい茶", "genmaiCha"),
        ("🦄☣", "unicorn biohazard"),
        ("…", "..."),
        ("🄏中国", "NonCommercialZhong Guo"),
        ("中国x🅶", "Zhong Guo xG"),
        ("☃中 国", "snowman Zhong Guo"),
        ("北 ", "Bei "),  # Trim generated space before an explicit space, not both.
        ("北\u0301", "Bei "),  # Empty following segment is NOT end-of-input.
        ("北\U0010ffff", "Bei [?]"),
        ("\x00é\x00", "\x00e"),  # Prefix fast path differs from subsequent lookup.
        ("é\x7f", "e"),
        ("abc\x00", "abc\x00"),
    ],
)
def test_pinned_deunicode_source_examples_and_lookahead(text, expected):
    assert normalize(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "can't you're won't let's couldn't've",
            ["can't", "you're", "won't", "let's", "couldn't've"],
        ),
        ("foo_bar __ __a_ _1_", ["foo_bar", "__a_", "_1_"]),
        ("a:b a.b 3.14 1,234 5;67", ["a:b", "a.b", "3.14", "1,234", "5;67"]),
        ("a,b a;b 1:2 1.a a.1", ["a", "b", "a", "b", "1", "2", "1", "a", "a", "1"]),
        ("ab''cd ab.:cd _'a a'_ ab' 'cd", ["ab", "cd", "ab", "cd", "a", "a", "ab", "cd"]),
        ("a1_2b\r\nfoo-bar\t", ["a1_2b", "foo", "bar"]),
    ],
)
def test_reachable_word_boundary_states(text, expected):
    assert list(_ascii_words(text)) == expected


def test_stopwords_removed_before_stemming_and_stemmer_not_shared():
    # "having" is removed before it becomes "have"; "skies" is not a stopword.
    text = "having skies dying lying news proceeds exceeding succeeding"
    expected = ["sky", "die", "lie", "news", "proceed", "exceed", "succeed"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(_tokens, [text] * 100)) == [expected] * 100


@pytest.mark.skipif(sys.byteorder != "little", reason="published crate golden uses little endian")
@pytest.mark.parametrize(
    "text,expected",
    [
        ("cup", 2070875659),
        ("tea", 415655421),
        ("appl", 1777144781),
        ("orang", 3887370161),
        ("papaya", 2177600299),
        ("Cup", 3568447556),
        ("of", 3221979461),
    ],
)
def test_fxhash32_published_embedding_ids(text, expected):
    assert token_id(text) == expected


@pytest.mark.skipif(sys.byteorder != "little", reason="fixture collision uses little endian")
def test_term_identity_retains_u32_collision_in_real_index():
    first, second = "ptgwvgyrialmq", "jlfmcjtacoyrq"
    assert _tokens(first) == [first] and _tokens(second) == [second]
    assert token_id(first) == token_id(second) == 1443976578
    spec = ToolSpec("candidate", "", {"type": "object"}, search_text=first)
    assert ToolSearchIndex().search((spec,), second, 8) == (spec,)


def test_bundled_nltk_feature_and_normalizer_assets_are_pinned():
    pointers, mapping, words = _data()
    assert len(pointers) == 419994 and len(mapping) == 56405
    assert hashlib.sha256(pointers).hexdigest() == (
        "f2e1772f608f050555f6bd0f1d7a2b453b929bd02300927ce2db6afb88ad500f"
    )
    assert hashlib.sha256(mapping).hexdigest() == (
        "8cb5a957e0bf7b702accc3ba25bf01bb34c1b0cc5d5fa4f0081d36c2cb63db20"
    )
    assert len(words) == 179
    assert "about" in words and "able" not in words and "10" not in words
    assert hashlib.sha256("\n".join(sorted(words)).encode()).hexdigest() == (
        "63c081f949766b37ecdc95ac5b55639990d34295166996357ff47df805fda211"
    )


@pytest.mark.parametrize("field", ["pointers_sha256", "mapping_sha256", "nltk_english_sha256"])
def test_corrupt_data_fails_without_publishing_index_or_silent_fallback(monkeypatch, field):
    index = ToolSearchIndex()
    original = ToolSpec("old", "", {"type": "object"}, search_text="amber")
    assert index.search((original,), "amber", 1) == (original,)
    replacement = ToolSpec("new", "", {"type": "object"}, search_text="cobalt")
    resource = json.loads(files("corki.tools").joinpath("tokenizer_data.json").read_text("utf-8"))
    resource[field] = "corrupt"
    fake = SimpleNamespace(read_text=lambda encoding: json.dumps(resource))
    _data.cache_clear()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(
                tokenizer_module, "files", lambda name: SimpleNamespace(joinpath=lambda p: fake)
            )
            with pytest.raises(ValueError, match="checksum mismatch"):
                index.search((replacement,), "cobalt", 1)
            assert index.generation == 1 and index.specs == (original,)
    finally:
        _data.cache_clear()
    assert index.search((replacement,), "cobalt", 1) == (replacement,)
    assert index.generation == 2
