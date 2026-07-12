"""Движок модерации чата: фильтр нецензурной лексики + детектор спама.

Правила работают полностью и тестируются офлайн (через /moderation/test).
Действия — best-effort через GUI: удаление сообщения реализовано (правый клик
по координатам строки из OCR + поиск пункта «Delete» на экране, см.
gui.delete_chat_message), мьют автора недоступен, пока OCR не привязывает
автора к сообщению. Если действие не удалось — событие фиксируется как
«flagged» (нарушение обнаружено и залогировано).
"""

import logging
import os
import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import httpx

from app import gui
from app.chatreader import ChatMessage, get_reader
from app.config import config

log = logging.getLogger("moderation")

# --- Нецензурная лексика: корни (базовый список, расширяемый) ---
PROFANITY_ROOTS = [
    r"ху[йяе]", r"пизд", r"еб[аеёуы]", r"ёб", r"бля", r"блят", r"блад", r"сук[аи]",
    r"муд[ао]", r"залуп", r"гандон", r"гондон", r"пидор", r"пидар", r"хер",
    r"дроч", r"манда", r"уеб", r"долбоёб", r"долбоеб", r"выеб", r"наеб", r"чмо",
    r"fuck", r"shit", r"bitch", r"asshole", r"cunt", r"dick",
]

# Частые подмены символов, чтобы ловить обфускацию (х у й, п1зд, e6a…).
_LEET = str.maketrans({
    "0": "о", "3": "е", "1": "и", "@": "а", "4": "ч", "6": "б",
    "y": "у", "o": "о", "a": "а", "e": "е", "p": "р", "c": "с", "x": "х",
})


def strip_delims(text: str) -> str:
    """lower + убрать разделители между буквами: "х-у-й", "п и з д а", "f u c k"."""
    return re.sub(r"[\s\.\-_*]+", "", text.lower())


def normalize(text: str) -> str:
    """Полная нормализация: разделители + leet-подстановки (кир. обфускация)."""
    return strip_delims(text).translate(_LEET)


class ProfanityFilter:
    def __init__(self, roots: list[str] | None = None) -> None:
        roots = roots or PROFANITY_ROOTS
        self._re = re.compile("|".join(roots))

    def check(self, text: str) -> bool:
        # По «сырому» тексту (ловит англ. корни: fuck/bitch/dick) И по
        # leet-нормализованному (ловит кир. обфускацию: п1зд, e6a). Одна таблица
        # _LEET мапит латиницу в кириллицу и портит англ. слова — потому две проверки.
        return bool(self._re.search(strip_delims(text))
                    or self._re.search(normalize(text)))


class SpamDetector:
    """Флуд (частота) + повторы одинаковых сообщений."""

    def __init__(self, max_msgs: int = 5, window_sec: int = 10,
                 repeat_limit: int = 3) -> None:
        self.max_msgs = max_msgs
        self.window = window_sec
        self.repeat_limit = repeat_limit
        self._times: dict[str, deque] = defaultdict(deque)
        self._texts: dict[str, deque] = defaultdict(lambda: deque(maxlen=10))

    def check(self, msg: ChatMessage) -> str | None:
        now = time.time()
        times = self._times[msg.sender]
        times.append(now)
        while times and now - times[0] > self.window:
            times.popleft()
        if len(times) > self.max_msgs:
            return f"флуд: {len(times)} сообщений за {self.window}с"

        texts = self._texts[msg.sender]
        norm = msg.text.strip().lower()
        texts.append(norm)
        if norm and list(texts).count(norm) >= self.repeat_limit:
            return f"повтор: одно и то же ×{self.repeat_limit}"
        return None


@dataclass
class ModEvent:
    sender: str
    text: str
    category: str   # "profanity" | "spam"
    reason: str
    action: str     # "muted" | "deleted" | "flagged"
    ts: str


class Moderator:
    def __init__(self) -> None:
        self.profanity = ProfanityFilter()
        self.spam = SpamDetector()
        self.events: deque = deque(maxlen=100)
        self.enabled = False
        self._reader = None
        self._thread: threading.Thread | None = None

    # ---- жизненный цикл ----

    def start(self) -> None:
        if self.enabled:
            return
        self.spam = SpamDetector()  # сброс истории на новый митинг
        self._reader = get_reader()
        self.enabled = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info("модерация включена (reader=%s)", type(self._reader).__name__)

    def stop(self) -> None:
        self.enabled = False

    def _loop(self) -> None:
        while self.enabled:
            try:
                for msg in self._reader.poll():
                    self.process(msg)
            except Exception as exc:  # noqa: BLE001
                log.warning("ошибка чтения чата: %s", exc)
            time.sleep(2)

    # ---- обработка одного сообщения ----

    def process(self, msg: ChatMessage) -> ModEvent | None:
        category = None
        reason = ""
        if self.profanity.check(msg.text):
            category, reason = "profanity", "нецензурная лексика"
        else:
            spam_reason = self.spam.check(msg)
            if spam_reason:
                category, reason = "spam", spam_reason

        if not category:
            return None

        action = self._act(msg, category)
        ev = ModEvent(
            sender=msg.sender, text=msg.text, category=category,
            reason=reason, action=action,
            ts=datetime.now(timezone.utc).isoformat(),
        )
        self.events.append(ev)
        self._push(ev)
        log.info("модерация: %s [%s] %s: %s", action, category, msg.sender, reason)
        return ev

    def _act(self, msg: ChatMessage, category: str) -> str:
        # best-effort: сперва удалить сообщение — его позицию на экране знает
        # OCR-ридер (msg.pos). Мьют автора пока недостижим: OCR не привязывает
        # автора к сообщению (sender="чат"), поэтому mute_participant — no-op.
        try:
            if gui.delete_chat_message(msg.pos):
                return "deleted"
            if gui.mute_participant(msg.sender):
                return "muted"
        except Exception as exc:  # noqa: BLE001
            log.warning("действие модерации не удалось: %s", exc)
        return "flagged"

    def _push(self, ev: ModEvent) -> None:
        """Отправить событие в web для сохранения/показа (best-effort)."""
        url = config.callback_url
        if not url:
            return
        try:
            payload = asdict(ev)
            payload["token"] = os.environ.get("INTERNAL_API_TOKEN", "")
            httpx.post(f"{url.rstrip('/')}/api/internal/moderation",
                       json=payload, timeout=5)
        except Exception as exc:  # noqa: BLE001
            log.debug("push moderation failed: %s", exc)

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "events": [asdict(e) for e in list(self.events)[-30:]],
        }


moderator = Moderator()
