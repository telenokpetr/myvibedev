"""Чтение сообщений чата Zoom.

Live-чтение делаем через OCR отдельного окна «Meeting chat»: активируем окно
чата, снимаем его скриншот, распознаём текст, отфильтровываем элементы
интерфейса и служебные строки, дедупим уже виденные строки. Каждая новая
строка отдаётся как ChatMessage движку модерации.

Движок OCR — PaddleOCR (читает мелкую кириллицу чата, на которой tesseract
выдаёт мусор — см. docs/RELIABILITY-PLAN.md P2); tesseract остаётся fallback'ом,
выбор можно форсировать через CHAT_READER=paddle|tesseract|atspi.

⚠️ распознаётся ТЕКСТ сообщений (для детекции мата/спама этого достаточно),
но надёжно разбить на «автор/сообщение/время» OCR не может — поэтому sender="чат".
Зато каждая строка знает свои ЭКРАННЫЕ координаты (ChatMessage.pos) — на них
опирается действие «удалить сообщение» (gui.delete_chat_message).
"""

import logging
import os
import re
import subprocess
import time
from collections import Counter
from dataclasses import dataclass

from app import ocrutil

log = logging.getLogger("chatreader")

DISPLAY = os.environ.get("DISPLAY", ":99")
ENV = {**os.environ, "DISPLAY": DISPLAY}

# Строки интерфейса чата, которые НЕ являются сообщениями — отбрасываем.
_UI_NOISE = {
    "everyone", "new chat", "who can see your messages", "message everyone",
    "meeting chat", "gif", "конференция", "reactions", "to everyone",
    "type message here", "direct message", "in meeting", "host", "you",
    "today", "yesterday",
    "еуегуопе",  # «Everyone» кириллическими двойниками (так его читает rus+eng OCR)
    # пункты меню «…» сообщения — попадают в кадр, пока меню открыто удалением
    "copy", "quote", "delete",
}
# Строки-время вида 12:34 / 12:34:56 и одиночные разделители.
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?(\s?(am|pm))?$", re.I)
# Шапка группы сообщений «Имя to Everyone 05:48 PM» — оканчивается временем
# (OCR читает PM и как «РМ», цифры путает с «о»), сообщением не является.
_HDR_TIME_RE = re.compile(r"[\dо]{1,2}[:.][\dо]{2}\s*([ap]m|[ра]м)?\s*$", re.I)


