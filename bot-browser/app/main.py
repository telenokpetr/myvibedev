import logging
import os

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s: %(message)s")

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.config import config
from app.moderation import ChatMessage, ProfanityFilter, SpamDetector, classify
from app.moderator import moderator

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


@app.get("/screenshot")
def screenshot():
    path = os.path.join(config.shots_dir, "last.png")
    if not os.path.exists(path):
        return JSONResponse({"error": "no screenshot yet"}, status_code=404)
    return FileResponse(path, media_type="image/png", filename="screen.png")
