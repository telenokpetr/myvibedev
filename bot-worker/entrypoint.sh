#!/usr/bin/env bash
set -e

SCREEN_GEOMETRY="${SCREEN_GEOMETRY:-1280x720x24}"

# Запускаем виртуальный дисплей, если он ещё не поднят.
echo "[entrypoint] запускаю Xvfb на ${DISPLAY} (${SCREEN_GEOMETRY})"
Xvfb "${DISPLAY}" -screen 0 "${SCREEN_GEOMETRY}" -nolisten tcp &
XVFB_PID=$!

# Ждём готовности дисплея.
for i in $(seq 1 30); do
    if xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1; then
        echo "[entrypoint] Xvfb готов"
        break
    fi
    sleep 0.5
done

# Корректно гасим Xvfb при остановке контейнера.
trap "kill ${XVFB_PID} 2>/dev/null || true" EXIT

echo "[entrypoint] запускаю управляющий API на :9000"
exec uvicorn app.main:app --host 0.0.0.0 --port 9000
