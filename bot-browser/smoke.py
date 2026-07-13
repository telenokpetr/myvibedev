"""Smoke-проверка: заводится ли Zoom web-клиент в контейнере, входит ли бот в
митинг и читается ли чат из DOM. Запуск:

    docker run --rm -e JOIN_URL=... -v botshots:/data bot-browser python smoke.py

Печатает участников и сообщения чата, кладёт скриншот в /data/smoke.png.
"""

import os
import sys
import time

from playwright.sync_api import sync_playwright

URL = os.environ.get("JOIN_URL", "")
NAME = os.environ.get("BOT_NAME", "Веб-модератор")
SHOT = "/data/smoke.png"

if not URL:
    print("нет JOIN_URL"); sys.exit(1)


def dump_chat(page) -> list[dict]:
    """Сообщения чата из DOM (селекторы из ручной разведки 13.07)."""
    return page.evaluate("""() => {
      const nodes = document.querySelectorAll('[class*="new-chat-message__body"], [class*="message-text"]');
      return [...nodes].map(n => (n.innerText || '').trim()).filter(Boolean);
    }""")


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=[
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ])
        ctx = browser.new_context(
            permissions=["microphone", "camera"],
            viewport={"width": 1280, "height": 800},
            locale="ru-RU",
        )
        page = ctx.new_page()
        print("→ goto", URL)
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)

        # Ввод имени
        try:
            name_box = page.get_by_role("textbox", name="Ваше имя")
            name_box.fill(NAME, timeout=15000)
            print("→ имя введено")
        except Exception as exc:  # noqa: BLE001
            print("имя не введено:", exc)

        page.screenshot(path="/data/smoke_prejoin.png")

        # Кнопка входа
        for label in ("Войти", "Join", "Присоединиться"):
            try:
                page.get_by_role("button", name=label, exact=False).first.click(timeout=5000)
                print("→ клик", label)
                break
            except Exception:  # noqa: BLE001
                continue

        page.wait_for_timeout(6000)
        # «Продолжить без аудио и видео», если предложат
        for label in ("Продолжить без аудио", "Join without", "Продолжить"):
            try:
                page.get_by_role("button", name=label, exact=False).first.click(timeout=4000)
                print("→ закрыл диалог аудио:", label)
                break
            except Exception:  # noqa: BLE001
                continue

        page.wait_for_timeout(8000)
        print("URL после входа:", page.url)
        print("title:", page.title())

        # Открыть панель чата
        try:
            page.locator('button[aria-label*="chat panel"], button[aria-label*="Чат"]').first.click(timeout=8000)
            print("→ чат открыт")
        except Exception as exc:  # noqa: BLE001
            print("чат не открылся:", exc)
        page.wait_for_timeout(2500)

        msgs = dump_chat(page)
        print("=== СООБЩЕНИЯ ЧАТА ===")
        for m in msgs:
            print("  •", repr(m))
        print("итого:", len(msgs))

        page.screenshot(path=SHOT)
        print("скриншот:", SHOT)
        browser.close()


if __name__ == "__main__":
    main()
