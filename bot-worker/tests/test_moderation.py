"""Юнит-тесты движка модерации: фильтр мата, детектор спама, оркестрация.

Всё работает офлайн — GUI-действия и отправка событий в web замоканы/выключены
(callback_url пуст в тестовом окружении), реальный Zoom-клиент не нужен.
"""

import time

import pytest

from app.chatreader import ChatMessage
from app.moderation import (
    Moderator,
    ProfanityFilter,
    SpamDetector,
    normalize,
)


# ---------------------------------------------------------------- normalize ---

class TestNormalize:
    def test_lowercases_and_strips_separators(self):
        assert normalize("Х-У-Й") == "хуй"
        assert normalize("п и з д а") == "пизда"

    def test_leet_substitutions(self):
        # латиница/цифры → кириллица
        assert normalize("п1зда") == "пизда"
        assert normalize("e6a") == "еба"

    def test_plain_text_unchanged_except_case(self):
        assert normalize("Привет, мир") == "привет,мир"


# ---------------------------------------------------------- ProfanityFilter ---

class TestProfanityFilter:
    @pytest.fixture
    def flt(self):
        return ProfanityFilter()

    @pytest.mark.parametrize("text", [
        "иди на хуй",
        "что за пиздец",
        "бля буду",
        "ты гандон",
        "fuck you",
        "what the shit",
    ])
    def test_detects_profanity(self, flt, text):
        assert flt.check(text) is True

    @pytest.mark.parametrize("text", [
        "х у й",        # разделённое пробелами
        "п1зда",        # цифровая подмена
        "f*u*c*k",      # звёздочки-разделители
    ])
    def test_detects_obfuscated(self, flt, text):
        assert flt.check(text) is True

    @pytest.mark.parametrize("text", [
        "Здравствуйте, начинаем лекцию",
        "Спасибо за ответ на вопрос",
        "Хорошая презентация, всё понятно",
        "",
        "Please share the slides",
    ])
    def test_clean_text_not_flagged(self, flt, text):
        # Регресс на ложные срабатывания: обычные фразы не должны триггерить.
        assert flt.check(text) is False

    def test_custom_roots(self):
        flt = ProfanityFilter(roots=[r"запрещёнка"])
        assert flt.check("это запрещёнка") is True
        assert flt.check("иди на хуй") is False


# ------------------------------------------------------------- SpamDetector ---

class TestSpamDetector:
    def test_flood_over_limit(self):
        det = SpamDetector(max_msgs=3, window_sec=10, repeat_limit=99)
        msgs = [ChatMessage("user", f"msg {i}") for i in range(4)]
        results = [det.check(m) for m in msgs]
        # первые 3 — ок, 4-е за окно — флуд
        assert results[:3] == [None, None, None]
        assert results[3] is not None
        assert "флуд" in results[3]

    def test_flood_window_slides(self, monkeypatch):
        det = SpamDetector(max_msgs=2, window_sec=10, repeat_limit=99)
        t = [1000.0]
        monkeypatch.setattr(time, "time", lambda: t[0])

        assert det.check(ChatMessage("u", "a")) is None
        assert det.check(ChatMessage("u", "b")) is None
        t[0] += 20  # старые сообщения вышли из окна
        assert det.check(ChatMessage("u", "c")) is None

    def test_repeat_detection(self):
        det = SpamDetector(max_msgs=99, window_sec=10, repeat_limit=3)
        det.check(ChatMessage("u", "купи крипту"))
        det.check(ChatMessage("u", "купи крипту"))
        res = det.check(ChatMessage("u", "купи крипту"))
        assert res is not None
        assert "повтор" in res

    def test_senders_are_isolated(self):
        det = SpamDetector(max_msgs=2, window_sec=10, repeat_limit=99)
        assert det.check(ChatMessage("alice", "a")) is None
        assert det.check(ChatMessage("alice", "b")) is None
        # у bob свой счётчик — флуда быть не должно
        assert det.check(ChatMessage("bob", "a")) is None

    def test_empty_text_not_repeat(self):
        det = SpamDetector(max_msgs=99, window_sec=10, repeat_limit=2)
        assert det.check(ChatMessage("u", "")) is None
        assert det.check(ChatMessage("u", "")) is None


# ----------------------------------------------------------------- Moderator ---

class TestModerator:
    @pytest.fixture
    def mod(self, monkeypatch):
        m = Moderator()
        # _push не должен ходить в сеть в тестах
        monkeypatch.setattr(m, "_push", lambda ev: None)
        return m

    def test_profanity_takes_priority_over_spam(self, mod):
        ev = mod.process(ChatMessage("u", "иди на хуй"))
        assert ev is not None
        assert ev.category == "profanity"
        assert ev.action == "flagged"  # GUI-действия пока заглушены

    def test_clean_message_returns_none(self, mod):
        assert mod.process(ChatMessage("u", "добрый день")) is None

    def test_spam_repeat_flagged(self, mod):
        mod.process(ChatMessage("u", "реклама"))
        mod.process(ChatMessage("u", "реклама"))
        ev = mod.process(ChatMessage("u", "реклама"))
        assert ev is not None
        assert ev.category == "spam"

    def test_events_are_stored_and_capped(self, mod):
        for i in range(150):
            mod.process(ChatMessage(f"u{i}", "бля"))
        # deque(maxlen=100) не даёт расти бесконечно
        assert len(mod.events) == 100

    def test_status_shape(self, mod):
        mod.process(ChatMessage("u", "fuck"))
        st = mod.status()
        assert st["enabled"] is False
        assert isinstance(st["events"], list)
        assert st["events"][-1]["category"] == "profanity"
