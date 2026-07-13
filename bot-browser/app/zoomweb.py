"""Драйвер Zoom web-клиента через Playwright (headful под Xvfb).

Заходит в митинг по ссылке, читает чат из DOM (автор + текст + id сообщения),
удаляет сообщение через контекстное меню «...» → «Удалить» → диалог. Всё
селекторами DOM — без OCR и без кликов по пикселям (в отличие от desktop-бота).

Селекторы выверены ручной разведкой 13.07 (см. историю): кнопка опций
сообщения — `button.new-chat-message__options-button` (последняя в строке —
это «...»), пункт меню и кнопка диалога ищутся по тексту «Удалить».

Playwright sync API привязан к потоку-владельцу, поэтому весь драйвер живёт
в одном рабочем потоке (см. moderator.BrowserModerator).
"""

import logging
import re
import time

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

log = logging.getLogger("zoomweb")


def to_web_client(url: str) -> str:
    """Любую Zoom-ссылку привести к web-клиенту app.zoom.us/wc/join/<id>.

    Desktop-ссылка вида us06web.zoom.us/j/<id>?pwd=… в браузере ведёт на
    landing «запустить Zoom», поэтому строим прямой web-URL (проверено
    разведкой 13.07)."""
    m = (re.search(r"/j/(\d+)", url) or re.search(r"/wc/join/(\d+)", url)
         or re.search(r"/wc/(\d+)/join", url))
    if not m:
        return url
    mid = m.group(1)
    web = f"https://app.zoom.us/wc/join/{mid}"
    pwd = re.search(r"[?&]pwd=([^&]+)", url)
    if pwd:
        web += f"?pwd={pwd.group(1)}"
    return web

# JS-извлечение сообщений чата: id (генерим из позиции+текста, если нет data-id),
# автор (заголовок группы) и текст. Пузыри без явного автора наследуют
# последний виденный заголовок «Имя to Everyone».
_JS_READ_CHAT = r"""
() => {
  const out = [];
  const items = document.querySelectorAll('[class*="new-chat-message"]');
  let sender = 'чат';
  for (const el of items) {
    // Заголовок группы: «Имя Кому Все ЧЧ:ММ» (RU) / «Name to Everyone HH:MM».
    // Ищем в самом элементе или его предыдущем соседе по этому паттерну.
    const scan = [el, el.previousElementSibling].filter(Boolean);
    for (const s of scan) {
      const m = (s.innerText||'').match(/^\s*(.+?)\s+(?:Кому|to)\s+/);
      if (m && m[1].trim() && m[1].trim().length < 40) { sender = m[1].trim(); break; }
    }
    const body = el.querySelector('[class*="new-chat-message__body"], [class*="message-text"], [class*="__content"]');
    if (!body) continue;
    const text = (body.innerText || '').trim();
    if (!text) continue;
    const id = el.getAttribute('data-msg-id') || el.id ||
               (sender + '|' + text + '|' + out.length);
    out.push({id, sender, text});
  }
  return out;
}
"""


