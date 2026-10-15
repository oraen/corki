"""A single denial retry belongs to startup, never to resumable observations."""

import asyncio
from contextlib import suppress

from corki.execution.retry import is_sandbox_denial
from corki.tools.builtin.process_status import observation_exit_code


async def _until_cancelled(operation, cancelled):
    task = asyncio.ensure_future(operation)
    cancellation = asyncio.create_task(cancelled.wait())
    try:
        await asyncio.wait((task, cancellation), return_when=asyncio.FIRST_COMPLETED)
        if cancelled.is_set():
            raise asyncio.CancelledError
        return task.result()
    finally:
        task.cancel()
        cancellation.cancel()
        await asyncio.gather(task, cancellation, return_exceptions=True)


async def finish_sandbox_startup(
    manager, spawn, admission, permissions, cwd, *, call_id, tty, cancelled, generation
):
    """Own both attempts through every await until the surviving session is published."""
    plan = admission.plan
    assert plan is not None
    session = None

    def check_fence():
        if cancelled.is_set() or manager._closing or generation != manager._generation:
            raise asyncio.CancelledError
        if session is not None and session.failure is not None:
            raise session.failure

    try:
        check_fence()
        session = await spawn(None)
        await manager._wait_session(session, 0.15)
        check_fence()
        if (
            plan.command is not None
            and session.process.returncode is not None
            and not session.timed_out
        ):
            if session.reader_task is not None:
                # Same bounded post-exit opportunity as native's output notification.
                with suppress(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(session.reader_task), 0.02)
            check_fence()
            denied = await _until_cancelled(
                is_sandbox_denial(
                    permissions,
                    plan.sandbox,
                    observation_exit_code(session.process.returncode, tty=session.tty),
                    session.peek_output(),
                ),
                cancelled,
            )
            check_fence()
            if denied:
                # The first leader has exited. Join its remaining pipe/group owners
                # before asking for/reusing approval or starting another command.
                await manager._retire(session)
                check_fence()
                if not admission.approved:
                    assert plan.approval is not None
                    await _until_cancelled(
                        manager.approvals.authorize(
                            plan.approval,
                            list(plan.command),
                            cwd,
                            call_id=call_id,
                            tty=tty,
                        ),
                        cancelled,
                    )
                check_fence()
                session = await spawn(list(plan.command))
                await manager._wait_session(session, 0.15)
                check_fence()
                # No loop: even another denial is now the final ordinary output.
        if session.process.returncode is None:
            await manager._publish_session(session)
        check_fence()
        return session
    except BaseException:
        if session is not None:
            await manager._retire(session)
        raise
