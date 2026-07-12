"""Чтение сообщений чата Zoom.

Live-чтение делаем через OCR отдельного окна «Meeting chat»: активируем окно
чата, снимаем его скриншот, распознаём текст, отфильтровываем элементы
интерфейса и служебные строки, дедупим уже виденные строки. Каждая новая
строка отдаётся как ChatMessage движку модерации.

Движок OCR — PaddleOCR (читает мелкую кириллицу чата, на которой tesseract
выдаёт мусор — см. docs/RELIABILITY-PLAN.md P2); tesseract остаётся fallback'ом,
выбор можно форсировать через CHAT_READER=paddle|tesseract|atspi.

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
    "today", "yesterday",
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
        self._opened = False
        self._empty_polls = 0

    def available(self) -> bool:
        return True

    def _chat_window(self) -> str | None:
        out = _run(["xdotool", "search", "--name", "Meeting chat"]).stdout.split()
        return out[0] if out else None

    def _open_panel(self) -> None:
        """Открыть чат (Alt+H). ВАЖНО: alt+h — тоггл, слепо жать нельзя,
        вызывается один раз на сессию + при подозрении, что панель закрыта."""
        try:
            from app import gui
            gui.activate_meeting_window()
            gui.move_mouse_center()
            _run(["xdotool", "key", "--clearmodifiers", "alt+h"])
            time.sleep(1.5)
        except Exception:  # noqa: BLE001
            pass

    def _capture(self) -> bool:
        """Снять область чата. Современный Zoom открывает чат встроенной панелью
        справа (отдельного окна «Meeting chat» нет, заголовок панели — имя
        митинга), поэтому: если отдельное окно есть — снимаем его, иначе снимаем
        весь экран и кропаем правую панель."""
        wid = self._chat_window()
        if wid:
            _run(["xdotool", "windowactivate", "--sync", wid])
            time.sleep(0.3)
            r = _run(["scrot", "-u", "-o", self._img], timeout=8)
            return r.returncode == 0 and os.path.exists(self._img)
        r = _run(["scrot", "-o", self._img], timeout=8)
        if r.returncode != 0 or not os.path.exists(self._img):
            return False
        try:
            from PIL import Image
            img = Image.open(self._img)
            w, h = img.size
            # панель ~ правые 23% ширины; сверху срезаем тулбар Zoom, снизу поле ввода
            img.crop((int(w * 0.77), int(h * 0.055), w, int(h * 0.86))).save(self._img)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _is_noise(self, line: str) -> bool:
        low = line.lower()
        if len(line) < 2:
            return True
        if _TIME_RE.match(low):
            return True
        return any(n in low for n in _UI_NOISE)

    _MAX_SIDE = 4000  # предел большей стороны после апскейла

    def _prepare(self) -> None:
        """Апскейл скриншота ×3 (LANCZOS): шрифт чата ~11-12px «как есть» оба
        движка читают мусором, после апскейла — уверенно (проверено на синтетике,
        см. коммит). Большие окна апскейлим меньше, чтобы не раздувать кадр."""
        try:
            from PIL import Image
            img = Image.open(self._img)
            factor = max(1, min(3, self._MAX_SIDE // max(img.size)))
            if factor > 1:
                img.resize((img.width * factor, img.height * factor),
                           Image.LANCZOS).save(self._img)
        except Exception as exc:  # noqa: BLE001
            log.debug("апскейл скриншота не удался: %s", exc)

    def _recognize(self) -> list[str]:
        """Распознать строки текста на self._img (tesseract, rus+eng).

        --psm 11 (sparse text): дефолтная сегментация (psm 3) выбрасывает
        одинокий «пузырь» сообщения рядом с аватаркой как не-текст — первое
        сообщение митинга не читалось вовсе (живой тест 12.07). В sparse-режиме
        и одиночные пузыри, и плотный чат читаются без потерь."""
        out = _run(["tesseract", self._img, "stdout", "-l", "rus+eng",
                    "--psm", "11"], timeout=20).stdout
        return out.splitlines()

    def poll(self) -> list[ChatMessage]:
        if not self._opened:
            if not self._chat_window():
                self._open_panel()
            self._opened = True
        if not self._capture():
            return []
        self._prepare()
        recognized = [raw.strip() for raw in self._recognize() if raw.strip()]
        if not recognized:
            # Открытая панель всегда даёт хоть какие-то строки (Everyone/время),
            # полная пустота = панель, видимо, закрыли — переоткрыть, но не чаще
            # чем раз в 10 пустых циклов (alt+h — тоггл, дребезжать нельзя).
            self._empty_polls += 1
            if self._empty_polls >= 10 and not self._chat_window():
                self._open_panel()
                self._empty_polls = 0
            return []
        self._empty_polls = 0
        msgs: list[ChatMessage] = []
        for line in recognized:
            if self._is_noise(line):
                continue
            if line in self._seen:
                continue
            self._seen.add(line)
            msgs.append(ChatMessage(sender="чат", text=line))
        if msgs:
            log.info("OCR чата: %d новых строк", len(msgs))
        return msgs


class PaddleOcrChatReader(OcrChatReader):
    """OCR через PaddleOCR — детекция текстовых строк + распознавание (lang=ru,
    кириллица+латиница). Модель грузится лениво при первом poll() — это поток
    модерации, API не блокируется; ~0.4-0.5 ГБ RAM на процесс, кадр на CPU ~1-2 с.
    """

    def __init__(self) -> None:
        super().__init__()
        self._engine = None
        self._min_conf = float(os.environ.get("CHAT_OCR_MIN_CONF", "0.5"))

    def available(self) -> bool:
        try:
            import paddleocr  # noqa: F401
            return True
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _seed_models() -> None:
        """Скопировать вшитые в образ модели в ~/.paddleocr, если их там нет:
        /home/zoom заслоняется volume'ом zoomprofile, поэтому seed лежит в /opt."""
        dst = os.path.expanduser("~/.paddleocr")
        seed = "/opt/paddleseed/.paddleocr"
        if not os.path.isdir(dst) and os.path.isdir(seed):
            import shutil
            shutil.copytree(seed, dst)
            log.info("модели PaddleOCR скопированы из seed в %s", dst)

    def _recognize(self) -> list[str]:
        if self._engine is None:
            self._seed_models()
            from paddleocr import PaddleOCR
            # det_limit поднят: после апскейла x3 кадр крупный, дефолт (960)
            # ужал бы его обратно и мелкий текст потерялся бы.
            # cpu_threads=2: дефолтные 10 съедают все ядра, Zoom-клиент теряет
            # соединение и вылетает из митинга (наблюдалось вживую 11.07).
            self._engine = PaddleOCR(use_angle_cls=False, lang="ru", show_log=False,
                                     det_limit_side_len=self._MAX_SIDE,
                                     det_limit_type="max", cpu_threads=2)
            log.info("PaddleOCR инициализирован (lang=ru)")
        result = self._engine.ocr(self._img, cls=False)
        # Paddle отдаёт отдельные слова/фрагменты с боксами — склеиваем их в
        # строки по вертикали, иначе мат, разбитый на «пиз»+«д»+«е», не поймается.
        words: list[tuple[float, float, float, str]] = []  # (y_центр, x, высота, текст)
        for page in result or []:       # ocr() возвращает список страниц,
            for box, (text, conf) in page or []:  # пустая страница — None
                if conf < self._min_conf:
                    continue
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                words.append(((min(ys) + max(ys)) / 2, min(xs),
                              max(ys) - min(ys), text))
        if not words:
            return []
        words.sort(key=lambda w: w[0])
        med_h = sorted(w[2] for w in words)[len(words) // 2]
        rows: list[list] = [[words[0]]]
        for w in words[1:]:
            if w[0] - rows[-1][-1][0] <= med_h * 0.6:
                rows[-1].append(w)
            else:
                rows.append([w])
        return [" ".join(w[3] for w in sorted(row, key=lambda w: w[1]))
                for row in rows]


def get_reader() -> ChatReader:
    """CHAT_READER=paddle|tesseract|atspi форсирует движок; по умолчанию —
    PaddleOCR, если установлен, иначе tesseract."""
    forced = os.environ.get("CHAT_READER", "").strip().lower()
    if forced == "atspi":
        return AtspiChatReader()
    if forced == "tesseract":
        return OcrChatReader()
    paddle = PaddleOcrChatReader()
    if forced == "paddle" or paddle.available():
        return paddle
    return OcrChatReader()