class ZoomWeb:
    def __init__(self, name: str) -> None:
        self._name = name
        self._pw = None
        self._browser = None
        self._ctx = None
        self.page = None

    # ---- жизненный цикл ----

    def start(self) -> None:
        from app import media
        media.ensure_assets()
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=False, args=[
            *media.launch_args(),   # виртуальные камера + микрофон из файлов
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ])
        self._ctx = self._browser.new_context(
            permissions=["microphone", "camera"],
            viewport={"width": 1280, "height": 800},
            locale="ru-RU",
        )
        self.page = self._ctx.new_page()

    def stop(self) -> None:
        for closer in (self._browser, self._pw):
            try:
                if closer is self._pw:
                    closer.stop()
                else:
                    closer.close()
            except Exception:  # noqa: BLE001
                pass

    def screenshot(self, path: str) -> None:
        try:
            self.page.screenshot(path=path)
        except Exception:  # noqa: BLE001
            pass

    # ---- вход в митинг ----

    def join(self, url: str) -> bool:
        p = self.page
        url = to_web_client(url)
        log.info("join web-url: %s", url)
        p.goto(url, wait_until="domcontentloaded", timeout=60000)
        p.wait_for_timeout(4000)
        try:
            p.get_by_role("textbox", name="Ваше имя").fill(self._name, timeout=15000)
        except PWTimeout:
            log.warning("поле имени не найдено")
        # В prejoin включить камеру (кнопка-тумблер видео), чтобы войти с видео.
        for label in ("Включить показ видео", "Start Video", "Start video"):
            try:
                p.get_by_role("button", name=label, exact=False).first.click(timeout=3000)
                log.info("камера включена в prejoin")
                break
            except PWTimeout:
                continue
        for label in ("Войти", "Join", "Присоединиться"):
            try:
                p.get_by_role("button", name=label, exact=False).first.click(timeout=5000)
                break
            except PWTimeout:
                continue
        p.wait_for_timeout(6000)
        # Подключить звук компьютера (НЕ «Продолжить без аудио» — иначе микрофон
        # не транслируется). Фейковый микрофон отдаётся браузером из файла.
        for label in ("Войти в аудиоконференцию", "Join Audio", "Использовать звук",
                      "Computer Audio", "звук компьютера"):
            try:
                p.get_by_role("button", name=label, exact=False).first.click(timeout=4000)
                log.info("аудио подключено: %s", label)
                break
            except PWTimeout:
                continue
        p.wait_for_timeout(4000)
        self._enable_media_in_meeting()
        ok = self._open_chat()
        log.info("join: url=%s chat_open=%s", p.url, ok)
        return "/wc/" in p.url

    def _enable_media_in_meeting(self) -> None:
        self.ensure_video_on()

    def ensure_video_on(self) -> None:
        """Включить камеру, если выключена. Кнопка тумблера в тулбаре: когда
        камера ВЫКЛ, её aria-label начинается со «start»/«начать» — по этому и
        отличаем (кликать всегда нельзя — тумблер выключит включённую)."""
        try:
            off = self.page.locator(
                'button[aria-label*="start my video" i], '
                'button[aria-label*="start video" i], '
                'button[aria-label*="начать видео" i], '
                'button[aria-label*="включить видео" i]')
            if off.count() > 0 and off.first.is_visible():
                off.first.click(timeout=2000)
                log.info("камера включена в митинге")
        except Exception:  # noqa: BLE001
            pass

    def _open_chat(self) -> bool:
        # Панель чата в web рендерит сообщения только когда открыта. Кнопка —
        # aria «open the chat panel» (может нести суффикс «N unread message»).
        try:
            self.page.locator(
                'button[aria-label*="chat panel"], button[aria-label*="open the chat"], '
                'button[aria-label*="Чат"]'
            ).first.click(timeout=8000)
            self.page.wait_for_timeout(1500)
            return True
        except PWTimeout:
            return False

    def chat_is_open(self) -> bool:
        """Открыта ли панель чата (есть поле ввода/контейнер сообщений)."""
        try:
            return bool(self.page.evaluate(
                "() => !!document.querySelector('[class*=\"new-chat-message\"], "
                "[class*=\"chat-rich-text\"], [aria-label*=\"Введите\"]')"))
        except Exception:  # noqa: BLE001
            return False

    def ensure_chat_open(self) -> None:
        if not self.chat_is_open():
            self._open_chat()

    def in_meeting(self) -> bool:
        return self.page is not None and "/wc/" in (self.page.url or "")

    # ---- чтение чата ----

    def read_chat(self) -> list[dict]:
        try:
            return self.page.evaluate(_JS_READ_CHAT)
        except Exception as exc:  # noqa: BLE001
            log.warning("read_chat: %s", exc)
            return []

    # ---- отправка предупреждения в чат ----

    def send_chat(self, text: str) -> bool:
        """Написать в общий чат. Поле ввода Zoom web — contenteditable div внизу
        панели чата. Ввод через настоящий клик+keyboard, отправка Enter; если
        не ушло — кнопка отправки. Логируем, что нашли, чтобы видеть сбой."""
        p = self.page
        try:
            self.ensure_chat_open()
            box = p.locator(
                'div[contenteditable="true"], [role="textbox"][contenteditable="true"], '
                'textarea[placeholder*="ообщение" i]')
            n = box.count()
            if n == 0:
                log.warning("send_chat: поле ввода не найдено")
                return False
            field = box.last                       # поле ввода — последний editable
            field.click(timeout=3000)
            p.wait_for_timeout(200)
            p.keyboard.type(text, delay=10)
            p.wait_for_timeout(200)
            p.keyboard.press("Enter")
            p.wait_for_timeout(500)
            # проверка: текст ушёл (поле очистилось)?
            remaining = ""
            try:
                remaining = (field.inner_text() or "").strip()
            except Exception:  # noqa: BLE001
                pass
            if remaining and text[:5] in remaining:
                # Enter не отправил — жмём кнопку отправки (иконка справа снизу)
                try:
                    p.locator('button[aria-label*="Send" i], button[aria-label*="тправ" i], '
                              'button[class*="send"]').last.click(timeout=1500)
                    p.wait_for_timeout(300)
                except PWTimeout:
                    log.warning("send_chat: Enter не отправил, кнопки нет")
                    return False
            log.info("предупреждение отправлено: %r", text)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("send_chat: %s", exc)
            return False

    # ---- мьют участника ----

    def mute_participant(self, name: str) -> bool:
        """Замьютить участника по имени: открыть панель «Участники», навести
        РЕАЛЬНЫМ hover на строку (кнопка мьюта всплывает только от курсора,
        как «...» в чате) → кнопка «Выключить звук»/Mute."""
        p = self.page
        try:
            p.locator('button[aria-label*="participants" i], '
                      'button[aria-label*="частник" i]').first.click(timeout=3000)
            p.wait_for_timeout(800)
            row = p.locator('[class*="participants-item"], li, [role="listitem"]'
                            ).filter(has_text=name).first
            if row.count() == 0:
                log.info("mute: участник %r не найден в панели", name)
                return False
            row.scroll_into_view_if_needed(timeout=2000)
            row.hover(timeout=2000)                # реальный hover
            p.wait_for_timeout(250)
            btn = row.locator(
                'button[aria-label*="ute" i], button[aria-label*="ыключить звук" i]')
            if btn.count() == 0:
                # кнопка мьюта могла не всплыть/уже muted
                log.info("mute: кнопка мьюта не найдена у %r", name)
                return False
            btn.first.click(timeout=2000)
            log.info("mute: клик по кнопке мьюта у %r", name)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("mute_participant: %s", exc)
            return False

    # ---- удаление сообщения ----

    def delete_duplicates(self, text: str, keep_last: int = 1) -> int:
        """Удалить повторяющиеся сообщения с одинаковым текстом, оставив
        последние keep_last. Возвращает число удалённых."""
        removed = 0
        for _ in range(20):  # предохранитель от бесконечного цикла
            n = self._count_text(text)
            if n <= keep_last:
                break
            if not self.delete_message(None, text):   # удаляет верхнее совпадение
                break
            removed += 1
        return removed

    def _count_text(self, text: str) -> int:
        try:
            return int(self.page.evaluate(r"""
            (text) => {
              const items = [...document.querySelectorAll('[class*="new-chat-message"]')];
              let n = 0;
              for (const el of items) {
                const b = el.querySelector('[class*="new-chat-message__body"], [class*="message-text"], [class*="__content"]');
                if (b && (b.innerText||'').trim() === text) n++;
              }
              return n;
            }
            """, text))
        except Exception:  # noqa: BLE001
            return 0

    def _message_locator(self, text: str):
        """Locator верхнего (старейшего видимого) сообщения с точным текстом."""
        import re as _re
        return (self.page.locator('[class*="new-chat-message"]')
                .filter(has_text=_re.compile(rf"^{_re.escape(text)}$")))

    def delete_message(self, mid: str, text: str) -> bool:
        """Удалить сообщение: РЕАЛЬНЫЙ Playwright-hover по строке (Zoom не
        реагирует на синтетические mouseover — «...» поднимается только от
        настоящего курсора) → кнопка «...» → «Удалить» → диалог. True только
        если сообщение исчезло из DOM."""
        p = self.page
        loc = self._message_locator(text)
        try:
            cnt = loc.count()
        except Exception:  # noqa: BLE001
            cnt = 0
        if cnt == 0:
            log.info("delete: сообщение %r не найдено в DOM", text)
            return False
        el = loc.first
        try:
            el.scroll_into_view_if_needed(timeout=2000)
            el.hover(timeout=3000)                 # НАСТОЯЩИЙ hover
            p.wait_for_timeout(250)
            dots = el.locator('button.new-chat-message__options-button').last
            dots.hover(timeout=2000)
            dots.click(timeout=2000)
        except PWTimeout:
            log.info("delete: «...» не поднялась для %r", text)
            return False
        p.wait_for_timeout(400)
        if not self._click_text_node("Удалить"):
            log.info("delete: пункт «Удалить» не найден для %r", text)
            p.keyboard.press("Escape")
            return False
        p.wait_for_timeout(400)
        try:
            p.get_by_role("button", name="Удалить", exact=True).last.click(timeout=3000)
        except PWTimeout:
            p.keyboard.press("Enter")
        p.wait_for_timeout(600)
        gone = self._count_text(text) < cnt
        log.info("delete %r: было %d, %s", text, cnt,
                 "удалено" if gone else "НЕ удалилось")
        return gone

    # ---- запись (host-контрол) ----

    def _more_menu(self) -> None:
        """Открыть меню «Подробнее»/«More» в тулбаре (там живут Record и пр.)."""
        for label in ("Подробнее", "More", "More meeting control"):
            try:
                self.page.get_by_role("button", name=label, exact=False).first.click(timeout=2500)
                self.page.wait_for_timeout(600)
                return
            except PWTimeout:
                continue

    def start_recording(self, target: str = "cloud") -> bool:
        """Старт записи. target=cloud|local. Пункт в меню «Подробнее» или прямо
        в тулбаре («Запись»/«Record»). Облако — «в облаке/to the Cloud»."""
        p = self.page
        # прямая кнопка в тулбаре
        for label in ("Запись", "Record"):
            try:
                p.get_by_role("button", name=label, exact=True).first.click(timeout=2000)
                break
            except PWTimeout:
                continue
        else:
            self._more_menu()
            for label in ("Записать", "Запись", "Record"):
                if self._click_text_node(label):
                    break
        p.wait_for_timeout(800)
        # выбор облако/компьютер, если предложат
        want = ("облак", "cloud") if target == "cloud" else ("компьютер", "computer", "локальн")
        for key in want:
            for node in ("Записать в облаке", "Record to the Cloud",
                         "Записать на этот компьютер", "Record to this Computer"):
                if key in node.lower() and self._click_text_node(node):
                    p.wait_for_timeout(500)
                    return True
        # если меню выбора не появилось — запись уже пошла (одиночный вариант)
        return self._is_recording()

    def pause_recording(self) -> bool:
        return self._rec_action(("Приостановить запись", "Pause Recording", "Пауза"))

    def resume_recording(self) -> bool:
        return self._rec_action(("Возобновить запись", "Resume Recording"))

    def stop_recording(self) -> bool:
        if not self._rec_action(("Остановить запись", "Stop Recording")):
            return False
        self.page.wait_for_timeout(600)
        # подтверждение «Остановить»/«Yes» в диалоге
        for label in ("Остановить", "Stop", "Да", "Yes"):
            try:
                self.page.get_by_role("button", name=label, exact=True).first.click(timeout=2500)
                return True
            except PWTimeout:
                continue
        return True

    def _rec_action(self, labels: tuple[str, ...]) -> bool:
        self._more_menu()
        for label in labels:
            if self._click_text_node(label):
                return True
        # некоторые сборки держат Pause/Stop у индикатора записи вверху
        for label in labels:
            try:
                self.page.get_by_role("button", name=label, exact=False).first.click(timeout=1500)
                return True
            except PWTimeout:
                continue
        return False

    def _is_recording(self) -> bool:
        try:
            return bool(self.page.evaluate(
                "() => /Запись|Recording/i.test(document.body.innerText) && "
                "!!document.querySelector('[aria-label*=\"ecording\" i],[class*=\"recording\"]')"))
        except Exception:  # noqa: BLE001
            return False

    def _click_text_node(self, label: str) -> bool:
        """Клик по листовому DOM-узлу с точным текстом (пункт меню Zoom)."""
        return bool(self.page.evaluate(r"""
        (label) => {
          const w = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
          while (w.nextNode()) {
            const e = w.currentNode;
            if (e.children.length === 0 && (e.textContent||'').trim() === label) {
              (e.closest('[role="menuitem"],li,button,a') || e).click();
              return true;
            }
          }
          return false;
        }
        """, label))
