// Панель музыки: управление mpv-плеером бота (вкл/выкл, пауза, громкость,
// переключение треков, загрузка своих mp3 до 5 МБ). Звук идёт в микрофон бота.

const MU = {
  state: document.getElementById("music-state"),
  current: document.getElementById("music-current"),
  toggleRun: document.getElementById("music-toggle-run"),
  playpause: document.getElementById("music-playpause"),
  prev: document.getElementById("music-prev"),
  next: document.getElementById("music-next"),
  vol: document.getElementById("music-volume"),
  volVal: document.getElementById("music-volume-val"),
  file: document.getElementById("music-file"),
  uploadBtn: document.getElementById("music-upload-btn"),
  uploadMsg: document.getElementById("music-upload-msg"),
  tracks: document.querySelector("#music-tracks tbody"),
};

let musicRunning = false;
let musicVolTimer = null;

function renderMusic(s) {
  if (!s || s.error) {
    MU.state.textContent = "недоступно";
    MU.state.className = "pill";
    musicRunning = false;
    return;
  }
  musicRunning = !!s.running;
  MU.state.textContent = s.running ? (s.paused ? "на паузе" : "играет") : "выключено";
  MU.state.className = "pill " + (s.running && !s.paused ? "pill-recording" : "");
  MU.current.textContent = s.current || "—";
  MU.toggleRun.textContent = s.running ? "⏹ Выключить музыку" : "▶ Включить музыку";
  MU.playpause.textContent = s.paused ? "▶" : "⏸";
  MU.playpause.disabled = !s.running;
  MU.prev.disabled = !s.running;
  MU.next.disabled = !s.running;

  // не перебиваем ползунок, пока пользователь его тащит
  if (document.activeElement !== MU.vol && s.volume != null) {
    MU.vol.value = s.volume;
    MU.volVal.textContent = s.volume + "%";
  }

  const list = s.tracks || [];
  if (!list.length) {
    MU.tracks.innerHTML = '<tr><td colspan="2" class="muted">нет треков — загрузите mp3</td></tr>';
    return;
  }
  MU.tracks.innerHTML = list.map((t, i) => {
    const nowPlaying = s.running && s.index === i ? "▶ " : "";
    return `<tr>
      <td>${nowPlaying}${esc(t)}</td>
      <td style="text-align:right">
        <button class="del" data-del="${encodeURIComponent(t)}" title="Удалить">✕</button>
      </td>
    </tr>`;
  }).join("");
}

async function loadMusic() {
  try {
    const r = await fetch("/api/bot/music/status");
    renderMusic(await r.json());
  } catch (e) {
    renderMusic(null);
  }
}

async function musicAction(action) {
  try {
    const r = await fetch("/api/bot/music/" + action, { method: "POST" });
    renderMusic(await r.json());
  } catch (e) {
    renderMusic(null);
  }
}

MU.toggleRun.addEventListener("click", () => musicAction(musicRunning ? "stop" : "start"));
MU.prev.addEventListener("click", () => musicAction("prev"));
MU.next.addEventListener("click", () => musicAction("next"));
MU.playpause.addEventListener("click", () =>
  musicAction(MU.playpause.textContent === "▶" ? "resume" : "pause"));

MU.vol.addEventListener("input", () => {
  MU.volVal.textContent = MU.vol.value + "%";
  clearTimeout(musicVolTimer);
  musicVolTimer = setTimeout(async () => {
    try {
      const r = await fetch("/api/bot/music/volume", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ volume: parseInt(MU.vol.value, 10) }),
      });
      renderMusic(await r.json());
    } catch (e) { /* игнор */ }
  }, 250);
});

MU.uploadBtn.addEventListener("click", async () => {
  const f = MU.file.files[0];
  if (!f) {
    MU.uploadMsg.textContent = "выберите mp3";
    MU.uploadMsg.className = "msg err";
    return;
  }
  if (f.size > 5 * 1024 * 1024) {
    MU.uploadMsg.textContent = "файл больше 5 МБ";
    MU.uploadMsg.className = "msg err";
    return;
  }
  MU.uploadMsg.textContent = "загрузка…";
  MU.uploadMsg.className = "msg";
  const fd = new FormData();
  fd.append("file", f);
  try {
    const r = await fetch("/api/bot/music/upload", { method: "POST", body: fd });
    const j = await r.json();
    if (r.ok && !j.error) {
      MU.uploadMsg.textContent = "добавлено ✓";
      MU.uploadMsg.className = "msg ok";
      MU.file.value = "";
      renderMusic(j);
    } else {
      MU.uploadMsg.textContent = j.error || "ошибка загрузки";
      MU.uploadMsg.className = "msg err";
    }
  } catch (e) {
    MU.uploadMsg.textContent = "ошибка сети";
    MU.uploadMsg.className = "msg err";
  }
});

MU.tracks.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-del]");
  if (!btn) return;
  if (!confirm("Удалить трек?")) return;
  try {
    const r = await fetch("/api/bot/music/tracks/" + btn.getAttribute("data-del"),
      { method: "DELETE" });
    renderMusic(await r.json());
  } catch (e2) { /* игнор */ }
});

loadMusic();
setInterval(loadMusic, 4000);
