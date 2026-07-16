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
import os
import re
import time

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

log = logging.getLogger("zoomweb")

# Какой браузер запускать: "chrome" — настоящий Google Chrome (умеет mp4/H.264,
# Chromium из Playwright — НЕТ). Пустая строка → бандловый Chromium.
BROWSER_CHANNEL = os.environ.get("BROWSER_CHANNEL", "chrome")

# Найти видимый элемент по тексту и вернуть центр его прямоугольника — чтобы
# навести/кликнуть МЫШЬЮ. Берём самый глубокий подходящий узел: у внешних
# контейнеров текст тот же, а координаты центра уезжают мимо пункта.
_JS_FIND_TEXT = r"""
(pattern) => {
  const rx = new RegExp(pattern, 'i');
  // Тот же запрос, что и в дампе меню (он пункт находит), + берём элемент с
  // САМЫМ КОРОТКИМ текстом: у внешних контейнеров текст тот же, а центр их
  // прямоугольника уезжает мимо пункта.
  const all = [...document.querySelectorAll('*')]
    .filter(e => rx.test((e.innerText || '')));
  // Кандидаты: видимые и с НЕНУЛЕВЫМ прямоугольником (пустышки с тем же
  // текстом ломали выбор), самый короткий текст = сам пункт, а не контейнер.
  const hit = all
    .map(e => ({ e: e, r: e.getBoundingClientRect(), t: (e.innerText || '').trim() }))
    .filter(o => o.r.width > 0 && o.r.height > 0 && o.e.offsetParent)
    .sort((a, b) => a.t.length - b.t.length);
  if (!hit.length) {
    return { none: true, matchedAnyway: all.length,
             sample: all.slice(0, 3).map(e => e.tagName + ':' +
               (e.innerText || '').trim().slice(0, 30) + ':' +
               JSON.stringify(e.getBoundingClientRect().toJSON())) };
  }
  const o = hit[0];
  return { x: o.r.x + o.r.width / 2, y: o.r.y + o.r.height / 2,
           tag: o.e.tagName, txt: o.t.slice(0, 40) };
}
"""

# Стоит ли галочка на пункте-режиме: Zoom рисует её либо aria-checked, либо
# символом ✓ в строке пункта.
_JS_CHECKED = r"""
(pattern) => {
  const rx = new RegExp(pattern, 'i');
  const row = [...document.querySelectorAll('[role=menuitem], [role=menuitemcheckbox], li, button, div')]
    .filter(e => e.offsetParent && rx.test((e.innerText || '')))
    .sort((a, b) => (a.innerText || '').length - (b.innerText || '').length)[0];
  if (!row) return null;
  const aria = row.getAttribute('aria-checked');
  if (aria !== null) return aria === 'true';
  return /[✓✔]/.test(row.innerText || '') ||
         !!row.querySelector('[class*=check], [class*=selected], svg');
}
"""


