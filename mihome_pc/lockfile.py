"""``app.lock``: which process is serving the UI, on which URL, since when.

Why a file instead of "ask the OS": ``serve`` picks a random free port, so the
URL has to be discoverable by a *second* invocation (``mihome-pc stop``, or a
second ``app`` re-using the running instance).  The lock is written atomically
(``os.replace``) because a reader may hit it while it is being rewritten.

Windows note: ``os.kill(pid, 0)`` is **not** a liveness probe there -- CPython
maps any signal other than CTRL_C/CTRL_BREAK to ``TerminateProcess``.  Liveness
is therefore derived from the HTTP probe in :mod:`mihome_pc.app`, never from
signal 0.
"""
import contextlib
import json
import os
import time

__all__ = ["LOCK_NAME", "lock_path", "read", "write", "drop"]

LOCK_NAME = "app.lock"


def lock_path(data_dir=None):
    if data_dir is None:
        from .cli import data_dir as _data_dir
        data_dir = _data_dir()
    return os.path.join(data_dir, LOCK_NAME)


def read(data_dir=None):
    """``{"pid": int, "url": str, "since": int}`` or ``{}`` when absent/corrupt."""
    try:
        with open(lock_path(data_dir), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write(url, data_dir=None, pid=None):
    """Record ``url`` as served by ``pid`` (default: this process)."""
    path = lock_path(data_dir)
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    info = {"pid": int(pid or os.getpid()), "url": url, "since": int(time.time())}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(info, handle)
    os.replace(tmp, path)
    return info


def drop(data_dir=None, own=False):
    """Remove the lock; with ``own`` only when it belongs to this process."""
    if own:
        info = read(data_dir)
        if info.get("pid") not in (None, os.getpid()):
            return False
    with contextlib.suppress(OSError):
        os.remove(lock_path(data_dir))
        return True
    return False
