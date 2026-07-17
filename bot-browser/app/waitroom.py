"""Модерация зала ожидания: впускать по списку разрешённых имён/фамилий.

Правила (согласованы с пользователем):
- Список ведётся в веб-панели, хранится здесь в /data/allowlist.json, переживает
  рестарт (том botshots).
- Совпадение ПО ЛЮБОМУ СЛОВУ: имя ожидающего впускается, если хоть одно его слово
  (имя ИЛИ фамилия) есть в списке.
- Нет совпадения и имя полное (2+ слова) → оставляем в зале, решает человек.
- Одно слово (фамилии нет) → впускаем условно, просим в чате указать фамилию;
  не ответил за ASK_TIMEOUT — возвращаем в зал ожидания.

Само чтение зала / впуск / возврат — в zoomweb (DOM Zoom). Здесь только данные,
матчинг и состояние «ждём фамилию».
"""

import json
import logging
import os
import re
import time

log = logging.getLogger("waitroom")

STORE = os.environ.get("ALLOWLIST_FILE", "/data/allowlist.json")
ASK_TIMEOUT = float(os.environ.get("WAIT_ASK_TIMEOUT", "90"))  # сек на ответ с фамилией

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)   # слова из букв (без цифр/пунктуации)


def _words(name: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(name or "")}


class Gatekeeper:
    def __init__(self) -> None:
        self.enabled = False            # включена ли авто-модерация входа
        self.names: list[str] = []      # разрешённые имена/фамилии (как ввёл человек)
        self._allowed_words: set[str] = set()
        # Мат в НИКЕ — тем же фильтром, что и чат (см. moderation.py).
        from app.moderation import ProfanityFilter
        self._profanity = ProfanityFilter()
        # админ провизорно впущенных без фамилии: имя → время впуска
        self.pending: dict[str, float] = {}
        self._rejected: set[str] = set()   # ники, уже помеченные как неприемлемые
        # лог решений для панели: последние N
        self.log: list[dict] = []
        self._load()

    # ---- хранилище ----
    def _load(self) -> None:
        try:
            with open(STORE, encoding="utf-8") as f:
                data = json.load(f)
            self.enabled = bool(data.get("enabled", False))
            self.names = [str(n).strip() for n in data.get("names", []) if str(n).strip()]
        except FileNotFoundError:
            pass
        except Exception as exc:  # noqa: BLE001
            log.warning("allowlist не прочитан: %s", exc)
        self._reindex()

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(STORE), exist_ok=True)
            tmp = STORE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"enabled": self.enabled, "names": self.names}, f,
                          ensure_ascii=False, indent=2)
            os.replace(tmp, STORE)
        except Exception as exc:  # noqa: BLE001
            log.warning("allowlist не сохранён: %s", exc)

    def _reindex(self) -> None:
        self._allowed_words = set()
        for n in self.names:
            self._allowed_words |= _words(n)

    # ---- API для панели ----
    def state(self) -> dict:
        return {"enabled": self.enabled, "names": self.names,
                "pending": list(self.pending), "rejected": list(self._rejected),
                "log": self.log[-30:]}

    def set_enabled(self, on: bool) -> dict:
        self.enabled = bool(on)
        self._save()
        return self.state()

    def add(self, name: str) -> dict:
        name = (name or "").strip()
        if name and name.lower() not in {n.lower() for n in self.names}:
            self.names.append(name)
            self._reindex()
            self._save()
        return self.state()

    def remove(self, name: str) -> dict:
        self.names = [n for n in self.names if n.lower() != (name or "").strip().lower()]
        self._reindex()
        self._save()
        return self.state()

    # ---- логика ----
    def has_surname(self, name: str) -> bool:
        """Фамилия есть, если в имени 2+ слова из букв."""
        return len(_WORD.findall(name or "")) >= 2

    def matches(self, name: str) -> bool:
        """Совпадение по любому слову (имя ИЛИ фамилия)."""
        return bool(_words(name) & self._allowed_words)

    def is_bad_nick(self, name: str) -> bool:
        """Неприемлемый ник (мат/оскорбление в имени участника)."""
        return self._profanity.check(name or "")

    def note(self, name: str, action: str, reason: str) -> None:
        self.log.append({"ts": time.time(), "name": name,
                         "action": action, "reason": reason})
        self.log = self.log[-100:]
        log.info("gatekeeper: %s → %s (%s)", name, action, reason)

    def decide(self, name: str) -> str:
        """Что делать с ОЖИДАЮЩИМ. Возвращает:
        reject | admit | ask_surname | leave.
        (reject = неприемлемый ник, НЕ впускаем; ask_surname = впустить условно
        и спросить фамилию; leave = решает человек вручную)."""
        if not self.enabled:
            return "leave"
        # Неприемлемый ник — не впускаем ни при каких условиях (даже если слово
        # из списка): мат в имени важнее совпадения.
        if self.is_bad_nick(name):
            return "reject"
        if not self.has_surname(name):
            return "ask_surname"
        if self.matches(name):
            return "admit"
        return "leave"


gatekeeper = Gatekeeper()
