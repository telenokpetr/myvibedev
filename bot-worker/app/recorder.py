"""Запись экрана (Xvfb) + звука митинга (PulseAudio) в mp4 через ffmpeg."""

import os
import signal
import subprocess
from datetime import datetime, timezone

from app.config import config

ENV = {**os.environ, "DISPLAY": config.display}


class Recorder:
    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._path: str | None = None
        self._paused: bool = False

    @property
    def active(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def path(self) -> str | None:
        return self._path

    def start(self, name_hint: str = "event") -> str:
        if self.active:
            return self._path  # уже пишем

        os.makedirs(config.recordings_dir, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        safe = "".join(c for c in name_hint if c.isalnum() or c in "-_") or "event"
        self._path = os.path.join(config.recordings_dir, f"{safe}-{ts}.mp4")

        cmd = [
            "ffmpeg", "-y",
            "-f", "x11grab",
            "-video_size", f"{config.width}x{config.height}",
            "-framerate", "15",
            "-i", config.display,
            "-f", "pulse",
            "-i", "vspeaker.monitor",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            self._path,
        ]
        self._proc = subprocess.Popen(
            cmd, env=ENV, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return self._path

    def pause(self) -> bool:
        """Пауза локальной записи: замораживаем ffmpeg сигналом SIGSTOP."""
        if not self.active or self._paused:
            return False
        self._proc.send_signal(signal.SIGSTOP)
        self._paused = True
        return True

    def resume(self) -> bool:
        if not self.active or not self._paused:
            return False
        self._proc.send_signal(signal.SIGCONT)
        self._paused = False
        return True

    def stop(self) -> str | None:
        if not self.active:
            return self._path
        # На паузе процесс заморожен — сначала разбудим, иначе не примет 'q'.
        if self._paused:
            self._proc.send_signal(signal.SIGCONT)
            self._paused = False
        # 'q' в stdin — корректное завершение ffmpeg (закрывает контейнер).
        try:
            self._proc.communicate(input=b"q", timeout=10)
        except Exception:  # noqa: BLE001
            self._proc.send_signal(signal.SIGINT)
            try:
                self._proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                self._proc.kill()
        path = self._path
        self._proc = None
        self._paused = False
        return path


recorder = Recorder()
