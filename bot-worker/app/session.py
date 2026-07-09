"""Менеджер одной активной сессии: вход в конференцию, захват хоста, запись."""

import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

from app import gui, zoom
from app.moderation import moderator
from app.recorder import recorder
from app.recording import recording


@dataclass
class SessionState:
    status: str = "idle"          # idle | joining | claiming | live | recording | error
    join_url: str | None = None
    title: str | None = None
    host_claimed: bool = False
    recording_path: str | None = None
    error: str | None = None
    started_at: str | None = None
    log: list[str] = field(default_factory=list)


class SessionManager:
    def __init__(self) -> None:
        self._state = SessionState()
        self._lock = threading.Lock()
        self._proc = None

    def _log(self, msg: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self._state.log.append(f"{stamp} {msg}")
        self._state.log = self._state.log[-50:]

    @property
    def busy(self) -> bool:
        return self._state.status not in ("idle", "error", "finished")

    def status(self) -> dict:
        d = asdict(self._state)
        d["recording"] = recording.status()
        d["recording_active"] = recording.status()["active"]
        return d

    def join(self, join_url: str, passcode: str | None, host_key: str | None,
             record: bool, title: str | None, moderate: bool = False) -> None:
        with self._lock:
            if self.busy:
                raise RuntimeError("Уже идёт активная сессия")
            self._state = SessionState(
                status="joining", join_url=join_url, title=title,
                started_at=datetime.now(timezone.utc).isoformat(),
            )
        self._log(f"вход в конференцию: {join_url}")
        # Всю долгую работу — в фоне, чтобы API отвечал сразу.
        threading.Thread(
            target=self._run_session,
            args=(join_url, passcode, host_key, record, title, moderate),
            daemon=True,
        ).start()

    def _run_session(self, join_url, passcode, host_key, record, title, moderate) -> None:
        try:
            self._proc = zoom.launch(join_url, passcode)
            self._log("Zoom-клиент запущен, жду загрузки")
            time.sleep(8)

            gui.dismiss_startup_dialogs()
            gui.activate_meeting_window()

            # Верифицируем вход по окну, а не ставим live вслепую (см. баг §7):
            # при «Invalid meeting ID» окно митинга не появится → это ошибка.
            if not gui.wait_in_meeting():
                self._state.status = "error"
                self._state.error = "вход не подтверждён (окно митинга не появилось)"
                self._log("вход НЕ подтверждён — окна конференции нет")
                return
            self._state.status = "live"
            self._log("бот в конференции")
            # Разворачиваем реальное окно конференции на весь экран.
            gui.maximize_meeting_window()

            if host_key:
                self._state.status = "claiming"
                self._log(f"пробую стать организатором (host key)")
                ok = gui.claim_host(host_key)
                self._state.host_claimed = ok
                self._log("организатор получен" if ok else
                          "не удалось забрать хост (нужна калибровка UI)")
                self._state.status = "live"

            if moderate:
                moderator.start()
                self._log("модерация чата включена")

            if record:
                res = recording.start("local", title or "event")
                self._state.recording_path = res.get("path")
                self._state.status = "recording"
                self._log(f"запись (локально) начата: {res.get('path')}")

        except Exception as exc:  # noqa: BLE001
            self._state.status = "error"
            self._state.error = str(exc)
            self._log(f"ошибка: {exc}")

    def leave(self) -> None:
        with self._lock:
            self._log("выхожу из конференции")
            moderator.stop()
            if recording.status()["active"]:
                res = recording.stop()
                self._state.recording_path = res.get("path")
                self._log(f"запись остановлена: {res.get('path')}")
            zoom.kill(self._proc)
            self._proc = None
            self._state.status = "finished"


session = SessionManager()
