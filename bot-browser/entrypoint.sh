#!/bin/bash
# Поднимаем виртуальный дисплей для headful Chromium, затем запускаем команду
# (uvicorn для сервиса или smoke-скрипт при отладке).
set -e

# Снять stale-локи от прошлого запуска (docker compose restart сохраняет /tmp,
# и оставшийся /tmp/.X99-lock не даёт Xvfb стартовать — «Missing X server»).
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null || true

# То же и с профилем браузера: том /data переживает контейнер, и от убитого
# процесса остаётся SingletonLock с ИМЕНЕМ СТАРОГО контейнера. Chrome видит
# «профиль занят другим компьютером» и не стартует вообще.
rm -f /data/zoomprofile/SingletonLock /data/zoomprofile/SingletonCookie \
      /data/zoomprofile/SingletonSocket 2>/dev/null || true

Xvfb :99 -screen 0 1280x800x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
for i in $(seq 1 30); do
  xdpyinfo -display :99 >/dev/null 2>&1 && break
  sleep 0.2
done

# PulseAudio для fake-микрофона музыкального браузера (пока не используется).
pulseaudio --start --exit-idle-time=-1 >/tmp/pulse.log 2>&1 || true

exec "$@"
