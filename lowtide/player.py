from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
import tempfile
from typing import Callable, Optional

_REPLAYGAIN_MODES = {"track", "album", "no"}

MPV_BASE_ARGS = [
    "mpv",
    "--no-video",
    "--idle=yes",
    "--really-quiet",
    "--prefetch-playlist=yes",
    "--gapless-audio=yes",
    # Hi-res tracks are DASH: a local .mpd manifest whose segments are fetched
    # over https. ffmpeg's demuxer only allows file/crypto/data by default for
    # local manifests, so https/tls/tcp must be whitelisted. Harmless for the
    # direct stream URLs used by lossless and below.
    "--demuxer-lavf-o=protocol_whitelist=[file,crypto,data,https,tls,tcp]",
]


def _build_mpv_args(config: dict) -> list[str]:
    args = list(MPV_BASE_ARGS)

    # macOS: prevent mpv from creating a visible window when video-add
    # injects album art tracks. --vo=null skips video rendering;
    # --macos-app-activation-policy=prohibited prevents the Cocoa app
    # from creating any window at all (media keys and Now Playing info
    # center work independently).
    if sys.platform == "darwin":
        args.append("--vo=null")
        args.append("--macos-app-activation-policy=prohibited")

    rg = config.get("replaygain", "album")
    if rg not in _REPLAYGAIN_MODES:
        rg = "album"
    args.append(f"--replaygain={rg}")
    args.append("--replaygain-clip=yes")

    if config.get("alsa_output"):
        args.append("--ao=alsa")
        if bit_depth := config.get("alsa_bit_depth"):
            fmt = {16: "s16", 24: "s24", 32: "s32"}.get(int(bit_depth), "s32")
            args.append(f"--audio-format={fmt}")
        if samplerate := config.get("alsa_samplerate"):
            args.append(f"--audio-samplerate={int(samplerate)}")
        if device := config.get("alsa_device"):
            args.append(f"--audio-device=alsa/{device}")

    return args


def _make_socket_path() -> tuple[str, str | None]:
    """Return (socket_path, private_dir). The socket is per process so several
    instances don't clobber each other's IPC. $XDG_RUNTIME_DIR is already
    private to the user; elsewhere (e.g. macOS) a private temp dir is created,
    returned as private_dir so it can be removed on shutdown."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir and os.path.isdir(runtime_dir):
        return os.path.join(runtime_dir, f"lowtide-mpv-{os.getpid()}.sock"), None
    private_dir = tempfile.mkdtemp(prefix="lowtide-")
    return os.path.join(private_dir, "mpv.sock"), private_dir


class Player:
    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self._socket_path, self._socket_dir = _make_socket_path()
        self._mpv_args = _build_mpv_args(cfg) + [f"--input-ipc-server={self._socket_path}"]
        self.crossfade_secs: int = int(cfg.get("crossfade", 0))
        self._process: Optional[asyncio.subprocess.Process] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._req_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._read_task: Optional[asyncio.Task] = None
        self.volume: int = 80
        self.shuffle_mode: int = 0  # 0=off 1=random 2=favourite 3=discovery
        self.repeat: bool = False

        # Callbacks fired on mpv events
        self.on_track_start: list[Callable] = []
        self.on_track_end: list[Callable] = []

    async def start(self) -> None:
        if Path(self._socket_path).exists():
            os.unlink(self._socket_path)

        self._process = await asyncio.create_subprocess_exec(
            *self._mpv_args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        for _ in range(50):
            if Path(self._socket_path).exists():
                break
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("mpv IPC socket did not appear – is mpv installed?")

        self._reader, self._writer = await asyncio.open_unix_connection(self._socket_path)
        self._read_task = asyncio.create_task(self._read_loop())
        await self.set_volume(self.volume)

    async def _read_loop(self) -> None:
        while True:
            try:
                line = await self._reader.readline()
                if not line:
                    break
                data = json.loads(line.decode())
                req_id = data.get("request_id")
                if req_id is not None:
                    fut = self._pending.pop(req_id, None)
                    if fut and not fut.done():
                        fut.set_result(data.get("data"))
                event = data.get("event")
                if event == "file-loaded":
                    for cb in self.on_track_start:
                        asyncio.create_task(cb())
                elif event == "end-file":
                    for cb in self.on_track_end:
                        asyncio.create_task(cb())
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    async def _cmd(self, command: list, *, wait: bool = False):
        if not self._writer:
            return None
        self._req_id += 1
        req_id = self._req_id
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[req_id] = fut
        msg = json.dumps({"command": command, "request_id": req_id}) + "\n"
        self._writer.write(msg.encode())
        try:
            await self._writer.drain()
        except (ConnectionResetError, BrokenPipeError, OSError):
            self._pending.pop(req_id, None)
            fut.cancel()
            return None
        if wait:
            try:
                return await asyncio.wait_for(asyncio.shield(fut), timeout=3.0)
            except asyncio.TimeoutError:
                self._pending.pop(req_id, None)
                return None
        return None

    # --- Playback controls ---

    async def play(self, url: str) -> None:
        await self._cmd(["loadfile", url, "replace"])

    async def append(self, url: str) -> None:
        await self._cmd(["loadfile", url, "append"])

    async def toggle_pause(self) -> None:
        await self._cmd(["cycle", "pause"])

    async def set_paused(self, paused: bool) -> None:
        await self._cmd(["set_property", "pause", paused])

    async def next(self) -> None:
        await self._cmd(["playlist-next", "force"])

    async def prev(self) -> None:
        await self._cmd(["playlist-prev", "force"])

    async def seek(self, seconds: float) -> None:
        await self._cmd(["seek", seconds, "absolute"])

    async def stop(self) -> None:
        await self._cmd(["stop"])

    async def toggle_repeat(self) -> None:
        self.repeat = not self.repeat
        value = "inf" if self.repeat else "no"
        await self._cmd(["set_property", "loop-playlist", value])

    # --- Volume ---

    async def set_volume(self, level: int) -> None:
        self.volume = max(0, min(100, level))
        await self._cmd(["set_property", "volume", self.volume])

    # --- State queries ---

    async def get_property(self, prop: str):
        return await self._cmd(["get_property", prop], wait=True)

    async def get_position(self) -> float:
        val = await self.get_property("time-pos")
        return val if isinstance(val, (int, float)) else 0.0

    async def get_duration(self) -> float:
        val = await self.get_property("duration")
        return val if isinstance(val, (int, float)) else 0.0

    async def get_paused(self) -> bool:
        return bool(await self.get_property("pause"))

    async def get_playlist_pos(self) -> int:
        val = await self.get_property("playlist-pos")
        return val if isinstance(val, int) else 0

    # --- Teardown ---

    async def shutdown(self) -> None:
        if self._read_task:
            self._read_task.cancel()
        if self._writer:
            try:
                self._writer.close()
            except Exception:
                pass
        if self._process:
            try:
                self._process.terminate()
                await self._process.wait()
            except Exception:
                pass
        if Path(self._socket_path).exists():
            os.unlink(self._socket_path)
        if self._socket_dir:
            try:
                os.rmdir(self._socket_dir)
            except OSError:
                pass
