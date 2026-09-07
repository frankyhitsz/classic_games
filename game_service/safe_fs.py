"""Small no-follow and atomic-publication primitives shared by maintenance."""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import sys
from pathlib import Path


def is_reparse(metadata) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def is_safe_directory(metadata) -> bool:
    return stat.S_ISDIR(metadata.st_mode) and not is_reparse(metadata)


def is_safe_regular(metadata) -> bool:
    return (stat.S_ISREG(metadata.st_mode) and not is_reparse(metadata)
            and metadata.st_nlink == 1)


def rename_noreplace(source: Path, target: Path) -> None:
    """Atomic no-clobber rename. Unsupported filesystems fail before publishing.

    Do not emulate this with link/unlink or an O_EXCL copy: both expose an
    unreadable final filename if the process dies halfway through publication.
    """
    if os.name == "nt":
        os.rename(source, target)
        return
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = library.renamex_np
        rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        args = (os.fsencode(source), os.fsencode(target), 4)  # RENAME_EXCL
    elif sys.platform.startswith("linux") and hasattr(library, "renameat2"):
        rename = library.renameat2
        rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                           ctypes.c_char_p, ctypes.c_uint)
        args = (-100, os.fsencode(source), -100, os.fsencode(target), 1)
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace rename unavailable")
    rename.restype = ctypes.c_int
    if rename(*args) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(target))