@dataclass
class ChatMessage:
    sender: str
    text: str
    # Центр строки сообщения в ЭКРАННЫХ координатах (для GUI-действий:
    # ховер по сообщению → «…» → Delete). None — позиция неизвестна
    # (например, сообщение пришло через /moderation/test).
    pos: tuple[int, int] | None = None
    # Правый край строки на экране — оттуда начинается ховер-тулбар пузыря.
    pos_right: tuple[int, int] | None = None
    # Сообщение из первого чтения панели (история до старта модерации):
    # мат-фильтр применяется, спам-детектор — нет (бэклог приходит разом и
    # выглядит как флуд).
    backlog: bool = False


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
        # Дедуп — СЧЁТЧИКИ ключей ПРЕДЫДУЩЕГО кадра, не накопленное множество:
        # чат append-only, повторное одинаковое сообщение («хуй» второй раз,
        # «ав» три раза подряд) при накопительном дедупе становилось невидимым
        # (живой тест 12.07: мат после удаления такого же не ловился). Счётчик
        # различает два одинаковых пузыря в одном кадре.
        self._prev_counts: Counter = Counter()
        self._img = "/tmp/_chat_ocr.png"
        self._opened = False
        self._empty_polls = 0
        self._backlog_done = False
        # Пересчёт координат изображения в экранные: screen = origin + px/scale.
        self._origin = (0, 0)   # левый-верхний угол снятой области на экране
        self._scale = 1.0       # фактор апскейла (_prepare)

    def available(self) -> bool:
        return True

    def _chat_window(self) -> str | None:
        out = _run(["xdotool", "search", "--name", "Meeting chat"]).stdout.split()
        return out[0] if out else None

    def _open_panel(self) -> None:
        """Открыть чат (Alt+H). ВАЖНО: alt+h — тоггл, слепо жать нельзя,
        вызывается один раз на сессию + при подозрении, что панель закрыта.
        Фокус — строго на окно митинга: в фокусе плавающего окна чата alt+h
        печатает «h» в поле ввода (живой тест 12.07)."""
        try:
            from app import gui
            if gui.focus_meeting_window() is None:
                return
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
            m = re.search(r"Position:\s*(-?\d+),(-?\d+)",
                          _run(["xdotool", "getwindowgeometry", wid]).stdout)
            self._origin = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
            return r.returncode == 0 and os.path.exists(self._img)
        r = _run(["scrot", "-o", self._img], timeout=8)
        if r.returncode != 0 or not os.path.exists(self._img):
            return False
        try:
            from PIL import Image
            img = Image.open(self._img)
            w, h = img.size
            # панель ~ правые 23% ширины; сверху срезаем тулбар Zoom, снизу поле ввода
            box = (int(w * 0.77), int(h * 0.055), w, int(h * 0.86))
            img.crop(box).save(self._img)
            self._origin = (box[0], box[1])
            return True
        except Exception:  # noqa: BLE001
            return False

    def _is_noise(self, line: str) -> bool:
        low = line.lower()
        if len(line) < 2:
            return True
        if _TIME_RE.match(low):
            return True
        if _HDR_TIME_RE.search(low):
            return True
        return any(n in low for n in _UI_NOISE)

    @staticmethod
    def _dedup_key(text: str) -> str:
        """Ключ дедупа: только буквы/цифры, нижний регистр. Иконки ховер-тулбара
        и прочий UI-мусор OCR дочитывает к строке по-разному («хуй =», «= хуй»)
        — без нормализации одно сообщение флагается повторно (живой тест 12.07).
        """
        return re.sub(r"[\W_]+", "", text.lower())

    _MAX_SIDE = 4000  # предел большей стороны после апскейла

    def _prepare(self) -> None:
        """Апскейл скриншота ×3 (LANCZOS): шрифт чата ~11-12px «как есть» оба
        движка читают мусором, после апскейла — уверенно (проверено на синтетике,
        см. коммит). Большие окна апскейлим меньше, чтобы не раздувать кадр."""
        self._scale = 1.0
        try:
            from PIL import Image
            img = Image.open(self._img)
            factor = max(1, min(3, self._MAX_SIDE // max(img.size)))
            if factor > 1:
                img.resize((img.width * factor, img.height * factor),
                           Image.LANCZOS).save(self._img)
                self._scale = float(factor)
        except Exception as exc:  # noqa: BLE001
            log.debug("апскейл скриншота не удался: %s", exc)

    def _recognize(self) -> list[ocrutil.Line]:
        """Распознать строки текста на self._img (tesseract, rus+eng, TSV —
        нужны боксы слов для координат строк).

        --psm 11 (sparse text): дефолтная сегментация (psm 3) выбрасывает
        одинокий «пузырь» сообщения рядом с аватаркой как не-текст — первое
        сообщение митинга не читалось вовсе (живой тест 12.07). В sparse-режиме
        и одиночные пузыри, и плотный чат читаются без потерь. Склейка слов в
        строки — общая с paddle (ocrutil.group_lines), сегментации psm 11 не
        доверяем: она дробит визуальную строку на отдельные «блоки»."""
        out = _run(["tesseract", self._img, "stdout", "-l", "rus+eng",
                    "--psm", "11", "tsv"], timeout=20).stdout
        return ocrutil.group_lines(ocrutil.parse_tsv(out))

    def _to_screen(self, line: ocrutil.Line) -> tuple[int, int]:
        """Центр строки: пиксели изображения (после кропа+апскейла) → экран."""
        return (self._origin[0] + int(line.cx / self._scale),
                self._origin[1] + int(line.cy / self._scale))

    def _to_screen_right(self, line: ocrutil.Line) -> tuple[int, int]:
        """Правый край строки в экранных координатах (та же высота)."""
        return (self._origin[0] + int(line.x1 / self._scale),
                self._origin[1] + int(line.cy / self._scale))

    def poll(self) -> list[ChatMessage]:
        if not self._opened:
            if not self._chat_window():
                self._open_panel()
            self._opened = True
        if not self._capture():
            return []
        self._prepare()
        recognized = [ln for ln in self._recognize() if ln.text.strip()]
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
        # Осмысленные строки кадра (сверху вниз) с их ключами.
        rows: list[tuple[str, str, ocrutil.Line]] = []
        for line in recognized:
            text = line.text.strip()
            if self._is_noise(text):
                continue
            key = self._dedup_key(text)
            if key:
                rows.append((key, text, line))
        cur_counts = Counter(k for k, _, _ in rows)
        # Новое = вхождения, которых не было в прошлом кадре. Новые сообщения
        # append-only и появляются СНИЗУ, поэтому лишние вхождения ключа
        # отдаём начиная с нижних строк.
        msgs: list[ChatMessage] = []
        emitted: Counter = Counter()
        for key, text, line in reversed(rows):
            if emitted[key] < cur_counts[key] - self._prev_counts[key]:
                emitted[key] += 1
                msgs.append(ChatMessage(sender="чат", text=text,
                                        pos=self._to_screen(line),
                                        pos_right=self._to_screen_right(line)))
        msgs.reverse()
        self._prev_counts = cur_counts
        if not self._backlog_done:
            # Первое непустое чтение — уже ВИДИМАЯ история чата (рестарт бота
            # посреди митинга или заход в идущий). Полностью выбрасывать её
            # нельзя: мат, отправленный до первого чтения, терялся (живой
            # тест 12.07 — «хуй» сразу после захода бота остался в чате).
            # Помечаем backlog: модерация прогонит только мат-фильтр, без
            # спам-детектора (бэклог приходит разом и выглядит как флуд).
            self._backlog_done = True
            for m in msgs:
                m.backlog = True
            if msgs:
                log.info("OCR чата: бэклог %d строк — только мат-фильтр",
                         len(msgs))
            return msgs
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

    def _recognize(self) -> list[ocrutil.Line]:
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
        # Paddle отдаёт отдельные слова/фрагменты с боксами — склейка в строки
        # общая с tesseract (ocrutil.group_lines), иначе мат, разбитый на
        # «пиз»+«д»+«е», не поймается.
        words: list[ocrutil.Word] = []
        for page in result or []:       # ocr() возвращает список страниц,
            for box, (text, conf) in page or []:  # пустая страница — None
                if conf < self._min_conf:
                    continue
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                words.append(ocrutil.Word(min(xs), min(ys), max(xs), max(ys), text))
        return ocrutil.group_lines(words)


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
