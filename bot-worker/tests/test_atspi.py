"""Тесты ядра AT-SPI-навигации на фейковом дереве (без pyatspi и Zoom)."""

import pytest

from app import atspi


class FakeNode:
    def __init__(self, name="", role="", children=None, pressable=True, center=None):
        self.name = name
        self.role = role
        self._children = children or []
        self._pressable = pressable
        self._center = center
        self.pressed = False

    @property
    def children(self):
        return self._children

    def press(self):
        if self._pressable:
            self.pressed = True
            return True
        return False

    def center(self):
        return self._center


# ------------------------------------------------------------- label_matches ---

class TestLabelMatches:
    def test_substring_case_insensitive(self):
        assert atspi.label_matches("Participants (5)", ["participants"]) is True
        assert atspi.label_matches("МЬЮТ ВСЕХ", ["мьют"]) is True

    def test_russian_candidate(self):
        assert atspi.label_matches("Стать организатором", ["стать организатором"]) is True

    def test_no_match_and_empty(self):
        assert atspi.label_matches("Hello", ["mute"]) is False
        assert atspi.label_matches("", ["mute"]) is False
        assert atspi.label_matches(None, ["mute"]) is False


# ---------------------------------------------------------------------- find ---

class TestFind:
    def _frame(self):
        return FakeNode("frame", "frame", children=[
            FakeNode("Participants (3)", "push button"),
            FakeNode("Mute All", "push button"),
            FakeNode("Mute", "push button"),
            FakeNode("Some Label", "label"),
        ])

    def test_finds_button_by_key(self):
        node = atspi.find(self._frame(), "participants")
        assert node is not None and node.name.startswith("Participants")

    def test_role_filter_excludes_non_buttons(self):
        # Элемент с подходящей подписью, но ролью label — не кликабельный.
        frame = FakeNode("frame", "frame", children=[
            FakeNode("Mute All", "label"),
        ])
        assert atspi.find(frame, "mute_all") is None

    def test_exclude_key_distinguishes_mute_from_mute_all(self):
        node = atspi.find(self._frame(), "mute", exclude_key="mute_all")
        assert node is not None
        assert node.name == "Mute"

    def test_returns_none_when_absent(self):
        assert atspi.find(self._frame(), "claim_host") is None


# --------------------------------------------------------------- find_by_name ---

class TestFindByName:
    def test_matches_arbitrary_name(self):
        frame = FakeNode("frame", "frame", children=[
            FakeNode("Иван Петров", "list item"),
            FakeNode("Anna Smith", "list item"),
        ])
        assert atspi.find_by_name(frame, "Иван").name == "Иван Петров"

    def test_empty_needle_returns_none(self):
        frame = FakeNode("frame", children=[FakeNode("x")])
        assert atspi.find_by_name(frame, "") is None


# ------------------------------------------------------------- click / hover ---

class TestClickHover:
    def test_click_uses_press(self):
        n = FakeNode("Mute", "push button", pressable=True)
        assert atspi.click(n) is True
        assert n.pressed is True

    def test_click_falls_back_to_coordinates(self, monkeypatch):
        calls = []
        monkeypatch.setattr(atspi, "_xdotool", lambda *a: calls.append(a))
        n = FakeNode("Mute", "push button", pressable=False, center=(100, 200))
        assert atspi.click(n) is True
        assert calls and "click" in calls[0]

    def test_click_none_and_unreachable(self, monkeypatch):
        monkeypatch.setattr(atspi, "_xdotool", lambda *a: None)
        assert atspi.click(None) is False
        assert atspi.click(FakeNode("x", pressable=False, center=None)) is False

    def test_hover_moves_to_center(self, monkeypatch):
        calls = []
        monkeypatch.setattr(atspi, "_xdotool", lambda *a: calls.append(a))
        assert atspi.hover(FakeNode("row", center=(10, 20))) is True
        assert calls[0] == ("mousemove", "10", "20")
        assert atspi.hover(FakeNode("row", center=None)) is False
