"""Private, atomic persistence helpers for low-tide state.

The files in the low-tide configuration directory can contain OAuth bearer
tokens, Last.fm credentials, listening history, and local filesystem paths.
Keep all of them private and refuse filesystem objects that could redirect an
operation outside the expected directory.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
DEFAULT_MAX_JSON_BYTES = 16 * 1024 * 1024

PRIVATE_JSON_NAMES = frozenset(
    {
        "config.json",
        "local_index.json",
        "playcounts.json",
        "queue.json",
        "scrobbles.json",
        "session.json",
    }
)


class UnsafePathError(RuntimeError):
    """Raised when a sensitive path is a symlink or has an unsafe owner/type."""


def get_config_dir() -> str:
    """Return the XDG-compliant per-user configuration directory."""
    root = os.environ.get("XDG_CONFIG_HOME")
    if not root:
        root = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.abspath(os.path.join(os.path.expanduser(root), "low-tide"))


def _require_owned(st: os.stat_result, path: str) -> None:
    if st.st_uid != os.getuid():
        raise UnsafePathError(f"refusing path not owned by current user: {path}")


def ensure_private_dir(path: str | os.PathLike[str]) -> str:
    """Create or repair a user-owned directory without accepting a symlink."""
    value = os.path.abspath(os.fspath(path))
    try:
        st = os.lstat(value)
    except FileNotFoundError:
        parent = os.path.dirname(value)
        os.makedirs(parent, exist_ok=True)
        try:
            os.mkdir(value, PRIVATE_DIR_MODE)
        except FileExistsError:
            # A concurrent creator won the race; validate it below.
            pass
        st = os.lstat(value)

    if stat.S_ISLNK(st.st_mode):
        raise UnsafePathError(f"refusing symlink for private directory: {value}")
    if not stat.S_ISDIR(st.st_mode):
        raise UnsafePathError(f"private path is not a directory: {value}")
    _require_owned(st, value)
    if stat.S_IMODE(st.st_mode) != PRIVATE_DIR_MODE:
        os.chmod(value, PRIVATE_DIR_MODE, follow_symlinks=False)
    return value


def ensure_private_config_dir() -> str:
    return ensure_private_dir(get_config_dir())


def _validate_regular_file(path: str, *, repair_mode: bool) -> os.stat_result:
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode):
        raise UnsafePathError(f"refusing symlink for private file: {path}")
    if not stat.S_ISREG(st.st_mode):
        raise UnsafePathError(f"private path is not a regular file: {path}")
    _require_owned(st, path)
    if repair_mode and stat.S_IMODE(st.st_mode) != PRIVATE_FILE_MODE:
        os.chmod(path, PRIVATE_FILE_MODE, follow_symlinks=False)
        st = os.lstat(path)
    return st


def repair_private_json_files(directory: str | None = None) -> list[str]:
    """Repair known legacy JSON modes, rejecting unsafe filesystem objects."""
    root = ensure_private_dir(directory or get_config_dir())
    repaired: list[str] = []
    for name in PRIVATE_JSON_NAMES:
        path = os.path.join(root, name)
        try:
            before = os.lstat(path)
        except FileNotFoundError:
            continue
        _validate_regular_file(path, repair_mode=True)
        if stat.S_IMODE(before.st_mode) != PRIVATE_FILE_MODE:
            repaired.append(name)
    return repaired


def read_json(
    path: str | os.PathLike[str],
    *,
    default: Any = None,
    max_bytes: int = DEFAULT_MAX_JSON_BYTES,
) -> Any:
    """Read a private JSON file with O_NOFOLLOW and a bounded input size."""
    value = os.path.abspath(os.fspath(path))
    ensure_private_dir(os.path.dirname(value))
    try:
        expected = _validate_regular_file(value, repair_mode=True)
    except FileNotFoundError:
        return default

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(value, flags)
    try:
        actual = os.fstat(fd)
        _require_owned(actual, value)
        if not stat.S_ISREG(actual.st_mode):
            raise UnsafePathError(f"private path is not a regular file: {value}")
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise UnsafePathError(f"private file changed while opening: {value}")
        if actual.st_size > max_bytes:
            raise ValueError(f"JSON state exceeds {max_bytes} bytes: {Path(value).name}")
        if stat.S_IMODE(actual.st_mode) != PRIVATE_FILE_MODE:
            os.fchmod(fd, PRIVATE_FILE_MODE)
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = -1
            return json.load(handle)
    finally:
        if fd >= 0:
            os.close(fd)


def atomic_write_json(path: str | os.PathLike[str], data: Any) -> None:
    """Atomically replace a private JSON file using a same-directory temp file."""
    value = os.path.abspath(os.fspath(path))
    directory = ensure_private_dir(os.path.dirname(value))
    try:
        _validate_regular_file(value, repair_mode=False)
    except FileNotFoundError:
        pass

    name = os.path.basename(value)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{name}.",
        suffix=".tmp",
        dir=directory,
        text=True,
    )
    replaced = False
    try:
        os.fchmod(fd, PRIVATE_FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            json.dump(data, handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, value)
        replaced = True
        final = _validate_regular_file(value, repair_mode=True)
        if stat.S_IMODE(final.st_mode) != PRIVATE_FILE_MODE:
            raise UnsafePathError(f"failed to protect private file: {value}")
        dir_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        if not replaced:
            try:
                st = os.lstat(temporary)
                if stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid():
                    os.unlink(temporary)
            except FileNotFoundError:
                pass
