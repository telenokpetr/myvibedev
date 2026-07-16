// Панель медиа: видео идёт в КАМЕРУ бота, музыка — только в МИКРОФОН (на
// камере остаётся заставка). Живой поток с canvas —
// см. bot-browser/app/vcam.py). Загрузка .mp4/.webm до 100 МБ, играть/пауза/стоп,
// громкость звука ролика. Без ролика камера показывает заставку.

const VD = {
  state: document.getElementById("video-state"),
  current: document.getElementById("video-current"),
  stop: document.getElementById("video-stop"),
  playpause: document.getElementById("video-playpause"),
  vol: document.getElementById("video-volume"),
  volVal: document.getElementById("video-volume-val"),
  file: document.getElementById("video-file"),
  uploadBtn: document.getElementById("video-upload-btn"),
  uploadMsg: document.getElementById("video-upload-msg"),
  list: document.querySelector("#video-list tbody"),
};

const VIDEO_MAX = 100 * 1024 * 1024;
let videoPlaying = null;
let videoPaused = false;
let videoVolTimer = null;

function fmtSize(b) {
  return b >= 1024 * 1024 ? (b / 1024 / 1024).toFixed(1) + " МБ"
                          : Math.max(1, Math.round(b / 1024)) + " КБ";
}

function renderVideo(list, status) {
  if (!list || list.error) {
    VD.state.textContent = "недоступно";
    VD.state.className = "pill";
    VD.list.innerHTML = '<tr><td colspan="4" class="muted">бот недоступен</td></tr>';
    return;
  }
  const inMeeting = status && status.in_meeting;
  videoPlaying = (status && status.playing) || list.playing || null;
  const page = (status && status.page) || null;
  videoPaused = page ? !!page.paused : false;

  if (!inMeeting) {
    VD.state.textContent = "бот не в конференции";
    VD.state.className = "pill";
  } else if (videoPlaying && !videoPaused) {
    VD.state.textContent = "в эфире";
    VD.state.className = "pill pill-recording";
  } else if (videoPlaying) {
    VD.state.textContent = "на паузе";
    VD.state.className = "pill";
  } else {
    VD.state.textContent = "заставка";
    VD.state.className = "pill";
  }

  VD.current.textContent = videoPlaying || "—";
  VD.stop.disabled = !videoPlaying;
  VD.playpause.disabled = !videoPlaying;
  VD.playpause.textContent = videoPaused ? "▶" : "⏸";

  const items = list.videos || [];
  if (!items.length) {
    VD.list.innerHTML =
      '<tr><td colspan="4" class="muted">пусто — загрузите видео или музыку</td></tr>';
    return;
  }
  VD.list.innerHTML = items.map((v) => {
    const now = videoPlaying === v.name ? "▶ " : "";
    const audio = v.kind === "audio";
    const where = audio ? "🎵 микрофон" : "🎬 камера";
    // Пока идёт перегон, играть нечего — кнопку прячем.
    const busy = v.converting;
    const play = busy
      ? '<span class="muted">перегон…</span>'
      : `<button data-play="${encodeURIComponent(v.name)}" ${inMeeting ? "" : "disabled"}
                 title="${audio ? "Пустить в микрофон" : "Пустить в камеру"}">▶</button>`;
    return `<tr>
      <td>${now}${esc(v.name)}</td>
      <td class="muted">${where}</td>
      <td class="muted">${fmtSize(v.size)}</td>
      <td style="text-align:right">
        ${play}
        <button class="del" data-del="${encodeURIComponent(v.name)}" title="Удалить">✕</button>
      </td>
    </tr>`;
  }).join("");
}

async function loadVideo() {
  try {
    const [l, s] = await Promise.all([
      fetch("/api/bot/video/list").then((r) => r.json()),
      fetch("/api/bot/video/status").then((r) => r.json()).catch(() => null),
    ]);
    renderVideo(l, s);
  } catch (e) {
    renderVideo(null);
  }
}

async function videoAction(action) {
  try {
    await fetch("/api/bot/video/" + action, { method: "POST" });
  } catch (e) { /* игнор */ }
  loadVideo();
}

VD.stop.addEventListener("click", () => videoAction("stop"));
VD.playpause.addEventListener("click", () =>
  videoAction(VD.playpause.textContent === "▶" ? "resume" : "pause"));

VD.vol.addEventListener("input", () => {
  VD.volVal.textContent = VD.vol.value + "%";
  clearTimeout(videoVolTimer);
  videoVolTimer = setTimeout(async () => {
    try {
      await fetch("/api/bot/video/volume", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ volume: parseInt(VD.vol.value, 10) }),
      });
    } catch (e) { /* игнор */ }
  }, 250);
});

VD.uploadBtn.addEventListener("click", async () => {
  const f = VD.file.files[0];
  if (!f) {
    VD.uploadMsg.textContent = "выберите файл";
    VD.uploadMsg.className = "msg err";
    return;
  }
  if (f.size > VIDEO_MAX) {
    VD.uploadMsg.textContent = "файл больше 100 МБ";
    VD.uploadMsg.className = "msg err";
    return;
  }
  VD.uploadMsg.textContent = `загрузка ${fmtSize(f.size)}…`;
  VD.uploadMsg.className = "msg";
  const fd = new FormData();
  fd.append("file", f);
  try {
    const r = await fetch("/api/bot/video/upload", { method: "POST", body: fd });
    const j = await r.json();
    if (r.ok && !j.error) {
      VD.uploadMsg.textContent = "загружено ✓";
      VD.uploadMsg.className = "msg ok";
      VD.file.value = "";
      loadVideo();
    } else {
      VD.uploadMsg.textContent = j.error || "ошибка загрузки";
      VD.uploadMsg.className = "msg err";
    }
  } catch (e) {
    VD.uploadMsg.textContent = "ошибка сети";
    VD.uploadMsg.className = "msg err";
  }
});

VD.list.addEventListener("click", async (e) => {
  const play = e.target.closest("[data-play]");
  if (play) {
    VD.uploadMsg.textContent = "";
    try {
      const r = await fetch("/api/bot/video/play", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: decodeURIComponent(play.getAttribute("data-play")) }),
      });
      const j = await r.json();
      if (!r.ok || j.error) {
        VD.uploadMsg.textContent = j.error || "не удалось пустить ролик";
        VD.uploadMsg.className = "msg err";
      }
    } catch (e2) { /* игнор */ }
    loadVideo();
    return;
  }
  const del = e.target.closest("[data-del]");
  if (!del) return;
  if (!confirm("Удалить ролик?")) return;
  try {
    await fetch("/api/bot/video/tracks/" + del.getAttribute("data-del"), { method: "DELETE" });
  } catch (e2) { /* игнор */ }
  loadVideo();
});

window.addEventListener("slotchange", () => {
  VD.uploadMsg.textContent = "";
  loadVideo();
});

loadVideo();
setInterval(loadVideo, 4000);
