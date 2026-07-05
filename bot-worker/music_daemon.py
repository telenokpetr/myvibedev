#!/usr/bin/env python3
"""Фоновая музыка бота через виртуальный микрофон vmic, с автопаузой по голосу.

- Треки из PLAYLIST играют по очереди (чередование), зациклено по кругу.
- Детектор голоса слушает vspeaker.monitor (речь других участников).
- Речь непрерывно > SPEAK_PAUSE сек  -> музыка на паузу (SIGSTOP ffmpeg).
- Тишина непрерывно >= SILENCE_RESUME сек -> музыка снова играет (SIGCONT).
Останавливается: pkill -f music_daemon
"""
import os, time, signal, subprocess, threading, audioop

DISPLAY = ":99"
ENV = {**os.environ, "DISPLAY": DISPLAY}
VMIC = "vmic"
MON = "vspeaker.monitor"
VOL = os.environ.get("MUSIC_VOL", "0.05")
MUSIC_DIR = os.environ.get("MUSIC_DIR", "/data/music")


def load_playlist():
    """Все mp3 из MUSIC_DIR по алфавиту — новые файлы подхватываются сами."""
    try:
        files = sorted(f for f in os.listdir(MUSIC_DIR) if f.lower().endswith(".mp3"))
    except FileNotFoundError:
        files = []
    return [os.path.join(MUSIC_DIR, f) for f in files]


PLAYLIST = load_playlist()

SPEECH_RMS = int(os.environ.get("SPEECH_RMS", "150"))          # порог RMS s16: меньше = чувствительнее
HANGOVER = float(os.environ.get("HANGOVER", "0.6"))            # с: речь непрерывна, если громкий кадр был недавно
SPEAK_PAUSE = float(os.environ.get("SPEAK_PAUSE", "2.0"))      # с непрерывной речи -> пауза
SILENCE_RESUME = float(os.environ.get("SILENCE_RESUME", "60")) # с непрерывной тишины -> возобновление

state = {"last_speech": 0.0, "run_start": 0.0}
lock = threading.Lock()
stop = threading.Event()


def vad_loop():
    p = subprocess.Popen(
        ["parec", "--device=" + MON, "--format=s16le", "--rate=16000", "--channels=1"],
        env=ENV, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    FRAME = 640  # 20 мс @ 16k mono s16le
    try:
        while not stop.is_set():
            data = p.stdout.read(FRAME)
            if not data:
                break
            rms = audioop.rms(data, 2)
            now = time.time()
            if rms > SPEECH_RMS:
                with lock:
                    if now - state["last_speech"] > HANGOVER:
                        state["run_start"] = now      # начался новый заход речи
                    state["last_speech"] = now
    finally:
        try:
            p.terminate()
        except Exception:
            pass


class Player:
    def __init__(self):
        self.proc = None
        self.paused = False

    def start(self, path):
        self.stop()
        self.proc = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-re", "-i", path,
             "-af", "volume=" + VOL, "-f", "pulse", "-device", VMIC, "bot-music"],
            env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.paused = False

    def pause(self):
        if self.proc and self.proc.poll() is None and not self.paused:
            self.proc.send_signal(signal.SIGSTOP)
            self.paused = True

    def resume(self):
        if self.proc and self.proc.poll() is None and self.paused:
            self.proc.send_signal(signal.SIGCONT)
            self.paused = False

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.proc:
            try:
                if self.paused:
                    self.proc.send_signal(signal.SIGCONT)
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
            self.paused = False


def main():
    threading.Thread(target=vad_loop, daemon=True).start()
    pl = Player()
    idx = 0
    paused_for_speech = False
    pl.start(PLAYLIST[idx])
    print(f"[music] play {os.path.basename(PLAYLIST[idx])} vol={VOL}", flush=True)
    while not stop.is_set():
        now = time.time()
        with lock:
            last = state["last_speech"]
            run_start = state["run_start"]
        silent_for = now - last
        speaking = silent_for < HANGOVER
        run_len = (last - run_start) if speaking else 0.0

        if not paused_for_speech and speaking and run_len >= SPEAK_PAUSE:
            pl.pause()
            paused_for_speech = True
            print(f"[music] PAUSE — речь {run_len:.1f}s", flush=True)
        elif paused_for_speech and silent_for >= SILENCE_RESUME:
            pl.resume()
            paused_for_speech = False
            print(f"[music] RESUME — тишина {silent_for:.0f}s", flush=True)

        # трек доиграл и мы не на паузе -> следующий по кругу (чередование)
        if not pl.alive() and not paused_for_speech:
            idx = (idx + 1) % len(PLAYLIST)
            pl.start(PLAYLIST[idx])
            print(f"[music] next -> {os.path.basename(PLAYLIST[idx])}", flush=True)

        time.sleep(0.2)
    pl.stop()


if __name__ == "__main__":
    main()
