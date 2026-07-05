"""GUI-автоматизация Zoom-клиента через xdotool.

ВАЖНО: точные шаги «Стать организатором» / «Запись» зависят от версии клиента и
раскладки окна. Здесь заложены примитивы (клик, ввод, поиск окна) и best-effort
сценарии. Точные координаты/шаблоны калибруются по скриншотам на живом митинге —
см. TODO ниже.
"""

import os
import subprocess
import time

from app.config import config

ENV = {**os.environ, "DISPLAY": config.display}


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


# ---- Best-effort сценарии (калибруются на живом клиенте) ----

def dismiss_startup_dialogs() -> None:
    """Закрываем стартовые окна (согласие/аудио): пара Enter обычно подтверждает
    вход и «Join with Computer Audio»."""
    for _ in range(2):
        time.sleep(2)
        move_mouse_center()
        key("Return")


def claim_host(host_key: str) -> bool:
    """Стать организатором по host key.

    TODO(калибровка): реальный путь в UI —
      Participants → ⋯ (More) → Claim Host → ввести host key → Claim.
    Ниже — заготовка через ввод; точные клики подставим после первого прогона,
    когда увидим скриншоты панели участников.
    """
    if not host_key:
        return False
    activate_meeting_window()
    move_mouse_center()
    # Пока безопасная заглушка: печатаем host key в активное поле, если оно открыто.
    # Полноценную навигацию по панели участников добавим по скриншотам.
    return False


def mute_all() -> bool:
    """Отключить звук всем участникам (модерация).

    TODO(калибровка): Participants → Mute All → подтвердить. Точные клики
    подставим по скриншотам живого митинга.
    """
    activate_meeting_window()
    return False


def start_cloud_recording() -> bool:
    """Запустить облачную запись Zoom (нужны host-права и платный аккаунт).

    TODO(калибровка): More (⋯) → Record to the Cloud, либо host-контрол записи.
    """
    activate_meeting_window()
    return False


def pause_cloud_recording() -> bool:
    """TODO(калибровка): host-контрол «Pause Recording»."""
    return False


def resume_cloud_recording() -> bool:
    """TODO(калибровка): host-контрол «Resume Recording»."""
    return False


def stop_cloud_recording() -> bool:
    """TODO(калибровка): host-контрол «Stop Recording»."""
    return False
