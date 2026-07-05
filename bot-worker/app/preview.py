"""Живой A/V-поток экрана+звука для превью в браузере (jsmpeg / MPEG-TS).

ffmpeg захватывает Xvfb-дисплей и звук с виртуального PulseAudio-sink, кодирует
в MPEG-TS (mpeg1video + mp2) и отдаёт в stdout. Мы читаем поток и рассылаем всем
подключённым WebSocket-клиентам. ffmpeg запускается лениво — только когда есть
зрители, и гасится, когда последний отключился.
"""

import asyncio
import os

from app.config import config

ENV = {**os.environ, "DISPLAY": config.display}

# Размер окна превью (небольшое, чтобы щадить CPU).
PREVIEW_WIDTH = int(os.environ.get("PREVIEW_WIDTH", "480"))
PREVIEW_HEIGHT = int(os.environ.get("PREVIEW_HEIGHT", "320"))
# MPEG-1 допускает только 24/25/30 fps — 15 нельзя.
PREVIEW_FPS = os.environ.get("PREVIEW_FPS", "25")


class PreviewStreamer:
    def __init__(self) -> None:
        self.clients: set = set()
        self.proc: asyncio.subprocess.Process | None = None
        self.task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def _cmd(self) -> list[str]:
        return [
            "ffmpeg", "-loglevel", "error",
            "-thread_queue_size", "512",
            "-f", "x11grab",
            "-video_size", f"{config.width}x{config.height}",
            "-framerate", PREVIEW_FPS, "-i", config.display,
            "-thread_queue_size", "512",
            "-f", "pulse", "-i", "vspeaker.monitor",
            "-f", "mpegts",
            "-codec:v", "mpeg1video",
            "-s", f"{PREVIEW_WIDTH}x{PREVIEW_HEIGHT}",
            "-b:v", "700k", "-bf", "0",
            "-codec:a", "mp2", "-ar", "44100", "-ac", "2", "-b:a", "128k",
            "-muxdelay", "0.001",
            "pipe:1",
        ]

    async def _start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            *self._cmd(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=ENV,
        )
        self.task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        try:
            assert self.proc and self.proc.stdout
            while True:
                chunk = await self.proc.stdout.read(8192)
                if not chunk:
                    break
                dead = []
                for ws in list(self.clients):
                    try:
                        await ws.send_bytes(chunk)
                    except Exception:  # noqa: BLE001
                        dead.append(ws)
                for ws in dead:
                    self.clients.discard(ws)
        except asyncio.CancelledError:
            pass

    async def _stop(self) -> None:
        if self.task:
            self.task.cancel()
            self.task = None
        if self.proc:
            try:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    self.proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            self.proc = None

    async def add(self, ws) -> None:
        async with self._lock:
            self.clients.add(ws)
            if not self.proc:
                await self._start()

    async def remove(self, ws) -> None:
        async with self._lock:
            self.clients.discard(ws)
            if not self.clients:
                await self._stop()


preview = PreviewStreamer()
