import pytest

from corki.cli.reference_completion import FileCandidates, match_score


def test_chunk_boundaries_unicode_dedup_and_top_matches():
    paths = [f"目录/测试-{i:05}.md" for i in range(20000)]
    scan = FileCandidates("测试19")
    raw = ("\0".join(paths + paths[-5:]) + "\0").encode()
    for offset in range(0, len(raw), 137):
        scan.feed(raw[offset : offset + 137])
        assert len(scan.rows) <= 100 and len(scan.ranked) <= 100
        assert len(scan.pending) < 137
    expected = sorted(
        (score, path) for path in paths if (score := match_score(path, "测试19")) is not None
    )[:100]
    assert [row.label for row in scan.finish()] == [path for _, path in expected]


def test_partial_invalid_or_control_paths_never_become_references():
    scan = FileCandidates("")
    scan.feed(b"valid\0\xff\0bad\nname\0")
    assert [r.label for r in scan.finish()] == ["valid"]
    scan.feed(b"unfinished")
    with pytest.raises(ValueError, match="incomplete"):
        scan.finish()


@pytest.mark.parametrize("terminated", [False, True])
def test_single_path_memory_is_bounded(terminated):
    scan = FileCandidates("")
    with pytest.raises(ValueError, match="limit"):
        scan.feed(b"x" * 65537 + (b"\0" if terminated else b""))
