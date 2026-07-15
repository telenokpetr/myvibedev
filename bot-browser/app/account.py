"""Вход бота в Zoom-аккаунт через веб (форма из веб-интерфейса).

Веб-панель «Аккаунт бота» шлёт email+пароль сюда (/account/sign-in), при
необходимости — OTP-код с почты (/account/otp). Реальный вход выполняет
браузер бота (ZoomWeb) на постоянном профиле /data/zoomprofile — после успеха
сессия сохраняется и обычные заходи в конференции идут уже авторизованными
(снимает антибот-блок «Боты не могут присоединяться» и даёт роль/host key).

Playwright thread-affine: браузером владеет ОДИН рабочий поток, HTTP-обработчики
общаются с ним через очередь и ждут результат (single-flight под _lock).
Профиль один → вход и участие в конференции взаимоисключающи (сначала войти,
потом заходить в митинги).
"""

import logging
import os
import queue
import threading

from app.config import config
from app.zoomweb import ZoomWeb

log = logging.getLogger("account")

# Маркер вошедшего аккаунта — чтобы статус пережил рестарт (профиль хранит куки).
MARKER = "/data/zoomprofile/.bot_account"


class AccountManager:
    def __init__(self) -> None:
        self.state = "logged_out"   # logged_out|signing_in|waiting_otp|logged_in|error
        self.email = ""
        self.error = ""
        self._web: ZoomWeb | None = None
        self._thread: threading.Thread | None = None
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._load_marker()

    # ---- маркер сессии ----

    def _load_marker(self) -> None:
        try:
            if os.path.exists(MARKER):
                with open(MARKER, encoding="utf-8") as f:
                    self.email = f.read().strip()
                if self.email:
                    self.state = "logged_in"
        except Exception as exc:  # noqa: BLE001
            log.warning("чтение маркера: %s", exc)

    def _save_marker(self) -> None:
        try:
            os.makedirs(os.path.dirname(MARKER), exist_ok=True)
            with open(MARKER, "w", encoding="utf-8") as f:
                f.write(self.email or "")
        except Exception as exc:  # noqa: BLE001
            log.warning("запись маркера: %s", exc)

    def _clear_marker(self) -> None:
        try:
            os.path.exists(MARKER) and os.remove(MARKER)
        except Exception:  # noqa: BLE001
            pass

    # ---- публичное API (из HTTP-потоков) ----

    def status(self) -> dict:
        return {"state": self.state, "email": self.email, "error": self.error}

    def sign_in(self, email: str, password: str) -> dict:
        from app.moderator import moderator
        if moderator.enabled:
            return {"state": "error", "email": self.email,
                    "error": "бот сейчас в конференции — сначала выйдите из неё"}
        # НЕблокирующе: вход (запуск Chromium + форма) дольше 15с — веб-клиент
        # ждёт ответа мало и покажет «воркер не работает». Отвечаем сразу
        # signing_in, работу ведём в фоне; UI опрашивает /account/status.
        with self._lock:
            self.error = ""
            self.email = email
            self.state = "signing_in"
            self._ensure_thread()
            self._q.put(("sign_in", email, password))
        return self.status()

    def otp(self, code: str) -> dict:
        if self.state != "waiting_otp":
            return {"state": self.state, "email": self.email,
                    "error": "код сейчас не запрашивается — начните вход заново"}
        with self._lock:
            self.error = ""
            self.state = "signing_in"   # проверка кода
            self._ensure_thread()
            self._q.put(("otp", code, None))
        return self.status()

    def import_cookies(self) -> dict:
        """Импорт готовой сессии из /data/zoom-cookies.json (человек вошёл в
        своём браузере, reCAPTCHA пройдена им). Обходит блок автоформы."""
        from app.moderator import moderator
        if moderator.enabled:
            return {"state": "error", "email": self.email,
                    "error": "бот сейчас в конференции — сначала выйдите из неё"}
        with self._lock:
            self.error = ""
            self.state = "signing_in"
            self._ensure_thread()
            self._q.put(("import_cookies", None, None))
        return self.status()

    def sign_out(self) -> dict:
        self.state = "logged_out"
        self.email = ""
        self.error = ""
        self._clear_marker()
        return self.status()

    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # ---- рабочий поток (владелец браузера) ----

    def _run(self) -> None:
        while True:
            try:
                cmd = self._q.get(timeout=300)   # простой 5 мин → закрыть браузер
            except queue.Empty:
                self._close_web()
                return
            name = cmd[0]
            try:
                if name == "sign_in":
                    self._do_sign_in(cmd[1], cmd[2])
                elif name == "otp":
                    self._do_otp(cmd[1])
                elif name == "import_cookies":
                    self._do_import_cookies()
            except Exception as exc:  # noqa: BLE001
                log.exception("account %s: %s", name, exc)
                self.state = "error"
                self.error = f"сбой входа: {exc}"
            # Терминальные состояния — закрыть браузер (флашит профиль на диск).
            # waiting_otp НЕ закрываем: тот же сеанс нужен для ввода кода.
            if self.state in ("logged_in", "logged_out", "error"):
                self._close_web()

    def _ensure_web(self) -> None:
        if self._web is None:
            self._web = ZoomWeb(config.bot_name)
            self._web.start()

    def _close_web(self) -> None:
        if self._web is not None:
            try:
                self._web.stop()
            except Exception:  # noqa: BLE001
                pass
            self._web = None

    def _do_sign_in(self, email: str, password: str) -> None:
        self.state = "signing_in"
        self.error = ""
        self.email = email
        self._ensure_web()
        res = self._web.account_sign_in(email, password)   # logged_in|otp|captcha|error:<msg>
        if res == "logged_in":
            self.state = "logged_in"
            self._save_marker()
        elif res == "otp":
            self.state = "waiting_otp"
        elif res == "captcha":
            self.state = "error"
            self.error = ("Zoom показал капчу — автоматический вход невозможен. "
                          "Войдите этим аккаунтом вручную в обычном браузере, "
                          "либо повторите позже.")
        else:
            self.state = "error"
            self.error = res.split("error:", 1)[-1] if res.startswith("error:") else res

    def _do_import_cookies(self) -> None:
        import json
        path = "/data/zoom-cookies.json"
        if not os.path.exists(path):
            self.state = "error"
            self.error = "файл /data/zoom-cookies.json не найден"
            return
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as exc:  # noqa: BLE001
            self.state = "error"
            self.error = f"не читается JSON cookie: {exc}"
            return
        if isinstance(raw, dict):
            raw = raw.get("cookies", [])
        self.state = "signing_in"
        self._ensure_web()
        ok, msg = self._web.import_cookies_and_verify(raw)
        if ok:
            self.state = "logged_in"
            self.email = msg
            self._save_marker()
        else:
            self.state = "error"
            self.error = msg

    def _do_otp(self, code: str) -> None:
        if self._web is None:
            self.state = "error"
            self.error = "сеанс входа не активен — начните заново"
            return
        res = self._web.account_submit_otp(code)
        if res == "logged_in":
            self.state = "logged_in"
            self._save_marker()
        else:
            self.state = "error"
            self.error = res.split("error:", 1)[-1] if res.startswith("error:") else res


account = AccountManager()