def to_web_client(url: str) -> str:
    """Любую Zoom-ссылку привести к web-клиенту app.zoom.us/wc/join/<id>.

    Desktop-ссылка вида us06web.zoom.us/j/<id>?pwd=… в браузере ведёт на
    landing «запустить Zoom», поэтому строим прямой web-URL (проверено
    разведкой 13.07).

    Host-ссылка /wc/<id>/start?…&fromPWA=1 (её даёт кнопка «Начать» в веб-Zoom)
    открывает клиент в IFRAME-обёртке PWA: в верхнем документе кнопок нет,
    бот молча висел на prejoin. Приводим и её к чистому /wc/join/<id> —
    обёртки нет, весь остальной код (чат/мьют/тулбар) работает как обычно."""
    m = (re.search(r"/j/(\d+)", url) or re.search(r"/wc/join/(\d+)", url)
         or re.search(r"/wc/(\d+)/join", url) or re.search(r"/wc/(\d+)/start", url))
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
  // Заголовок группы: «Имя Кому Все ЧЧ:ММ» (RU) / «Name to Everyone HH:MM».
  const HEAD = /(.+?)\s+(?:Кому|to)\s+(?:Все|Everyone|всех|Всем)/;
  function findSender(el) {
    let node = el;
    for (let i = 0; i < 5 && node; i++) {          // вверх по предкам группы
      let sib = node;
      for (let j = 0; j < 4 && sib; j++) {         // и назад по соседям
        const t = (sib.innerText || '').replace(/\n/g, ' ');
        const m = t.match(HEAD);
        if (m && m[1].trim() && m[1].trim().length < 40) return m[1].trim();
        sib = sib.previousElementSibling;
      }
      node = node.parentElement;
    }
    return 'чат';
  }
  const out = [];
  const items = document.querySelectorAll('[class*="new-chat-message"]');
  let lastSender = 'чат';   // пузыри группы без заголовка наследуют имя выше
  for (const el of items) {
    const body = el.querySelector('[class*="new-chat-message__body"], [class*="message-text"], [class*="__content"]');
    if (!body) continue;
    const text = (body.innerText || '').trim();
    if (!text) continue;
    let sender = findSender(el);
    if (sender === 'чат') sender = lastSender;
    else lastSender = sender;
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

    # Постоянный профиль (в томе /data) — чтобы бот не выглядел «чистым»
    # безымянным браузером при каждом заходе (Zoom это ловит антибот-защитой:
    # «Боты не могут присоединяться»). Куки/история сохраняются между заходами.
    PROFILE_DIR = "/data/zoomprofile"
    UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

    def start(self) -> None:
        from app import vcam
        vcam.ensure_dir()
        self._pw = sync_playwright().start()
        # persistent_context = постоянный профиль (нет отдельного browser-объекта).
        #
        # channel="chrome" — НАСТОЯЩИЙ Google Chrome вместо Chromium из Playwright.
        # Chromium собран без проприетарных кодеков: mp4 (H.264/AAC) в нём не
        # играет вообще — <video> падает с «no supported source was found», и в
        # камеру проходил только WebM. Chrome их умеет, mp4 идёт как есть.
        # Если Chrome в образе нет (не пересобрали) — откат на Chromium, чтобы
        # бот не остался лежать: WebM работает и там.
        opts = dict(
            headless=False,
            args=[
                *vcam.launch_args(),    # живая виртуальная камера/микрофон (canvas)
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
            permissions=["microphone", "camera"],
            viewport={"width": 1280, "height": 800},
            locale="ru-RU",
            user_agent=self.UA,
        )
        try:
            self._ctx = self._pw.chromium.launch_persistent_context(
                self.PROFILE_DIR, channel=BROWSER_CHANNEL, **opts)
            log.info("браузер: %s (mp4/H.264 играет)", BROWSER_CHANNEL)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s не запустился (%s) — откат на Chromium (только WebM)",
                        BROWSER_CHANNEL, str(exc)[:120])
            self._ctx = self._pw.chromium.launch_persistent_context(
                self.PROFILE_DIR, **opts)
        # Прячем признаки автоматизации (navigator.webdriver и пр.) — до загрузки
        # любой страницы, чтобы reCAPTCHA/антибот Zoom видел «человеческий» браузер.
        self._ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "window.chrome=window.chrome||{runtime:{}};"
            "Object.defineProperty(navigator,'languages',"
            "{get:()=>['ru-RU','ru','en-US','en']});"
            "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"
        )
        # Виртуальная камера: перехват getUserMedia ставим ДО загрузки страницы,
        # иначе Zoom успеет взять настоящий поток (файл-заглушку).
        self._ctx.add_init_script(vcam.init_script(self._name))
        self._browser = None
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        # Перехват JS-ошибок страницы — чтобы понять, не из-за скрипта ли не
        # отрисовывается шаг пароля (форма пропадает после «Далее»).
        self._console_errs: list[str] = []
        try:
            self.page.on("pageerror",
                         lambda e: self._console_errs.append(f"PAGEERROR: {str(e)[:200]}"))
            self.page.on("console", lambda m: (
                m.type == "error" and self._console_errs.append(f"console.error: {m.text[:200]}")))
        except Exception:  # noqa: BLE001
            pass

    def stop(self) -> None:
        for closer in (self._ctx, self._pw):
            try:
                if closer is self._pw:
                    closer.stop()
                elif closer is not None:
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
        # Кнопка входа: prejoin может дорисоваться позже (особенно после
        # редиректа /start→/join) — ЖДЁМ её и кликаем с повтором, иначе бот
        # молча зависает на «Введите информацию о встрече».
        joined = False
        for attempt in range(3):
            for label in ("Войти", "Join", "Присоединиться", "Join Meeting"):
                try:
                    btn = p.get_by_role("button", name=label, exact=False).first
                    btn.wait_for(state="visible", timeout=6000)
                    btn.click(timeout=4000)
                    log.info("join: клик «%s» (попытка %d)", label, attempt + 1)
                    joined = True
                    break
                except PWTimeout:
                    continue
                except Exception:  # noqa: BLE001
                    continue
            if joined:
                break
            p.wait_for_timeout(2500)     # prejoin ещё грузится — ждём и пробуем снова
        if not joined:
            log.warning("join: кнопка входа так и не нажалась (застряли на prejoin)")
        p.wait_for_timeout(6000)
        log.info("join: url=%s", p.url)
        return "/wc/" in p.url

    # ---- вход в Zoom-аккаунт (через веб-форму) ----

    # ---- импорт готовой сессии (cookie от человека — обход reCAPTCHA) ----

    @staticmethod
    def _convert_cookies(raw: list) -> list:
        """Cookie-Editor JSON → формат Playwright add_cookies."""
        ss_map = {"no_restriction": "None", "none": "None", "unspecified": "Lax",
                  "lax": "Lax", "strict": "Strict"}
        out = []
        for c in raw:
            name, value = c.get("name"), c.get("value")
            domain, path = c.get("domain"), c.get("path", "/")
            if not name or value is None or not domain:
                continue
            ck: dict = {"name": name, "value": value, "domain": domain, "path": path}
            exp = c.get("expirationDate")
            if exp and not c.get("session"):
                ck["expires"] = float(exp)
            ck["httpOnly"] = bool(c.get("httpOnly", False))
            secure = bool(c.get("secure", False))
            ss = ss_map.get(str(c.get("sameSite") or "").lower(), "Lax")
            if ss == "None":
                secure = True          # Chrome требует Secure при SameSite=None
            ck["secure"] = secure
            ck["sameSite"] = ss
            out.append(ck)
        return out

    def import_cookies_and_verify(self, raw: list) -> tuple[bool, str]:
        """Вставить cookie в контекст (сохранятся в профиль) и проверить, что
        бот авторизован (страница профиля не редиректит на вход). Возвращает
        (ok, email/сообщение)."""
        cookies = self._convert_cookies(raw)
        if not cookies:
            return False, "в файле нет пригодных cookie"
        try:
            self._ctx.add_cookies(cookies)
        except Exception as exc:  # noqa: BLE001
            log.warning("add_cookies: %s", exc)
            return False, f"не удалось вставить cookie: {exc}"
        log.info("cookie вставлено: %d, проверяю вход…", len(cookies))
        p = self.page
        try:
            p.goto("https://zoom.us/profile", wait_until="domcontentloaded", timeout=45000)
            p.wait_for_timeout(3500)
        except Exception as exc:  # noqa: BLE001
            log.warning("проверка профиля: %s", exc)
        url = (p.url or "").lower()
        self._shot("cookie_verify")
        if "/signin" in url or "/login" in url:
            return False, ("cookie не авторизуют (сессия истекла/невалидна или "
                           "привязана к другому устройству)")
        # Попробовать вытащить email/имя с профиля (не критично).
        email = ""
        try:
            email = p.evaluate(r"""
            () => {
              const t = document.body.innerText || '';
              const m = t.match(/[\w.+-]+@[\w-]+\.[\w.-]+/);
              return m ? m[0] : '';
            }
            """) or ""
        except Exception:  # noqa: BLE001
            pass
        log.info("cookie: вход подтверждён, url=%s email=%r", p.url, email)
        return True, (email or "Zoom (cookie-сессия)")

    def goto_signin(self) -> None:
        try:
            self.page.goto("https://www.zoom.us/signin",
                           wait_until="domcontentloaded", timeout=60000)
            self.page.wait_for_timeout(2500)
        except Exception as exc:  # noqa: BLE001
            log.warning("goto_signin: %s", exc)

    def _login_state(self) -> str:
        """Определить текущее состояние страницы входа по URL и тексту:
        logged_in | otp | captcha | signin | unknown."""
        p = self.page
        url = (p.url or "").lower()
        try:
            body = (p.evaluate("() => document.body.innerText") or "").lower()
        except Exception:  # noqa: BLE001
            body = ""
        # Вошли: увели с /signin на профиль/личный кабинет.
        if ("/signin" not in url and "/login" not in url
                and any(k in url for k in ("/profile", "/account", "/wc", "myaccount", "/home"))):
            return "logged_in"
        # OTP/подтверждение устройства.
        otp_keys = ("verification code", "verify your identity", "one-time",
                    "проверочный код", "код подтверждения", "введите код",
                    "мы отправили", "enter the code", "verify the")
        if any(k in body for k in otp_keys):
            return "otp"
        # Капча — автоматом не пройти.
        try:
            cap = p.locator('iframe[src*="recaptcha"], iframe[title*="recaptcha" i], '
                            'iframe[src*="hcaptcha"], div.g-recaptcha')
            if cap.count() and cap.first.is_visible():
                return "captcha"
        except Exception:  # noqa: BLE001
            pass
        if "/signin" in url or "/login" in url:
            return "signin"
        return "unknown"

    # Форма входа Zoom — email-first (разведано 13.07): поле email type="text"
    # (плейсхолдер «Электронная почта или номер телефона»), затем БЕЗЫМЯННАЯ
    # кнопка «Далее» → появляется поле пароля. Кнопку по тексту не найти —
    # продвигаемся Enter'ом. Пароль берём ТОЛЬКО видимый (в DOM есть скрытый
    # дубль с tabindex=-1). Мешать может cookie-баннер — гасим заранее.
    _SEL_EMAIL = ('input[name="email"], input#email, input[type="email"], '
                  'input[placeholder*="лектронн" i], input[placeholder*="mail" i]')
    _SEL_PW_VISIBLE = 'input[type="password"]:visible, input[name="password"]:visible'

    def account_sign_in(self, email: str, password: str) -> str:
        """Заполнить форму входа Zoom (email-first) и отправить. Возвращает:
        'logged_in' | 'otp' | 'captcha' | 'error:<текст>'."""
        p = self.page
        try:
            self.goto_signin()
            log.info("вход: страница %s", p.url)
            self._dismiss_cookies()
            self._shot("1_signin")
            # 1) email → поле #email (разведано). fill() вместо click (клик не
            #    проходил: элемент «нестабилен»/перехват указателя; fill фокусирует
            #    и печатает без этих проверок).
            em = p.locator('#email, input[name="account"], input[name="email"], '
                           'input[type="email"]').first
            if em.count() == 0:
                self._dump_login_form("нет поля email")
                return "error:поле email не найдено (см. логи)"
            try:
                em.wait_for(state="visible", timeout=10000)
            except PWTimeout:
                pass
            em.fill(email, timeout=8000)
            em.press("Tab")           # blur → Zoom валидирует email, «Далее» готов
            p.wait_for_timeout(1200)
            self._shot("2_email")
            # 2) продвинуть email-first: клик #signin_btn_next → ЖДЁМ поле
            #    пароля ДОЛГО. Разведано: шаг пароля рендерится ~18-20с после
            #    «Далее» (reCAPTCHA v3 генерит токен, SPA ждёт его под Xvfb).
            pw = self._wait_password(2000)
            if pw is None:
                self._click_next_after_email(em)
                pw = self._wait_password(30000)
                self._shot("3_afternext")
            if pw is None:
                st = self._login_state()
                if st == "captcha":
                    self._shot("x_captcha")
                    return "captcha"
                self._dump_login_form("нет видимого поля пароля после email")
                return "error:поле пароля не появилось (email-first не прошёл, см. логи)"
            self._shot("4_password")
            # 3) пароль — «человеческим» набором (движение мыши + задержки), это
            #    поднимает балл reCAPTCHA v3 (она следит за поведением).
            self._human_click(pw)
            try:
                pw.press_sequentially(password, delay=90)
            except Exception:  # noqa: BLE001
                pw.fill(password, timeout=8000)
            p.wait_for_timeout(500)
            # 4) submit кнопкой #js_btn_login, с авто-ретраем на ошибке reCAPTCHA
            #    («Произошла ошибка ввода reCAPTCHA. Повторите попытку.»).
            btn = p.locator("#js_btn_login").first
            for attempt in range(3):
                if btn.count():
                    self._human_click(btn)
                else:
                    p.keyboard.press("Enter")
                p.wait_for_timeout(3500)
                self._shot(f"5_submit_{attempt}")
                st = self._login_state()
                if st in ("logged_in", "otp", "captcha"):
                    log.info("вход: после submit — %s", st)
                    return st
                if self._recaptcha_error():
                    log.info("вход: ошибка reCAPTCHA, повтор submit (%d/3)", attempt + 1)
                    p.wait_for_timeout(2500)
                    continue
                break
            return self._login_result_after_submit()
        except Exception as exc:  # noqa: BLE001
            log.warning("account_sign_in: %s", exc)
            return f"error:{exc}"

    def _human_click(self, locator, timeout: int = 5000) -> bool:
        """Клик с «человеческим» движением мыши к центру элемента (для балла
        reCAPTCHA v3). Фолбэк — обычный click."""
        p = self.page
        try:
            box = locator.bounding_box(timeout=timeout)
            if box:
                cx = box["x"] + box["width"] / 2
                cy = box["y"] + box["height"] / 2
                p.mouse.move(cx - 45, cy - 30, steps=8)
                p.wait_for_timeout(120)
                p.mouse.move(cx, cy, steps=12)
                p.wait_for_timeout(160)
                p.mouse.click(cx, cy)
                return True
        except Exception:  # noqa: BLE001
            pass
        try:
            locator.click(timeout=timeout)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _recaptcha_error(self) -> bool:
        """На странице тост «Произошла ошибка ввода reCAPTCHA»?"""
        try:
            body = (self.page.evaluate("() => document.body.innerText") or "").lower()
        except Exception:  # noqa: BLE001
            return False
        return "recaptcha" in body and any(
            k in body for k in ("ошибка", "повторите", "error", "try again"))

    def _wait_password(self, timeout_ms: int):
        """Дождаться ВИДИМОГО поля пароля (появляется на 2-м шаге email-first).
        Возвращает Locator или None."""
        p = self.page
        waited, step = 0, 400
        while waited < timeout_ms:
            loc = p.locator(self._SEL_PW_VISIBLE).first
            if loc.count() > 0:
                return loc
            p.wait_for_timeout(step)
            waited += step
        return None

    def _dismiss_cookies(self) -> None:
        """Погасить cookie-баннер (жмём «Отклонить», приватность по умолчанию)."""
        for name in ("Отклонить файлы cookie", "Отклонить", "Decline", "Reject"):
            try:
                self.page.get_by_role("button", name=name, exact=False).first.click(timeout=1500)
                log.info("вход: cookie-баннер закрыт (%s)", name)
                return
            except PWTimeout:
                continue
            except Exception:  # noqa: BLE001
                return

    def _click_next_after_email(self, em) -> bool:
        """Нажать кнопку «Далее» email-first — Zoom: id='signin_btn_next'
        (разведано 13.07). Фолбэки — по id-подстроке/submit."""
        p = self.page
        for sel in ("#signin_btn_next", "button#signin_btn_next",
                    'button[id*="next" i]', 'button[type="submit"]'):
            try:
                b = p.locator(sel).first
                if b.count() and b.is_visible():
                    b.click(timeout=3000)
                    log.info("вход: клик по кнопке продолжения (%s)", sel)
                    return True
            except Exception:  # noqa: BLE001
                continue
        log.info("вход: кнопка продолжения email-first не найдена")
        return False

    def _dump_login_form(self, why: str) -> None:
        """DEBUG: разобрать, ПОЧЕМУ нет шага пароля — рендер не догрузился или
        блок reCAPTCHA. Ждём ещё, снимаем поздний скриншот, анализируем DOM и
        JS-ошибки консоли."""
        p = self.page
        log.warning("вход: %s; URL=%s; текст: %s", why, p.url, self._page_snippet())
        p.wait_for_timeout(6000)                 # дать шансу дорисоваться
        self._shot("6_late")
        try:
            info = p.evaluate(r"""
            () => ({
              htmlLen: document.documentElement.innerHTML.length,
              hasPasswordInput: !!document.querySelector('input[type=password]'),
              inputCount: document.querySelectorAll('input').length,
              hasRecaptchaIframe: !!document.querySelector('iframe[src*="recaptcha"]'),
              grecaptcha: typeof window.grecaptcha,
              readyState: document.readyState,
              signinHtml: (document.querySelector('#signinContainer, [class*="signin"], #app, main')||{outerHTML:''})
                          .outerHTML.replace(/\s+/g,' ').slice(0,700)
            })
            """)
            log.warning("вход АНАЛИЗ: pwInput=%s inputs=%s recaptchaIframe=%s "
                        "grecaptcha=%s ready=%s htmlLen=%s",
                        info.get("hasPasswordInput"), info.get("inputCount"),
                        info.get("hasRecaptchaIframe"), info.get("grecaptcha"),
                        info.get("readyState"), info.get("htmlLen"))
            log.warning("вход АНАЛИЗ signin-контейнер: %s", info.get("signinHtml"))
        except Exception as exc:  # noqa: BLE001
            log.warning("вход: анализ DOM не удался: %s", exc)
        errs = getattr(self, "_console_errs", [])
        if errs:
            log.warning("вход JS-ошибки консоли (%d): %s", len(errs), errs[-12:])
        else:
            log.warning("вход: JS-ошибок консоли не зафиксировано")
        try:
            self.screenshot("/data/last.png")
        except Exception:  # noqa: BLE001
            pass
        try:
            info = p.evaluate(r"""
            () => {
              const vis = el => { const r=el.getBoundingClientRect();
                return r.width>0 && r.height>0 && getComputedStyle(el).visibility!=='hidden'; };
              const inp = [...document.querySelectorAll('input')].slice(0,10).map(i =>
                ({type:i.type,name:i.name,id:i.id,ph:i.placeholder,vis:vis(i)}));
              const btn = [...document.querySelectorAll('button')].slice(0,12).map(b =>
                ({tx:(b.innerText||'').trim().slice(0,20),al:b.getAttribute('aria-label'),
                  id:b.id,vis:vis(b)}));
              return {inputs:inp, buttons:btn};
            }
            """)
            log.warning("вход DUMP inputs: %s", info.get("inputs"))
            log.warning("вход DUMP buttons: %s", info.get("buttons"))
        except Exception as exc:  # noqa: BLE001
            log.warning("вход: дамп формы не удался: %s", exc)

    def _login_result_after_submit(self) -> str:
        """Опросить состояние несколько секунд после отправки формы входа."""
        p = self.page
        for _ in range(10):
            st = self._login_state()
            if st in ("logged_in", "otp", "captcha"):
                log.info("вход: состояние после отправки — %s (%s)", st, p.url)
                return st
            p.wait_for_timeout(1000)
        # Остались на signin — вероятно, неверные данные/ошибка на странице.
        snap = self._page_snippet()
        log.info("вход: остались на signin, текст: %s", snap)
        return "error:вход не подтверждён (неверные данные или доп. проверка)"

    def account_submit_otp(self, code: str) -> str:
        """Ввести OTP-код на странице подтверждения. 'logged_in' | 'error:<текст>'."""
        p = self.page
        try:
            box = p.locator('input[autocomplete="one-time-code"], '
                            'input[name*="code" i], input[inputmode="numeric"], '
                            'input[maxlength="6"]').first
            if box.count() == 0:
                return "error:поле кода не найдено"
            box.click(timeout=4000)
            box.fill(code, timeout=4000)
            for lbl in ("Verify", "Submit", "Подтвердить", "Continue", "Продолжить"):
                try:
                    p.get_by_role("button", name=lbl, exact=False).first.click(timeout=2500)
                    break
                except PWTimeout:
                    continue
            else:
                p.keyboard.press("Enter")
            p.wait_for_timeout(3000)
            for _ in range(8):
                st = self._login_state()
                if st == "logged_in":
                    return "logged_in"
                if st == "captcha":
                    return "error:капча после кода — вход невозможен автоматически"
                p.wait_for_timeout(1000)
            return "error:код не принят или требуется доп. проверка"
        except Exception as exc:  # noqa: BLE001
            log.warning("account_submit_otp: %s", exc)
            return f"error:{exc}"

    def _shot(self, tag: str) -> None:
        """DEBUG: скриншот шага входа в /data/login_<tag>.png (для разбора)."""
        try:
            self.screenshot(f"/data/login_{tag}.png")
            log.info("вход: скриншот %s", tag)
        except Exception:  # noqa: BLE001
            pass

    def _page_snippet(self) -> str:
        try:
            t = self.page.evaluate("() => document.body.innerText") or ""
            return " ".join(t.split())[:200]
        except Exception:  # noqa: BLE001
            return ""

    def in_waiting_room(self) -> bool:
        """Зал ожидания или «организатор ещё не начал конференцию». URL там
        уже /wc/… — по URL от митинга не отличить (из-за этого status был live
        до впуска). Определяем по тексту страницы: тулбара и чата в зале нет."""
        try:
            body = (self.page.evaluate("() => document.body.innerText") or "").lower()
        except Exception:  # noqa: BLE001
            return False
        keys = ("let you in", "waiting for the host", "разрешит вам войти",
                "ожидание организатора", "зал ожидания", "скоро начнёт",
                # Новый экран зала: «Организатор присоединился. Мы сообщили им,
                # что вы здесь.» Его не ловили — бот рапортовал live и искал
                # чат в пустоте («кнопка чата не найдена»).
                "мы сообщили им, что вы здесь", "we've let them know you're here",
                "организатор присоединился")
        return any(k in body for k in keys)

    def complete_join(self) -> bool:
        """Шаги ПОСЛЕ реального входа (после впуска из зала ожидания) — в зале
        этих кнопок нет, щёлкать их при join бессмысленно.
        Подключить звук компьютера (НЕ «Продолжить без аудио» — иначе микрофон
        не транслируется; фейковый микрофон отдаётся браузером из файла),
        включить камеру, открыть панель чата."""
        p = self.page
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
        log.info("complete_join: url=%s chat_open=%s", p.url, ok)
        return ok

    def _enable_media_in_meeting(self) -> None:
        self.ensure_video_on()
        # Сразу снимаем зумовский шумодав: иначе звук ролика/музыки Zoom режет
        # как «шум» и слышно только речь. Рычаг — в Настройках, а НЕ в меню
        # тулбара (галочка там оказалась не тем переключателем).
        try:
            res = self.ensure_browser_noise_suppression()
            log.info("звуковой профиль при входе: %s", res)
        except Exception as exc:  # noqa: BLE001
            log.warning("звуковой профиль при входе не выставлен: %s", str(exc)[:100])

    def ensure_video_on(self) -> None:
        """Включить камеру, если выключена. Кнопка тумблера в тулбаре: когда
        камера ВЫКЛ, её aria-label начинается со «start»/«начать» — по этому и
        отличаем (кликать всегда нельзя — тумблер выключит включённую).

        Playwright-клик по этой кнопке молча не срабатывал (тулбар всплывает и
        уезжает, элемент «двигается» → клик уходит в никуда, а исключение мы
        глотали). JS-клик по ней Zoom принимает нормально — он и остаётся
        фолбэком, иначе бот сидит с выключенной камерой и в неё ничего не идёт.
        """
        sel = ('button[aria-label*="start my video" i], '
               'button[aria-label*="start video" i], '
               'button[aria-label*="начать видео" i], '
               'button[aria-label*="включить видео" i]')
        try:
            off = self.page.locator(sel)
            if off.count() == 0:
                return                      # камера уже включена («stop my video»)
            try:
                off.first.click(timeout=2000)
            except Exception:               # noqa: BLE001
                self.page.evaluate(
                    "(s) => { const b = document.querySelector(s);"
                    " if (b) b.click(); }", sel)
            if self.page.locator(sel).count() == 0:
                log.info("камера включена в митинге")
            else:
                log.warning("камера: кнопку нажали, но тумблер остался в «выкл»")
        except Exception as exc:  # noqa: BLE001
            log.warning("камера: не удалось включить: %s", str(exc)[:100])

    def _open_chat(self) -> bool:
        # Панель чата в web рендерит сообщения только когда открыта. Кнопка —
        # aria «open the chat panel» (может нести суффикс «N unread message»).
        # ВАЖНО: тулбар Zoom прячется при простое (в Xvfb мышь не двигается) —
        # без «шевеления» мышью кнопки чата нет в DOM и чат не открыть.
        self._reveal_toolbar()
        try:
            self.page.locator(
                'button[aria-label*="chat panel"], button[aria-label*="open the chat"], '
                'button[aria-label*="Чат" i], button[aria-label*="chat" i], '
                'button[class*="footer-chat-button"] , div[class*="footer-chat"] button'
            ).first.click(timeout=6000)
            self.page.wait_for_timeout(1500)
            return True
        except PWTimeout:
            log.info("chat: кнопка чата не найдена (тулбар скрыт?)")
            return False

    def chat_is_open(self) -> bool:
        """Открыта ли ПАНЕЛЬ чата. Проверяем видимое ПОЛЕ ВВОДА — оно есть
        только в открытой панели. По сообщениям判 нельзя: их класс носит и
        всплывашка-превью нового сообщения при ЗАКРЫТОЙ панели (тогда полного
        списка в DOM нет и удаление не находит сообщения)."""
        try:
            field = self.page.locator(
                'div[contenteditable="true"], [role="textbox"][contenteditable="true"], '
                'textarea[placeholder*="ообщение" i]')
            return field.count() > 0 and field.last.is_visible()
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

    # «Mute»/«Выключить звук» — кнопка мьюта; «Unmute»/«Включить звук» (в т.ч.
    # «Попросить включить звук») — участник уже замьючен. Якорь ^ отсекает
    # «Unmute» от «mute», кириллица не пересекается («включить» не подстрока
    # «выключить»).
    _RX_MUTE = re.compile(r"^\s*(mute\b|выключить звук)", re.I)
    _RX_UNMUTE = re.compile(r"unmute|включить звук", re.I)

    def mute_participant(self, name: str) -> bool:
        """Замьютить участника по имени: открыть панель «Участники», навести
        РЕАЛЬНЫМ hover на строку (кнопки всплывают только от курсора, как «...»
        в чате) → кнопка «Mute»/«Выключить звук». Кнопки строки бывают без
        aria-label — ищем и по видимому тексту; «Unmute» = уже замьючен,
        считаем успехом. Нет прямой кнопки — фолбэк через меню «Ещё» строки.
        Панель чата после нас возвращает вызывающий (ensure_chat_open)."""
        try:
            # Панель участников не отдаёт ростер автоматизации — мьютим через
            # ВИДЕО-ПЛИТКУ участника (hover → «...» → «Выключить звук»).
            return self._mute_via_tile(name)
        except Exception as exc:  # noqa: BLE001
            log.warning("mute_participant: %s", exc)
            return False

    def _mute_via_tile(self, name: str) -> bool:
        """Мьют через видео-плитку участника (host-контрол). Наводим РЕАЛЬНЫЙ
        hover на плитку → всплывает «...»/меню → «Выключить звук»/Mute."""
        p = self.page
        self._reveal_toolbar()
        # Плитка участника: контейнер видео с его именем (в футере или alt img).
        tile = p.locator('[class*="video-avatar__avatar"]').filter(has_text=name).first
        if tile.count() == 0:
            tile = p.locator(f'[class*="video-avatar"]:has(img[alt="{name}"])').first
        if tile.count() == 0:
            log.info("mute-tile: плитка %r не найдена", name)
            return False
        try:
            tile.scroll_into_view_if_needed(timeout=2000)
            tile.hover(timeout=2000)
            p.wait_for_timeout(500)
        except Exception:  # noqa: BLE001
            pass
        # Кнопки, всплывшие на плитке (разведано: у замьюченного — «Попросить
        # включить звук», у говорящего — «Выключить звук», плюс «More managing
        # options» — меню «...»).
        labels: list = []
        try:
            labels = tile.evaluate(
                "el => [...el.querySelectorAll('button,[role=\"button\"]')]"
                ".map(b => (b.getAttribute('aria-label')||b.innerText||'').trim()).slice(0,10)")
            log.info("mute-tile: кнопки на плитке %r: %s", name, labels)
        except Exception:  # noqa: BLE001
            pass
        # Уже замьючен? («Попросить включить звук» / «Ask to Unmute») — цель
        # достигнута, ничего не жмём (иначе РАЗмьютим человека).
        for lb in labels:
            if self._RX_UNMUTE.search(lb) or "опросить включить" in lb:
                log.info("mute-tile: %r уже замьючен — ок", name)
                return True
        # Прямая кнопка «Выключить звук»/Mute на плитке.
        btns = tile.locator('button, [role="button"]')
        for i in range(min(btns.count(), 10)):
            b = btns.nth(i)
            try:
                cap = (b.get_attribute("aria-label") or b.inner_text() or "").strip()
            except Exception:  # noqa: BLE001
                continue
            if self._RX_MUTE.search(cap):
                b.click(timeout=1800)
                log.info("mute-tile: %r замьючен кнопкой на плитке (%r)", name, cap)
                return True
        # Иначе — открыть меню «...» плитки и выбрать «Выключить звук».
        menu = tile.locator('button[aria-label*="more" i], button[aria-label*="Ещё" i], '
                            'button[aria-label*="еще" i], button[aria-label*="menu" i], '
                            'button[class*="more"]')
        if menu.count() == 0:
            log.info("mute-tile: у плитки %r нет ни кнопки мьюта, ни «...»", name)
            return False
        try:
            menu.last.click(timeout=1800)
        except Exception:  # noqa: BLE001
            log.info("mute-tile: не кликнулось «...» у %r", name)
            return False
        p.wait_for_timeout(600)
        # Пункт «Выключить звук»/Mute в контекст-меню плитки.
        items = p.locator('[role="menuitem"], [class*="dropdown"] li, [class*="menu"] li, '
                          '[class*="menu"] a')
        for i in range(min(items.count(), 20)):
            try:
                cap = (items.nth(i).inner_text() or "").strip()
            except Exception:  # noqa: BLE001
                continue
            if self._RX_MUTE.search(cap):
                items.nth(i).click(timeout=1800)
                log.info("mute-tile: %r замьючен через меню плитки (%r)", name, cap)
                return True
        texts = p.evaluate(
            "() => [...document.querySelectorAll('[role=\"menuitem\"],li,[class*=\"menu\"] *')]"
            ".map(e=>e.children.length===0?(e.innerText||'').trim():'')"
            ".filter(t=>t&&t.length<30).slice(0,15)")
        log.info("mute-tile: пункт мьюта не найден у %r; в меню: %s", name, texts)
        p.keyboard.press("Escape")
        return False

    def _reveal_toolbar(self) -> None:
        """Показать нижний тулбар Zoom — он прячется при простое (в Xvfb мышь
        не двигается), из-за чего кнопки панелей исчезают из DOM."""
        for xy in ((640, 795), (400, 780), (640, 760)):
            try:
                self.page.mouse.move(*xy)
                self.page.wait_for_timeout(150)
            except Exception:  # noqa: BLE001
                pass

    def _participant_row(self, name: str):
        """Строку участника ищем НЕ по угадываемому классу (он даёт 0), а по
        имени: сперва пробуем контейнеры-кандидаты с фильтром по тексту, затем
        фолбэк — узел с именем → ближайший предок, содержащий кнопки. Возвращает
        Locator строки или None (с логом)."""
        p = self.page
        rows = p.locator('[class*="participants-item"], [class*="participant-item"], '
                         'li[class*="participant"], [role="listitem"], '
                         '[class*="participants-ul"] > *')
        cnt = rows.count()
        row = rows.filter(has_text=name).first
        if cnt and row.count():
            log.info("mute: строка по классу-контейнеру (всего строк %d), имя %r", cnt, name)
            return row
        # Фолбэк: привязка к тексту имени → ближайший предок с кнопкой.
        log.info("mute: строк по классу нет (%d) — ищу %r по тексту имени", cnt, name)
        try:
            node = p.get_by_text(name, exact=False).last
            if node.count() == 0:
                log.info("mute: имя %r не найдено на странице", name)
                return None
            row = node.locator(
                'xpath=ancestor-or-self::*[.//button or .//*[@role="button"]][1]')
            if row.count() == 0:
                log.info("mute: у %r нет предка с кнопками (строка не распознана)", name)
                return None
            return row.first
        except Exception as exc:  # noqa: BLE001
            log.warning("mute: поиск строки по имени %r: %s", name, exc)
            return None

    def _find_mute_button(self, row):
        """Кнопка мьюта среди кнопок строки — по aria-label И видимому тексту.
        Возвращает ('found', btn) / ('muted', None) / ('none', None). Все
        подписи пишутся в лог — по ним выверяется селектор на живом тесте."""
        labels = []
        try:
            buttons = row.locator("button")
            for i in range(min(buttons.count(), 12)):
                b = buttons.nth(i)
                try:
                    cap = (b.get_attribute("aria-label") or b.inner_text() or "").strip()
                except Exception:  # noqa: BLE001
                    continue
                labels.append(cap)
                if self._RX_MUTE.search(cap):
                    return "found", b
                if self._RX_UNMUTE.search(cap):
                    return "muted", None
        except Exception as exc:  # noqa: BLE001
            log.warning("mute: обход кнопок строки: %s", exc)
        log.info("mute: прямой кнопки нет, подписи кнопок строки: %s", labels)
        return "none", None

    def _mute_via_row_menu(self, row, name: str) -> bool:
        """Фолбэк: меню «Ещё»/«More» строки участника → пункт мьюта. При
        неудаче логируем пункты меню (как в delete) и закрываем Escape."""
        p = self.page
        try:
            row.locator('button[aria-label*="more" i], button[aria-label*="Ещё" i], '
                        'button[aria-label*="еще" i]').last.click(timeout=2000)
        except Exception:  # noqa: BLE001
            log.info("mute: у %r нет ни кнопки мьюта, ни «Ещё»", name)
            return False
        p.wait_for_timeout(600)                # меню-портал успевает отрисоваться
        items = p.locator('[role="menuitem"], [class*="dropdown"] a, [class*="menu"] li')
        try:
            for i in range(min(items.count(), 15)):
                it = items.nth(i)
                try:
                    cap = (it.inner_text() or "").strip()
                except Exception:  # noqa: BLE001
                    continue
                if self._RX_MUTE.search(cap):
                    it.click(timeout=2000)
                    log.info("mute: %r замьючен через меню строки (%r)", name, cap)
                    return True
        except Exception as exc:  # noqa: BLE001
            log.warning("mute: обход меню строки: %s", exc)
        texts = p.evaluate(
            "() => [...document.querySelectorAll('[role=\"menuitem\"],li,"
            "[class*=\"dropdown\"] *,[class*=\"menu\"] *')]"
            ".map(e=>e.children.length===0?(e.innerText||'').trim():'')"
            ".filter(t=>t&&t.length<30).slice(0,12)")
        log.info("mute: пункт мьюта не найден у %r; в меню: %s", name, texts)
        p.keyboard.press("Escape")
        return False

    def debug_eval(self, js: str):
        """DEBUG: выполнить произвольный JS на странице бота и вернуть результат.
        js — тело функции-стрелки, напр. '() => document.title'."""
        return self.page.evaluate(js)

    def debug_audio_menu(self) -> list:
        """DEBUG: открыть меню звука НАСТОЯЩИМ кликом и вернуть его пункты.

        Синтетический click() Zoom для меню игнорирует (как и в случае «...» у
        сообщений) — открываем через Playwright. Нужно, чтобы найти пункт
        «оригинальный звук»: иначе Zoom считает наш поток микрофоном и режет
        всё, кроме голоса (музыка/фонограмма из ролика пропадают)."""
        p = self.page
        self._reveal_toolbar()
        try:
            p.locator('button[aria-label="More audio controls"]').first.click(timeout=4000)
        except Exception as exc:  # noqa: BLE001
            return [{"error": f"меню звука не открылось: {str(exc)[:80]}"}]
        p.wait_for_timeout(1200)
        return p.evaluate(
            "() => [...document.querySelectorAll('[role=menuitem], [role=menuitemcheckbox],"
            " li, a, button')].filter(e => e.offsetParent && (e.innerText || '').trim())"
            ".map(e => ({t: (e.innerText || '').trim().slice(0, 45),"
            " role: e.getAttribute('role') || e.tagName,"
            " checked: e.getAttribute('aria-checked')})).slice(0, 40)")

    def debug_click(self, selector: str, dump: bool = True, reveal: bool = True):
        """DEBUG: НАСТОЯЩИЙ клик по селектору + дамп того, что появилось.
        Синтетический click() Zoom для меню игнорирует — нужен Playwright.

        reveal=False — когда меню уже открыто: движение мыши (reveal) его
        закрывает, и следующий клик уходит в пустоту."""
        p = self.page
        if reveal:
            self._reveal_toolbar()
        try:
            p.locator(selector).first.click(timeout=4000)
        except Exception as exc:  # noqa: BLE001
            return {"clicked": False, "error": str(exc)[:120]}
        p.wait_for_timeout(1200)
        return {"clicked": True, "seen": self.debug_dump() if dump else None}

    def debug_hover(self, selector: str, dump: bool = True):
        """DEBUG: НАСТОЯЩЕЕ наведение мыши + дамп появившегося.
        Подменю Zoom («Подавление фонового шума» → уровни) раскрывается по
        hover, а не по клику — кликом его не достать."""
        p = self.page
        try:
            p.locator(selector).first.hover(timeout=4000, force=True)
        except Exception as exc:  # noqa: BLE001
            return {"hovered": False, "error": str(exc)[:120]}
        p.wait_for_timeout(1000)
        return {"hovered": True, "seen": self.debug_dump() if dump else None}

    def debug_dump(self):
        """DEBUG: всё видимое кликабельное на экране (текст + роль + aria)."""
        return self.page.evaluate(
            "() => [...document.querySelectorAll('[role=menuitem],"
            " [role=menuitemcheckbox], [role=option], [role=tab], li, a, button,"
            " [class*=dropdown] *, [class*=menu] *')]"
            ".filter(e => e.offsetParent && (e.innerText || '').trim().length < 60"
            " && (e.innerText || '').trim())"
            ".map(e => ((e.innerText || '').trim().slice(0, 45) + ' |'"
            " + (e.getAttribute('role') || e.tagName) + '|'"
            " + (e.getAttribute('aria-label') || '').slice(0, 30)))"
            ".filter((v, i, a) => a.indexOf(v) === i).slice(0, 60)")

    def enable_original_sound(self, level: str = "Отключить") -> dict:
        """Отключить подавление фонового шума у бота.

        Zoom считает наш поток микрофоном и по умолчанию давит всё, кроме речи:
        музыка и фонограмма ролика доходят рваными или пропадают совсем
        (проверено вживую — «слышно только голос»). «Оригинального звука» в
        веб-клиенте НЕТ; его роль играет меню «Подавление фонового шума» с
        уровнями Авто/Низкий/Средний/Высокий/Отключить.

        Всё делается ОДНИМ вызовом: между вызовами меню закрывается от движения
        мыши (_reveal_toolbar), и клик по пункту уходит в пустоту.
        """
        p = self.page
        rx_ns = "подавление фонового шума|background noise"
        # Кнопка «More audio controls» — ПЕРЕКЛЮЧАТЕЛЬ: если меню осталось
        # открытым от прошлой попытки, клик его ЗАКРОЕТ (ловились пустые дампы,
        # matchedAnyway=0). Поэтому сначала гасим меню, потом открываем; если с
        # первого раза не открылось — жмём ещё раз.
        p.keyboard.press("Escape")
        p.wait_for_timeout(300)
        box = None
        for attempt in (1, 2):
            self._reveal_toolbar()
            try:
                p.locator('button[aria-label="More audio controls"]').first.click(timeout=4000)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"меню звука не открылось: {str(exc)[:80]}"}
            p.wait_for_timeout(1000)
            box = p.evaluate(_JS_FIND_TEXT, rx_ns)
            if box and not box.get("none"):
                break
            log.info("шумоподавление: меню пустое (попытка %d) — пробую ещё", attempt)
        # «Подавление фонового шума» — это РЕЖИМ-ГАЛОЧКА в разделе «Microphone
        # modes», а не подменю с уровнями: включён — стоит ✓, надо просто снять.
        # Кликаем МЫШЬЮ по координатам: локаторы Playwright этот пункт не берут
        # (text=/get_by_text таймаутятся, хотя JS его видит). Всё в ОДНОМ вызове —
        # цикл модерации каждую секунду трогает страницу и меню закрывается.
        if not box or box.get("none"):
            p.keyboard.press("Escape")
            return {"ok": False, "error": "пункт шумоподавления не виден в меню",
                    "debug": box}
        p.mouse.click(box["x"], box["y"])
        p.wait_for_timeout(700)
        # Проверяем: снова открываем меню и смотрим, ушла ли галочка.
        self._reveal_toolbar()
        try:
            p.locator('button[aria-label="More audio controls"]').first.click(timeout=4000)
            p.wait_for_timeout(900)
        except Exception:  # noqa: BLE001
            return {"ok": True, "note": "кликнул, но проверить состояние не смог"}
        still_on = p.evaluate(_JS_CHECKED, "подавление фонового шума|background noise")
        p.keyboard.press("Escape")
        log.info("шумоподавление после клика: %s", "ВСЁ ЕЩЁ ВКЛ" if still_on else "снято")
        return {"ok": not still_on, "checked_after": still_on, "item": box}

    def open_audio_settings(self) -> dict:
        """Открыть Настройки → Звук и показать «Звуковой профиль».

        Настоящее место настройки шумодава — не меню тулбара, а этот диалог:
        «Звуковой профиль» = радиокнопки (удаление шума в Zoom / встроенное в
        браузер / оригинальный звук). Для музыки нужен оригинальный звук.
        Клик — мышью по координатам: локаторы Playwright по этим пунктам
        таймаутятся, хотя JS их видит.
        """
        p = self.page
        p.keyboard.press("Escape")
        p.wait_for_timeout(300)
        box = None
        for _ in (1, 2):
            self._reveal_toolbar()
            try:
                p.locator('button[aria-label="More audio controls"]').first.click(timeout=4000)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"меню звука не открылось: {str(exc)[:80]}"}
            p.wait_for_timeout(1000)
            box = p.evaluate(_JS_FIND_TEXT, "^настройки звука$|^audio settings$")
            if box and not box.get("none"):
                break
        if not box or box.get("none"):
            p.keyboard.press("Escape")
            return {"ok": False, "error": "«Настройки звука» не найдены", "debug": box}
        p.mouse.click(box["x"], box["y"])
        p.wait_for_timeout(1800)          # диалог рисуется не мгновенно
        return {"ok": True, "seen": self.debug_dump()}

    def set_audio_profile(self, profile: str = "встроенное в браузер|browser built-in") -> dict:
        """Выбрать звуковой профиль в ОТКРЫТОМ диалоге настроек (радиокнопка)."""
        p = self.page
        box = p.evaluate(_JS_FIND_TEXT, profile)
        if not box or box.get("none"):
            return {"ok": False, "error": "профиль не найден в диалоге",
                    "debug": box, "seen": self.debug_dump()}
        p.mouse.click(box["x"], box["y"])
        p.wait_for_timeout(700)
        log.info("звуковой профиль: выбран %s", box.get("txt"))
        return {"ok": True, "clicked": box}

    def ensure_browser_noise_suppression(self) -> dict:
        """Снять зумовский шумодав с нашего звука — РАБОЧИЙ путь, вызывается при
        входе.

        Zoom считает синтетический трек микрофоном и «Удалением фонового шума»
        съедает музыку (доходит только голос). Ставим «Встроенное в браузер
        шумоподавление»: к WebAudio-треку браузер обработку не применяет, т.е.
        это и есть «выключить». В меню тулбара нужного рычага НЕТ — только
        здесь, в Настройках.
        """
        res = self.open_audio_settings()
        if not res.get("ok"):
            return {"ok": False, "step": "открыть настройки", **res}
        out = self.set_audio_profile()
        # Диалог обязательно закрыть: поверх него не работают чат и модерация.
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(500)
        return out

    # ---- виртуальная камера (живой поток с canvas, см. vcam.py) ----

    def vcam_play(self, name: str):
        """Пустить ролик в камеру бота. Трек не пересоздаётся — Zoom смены не
        замечает, картинка просто меняется."""
        from app import vcam
        url = vcam.file_url(name)
        # Тулбар Zoom прячется при простое (в Xvfb мышь не двигается) — иначе
        # кнопка камеры невидима и клик не проходит.
        self._reveal_toolbar()
        self.ensure_video_on()   # без включённой камеры поток никуда не пойдёт
        return self.page.evaluate(
            "(u) => window.__vcam ? window.__vcam.play(u) : 'vcam не установлен'", url)

    def vcam_stop(self):
        from app import vcam  # noqa: F401  (симметрия с vcam_play)
        return self.page.evaluate(
            "() => window.__vcam ? window.__vcam.stop() : false")

    def vcam_command(self, action: str, value: float | None = None):
        return self.page.evaluate(
            "([a, v]) => { if (!window.__vcam) return false;"
            " return a === 'volume' ? window.__vcam.volume(v) : window.__vcam[a](); }",
            [action, value])

    def vcam_state(self):
        return self.page.evaluate(
            "() => window.__vcam ? window.__vcam.state() : {error: 'vcam не установлен'}")

    def debug_dump_participants(self) -> None:
        """DEBUG: открыть панель участников и вывести в лог реальную структуру —
        сработал ли клик по кнопке панели и КАКИЕ элементы реально являются
        строками участников (tag+class+role+текст). По этому дампу выверяется
        селектор строк (сейчас находит 0)."""
        p = self.page
        try:
            # Чат занимает правый док и мешает раскрыться панели участников —
            # сперва закрываем чат.
            try:
                p.locator('button[aria-label*="chat panel" i], '
                          'button[aria-label*="Чат" i], '
                          'div[class*="footer-chat"] button').first.click(timeout=2500)
                p.wait_for_timeout(900)
                log.info("DUMP: чат закрыт перед открытием участников")
            except Exception:  # noqa: BLE001
                pass
            # Тулбар Zoom прячется при простое — «шевелим» мышью, чтобы показать.
            for xy in ((640, 795), (400, 780), (640, 760)):
                try:
                    p.mouse.move(*xy)
                    p.wait_for_timeout(200)
                except Exception:  # noqa: BLE001
                    pass
            # НЕ по «частник» — оно матчит «Оставить Участник» из диалога
            # конфликта хоста. Берём именно footer-кнопку участников.
            toggles = p.locator('div[class*="footer-participants-button"] button, '
                                 'button[class*="footer-participants"], '
                                 'button[aria-label*="manage participant" i], '
                                 'button[aria-label*="participants list" i]')
            log.info("DUMP: кнопок-панели участников найдено: %d", toggles.count())
            if toggles.count():
                al0 = ""
                try:
                    al0 = toggles.first.get_attribute("aria-label") or ""
                except Exception:  # noqa: BLE001
                    pass
                log.info("DUMP: кликаю кнопку участников aria=%r", al0)
                toggles.first.click(timeout=3000)
            p.wait_for_timeout(1800)
            self.screenshot("/data/last.png")   # увидеть панель глазами
            panel = p.evaluate(r"""
            () => {
              // Ищем контейнер, где реально есть список участников: элемент с
              // несколькими потомками, содержащими имена (по aria-label кнопок
              // «... more» участников или классам-строкам).
              const sels=['[class*="participants-item"]','[class*="participants-li"]',
                '[class*="participants-ul"]','[class*="participants-list"]',
                '[class*="participants-row"]','[class*="participant-item"]',
                '[class*="attendee"]','li[class*="participant"]','[role="listitem"]'];
              const counts={};
              for(const s of sels){const n=document.querySelectorAll(s).length; if(n)counts[s]=n;}
              // Кнопки «more»/мьют внутри строк участников (появляются у ростера):
              const rowBtns=[...document.querySelectorAll('button')]
                .filter(b=>/more options for|mute\b|выключить звук|unmute/i.test(b.getAttribute('aria-label')||''))
                .map(b=>'al='+(b.getAttribute('aria-label')||'').slice(0,40)+
                     ' cl='+(b.className||'').toString().slice(0,35)).slice(0,12);
              // Сырой HTML первого найденного контейнера-списка:
              let html='нет';
              for(const s of sels){const e=document.querySelector(s);
                if(e){const par=e.closest('ul,[class*="list"],[class*="section"]')||e.parentElement;
                  html=(par.outerHTML||'').replace(/\s+/g,' ').slice(0,1500); break;}}
              return {counts, rowBtns, html};
            }
            """)
            log.info("DUMP ростер-счётчики: %s", panel.get("counts"))
            log.info("DUMP ростер-кнопки: %s", panel.get("rowBtns"))
            log.info("DUMP ростер-HTML: %s", panel.get("html"))
            # Все кнопки мьюта/меню в DOM (часто есть до hover, просто скрыты).
            btns = p.evaluate(r"""
            () => {
              const out = [];
              for (const b of document.querySelectorAll('button,[role="button"]')) {
                const al = b.getAttribute('aria-label')||'';
                const ti = b.getAttribute('title')||'';
                const tx = (b.innerText||'').trim();
                if (/mute|unmute|выключить звук|включить звук|more|ещё|еще/i.test(al+' '+ti+' '+tx)) {
                  out.push('al='+al.slice(0,30)+' ti='+ti.slice(0,20)+
                           ' tx='+tx.slice(0,20)+' cl='+(b.className||'').toString().slice(0,40));
                }
              }
              return out.slice(0, 25);
            }
            """)
            log.info("DUMP mute/more-кнопок: %d", len(btns))
            for line in btns:
                log.info("DUMP btn %s", line)
            # 1) Структурные контейнеры списка (role) — покажет обёртку строк.
            roles = p.evaluate(r"""
            () => {
              const out = [];
              for (const el of document.querySelectorAll(
                  '[role="list"],[role="listbox"],[role="tree"],[role="grid"],'+
                  '[role="row"],[role="listitem"],[role="treeitem"],[role="gridcell"]')) {
                const cls = (el.className||'').toString().slice(0,50);
                const txt = (el.innerText||'').replace(/\n/g,' ').trim().slice(0,30);
                out.push(el.tagName.toLowerCase()+'.'+cls+
                         '[role='+el.getAttribute('role')+'] :: '+txt);
              }
              return out.slice(0, 30);
            }
            """)
            log.info("DUMP roles: %d", len(roles))
            for line in roles:
                log.info("DUMP role %s", line)
            # 2) Цепочка предков от ИМЕНИ бота — прямой путь к классу строки.
            chain = p.evaluate(r"""
            (name) => {
              let target = null;
              for (const el of document.querySelectorAll('span,div,a,p')) {
                const t = (el.innerText||'').trim();
                if (t.includes(name) && t.length < 40 && el.children.length === 0) {
                  target = el; break;
                }
              }
              if (!target) return ['имя '+name+' не найдено как отдельный узел'];
              const chain = [];
              let el = target;
              for (let i = 0; i < 7 && el; i++) {
                const cls = (el.className||'').toString().slice(0,55);
                const role = el.getAttribute('role')||'';
                const nb = el.querySelectorAll('button,[role="button"]').length;
                chain.push('L'+i+' '+el.tagName.toLowerCase()+'.'+cls+
                           (role?'[role='+role+']':'')+' btns='+nb);
                el = el.parentElement;
              }
              return chain;
            }
            """, self._name)
            log.info("DUMP chain от имени %r:", self._name)
            for line in chain:
                log.info("DUMP chain %s", line)
        except Exception as exc:  # noqa: BLE001
            log.warning("debug_dump_participants: %s", exc)

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
              // По КОНТЕЙНЕРАМ строк (одна на сообщение) — иначе вложенные узлы
              // завышают счётчик в несколько раз.
              const items = [...document.querySelectorAll('[class*="new-chat-message__container"]')];
              let n = 0;
              for (const el of items) {
                const b = el.querySelector('[class*="new-chat-message__text-content"], [class*="new-chat-message__body"], [class*="message-text"]');
                const t = (b ? b.innerText : el.innerText) || '';
                if (t.trim() === text) n++;
              }
              return n;
            }
            """, text))
        except Exception:  # noqa: BLE001
            return 0

    def _message_locator(self, text: str):
        """Locator верхнего (старейшего видимого) сообщения с точным текстом."""
        import re as _re
        # КОНТЕЙНЕР строки (в нём кнопка «...»), но совпадение — по ТЕКСТУ
        # СООБЩЕНИЯ внутри (text-content), а не по всему контейнеру: у первого
        # сообщения в группе автора контейнер несёт шапку «Имя Кому Все время»,
        # и `^text$` по контейнеру не срабатывает (напр. «пидарас» не удалялся).
        p = self.page
        return p.locator('[class*="new-chat-message__container"]').filter(
            has=p.locator('[class*="new-chat-message__text-content"]',
                          has_text=_re.compile(rf"^{_re.escape(text)}$")))

    def _open_message_menu(self, el, text: str) -> bool:
        """Навести на строку и открыть меню «...». Ховер под Xvfb иногда не
        поднимает кнопку с первого раза — «будим» (уводим мышь и наводим заново)
        и повторяем до 3 раз. Кнопку ищем и как потомка строки, и по xpath."""
        p = self.page
        for attempt in range(3):
            try:
                el.scroll_into_view_if_needed(timeout=2000)
                if attempt > 0:
                    p.mouse.move(5, 5)             # сброс ховера
                    p.wait_for_timeout(150)
                el.hover(timeout=3000)             # НАСТОЯЩИЙ hover
                p.wait_for_timeout(300)
                dots = el.locator('button.new-chat-message__options-button')
                if dots.count() == 0:
                    dots = el.locator(
                        'xpath=.//button[contains(@class,"options-button")]')
                if dots.count() == 0:
                    continue
                dots.last.hover(timeout=1500)
                dots.last.click(timeout=1800)
                return True
            except PWTimeout:
                continue
            except Exception as exc:  # noqa: BLE001
                log.info("delete: ошибка ховера (%d): %s", attempt, exc)
                continue
        log.info("delete: «...» не поднялась для %r (3 попытки)", text)
        return False

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
        if not self._open_message_menu(el, text):
            return False
        p.wait_for_timeout(700)                # меню-портал успевает отрисоваться
        if not self._click_menu_delete():
            items = p.evaluate(
                "() => [...document.querySelectorAll('[role=\"menuitem\"],li,"
                "[class*=\"dropdown\"] *,[class*=\"menu\"] *')]"
                ".map(e=>e.children.length===0?(e.innerText||'').trim():'')"
                ".filter(t=>t&&t.length<25).slice(0,12)")
            log.info("delete: пункт «Удалить» не найден для %r; в меню: %s", text, items)
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

    def _click_menu_delete(self) -> bool:
        """Клик пункта удаления в контекстном меню сообщения. Текст варьируется
        по роли/языку: «Удалить» / «Удалить для всех» / «Delete». Берём видимый
        листовой узел, чей текст начинается с «удал»/«delete»."""
        return bool(self.page.evaluate(r"""
        () => {
          const re = /^(удалить|delete)/i;
          const w = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
          while (w.nextNode()) {
            const e = w.currentNode;
            if (e.children.length !== 0) continue;
            if (e.offsetParent === null) continue;         // только видимые
            const t = (e.textContent||'').trim();
            if (re.test(t) && t.length < 25) {
              (e.closest('[role="menuitem"],li,button,a') || e).click();
              return true;
            }
          }
          return false;
        }
        """))
