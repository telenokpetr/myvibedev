"""Чтение сообщений чата Zoom.

Live-чтение делаем через OCR отдельного окна «Meeting chat» (tesseract, rus+eng):
активируем окно чата, снимаем его скриншот, распознаём текст, отфильтровываем
элементы интерфейса и служебные строки, дедупим уже виденные строки. Каждая новая
строка отдаётся как ChatMessage движку модерации.

⚠️ v1: распознаётся ТЕКСТ сообщений (для детекции мата/спама этого достаточно),
но надёжно разбить на «автор/сообщение/время» OCR не может — поэтому sender="чат".
Точная привязка автора и калибровка региона — на живом тесте (см. docs/RELIABILITY-PLAN.md P2).
"""

import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass

log = logging.getLogger("chatreader")

DISPLAY = os.environ.get("DISPLAY", ":99")
ENV = {**os.environ, "DISPLAY": DISPLAY}

# Строки интерфейса чата, которые НЕ являются сообщениями — отбрасываем.
_UI_NOISE = {
    "everyone", "new chat", "who can see your messages", "message everyone",
    "meeting chat", "gif", "конференция", "reactions", "to everyone",
    "type message here", "direct message", "in meeting", "host", "you",
}
# Строки-время вида 12:34 / 12:34:56 и одиночные разделители.
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?(\s?(am|pm))?$", re.I)


@dataclass
class ChatMessage:
    sender: str
    text: str


class ChatReader:
    def available(self) -> bool:
        return False

    def poll(self) -> list[ChatMessage]:
        """Вернуть новые сообщения с прошлого вызова."""
        return []


def _run(args, timeout=10):
    return subprocess.run(args, env=ENV, capture_output=True, text=True,
                          timeout=timeout, check=False)


class AtspiChatReader(ChatReader):
    """Через AT-SPI (дерево доступности). Зум под Xvfb дерево обычно не отдаёт,
    поэтому основной путь — OCR. Оставлен как опция, если pyatspi доступен."""

    def available(self) -> bool:
        try:
            import pyatspi  # noqa: F401
            return True
        except Exception:  # noqa: BLE001
            return False

    def poll(self) -> list[ChatMessage]:
        return []


class OcrChatReader(ChatReader):
    """OCR окна «Meeting chat»: активируем окно, снимаем, распознаём, дедупим."""

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._img = "/tmp/_chat_ocr.png"

    def available(self) -> bool:
        return True

    def _chat_window(self) -> str | None:
        out = _run(["xdotool", "search", "--name", "Meeting chat"]).stdout.split()
        return out[0] if out else None

    def _ensure_open(self) -> None:
        """Если окна чата нет — открыть панель чата (гор. клавиша Zoom Alt+H)."""
        if self._chat_window():
            return
        try:
            from app import gui
            gui.maximize_meeting_window()
            gui.move_mouse_center()
            _run(["xdotool", "key", "--clearmodifiers", "alt+h"])
            time.sleep(1.5)
        except Exception:  # noqa: BLE001
            pass

    def _is_noise(self, line: str) -> bool:
        low = line.lower()
        if len(line) < 2:
            return True
        if _TIME_RE.match(low):
            return True
        return any(n in low for n in _UI_NOISE)

    def poll(self) -> list[ChatMessage]:
        self._ensure_open()
        wid = self._chat_window()
        if not wid:
            return []
        # Активируем окно чата и снимаем ЕГО (scrot -u = текущее окно в фокусе).
        _run(["xdotool", "windowactivate", "--sync", wid])
        time.sleep(0.3)
        r = _run(["scrot", "-u", "-o", self._img], timeout=8)
        if r.returncode != 0 or not os.path.exists(self._img):
            return []
        ocr = _run(["tesseract", self._img, "stdout", "-l", "rus+eng"], timeout=20).stdout
        msgs: list[ChatMessage] = []
        for raw in ocr.splitlines():
            line = raw.strip()
            if not line or self._is_noise(line):
                continue
            if line in self._seen:
                continue
            self._seen.add(line)
            msgs.append(ChatMessage(sender="чат", text=line))
        if msgs:
            log.info("OCR чата: %d новых строк", len(msgs))
        return msgs


def get_reader() -> ChatReader:
    atspi = AtspiChatReader()
    if atspi.available():
        return atspi
    return OcrChatReader()
