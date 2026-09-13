"""One task-owned user question waiter, separate from ordinary input and approvals."""

import asyncio
from collections.abc import Awaitable, Callable

from corki.protocol.events import UserInputRequested
from corki.protocol.ids import ThreadId, TurnId
from corki.protocol.user_input import UserInputQuestion, copy_response


class UserInputBroker:
    def __init__(self) -> None:
        self._pending: tuple[str, asyncio.Future] | None = None

    async def request(
        self,
        thread_id: ThreadId,
        turn_id: TurnId,
        call_id: str,
        questions: tuple[UserInputQuestion, ...],
        is_blocking: bool,
        emit: Callable[[UserInputRequested], Awaitable[None]],
    ) -> dict:
        future = asyncio.get_running_loop().create_future()
        previous = self._pending
        owner = (call_id, future)
        self._pending = owner
        if previous is not None and not previous[1].done():
            previous[1].set_result(None)
        try:
            await emit(UserInputRequested(thread_id, turn_id, call_id, questions, is_blocking))
            response = await future
            if response is None:
                raise ValueError("request_user_input was cancelled before receiving a response")
            return response
        finally:
            if self._pending is owner:
                self._pending = None
            if not future.done():
                future.cancel()

    def respond(self, call_id: str, response: object) -> bool:
        owner = self._pending
        if owner is None or owner[0] != call_id or owner[1].done():
            return False
        value = None if response is None else copy_response(response)
        self._pending = None
        owner[1].set_result(value)
        return True

    def is_pending(self, call_id: str) -> bool:
        owner = self._pending
        return owner is not None and owner[0] == call_id and not owner[1].done()
