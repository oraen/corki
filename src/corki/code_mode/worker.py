"""One disposable engine process. No project imports, module loader or JS I/O APIs."""

import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

import quickjs


def main():
    configuration = json.loads(sys.stdin.readline())
    context = quickjs.Context()
    context.set_memory_limit(configuration["memory_bytes"])
    context.set_max_stack_size(1024 * 1024)
    timers = {}
    finished = False
    incoming = queue.Queue(maxsize=32)

    def receive():
        for line in sys.stdin:
            incoming.put(line)
        # Parent death must also stop JS stuck in a synchronous loop. QuickJS
        # releases the GIL while evaluating; this reader can end its own worker.
        os._exit(0)

    threading.Thread(target=receive, daemon=True).start()

    def emit(raw):
        nonlocal finished
        value = json.loads(raw)
        if value["type"] == "timer":
            timers[value["id"]] = time.monotonic() + value["delay"] / 1000
        elif value["type"] == "clear_timer":
            timers.pop(value["id"], None)
        else:
            if value["type"] == "complete":
                finished = True
            print(raw, flush=True)

    context.add_callable("__corki_emit", emit)
    bootstrap = context.eval(Path(__file__).with_name("bootstrap.js").read_text())
    dispatch = bootstrap(json.dumps(configuration))
    try:
        promise = context.module(configuration["source"])
        dispatch("main", promise)
        while not finished:
            # A microtask storm remains externally terminable in this process.
            for _ in range(1000):
                if finished or not context.execute_pending_job():
                    break
            if finished:
                break
            now = time.monotonic()
            for timer_id, deadline in tuple(timers.items()):
                if deadline <= now:
                    timers.pop(timer_id, None)
                    dispatch(json.dumps({"type": "timer", "id": timer_id}))
            timeout = min(0.01, max(0, min(timers.values()) - time.monotonic())) if timers else 0.01
            try:
                line = incoming.get(timeout=timeout)
                if not line:
                    break
                dispatch(line)
            except queue.Empty:
                pass
    except Exception as error:
        emit(json.dumps({"type": "complete", "error": str(error), "writes": {}}))


if __name__ == "__main__":
    main()
    # No Python objects are exposed to JS; the only background reader owns
    # stdin. Avoid CPython finalization racing that blocked daemon's I/O lock.
    os._exit(0)
