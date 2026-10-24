"""CLI unit tests use explicit terminal adapters, independent of the host console."""

import pytest


@pytest.fixture(autouse=True)
def unit_terminal_session(headless_terminal):
    """Keep display tests headless; explicit pipe inputs still override defaults.

    Real Windows console modes are exercised in their own subprocesses, while
    native keyboard, stream and process behavior remains in ConPTY integration
    tests. This fixture never applies to those integration or PTY suites.
    """
