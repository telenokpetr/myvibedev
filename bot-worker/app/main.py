import logging
import os
import subprocess
import tempfile

# INFO-логи приложения (модерация, OCR, сессия) в stdout контейнера —
# без этого в docker logs видно только HTTP-доступ uvicorn'а.
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s: %(message)s")

from fastapi import FastAPI, File, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app import gui
from app.account import account
from app.chatreader import ChatMessage
from app.config import config
from app.moderation import moderator
from app.music import music
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


class VolumeRequest(BaseModel):
    volume: int


class SignInRequest(BaseModel):
    email: str
    password: str


class OtpRequest(BaseModel):
    code: str


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


# ---- Вход в Zoom-аккаунт (для веб-формы) ----

@app.get("/account/status")
def account_status():
    return account.status()


@app.post("/account/sign-in")
def account_sign_in(req: SignInRequest):
    return account.sign_in(req.email, req.password)


@app.post("/account/otp")
def account_otp(req: OtpRequest):
    return account.submit_otp(req.code)


# ---- Музыка (виртуальный микрофон): open-source плеер mpv ----

@app.get("/music/status")
def music_status():
    return music.status()


@app.post("/music/start")
def music_start():
    return music.start()


@app.post("/music/stop")
def music_stop():
    return music.stop()


@app.post("/music/pause")
def music_pause():
    return music.pause()


@app.post("/music/resume")
def music_resume():
    return music.resume()


@app.post("/music/next")
def music_next():
    return music.next()


@app.post("/music/prev")
def music_prev():
    return music.prev()


@app.post("/music/volume")
def music_volume(req: VolumeRequest):
    return music.set_volume(req.volume)


@app.post("/music/upload")
async def music_upload(file: UploadFile = File(...)):
    data = await file.read()
    try:
        return music.add_file(file.filename, data)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.delete("/music/tracks/{name}")
def music_delete(name: str):
    return music.delete_track(name)


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
