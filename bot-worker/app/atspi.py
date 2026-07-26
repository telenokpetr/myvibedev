"""Навигация по дереву доступности (AT-SPI) для управления Zoom-клиентом.

Почему AT-SPI, а не клики по координатам: координаты зависят от версии клиента,
локали и раскладки окна и ломаются на каждом обновлении. Здесь мы ищем элементы
UI по их **доступному имени** (подпись кнопки/пункта меню) и роли, а кликаем по
центру найденного элемента (через action-интерфейс либо xdotool по его extents).

Подписи Zoom отличаются между версиями/языками, поэтому все варианты вынесены в
`LABELS` — их можно расширять по скриншотам живого клиента, не трогая логику.

Ядро (label_matches / find_descendant / find / find_by_name) — чистые функции над
любым объектом-узлом с атрибутами .name/.role/.children, поэтому тестируется
офлайн на фейковом дереве, без реального pyatspi и Zoom.
"""

import logging
import subprocess

from app.config import config

log = logging.getLogger("atspi")

ENV_DISPLAY = config.display

# Роли кликабельных управляющих элементов (getRoleName из AT-SPI).
BUTTON_ROLES = {
    "push button", "toggle button", "menu item",
    "check menu item", "radio menu item",
}

# Кандидаты подписей (нижний регистр, EN + RU). Матчинг — по вхождению подстроки,
# поэтому "Participants (5)" поймается по "participants".
LABELS: dict[str, list[str]] = {
    "participants": ["participants", "участники", "manage participants"],
    "more": ["more", "ещё", "еще", "more options", "больше"],
    "claim_host": ["claim host", "claim to be host", "стать организатором",
                   "заявить права организатора", "reclaim host"],
    "mute_all": ["mute all", "выключить звук у всех", "отключить звук у всех"],
    "mute": ["mute", "выключить звук", "отключить звук"],
    "delete": ["delete", "удалить"],
    "chat": ["chat", "чат"],
    "confirm": ["yes", "да", "continue", "продолжить", "ok", "claim", "confirm",
                "delete", "удалить", "стать организатором"],
    "record": ["record to the cloud", "record", "запись в облако", "запись",
               "начать запись"],
    "pause_record": ["pause recording", "приостановить запись", "пауза записи"],
    "resume_record": ["resume recording", "возобновить запись"],
    "stop_record": ["stop recording", "остановить запись", "завершить запись"],
}


# ---------------------------------------------------------------- чистое ядро ---

def label_matches(text: str | None, candidates: list[str]) -> bool:
    """True, если подпись элемента содержит любой из кандидатов (case-insensitive)."""
    if not text:
        return False
    t = text.strip().lower()
    return any(c in t for c in candidates)


def find_descendant(node, predicate):
    """DFS по дереву узлов (включая корень). Возвращает первый узел под predicate."""
    if node is None:
        return None
    try:
        if predicate(node):
            return node
    except Exception:  # noqa: BLE001
        pass
    for child in getattr(node, "children", []) or []:
        found = find_descendant(child, predicate)
        if found is not None:
            return found
    return None


def find(node, key: str, exclude_key: str | None = None, roles=BUTTON_ROLES):
    """Найти кликабельный элемент по ключу из LABELS.

    exclude_key — исключить совпадения по другому набору (напр. найти «Mute», но
    не «Mute All»).
    """
    cands = LABELS[key]
    excl = LABELS.get(exclude_key, []) if exclude_key else []

    def pred(n):
        if roles and n.role not in roles:
            return False
        if not label_matches(n.name, cands):
            return False
        if excl and label_matches(n.name, excl):
            return False
        return True

    return find_descendant(node, pred)


def find_by_name(node, literal: str, roles=None):
    """Найти элемент, чьё имя содержит произвольную строку (участник, текст чата)."""
    needle = (literal or "").strip().lower()
    if not needle:
        return None

    def pred(n):
        if roles and n.role not in roles:
            return False
        name = (n.name or "").strip().lower()
        return needle in name

    return find_descendant(node, pred)


# --------------------------------------------------------- действия над узлами ---

def click(node) -> bool:
    """Кликнуть: сначала через action-интерфейс, затем — по центру элемента."""
    if node is None:
        return False
    if node.press():
        return True
    center = node.center()
    if center:
        _xdotool("mousemove", str(center[0]), str(center[1]), "click", "1")
        return True
    return False


def hover(node) -> bool:
    """Навести мышь на центр элемента (в Zoom так всплывают hover-контролы)."""
    if node is None:
        return False
    center = node.center()
    if center:
        _xdotool("mousemove", str(center[0]), str(center[1]))
        return True
    return False


def _xdotool(*args: str) -> None:
    try:
        subprocess.run(
            ["xdotool", *args],
            env={"DISPLAY": ENV_DISPLAY},
            capture_output=True, timeout=10, check=False,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("xdotool %s failed: %s", args, exc)


# --------------------------------------------------------- адаптер к pyatspi ---

def available() -> bool:
    try:
        import pyatspi  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


class _Node:
    """Обёртка над pyatspi-accessible под протокол name/role/children/press/center."""

    def __init__(self, acc) -> None:
        self._acc = acc

    @property
    def name(self) -> str:
        try:
            return self._acc.name or ""
        except Exception:  # noqa: BLE001
            return ""

    @property
    def role(self) -> str:
        try:
            return (self._acc.getRoleName() or "").lower()
        except Exception:  # noqa: BLE001
            return ""

    @property
    def children(self) -> list["_Node"]:
        out: list[_Node] = []
        try:
            for i in range(self._acc.childCount):
                c = self._acc.getChildAtIndex(i)
                if c is not None:
                    out.append(_Node(c))
        except Exception:  # noqa: BLE001
            pass
        return out

    def press(self) -> bool:
        try:
            action = self._acc.queryAction()
            for i in range(action.nActions):
                if action.getName(i).lower() in ("click", "press", "activate", "jump"):
                    action.doAction(i)
                    return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def center(self) -> tuple[int, int] | None:
        try:
            import pyatspi
            comp = self._acc.queryComponent()
            ext = comp.getExtents(pyatspi.DESKTOP_COORDS)
            if ext.width <= 0 or ext.height <= 0:
                return None
            return ext.x + ext.width // 2, ext.y + ext.height // 2
        except Exception:  # noqa: BLE001
            return None


def zoom_frames() -> list["_Node"]:
    """Окна приложения Zoom из реестра доступности."""
    if not available():
        return []
    frames: list[_Node] = []
    try:
        import pyatspi
        desktop = pyatspi.Registry.getDesktop(0)
        for i in range(desktop.childCount):
            app = desktop.getChildAtIndex(i)
            if app is None:
                continue
            if "zoom" not in (app.name or "").lower():
                continue
            for j in range(app.childCount):
                w = app.getChildAtIndex(j)
                if w is not None:
                    frames.append(_Node(w))
    except Exception as exc:  # noqa: BLE001
        log.debug("zoom_frames failed: %s", exc)
    return frames


def meeting_frame() -> "_Node | None":
    """Окно конференции — эвристически последнее окно Zoom (как в activate_meeting_window)."""
    frames = zoom_frames()
    return frames[-1] if frames else None
