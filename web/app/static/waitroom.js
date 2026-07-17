// Панель зала ожидания: бот (организатор) впускает по списку фамилий/имён.
// Совпало любое слово — впуск; нет в списке — оставляет в зале; без фамилии —
// впускает и просит фамилию в чате. См. bot-browser/app/waitroom.py.

const WR = {
  state: document.getElementById("wr-state"),
  enabled: document.getElementById("wr-enabled"),
  name: document.getElementById("wr-name"),
  add: document.getElementById("wr-add"),
  msg: document.getElementById("wr-msg"),
  names: document.querySelector("#wr-names tbody"),
  waiting: document.querySelector("#wr-waiting tbody"),
};

function renderWR(s) {
  if (!s || s.error) {
    WR.state.textContent = "недоступно";
    WR.state.className = "pill";
    return;
  }
  WR.state.textContent = s.enabled ? "авто-впуск включён" : "выключен";
  WR.state.className = "pill " + (s.enabled ? "pill-ok" : "");
  if (document.activeElement !== WR.enabled) WR.enabled.checked = !!s.enabled;

  const names = s.names || [];
  WR.names.innerHTML = names.length
    ? names.map((n) => `<tr>
        <td>${esc(n)}</td>
        <td style="text-align:right">
          <button class="del" data-del="${encodeURIComponent(n)}" title="Убрать">✕</button>
        </td></tr>`).join("")
    : '<tr><td colspan="2" class="muted">список пуст — добавьте фамилии</td></tr>';

  const waiting = s.waiting || [];
  const pending = new Set(s.pending || []);
  const rejected = new Set(s.rejected || []);
  WR.waiting.innerHTML = waiting.length
    ? waiting.map((n) => {
        let tag = "";
        if (rejected.has(n)) tag = ' <span class="msg err">неприемлемый ник</span>';
        else if (pending.has(n)) tag = ' <span class="muted">(ждём фамилию)</span>';
        return `<tr>
          <td>${esc(n)}${tag}</td>
          <td style="text-align:right">
            <button data-admit="${encodeURIComponent(n)}" title="Впустить">✓</button>
            <button class="del" data-deny="${encodeURIComponent(n)}" title="В зал / убрать">✕</button>
          </td></tr>`;
      }).join("")
    : '<tr><td colspan="2" class="muted">никого</td></tr>';
}

async function loadWR() {
  try {
    const r = await fetch("/api/bot/waitroom/status");
    renderWR(await r.json());
  } catch (e) {
    renderWR(null);
  }
}

WR.enabled.addEventListener("change", async () => {
  try {
    const r = await fetch("/api/bot/waitroom/enabled", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ on: WR.enabled.checked }),
    });
    renderWR(await r.json());
  } catch (e) { /* игнор */ }
});

async function addName() {
  const n = WR.name.value.trim();
  if (!n) return;
  WR.msg.textContent = "";
  try {
    const r = await fetch("/api/bot/waitroom/names", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: n }),
    });
    const j = await r.json();
    if (r.ok && !j.error) {
      WR.name.value = "";
      renderWR(j);
    } else {
      WR.msg.textContent = j.error || "ошибка";
      WR.msg.className = "msg err";
    }
  } catch (e) { /* игнор */ }
}

WR.add.addEventListener("click", addName);
WR.name.addEventListener("keydown", (e) => { if (e.key === "Enter") addName(); });

WR.names.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-del]");
  if (!btn) return;
  try {
    const r = await fetch("/api/bot/waitroom/names/" + btn.getAttribute("data-del"),
      { method: "DELETE" });
    renderWR(await r.json());
  } catch (e2) { /* игнор */ }
});

WR.waiting.addEventListener("click", async (e) => {
  const admit = e.target.closest("[data-admit]");
  const deny = e.target.closest("[data-deny]");
  const target = admit || deny;
  if (!target) return;
  const name = decodeURIComponent(target.getAttribute(admit ? "data-admit" : "data-deny"));
  const url = "/api/bot/waitroom/" + (admit ? "admit" : "deny");
  try {
    await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
  } catch (e2) { /* игнор */ }
  loadWR();
});

window.addEventListener("slotchange", () => { WR.msg.textContent = ""; loadWR(); });

loadWR();
setInterval(loadWR, 4000);
