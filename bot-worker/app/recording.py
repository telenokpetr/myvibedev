"""Контроллер записи: локально (ffmpeg) или в облако Zoom (host-контролы клиента)."""

from app import gui
from app.recorder import recorder


class RecordingController:
    def __init__(self) -> None:
        self.target: str | None = None      # "local" | "cloud" | None
        self._cloud_active = False
        self._cloud_paused = False

    # ---- управление ----

    def start(self, target: str, name_hint: str = "event") -> dict:
        self.target = target
        if target == "cloud":
            ok = gui.start_cloud_recording()
            self._cloud_active = ok
            self._cloud_paused = False
            return {"ok": ok, "target": "cloud"}
        path = recorder.start(name_hint)
        return {"ok": True, "target": "local", "path": path}

    def pause(self) -> bool:
        if self.target == "cloud":
            self._cloud_paused = gui.pause_cloud_recording()
            return self._cloud_paused
        return recorder.pause()

    def resume(self) -> bool:
        if self.target == "cloud":
            ok = gui.resume_cloud_recording()
            self._cloud_paused = not ok
            return ok
        return recorder.resume()

    def stop(self) -> dict:
        if self.target == "cloud":
            ok = gui.stop_cloud_recording()
            self._cloud_active = False
            self._cloud_paused = False
            self.target = None
            return {"ok": ok, "target": "cloud"}
        path = recorder.stop()
        self.target = None
        return {"ok": True, "target": "local", "path": path}

    # ---- статус ----

    def status(self) -> dict:
        if self.target == "cloud":
            active, paused, path = self._cloud_active, self._cloud_paused, None
        else:
            active, paused, path = recorder.active, recorder.paused, recorder.path
        return {
            "active": active,
            "paused": paused,
            "target": self.target,
            "path": path,
        }


recording = RecordingController()
