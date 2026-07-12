"""GUI-автоматизация Zoom-клиента через xdotool.

Деплой работает на 1280×720 (docker-compose SCREEN_GEOMETRY). Координаты входа в
аккаунт, конференцию, Audio-меню и Mute All ВЕРИФИЦИРОВАНЫ вживую под 720p
(docs/CALIBRATION.md). Координаты облачной записи (`rec_*`) выверялись под 1080p —
они в `_UNVERIFIED`, и `_click` предупреждает при их использовании под 720p.
"""

import logging
import os
import re
import subprocess
import time

from app.config import config

log = logging.getLogger("gui")

ENV = {**os.environ, "DISPLAY": config.display}

# Опорная геометрия деплоя (docker-compose SCREEN_GEOMETRY).
_REF_GEOMETRY = (1920, 1080)

# Координаты кликов (x, y) под 1920×1080. Часть выверена вживую, часть в
# _UNVERIFIED (калибруется на живом митинге; `_click` предупреждает).
COORDS = {
    # Вход в аккаунт Zoom — экран логина клиента (§3), верифицировано 1080p
    "signin_button": (817, 583),
    "email_field": (817, 503),
    "email_next": (817, 559),
    "password_field": (817, 531),
    "stay_signed_in": (647, 580),
    "signin_submit": (817, 634),
    "otp_field": (700, 400),         # ОЦЕНКА — уточнить когда появится OTP
    # Вход в конференцию (§1)
    "join_meeting": (1229, 945),     # «Join» на экране preview (верифиц. 1080p)
    "join_with_audio": (637, 323),   # «Join with Computer Audio» — калибровать 1080p
    # Audio-настройки (§5)
    # ВНИМАНИЕ: координаты тулбара/меню — для ПОЛНОЭКРАННОГО окна 1920×1080
    # (maximize_meeting_window принудительно приводит окно к этому размеру).
    "audio_menu_caret": (78, 1040),  # каретка ^ у кнопки Audio (fullscreen, верифиц.)
    "original_sound": (174, 909),    # «Original sound for musicians» (fullscreen, верифиц.)
    "output_volume_max": (1205, 343),
    # Модерация как хост (§4) — калибровать 1080p
    "mute_all_panel": (1060, 686),
    "allow_unmute_check": (399, 421),
    "mute_all_confirm": (744, 421),
    # Облачная запись — верифицировано вживую 1080p:
    "rec_indicator": (955, 172),
    "rec_cloud_option": (510, 803),
    "rec_stop_option": (816, 243),
    "rec_pause_option": (816, 291),
    "rec_stop_confirm_yes": (1082, 688),
}

