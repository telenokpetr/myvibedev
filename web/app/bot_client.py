"""HTTP-клиенты к bot-worker'ам. Каждый воркер = отдельный Zoom-клиент (свой
параллельный вебинар). Все вызовы «безопасные»: при недоступности воркера
возвращают None/False, а не роняют планировщик.

WORKERS — реестр по слотам (0, 1, …) из settings.worker_urls. Модульные функции
join/leave/status работают с воркером 0 (для планировщика/legacy).
"""

import logging

import httpx

from app.config import settings

log = logging.getLogger("bot_client")


class WorkerClient:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    # ---- сессия ----
    def join(self, event) -> str:
        """'ok' | 'busy' | 'error'."""
        payload = {
            "join_url": event.join_url, "passcode": event.passcode,
            "host_key": event.host_key, "record": event.record,
            "moderate": event.moderate, "title": event.title,
        }
        try:
            r = httpx.post(f"{self.base}/session/join", json=payload, timeout=30)
            if r.status_code == 409:
                return "busy"
            r.raise_for_status()
            return "ok"
        except Exception as exc:  # noqa: BLE001
            log.warning("join failed (%s): %s", self.base, exc)
            return "error"

    def leave(self) -> bool:
        try:
            httpx.post(f"{self.base}/session/leave", timeout=30).raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("leave failed (%s): %s", self.base, exc)
            return False

    def status(self) -> dict | None:
        try:
            return httpx.get(f"{self.base}/session/status", timeout=10).json()
        except Exception:  # noqa: BLE001
            return None

    # ---- запись ----
    def recording_start(self, target: str) -> dict | None:
        try:
            r = httpx.post(f"{self.base}/recording/start", json={"target": target}, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("recording_start failed: %s", exc)
            return None

    def recording_action(self, action: str) -> dict | None:
        try:
            r = httpx.post(f"{self.base}/recording/{action}", timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("recording_%s failed: %s", action, exc)
            return None

    # ---- модерация ----
    def mute_all(self) -> dict | None:
        try:
            r = httpx.post(f"{self.base}/moderation/mute-all", timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("mute_all failed: %s", exc)
            return None

    def moderation_test(self, sender: str, text: str) -> dict | None:
        try:
            r = httpx.post(f"{self.base}/moderation/test",
                           json={"sender": sender, "text": text}, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("moderation_test failed: %s", exc)
            return None

    # ---- музыка ----
    def music_status(self) -> dict | None:
        try:
            r = httpx.get(f"{self.base}/music/status", timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception:  # noqa: BLE001
            return None

    def music_action(self, action: str) -> dict | None:
        try:
            r = httpx.post(f"{self.base}/music/{action}", timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("music_%s failed: %s", action, exc)
            return None

    def music_volume(self, volume: int) -> dict | None:
        try:
            r = httpx.post(f"{self.base}/music/volume", json={"volume": volume}, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("music_volume failed: %s", exc)
            return None

    def music_upload(self, filename: str, data: bytes) -> tuple[dict | None, int]:
        try:
            r = httpx.post(f"{self.base}/music/upload",
                           files={"file": (filename, data, "audio/mpeg")}, timeout=30)
            return r.json(), r.status_code
        except Exception as exc:  # noqa: BLE001
            log.warning("music_upload failed: %s", exc)
            return None, 502

    def music_delete(self, name: str) -> dict | None:
        try:
            r = httpx.delete(f"{self.base}/music/tracks/{name}", timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("music_delete failed: %s", exc)
            return None

    # ---- видео в камеру бота ----
    def video_list(self) -> dict | None:
        try:
            r = httpx.get(f"{self.base}/video/list", timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception:  # noqa: BLE001
            return None

    def video_status(self) -> dict | None:
        try:
            r = httpx.get(f"{self.base}/video/status", timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception:  # noqa: BLE001
            return None

    def video_upload(self, filename: str, data: bytes) -> tuple[dict | None, int]:
        try:
            # Сырым телом, имя в query: боту не нужен python-multipart, а 100 МБ
            # не парсятся как форма. Таймаут щедрый — файл большой.
            r = httpx.post(f"{self.base}/video/upload", params={"name": filename},
                           content=data, timeout=180,
                           headers={"Content-Type": "application/octet-stream"})
            return r.json(), r.status_code
        except Exception as exc:  # noqa: BLE001
            log.warning("video_upload failed: %s", exc)
            return None, 502

    def video_play(self, name: str) -> tuple[dict | None, int]:
        try:
            r = httpx.post(f"{self.base}/video/play", json={"name": name}, timeout=20)
            return r.json(), r.status_code
        except Exception as exc:  # noqa: BLE001
            log.warning("video_play failed: %s", exc)
            return None, 502

    def video_action(self, action: str) -> dict | None:
        try:
            r = httpx.post(f"{self.base}/video/{action}", timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("video_%s failed: %s", action, exc)
            return None

    def video_volume(self, volume: int) -> dict | None:
        try:
            r = httpx.post(f"{self.base}/video/volume", json={"volume": volume}, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("video_volume failed: %s", exc)
            return None

    def video_delete(self, name: str) -> dict | None:
        try:
            r = httpx.delete(f"{self.base}/video/tracks/{name}", timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("video_delete failed: %s", exc)
            return None

    # ---- зал ожидания (впуск по списку) ----
    def waitroom_status(self) -> dict | None:
        try:
            r = httpx.get(f"{self.base}/waitroom/status", timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception:  # noqa: BLE001
            return None

    def waitroom_enabled(self, on: bool) -> dict | None:
        try:
            r = httpx.post(f"{self.base}/waitroom/enabled", json={"on": on}, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("waitroom_enabled failed: %s", exc)
            return None

    def waitroom_add(self, name: str) -> dict | None:
        try:
            r = httpx.post(f"{self.base}/waitroom/names", json={"name": name}, timeout=10)
            return r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("waitroom_add failed: %s", exc)
            return None

    def waitroom_remove(self, name: str) -> dict | None:
        try:
            r = httpx.delete(f"{self.base}/waitroom/names/{name}", timeout=10)
            return r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("waitroom_remove failed: %s", exc)
            return None

    def waitroom_admit(self, name: str) -> dict | None:
        try:
            r = httpx.post(f"{self.base}/waitroom/admit", json={"name": name}, timeout=20)
            return r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("waitroom_admit failed: %s", exc)
            return None

    def waitroom_deny(self, name: str) -> dict | None:
        try:
            r = httpx.post(f"{self.base}/waitroom/deny", json={"name": name}, timeout=20)
            return r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("waitroom_deny failed: %s", exc)
            return None

    # ---- аккаунт ----
    def account_status(self) -> dict | None:
        try:
            r = httpx.get(f"{self.base}/account/status", timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception:  # noqa: BLE001
            return None

    def account_sign_in(self, email: str, password: str) -> dict | None:
        try:
            r = httpx.post(f"{self.base}/account/sign-in",
                           json={"email": email, "password": password}, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("account_sign_in failed: %s", exc)
            return None

    def account_otp(self, code: str) -> dict | None:
        try:
            r = httpx.post(f"{self.base}/account/otp", json={"code": code}, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("account_otp failed: %s", exc)
            return None


# Реестр воркеров по слотам (0, 1, …).
WORKERS: list[WorkerClient] = [WorkerClient(u) for u in settings.worker_urls]


def worker_count() -> int:
    return len(WORKERS)


def get_worker(slot: int) -> WorkerClient | None:
    return WORKERS[slot] if 0 <= slot < len(WORKERS) else None


# --- Модульные делегаты для планировщика/legacy (воркер 0) ---

def join(event) -> str:
    return WORKERS[0].join(event)


def leave() -> bool:
    return WORKERS[0].leave()


def status() -> dict | None:
    return WORKERS[0].status()
