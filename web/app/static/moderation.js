// Модерация чата: тест правил + лента событий.

const MOD_CAT = { profanity: "мат", spam: "спам" };
const MOD_ACT = { muted: "замьючен", deleted: "удалено", flagged: "отмечено" };

async function loadModEvents() {
  const tbody = document.querySelector("#mod-events tbody");
  try {
    const r = await fetch("/api/moderation/events?limit=50");
    const items = await r.json();
    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="muted">пока нет событий</td></tr>';
      return;
    }
    tbody.innerHTML = items.map((e) => {
      const catCls = e.category === "profanity" ? "pill-error" : "pill-recording";
      return `<tr>
        <td>${new Date(e.created_at).toLocaleTimeString("ru-RU")}</td>
        <td>${esc(e.sender)}</td>
        <td>${esc(e.text)}</td>
        <td><span class="pill ${catCls}">${MOD_CAT[e.category] || e.category}</span>
            <span class="muted">${esc(e.reason || "")}</span></td>
        <td>${MOD_ACT[e.action] || e.action}</td>
      </tr>`;
    }).join("");
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="5" class="err-cell">не удалось загрузить</td></tr>';
  }
}

document.getElementById("mod-test").addEventListener("click", async () => {
  const sender = document.getElementById("mod-sender").value.trim() || "Тест";
  const text = document.getElementById("mod-text").value.trim();
  const res = document.getElementById("mod-test-res");
  if (!text) { res.textContent = "введите текст"; res.className = "msg err"; return; }
  try {
    const r = await fetch("/api/bot/moderation/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sender, text }),
    });
    const j = await r.json();
    if (j.detected) {
      res.textContent = `нарушение: ${MOD_CAT[j.event.category] || j.event.category} — ${j.event.reason}`;
      res.className = "msg err";
    } else {
      res.textContent = "чисто ✓";
      res.className = "msg ok";
    }
  } catch (e) {
    res.textContent = "bot-worker недоступен";
    res.className = "msg err";
  }
  document.getElementById("mod-text").value = "";
  setTimeout(loadModEvents, 500); // событие ушло в web через callback
});

loadModEvents();
setInterval(loadModEvents, 5000);
