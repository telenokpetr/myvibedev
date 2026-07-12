"""Утилиты OCR: парсинг TSV-вывода tesseract и склейка слов в визуальные строки.

Общий код для chatreader (чтение чата с координатами строк) и gui (поиск
пунктов меню на экране по тексту). Работает с боксами слов, поэтому каждая
склеенная строка знает свой центр в пикселях изображения — на этом держатся
GUI-действия модерации (навести мышь на сообщение и удалить его).
"""

from dataclasses import dataclass


@dataclass
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def h(self) -> float:
        return self.y1 - self.y0


@dataclass
class Line:
    text: str
    cx: int  # центр строки, px изображения
    cy: int


def parse_tsv(tsv: str, min_conf: float = 0.0) -> list[Word]:
    """Слова из TSV-вывода tesseract (level 5 = слово; conf -1 — не слово).

    min_conf=0 для чтения чата (мат с низким conf лучше поймать, чем пропустить),
    выше (50+) — для поиска пунктов меню, где ложный клик хуже пропуска.
    """
    words: list[Word] = []
    for row in tsv.splitlines()[1:]:  # первая строка — заголовок
        cols = row.split("\t")
        if len(cols) < 12:
            continue
        try:
            conf = float(cols[10])
            left, top, w, h = (int(c) for c in cols[6:10])
        except ValueError:
            continue
        text = cols[11].strip()
        if conf < min_conf or conf < 0 or not text:
            continue
        words.append(Word(left, top, left + w, top + h, text))
    return words


def group_lines(words: list[Word]) -> list[Line]:
    """Склейка слов в визуальные строки по близости y-центров.

    И tesseract (psm 11), и paddle отдают отдельные слова/фрагменты — мат,
    разбитый на «пиз»+«д»+«а», без склейки не поймается. Порог — 0.6 медианной
    высоты слова (перенесено из живой проверки paddle-ридера 11.07).
    """
    if not words:
        return []
    ws = sorted(words, key=lambda w: w.yc)
    med_h = sorted(w.h for w in ws)[len(ws) // 2]
    rows: list[list[Word]] = [[ws[0]]]
    for w in ws[1:]:
        if w.yc - rows[-1][-1].yc <= med_h * 0.6:
            rows[-1].append(w)
        else:
            rows.append([w])
    lines: list[Line] = []
    for row in rows:
        row.sort(key=lambda w: w.x0)
        cx = (min(w.x0 for w in row) + max(w.x1 for w in row)) / 2
        cy = sum(w.yc for w in row) / len(row)
        lines.append(Line(" ".join(w.text for w in row), int(cx), int(cy)))
    return lines
