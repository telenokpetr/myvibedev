"""Управляемый музыкальный сервис на базе mpv (open-source плеер).

Проигрывание/плейлист/громкость/переключение делает сам mpv — мы им управляем
через его JSON-IPC сокет. Поверх — VAD-автопауза: речь других участников
(vspeaker.monitor) > SPEAK_PAUSE сек ставит музыку на паузу, тишина >= SILENCE_RESUME
возобновляет. Вывод звука идёт в PulseAudio-sink vmic → vmic.monitor → BotMic (микрофон
бота в Zoom).

Управление из веб-интерфейса через эндпоинты /music/* в app.main.
"""

import audioop
import json
import logging
import os
import socket
import subprocess
import threading
import time

from app.config import config

log = logging.getLogger("music")

ENV = {**os.environ, "DISPLAY": config.display}
VMIC = "vmic"
MON = "vspeaker.monitor"
IPC_SOCK = os.environ.get("MPV_IPC", "/tmp/mpv-music.sock")

# VAD (автопауза по речи) — параметры через env, дефолты как в калибровке.
SPEECH_RMS = int(os.environ.get("SPEECH_RMS", "150"))
HANGOVER = float(os.environ.get("HANGOVER", "0.6"))
SPEAK_PAUSE = float(os.environ.get("SPEAK_PAUSE", "2.0"))
SILENCE_RESUME = float(os.environ.get("SILENCE_RESUME", "60"))

MAX_UPLOAD = 5 * 1024 * 1024  # 5 МБ на трек (требование)


