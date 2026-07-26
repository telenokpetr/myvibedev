#!/usr/bin/env bash
set -e

export DISPLAY="${DISPLAY:-:99}"
SCREEN_GEOMETRY="${SCREEN_GEOMETRY:-1280x720x24}"

mkdir -p "${XDG_RUNTIME_DIR}" 2>/dev/null || true
chmod 700 "${XDG_RUNTIME_DIR}" 2>/dev/null || true

echo "[entrypoint] Xvfb на ${DISPLAY} (${SCREEN_GEOMETRY})"
Xvfb "${DISPLAY}" -screen 0 "${SCREEN_GEOMETRY}" -nolisten tcp &
for i in $(seq 1 40); do
    xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1 && { echo "[entrypoint] Xvfb готов"; break; }
    sleep 0.5
done

echo "[entrypoint] запускаю dbus"
eval "$(dbus-launch --sh-syntax)" || true

echo "[entrypoint] шина доступности AT-SPI (для управления Zoom через дерево доступности)"
/usr/libexec/at-spi-bus-launcher --launch-immediately >/dev/null 2>&1 &

echo "[entrypoint] оконный менеджер openbox"
openbox &

echo "[entrypoint] звук: PulseAudio + виртуальные устройства"
pulseaudio -D --exit-idle-time=-1 --log-target=stderr 2>/dev/null || true
sleep 1
# Виртуальный «динамик»: сюда Zoom выводит звук митинга, его же и записываем.
pactl load-module module-null-sink sink_name=vspeaker \
    sink_properties=device.description=vspeaker 2>/dev/null || true
# Виртуальный «микрофон» (на будущее — если бот должен что-то говорить).
pactl load-module module-null-sink sink_name=vmic \
    sink_properties=device.description=vmic 2>/dev/null || true
pactl set-default-sink vspeaker 2>/dev/null || true
pactl set-default-source vspeaker.monitor 2>/dev/null || true

trap "pkill -TERM zoom 2>/dev/null || true; kill %1 2>/dev/null || true" EXIT

echo "[entrypoint] управляющий API на :9000"
exec uvicorn app.main:app --host 0.0.0.0 --port 9000
