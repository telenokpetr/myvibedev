"""Вход в Zoom-аккаунт из веб-интерфейса.

Состояния входа: logged_out → signing_in → (waiting_otp) → logged_in | error.

Детекция логина — по `~/.config/zoomus.conf`: после успешного входа поле
`userEmailAddress` становится непустым. OTP-детекция эвристическая: если после
sign-in вход не подтвердился за таймаут, считаем, что Zoom ждёт OTP с почты.

Профиль Zoom вынесен в volume (docker-compose), поэтому логин («Stay signed in»)
переживает пересоздание контейнера и OTP не нужен каждый раз.
"""

import logging
import os
import subprocess
import threading
import time

from app import gui
from app.config import config

log = logging.getLogger("account")

ZOOMUS_CONF = os.path.expanduser("~/.config/zoomus.conf")
ENV = {**os.environ, "DISPLAY": config.display}


class AccountManager:
    def __init__(self) -> None:
        self.state = "logged_out"   # logged_out|signing_in|waiting_otp|logged_in|error
        self.email: str | None = None
        self.error: str | None = None
        self._lock = threading.Lock()

    # ---- детекция логина по конфигу Zoom ----
    def _logged_in_email(self) -> str:
        try:
            with open(ZOOMUS_CONF, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.startswith("userEmailAddress="):
                        return line.split("=", 1)[1].strip()
        except FileNotFoundError:
            return ""
        return ""

    def status(self) -> dict:
        em = self._logged_in_email()
        # если Zoom уже показывает залогиненный аккаунт — отражаем это
        if em and self.state not in ("signing_in", "waiting_otp"):
            self.state = "logged_in"
            self.email = em
        return {"state": self.state, "email": self.email or em or None, "error": self.error}

    # ---- запуск клиента на экран логина ----
    def _ensure_zoom_running(self) -> None:
        if gui.find_zoom_windows():
            return
        subprocess.Popen(["zoom"], env=ENV,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(30):
            if gui.find_zoom_windows():
                break
            time.sleep(1)
        time.sleep(3)

    # ---- вход ----
    def sign_in(self, email: str, password: str) -> dict:
        with self._lock:
            if self.state in ("signing_in", "waiting_otp"):
                return self.status()
            self.state = "signing_in"
            self.email = email
            self.error = None
        threading.Thread(target=self._do_sign_in, args=(email, password),
                         daemon=True).start()
        return self.status()

    def _do_sign_in(self, email: str, password: str) -> None:
        try:
            if self._logged_in_email():          # уже залогинен (профиль из volume)
                self.state, self.email = "logged_in", self._logged_in_email()
                return
            self._ensure_zoom_running()
            gui.sign_in(email, password)
            for _ in range(15):                  # ждём подтверждения по zoomus.conf
                time.sleep(1)
                if self._logged_in_email():
                    self.state, self.email = "logged_in", self._logged_in_email()
                    log.info("вход подтверждён: %s", self.email)
                    return
            self.state = "waiting_otp"           # не вошли -> вероятно нужен OTP
            log.info("вход не подтверждён за таймаут — жду OTP")
        except Exception as exc:  # noqa: BLE001
            self.state, self.error = "error", str(exc)
            log.warning("ошибка входа: %s", exc)

    # ---- OTP ----
    def submit_otp(self, code: str) -> dict:
        with self._lock:
            if self.state != "waiting_otp":
                return self.status()
        threading.Thread(target=self._do_otp, args=(code,), daemon=True).start()
        return self.status()

    def _do_otp(self, code: str) -> None:
        try:
            gui.enter_otp(code)
            for _ in range(15):
                time.sleep(1)
                if self._logged_in_email():
                    self.state, self.email = "logged_in", self._logged_in_email()
                    log.info("OTP принят, вход подтверждён: %s", self.email)
                    return
            self.state, self.error = "error", "OTP не подтвердил вход"
        except Exception as exc:  # noqa: BLE001
            self.state, self.error = "error", str(exc)


account = AccountManager()