class MusicService:
    def __init__(self) -> None:
        self.dir = config.music_dir
        self.volume = int(os.environ.get("MUSIC_VOL_PCT", "60"))  # 0..130
        self.manual_paused = False
        self.speech_paused = False
        self.running = False
        self._mpv: subprocess.Popen | None = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._last_speech = 0.0
        self._run_start = 0.0

    # ---------- плейлист / файлы ----------
    def _files(self) -> list[str]:
        try:
            return sorted(f for f in os.listdir(self.dir) if f.lower().endswith(".mp3"))
        except FileNotFoundError:
            return []

    def tracks(self) -> list[str]:
        return self._files()

    # ---------- IPC к mpv ----------
    def _ipc(self, *command):
        """Одна команда в mpv через unix-сокет. None при недоступности."""
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect(IPC_SOCK)
            s.sendall((json.dumps({"command": list(command)}) + "\n").encode())
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
            s.close()
            for line in buf.decode(errors="ignore").splitlines():
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if "error" in obj:  # это ответ на нашу команду
                    return obj
            return None
        except Exception:  # noqa: BLE001
            return None

    def _get(self, prop):
        r = self._ipc("get_property", prop)
        if r and r.get("error") == "success":
            return r.get("data")
        return None

    def _apply_pause(self) -> None:
        """Эффективная пауза = ручная ИЛИ по речи."""
        self._ipc("set_property", "pause", bool(self.manual_paused or self.speech_paused))

    # ---------- жизненный цикл ----------
    def start(self) -> dict:
        with self._lock:
            os.makedirs(self.dir, exist_ok=True)
            if self.running and self._mpv and self._mpv.poll() is None:
                return self.status()
            self._stop.clear()
            try:
                os.unlink(IPC_SOCK)
            except FileNotFoundError:
                pass
            args = [
                "mpv", "--no-video", "--idle=yes", "--force-window=no",
                "--loop-playlist=inf", "--gapless-audio=yes",
                f"--input-ipc-server={IPC_SOCK}",
                "--ao=pulse", f"--audio-device=pulse/{VMIC}",
                f"--volume={self.volume}", "--volume-max=130",
            ] + [os.path.join(self.dir, f) for f in self._files()]
            self._mpv = subprocess.Popen(
                args, env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self.running = True
            self.manual_paused = False
            self.speech_paused = False
            # ждём появления сокета
            for _ in range(25):
                if os.path.exists(IPC_SOCK):
                    break
                time.sleep(0.2)
            self._ipc("set_property", "volume", self.volume)
            threading.Thread(target=self._vad_loop, daemon=True).start()
            threading.Thread(target=self._control_loop, daemon=True).start()
            log.info("music: mpv запущен, %d треков, vol=%d", len(self._files()), self.volume)
            return self.status()

    def stop(self) -> dict:
        with self._lock:
            self._stop.set()
            self.running = False
            if self._mpv:
                try:
                    self._ipc("quit")
                    self._mpv.wait(timeout=4)
                except Exception:  # noqa: BLE001
                    try:
                        self._mpv.kill()
                    except Exception:  # noqa: BLE001
                        pass
                self._mpv = None
            return self.status()

    # ---------- ручное управление ----------
    def pause(self) -> dict:
        with self._lock:
            self.manual_paused = True
            self._apply_pause()
            return self.status()

    def resume(self) -> dict:
        with self._lock:
            self.manual_paused = False
            self._apply_pause()
            return self.status()

    def toggle(self) -> dict:
        return self.resume() if self.manual_paused else self.pause()

    def next(self) -> dict:
        with self._lock:
            self.manual_paused = False
            self._ipc("playlist-next", "force")
            self._apply_pause()
            return self.status()

    def prev(self) -> dict:
        with self._lock:
            self.manual_paused = False
            self._ipc("playlist-prev", "force")
            self._apply_pause()
            return self.status()

    def set_volume(self, pct: int) -> dict:
        with self._lock:
            self.volume = max(0, min(130, int(pct)))
            self._ipc("set_property", "volume", self.volume)
            return self.status()

    # ---------- добавление / удаление треков ----------
    def add_file(self, filename: str, data: bytes) -> dict:
        base = os.path.basename(filename or "").strip()
        if not base.lower().endswith(".mp3"):
            raise ValueError("только .mp3")
        if len(data) > MAX_UPLOAD:
            raise ValueError("файл больше 5 МБ")
        # безопасное имя
        safe = "".join(c for c in base if c.isalnum() or c in " ._-()").strip() or "track.mp3"
        os.makedirs(self.dir, exist_ok=True)
        path = os.path.join(self.dir, safe)
        with open(path, "wb") as f:
            f.write(data)
        # если mpv жив — добавляем в текущий плейлист без перезапуска
        if self.running and self._mpv and self._mpv.poll() is None:
            self._ipc("loadfile", path, "append-play")
        return self.status()

    def delete_track(self, filename: str) -> dict:
        base = os.path.basename(filename or "")
        path = os.path.join(self.dir, base)
        if os.path.isfile(path):
            os.unlink(path)
        # перестраиваем плейлист mpv из оставшихся файлов
        if self.running and self._mpv and self._mpv.poll() is None:
            self._ipc("playlist-clear")
            self._ipc("playlist-remove", "current")
            for f in self._files():
                self._ipc("loadfile", os.path.join(self.dir, f), "append-play")
        return self.status()

    # ---------- фоновые циклы ----------
    def _vad_loop(self) -> None:
        p = subprocess.Popen(
            ["parec", "--device=" + MON, "--format=s16le", "--rate=16000", "--channels=1"],
            env=ENV, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        FRAME = 640  # 20 мс @ 16k mono s16le
        try:
            while not self._stop.is_set():
                data = p.stdout.read(FRAME)
                if not data:
                    break
                rms = audioop.rms(data, 2)
                now = time.time()
                if rms > SPEECH_RMS:
                    if now - self._last_speech > HANGOVER:
                        self._run_start = now
                    self._last_speech = now
        finally:
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass

    def _control_loop(self) -> None:
        while not self._stop.is_set():
            now = time.time()
            silent_for = now - self._last_speech
            speaking = silent_for < HANGOVER
            run_len = (self._last_speech - self._run_start) if speaking else 0.0
            with self._lock:
                if not self.speech_paused and speaking and run_len >= SPEAK_PAUSE:
                    self.speech_paused = True
                    self._apply_pause()
                    log.info("music: PAUSE — речь %.1fs", run_len)
                elif self.speech_paused and silent_for >= SILENCE_RESUME:
                    self.speech_paused = False
                    self._apply_pause()
                    log.info("music: RESUME — тишина %.0fs", silent_for)
            time.sleep(0.2)

    # ---------- статус ----------
    def status(self) -> dict:
        alive = self.running and self._mpv is not None and self._mpv.poll() is None
        current = None
        pos = None
        if alive:
            current = self._get("media-title") or self._get("filename")
            pos = self._get("playlist-pos")
        return {
            "running": alive,
            "playing": alive and not self.manual_paused and not self.speech_paused,
            "paused": self.manual_paused or self.speech_paused,
            "manual_paused": self.manual_paused,
            "speech_paused": self.speech_paused,
            "current": current,
            "index": pos,
            "volume": self.volume,
            "tracks": self._files(),
        }


music = MusicService()
