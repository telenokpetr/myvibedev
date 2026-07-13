"""Браузерный модератор: владеет ZoomWeb в одном потоке (Playwright thread-affine),
крутит цикл чтения чата → классификация → удаление через DOM.

Отличия от desktop-OCR-бота, которые упрощают логику:
- у каждого сообщения есть стабильный DOM-id → дедуп по id (не покадровый счётчик);
- удаление через DOM возвращает достоверный успех (проверяем исчезновение из DOM)
  → кулдаун нужен только на реально неудачные попытки, дребезга ховера нет.
"""

import logging
import os
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import httpx

from app.config import config
from app.moderation import ChatMessage, ProfanityFilter, SpamDetector, classify
from app.zoomweb import ZoomWeb

log = logging.getLogger("moderator")


@dataclass
class ModEvent:
    sender: str
    text: str
    category: str
    reason: str
    action: str      # "deleted" | "flagged"
    ts: str


class BrowserModerator:
    RETRY_COOLDOWN = 90.0

    def __init__(self) -> None:
        self.profanity = ProfanityFilter()
        self.spam = SpamDetector()
        self.events: deque = deque(maxlen=100)
        self.enabled = False
        self.status = "idle"        # idle|joining|live|finished|error
        self._web: ZoomWeb | None = None
        self._thread: threading.Thread | None = None
        self._seen: set[str] = set()
        self._backlog_done = False
        self._failed_at: dict[str, float] = {}
        self._join_url = ""

    # ---- управление ----

    def join(self, url: str) -> None:
        if self.enabled:
            raise RuntimeError("уже в конференции")
        self.spam = SpamDetector()
        self._seen = set()
        self._backlog_done = False
        self._failed_at = {}
        self._join_url = url
        self.enabled = True
        self.status = "joining"
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def leave(self) -> None:
        self.enabled = False

    def state(self) -> dict:
        return {"status": self.status, "join_url": self._join_url,
                "enabled": self.enabled}

    def mod_status(self) -> dict:
        return {"enabled": self.enabled,
                "events": [asdict(e) for e in list(self.events)[-30:]]}

    # ---- рабочий поток (владелец браузера) ----

    def _run(self) -> None:
        web = ZoomWeb(config.bot_name)
        self._web = web
        try:
            web.start()
            if not web.join(self._join_url):
                self.status = "error"
                log.warning("вход не подтверждён")
                return
            self.status = "live"
            log.info("браузерная модерация включена")
            while self.enabled:
                web.screenshot(os.path.join(config.shots_dir, "last.png"))
                if not web.in_meeting():
                    log.info("похоже, вышли из конференции")
                    break
                try:
                    self._poll(web)
                except Exception as exc:  # noqa: BLE001
                    log.warning("ошибка цикла: %s", exc)
                time.sleep(2)
        except Exception as exc:  # noqa: BLE001
            self.status = "error"
            log.exception("сбой браузерного модератора: %s", exc)
        finally:
            web.stop()
            self.enabled = False
            if self.status not in ("error",):
                self.status = "finished"

    def _poll(self, web: ZoomWeb) -> None:
        msgs = web.read_chat()
        new = [m for m in msgs if m["id"] not in self._seen]
        for m in new:
            self._seen.add(m["id"])
        if not self._backlog_done:
            # первое чтение — видимая история: мат удаляем, спам не считаем
            self._backlog_done = True
            for m in new:
                self._process(web, m, backlog=True)
            if new:
                log.info("бэклог %d сообщений (только мат)", len(new))
            return
        for m in new:
            self._process(web, m, backlog=False)

    def _process(self, web: ZoomWeb, m: dict, backlog: bool) -> None:
        msg = ChatMessage(sender=m["sender"], text=m["text"], mid=m["id"])
        res = classify(msg.text, msg, self.profanity, self.spam)
        if res is None:
            return
        category, reason = res
        if backlog and category == "spam":
            return  # история приходит пачкой — не считаем флудом
        ckey = self._cooldown_key(msg.text)
        if ckey and self._failed_recently(ckey):
            return
        deletable = category == "profanity" or reason.startswith("повтор")
        action = "flagged"
        if deletable:
            try:
                if web.delete_message(msg.mid, msg.text):
                    action = "deleted"
            except Exception as exc:  # noqa: BLE001
                log.warning("удаление не удалось: %s", exc)
        if ckey:
            if action == "flagged":
                self._failed_at[ckey] = time.time()
            else:
                self._failed_at.pop(ckey, None)
        ev = ModEvent(sender=msg.sender, text=msg.text, category=category,
                      reason=reason, action=action,
                      ts=datetime.now(timezone.utc).isoformat())
        self.events.append(ev)
        self._push(ev)
        log.info("модерация: %s [%s] %s: %s", action, category, msg.sender, reason)

    # ---- кулдаун повторных попыток ----

    @staticmethod
    def _cooldown_key(text: str) -> str:
        import re
        return re.sub(r"[\W_\d]+", "", text.lower())

    def _failed_recently(self, key: str) -> bool:
        now = time.time()
        self._failed_at = {k: t for k, t in self._failed_at.items()
                           if now - t < self.RETRY_COOLDOWN}
        return any(key.startswith(k) or k.startswith(key)
                   for k in self._failed_at)

    def _push(self, ev: ModEvent) -> None:
        if not config.callback_url:
            return
        try:
            payload = asdict(ev)
            payload["token"] = config.internal_token
            httpx.post(f"{config.callback_url.rstrip('/')}/api/internal/moderation",
                       json=payload, timeout=5)
        except Exception as exc:  # noqa: BLE001
            log.debug("push moderation failed: %s", exc)


moderator = BrowserModerator()
