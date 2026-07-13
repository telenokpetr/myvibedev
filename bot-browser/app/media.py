"""Виртуальные камера и микрофон для Zoom web через fake-file устройства
Chromium — userspace, без v4l2loopback/kernel-модулей (работает в Docker/WSL2).

Камера: файл .y4m отдаётся как видеопоток (Chromium зацикливает).
Микрофон: файл .wav отдаётся как аудиовход. Для гибкого плейлиста музыки
позже вместо статичного wav подключим PulseAudio sink + mpv; сейчас — заставка
и звук для проверки, что Zoom web их принимает и не глушит.
"""

import logging
import os
import subprocess

log = logging.getLogger("media")

CAM = "/data/cam.y4m"
MIC = "/data/mic.wav"


def ensure_assets(caption: str = "Тех. поддержка") -> None:
    """Сгенерировать дефолтную видео-заставку и аудио, если их ещё нет."""
    if not os.path.exists(CAM):
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            "color=c=navy:s=640x480:d=2,format=yuv420p",
            "-vf", f"drawtext=text='{caption}':fontcolor=white:fontsize=40:"
                   "x=(w-tw)/2:y=(h-th)/2", CAM,
        ], check=True, capture_output=True)
        log.info("видео-заставка создана: %s", CAM)
    if not os.path.exists(MIC):
        # тихий фон 2 c (для проверки тракта; музыку зальём позже)
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "anullsrc=r=48000:cl=mono", "-t", "2", MIC,
        ], check=True, capture_output=True)
        log.info("аудио-заглушка создана: %s", MIC)


def launch_args() -> list[str]:
    """Флаги Chromium для подачи файлов как камеры и микрофона."""
    return [
        "--use-fake-ui-for-media-stream",      # авто-грант разрешений
        "--use-fake-device-for-media-stream",
        f"--use-file-for-fake-video-capture={CAM}",
        f"--use-file-for-fake-audio-capture={MIC}",
    ]
