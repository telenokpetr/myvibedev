// Две области панели: ГЛАВНАЯ (мониторинг/медиа/реакции — работа во время
// мероприятия) и НАСТРОЙКИ (аккаунт, зал ожидания, расписание). Вкладка
// «⚙ Настройки» рядом со слот-вкладками переключает область.

(function () {
  const nav = document.getElementById("webinar-tabs");
  const cards = [...document.querySelectorAll("main > .card")];

  // Главная область — карточки с мониторингом / медиа / реакциями.
  const isMain = (c) =>
    c.querySelector("#preview-frame") ||
    c.querySelector("#video-list") ||
    c.querySelector("#reaction-row");
  cards.forEach((c) => { c.dataset.view = isMain(c) ? "main" : "settings"; });

  function setView(v) {
    document.body.setAttribute("data-view", v);
    const st = document.getElementById("settings-tab");
    if (st) st.classList.toggle("active", v === "settings");
    // Слот-вкладку держим подсвеченной ВСЕГДА (и в настройках) — настройки
    // (аккаунт/зал) относятся к выбранному мероприятию, это должно быть видно.
    if (nav) {
      nav.querySelectorAll(".tab[data-slot]").forEach((t) =>
        t.classList.toggle("active", Number(t.dataset.slot) === (window.currentSlot || 0)));
    }
  }

  function addSettingsTab() {
    if (!nav || document.getElementById("settings-tab")) return;
    const t = document.createElement("div");
    t.id = "settings-tab";
    t.className = "tab";
    t.innerHTML = '<span class="tab-title">⚙ Настройки</span>';
    t.addEventListener("click", () => setView("settings"));
    nav.appendChild(t);
  }

  // Клик по слот-вкладке (Мероприятие 1/2) — вернуться на главную.
  if (nav) {
    nav.addEventListener("click", (e) => {
      if (e.target.closest(".tab[data-slot]")) setView("main");
    });
  }

  // Слот-вкладки строятся асинхронно (app.js) — дождёмся и добавим «Настройки».
  const iv = setInterval(() => {
    if (nav && nav.querySelector(".tab[data-slot]")) { addSettingsTab(); clearInterval(iv); }
  }, 300);
  setTimeout(addSettingsTab, 4000);

  setView("main");
})();
