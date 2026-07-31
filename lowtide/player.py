from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import sys
from typing import Callable, Optional

from lowtide.runtime import RuntimeWorkspace

log = logging.getLogger(__name__)

_REPLAYGAIN_MODES = {"track", "album", "no"}

MPV_BASE_ARGS = [
    "mpv",
    "--no-config",
    "--load-scripts=no",
    "--input-terminal=no",
    "--terminal=no",
    "--tls-verify=yes",
    "--network-timeout=15",
    "--ytdl=no",
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


def _build_mpv_args(config: dict, socket_path: str) -> list[str]:
    args = list(MPV_BASE_ARGS)
    args.append(f"--input-ipc-server={socket_path}")

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
            if bit_depth in (16, 24, 32):
                fmt = {16: "s16", 24: "s24", 32: "s32"}[bit_depth]
                args.append(f"--audio-format={fmt}")
        if samplerate := config.get("alsa_samplerate"):
            if (
                isinstance(samplerate, int)
                and not isinstance(samplerate, bool)
                and 8_000 <= samplerate <= 768_000
            ):
                args.append(f"--audio-samplerate={samplerate}")
        if device := config.get("alsa_device"):
            if (
                isinstance(device, str)
                and 0 < len(device) <= 128
                and all(ch.isalnum() or ch in "_.:,=+-" for ch in device)
            ):
                args.append(f"--audio-device=alsa/{device}")

    return args


class Player:
    def __init__(
        self,
        config: dict | None = None,
        runtime: RuntimeWorkspace | None = None,
    ):
        cfg = config or {}
        self.runtime = runtime or RuntimeWorkspace.create()
        self._owns_runtime = runtime is None
        self.socket_path = self.runtime.socket_path
        self._mpv_args = _build_mpv_args(cfg, self.socket_path)
        crossfade = cfg.get("crossfade", 0)
        self.crossfade_secs = (
            crossfade
            if isinstance(crossfade, int)
            and not isinstance(crossfade, bool)
            and 0 <= crossfade <= 30
            else 0
        )
        self._process: Optional[asyncio.subprocess.Process] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._req_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._read_task: Optional[asyncio.Task] = None
        volume = cfg.get("volume", 80)
        self.volume = (
            volume
            if isinstance(volume, int)
            and not isinstance(volume, bool)
            and 0 <= volume <= 100
            else 80
        )
        self.shuffle_mode: int = 0  # 0=off 1=random 2=favourite 3=discovery
        self.repeat: bool = False
        self._socket_identity: tuple[int, int] | None = None

        # Callbacks fired on mpv events
        self.on_track_start: list[Callable] = []
        self.on_track_end: list[Callable] = []

    async def start(self) -> None:
        try:
            os.lstat(self.socket_path)
        except FileNotFoundError:
            pass
        else:
            raise RuntimeError("refusing an existing mpv IPC path in this instance")

        self._process = await asyncio.create_subprocess_exec(
            *self._mpv_args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        for _ in range(50):
            if self._process.returncode is not None:
                raise RuntimeError(f"mpv exited before creating IPC (status {self._process.returncode})")
            try:
                socket_st = os.lstat(self.socket_path)
            except FileNotFoundError:
                await asyncio.sleep(0.1)
                continue
            if not stat.S_ISSOCK(socket_st.st_mode) or socket_st.st_uid != os.getuid():
                await self._terminate_process()
                raise RuntimeError("mpv IPC path was not a user-owned Unix socket")
            os.chmod(self.socket_path, 0o600, follow_symlinks=False)
            socket_st = os.lstat(self.socket_path)
            if stat.S_IMODE(socket_st.st_mode) != 0o600:
                await self._terminate_process()
                raise RuntimeError("mpv IPC socket permissions could not be restricted")
            self._socket_identity = (socket_st.st_dev, socket_st.st_ino)
            break
        else:
            await self._terminate_process()
            raise RuntimeError("mpv IPC socket did not appear – is mpv installed?")

        self._reader, self._writer = await asyncio.open_unix_connection(self.socket_path)
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
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                log.warning("invalid response from mpv IPC (%s)", type(error).__name__)
            except (ConnectionError, OSError) as error:
                log.warning("mpv IPC read failed (%s)", type(error).__name__)
                break

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

    async def pause(self) -> None:
        await self._cmd(["set_property", "pause", True])

    async def resume(self) -> None:
        await self._cmd(["set_property", "pause", False])

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

    async def _terminate_process(self) -> None:
        if not self._process or self._process.returncode is not None:
            return
        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=3)
        except asyncio.TimeoutError:
            self._process.kill()
            await self._process.wait()

    async def shutdown(self) -> None:
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except (ConnectionError, OSError):
                log.warning("mpv IPC writer did not close cleanly")
        await self._terminate_process()
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        if self._socket_identity is not None:
            try:
                socket_st = os.lstat(self.socket_path)
                if (
                    stat.S_ISSOCK(socket_st.st_mode)
                    and socket_st.st_uid == os.getuid()
                    and (socket_st.st_dev, socket_st.st_ino) == self._socket_identity
                ):
                    os.unlink(self.socket_path)
            except FileNotFoundError:
                pass
        if self._owns_runtime:
            self.runtime.cleanup()
