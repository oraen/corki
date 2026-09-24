import os

import pytest

from corki.cli.terminal_input_mode import between_readers_mode


@pytest.mark.skipif(os.name != "posix", reason="POSIX terminal signal flags")
@pytest.mark.parametrize("fail", [False, True])
def test_signal_gaps_preserve_typeahead_and_restore_terminal(fail):
    import termios

    from prompt_toolkit.input.vt100 import Vt100Input

    master, slave = os.openpty()
    try:
        with os.fdopen(slave, "r") as stream:
            original = termios.tcgetattr(stream.fileno())
            terminal_input = Vt100Input(stream)
            try:
                with between_readers_mode(terminal_input):
                    gap = termios.tcgetattr(stream.fileno())
                    assert gap[3] & termios.ISIG
                    assert gap[3] & termios.NOFLSH
                    assert not gap[3] & (termios.ECHO | termios.ICANON)
                    assert not gap[0] & termios.ICRNL
                    os.write(master, b"before\rafter\n")
                    assert os.read(stream.fileno(), 64) == b"before\rafter\n"
                    with terminal_input.raw_mode():
                        assert not termios.tcgetattr(stream.fileno())[3] & termios.ISIG
                        os.write(master, b"\x03")
                        assert os.read(stream.fileno(), 1) == b"\x03"
                    assert termios.tcgetattr(stream.fileno()) == gap
                    if fail:
                        raise ValueError("fixture")
            except ValueError:
                assert fail
            finally:
                terminal_input.close()
            restored = termios.tcgetattr(stream.fileno())
            # macOS sets PENDIN when canonical mode is restored. It is a
            # transient line-discipline flag, not a setting owned by Corki.
            restored[3] &= ~getattr(termios, "PENDIN", 0)
            original[3] &= ~getattr(termios, "PENDIN", 0)
            assert restored == original
    finally:
        os.close(master)


def test_dummy_input_needs_no_terminal_descriptor():
    from prompt_toolkit.input import DummyInput

    with between_readers_mode(DummyInput()):
        pass
