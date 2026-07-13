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
import time

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

log = logging.getLogger("zoomweb")

# JS-извлечение сообщений чата: id (генерим из позиции+текста, если нет data-id),
# автор (заголовок группы) и текст. Пузыри без явного автора наследуют
# последний виденный заголовок «Имя to Everyone».
_JS_READ_CHAT = r"""
() => {
  const out = [];
  const items = document.querySelectorAll(
    '[class*="new-chat-message"]');
  let sender = 'чат';
  for (const el of items) {
    // заголовок группы сообщений: «Имя to Everyone ЧЧ:ММ»
    const head = el.querySelector('[class*="sender-name"], [class*="__sender"], [class*="chat-message-name"]');
    if (head && head.innerText.trim()) sender = head.innerText.trim();
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
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=False, args=[
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ])
        self._ctx = self._browser.new_context(
            permissions=["microphone"],
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
        p.goto(url, wait_until="domcontentloaded", timeout=60000)
        p.wait_for_timeout(4000)
        try:
            p.get_by_role("textbox", name="Ваше имя").fill(self._name, timeout=15000)
        except PWTimeout:
            log.warning("поле имени не найдено")
        for label in ("Войти", "Join", "Присоединиться"):
            try:
                p.get_by_role("button", name=label, exact=False).first.click(timeout=5000)
                break
            except PWTimeout:
                continue
        p.wait_for_timeout(6000)
        for label in ("Продолжить без аудио", "Join without", "Продолжить"):
            try:
                p.get_by_role("button", name=label, exact=False).first.click(timeout=4000)
                break
            except PWTimeout:
                continue
        p.wait_for_timeout(6000)
        ok = self._open_chat()
        log.info("join: url=%s chat_open=%s", p.url, ok)
        return "/wc/" in p.url

    def _open_chat(self) -> bool:
        try:
            self.page.locator(
                'button[aria-label*="chat panel"], button[aria-label*="Чат"]'
            ).first.click(timeout=8000)
            self.page.wait_for_timeout(1500)
            return True
        except PWTimeout:
            return False

    def in_meeting(self) -> bool:
        return self.page is not None and "/wc/" in (self.page.url or "")

    # ---- чтение чата ----

    def read_chat(self) -> list[dict]:
        try:
            return self.page.evaluate(_JS_READ_CHAT)
        except Exception as exc:  # noqa: BLE001
            log.warning("read_chat: %s", exc)
            return []

    # ---- удаление сообщения ----

    def delete_message(self, mid: str, text: str) -> bool:
        """Найти сообщение по id/тексту, открыть «...», «Удалить», подтвердить.

        Возвращает True только если сообщение реально пропало из DOM."""
        p = self.page
        # навести на сообщение и нажать его кнопку опций «...»
        opened = p.evaluate(r"""
        ([mid, text]) => {
          const items = [...document.querySelectorAll('[class*="new-chat-message"]')];
          let target = null;
          for (const el of items) {
            const body = el.querySelector('[class*="new-chat-message__body"], [class*="message-text"], [class*="__content"]');
            if (!body) continue;
            const t = (body.innerText||'').trim();
            const id = el.getAttribute('data-msg-id') || el.id;
            if ((mid && id === mid) || t === text) { target = el; break; }
          }
          if (!target) return 'no-target';
          target.dispatchEvent(new MouseEvent('mouseover', {bubbles:true}));
          const opts = target.querySelectorAll('button.new-chat-message__options-button');
          const dots = opts[opts.length - 1];  // последняя — «...»
          if (!dots) return 'no-dots';
          dots.click();
          return 'clicked';
        }
        """, [mid, text])
        if opened != "clicked":
            log.info("delete: тулбар/сообщение не найдено (%s)", opened)
            return False
        p.wait_for_timeout(500)
        # пункт «Удалить» в контекстном меню
        if not self._click_text_node("Удалить"):
            log.info("delete: пункт «Удалить» не найден")
            p.keyboard.press("Escape")
            return False
        p.wait_for_timeout(500)
        # кнопка «Удалить» в диалоге подтверждения
        try:
            p.get_by_role("button", name="Удалить", exact=True).first.click(timeout=4000)
        except PWTimeout:
            # запасной путь — кнопка по умолчанию
            p.keyboard.press("Enter")
        p.wait_for_timeout(800)
        # проверяем, что сообщение исчезло
        still = p.evaluate(r"""
        ([mid, text]) => {
          const items = [...document.querySelectorAll('[class*="new-chat-message"]')];
          for (const el of items) {
            const body = el.querySelector('[class*="new-chat-message__body"], [class*="message-text"], [class*="__content"]');
            if (!body) continue;
            if ((body.innerText||'').trim() === text) return true;
          }
          return false;
        }
        """, [mid, text])
        return not still

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
