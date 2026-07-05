import os
import subprocess
import tempfile

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app import gui
from app.chatreader import ChatMessage
from app.config import config
from app.moderation import moderator
from app.preview import preview
from app.recording import recording
from app.session import session

app = FastAPI(title="Zoom Bot Worker", version="0.2.0")


def _display_ok() -> bool:
    try:
        subprocess.run(
            ["xdpyinfo", "-display", config.display],
            check=True, capture_output=True, timeout=5,
        )
        return True
    except Exception:  # noqa: BLE001
        return False


class JoinRequest(BaseModel):
    join_url: str
    passcode: str | None = None
    host_key: str | None = None
    record: bool = False
    moderate: bool = False
    title: str | None = None


class ChatTestRequest(BaseModel):
    sender: str = "Тест"
    text: str


class RecordStartRequest(BaseModel):
    target: str = "local"       # "local" | "cloud"
    title: str | None = None


@app.get("/health")
def health():
    ok = _display_ok()
    return JSONResponse(
        {"service": "ok", "display": config.display, "xvfb": "ok" if ok else "down"},
        status_code=200 if ok else 503,
    )


@app.post("/session/join")
def join(req: JoinRequest):
    try:
        session.join(req.join_url, req.passcode, req.host_key, req.record,
                     req.title, req.moderate)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    return session.status()


@app.post("/session/leave")
def leave():
    session.leave()
    return session.status()


@app.get("/session/status")
def status():
    return session.status()


@app.post("/recording/start")
def recording_start(req: RecordStartRequest):
    return recording.start(req.target, req.title or "event")


@app.post("/recording/pause")
def recording_pause():
    return {"paused": recording.pause()}


@app.post("/recording/resume")
def recording_resume():
    return {"resumed": recording.resume()}


@app.post("/recording/stop")
def recording_stop():
    return recording.stop()


@app.get("/recording/status")
def recording_status():
    return recording.status()


@app.post("/moderation/mute-all")
def mute_all():
    return {"ok": gui.mute_all()}


@app.get("/moderation/status")
def moderation_status():
    return moderator.status()


@app.post("/moderation/test")
def moderation_test(req: ChatTestRequest):
    """Прогнать сообщение через правила модерации (для проверки без митинга)."""
    ev = moderator.process(ChatMessage(sender=req.sender, text=req.text))
    if ev is None:
        return {"detected": False}
    return {"detected": True, "event": ev.__dict__}


@app.get("/screenshot")
def screenshot():
    """Снимок текущего экрана (что видит бот)."""
    if not _display_ok():
        return JSONResponse({"error": "display not ready"}, status_code=503)
    path = tempfile.mktemp(suffix=".png")
    subprocess.run(
        ["scrot", "-o", path],
        env={**os.environ, "DISPLAY": config.display},
        check=True, timeout=10,
    )
    return FileResponse(path, media_type="image/png", filename="screen.png")


@app.websocket("/preview")
async def preview_ws(ws: WebSocket):
    """Живой поток экрана+звука (MPEG-TS для jsmpeg)."""
    await ws.accept()
    await preview.add(ws)
    try:
        while True:
            # Ждём закрытия соединения; данные шлём мы, от клиента ничего не нужно.
            await ws.receive_bytes()
    except Exception:  # noqa: BLE001
        pass
    finally:
        await preview.remove(ws)


@app.get("/recordings")
def recordings():
    os.makedirs(config.recordings_dir, exist_ok=True)
    files = sorted(os.listdir(config.recordings_dir))
    return {"dir": config.recordings_dir, "files": files}
