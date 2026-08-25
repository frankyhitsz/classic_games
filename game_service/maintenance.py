"""Cross-process gate for data maintenance and normal local writes."""

from __future__ import annotations

import os
import stat
import time
from contextlib import contextmanager
from pathlib import Path


class MaintenanceBusyError(OSError):
    """Raised when an application/maintenance lock cannot be acquired."""


def _open_windows_control_file(path: Path) -> int:
    """Open the reparse point itself so it cannot masquerade as a lock."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create_file.restype = wintypes.HANDLE
    close_handle = ctypes.windll.kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    get_attributes = ctypes.windll.kernel32.GetFileInformationByHandleEx
    get_attributes.argtypes = (
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    get_attributes.restype = wintypes.BOOL

    class FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [("file_attributes", wintypes.DWORD),
                    ("reparse_tag", wintypes.DWORD)]

    generic_read = 0x80000000
    generic_write = 0x40000000
    share_all = 0x00000001 | 0x00000002 | 0x00000004
    open_always = 4
    file_attribute_normal = 0x00000080
    file_flag_open_reparse_point = 0x00200000
    file_attribute_reparse_point = 0x00000400
    file_attribute_tag_info = 9
    invalid_handle = ctypes.c_void_p(-1).value
    handle = create_file(
        str(path), generic_read | generic_write, share_all, None, open_always,
        file_attribute_normal | file_flag_open_reparse_point, None)
    if handle == invalid_handle:
        raise MaintenanceBusyError(
            f"unsafe or unavailable data lock: {path.name}")
    try:
        attributes = FileAttributeTagInfo()
        if not get_attributes(
                handle, file_attribute_tag_info, ctypes.byref(attributes),
                ctypes.sizeof(attributes)):
            raise MaintenanceBusyError(
                f"cannot inspect data lock: {path.name}")
        if attributes.file_attributes & file_attribute_reparse_point:
            raise MaintenanceBusyError(
                f"data lock is a Windows reparse point: {path.name}")
        descriptor = msvcrt.open_osfhandle(int(handle), os.O_RDWR)
    except Exception:
        close_handle(handle)
        raise
    return descriptor


def _open_control_file(path: Path) -> int:
    """Open a lock without following a user-controlled final symlink."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        descriptor = _open_windows_control_file(path)
    else:
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
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise MaintenanceBusyError(
                f"data lock is not a private ordinary file: {path.name}")
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


def application_transition_lock_path(database: Path | str) -> Path:
    """Serialize POSIX application-lock acquisition and conversion."""
    path = Path(database)
    return path.with_name(f".{path.name}.application-transition.lock")


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


def _try_windows_range_lock(descriptor: int, offset: int, length: int) -> bool:
    import msvcrt

    try:
        os.lseek(descriptor, offset, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, length)
    except OSError:
        return False
    return True


def _unlock_windows_range(descriptor: int, offset: int, length: int) -> None:
    import msvcrt

    os.lseek(descriptor, offset, os.SEEK_SET)
    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, length)


class ApplicationSession:
    """Lifetime shared lease proving that a local backend is still active."""

    def __init__(self, descriptor: int, slot: int | None = None):
        self._descriptor = descriptor
        self._slot = slot

    @classmethod
    def acquire(cls, database: Path | str, timeout: float = 2.0):
        path = application_lock_path(database)
        descriptor = _open_control_file(path)
        transition_descriptor = None
        deadline = time.monotonic() + max(0.0, timeout)
        try:
            if os.fstat(descriptor).st_size < 256:
                os.ftruncate(descriptor, 256)
                os.fsync(descriptor)
            if os.name != "nt":
                transition_descriptor = _open_control_file(
                    application_transition_lock_path(database))
                while not _try_lock(transition_descriptor, exclusive=True):
                    if time.monotonic() >= deadline:
                        raise MaintenanceBusyError(
                            "local data startup transition is active; retry shortly")
                    time.sleep(0.02)
                while not _try_lock(descriptor, exclusive=False):
                    if time.monotonic() >= deadline:
                        raise MaintenanceBusyError(
                            "local data maintenance is active; retry shortly")
                    time.sleep(0.02)
                _unlock(transition_descriptor)
                os.close(transition_descriptor)
                transition_descriptor = None
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
            if transition_descriptor is not None:
                try:
                    _unlock(transition_descriptor)
                except OSError:
                    pass
                os.close(transition_descriptor)
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


