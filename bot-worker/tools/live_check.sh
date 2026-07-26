#!/usr/bin/env bash
# Снимает состояние живого бота для калибровки AT-SPI-действий:
#   - session/status  (зашёл ли бот, статус записи/модерации)
#   - atspi/tree       (дерево доступности окон Zoom — по нему правим atspi.LABELS)
#   - screenshot       (что видит бот на экране)
# Собирает всё в один каталог и .tar.gz для отправки.
#
# Запуск (на машине с поднятым docker compose):
#   INTERNAL_API_TOKEN=<твой токен> bot-worker/tools/live_check.sh
# Переменные:
#   BOT_WORKER_URL     адрес bot-worker (по умолчанию http://localhost:9000)
#   INTERNAL_API_TOKEN общий секрет из .env (обязателен)
set -euo pipefail

BASE="${BOT_WORKER_URL:-http://localhost:9000}"
: "${INTERNAL_API_TOKEN:?задай INTERNAL_API_TOKEN (значение из .env)}"

OUT="${1:-live_check_$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"
AUTH=(-H "X-Internal-Token: ${INTERNAL_API_TOKEN}")

echo "[live_check] bot-worker: ${BASE}"

echo "[live_check] статус сессии → status.json"
curl -fsS "${AUTH[@]}" "${BASE}/session/status" -o "${OUT}/status.json" \
  || echo "  ! не удалось получить статус (бот запущен? токен верный?)"

echo "[live_check] дерево доступности → atspi.json"
curl -fsS "${AUTH[@]}" "${BASE}/atspi/tree" -o "${OUT}/atspi.json" \
  || echo "  ! /atspi/tree недоступно (pyatspi не поднялся? см. логи bot-worker)"

echo "[live_check] скриншот экрана бота → screen.png"
curl -fsS "${AUTH[@]}" "${BASE}/screenshot" -o "${OUT}/screen.png" \
  || echo "  ! скриншот недоступен (нет дисплея?)"

tar czf "${OUT}.tar.gz" "${OUT}"
echo
echo "[live_check] готово. Пришли этот архив для калибровки: ${OUT}.tar.gz"
echo "             (в нём: status.json, atspi.json, screen.png)"
