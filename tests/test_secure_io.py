from __future__ import annotations

import json
import os
import stat

import pytest

from lowtide.secure_io import (
    UnsafePathError,
    atomic_write_json,
    ensure_private_config_dir,
    get_config_dir,
    read_json,
    repair_private_json_files,
)


def _mode(path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def test_xdg_config_permissions_and_atomic_write(monkeypatch, tmp_path):
    xdg = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    config_dir = ensure_private_config_dir()
    assert config_dir == str(xdg / "low-tide")
    assert _mode(config_dir) == 0o700

    path = os.path.join(config_dir, "config.json")
    atomic_write_json(path, {"quality": "max"})
    first_inode = os.lstat(path).st_ino
    assert _mode(path) == 0o600
    assert read_json(path) == {"quality": "max"}

    atomic_write_json(path, {"quality": "lossless"})
    assert os.lstat(path).st_ino != first_inode
    assert read_json(path) == {"quality": "lossless"}
    assert not list((xdg / "low-tide").glob(".config.json.*.tmp"))


def test_legacy_permissions_are_repaired(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    directory = get_config_dir()
    os.makedirs(directory, mode=0o755)
    path = os.path.join(directory, "session.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"access_token": "not-a-real-token"}, handle)
    os.chmod(path, 0o644)

    repaired = repair_private_json_files()
    assert repaired == ["session.json"]
    assert _mode(directory) == 0o700
    assert _mode(path) == 0o600


def test_sensitive_symlinks_are_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    directory = ensure_private_config_dir()
    target = tmp_path / "target.json"
    target.write_text('{"untouched": true}', encoding="utf-8")
    path = os.path.join(directory, "session.json")
    os.symlink(target, path)

    with pytest.raises(UnsafePathError):
        read_json(path)
    with pytest.raises(UnsafePathError):
        atomic_write_json(path, {"access_token": "secret"})
    assert target.read_text(encoding="utf-8") == '{"untouched": true}'


def test_atomic_failure_preserves_existing_file(monkeypatch, tmp_path):
    directory = tmp_path / "private"
    path = directory / "queue.json"
    atomic_write_json(path, {"old": True})

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError):
        atomic_write_json(path, {"new": True})
    assert read_json(path) == {"old": True}
    assert not list(directory.glob(".queue.json.*.tmp"))
