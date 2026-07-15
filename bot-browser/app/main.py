import logging
import os

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s: %(message)s")

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.account import account
from app.config import config
from app.moderation import ChatMessage, ProfanityFilter, SpamDetector, classify
from app.moderator import moderator
from app.recorder import recorder

app = FastAPI(title="Zoom Browser Bot", version="0.1.0")


class JoinRequest(BaseModel):
    join_url: str
    moderate: bool = True


class ChatTestRequest(BaseModel):
    sender: str = "Тест"
    text: str


@app.get("/health")
def health():
    return {"service": "ok"}


@app.post("/session/join")
def join(req: JoinRequest):
    try:
        moderator.join(req.join_url)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    return moderator.state()


@app.post("/session/leave")
def leave():
    moderator.leave()
    return moderator.state()


# ---- вход в Zoom-аккаунт (управляется веб-панелью «Аккаунт бота») ----

class SignInRequest(BaseModel):
    email: str
    password: str


class OtpRequest(BaseModel):
    code: str


@app.get("/account/status")
def account_status():
    return account.status()


@app.post("/account/sign-in")
def account_sign_in(req: SignInRequest):
    if not req.email or not req.password:
        return JSONResponse({"error": "нужны email и пароль"}, status_code=400)
    return account.sign_in(req.email.strip(), req.password)


@app.post("/account/otp")
def account_otp(req: OtpRequest):
    if not req.code:
        return JSONResponse({"error": "нужен код OTP"}, status_code=400)
    return account.otp(req.code.strip())


@app.post("/account/import-cookies")
def account_import_cookies():
    """Импорт готовой Zoom-сессии из /data/zoom-cookies.json (обход reCAPTCHA:
    человек вошёл сам в своём браузере, бот берёт его cookie)."""
    return account.import_cookies()


@app.get("/session/status")
def status():
    return moderator.state()


@app.get("/moderation/status")
def moderation_status():
    return moderator.mod_status()


@app.post("/moderation/test")
def moderation_test(req: ChatTestRequest):
    """Прогнать текст через движок без митинга (проверка правил)."""
    msg = ChatMessage(sender=req.sender, text=req.text)
    res = classify(msg.text, msg, ProfanityFilter(), SpamDetector())
    if res is None:
        return {"detected": False}
    return {"detected": True, "category": res[0], "reason": res[1]}


class RecordRequest(BaseModel):
    target: str = "cloud"    # cloud|local


@app.post("/recording/start")
def recording_start(req: RecordRequest):
    # local — ffmpeg-запись экрана бота (любой Zoom-план); cloud — облачная
    # запись Zoom через host-меню (нужен платный/лицензированный аккаунт).
    if req.target == "cloud":
        moderator.command("rec_start", "cloud")
        return {"mode": "zoom-cloud", "queued": True}
    return {"mode": "screen", **recorder.start()}


@app.post("/recording/pause")
def recording_pause(req: RecordRequest = RecordRequest()):
    if req.target == "cloud":
        moderator.command("rec_pause")
        return {"mode": "zoom-cloud", "queued": True}
    return {"mode": "screen", **recorder.pause()}


@app.post("/recording/resume")
def recording_resume(req: RecordRequest = RecordRequest()):
    if req.target == "cloud":
        moderator.command("rec_resume")
        return {"mode": "zoom-cloud", "queued": True}
    return {"mode": "screen", **recorder.resume()}


@app.post("/recording/stop")
def recording_stop(req: RecordRequest = RecordRequest()):
    if req.target == "cloud":
        moderator.command("rec_stop")
        return {"mode": "zoom-cloud", "queued": True}
    return {"mode": "screen", **recorder.stop()}


@app.get("/recording/status")
def recording_status():
    return {"screen": recorder.status(), "zoom_cloud": moderator.recording}


@app.get("/recordings")
def recordings():
    from app.recorder import REC_DIR
    os.makedirs(REC_DIR, exist_ok=True)
    return {"dir": REC_DIR, "files": sorted(os.listdir(REC_DIR))}


@app.post("/debug/dump_participants")
def debug_dump_participants():
    """DEBUG: дамп структуры панели участников в логи (для выверки селектора)."""
    moderator.command("dump_participants")
    return {"queued": True}


class MuteRequest(BaseModel):
    name: str


@app.post("/debug/mute")
def debug_mute(req: MuteRequest):
    """DEBUG: замьютить участника по имени напрямую (тест мьюта без ожидания
    мата от него)."""
    moderator.command("mute", req.name)
    return {"queued": True, "name": req.name}


class EvalRequest(BaseModel):
    js: str


@app.post("/debug/eval")
def debug_eval(req: EvalRequest):
    """DEBUG: выполнить JS на живой странице бота, вернуть результат (DOM-разведка
    без рестартов). js — тело arrow-функции, напр. '() => document.title'."""
    return {"result": moderator.debug_eval(req.js)}


class HoldRequest(BaseModel):
    on: bool = True


@app.post("/debug/hold")
def debug_hold(req: HoldRequest):
    """DEBUG: приостановить модерацию/переоткрытие чата, чтобы держать открытой
    панель участников во время разведки (True/False)."""
    moderator.debug_hold = req.on
    return {"debug_hold": moderator.debug_hold}


class PauseRequest(BaseModel):
    seconds: float = 60.0


@app.post("/debug/pause")
def debug_pause(req: PauseRequest):
    """DEBUG: пауза цикла модерации на N секунд (авто-снятие) — держать панель
    участников открытой для разведки через /debug/eval."""
    import time as _t
    moderator._pause_until = _t.time() + max(0.0, req.seconds)
    return {"pause_until": moderator._pause_until, "seconds": req.seconds}


@app.get("/screenshot")
def screenshot():
    path = os.path.join(config.shots_dir, "last.png")
    if not os.path.exists(path):
        return JSONResponse({"error": "no screenshot yet"}, status_code=404)
    return FileResponse(path, media_type="image/png", filename="screen.png")
