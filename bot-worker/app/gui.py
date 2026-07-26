"""GUI-автоматизация Zoom-клиента через xdotool.

ВАЖНО: точные шаги «Стать организатором» / «Запись» зависят от версии клиента и
раскладки окна. Здесь заложены примитивы (клик, ввод, поиск окна) и best-effort
сценарии. Точные координаты/шаблоны калибруются по скриншотам на живом митинге —
см. TODO ниже.
"""

import os
import subprocess
import time

from app import atspi
from app.config import config

ENV = {**os.environ, "DISPLAY": config.display}

# Паузы, чтобы панели/меню Zoom успевали раскрыться между шагами.
_PANEL_WAIT = 1.0
_MENU_WAIT = 0.5


def _run(args: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, env=ENV, capture_output=True, text=True, timeout=timeout, check=False
    )


# ---- Примитивы ----

def key(*keys: str) -> None:
    _run(["xdotool", "key", "--clearmodifiers", *keys])


def type_text(text: str) -> None:
    _run(["xdotool", "type", "--clearmodifiers", text])


def move_click(x: int, y: int, button: str = "1") -> None:
    _run(["xdotool", "mousemove", str(x), str(y), "click", button])


def find_zoom_windows() -> list[str]:
    out = _run(["xdotool", "search", "--class", "zoom"]).stdout.split()
    return out


def zoom_window_titles() -> list[str]:
    titles = []
    for wid in find_zoom_windows():
        t = _run(["xdotool", "getwindowname", wid]).stdout.strip()
        if t:
            titles.append(t)
    return titles


def activate_meeting_window() -> str | None:
    """Активируем окно митинга (обычно самое большое окно Zoom) и максимизируем."""
    wins = find_zoom_windows()
    if not wins:
        return None
    # Берём последнее — как правило, это окно конференции, а не главное меню.
    wid = wins[-1]
    _run(["xdotool", "windowactivate", "--sync", wid])
    _run(["wmctrl", "-i", "-r", wid, "-b", "add,maximized_vert,maximized_horz"])
    return wid


def move_mouse_center() -> None:
    """Двигаем мышь в центр — в Zoom так всплывает нижняя панель управления."""
    _run(["xdotool", "mousemove", str(config.width // 2), str(config.height // 2)])


# ---- Сценарии управления через AT-SPI ----
#
# Пути в UI Zoom (гайдлайны десктоп-клиента):
#   Claim host:  Participants → ⋯ (More) → Claim Host → ввод host key → Claim
#   Mute all:    Participants → Mute All → подтвердить
#   Mute one:    Participants → навести на участника → Mute
#   Delete msg:  Chat → навести на сообщение → ⋯ → Delete
#   Record:      More (⋯) → Record to the Cloud / Pause / Resume / Stop
#
# Подписи элементов берём из atspi.LABELS (EN+RU). Если pyatspi недоступен или
# элемент не найден — функция возвращает False (без падения), поведение
# калибруется расширением LABELS по скриншотам живого клиента.


def _open_participants(frame):
    """Открыть панель участников (если кнопка есть) и вернуть свежий кадр окна."""
    btn = atspi.find(frame, "participants")
    if btn:
        atspi.click(btn)
        time.sleep(_PANEL_WAIT)
    return atspi.meeting_frame() or frame


def dismiss_startup_dialogs() -> None:
    """Закрываем стартовые окна (согласие/аудио): пара Enter обычно подтверждает
    вход и «Join with Computer Audio»."""
    for _ in range(2):
        time.sleep(2)
        move_mouse_center()
        key("Return")


def claim_host(host_key: str) -> bool:
    """Стать организатором: Participants → ⋯ → Claim Host → host key → подтвердить."""
    if not host_key:
        return False
    frame = atspi.meeting_frame()
    if frame is None:
        return False
    activate_meeting_window()
    frame = _open_participants(frame)

    more = atspi.find(frame, "more")
    if more:
        atspi.click(more)
        time.sleep(_MENU_WAIT)
        frame = atspi.meeting_frame() or frame

    claim = atspi.find(frame, "claim_host")
    if not claim:
        return False
    atspi.click(claim)
    time.sleep(_MENU_WAIT)

    # Диалог: ввести host key в активное поле и подтвердить.
    type_text(host_key)
    frame = atspi.meeting_frame() or frame
    confirm = atspi.find(frame, "confirm")
    if confirm:
        atspi.click(confirm)
    else:
        key("Return")
    return True


def mute_all() -> bool:
    """Отключить звук всем: Participants → Mute All → подтвердить."""
    frame = atspi.meeting_frame()
    if frame is None:
        return False
    frame = _open_participants(frame)

    btn = atspi.find(frame, "mute_all")
    if not btn:
        return False
    atspi.click(btn)
    time.sleep(_MENU_WAIT)

    frame = atspi.meeting_frame() or frame
    confirm = atspi.find(frame, "confirm")
    if confirm:
        atspi.click(confirm)
    else:
        key("Return")
    return True


def mute_participant(name: str) -> bool:
    """Замьютить участника: Participants → навести на строку → Mute."""
    if not name:
        return False
    frame = atspi.meeting_frame()
    if frame is None:
        return False
    frame = _open_participants(frame)

    row = atspi.find_by_name(frame, name)
    if row is None:
        return False
    atspi.hover(row)          # hover раскрывает кнопки строки
    time.sleep(_MENU_WAIT)

    frame = atspi.meeting_frame() or frame
    # «Mute», но не «Mute All».
    btn = atspi.find(frame, "mute", exclude_key="mute_all")
    if not btn:
        return False
    atspi.click(btn)
    return True


def delete_chat_message(sender: str, text: str) -> bool:
    """Удалить сообщение: Chat → навести на сообщение → ⋯ → Delete."""
    frame = atspi.meeting_frame()
    if frame is None or not text:
        return False

    chat = atspi.find(frame, "chat")
    if chat:
        atspi.click(chat)
        time.sleep(_PANEL_WAIT)
        frame = atspi.meeting_frame() or frame

    msg = atspi.find_by_name(frame, text[:40])
    if msg is None:
        return False
    atspi.hover(msg)
    time.sleep(_MENU_WAIT)

    frame = atspi.meeting_frame() or frame
    more = atspi.find(frame, "more")
    if more:
        atspi.click(more)
        time.sleep(_MENU_WAIT)
        frame = atspi.meeting_frame() or frame

    delete = atspi.find(frame, "delete")
    if not delete:
        return False
    atspi.click(delete)
    return True


def _more_menu_action(key_name: str) -> bool:
    """Открыть меню ⋯ (More) и кликнуть пункт по ключу LABELS."""
    frame = atspi.meeting_frame()
    if frame is None:
        return False
    activate_meeting_window()

    more = atspi.find(frame, "more")
    if more:
        atspi.click(more)
        time.sleep(_MENU_WAIT)
        frame = atspi.meeting_frame() or frame

    item = atspi.find(frame, key_name)
    if not item:
        return False
    atspi.click(item)
    return True


def start_cloud_recording() -> bool:
    """Облачная запись: More (⋯) → Record to the Cloud (нужны host-права)."""
    return _more_menu_action("record")


def pause_cloud_recording() -> bool:
    """More (⋯) → Pause Recording."""
    return _more_menu_action("pause_record")


def resume_cloud_recording() -> bool:
    """More (⋯) → Resume Recording."""
    return _more_menu_action("resume_record")


def stop_cloud_recording() -> bool:
    """More (⋯) → Stop Recording."""
    return _more_menu_action("stop_record")
