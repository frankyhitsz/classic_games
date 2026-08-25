"""Cross-process gate for data maintenance and normal local writes."""

from __future__ import annotations

import os
import stat
import time
from contextlib import contextmanager
from pathlib import Path


class MaintenanceBusyError(OSError):
    """Raised when an application/maintenance lock cannot be acquired."""


def _open_control_file(path: Path) -> int:
    """Open a lock without following a user-controlled final symlink."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise MaintenanceBusyError(
            f"unsafe or unavailable data lock: {path.name}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise MaintenanceBusyError(
                f"data lock is not an ordinary file: {path.name}")
        if os.name == "posix":
            if metadata.st_uid != os.getuid():
                raise MaintenanceBusyError(
                    f"data lock has another owner: {path.name}")
            if metadata.st_mode & 0o022:
                raise MaintenanceBusyError(
                    f"data lock is writable by another account: {path.name}")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def lock_path(database: Path | str) -> Path:
    path = Path(database)
    return path.with_name(f".{path.name}.maintenance.lock")


def application_lock_path(database: Path | str) -> Path:
    path = Path(database)
    return path.with_name(f".{path.name}.application.lock")


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


class ApplicationSession:
    """Lifetime shared lease proving that a local backend is still active."""

    def __init__(self, descriptor: int, slot: int | None = None):
        self._descriptor = descriptor
        self._slot = slot

    @classmethod
    def acquire(cls, database: Path | str, timeout: float = 2.0):
        path = application_lock_path(database)
        descriptor = _open_control_file(path)
        deadline = time.monotonic() + max(0.0, timeout)
        try:
            if os.fstat(descriptor).st_size < 256:
                os.ftruncate(descriptor, 256)
                os.fsync(descriptor)
            if os.name != "nt":
                while not _try_lock(descriptor, exclusive=False):
                    if time.monotonic() >= deadline:
                        raise MaintenanceBusyError(
                            "local data maintenance is active; retry shortly")
                    time.sleep(0.02)
                return cls(descriptor)
            import msvcrt

            while True:
                for slot in range(1, 256):
                    try:
                        os.lseek(descriptor, slot, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    except OSError:
                        continue
                    return cls(descriptor, slot)
                if time.monotonic() >= deadline:
                    raise MaintenanceBusyError(
                        "local data maintenance is active; retry shortly")
                time.sleep(0.02)
        except Exception:
            os.close(descriptor)
            raise

    def close(self) -> None:
        descriptor = self._descriptor
        if descriptor < 0:
            return
        self._descriptor = -1
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, int(self._slot), os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                _unlock(descriptor)
        finally:
            os.close(descriptor)

    def __del__(self):
        try:
            self.close()
        except OSError:
            pass


@contextmanager
def inactive_application_lock(database: Path | str, timeout: float = 2.0):
    """Hold an exclusive lease after every cooperating backend has closed."""
    path = application_lock_path(database)
    descriptor = _open_control_file(path)
    try:
        if os.fstat(descriptor).st_size < 256:
            os.ftruncate(descriptor, 256)
            os.fsync(descriptor)
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            if os.name == "nt":
                import msvcrt

                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 256)
                except OSError:
                    acquired = False
                else:
                    acquired = True
            else:
                acquired = _try_lock(descriptor, exclusive=True)
            if acquired:
                break
            if time.monotonic() >= deadline:
                raise MaintenanceBusyError(
                    "Classic Games is active; close it before data maintenance")
            time.sleep(0.02)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 256)
            else:
                _unlock(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def maintenance_lock(database: Path | str, *, exclusive: bool,
                     timeout: float = 2.0):
    """Acquire the shared application or exclusive maintenance gate."""
    path = lock_path(database)
    descriptor = _open_control_file(path)
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


def recovered_application_session(database: Path | str,
                                  timeout: float = 2.0) -> ApplicationSession:
    """Recover interrupted imports before granting an application lease."""
    database = Path(database).expanduser().resolve(strict=False)
    with (inactive_application_lock(database, timeout=timeout),
          maintenance_lock(database, exclusive=True, timeout=timeout)):
        # Imported lazily so the recovery module can keep using StoreError
        # without creating an import cycle at module load time.
        from .import_transaction import recover_import_transactions

        recover_import_transactions(database)
    # If maintenance starts in the small hand-off window it wins the
    # exclusive application lock; this shared acquisition then waits for it.
    return ApplicationSession.acquire(database, timeout=timeout)
