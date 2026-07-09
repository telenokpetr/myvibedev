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

function previewWsUrl() {
  return (location.protocol === "https:" ? "wss" : "ws") +
    "://" + location.host + "/api/preview?slot=" + window.currentSlot;
}

const canvas = document.getElementById("preview-canvas");
const overlay = document.getElementById("preview-overlay");
const toggleBtn = document.getElementById("preview-toggle");
const soundBtn = document.getElementById("preview-sound");
const logEl = document.getElementById("preview-log");
const statePill = document.getElementById("bot-state");

let player = null;
let soundOn = false;

function startPreview() {
  overlay.style.display = "flex";
  overlay.textContent = "подключение…";
  player = new JSMpeg.Player(previewWsUrl(), {
    canvas: canvas,
    audio: true,
    autoplay: true,
    videoBufferSize: 1024 * 1024,
    audioBufferSize: 256 * 1024,
    onVideoDecode: () => { overlay.style.display = "none"; },
  });
  player.volume = 0; // старт без звука, включается по клику
  toggleBtn.textContent = "⏸ Стоп";
  soundBtn.disabled = false;
  soundOn = false;
  updateSoundBtn();
}

function stopPreview() {
  if (player) { try { player.destroy(); } catch (e) {} player = null; }
  overlay.style.display = "flex";
  overlay.textContent = "просмотр выключен";
  toggleBtn.textContent = "▶ Смотреть";
  soundBtn.disabled = true;
}

toggleBtn.addEventListener("click", () => {
  player ? stopPreview() : startPreview();
});

soundBtn.addEventListener("click", () => {
  if (!player) return;
  soundOn = !soundOn;
  player.volume = soundOn ? 1 : 0;
  // разблокировать WebAudio (браузеры глушат автоплей до жеста пользователя)
  try {
    const ctx = player.audioOut && player.audioOut.context;
    if (ctx && ctx.state === "suspended") ctx.resume();
  } catch (e) {}
  updateSoundBtn();
});

function updateSoundBtn() {
  soundBtn.textContent = soundOn ? "🔊 Звук" : "🔇 Звук";
  soundBtn.classList.toggle("ghost", !soundOn);
}

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

// Смена вебинара: гасим просмотр (это другой воркер) и перечитываем статус.
window.addEventListener("slotchange", () => {
  if (player) stopPreview();
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
