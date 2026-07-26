// Сворачивание блоков панели, кроме мониторинга. У каждой карточки — кнопка
// ▾/▸ в углу; плюс общая кнопка «Свернуть всё кроме мониторинга».

(function () {
  const cards = [...document.querySelectorAll("main .card")];

  function isMonitor(card) {
    // Карточка «Живой просмотр» — не сворачиваем (в ней превью-iframe).
    return !!card.querySelector("#preview-frame");
  }

  function setCollapsed(card, on) {
    card.classList.toggle("collapsed", on);
    const btn = card.querySelector(".collapse-btn");
    if (btn) btn.textContent = on ? "▸" : "▾";
  }

  cards.forEach((card) => {
    if (isMonitor(card)) return;               // мониторинг не трогаем
    const btn = document.createElement("button");
    btn.className = "collapse-btn";
    btn.textContent = "▾";
    btn.title = "Свернуть / развернуть блок";
    btn.addEventListener("click", () => setCollapsed(card, !card.classList.contains("collapsed")));
    card.appendChild(btn);                      // последним ребёнком (absolute)
    setCollapsed(card, true);                   // по умолчанию свёрнуто (кроме мониторинга)
  });

  // Общая кнопка над карточками.
  const main = document.querySelector("main");
  if (main) {
    const all = document.createElement("button");
    all.id = "collapse-all";
    all.textContent = "▾ Развернуть всё";   // стартуем свёрнутыми
    let collapsed = true;
    all.addEventListener("click", () => {
      collapsed = !collapsed;
      cards.forEach((c) => { if (!isMonitor(c)) setCollapsed(c, collapsed); });
      all.textContent = collapsed ? "▾ Развернуть всё" : "▸ Свернуть всё кроме мониторинга";
    });
    main.insertBefore(all, main.firstChild);
  }
})();
