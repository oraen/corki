"""Read-only verification against cached, checksum-pinned upstream archives.

Usage: .venv/bin/python harness-alignment/verify-tokenizer-sources.py CACHE_DIR
No network access, Rust execution, extraction or redistribution of test corpora.
"""

import argparse
import hashlib
import io
import json
import re
import tarfile
from pathlib import Path

from snowballstemmer.english_stemmer import EnglishStemmer

from corki.tools.tokenizer import _ascii_words, _data

ARCHIVES = {
    "deunicode-1.6.2": "abd57806937c9cc163efc8ea3910e00a62e2aeb0b8119f1793a978088f8f6b04",
    "rust-stemmers-1.2.0": "e46a2036019fdb888131db7a4c847a1063a7493f971ed94ea82c67eada63ca54",
    "stop-words-0.9.0": "645a3d441ccf4bf47f2e4b7681461986681a6eeea9937d4c3bc9febd61d17c71",
    "unicode-segmentation-1.12.0": (
        "f6ccf251212114b54433ec949fd6a7841275f9ada20dddd2f29e9ceea4501493"
    ),
    "fxhash-0.2.1": "c31b6d751ae2c7f11320402d34e41349dd1016f8d5d45e48c4312bc8625af50c",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache", type=Path)
    args = parser.parse_args()
    archives = {}
    for name, digest in ARCHIVES.items():
        data = (args.cache / (name + ".crate")).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest, name
        archives[name] = data

    def read(name: str, member: str) -> bytes:
        with tarfile.open(fileobj=io.BytesIO(archives[name]), mode="r:gz") as archive:
            stream = archive.extractfile(name + "/" + member)
            assert stream is not None, member
            return stream.read()

    pointers, mapping, stopwords = _data()
    assert pointers == read("deunicode-1.6.2", "src/pointers.bin")
    assert mapping == read("deunicode-1.6.2", "src/mapping.txt")
    upstream_words = read("stop-words-0.9.0", "src/nltk/english").decode().splitlines()
    assert stopwords == frozenset(upstream_words) and len(upstream_words) == 179

    words = read("rust-stemmers-1.2.0", "test_data/voc_en.txt").decode().splitlines()
    expected = read("rust-stemmers-1.2.0", "test_data/res_en.txt").decode().splitlines()
    actual = EnglishStemmer().stemWords(words)
    assert len(words) == 29417
    differences = [(w, a, e) for w, a, e in zip(words, actual, expected, strict=True) if a != e]
    assert not differences, differences[:10]

    # Select the ASCII-only subset of the pinned Unicode break test, without
    # normalizing non-ASCII test cases and incorrectly reusing their old boundaries.
    source = read("unicode-segmentation-1.12.0", "tests/testdata/mod.rs").decode()
    section = source.split("pub const TEST_WORD:", 1)[1].split("= &[", 1)[1].split("];", 1)[0]

    def rust_string(raw: str) -> str:
        escaped = re.sub(
            r"\\u\{([0-9a-fA-F]+)\}",
            lambda match: json.dumps(chr(int(match[1], 16)))[1:-1],
            raw,
        )
        return json.loads('"' + escaped + '"')

    pairs = re.findall(r'\("((?:\\.|[^"\\])*)",\s*&\[(.*?)\]\)', section, re.DOTALL)
    assert len(pairs) > 1000, "unexpected upstream fixture syntax"
    checked = 0
    for raw_text, raw_segments in pairs:
        text = rust_string(raw_text)
        if not text.isascii():
            continue
        segments = [rust_string(s) for s in re.findall(r'"((?:\\.|[^"\\])*)"', raw_segments)]
        assert "".join(segments) == text
        expected_words = [s for s in segments if any(c.isalnum() for c in s)]
        assert list(_ascii_words(text)) == expected_words, repr(text)
        checked += 1
    assert checked > 100
    print(
        json.dumps(
            {
                "verified_archives": len(archives),
                "normalizer_bytes_identical": len(pointers + mapping),
                "nltk_stopwords": len(stopwords),
                "stemmer_goldens": len(words),
                "word_boundary_ascii_goldens": checked,
                "differences": 0,
                "rust_executed": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
