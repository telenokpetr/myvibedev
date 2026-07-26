"""Тесты оркестрации GUI-сценариев (claim host, mute, запись) на фейковом дереве.

Проверяем последовательность действий и корректный разбор результата, не запуская
xdotool/Zoom: atspi.meeting_frame и низкоуровневый ввод замоканы.
"""

import pytest

from app import atspi, gui
from tests.test_atspi import FakeNode


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch):
    # Убираем реальные subprocess-вызовы и паузы.
    monkeypatch.setattr(gui.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(gui, "activate_meeting_window", lambda: None)
    monkeypatch.setattr(atspi, "_xdotool", lambda *a: None)
    typed = {"text": [], "keys": []}
    monkeypatch.setattr(gui, "type_text", lambda t: typed["text"].append(t))
    monkeypatch.setattr(gui, "key", lambda *k: typed["keys"].append(k))
    return typed


def _use_frame(monkeypatch, frame):
    monkeypatch.setattr(atspi, "meeting_frame", lambda: frame)


class TestClaimHost:
    def test_full_flow(self, monkeypatch, _no_side_effects):
        claim = FakeNode("Claim Host", "menu item")
        frame = FakeNode("frame", "frame", children=[
            FakeNode("Participants", "push button"),
            FakeNode("More", "push button"),
            claim,
            FakeNode("Claim", "push button"),  # кнопка подтверждения в диалоге
        ])
        _use_frame(monkeypatch, frame)

        assert gui.claim_host("123456") is True
        assert claim.pressed is True
        assert _no_side_effects["text"] == ["123456"]  # host key введён

    def test_empty_host_key(self, monkeypatch):
        _use_frame(monkeypatch, FakeNode("frame"))
        assert gui.claim_host("") is False

    def test_missing_claim_button(self, monkeypatch, _no_side_effects):
        frame = FakeNode("frame", children=[FakeNode("Participants", "push button")])
        _use_frame(monkeypatch, frame)
        assert gui.claim_host("123456") is False

    def test_no_zoom_window(self, monkeypatch):
        _use_frame(monkeypatch, None)
        assert gui.claim_host("123456") is False


class TestMuteAll:
    def test_flow(self, monkeypatch, _no_side_effects):
        mute_all = FakeNode("Mute All", "push button")
        frame = FakeNode("frame", children=[
            FakeNode("Participants", "push button"),
            mute_all,
            FakeNode("Yes", "push button"),
        ])
        _use_frame(monkeypatch, frame)
        assert gui.mute_all() is True
        assert mute_all.pressed is True

    def test_button_absent(self, monkeypatch, _no_side_effects):
        _use_frame(monkeypatch, FakeNode("frame", children=[]))
        assert gui.mute_all() is False


class TestMuteParticipant:
    def test_flow_targets_mute_not_mute_all(self, monkeypatch, _no_side_effects):
        mute = FakeNode("Mute", "push button")
        frame = FakeNode("frame", children=[
            FakeNode("Participants", "push button"),
            FakeNode("Иван Петров", "list item", center=(10, 10)),
            FakeNode("Mute All", "push button"),
            mute,
        ])
        _use_frame(monkeypatch, frame)
        assert gui.mute_participant("Иван Петров") is True
        assert mute.pressed is True

    def test_unknown_participant(self, monkeypatch, _no_side_effects):
        frame = FakeNode("frame", children=[FakeNode("Participants", "push button")])
        _use_frame(monkeypatch, frame)
        assert gui.mute_participant("Нет Такого") is False


class TestRecordingControls:
    def test_start_cloud_recording(self, monkeypatch, _no_side_effects):
        rec = FakeNode("Record to the Cloud", "menu item")
        frame = FakeNode("frame", children=[FakeNode("More", "push button"), rec])
        _use_frame(monkeypatch, frame)
        assert gui.start_cloud_recording() is True
        assert rec.pressed is True

    def test_stop_absent(self, monkeypatch, _no_side_effects):
        frame = FakeNode("frame", children=[FakeNode("More", "push button")])
        _use_frame(monkeypatch, frame)
        assert gui.stop_cloud_recording() is False
