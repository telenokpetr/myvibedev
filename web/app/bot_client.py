"""HTTP-клиент к bot-worker. Все вызовы «безопасные»: при недоступности воркера
возвращают None/False, а не роняют планировщик."""

import logging

import httpx

from app.config import settings

log = logging.getLogger("bot_client")
BASE = settings.bot_worker_url.rstrip("/")


def join(event) -> str:
    """Отправить воркеру команду войти. Возвращает: 'ok' | 'busy' | 'error'."""
    payload = {
        "join_url": event.join_url,
        "passcode": event.passcode,
        "host_key": event.host_key,
        "record": event.record,
        "moderate": event.moderate,
        "title": event.title,
    }
    try:
        r = httpx.post(f"{BASE}/session/join", json=payload, timeout=30)
        if r.status_code == 409:
            return "busy"
        r.raise_for_status()
        return "ok"
    except Exception as exc:  # noqa: BLE001
        log.warning("join failed: %s", exc)
        return "error"


def leave() -> bool:
    try:
        httpx.post(f"{BASE}/session/leave", timeout=30).raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("leave failed: %s", exc)
        return False


def status() -> dict | None:
    try:
        return httpx.get(f"{BASE}/session/status", timeout=10).json()
    except Exception:  # noqa: BLE001
        return None


def recording_start(target: str) -> dict | None:
    try:
        r = httpx.post(f"{BASE}/recording/start", json={"target": target}, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("recording_start failed: %s", exc)
        return None


def recording_action(action: str) -> dict | None:
    """action: pause | resume | stop."""
    try:
        r = httpx.post(f"{BASE}/recording/{action}", timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("recording_%s failed: %s", action, exc)
        return None


def mute_all() -> dict | None:
    try:
        r = httpx.post(f"{BASE}/moderation/mute-all", timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("mute_all failed: %s", exc)
        return None


def moderation_test(sender: str, text: str) -> dict | None:
    try:
        r = httpx.post(f"{BASE}/moderation/test",
                       json={"sender": sender, "text": text}, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("moderation_test failed: %s", exc)
        return None


# ---- Музыка (виртуальный микрофон) ----

def music_status() -> dict | None:
    try:
        r = httpx.get(f"{BASE}/music/status", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:  # noqa: BLE001
        return None


def music_action(action: str) -> dict | None:
    """action: start | stop | pause | resume | next | prev."""
    try:
        r = httpx.post(f"{BASE}/music/{action}", timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("music_%s failed: %s", action, exc)
        return None


def music_volume(volume: int) -> dict | None:
    try:
        r = httpx.post(f"{BASE}/music/volume", json={"volume": volume}, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("music_volume failed: %s", exc)
        return None


def music_upload(filename: str, data: bytes) -> tuple[dict | None, int]:
    """Загрузить трек в bot-worker. Возвращает (json, http_status)."""
    try:
        r = httpx.post(f"{BASE}/music/upload",
                       files={"file": (filename, data, "audio/mpeg")}, timeout=30)
        return r.json(), r.status_code
    except Exception as exc:  # noqa: BLE001
        log.warning("music_upload failed: %s", exc)
        return None, 502


def music_delete(name: str) -> dict | None:
    try:
        r = httpx.delete(f"{BASE}/music/tracks/{name}", timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("music_delete failed: %s", exc)
        return None
