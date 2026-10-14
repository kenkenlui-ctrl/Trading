#!/usr/bin/env python3
"""Single-instance guard for refresh.sh. Run BY refresh.sh; it re-execs bash.

Why this exists (2026-10-14)
---------------------------
Two refresh.sh runs overlapped on this machine and neither knew about the
other. Both wrote the same charts, the same public/ tree, and both reached the
git commit and the wrangler deploy, so the outcome was decided by whichever
process happened to finish last. Before this guard, that failure had no name:
a run either looked fine or looked like an ordinary vendor hiccup.

It bites hardest because the pipeline is designed to be re-runnable — the
chart guards skip work that is already current, and every step is written to
be safe to repeat. Re-runnable is not the same as safe to run twice at once,
and nothing in the script said so.

Why flock and not a lockfile or a lock directory
------------------------------------------------
A lockfile needs an age heuristic to survive a crashed run, and a lock
directory needs a delete to release it. fcntl.flock needs neither: the lock
lives on an open file description, so the kernel drops it the instant the
last holder exits — clean exit, SIGKILL, or a pull-the-plug power cut. A
stale lock is therefore not a state this system can reach.

The lock fd is deliberately made inheritable before exec, so the bash process
that replaces this one keeps holding it for its whole life. W9_REFRESH_LOCK
carries the fd number purely so refresh.sh can report it.
"""
from __future__ import annotations

import fcntl
import os
import sys

LOCK_PATH = "/tmp/w9refresh.lock"


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: refresh_lock.py /abs/path/to/refresh.sh")
    script = sys.argv[1]
    if not os.path.isfile(script):
        sys.exit(f"✗ refresh_lock.py: no such script {script}")

    fd = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        holder = b""
        try:
            holder = os.pread(fd, 64, 0).strip()
        except OSError:
            pass
        os.close(fd)
        who = holder.decode(errors="replace") if holder else "another process"
        sys.exit(f"✗ refresh already running (lock {LOCK_PATH}, holder pid {who}).\n"
                 f"  Two runs would race on public/, the commit and the deploy.\n"
                 f"  Wait for it to finish, or stop it first: pkill -f 'scripts/refresh.sh'")

    os.set_inheritable(fd, True)
    os.pwrite(fd, f"{os.getpid()}\n".encode(), 0)
    env = dict(os.environ, W9_REFRESH_LOCK=str(fd))
    os.execve("/bin/bash", ["/bin/bash", script], env)


if __name__ == "__main__":
    main()