const STATUS_LABELS = {
  scheduled: "запланировано",
  joining: "подключается",
  claiming: "берёт хост",
  live: "в эфире",
  recording: "запись",
  finished: "завершено",
  canceled: "отменено",
  error: "ошибка",
};

function fmtDate(iso) {
  return new Date(iso).toLocaleString("ru-RU", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function esc(s) {
  return (s ?? "").toString().replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function shortUrl(u) {
  try {
    const url = new URL(u);
    return url.host + url.pathname;
  } catch {
    return u;
  }
}

async function loadEvents() {
  const tbody = document.querySelector("#events tbody");
  try {
    const r = await fetch("/api/events");
    const items = await r.json();
    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="muted">пока пусто — добавьте мероприятие выше</td></tr>';
      return;
    }
    tbody.innerHTML = items.map((e) => {
      const name = e.title
        ? esc(e.title)
        : `<a href="${esc(e.join_url)}" target="_blank" rel="noopener">${esc(shortUrl(e.join_url))}</a>`;
      return `
      <tr>
        <td>${fmtDate(e.start_time)}</td>
        <td>${name}</td>
        <td>${e.duration_min}м</td>
        <td>${e.host_key ? "🔑" : "—"}</td>
        <td>${e.record ? "🔴" : "—"}</td>
        <td>${e.moderate ? "✓" : "—"}</td>
        <td><span class="pill pill-${e.status}">${STATUS_LABELS[e.status] || e.status}</span></td>
        <td class="row-actions">
          <button class="run" data-id="${e.id}" title="Запустить сейчас">▶</button>
          <button class="stop" data-id="${e.id}" title="Остановить">⏹</button>
          <button class="del" data-id="${e.id}" title="Удалить">✕</button>
        </td>
      </tr>`;
    }).join("");

    tbody.querySelectorAll("button.del").forEach((b) =>
      b.addEventListener("click", () => deleteEvent(b.dataset.id)));
    tbody.querySelectorAll("button.run").forEach((b) =>
      b.addEventListener("click", () => startNow(b.dataset.id)));
    tbody.querySelectorAll("button.stop").forEach((b) =>
      b.addEventListener("click", () => stopEvent(b.dataset.id)));
  } catch {
    tbody.innerHTML = '<tr><td colspan="8" class="err-cell">не удалось загрузить</td></tr>';
  }
}

async function deleteEvent(id) {
  if (!confirm("Удалить мероприятие?")) return;
  const r = await fetch(`/api/events/${id}`, { method: "DELETE" });
  if (r.ok) loadEvents();
}

async function startNow(id) {
  if (!confirm("Запустить сейчас? Бот подключится в ближайшие секунды.")) return;
  const r = await fetch(`/api/events/${id}/start-now`, { method: "POST" });
  if (r.ok) loadEvents();
}

async function stopEvent(id) {
  if (!confirm("Остановить мероприятие? Бот выйдет из конференции.")) return;
  const r = await fetch(`/api/events/${id}/stop`, { method: "POST" });
  if (r.ok) loadEvents();
}

// Переключение режима: datetime активно только для «по расписанию».
const form = document.getElementById("event-form");
form.querySelectorAll('input[name="mode"]').forEach((radio) => {
  radio.addEventListener("change", () => {
    const scheduled = form.mode.value === "scheduled";
    form.start_time.disabled = !scheduled;
    form.start_time.required = scheduled;
  });
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const msg = document.getElementById("form-msg");
  const isNow = f.mode.value === "now";
  const payload = {
    title: f.title.value.trim() || null,
    join_url: f.join_url.value.trim(),
    passcode: f.passcode.value.trim() || null,
    host_key: f.host_key.value.trim() || null,
    duration_min: Number(f.duration_min.value),
    record: f.record.checked,
    moderate: f.moderate.checked,
    start_now: isNow,
    start_time: isNow ? null : f.start_time.value,
  };
  const r = await fetch("/api/events", {
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
    f.start_time.disabled = true;
    loadEvents();
    setTimeout(() => (msg.textContent = ""), 2500);
  } else {
    const err = await r.json().catch(() => ({}));
    const detail = Array.isArray(err.detail) ? err.detail[0]?.msg : err.detail;
    msg.textContent = "ошибка: " + (detail || r.status);
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

loadEvents();
refreshStatus();
setInterval(refreshStatus, 5000);
