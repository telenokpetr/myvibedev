from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from app import models  # noqa: F401  (регистрирует таблицы в metadata)
from app import scheduler
from app.config import settings
from app.database import Base, engine
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
