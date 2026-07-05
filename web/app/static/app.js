const STATUS_LABELS = {
  scheduled: "запланирована",
  joining: "подключается",
  live: "в эфире",
  recording: "запись",
  finished: "завершена",
  canceled: "отменена",
  error: "ошибка",
};

function fmtDate(iso) {
  const d = new Date(iso);
  return d.toLocaleString("ru-RU", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function esc(s) {
  return (s ?? "").toString().replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function loadLectures() {
  const tbody = document.querySelector("#lectures tbody");
  try {
    const r = await fetch("/api/lectures");
    const items = await r.json();
    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="muted">пока пусто — добавьте лекцию выше</td></tr>';
      return;
    }
    tbody.innerHTML = items.map((l) => `
      <tr>
        <td>${fmtDate(l.start_time)}</td>
        <td>${esc(l.title)}</td>
        <td>${l.duration_min}м</td>
        <td>${esc(l.zoom_meeting_id) || "—"}</td>
        <td>${l.record ? "🔴" : "—"}</td>
        <td>${l.moderate ? "✓" : "—"}</td>
        <td><span class="pill pill-${l.status}">${STATUS_LABELS[l.status] || l.status}</span></td>
        <td><button class="del" data-id="${l.id}" title="Удалить">✕</button></td>
      </tr>`).join("");

    tbody.querySelectorAll("button.del").forEach((btn) => {
      btn.addEventListener("click", () => deleteLecture(btn.dataset.id));
    });
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="8" class="err-cell">не удалось загрузить</td></tr>';
  }
}

async function deleteLecture(id) {
  if (!confirm("Удалить лекцию из расписания?")) return;
  const r = await fetch(`/api/lectures/${id}`, { method: "DELETE" });
  if (r.ok) loadLectures();
}

document.getElementById("lecture-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const msg = document.getElementById("form-msg");
  const payload = {
    title: f.title.value.trim(),
    start_time: f.start_time.value,   // "YYYY-MM-DDTHH:MM" — локальное время
    duration_min: Number(f.duration_min.value),
    zoom_meeting_id: f.zoom_meeting_id.value.trim() || null,
    zoom_join_url: f.zoom_join_url.value.trim() || null,
    zoom_passcode: f.zoom_passcode.value.trim() || null,
    record: f.record.checked,
    moderate: f.moderate.checked,
  };
  const r = await fetch("/api/lectures", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (r.ok) {
    msg.textContent = "добавлено ✓";
    msg.className = "msg ok";
    f.reset();
    f.moderate.checked = true;
    f.duration_min.value = 60;
    loadLectures();
    setTimeout(() => (msg.textContent = ""), 2500);
  } else {
    const err = await r.json().catch(() => ({}));
    msg.textContent = "ошибка: " + (err.detail?.[0]?.msg || err.detail || r.status);
    msg.className = "msg err";
  }
});

async function refreshStatus() {
  const el = document.getElementById("status");
  try {
    const r = await fetch("/health");
    const j = await r.json();
    const ok = r.status === 200;
    el.textContent = ok ? "всё работает" : "есть проблемы";
    el.className = "badge " + (ok ? "ok" : "err");
    el.title = `db: ${j.db} · redis: ${j.redis}`;
  } catch {
    el.textContent = "нет связи";
    el.className = "badge err";
  }
}

loadLectures();
refreshStatus();
setInterval(refreshStatus, 5000);
