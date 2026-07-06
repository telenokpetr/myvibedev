"""GUI-автоматизация Zoom-клиента через xdotool.

Координаты кликов ВЕРИФИЦИРОВАНЫ вживую на Zoom Workplace 7.1.0 под Xvfb
1280×720 (см. docs/CALIBRATION.md). Все точки собраны в `COORDS` ниже и привязаны
к этой геометрии; при другом SCREEN_GEOMETRY их надо пересчитать (масштабирование
ненадёжно — UI Zoom не тянется линейно), поэтому `_click` предупреждает в лог.
"""

import logging
import os
import re
import subprocess
import time

from app.config import config

log = logging.getLogger("gui")

ENV = {**os.environ, "DISPLAY": config.display}

# Опорная геометрия, под которую верифицированы координаты (docs/CALIBRATION.md).
_REF_GEOMETRY = (1280, 720)

# Верифицированные вживую координаты кликов (x, y) для 1280×720.
COORDS = {
    # Вход в конференцию (§1)
    "join_with_audio": (637, 323),   # «Join with Computer Audio»
    # Вход в аккаунт Zoom (§3)
    "signin_button": (1045, 32),     # кнопка Sign in в окне
    "email_field": (498, 324),
    "email_next": (498, 380),
    "password_field": (498, 351),
    "stay_signed_in": (326, 401),
    "signin_submit": (498, 456),
    "otp_field": (348, 247),         # первое поле OTP (авто-submit после 6 цифр)
    # Модерация как хост (§4)
    "mute_all_panel": (1060, 686),   # «Mute All» внизу панели участников
    "allow_unmute_check": (399, 421),  # снять «Allow participants to unmute themselves»
    "mute_all_confirm": (744, 421),  # «Mute All» в диалоге подтверждения
    # Аудио-настройки (§5)
    "output_volume_max": (1205, 343),  # ползунок Output volume вправо (=100%)
    "original_sound": (173, 548),    # «Original sound for musicians» в Audio-меню
}


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


def _click(name: str, button: str = "1") -> None:
    """Клик по верифицированной координате из COORDS (см. docs/CALIBRATION.md)."""
    if (config.width, config.height) != _REF_GEOMETRY:
        log.warning("геометрия %dx%d != опорной 1280x720 — координата '%s' не "
                    "откалибрована, клик может промахнуться",
                    config.width, config.height, name)
    x, y = COORDS[name]
    move_click(x, y, button)


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


