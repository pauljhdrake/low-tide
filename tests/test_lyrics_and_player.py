from __future__ import annotations

import os
import stat

from lowtide import player
from lowtide.lyrics import current_line_index, parse_lrc


def test_parse_lrc_sorts_and_skips_blank_lines():
    lines = parse_lrc("[00:12.50]second\n[00:01.00]first\n[00:20.00]\nnot a timestamp")
    assert [(l.timestamp, l.text) for l in lines] == [(1.0, "first"), (12.5, "second")]


def test_current_line_index():
    lines = parse_lrc("[00:01.00]a\n[00:05.00]b\n[00:09.00]c")
    assert current_line_index(lines, 0.0) == 0
    assert current_line_index(lines, 6.0) == 1
    assert current_line_index(lines, 99.0) == 2


def test_socket_in_runtime_dir_is_per_process():
    path, private_dir = player._make_socket_path()
    assert os.path.dirname(path) == os.environ["XDG_RUNTIME_DIR"]
    assert str(os.getpid()) in os.path.basename(path)
    assert private_dir is None


def test_socket_fallback_uses_private_dir(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    path, private_dir = player._make_socket_path()
    try:
        assert os.path.dirname(path) == private_dir
        assert stat.S_IMODE(os.stat(private_dir).st_mode) == 0o700
    finally:
        os.rmdir(private_dir)


def test_player_passes_its_socket_to_mpv():
    p = player.Player({})
    assert f"--input-ipc-server={p._socket_path}" in p._mpv_args


def test_mpv_args_from_config():
    args = player._build_mpv_args({
        "replaygain": "bogus", "alsa_output": True, "alsa_bit_depth": 24,
        "alsa_samplerate": 96000, "alsa_device": "hw:0,0",
    })
    assert "--replaygain=album" in args  # invalid mode falls back
    assert "--ao=alsa" in args
    assert "--audio-format=s24" in args
    assert "--audio-samplerate=96000" in args
    assert "--audio-device=alsa/hw:0,0" in args
