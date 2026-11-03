"""Attach Windows process containment without changing POSIX spawning semantics."""

import asyncio
import os
import subprocess
import weakref
from contextlib import suppress


def background_process_options():
    """Hide Windows console children, including the venv launcher's real Python.

    Do not combine this with DETACHED_PROCESS: Windows ignores CREATE_NO_WINDOW
    in that case and a console grandchild may open its own visible window.
    Processes with redirected streams still outlive their launcher on Windows.
    """
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


async def create_subprocess_exec(*args, **kwargs):
    if os.name != "nt":
        return await asyncio.create_subprocess_exec(*args, **kwargs)
    from corki.platforms.windows_job import WindowsJob
    from corki.platforms.windows_program import prepare_command

    args = prepare_command(args, env=kwargs.get("env"), cwd=kwargs.get("cwd"))
    job = WindowsJob()
    process = None
    # CREATE_SUSPENDED closes the assignment race: user code cannot spawn a
    # grandchild before it joins the job. No breakaway permission is granted.
    kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW | 0x4
    spawn = asyncio.create_task(asyncio.create_subprocess_exec(*args, **kwargs))
    try:
        process = await asyncio.shield(spawn)
        job.assign_and_resume(process.pid)
        process._corki_job = job
        process._corki_job_finalizer = weakref.finalize(process, job.close)
        return process
    except BaseException:
        # Cancellation during CreateProcess still owns the eventual child.
        while not spawn.done():
            with suppress(Exception, asyncio.CancelledError):
                await asyncio.shield(spawn)
        if not spawn.cancelled() and spawn.exception() is None:
            process = spawn.result()
        job.close()
        if process is not None:
            with suppress(ProcessLookupError):
                process.kill()
            process._transport.close()
            await process.wait()
        raise


def close_job(process) -> bool:
    """Close only this process's owned job, even if its leader has exited."""
    finalizer = getattr(process, "_corki_job_finalizer", None)
    if finalizer is None:
        return False
    finalizer()
    return True
