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

Xvfb :99 -screen 0 1280x720x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
for i in $(seq 1 30); do
  xdpyinfo -display :99 >/dev/null 2>&1 && break
  sleep 0.2
done

# PulseAudio для fake-микрофона музыкального браузера (пока не используется).
pulseaudio --start --exit-idle-time=-1 >/tmp/pulse.log 2>&1 || true

# Экран браузера бота наружу через noVNC (для РУЧНОГО входа в Zoom-аккаунт):
# x11vnc отдаёт дисплей :99, websockify заворачивает его в веб на :6080.
# Порт биндится только на 127.0.0.1 (см. docker-compose) — доступ с этой машины.
# -nopw без пароля намеренно: доступ уже ограничен localhost'ом.
x11vnc -display :99 -forever -shared -nopw -rfbport 5900 -bg \
       -o /tmp/x11vnc.log >/dev/null 2>&1 || true
NOVNC_DIR=/usr/share/novnc
[ -f "$NOVNC_DIR/vnc.html" ] && [ ! -f "$NOVNC_DIR/index.html" ] && \
  ln -sf vnc.html "$NOVNC_DIR/index.html" 2>/dev/null || true
websockify --web="$NOVNC_DIR" 6080 localhost:5900 >/tmp/novnc.log 2>&1 &

exec "$@"
