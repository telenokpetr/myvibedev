import logging
import os
import re
import subprocess

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s: %(message)s")

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from app import vcam
from app.account import account
from app.config import config
from app.moderation import ChatMessage, ProfanityFilter, SpamDetector, classify
from app.moderator import moderator
from app.recorder import recorder
from app.waitroom import gatekeeper

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


@app.post("/session/start")
def start_own():
    """Начать СВОЮ конференцию из аккаунта бота (личная комната). Бот — хост."""
    try:
        moderator.join("NEW_MEETING")
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


@app.post("/account/manual-login")
def account_manual_login():
    """Открыть страницу входа Zoom в браузере бота и держать её — человек
    логинится РУКАМИ через noVNC (http://localhost:6080). Сессия осядет в
    профиле и будет держаться долго (в отличие от cookie-снимка)."""
    return account.manual_login_start()


@app.post("/account/manual-finish")
def account_manual_finish():
    """Подтвердить, что человек вошёл: проверить профиль, сохранить, закрыть."""
    return account.manual_login_finish()


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


# ---- Медиа бота: видео в камеру, музыка в микрофон (живой поток, см. vcam.py) ----

# Тип отдаём точный: с неверным Content-Type <video> отказывается играть файл.
_CTYPES = {".webm": "video/webm", ".mp4": "video/mp4", ".mp3": "audio/mpeg",
           ".ogg": "audio/ogg", ".opus": "audio/ogg", ".wav": "audio/wav",
           ".flac": "audio/flac", ".m4a": "audio/mp4", ".aac": "audio/aac"}


class VideoPlayRequest(BaseModel):
    name: str


class VideoVolumeRequest(BaseModel):
    volume: int = 100


@app.get("/video/list")
def video_list():
    return {"videos": vcam.list_videos(), "max_bytes": vcam.MAX_UPLOAD,
            "playing": moderator.video}


@app.post("/video/upload")
async def video_upload(request: Request, name: str):
    """Загрузка ролика СЫРЫМ телом (не multipart): имя — в query, байты — в body.

    Так не нужен python-multipart в этом образе, и 100 МБ не парсятся как форма —
    пишем на диск потоком. Клиент — web-сервис (см. bot_client.video_upload).
    """
    name = vcam.safe_name(name)
    if not name.lower().endswith(vcam.ALLOWED_EXT):
        return JSONResponse({"error": "видео .mp4/.webm или музыка .mp3/.ogg/.wav/.flac"},
                            status_code=400)
    vcam.ensure_dir()
    path = vcam.path_of(name)
    size = 0
    try:
        with open(path, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > vcam.MAX_UPLOAD:
                    f.close()
                    os.remove(path)
                    return JSONResponse({"error": "файл больше 100 МБ"}, status_code=413)
                f.write(chunk)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"не сохранён: {exc}"}, status_code=500)
    if not size:
        os.remove(path)
        return JSONResponse({"error": "пустой файл"}, status_code=400)
    if vcam.needs_convert(name):
        # Chromium не играет mp4 — перегоняем в WebM в фоне (см. vcam.py).
        final = vcam.convert_async(name)
        return {"ok": True, "name": final, "size": size, "converting": True}
    return {"ok": True, "name": name, "size": size}


@app.delete("/video/tracks/{name}")
def video_delete(name: str):
    path = vcam.path_of(name)
    if not os.path.exists(path):
        return JSONResponse({"error": "нет такого ролика"}, status_code=404)
    if moderator.video == vcam.safe_name(name):
        moderator.command("video_stop")
    os.remove(path)
    return {"ok": True, "videos": vcam.list_videos()}


@app.options("/video/file/{name}")
def video_file_preflight(name: str):
    """Preflight для Private Network Access: страница Zoom (публичный https)
    тянет ролик с 127.0.0.1, и Chrome сперва спрашивает разрешение."""
    return Response(status_code=204, headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "Range, Content-Type",
        "Access-Control-Allow-Private-Network": "true",
        "Access-Control-Max-Age": "86400",
    })


