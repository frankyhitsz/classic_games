"""Cross-process gate for data maintenance and normal local writes."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path


class MaintenanceBusyError(OSError):
    """Raised when an application/maintenance lock cannot be acquired."""


def lock_path(database: Path | str) -> Path:
    path = Path(database)
    return path.with_name(f".{path.name}.maintenance.lock")


def _try_lock(descriptor: int, exclusive: bool) -> bool:
    if os.name == "nt":
        # msvcrt has no shared byte-range lock. Serializing ordinary writers
        # is conservative but preserves the same safety contract.
        import msvcrt

        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    flags = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    try:
        fcntl.flock(descriptor, flags | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextmanager
def maintenance_lock(database: Path | str, *, exclusive: bool,
                     timeout: float = 2.0):
    """Acquire the shared application or exclusive maintenance gate."""
    path = lock_path(database)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        deadline = time.monotonic() + max(0.0, timeout)
        while not _try_lock(descriptor, exclusive):
            if time.monotonic() >= deadline:
                raise MaintenanceBusyError(
                    "Classic Games is active; close the game and retry maintenance")
            time.sleep(0.02)
        try:
            yield
        finally:
            _unlock(descriptor)
    finally:
        os.close(descriptor)