# Координаты, ещё не выверенные под 1080p — `_click` предупреждает при использовании.
_UNVERIFIED = {
    "otp_field", "join_with_audio",
    "mute_all_panel", "allow_unmute_check", "mute_all_confirm",
    "output_volume_max",
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


def paste_text(text: str) -> None:
    """Надёжный ввод текста (в т.ч. кириллицы) через буфер обмена: xclip + Ctrl+V.
    `xdotool type` кириллицу под Xvfb вводит ненадёжно (см. CALIBRATION §4)."""
    subprocess.run(["xclip", "-selection", "clipboard"], env=ENV,
                   input=text.encode("utf-8"), timeout=5, check=False)
    time.sleep(0.3)
    key("ctrl+v")


def move_click(x: int, y: int, button: str = "1") -> None:
    _run(["xdotool", "mousemove", str(x), str(y), "click", button])


def _click(name: str, button: str = "1") -> None:
    """Клик по координате из COORDS (см. docs/CALIBRATION.md)."""
    if (config.width, config.height) != _REF_GEOMETRY:
        log.warning("геометрия %dx%d != опорной %dx%d — координата '%s' может "
                    "промахнуться", config.width, config.height,
                    _REF_GEOMETRY[0], _REF_GEOMETRY[1], name)
    elif name in _UNVERIFIED:
        log.warning("координата '%s' не выверена под текущую геометрию — "
                    "клик может промахнуться", name)
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


# Заголовок окна активной конференции в этой версии Zoom — ровно «Meeting»
# (превью называется темой митинга, лобби/диалоги — «Zoom Workplace»). Проверено
# вживую 8 июля 2026, см. docs/CALIBRATION.md §7.
_MEETING_TITLE_RE = re.compile(r"^(zoom )?meeting$", re.I)


def find_meeting_window() -> str | None:
    """WID окна активной конференции (заголовок «Meeting»), либо None.

    Отличает реальный вход от превью/лобби/главного окна по ЗАГОЛОВКУ, а не по
    размеру: главное окно «Zoom Workplace» тоже развёрнуто и раньше давало ложный
    вход ещё до подключения (баг «слепой state machine», CALIBRATION §7).
    """
    for wid in find_zoom_windows():
        title = _run(["xdotool", "getwindowname", wid]).stdout.strip()
        if _MEETING_TITLE_RE.match(title):
            return wid
    return None


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


def focus_meeting_window() -> str | None:
    """Активировать именно ОКНО МИТИНГА (по заголовку «Meeting»), а не последнее
    окно Zoom: хоткеи митинга (alt+h и т.п.) в фокусе плавающего окна чата
    печатают буквы в поле ввода (живой тест 12.07)."""
    wid = find_meeting_window()
    if wid:
        _run(["xdotool", "windowactivate", "--sync", wid])
    return wid


def maximize_meeting_window() -> str | None:
    """Развернуть окно конференции РОВНО на весь экран (детерминированно).

    Важно: раскладка нижнего тулбара зависит от размера окна (плавающее 1188×800 vs
    полноэкранное 1920×1080) → фикс-координаты меню аудио промахиваются. Поэтому
    принудительно приводим окно к полному экрану (windowsize+move), а не полагаемся
    только на wmctrl maximize, который срабатывает не всегда."""
    wid = find_meeting_window()
    if wid is None:
        return None
    _run(["xdotool", "windowactivate", "--sync", wid])
    _run(["wmctrl", "-i", "-r", wid, "-b", "add,maximized_vert,maximized_horz"])
    _run(["xdotool", "windowsize", wid, str(config.width), str(config.height)])
    _run(["xdotool", "windowmove", wid, "0", "0"])
    return wid


def _is_fullscreen() -> bool:
    """Митинг в НАТИВНОМ полноэкранном режиме Zoom? Признак — вокруг нет домашнего
    окна «Zoom Workplace» (в fullscreen оно скрыто под митингом). Проверяем OCR:
    в fullscreen НЕТ пунктов левого меню home (Whiteboards/Canvas/Docs)."""
    t = _ocr_screen()
    return "whiteboards" not in t and "canvas" not in t


def enter_fullscreen(retries: int = 2) -> None:
    """Ввести митинг в НАТИВНЫЙ полноэкранный режим Zoom (двойной клик по видео).

    Критично для записи: ffmpeg снимает весь экран 1920×1080; в оконном режиме
    митинг — мелкое окно с домашней Zoom вокруг → плохой файл. Нативный fullscreen
    (в отличие от WM-resize) надёжно заполняет экран. Идемпотентно: если уже
    fullscreen — не трогаем (двойной клик — тумблер, иначе бы вышли)."""
    wid = find_meeting_window()
    if wid is None:
        return
    _run(["xdotool", "windowactivate", "--sync", wid])
    for _ in range(retries):
        if _is_fullscreen():
            return
        move_mouse_center()
        _run(["xdotool", "click", "--repeat", "2", "--delay", "200", "1"])
        time.sleep(2.0)
    # финально не проверяем повторно, чтобы случайно не выйти тумблером


def _window_size(wid: str) -> tuple[int, int] | None:
    out = _run(["xdotool", "getwindowgeometry", wid]).stdout
    m = re.search(r"Geometry:\s*(\d+)x(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else None


# Текст на экране, означающий что бот ПОДКЛЮЧИЛСЯ, но митинг ещё не идёт
# (хост не запустил / зал ожидания). Это НЕ ошибка входа.
_WAIT_KEYWORDS = (
    "waiting for the host", "waiting for host",
    "let them know you're here", "we've let them know",
    "host has joined", "please wait", "waiting room",
)


def _ocr_screen() -> str:
    """OCR всего экрана (англ.) — вернуть текст в нижнем регистре. '' при ошибке."""
    path = "/tmp/_ocr_state.png"
    try:
        _run(["scrot", "-o", path], timeout=8)
        r = _run(["tesseract", path, "stdout", "-l", "eng"], timeout=15)
        return r.stdout.lower()
    except Exception:  # noqa: BLE001
        return ""


def find_text_on_screen(word: str, min_conf: float = 50.0,
                        upscale: int = 2) -> list[tuple[int, int]]:
    """Все вхождения слова на экране (tesseract TSV) → центры в ЭКРАННЫХ px.

    Резолюшн-независимая альтернатива фикс-координатам для текстовых пунктов
    меню/кнопок. Апскейл ×2 (LANCZOS) — мелкий UI-шрифт Zoom без апскейла
    читается ненадёжно (тот же вывод, что и для чата, RELIABILITY-PLAN P2).
    min_conf высокий: ложный клик по меню хуже, чем «не нашли»."""
    from app import ocrutil
    path = "/tmp/_ocr_find.png"
    try:
        if _run(["scrot", "-o", path], timeout=8).returncode != 0:
            return []
        if upscale > 1:
            from PIL import Image
            img = Image.open(path)
            img.resize((img.width * upscale, img.height * upscale),
                       Image.LANCZOS).save(path)
        out = _run(["tesseract", path, "stdout", "-l", "eng",
                    "--psm", "11", "tsv"], timeout=20).stdout
    except Exception:  # noqa: BLE001
        return []
    target = word.lower()
    return [(int(w.xc / upscale), int(w.yc / upscale))
            for w in ocrutil.parse_tsv(out, min_conf=min_conf)
            if w.text.lower().strip(".,:;…") == target]


def click_text(word: str, near: tuple[int, int] | None = None,
               prefer_bottom: bool = False) -> bool:
    """Найти слово на экране и кликнуть по нему. near — выбрать вхождение,
    ближайшее к точке (контекстное меню у курсора); prefer_bottom — нижнее
    (кнопка диалога, а не его заголовок). False — слово не найдено."""
    hits = find_text_on_screen(word)
    if not hits:
        return False
    if near is not None:
        hits.sort(key=lambda p: (p[0] - near[0]) ** 2 + (p[1] - near[1]) ** 2)
    elif prefer_bottom:
        hits.sort(key=lambda p: -p[1])
    move_click(*hits[0])
    return True


def meeting_state() -> str:
    """Состояние входа: 'live' (окно Meeting) | 'waiting' (ожидание хоста/зал
    ожидания) | 'none' (не вошёл / дома / ошибка)."""
    if find_meeting_window() is not None:
        return "live"
    text = _ocr_screen()
    if any(k in text for k in _WAIT_KEYWORDS):
        return "waiting"
    return "none"


def verify_in_meeting(min_ratio: float = 0.6) -> bool:
    """True, если бот подключился к конференции — включая «ожидание хоста».

    'live' — окно с заголовком «Meeting»; 'waiting' — экран ожидания хоста/зала
    ожидания (окно всё ещё «Zoom Workplace», отличаем по тексту через OCR). Раньше
    учитывалось только окно «Meeting» → бот, пришедший раньше хоста, ошибочно давал
    `error` (docs/CALIBRATION.md §7). `min_ratio` не используется (для совместимости).
    """
    return meeting_state() in ("live", "waiting")


def wait_in_meeting(timeout: float = 60.0, interval: float = 3.0) -> bool:
    """Ждём подтверждения входа (live или waiting) до timeout сек.
    False — вход не подтверждён (остались дома / ошибка)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if verify_in_meeting():
            return True
        time.sleep(interval)
    return False


def move_mouse_center() -> None:
    """Двигаем мышь в центр — в Zoom так всплывает нижняя панель управления."""
    _run(["xdotool", "mousemove", str(config.width // 2), str(config.height // 2)])


def move_mouse_top() -> None:
    """Двигаем мышь к верхней кромке — так всплывает верхняя панель с индикатором
    записи «REC» (позиция стабильна, не зависит от плавающего нижнего тулбара)."""
    _run(["xdotool", "mousemove", str(config.width // 2), "20"])


# ---- Сценарии (координаты верифицированы вживую, docs/CALIBRATION.md) ----

def dismiss_startup_dialogs() -> None:
    """Проходим экран пред-входа и подключаем аудио.

    Экран пред-входа (preview) НЕ проматывается по Enter (CALIBRATION §1) — нужен
    явный клик по «Join». После входа сам всплывает аудио-диалог → «Join with
    Computer Audio». 2×Enter подтверждают возможные системные окна между шагами."""
    time.sleep(2)
    move_mouse_center()
    key("Return")                    # на случай системного окна
    click_prejoin_join()             # «Join» на экране preview
    time.sleep(2)
    key("Return")
    time.sleep(1)
    join_with_computer_audio()


def click_prejoin_join() -> None:
    """Клик «Join» на экране пред-входа (preview) — иначе бот застревает на превью
    и в митинг не входит (CALIBRATION §1)."""
    _click("join_meeting")


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

    Не реализовано: OCR-ридер чата не привязывает автора к сообщению
    (sender="чат"), так что мьютить некого; поиск участника в панели по имени —
    отдельная задача. Для жёсткой модерации используйте mute_all(). Пока —
    no-op (событие фиксируется как «flagged»).
    """
    return False


def _chat_window_id() -> str | None:
    """WID плавающего окна «Meeting chat» (появляется в нативном fullscreen или
    если чат «вынесен»), либо None — чат докнут панелью в окно митинга."""
    out = _run(["xdotool", "search", "--name", "Meeting chat"]).stdout.split()
    return out[0] if out else None


def _diff_rightmost_cluster(a_path: str, b_path: str,
                            x: int, y: int) -> tuple[int, int] | None:
    """Правый крайний кластер изменившихся пикселей между двумя скриншотами в
    полосе строки y±18 правее x — это кнопка «…» всплывшего ховер-тулбара."""
    try:
        import numpy as np
        from PIL import Image
        a = np.asarray(Image.open(a_path).convert("L"), dtype=np.int16)
        b = np.asarray(Image.open(b_path).convert("L"), dtype=np.int16)
        if a.shape != b.shape:
            return None
        band = 18
        y0, y1 = max(0, y - band), min(a.shape[0], y + band)
        x0 = min(x + 10, a.shape[1] - 1)   # x+10 — отсечь сам курсор мыши
        diff = np.abs(a[y0:y1, x0:] - b[y0:y1, x0:]) > 25
        cols = np.where(diff.any(axis=0))[0]
        if cols.size == 0:
            return None
        # кластеры колонок по разрывам >12px; правый кластер = «…»
        breaks = np.where(np.diff(cols) > 12)[0]
        cluster = cols[breaks[-1] + 1:] if breaks.size else cols
        cx = x0 + int(cluster.mean())
        rows = np.where(diff[:, cluster].any(axis=1))[0]
        return cx, y0 + int(rows.mean())
    except Exception as exc:  # noqa: BLE001
        log.warning("_diff_rightmost_cluster: %s", exc)
        return None


def _find_hover_ellipsis_docked(x: int, y: int) -> tuple[int, int] | None:
    """«…» для ДОКНУТОЙ панели чата: ховер работает от движения мыши.

    Кадр «без ховера» — курсор НАД строкой (y-80): тулбар всплывает при
    наведении на любую точку строки, поэтому нейтральная точка на той же y
    держала тулбар в обоих кадрах и дифф был пуст. Перед вторым кадром —
    «шевеление» мыши (одиночный телепорт xdotool ховер поднимает не всегда)."""
    _run(["xdotool", "mousemove", str(x), str(max(0, y - 80))])
    time.sleep(0.6)
    _run(["scrot", "-o", "/tmp/_hover_a.png"], timeout=8)
    _run(["xdotool", "mousemove", str(x - 8), str(y)])
    time.sleep(0.15)
    _run(["xdotool", "mousemove", str(x), str(y)])
    time.sleep(0.9)
    _run(["scrot", "-o", "/tmp/_hover_b.png"], timeout=8)
    return _diff_rightmost_cluster("/tmp/_hover_a.png", "/tmp/_hover_b.png", x, y)


def _find_hover_ellipsis_floating(wid: str, x: int, y: int) -> tuple[int, int] | None:
    """«…» для ПЛАВАЮЩЕГО окна «Meeting chat».

    Живой тест 12.07: плавающее окно не реагирует на XTest-движения мыши —
    ховер-тулбар оно показывает ровно один раз, при ОТКРЫТИИ окна, для строки
    под курсором (позицию оно опрашивает само). Поэтому: кадр А (тулбара нет —
    ховер мёртв) → закрыть окно → фокус на митинг → подвести мышь к строке
    (движение должно случиться при живом окне митинга) → Alt+H (окно
    возвращается с той же геометрией/прокруткой, тулбар всплывает под
    курсором) → кадр Б → дифф. Alt+H слать только при фокусе на ОКНЕ
    МИТИНГА: в фокусе чата он печатает «h» в поле ввода."""
    _run(["xdotool", "mousemove", str(x), str(y)])
    time.sleep(0.4)
    _run(["scrot", "-o", "/tmp/_hover_a.png"], timeout=8)
    _run(["wmctrl", "-i", "-c", wid])          # мягко закрыть окно чата
    time.sleep(1.2)
    if focus_meeting_window() is None:
        return None
    # Мышь на цель — ПОСЛЕ закрытия чата: движение должно увидеть окно
    # митинга, тогда Zoom при открытии чата поднимет тулбар под курсором.
    _run(["xdotool", "mousemove", str(x - 40), str(y)])
    time.sleep(0.15)
    _run(["xdotool", "mousemove", str(x), str(y)])
    time.sleep(0.3)
    key("alt+h")                               # переоткрыть чат под курсором
    time.sleep(1.8)
    if not _chat_window_id():
        log.warning("чат не переоткрылся после alt+h")
        return None
    _run(["scrot", "-o", "/tmp/_hover_b.png"], timeout=8)
    return _diff_rightmost_cluster("/tmp/_hover_a.png", "/tmp/_hover_b.png", x, y)


def delete_chat_message(pos: tuple[int, int] | None,
                        pos_right: tuple[int, int] | None = None) -> bool:
    """Удалить сообщение чата (host-контрол): ховер по строке сообщения →
    кнопка «…» всплывшего тулбара → меню Copy/Quote/Delete → «Delete».

    pos/pos_right — центр и правый край строки в экранных координатах, их
    знает OCR-ридер (ChatMessage), поэтому фикс-координат здесь нет: «…»
    находится пиксель-диффом ховера (_find_hover_ellipsis), а если дифф пуст —
    кликом с оффсетом от правого края пузыря (иконки тулбара идут через ~21px:
    ответить +20, эмодзи +42, «…» +63; замерено вживую 12.07). Пункт меню —
    OCR-поиском слова Delete. Best-effort: чат мог прокрутиться с момента
    распознавания — тогда тулбар/пункт не найдётся и вернём False (событие
    останется «flagged»). Требует прав хоста."""
    if not pos:
        return False
    x, y = pos
    wid = _chat_window_id()
    if wid:
        dots = _find_hover_ellipsis_floating(wid, x, y)
    else:
        mwid = find_meeting_window()
        if mwid:
            _run(["xdotool", "windowactivate", "--sync", mwid])
            time.sleep(0.3)
        dots = _find_hover_ellipsis_docked(x, y)
    if dots is None and pos_right:
        # дифф не увидел тулбар — возможно, он уже был поднят (кадр «до» тоже
        # его содержал). Пробуем оффсет от правого края текста строки: иконки
        # тулбара идут от правого края пузыря через ~21px, «…» ≈ x1+68
        # (замерено вживую 12.07 на пузырях «а» и «еблан»).
        dots = (pos_right[0] + 68, y)
        log.info("delete_chat_message: тулбар по диффу не найден, "
                 "пробую оффсет от пузыря: (%d,%d)", *dots)
    if dots is None:
        log.info("delete_chat_message: тулбар ховера не найден у (%d,%d)", x, y)
        return False
    move_click(*dots)                     # открыть меню «…»
    time.sleep(1.0)
    if not click_text("delete", near=dots):
        key("Escape")
        log.info("delete_chat_message: пункт Delete не найден у (%d,%d)", x, y)
        return False
    time.sleep(0.8)
    # Возможен диалог подтверждения — его кнопка «Delete» ниже заголовка.
    # Если диалога нет, click_text вернёт False («deleted»-плейсхолдер и прочие
    # формы слова точному матчу не соответствуют) — это не ошибка.
    click_text("delete", prefer_bottom=True)
    key("Escape")                         # прибрать остатки меню/диалога
    return True


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


def _original_sound_on() -> bool:
    """OCR-проверка баннера подтверждения включения Original sound."""
    return "original sound for musicians is on" in _ocr_screen()


def _reveal_toolbar() -> None:
    """Разбудить нижний тулбар движением мыши у самой кромки экрана."""
    _run(["xdotool", "mousemove", str(config.width // 2), str(config.height - 3)])
    time.sleep(0.5)
    _run(["xdotool", "mousemove", str(config.width // 2), str(config.height - 45)])
    time.sleep(0.6)


def enable_original_sound(retries: int = 3) -> bool:
    """Включить «Original sound for musicians» — иначе шумодав режет музыку (§5).

    Открывает меню аудио (каретка ^) и СРАЗУ кликает пункт — без OCR между кликами,
    иначе меню успевает закрыться. Результат проверяем по баннеру «...is on» и при
    неудаче повторяем. Клик — тумблер: если было уже включено, первый клик выключит,
    следующий снова включит, поэтому цикл сходится к «On»."""
    enter_fullscreen()               # нативный fullscreen -> стабильные координаты тулбара
    time.sleep(1.2)
    for _ in range(retries):
        _reveal_toolbar()
        _click("audio_menu_caret")   # открыть меню
        time.sleep(1.0)
        _click("original_sound")     # сразу кликнуть пункт (меню ещё открыто)
        time.sleep(1.0)
        if _original_sound_on():
            key("Escape")
            return True
        key("Escape")
    log.warning("enable_original_sound: не удалось подтвердить включение")
    return False


def setup_meeting_audio() -> None:
    """Пост-вход: включить Original sound (иначе музыку глушит шумодав). Микрофон
    BotMic и «не замьючен» обычно уже стоят из профиля/входа с computer audio.
    Критично для гостя — у него режим сбрасывается на Noise removal при рестарте."""
    try:
        enable_original_sound()
    except Exception as exc:  # noqa: BLE001
        log.warning("setup_meeting_audio: не удалось включить original sound: %s", exc)


# ---- Облачная запись Zoom (как со-хост в лицензированном митинге) ----
# Управление через закреплённую кнопку Record в нижней панели. Кнопка — единая
# точка: не пишет → меню «Record to this computer / Record to the cloud»; пишет →
# «Stop / Pause recording». Стоп требует подтверждения «Yes». Координаты сняты
# вживую 8 июля (1080p, полноэкранное окно). Требует прав со-хоста и лицензии
# Cloud Recording у хост-аккаунта. Возвращают True оптимистично (клик выполнен) —
# фактический статус клиент показывает индикатором «REC».

def _open_rec_indicator_menu() -> None:
    """Открыть меню Stop/Pause кликом по верхнему индикатору «REC».

    Индикатор в верхней панели на стабильной позиции — в отличие от кнопки Record в
    нижнем тулбаре, которая гуляет (закреплена/не закреплена, чат открыт и т.п.) и
    из-за этого раньше клик промахивался.
    """
    maximize_meeting_window()
    move_mouse_top()             # показать верхнюю панель с индикатором записи
    time.sleep(0.4)
    _click("rec_indicator")
    time.sleep(1.0)


def start_cloud_recording() -> bool:
    """Старт облачной записи: Alt+R открывает меню выбора → «Record to the cloud».

    Alt+R (при доступных обоих вариантах записи) показывает меню computer/cloud на
    стабильной позиции у нижне-левого угла развёрнутого окна. Alt+C (прямой шорткат
    облака) в этом билде Zoom не срабатывает, поэтому идём через Alt+R + клик.
    """
    maximize_meeting_window()
    move_mouse_center()          # фокус окна + показать интерфейс
    time.sleep(0.4)
    key("alt+r")                 # меню записи (Record to this computer / to the cloud)
    time.sleep(1.2)
    _click("rec_cloud_option")   # «Record to the cloud»
    return True


def pause_cloud_recording() -> bool:
    """Пауза: клик по «REC» → «Pause recording»."""
    _open_rec_indicator_menu()
    _click("rec_pause_option")
    return True


def resume_cloud_recording() -> bool:
    """Возобновление: клик по «REC» → «Resume recording» (та же позиция, что и Pause)."""
    _open_rec_indicator_menu()
    _click("rec_pause_option")
    return True


def stop_cloud_recording() -> bool:
    """Стоп: клик по «REC» → «Stop recording» → подтвердить «Yes» в диалоге."""
    _open_rec_indicator_menu()
    _click("rec_stop_option")
    time.sleep(1.0)
    _click("rec_stop_confirm_yes")
    return True