@app.get("/video/file/{name}")
def video_file(name: str, request: Request):
    """Отдать ролик СТРАНИЦЕ бота (Chromium ходит сюда на 127.0.0.1).

    CORS обязателен: без него cross-origin видео «портит» canvas и captureStream
    падает с SecurityError. Range — чтобы <video> мог перематывать и не тянуть
    все 100 МБ разом.
    """
    path = vcam.path_of(name)
    if not os.path.exists(path):
        return JSONResponse({"error": "нет такого ролика"}, status_code=404)
    total = os.path.getsize(path)
    ctype = _CTYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
    headers = {"Access-Control-Allow-Origin": "*", "Accept-Ranges": "bytes",
               "Cache-Control": "no-store",
               # Chrome спрашивает разрешение на выход в локальную сеть
               # (Private Network Access) — без этого заголовка запрос с
               # https://app.zoom.us на 127.0.0.1 он режет.
               "Access-Control-Allow-Private-Network": "true"}

    rng = request.headers.get("range")
    if not rng:
        return FileResponse(path, media_type=ctype, headers=headers)

    m = re.match(r"bytes=(\d*)-(\d*)\s*$", rng.strip())
    if not m or (not m.group(1) and not m.group(2)):
        return Response(status_code=416,
                        headers={**headers, "Content-Range": f"bytes */{total}"})
    if not m.group(1):
        # Суффиксный диапазон «bytes=-N» = ПОСЛЕДНИЕ N байт. Именно им браузер
        # забирает индекс mp4 (moov в конце файла, если не делали faststart).
        # Раньше он читался как «первые N байт» — Chrome получал мусор вместо
        # moov и падал с «Format error», хотя кодеки были на месте.
        length = int(m.group(2))
        if length <= 0:
            return Response(status_code=416,
                            headers={**headers, "Content-Range": f"bytes */{total}"})
        start = max(0, total - length)
        end = total - 1
    else:
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else total - 1
    end = min(end, total - 1)
    if start > end:
        return Response(status_code=416,
                        headers={**headers, "Content-Range": f"bytes */{total}"})

    def body():
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(256 * 1024, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    return StreamingResponse(body(), status_code=206, media_type=ctype, headers={
        **headers, "Content-Range": f"bytes {start}-{end}/{total}",
        "Content-Length": str(end - start + 1)})


@app.post("/video/play")
def video_play(req: VideoPlayRequest):
    if vcam.safe_name(req.name) in vcam._converting:
        return JSONResponse({"error": "ролик ещё перегоняется в WebM — подождите"},
                            status_code=409)
    if not os.path.exists(vcam.path_of(req.name)):
        return JSONResponse({"error": "нет такого ролика"}, status_code=404)
    if not moderator.enabled:
        return JSONResponse({"error": "бот не в конференции"}, status_code=409)
    moderator.command("video_play", req.name)
    return {"queued": True, "name": vcam.safe_name(req.name)}


@app.post("/video/volume")
def video_volume(req: VideoVolumeRequest):
    moderator.command("video_volume", str(req.volume))
    return {"queued": True, "volume": req.volume}


@app.post("/video/{action}")
def video_action(action: str):
    if action not in ("stop", "pause", "resume"):
        return JSONResponse({"error": "неизвестное действие"}, status_code=400)
    moderator.command(f"video_{action}")
    return {"queued": True}


@app.get("/video/status")
def video_status():
    if not moderator.enabled:
        return {"in_meeting": False, "playing": None}
    st = moderator.debug_eval("() => window.__vcam ? window.__vcam.state() : null")
    return {"in_meeting": True, "playing": moderator.video, "page": st}


# ---- Звук: VU бота + стрим звука участников для мониторинга ----

@app.get("/audio/bot-level")
def audio_bot_level():
    """Уровень звука, который ОТДАЁТ бот (музыка/ролик), 0..100 — для VU."""
    if not moderator.enabled:
        return {"level": 0, "in_meeting": False}
    return {"level": int(moderator.call("vcam_level") or 0), "in_meeting": True}


def _pulse_monitor() -> str:
    """Источник-монитор PulseAudio, куда Chromium играет звук КОНФЕРЕНЦИИ
    (других участников). Свой звук бота сюда НЕ попадает (он в WebAudio→микрофон)."""
    try:
        sink = subprocess.run(["pactl", "get-default-sink"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
        if sink:
            return sink + ".monitor"
    except Exception:  # noqa: BLE001
        pass
    return "@DEFAULT_MONITOR@"


@app.get("/audio/meeting")
def audio_meeting():
    """Живой звук конференции (участники) в mp3 — чтобы СЛЫШАТЬ в мониторинге.
    ffmpeg снимает PulseAudio-монитор бота и стримит. Клиент включает по желанию."""
    mon = _pulse_monitor()
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error",
           "-f", "pulse", "-i", mon, "-ac", "1", "-ar", "44100",
           "-f", "mp3", "-b:a", "64k", "-"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"ffmpeg не запустился: {exc}"}, status_code=500)

    def gen():
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    return StreamingResponse(gen(), media_type="audio/mpeg",
                             headers={"Cache-Control": "no-store"})


# ---- Модерация зала ожидания (впуск по списку, см. waitroom.py) ----

class NameRequest(BaseModel):
    name: str


class EnabledRequest(BaseModel):
    on: bool = True


@app.get("/waitroom/status")
def waitroom_status():
    st = gatekeeper.state()
    # Текущие имена в зале ожидания — с живой страницы, если бот в митинге.
    if moderator.enabled:
        try:
            st["waiting"] = moderator.call("read_waiting_room") or []
        except Exception:  # noqa: BLE001
            st["waiting"] = []
    else:
        st["waiting"] = []
    return st


@app.post("/waitroom/enabled")
def waitroom_enabled(req: EnabledRequest):
    return gatekeeper.set_enabled(req.on)


@app.post("/waitroom/names")
def waitroom_add(req: NameRequest):
    if not (req.name or "").strip():
        return JSONResponse({"error": "пустое имя"}, status_code=400)
    return gatekeeper.add(req.name)


@app.delete("/waitroom/names/{name}")
def waitroom_remove(name: str):
    return gatekeeper.remove(name)


@app.post("/waitroom/admit")
def waitroom_admit(req: NameRequest):
    """Ручной впуск конкретного человека из зала ожидания."""
    if not moderator.enabled:
        return JSONResponse({"error": "бот не в конференции"}, status_code=409)
    ok = moderator.call("admit", args=[req.name])
    return {"ok": bool(ok), "name": req.name}


@app.post("/waitroom/deny")
def waitroom_deny(req: NameRequest):
    """Ручной возврат участника в зал ожидания."""
    if not moderator.enabled:
        return JSONResponse({"error": "бот не в конференции"}, status_code=409)
    ok = moderator.call("send_to_waiting", args=[req.name])
    return {"ok": bool(ok), "name": req.name}


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


class CallRequest(BaseModel):
    method: str
    args: list = []


@app.post("/debug/call")
def debug_call(req: CallRequest):
    """DEBUG: вызвать метод ZoomWeb на живой сессии (напр. enable_original_sound,
    debug_click, debug_dump) — настройка/разведка без рестарта бота."""
    if not req.method.isidentifier() or req.method.startswith("_"):
        return JSONResponse({"error": "плохое имя метода"}, status_code=400)
    return {"result": moderator.call(req.method, args=req.args)}


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
