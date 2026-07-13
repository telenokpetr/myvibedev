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
        # 10 c, 15 fps, 1280x720 — Chromium зацикливает файл как камеру.
        # Явный fps/размер: слишком короткий/нестандартный y4m Zoom иногда
        # показывал чёрным кадром.
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            "color=c=navy:s=1280x720:d=10:r=15,format=yuv420p",
            "-vf", f"drawtext=text='{caption}':fontcolor=white:fontsize=64:"
                   "x=(w-tw)/2:y=(h-th)/2", "-pix_fmt", "yuv420p", CAM,
        ], check=True, capture_output=True)
        log.info("видео-заставка создана: %s", CAM)
    if not os.path.exists(MIC):
        # Тестовый тон 440 Гц, 3 c (зациклится) — чтобы СЛЫШАТЬ, что микрофон
        # транслируется и не режется шумодавом. Реальный плейлист музыки
        # подключим через PulseAudio+mpv следующим шагом.
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=3", "-ac", "1", "-ar", "48000", MIC,
        ], check=True, capture_output=True)
        log.info("аудио-тон создан: %s", MIC)


def launch_args() -> list[str]:
    """Флаги Chromium для подачи файлов как камеры и микрофона."""
    return [
        "--use-fake-ui-for-media-stream",      # авто-грант разрешений
        "--use-fake-device-for-media-stream",
        f"--use-file-for-fake-video-capture={CAM}",
        f"--use-file-for-fake-audio-capture={MIC}",
    ]
