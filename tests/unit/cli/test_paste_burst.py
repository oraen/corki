import pytest

from corki.cli.paste_burst import Flush, PasteBurst


def test_ascii_first_character_and_fast_pair():
    state = PasteBurst()
    assert state.char("a", 0) == ("hold", 0)
    assert state.flush_due(0.007) is None
    assert state.flush_due(0.009) == Flush("typed", "a")
    assert not state.active
    assert state.char("b", 0.020) == ("hold", 0)
    assert state.char("c", 0.021) == ("pending", 0)
    state.append("c", 0.021)
    assert state.flush_due(0.030) == Flush("paste", "bc")
    assert state.suppress_enter(0.031)
    assert not state.suppress_enter(0.142)


def test_held_character_plus_enter_is_not_submission():
    state = PasteBurst()
    state.char("a", 0)
    assert state.newline(0.001)
    assert state.flush_due(0.009) == Flush("paste", "a\n")
    assert state.suppress_enter(0.05)


def test_non_ascii_is_not_held_and_retro_grab_is_unicode_safe():
    state = PasteBurst()
    assert state.char("中", 0, hold=False) == ("insert", 0)
    assert state.char("文", 0.001, hold=False) == ("insert", 0)
    assert state.char("字", 0.002, hold=False) == ("retro", 2)
    assert state.retro_grab("中文", 2, 0.002) is None
    assert not state.active
    assert state.retro_grab("前 中", 2, 0.003) == 1
    state.append("字", 0.003)
    assert state.flush_due(0.012) == Flush("paste", " 中字")


def test_modified_key_drain_and_explicit_paste_reset():
    state = PasteBurst()
    state.char("x", 0)
    assert state.drain_before_modified() == "x"
    state.clear_window()
    assert not state.suppress_enter(0.001)
    state.char("a", 1)
    state.char("b", 1.001)
    state.append("b", 1.001)
    with pytest.raises(RuntimeError):
        state.clear_window()
    assert state.drain_before_modified() == "ab"
    state.clear()
    assert state.flush_due(2) is None
    assert not state.active and not state.suppress_enter(2)


def test_windows_burst_has_longer_idle_window():
    state = PasteBurst(windows=True)
    state.char("a", 0)
    state.char("b", 0.001)
    state.append("b", 0.001)
    assert state.flush_due(0.010) is None
    assert state.flush_due(0.062) == Flush("paste", "ab")
