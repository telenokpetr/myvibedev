#!/bin/bash
# Поднимаем виртуальный дисплей для headful Chromium, затем запускаем команду
# (uvicorn для сервиса или smoke-скрипт при отладке).
set -e

Xvfb :99 -screen 0 1280x800x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
for i in $(seq 1 30); do
  xdpyinfo -display :99 >/dev/null 2>&1 && break
  sleep 0.2
done

# PulseAudio для fake-микрофона музыкального браузера (пока не используется).
pulseaudio --start --exit-idle-time=-1 >/tmp/pulse.log 2>&1 || true

exec "$@"
