#!/usr/bin/env bash
set -e

export DISPLAY="${DISPLAY:-:99}"
SCREEN_GEOMETRY="${SCREEN_GEOMETRY:-1280x720x24}"

mkdir -p "${XDG_RUNTIME_DIR}" 2>/dev/null || true
chmod 700 "${XDG_RUNTIME_DIR}" 2>/dev/null || true

# Чистим протухший lock/сокет от упавшего Xvfb — иначе при рестарте новый сервер
# видит их и падает с «Server is already active for display» (дисплей не поднимается).
DISPNUM="${DISPLAY#:}"; DISPNUM="${DISPNUM%%.*}"
rm -f "/tmp/.X${DISPNUM}-lock" "/tmp/.X11-unix/X${DISPNUM}" 2>/dev/null || true

echo "[entrypoint] Xvfb на ${DISPLAY} (${SCREEN_GEOMETRY})"
Xvfb "${DISPLAY}" -screen 0 "${SCREEN_GEOMETRY}" -nolisten tcp &
for i in $(seq 1 40); do
    xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1 && { echo "[entrypoint] Xvfb готов"; break; }
    sleep 0.5
done

echo "[entrypoint] запускаю dbus"
eval "$(dbus-launch --sh-syntax)" || true

echo "[entrypoint] оконный менеджер openbox"
openbox &

echo "[entrypoint] звук: PulseAudio + виртуальные устройства"
# Чистим протухший pid/сокет PulseAudio: XDG_RUNTIME_DIR лежит в /tmp и переживает
# `docker restart` (в отличие от recreate). Устаревший pid от прошлого запуска мешает
# демону подняться (иногда pid переиспользован другим процессом) — и тогда весь звук
# (музыка, микрофон, запись) молчит. Убираем стухшие файлы до старта.
pulseaudio --kill 2>/dev/null || true
rm -f "${XDG_RUNTIME_DIR}/pulse/pid" "${XDG_RUNTIME_DIR}/pulse/native" 2>/dev/null || true
pulseaudio -D --exit-idle-time=-1 --log-target=stderr 2>/dev/null || true
sleep 1
# Подстраховка: если демон всё же не поднялся — пробуем ещё раз после доп. очистки.
if ! pulseaudio --check 2>/dev/null; then
    echo "[entrypoint] PulseAudio не поднялся с первого раза — чистим и повторяем"
    rm -f "${XDG_RUNTIME_DIR}/pulse/pid" "${XDG_RUNTIME_DIR}/pulse/native" 2>/dev/null || true
    pulseaudio -D --exit-idle-time=-1 --log-target=stderr 2>/dev/null || true
    sleep 1
fi
# Виртуальный «динамик»: сюда Zoom выводит звук митинга, его же и записываем.
pactl load-module module-null-sink sink_name=vspeaker \
    sink_properties=device.description=vspeaker 2>/dev/null || true
# Виртуальный «микрофон»: сюда бот проигрывает музыку/звук (music_daemon.py).
pactl load-module module-null-sink sink_name=vmic \
    sink_properties=device.description=vmic 2>/dev/null || true
# ВАЖНО: голый monitor-источник Zoom НЕ показывает как микрофон. Оборачиваем
# vmic.monitor в полноценный source «BotMic» через remap — его Zoom видит как
# обычный микрофон (проверено вживую на Zoom 7.1.0).
pactl load-module module-remap-source master=vmic.monitor source_name=BotMic \
    source_properties="device.description='BotMic'" 2>/dev/null || true
pactl set-default-sink vspeaker 2>/dev/null || true
# Микрофон Zoom по умолчанию -> BotMic (музыка бота). Запись/превью/VAD берут
# vspeaker.monitor явно, поэтому им это не мешает.
pactl set-default-source BotMic 2>/dev/null || true

trap "pkill -TERM zoom 2>/dev/null || true; kill %1 2>/dev/null || true" EXIT

echo "[entrypoint] управляющий API на :9000"
exec uvicorn app.main:app --host 0.0.0.0 --port 9000
