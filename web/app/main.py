import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import websockets
from fastapi import Body, FastAPI, File, Request, UploadFile, WebSocket
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, text

from app import bot_client
from app import models  # noqa: F401  (регистрирует таблицы в metadata)
from app import scheduler
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.routers import events

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Создаём таблицы при старте (для прод — заменим на Alembic-миграции).
    Base.metadata.create_all(bind=engine)
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="Zoom Bot Console", version="0.1.0", lifespan=lifespan)

app.include_router(events.router)

static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/health")
def health():
    """Проверка живости сервиса и зависимостей."""
    status = {"service": "ok", "db": "unknown", "redis": "unknown"}

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        status["db"] = "ok"
    except Exception as exc:  # noqa: BLE001
        status["db"] = f"error: {exc.__class__.__name__}"

    try:
        import redis

        r = redis.from_url(settings.redis_url, socket_connect_timeout=2)
        r.ping()
        status["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        status["redis"] = f"error: {exc.__class__.__name__}"

    healthy = status["db"] == "ok" and status["redis"] == "ok"
    return JSONResponse(status, status_code=200 if healthy else 503)


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# ---- Проксирование команд к воркеру по slot (0, 1, …) ----
# slot — какой параллельный вебинар/воркер. По умолчанию 0 (существующий UI).
UNAVAILABLE = {"error": "bot-worker недоступен"}


def _worker(slot: int):
    return bot_client.get_worker(slot)


@app.get("/api/bot/workers")
def bot_workers():
    """Сколько воркеров (слотов) доступно — для вкладок в UI."""
    return {"count": bot_client.worker_count()}


@app.get("/api/bot/status")
def bot_status(slot: int = 0):
    """Статус воркера (для панели живого просмотра)."""
    w = _worker(slot)
    return (w.status() if w else None) or {"status": "unavailable"}


@app.post("/api/bot/recording/start")
def bot_recording_start(target: str = "local", slot: int = 0):
    w = _worker(slot)
    return (w.recording_start(target) if w else None) or UNAVAILABLE


@app.post("/api/bot/recording/{action}")
def bot_recording_action(action: str, slot: int = 0):
    if action not in ("pause", "resume", "stop"):
        return JSONResponse({"error": "неизвестное действие"}, status_code=400)
    w = _worker(slot)
    return (w.recording_action(action) if w else None) or UNAVAILABLE


@app.post("/api/bot/mute-all")
def bot_mute_all(slot: int = 0):
    w = _worker(slot)
    return (w.mute_all() if w else None) or UNAVAILABLE


# ---- Вход в Zoom-аккаунт ----

@app.get("/api/bot/account/status")
def bot_account_status(slot: int = 0):
    w = _worker(slot)
    return (w.account_status() if w else None) or UNAVAILABLE


@app.post("/api/bot/account/sign-in")
def bot_account_sign_in(payload: dict = Body(...), slot: int = 0):
    email = (payload.get("email") or "").strip()
    password = payload.get("password") or ""
    if not email or not password:
        return JSONResponse({"error": "нужны email и пароль"}, status_code=400)
    w = _worker(slot)
    return (w.account_sign_in(email, password) if w else None) or UNAVAILABLE


@app.post("/api/bot/account/otp")
def bot_account_otp(payload: dict = Body(...), slot: int = 0):
    code = (payload.get("code") or "").strip()
    if not code:
        return JSONResponse({"error": "нужен код OTP"}, status_code=400)
    w = _worker(slot)
    return (w.account_otp(code) if w else None) or UNAVAILABLE


# ---- Музыка ----
MUSIC_MAX = 5 * 1024 * 1024  # 5 МБ


@app.get("/api/bot/music/status")
def bot_music_status(slot: int = 0):
    w = _worker(slot)
    return (w.music_status() if w else None) or UNAVAILABLE


@app.post("/api/bot/music/volume")
def bot_music_volume(payload: dict = Body(...), slot: int = 0):
    try:
        vol = int(payload.get("volume", 60))
    except (TypeError, ValueError):
        return JSONResponse({"error": "volume должен быть числом"}, status_code=400)
    w = _worker(slot)
    return (w.music_volume(vol) if w else None) or UNAVAILABLE


@app.post("/api/bot/music/upload")
async def bot_music_upload(file: UploadFile = File(...), slot: int = 0):
    if not (file.filename or "").lower().endswith(".mp3"):
        return JSONResponse({"error": "только .mp3"}, status_code=400)
    data = await file.read()
    if len(data) > MUSIC_MAX:
        return JSONResponse({"error": "файл больше 5 МБ"}, status_code=413)
    w = _worker(slot)
    if not w:
        return JSONResponse(UNAVAILABLE, status_code=502)
    j, code = w.music_upload(file.filename, data)
    return JSONResponse(j or UNAVAILABLE, status_code=code)


@app.delete("/api/bot/music/tracks/{name}")
def bot_music_delete(name: str, slot: int = 0):
    w = _worker(slot)
    return (w.music_delete(name) if w else None) or UNAVAILABLE


@app.post("/api/bot/music/{action}")
def bot_music_action(action: str, slot: int = 0):
    if action not in ("start", "stop", "pause", "resume", "next", "prev"):
        return JSONResponse({"error": "неизвестное действие"}, status_code=400)
    w = _worker(slot)
    return (w.music_action(action) if w else None) or UNAVAILABLE


@app.post("/api/bot/moderation/test")
def bot_moderation_test(payload: dict = Body(...), slot: int = 0):
    sender = payload.get("sender") or "Тест"
    text_ = payload.get("text") or ""
    w = _worker(slot)
    return (w.moderation_test(sender, text_) if w else None) or UNAVAILABLE


@app.post("/api/internal/moderation")
def internal_moderation(payload: dict = Body(...)):
    """Приём событий модерации от bot-worker (по внутреннему токену)."""
    if payload.get("token") != settings.internal_api_token:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    db = SessionLocal()
    try:
        ev = models.ModerationEvent(
            sender=payload.get("sender", "?"),
            text=payload.get("text", ""),
            category=payload.get("category", "spam"),
            reason=payload.get("reason"),
            action=payload.get("action", "flagged"),
        )
        db.add(ev)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@app.get("/api/moderation/events")
def moderation_events(limit: int = 50):
    db = SessionLocal()
    try:
        stmt = (
            select(models.ModerationEvent)
            .order_by(models.ModerationEvent.created_at.desc())
            .limit(min(limit, 200))
        )
        rows = list(db.scalars(stmt))
        return [
            {
                "id": r.id, "sender": r.sender, "text": r.text,
                "category": r.category, "reason": r.reason,
                "action": r.action, "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    finally:
        db.close()


@app.websocket("/api/preview")
async def preview_proxy(ws: WebSocket):
    """Проксируем A/V-поток bot-worker в браузер (единый origin)."""
    await ws.accept()
    upstream = (
        settings.bot_worker_url.replace("http://", "ws://").replace("https://", "wss://")
        + "/preview"
    )
    try:
        async with websockets.connect(upstream, max_size=None) as up:
            while True:
                data = await up.recv()
                if isinstance(data, str):
                    data = data.encode()
                await ws.send_bytes(data)
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
