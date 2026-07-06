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


@app.get("/api/bot/status")
def bot_status():
    """Статус bot-worker (для панели живого просмотра)."""
    return bot_client.status() or {"status": "unavailable"}


@app.post("/api/bot/recording/start")
def bot_recording_start(target: str = "local"):
    return bot_client.recording_start(target) or {"error": "bot-worker недоступен"}


@app.post("/api/bot/recording/{action}")
def bot_recording_action(action: str):
    if action not in ("pause", "resume", "stop"):
        return JSONResponse({"error": "неизвестное действие"}, status_code=400)
    return bot_client.recording_action(action) or {"error": "bot-worker недоступен"}


@app.post("/api/bot/mute-all")
def bot_mute_all():
    return bot_client.mute_all() or {"error": "bot-worker недоступен"}


# ---- Музыка (проксирование в bot-worker) ----
MUSIC_MAX = 5 * 1024 * 1024  # 5 МБ


@app.get("/api/bot/music/status")
def bot_music_status():
    return bot_client.music_status() or {"error": "bot-worker недоступен"}


@app.post("/api/bot/music/volume")
def bot_music_volume(payload: dict = Body(...)):
    try:
        vol = int(payload.get("volume", 60))
    except (TypeError, ValueError):
        return JSONResponse({"error": "volume должен быть числом"}, status_code=400)
    return bot_client.music_volume(vol) or {"error": "bot-worker недоступен"}


@app.post("/api/bot/music/upload")
async def bot_music_upload(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".mp3"):
        return JSONResponse({"error": "только .mp3"}, status_code=400)
    data = await file.read()
    if len(data) > MUSIC_MAX:
        return JSONResponse({"error": "файл больше 5 МБ"}, status_code=413)
    j, code = bot_client.music_upload(file.filename, data)
    return JSONResponse(j or {"error": "bot-worker недоступен"}, status_code=code)


@app.delete("/api/bot/music/tracks/{name}")
def bot_music_delete(name: str):
    return bot_client.music_delete(name) or {"error": "bot-worker недоступен"}


@app.post("/api/bot/music/{action}")
def bot_music_action(action: str):
    if action not in ("start", "stop", "pause", "resume", "next", "prev"):
        return JSONResponse({"error": "неизвестное действие"}, status_code=400)
    return bot_client.music_action(action) or {"error": "bot-worker недоступен"}


@app.post("/api/bot/moderation/test")
def bot_moderation_test(payload: dict = Body(...)):
    sender = payload.get("sender") or "Тест"
    text_ = payload.get("text") or ""
    return bot_client.moderation_test(sender, text_) or {"error": "bot-worker недоступен"}


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
