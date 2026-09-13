"""Await construction rollback without transferring borrowed host resources."""

import asyncio
from functools import wraps


def rollback_registry(factory):
    """Keep a borrowed registry unchanged when synchronous assembly fails."""

    @wraps(factory)
    def construct(*args, **kwargs):
        registry = kwargs.get("registry")
        if registry is None:
            return factory(*args, **kwargs)
        with registry.composition():
            return factory(*args, **kwargs)

    return construct


async def create_owned(factory, kwargs):
    closers = []
    try:
        return factory(_construction=closers, **kwargs)
    except BaseException as primary:
        await _finish_rollback(closers, primary)
        raise


def synchronous_rollback(factory):
    """Wait for failed synchronous construction when no event loop is running."""

    @wraps(factory)
    def construct(*args, **kwargs):
        if kwargs.get("_construction") is not None:
            return factory(*args, **kwargs)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            # In-loop hosts must use acreate to await failure cleanup. Never
            # block their loop or launch an unowned cleanup task here.
            return factory(*args, **kwargs)
        closers = []
        kwargs["_construction"] = closers
        try:
            return factory(*args, **kwargs)
        except BaseException as primary:
            asyncio.run(_finish_rollback(closers, primary))
            raise

    return construct


async def _finish_rollback(closers, primary):
    async def rollback():
        errors = []
        for close in reversed(closers):
            try:
                await close()
            except BaseException as error:
                errors.append(error)
        return errors

    cleanup = asyncio.create_task(rollback(), name="corki-construction-rollback")
    cancelled = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            cancelled = True
    for error in cleanup.result():
        primary.add_note(f"Construction cleanup failed: {type(error).__name__}")
        cancelled = cancelled or isinstance(error, asyncio.CancelledError)
    if cancelled and not isinstance(primary, asyncio.CancelledError):
        raise asyncio.CancelledError from primary
