import pytest

from corki.tools.builtin.process_status import observation_exit_code


@pytest.mark.parametrize("tty", [False, True])
@pytest.mark.parametrize("value", [None, 0, 1, 7, 255])
def test_ordinary_status_is_unchanged(tty, value):
    assert observation_exit_code(value, tty=tty, unix=True) == value


@pytest.mark.parametrize("tty", [False, True])
@pytest.mark.parametrize("value", [-1, -2, -9, -15])
def test_default_unix_paths_have_distinct_signal_status(tty, value):
    assert observation_exit_code(value, tty=tty, unix=True) == (1 if tty else 128 - value)


def test_non_unix_status_is_not_interpreted_as_a_signal():
    assert observation_exit_code(-1073741510, tty=False, unix=False) == -1073741510