def _window_size(wid: str) -> tuple[int, int] | None:
    out = _run(["xdotool", "getwindowgeometry", wid]).stdout
    m = re.search(r"Geometry:\s*(\d+)x(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else None


def verify_in_meeting(min_ratio: float = 0.6) -> bool:
    """True, если бот реально в окне митинга, а не завис на диалоге ошибки/ожидания.

    Верификация без OCR (см. баг «слепой state machine» в docs/CALIBRATION.md §7):
    окно конференции развёрнуто почти на весь экран, а диалоги «Invalid meeting ID»
    / «Waiting for host» — маленькие. Считаем входом наличие Zoom-окна шириной и
    высотой >= min_ratio экрана.
    """
    min_w = config.width * min_ratio
    min_h = config.height * min_ratio
    for wid in find_zoom_windows():
        size = _window_size(wid)
        if size and size[0] >= min_w and size[1] >= min_h:
            return True
    return False


def wait_in_meeting(timeout: float = 40.0, interval: float = 2.0) -> bool:
    """Ждём появления окна митинга до timeout сек. False — вход не подтверждён."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if verify_in_meeting():
            return True
        time.sleep(interval)
    return False


def move_mouse_center() -> None:
    """Двигаем мышь в центр — в Zoom так всплывает нижняя панель управления."""
    _run(["xdotool", "mousemove", str(config.width // 2), str(config.height // 2)])


# ---- Сценарии (координаты верифицированы вживую, docs/CALIBRATION.md) ----

def dismiss_startup_dialogs() -> None:
    """Закрываем системные диалоги и подключаем аудио.

    2×Enter подтверждают часть системных окон, но экран пред-входа НЕ проматывают
    (CALIBRATION §1) — вход по ссылке уже инициирован zoommtg://. После входа сам
    всплывает аудио-диалог → жмём «Join with Computer Audio»."""
    for _ in range(2):
        time.sleep(2)
        move_mouse_center()
        key("Return")
    time.sleep(1)
    join_with_computer_audio()


def join_with_computer_audio() -> None:
    """Подключить аудио митинга: диалог «Choose one of the audio conference
    options» появляется сам после входа → клик «Join with Computer Audio» (§1)."""
    _click("join_with_audio")


def claim_host(host_key: str) -> bool:
    """Стать организатором по host key.

    Проверено вживую (CALIBRATION §2): пункт «Claim Host» в UI ОТСУТСТВУЕТ, пока в
    митинге есть активный хост — забрать роль ключом нельзя. Рабочий путь: бот
    входит под owner-аккаунтом (см. sign_in), и при выходе организатора хост
    переходит боту автоматически (host key не нужен). Поэтому здесь — no-op.
    """
    return False


def mute_all(hard: bool = True) -> bool:
    """Mute All как хост (CALIBRATION §4). hard=True — дополнительно снять
    «Allow participants to unmute themselves» (жёсткий мьют).

    Панель участников открывается хоткеем Alt+U (стандартный для Zoom; сам хоткей
    вживую не верифицировали — верифицированы клики в панели/диалоге). Действие
    инициируется оператором через /moderation/mute-all, результат виден в live.
    """
    activate_meeting_window()
    move_mouse_center()
    key("alt+u")                 # показать панель участников
    time.sleep(1)
    _click("mute_all_panel")     # «Mute All» внизу панели
    time.sleep(1)
    if hard:
        _click("allow_unmute_check")  # снять галку самораз-мьюта
    _click("mute_all_confirm")   # подтвердить в диалоге
    return True


def mute_participant(name: str) -> bool:
    """Замьютить конкретного участника (точечная модерация).

    Не откалибровано: в CALIBRATION нет верифицированных координат выбора
    участника в панели (позиция зависит от списка). Для жёсткой модерации
    используйте mute_all(). Пока — no-op (событие фиксируется как «flagged»).
    """
    return False


def delete_chat_message(sender: str, text: str) -> bool:
    """Удалить сообщение в чате (host-контрол).

    Не откалибровано: нет верифицированных координат наведения на конкретное
    сообщение (позиция зависит от прокрутки чата). Пока — no-op.
    """
    return False


# ---- Вход в аккаунт Zoom (§3) ----

def sign_in(email: str, password: str) -> None:
    """Логин в десктоп-клиент под owner-аккаунтом (CALIBRATION §3).

    ⚠️ Zoom шлёт OTP на почту аккаунта → полностью headless вход невозможен:
    после этой функции нужен код из письма → enter_otp(code). «Stay signed in»
    сохраняет сессию между запусками.
    """
    _click("signin_button")
    time.sleep(2)
    _click("email_field")
    type_text(email)
    _click("email_next")
    time.sleep(2)
    _click("password_field")
    type_text(password)
    _click("stay_signed_in")
    _click("signin_submit")


def enter_otp(code: str) -> None:
    """Ввести 6-значный OTP из письма в первое поле (авто-submit; §3)."""
    _click("otp_field")
    type_text(code)


# ---- Аудио-настройки (§5): критично для записи и музыки ----

def set_output_volume_max() -> None:
    """Output volume → 100%, иначе в null-sink уходит тишина и запись немая (§5).
    Клик по правому краю ползунка верифицирован; открытие Audio settings — вручную.
    """
    _click("output_volume_max")


def enable_original_sound() -> None:
    """Включить «Original sound for musicians» — иначе шумодав режет музыку (§5).
    Пункт в Audio-меню (каретка ^ у кнопки Mute); открытие меню — вручную.
    """
    _click("original_sound")


# ---- Облачная запись Zoom: недоступна (§3) ----
# Аккаунт Free → облачной записи нет. Используется локальный ffmpeg (recording.py),
# поэтому все функции ниже — осознанные no-op, а не «недокалибровано».

def start_cloud_recording() -> bool:
    return False


def pause_cloud_recording() -> bool:
    return False


def resume_cloud_recording() -> bool:
    return False


def stop_cloud_recording() -> bool:
    return False
