from __future__ import annotations

import asyncio
import inspect
import os
import stat

from lowtide import player as player_module
from lowtide.player import MPV_BASE_ARGS, Player, _build_mpv_args
from lowtide.runtime import RuntimeWorkspace


def test_mpv_arguments_are_isolated_and_not_shell_strings(tmp_path):
    socket_path = str(tmp_path / "mpv.sock")
    args = _build_mpv_args(
        {
            "replaygain": "no",
            "alsa_output": False,
            "alsa_device": "--script=unsafe",
        },
        socket_path,
    )
    for expected in (
        "--no-config",
        "--load-scripts=no",
        "--input-terminal=no",
        "--terminal=no",
        "--tls-verify=yes",
        "--network-timeout=15",
        "--ytdl=no",
        "--replaygain=no",
    ):
        assert expected in args
    assert f"--input-ipc-server={socket_path}" in args
    assert not any(arg.startswith("--audio-device=") for arg in args)
    assert "shell=True" not in inspect.getsource(player_module)


def test_numeric_configuration_is_bounded(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime_root))
    workspace = RuntimeWorkspace.create()
    try:
        player = Player({"volume": 101, "crossfade": "bad"}, runtime=workspace)
        assert player.volume == 80
        assert player.crossfade_secs == 0
    finally:
        workspace.cleanup()


def test_real_mpv_socket_is_private_and_removed(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime_root))
    workspace = RuntimeWorkspace.create()
    player = Player(
        {"volume": 100, "replaygain": "no", "crossfade": 0},
        runtime=workspace,
    )

    async def scenario():
        await player.start()
        socket_st = os.lstat(player.socket_path)
        assert stat.S_ISSOCK(socket_st.st_mode)
        assert stat.S_IMODE(socket_st.st_mode) == 0o600
        assert socket_st.st_uid == os.getuid()
        assert await player.get_property("volume") == 100
        await player.shutdown()

    try:
        asyncio.run(scenario())
        assert not os.path.exists(player.socket_path)
    finally:
        workspace.cleanup()


def test_required_mpv_options_exist_in_installed_version():
    assert "--no-config" in MPV_BASE_ARGS
    assert "--load-scripts=no" in MPV_BASE_ARGS
