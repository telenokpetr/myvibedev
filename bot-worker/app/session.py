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
                self._state.error = "вход не подтверждён (остались вне конференции)"
                self._log("вход НЕ подтверждён — бот не в конференции")
                return
            self._state.status = "live"
            if gui.meeting_state() == "waiting":
                # Бот подключился, но хост ещё не запустил митинг / зал ожидания.
                # Это не ошибка. Аудио настроим, когда появится тулбар (митинг стартует);
                # Original sound уже включён «для всех будущих митингов» в профиле.
                self._log("бот подключён, ожидание хоста")
            else:
                self._log("бот в конференции")
                gui.maximize_meeting_window()
                # Включаем Original sound, иначе шумодав глушит музыку бота (P1).
                gui.setup_meeting_audio()
                self._log("аудио настроено (original sound)")

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

            # Следим, что бот всё ещё в митинге: если митинг завершат извне (лимит
            # бесплатного тарифа / хост закрыл), бот выпадает на home — фиксируем это,
            # иначе статус зависает в live и слот не освобождается.
            self._start_watch()

        except Exception as exc:  # noqa: BLE001
            self._state.status = "error"
            self._state.error = str(exc)
            self._log(f"ошибка: {exc}")

    def _start_watch(self) -> None:
        self._watch_stop = False
        threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self) -> None:
        """Раз в 15с проверяем, что бот в конференции. Два «none» подряд → митинг
        завершён извне: чистим ресурсы и помечаем finished."""
        misses = 0
        while not getattr(self, "_watch_stop", True):
            time.sleep(15)
            if self._state.status not in ("live", "recording", "claiming"):
                return
            try:
                st = gui.meeting_state()
            except Exception:  # noqa: BLE001
                st = "none"
            misses = misses + 1 if st == "none" else 0
            if misses >= 2:
                self._log("митинг завершён извне — бот вне конференции")
                self._finish_externally()
                return

    def _finish_externally(self) -> None:
        self._watch_stop = True
        try:
            moderator.stop()
            if recording.status()["active"]:
                recording.stop()
        except Exception:  # noqa: BLE001
            pass
        self._state.status = "finished"

    def leave(self) -> None:
        with self._lock:
            self._watch_stop = True
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
