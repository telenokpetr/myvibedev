import os
import subprocess
import tempfile

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.config import config
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
        session.join(req.join_url, req.passcode, req.host_key, req.record, req.title)
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


@app.get("/recordings")
def recordings():
    os.makedirs(config.recordings_dir, exist_ok=True)
    files = sorted(os.listdir(config.recordings_dir))
    return {"dir": config.recordings_dir, "files": files}
