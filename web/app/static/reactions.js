// Реакции (кнопки эмодзи) + тумблер автоприветствия. См. bot-browser reaction/greet.

const RC = {
  row: document.getElementById("reaction-row"),
  msg: document.getElementById("reaction-msg"),
  greet: document.getElementById("greet-enabled"),
  state: document.getElementById("greet-state"),
};

if (RC.row) {
  RC.row.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-react]");
    if (!btn) return;
    RC.msg.textContent = "…";
    try {
      const r = await fetch("/api/bot/reaction", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: btn.getAttribute("data-react") }),
      });
      const j = await r.json();
      RC.msg.textContent = j.error ? j.error : "отправлено ✓";
      RC.msg.className = "msg " + (j.error ? "err" : "ok");
    } catch (e2) {
      RC.msg.textContent = "ошибка сети";
      RC.msg.className = "msg err";
    }
    setTimeout(() => { RC.msg.textContent = ""; }, 2500);
  });
}

async function loadGreet() {
  try {
    const j = await (await fetch("/api/bot/greet")).json();
    const on = j && j.enabled;
    if (document.activeElement !== RC.greet) RC.greet.checked = !!on;
    RC.state.textContent = on ? "приветствие вкл" : "приветствие выкл";
    RC.state.className = "pill " + (on ? "pill-ok" : "");
  } catch (e) {
    RC.state.textContent = "недоступно";
    RC.state.className = "pill";
  }
}

if (RC.greet) {
  RC.greet.addEventListener("change", async () => {
    try {
      await fetch("/api/bot/greet", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ on: RC.greet.checked }),
      });
    } catch (e) { /* игнор */ }
    loadGreet();
  });
  window.addEventListener("slotchange", loadGreet);
  loadGreet();
}
