from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Callable, Optional

SOCKET_PATH = "/tmp/lowtide-mpv.sock"

MPV_ARGS = [
    "mpv",
    "--no-video",
    "--idle=yes",
    f"--input-ipc-server={SOCKET_PATH}",
    "--really-quiet",
    "--prefetch-playlist=yes",
]


class Player:
    def __init__(self):
        self._process: Optional[asyncio.subprocess.Process] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._req_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._read_task: Optional[asyncio.Task] = None
        self.volume: int = 80
        self.shuffle: bool = False
        self.repeat: bool = False

        # Callbacks fired on mpv events
        self.on_track_start: list[Callable] = []
        self.on_track_end: list[Callable] = []

    async def start(self) -> None:
        if Path(SOCKET_PATH).exists():
            os.unlink(SOCKET_PATH)

        self._process = await asyncio.create_subprocess_exec(
            *MPV_ARGS,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        for _ in range(50):
            if Path(SOCKET_PATH).exists():
                break
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("mpv IPC socket did not appear — is mpv installed?")

        self._reader, self._writer = await asyncio.open_unix_connection(SOCKET_PATH)
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
        await self._writer.drain()
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
        if Path(SOCKET_PATH).exists():
            os.unlink(SOCKET_PATH)
