"""Isolated child entrypoint: attach the PTY, then replace this process with argv.

Executed by path with Python -I -S; intentionally has no Corki/package imports.
"""

import errno
import json
import os
import signal
import sys


def main() -> None:
    import fcntl
    import termios

    status = int(sys.argv[1])
    os.set_inheritable(status, False)
    try:
        # The parent used start_new_session; this process is already the leader.
        for name in (
            "SIGCHLD",
            "SIGHUP",
            "SIGINT",
            "SIGQUIT",
            "SIGTERM",
            "SIGALRM",
            "SIGPIPE",
            "SIGXFZ",
            "SIGXFSZ",
        ):
            if number := getattr(signal, name, None):
                signal.signal(number, signal.SIG_DFL)
        signal.pthread_sigmask(signal.SIG_SETMASK, [])
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        os.write(status, b"R")
        os.execvpe(sys.argv[2], sys.argv[2:], os.environ)
    except Exception as exc:
        payload = json.dumps(
            {
                "errno": getattr(exc, "errno", None) or errno.EIO,
                "message": str(exc)[:500],
            }
        ).encode("ascii")
        os.write(status, payload)
        os._exit(127)


if __name__ == "__main__":
    main()
