"""Тест сопоставления реакций: шаблоны _REACTIONS должны находить нужную кнопку
эмодзи по её aria-label среди типичных подписей панели Zoom (EN + RU).

Playwright не импортируем (в CI его нет) — берём _REACTIONS прямо из исходника
через ast, что и тестирует именно те шаблоны, что использует бот.
"""

import ast
import re
from pathlib import Path

import pytest

_SRC = (Path(__file__).resolve().parent.parent / "app" / "zoomweb.py").read_text(
    encoding="utf-8")
_REACTIONS = ast.literal_eval(re.search(r"_REACTIONS = (\{.*?\})", _SRC, re.S).group(1))


def _pick(kind: str, labels: list[str]) -> str | None:
    """Мимика _JS_FIND_CLICKABLE: из подписей, совпавших с шаблоном kind, берём
    самую короткую (в JS-хелпере — элемент с самым коротким label)."""
    rx = re.compile(_REACTIONS[kind], re.I)
    hits = sorted([l for l in labels if rx.search(l)], key=len)
    return hits[0] if hits else None


PANEL_EN = ["Clapping Hands", "Thumbs Up", "Red Heart",
            "Face with Tears of Joy", "Party Popper", "Waving Hand", "Raise Hand"]
PANEL_RU = ["Аплодисменты", "Большой палец вверх", "Красное сердце",
            "Лицо со слезами радости", "Хлопушка", "Помахать рукой", "Поднять руку"]

# kind → ожидаемая подпись в каждой панели
CASES_EN = {"clap": "Clapping Hands", "like": "Thumbs Up", "heart": "Red Heart",
            "joy": "Face with Tears of Joy", "tada": "Party Popper",
            "wave": "Waving Hand"}
CASES_RU = {"clap": "Аплодисменты", "like": "Большой палец вверх",
            "heart": "Красное сердце", "joy": "Лицо со слезами радости",
            "tada": "Хлопушка", "wave": "Помахать рукой"}


@pytest.mark.parametrize("kind,expected", CASES_EN.items())
def test_reactions_match_english_labels(kind, expected):
    assert _pick(kind, PANEL_EN) == expected


@pytest.mark.parametrize("kind,expected", CASES_RU.items())
def test_reactions_match_russian_labels(kind, expected):
    assert _pick(kind, PANEL_RU) == expected


def test_wave_does_not_grab_raise_hand():
    # Регресс: «wave» не должен цеплять «Raise Hand»/«Поднять руку».
    assert _pick("wave", ["Raise Hand"]) is None
    assert _pick("wave", ["Поднять руку"]) is None


def test_clap_does_not_grab_party_popper():
    # Регресс: «clap» не должен цеплять «Хлопушку» (это tada).
    assert _pick("clap", ["Хлопушка"]) is None
    assert _pick("tada", ["Хлопушка"]) == "Хлопушка"
