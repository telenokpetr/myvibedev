"""Локальная запись митинга через ffmpeg: снимает экран Xvfb (что видит бот —
галерея участников + чат) со звуком из PulseAudio. Userspace, не зависит от
Zoom-плана (облачная запись Zoom требует платный аккаунт).

Пауза — SIGSTOP/SIGCONT процессу ffmpeg (замораживает захват; при resume время
продолжается без вставки паузы — для v1 достаточно). Стоп — SIGINT, чтобы
ffmpeg корректно финализировал mp4 (moov-атом).
"""

import logging
import os
import signal
import subprocess
import time

log = logging.getLogger("recorder")

REC_DIR = os.environ.get("RECORDINGS_DIR", "/data/recordings")
DISPLAY = os.environ.get("DISPLAY", ":99")
GEOMETRY = os.environ.get("REC_GEOMETRY", "1280x800")


class ScreenRecorder:
    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._path: str | None = None
        self._paused = False

    @property
    def active(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> dict:
        if self.active:
            return {"ok": False, "error": "уже идёт запись", "path": self._path}
        os.makedirs(REC_DIR, exist_ok=True)
        self._path = os.path.join(REC_DIR, f"rec_{int(time.time())}.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-f", "x11grab", "-video_size", GEOMETRY, "-framerate", "15",
            "-i", DISPLAY,
            # звук митинга — из PulseAudio (что играет Chromium). Если тишина —
            # видео всё равно пишется; аудио-роутинг допилим с музыкой.
            "-f", "pulse", "-i", "default",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            self._path,
        ]
        self._proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        self._paused = False
        log.info("запись начата: %s", self._path)
        return {"ok": True, "path": self._path}

    def pause(self) -> dict:
        if not self.active or self._paused:
            return {"ok": False}
        self._proc.send_signal(signal.SIGSTOP)
        self._paused = True
        log.info("запись на паузе")
        return {"ok": True}

    def resume(self) -> dict:
        if not self.active or not self._paused:
            return {"ok": False}
        self._proc.send_signal(signal.SIGCONT)
        self._paused = False
        log.info("запись возобновлена")
        return {"ok": True}

    def stop(self) -> dict:
        if not self.active:
            return {"ok": False, "error": "запись не идёт"}
        if self._paused:
            self._proc.send_signal(signal.SIGCONT)
        try:
            # 'q' в stdin — мягкая остановка ffmpeg (финализирует mp4)
            self._proc.communicate(input=b"q", timeout=10)
        except Exception:  # noqa: BLE001
            self._proc.send_signal(signal.SIGINT)
            try:
                self._proc.wait(timeout=8)
            except Exception:  # noqa: BLE001
                self._proc.kill()
        path = self._path
        self._proc = None
        self._paused = False
        log.info("запись остановлена: %s", path)
        return {"ok": True, "path": path}

    def status(self) -> dict:
        return {"active": self.active, "paused": self._paused, "path": self._path}


recorder = ScreenRecorder()
