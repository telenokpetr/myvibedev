"""Движок модерации чата: фильтр нецензурной лексики + детектор спама.

Чистое ядро без зависимостей от способа чтения/действия — переносимо между
desktop-OCR и браузерным (DOM) ботами. Скопировано из bot-worker/app/moderation
(проверенные вживую корни и leet-подстановки), очищено от gui/chatreader.
"""

import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass

# --- Нецензурная лексика: корни (базовый список, расширяемый) ---
PROFANITY_ROOTS = [
    r"ху[йяе]", r"пизд", r"еб[аеёуы]", r"ёб", r"бля", r"блят", r"блад", r"сук[аи]",
    r"муд[ао]", r"залуп", r"гандон", r"гондон", r"пидор", r"пидар", r"хер",
    r"дроч", r"манда", r"уеб", r"долбоёб", r"долбоеб", r"выеб", r"наеб", r"чмо",
    r"ебл",  # еблан/ебло — корень еб[аеёуы] их не ловит
    # Ругательные (анатомические типа «пенис» НЕ включаем — это не мат):
    r"шлюх", r"проститут", r"мраз", r"говн", r"сран", r"обос", r"насрат", r"ссан",
    r"fuck", r"shit", r"bitch", r"asshole", r"cunt", r"dick",
]

# Частые подмены символов, чтобы ловить обфускацию (х у й, п1зд, e6a…).
_LEET = str.maketrans({
    "0": "о", "3": "е", "1": "и", "@": "а", "4": "ч", "6": "б",
    "y": "у", "o": "о", "a": "а", "e": "е", "p": "р", "c": "с", "x": "х",
})


@dataclass
class ChatMessage:
    sender: str
    text: str
    # Уникальный id сообщения в DOM Zoom (data-* атрибут) — для точечного
    # удаления и дедупа. Браузерный ридер знает его сразу, в отличие от OCR.
    mid: str | None = None


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
        # По «сырому» тексту (англ. корни) И по leet-нормализованному (кир.
        # обфускация). _LEET мапит латиницу в кириллицу и портит англ. слова —
        # потому две проверки.
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
        # Короткие сообщения (1-3 символа, «а»/«ок»/«лол») — спам уже при 2
        # повторах: ими флудят чаще, а длинный текст случайно не повторяют.
        limit = 2 if len(norm) <= 3 else self.repeat_limit
        if norm and list(texts).count(norm) >= limit:
            return f"повтор: одно и то же ×{limit}"
        return None


def classify(text: str, msg: ChatMessage,
             profanity: ProfanityFilter,
             spam: SpamDetector) -> tuple[str, str] | None:
    """(category, reason) или None. Мат вперёд спама (см. bot-worker)."""
    if profanity.check(text):
        return "profanity", "нецензурная лексика"
    reason = spam.check(msg)
    if reason:
        return "spam", reason
    return None
