import os
import subprocess
import tempfile

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

DISPLAY = os.environ.get("DISPLAY", ":99")

app = FastAPI(title="Zoom Bot Worker", version="0.1.0")


def _display_ok() -> bool:
    try:
        subprocess.run(
            ["xdpyinfo", "-display", DISPLAY],
            check=True,
            capture_output=True,
            timeout=5,
        )
        return True
    except Exception:  # noqa: BLE001
        return False


@app.get("/health")
def health():
    ok = _display_ok()
    return JSONResponse(
        {"service": "ok", "display": DISPLAY, "xvfb": "ok" if ok else "down"},
        status_code=200 if ok else 503,
    )


@app.get("/screenshot")
def screenshot():
    """Снимок текущего виртуального экрана (этап 1 — проверка захвата)."""
    if not _display_ok():
        return JSONResponse({"error": "display not ready"}, status_code=503)

    path = tempfile.mktemp(suffix=".png")
    subprocess.run(
        ["scrot", "-o", path],
        env={**os.environ, "DISPLAY": DISPLAY},
        check=True,
        timeout=10,
    )
    return FileResponse(path, media_type="image/png", filename="screen.png")
