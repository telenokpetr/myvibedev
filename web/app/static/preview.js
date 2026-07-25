// Живой просмотр: jsmpeg-плеер (видео+звук) + панель статуса бота.

const BOT_LABELS = {
  idle: "простаивает",
  joining: "подключается",
  claiming: "берёт хост",
  live: "в эфире",
  recording: "запись",
  finished: "завершено",
  error: "ошибка",
  unavailable: "воркер недоступен",
};

// Живой экран бота — через noVNC (уже запущен в контейнере бота: слот 0 → 6080,
// слот 1 → 6081). Раньше тут был jsmpeg-поток /api/preview от desktop-воркера,
// но у браузерного бота такого потока нет — поэтому мониторинг был пуст.
const frame = document.getElementById("preview-frame");
const overlay = document.getElementById("preview-overlay");
const toggleBtn = document.getElementById("preview-toggle");
const openLink = document.getElementById("preview-open");
const logEl = document.getElementById("preview-log");
const statePill = document.getElementById("bot-state");

let previewOn = false;

function novncUrl() {
  const port = 6080 + (window.currentSlot || 0);
  return "http://" + location.hostname + ":" + port +
    "/vnc.html?autoconnect=1&resize=scale&view_only=1&reconnect=1";
}

function startPreview() {
  const url = novncUrl();
  frame.src = url;
  frame.style.display = "";
  overlay.style.display = "none";
  openLink.href = url;
  openLink.style.display = "";
  toggleBtn.textContent = "⏸ Стоп";
  previewOn = true;
}

function stopPreview() {
  frame.src = "about:blank";
  frame.style.display = "none";
  overlay.style.display = "flex";
  overlay.textContent = "просмотр выключен";
  openLink.style.display = "none";
  toggleBtn.textContent = "▶ Смотреть";
  previewOn = false;
}

toggleBtn.addEventListener("click", () => {
  previewOn ? stopPreview() : startPreview();
});

// Сменили вкладку мероприятия — если смотрим, показать экран бота нового слота.
window.addEventListener("slotchange", () => {
  if (previewOn) startPreview();
});

// ---- Запись и модерация ----
const recTarget = document.getElementById("rec-target");
const recToggle = document.getElementById("rec-toggle");
const recPause = document.getElementById("rec-pause");
const recState = document.getElementById("rec-state");
const muteAllBtn = document.getElementById("mute-all");

let rec = { active: false, paused: false, target: null };

recToggle.addEventListener("click", async () => {
  recToggle.disabled = true;
  try {
    if (!rec.active) {
      await fetch(`/api/bot/recording/start?target=${recTarget.value}`, { method: "POST" });
    } else {
      await fetch(`/api/bot/recording/stop`, { method: "POST" });
    }
  } finally {
    recToggle.disabled = false;
    pollBot();
  }
});

recPause.addEventListener("click", async () => {
  const action = rec.paused ? "resume" : "pause";
  await fetch(`/api/bot/recording/${action}`, { method: "POST" });
  pollBot();
});

muteAllBtn.addEventListener("click", async () => {
  if (!confirm("Отключить звук всем участникам?")) return;
  const r = await fetch(`/api/bot/mute-all`, { method: "POST" });
  const j = await r.json().catch(() => ({}));
  muteAllBtn.textContent = j.ok ? "🔇 Звук отключён" : "🔇 Отключить звук всем";
  if (j.ok) setTimeout(() => (muteAllBtn.textContent = "🔇 Отключить звук всем"), 2500);
});

function renderRecording(r) {
  rec = r || { active: false, paused: false, target: null };
  if (!rec.active) {
    recToggle.textContent = "⏺ Начать запись";
    recToggle.classList.remove("danger");
    recPause.disabled = true;
    recPause.textContent = "⏸ Пауза";
    recTarget.disabled = false;
    recState.textContent = "нет записи";
    recState.className = "pill pill-idle";
  } else {
    recToggle.textContent = "⏹ Остановить";
    recToggle.classList.add("danger");
    recPause.disabled = false;
    recPause.textContent = rec.paused ? "▶ Продолжить" : "⏸ Пауза";
    recTarget.disabled = true;
    const tgt = rec.target === "cloud" ? "облако" : "локально";
    recState.textContent = rec.paused ? `пауза (${tgt})` : `запись (${tgt})`;
    recState.className = "pill " + (rec.paused ? "pill-idle" : "pill-recording");
  }
}

async function pollBot() {
  try {
    const r = await fetch("/api/bot/status");
    const j = await r.json();
    const s = j.status || "—";
    statePill.textContent = BOT_LABELS[s] || s;
    statePill.className = "pill pill-" + s;
    renderRecording(j.recording);
    if (Array.isArray(j.log)) {
      logEl.innerHTML = j.log.slice(-8)
        .map((l) => `<div>${l.replace(/</g, "&lt;")}</div>`).join("");
      logEl.scrollTop = logEl.scrollHeight;
    }
  } catch (e) {
    statePill.textContent = "нет связи";
    statePill.className = "pill pill-error";
  }
}

// Смена мероприятия: сбрасываем кнопку мьюта и перечитываем статус.
// (Экран превью переключает отдельный slotchange-обработчик выше.)
window.addEventListener("slotchange", () => {
  muteAllBtn.textContent = "🔇 Отключить звук всем";
  pollBot();
});

setInterval(pollBot, 3000);
pollBot();

// Статус записи — отдельное автообновление раз в 30 секунд с отметкой времени.
const recUpdated = document.getElementById("rec-updated");
async function refreshRecordingStatus() {
  try {
    const r = await fetch("/api/bot/status");
    const j = await r.json();
    renderRecording(j.recording);
    if (recUpdated) recUpdated.textContent = "обновлено " + new Date().toLocaleTimeString();
  } catch (e) {
    if (recUpdated) recUpdated.textContent = "нет связи";
  }
}
setInterval(refreshRecordingStatus, 30000);
refreshRecordingStatus();
