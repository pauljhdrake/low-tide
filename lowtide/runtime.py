"""Private per-process runtime workspace for mpv IPC and DASH manifests."""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from lowtide.secure_io import PRIVATE_DIR_MODE, ensure_private_dir


def _usable_xdg_runtime_dir(value: str | None) -> str | None:
    if not value:
        return None
    path = os.path.abspath(value)
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if (
        stat.S_ISDIR(st.st_mode)
        and not stat.S_ISLNK(st.st_mode)
        and st.st_uid == os.getuid()
    ):
        return path
    return None


@dataclass
class RuntimeWorkspace:
    """Paths created by this instance and safe cleanup metadata for them."""

    directory: str
    socket_path: str
    manifest_dir: str
    _identity: tuple[int, int]
    _manifest_paths: set[str] = field(default_factory=set)

    @classmethod
    def create(cls) -> "RuntimeWorkspace":
        xdg_root = _usable_xdg_runtime_dir(os.environ.get("XDG_RUNTIME_DIR"))
        if xdg_root:
            app_root = ensure_private_dir(os.path.join(xdg_root, "low-tide"))
            directory = tempfile.mkdtemp(
                prefix=f"{os.getpid()}-",
                dir=app_root,
            )
        else:
            directory = tempfile.mkdtemp(prefix=f"low-tide-{os.getuid()}-{os.getpid()}-")
        os.chmod(directory, PRIVATE_DIR_MODE)
        st = os.lstat(directory)
        manifest_dir = os.path.join(directory, "manifests")
        os.mkdir(manifest_dir, PRIVATE_DIR_MODE)
        socket_path = os.path.join(directory, "mpv.sock")
        # Linux sockaddr_un.sun_path is normally 108 bytes including NUL.
        if len(os.fsencode(socket_path)) >= 104:
            os.rmdir(manifest_dir)
            os.rmdir(directory)
            directory = tempfile.mkdtemp(
                prefix=f"low-tide-{os.getuid()}-{os.getpid()}-"
            )
            os.chmod(directory, PRIVATE_DIR_MODE)
            st = os.lstat(directory)
            manifest_dir = os.path.join(directory, "manifests")
            os.mkdir(manifest_dir, PRIVATE_DIR_MODE)
            socket_path = os.path.join(directory, "mpv.sock")
        return cls(
            directory=directory,
            socket_path=socket_path,
            manifest_dir=manifest_dir,
            _identity=(st.st_dev, st.st_ino),
        )

    def track_manifest(self, path: str) -> None:
        candidate = os.path.abspath(path)
        if os.path.dirname(candidate) != self.manifest_dir:
            raise RuntimeError("manifest path escaped private runtime directory")
        self._manifest_paths.add(candidate)

    def remove_manifest(self, path: str) -> None:
        candidate = os.path.abspath(path)
        if candidate not in self._manifest_paths:
            return
        try:
            st = os.lstat(candidate)
            if stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid():
                os.unlink(candidate)
        except FileNotFoundError:
            pass
        finally:
            self._manifest_paths.discard(candidate)

    def cleanup_manifests(self) -> None:
        for path in tuple(self._manifest_paths):
            self.remove_manifest(path)

    def _verified(self) -> bool:
        try:
            st = os.lstat(self.directory)
        except FileNotFoundError:
            return False
        return (
            stat.S_ISDIR(st.st_mode)
            and not stat.S_ISLNK(st.st_mode)
            and st.st_uid == os.getuid()
            and (st.st_dev, st.st_ino) == self._identity
        )

    def cleanup(self) -> None:
        """Remove only resources recorded as belonging to this instance."""
        if not self._verified():
            return
        self.cleanup_manifests()
        try:
            os.rmdir(self.manifest_dir)
        except OSError:
            pass
        try:
            socket_st = os.lstat(self.socket_path)
            if stat.S_ISSOCK(socket_st.st_mode) and socket_st.st_uid == os.getuid():
                os.unlink(self.socket_path)
        except FileNotFoundError:
            pass
        try:
            os.rmdir(self.directory)
        except OSError:
            pass

    @property
    def uses_xdg_runtime(self) -> bool:
        root = _usable_xdg_runtime_dir(os.environ.get("XDG_RUNTIME_DIR"))
        return bool(root and Path(self.directory).is_relative_to(root))