class InactiveApplicationLease:
    """Exclusive application lease that can atomically become a session."""

    def __init__(self, descriptor: int,
                 transition_descriptor: int | None = None):
        self._descriptor = descriptor
        self._transition_descriptor = transition_descriptor

    @classmethod
    def acquire(cls, database: Path | str, timeout: float = 2.0):
        descriptor = _open_control_file(application_lock_path(database))
        transition_descriptor = None
        deadline = time.monotonic() + max(0.0, timeout)
        try:
            if os.fstat(descriptor).st_size < 256:
                os.ftruncate(descriptor, 256)
                os.fsync(descriptor)
            if os.name != "nt":
                transition_descriptor = _open_control_file(
                    application_transition_lock_path(database))
                while not _try_lock(transition_descriptor, exclusive=True):
                    if time.monotonic() >= deadline:
                        raise MaintenanceBusyError(
                            "Classic Games is starting; retry data maintenance")
                    time.sleep(0.02)
            while True:
                if os.name == "nt":
                    acquired = _try_windows_range_lock(descriptor, 0, 1)
                    if acquired:
                        acquired = _try_windows_range_lock(descriptor, 1, 255)
                        if not acquired:
                            _unlock_windows_range(descriptor, 0, 1)
                else:
                    acquired = _try_lock(descriptor, exclusive=True)
                if acquired:
                    return cls(descriptor, transition_descriptor)
                if time.monotonic() >= deadline:
                    raise MaintenanceBusyError(
                        "Classic Games is active; close it before data maintenance")
                time.sleep(0.02)
        except Exception:
            if transition_descriptor is not None:
                try:
                    _unlock(transition_descriptor)
                except OSError:
                    pass
                os.close(transition_descriptor)
            os.close(descriptor)
            raise

    def handoff(self) -> ApplicationSession:
        """Downgrade without exposing an interval to another maintainer."""
        descriptor = self._descriptor
        if descriptor < 0:
            raise RuntimeError("inactive application lease is already closed")
        if os.name == "nt":
            # Byte 0 is a transition gate used by every maintenance process.
            # Keep it while releasing the application range and acquiring our
            # shared slot, then release the gate last.
            _unlock_windows_range(descriptor, 1, 255)
            slot = next(
                (candidate for candidate in range(1, 256)
                 if _try_windows_range_lock(descriptor, candidate, 1)),
                None)
            if slot is None:
                _unlock_windows_range(descriptor, 0, 1)
                raise MaintenanceBusyError(
                    "could not complete the application lease handoff")
            _unlock_windows_range(descriptor, 0, 1)
        else:
            import fcntl

            # flock conversion itself may temporarily remove the old lock.
            # Every cooperating POSIX acquirer first takes the separate
            # transition gate, which remains held across this conversion.
            fcntl.flock(descriptor, fcntl.LOCK_SH)
            slot = None
            transition_descriptor = self._transition_descriptor
            self._transition_descriptor = None
            if transition_descriptor is not None:
                _unlock(transition_descriptor)
                os.close(transition_descriptor)
        self._descriptor = -1
        return ApplicationSession(descriptor, slot)

    def close(self) -> None:
        descriptor = self._descriptor
        if descriptor < 0:
            return
        self._descriptor = -1
        try:
            if os.name == "nt":
                _unlock_windows_range(descriptor, 1, 255)
                _unlock_windows_range(descriptor, 0, 1)
            else:
                _unlock(descriptor)
        finally:
            os.close(descriptor)
            transition_descriptor = self._transition_descriptor
            self._transition_descriptor = None
            if transition_descriptor is not None:
                try:
                    _unlock(transition_descriptor)
                finally:
                    os.close(transition_descriptor)


@contextmanager
def inactive_application_lock(database: Path | str, timeout: float = 2.0):
    """Hold an exclusive lease after every cooperating backend has closed."""
    lease = InactiveApplicationLease.acquire(database, timeout=timeout)
    try:
        yield lease
    finally:
        lease.close()


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
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        remaining = max(0.0, deadline - time.monotonic())
        with inactive_application_lock(database, timeout=remaining) as inactive:
            with maintenance_lock(database, exclusive=True, timeout=remaining):
                # Imported lazily so the recovery module can keep using
                # StoreError without creating an import cycle at module load.
                from .import_transaction import (
                    has_import_transaction_roots,
                    recover_import_transactions,
                )

                recover_import_transactions(database)
            session = inactive.handoff()
        try:
            # Defense in depth for platforms or remote filesystems with
            # different conversion behavior. While this SH lease is held, no
            # cooperating importer can add a new transaction root.
            if not has_import_transaction_roots(database):
                return session
        except Exception:
            session.close()
            raise
        session.close()
        if time.monotonic() >= deadline:
            raise MaintenanceBusyError(
                "an import transaction appeared during application startup")
