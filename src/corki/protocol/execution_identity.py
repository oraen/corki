"""Host-owned process identity, independent of a terminal process/session handle."""

from dataclasses import dataclass

from corki.protocol.ids import SessionId, ThreadId


@dataclass(frozen=True, slots=True)
class ExecutionIdentity:
    """Immutable identity resolved from durable thread metadata before tool admission."""

    thread_id: ThreadId
    session_id: SessionId

    def __post_init__(self) -> None:
        # Existing Corki adapters use opaque string IDs, including pre-UUID fixtures.
        # Do not silently convert a malformed stored value into another identity.
        if any(
            not isinstance(value, str) or not value or "\0" in value
            for value in (self.thread_id, self.session_id)
        ):
            raise ValueError("execution identity requires nonempty NUL-free string IDs")
