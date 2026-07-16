"""Виртуальная камера с ЖИВЫМ потоком — целиком в userspace, без v4l2loopback.

Почему не как раньше: fake-file камера Chromium
(`--use-file-for-fake-video-capture`) читает .y4m ОДИН РАЗ при старте браузера —
ролик не сменить без перезапуска, а 100-мегабайтный mp4 в сыром y4m
разворачивается в гигабайты (720p15 ≈ 20 МБ/с).

Как сейчас: подменяем `navigator.mediaDevices.getUserMedia` — Zoom получает
поток с <canvas>, куда мы 15 раз в секунду перерисовываем кадр из скрытого
<video>. Трек живёт на canvas и НЕ рвётся при смене ролика (заодно уходит
мигание заставки). Звук ролика подмешивается в аудио-трек через WebAudio, так
что видео идёт со звуком.

Ролики отдаёт сам bot-browser с http://127.0.0.1:9100 (см. /video/file/* в
main.py). Это тот же контейнер, а 127.0.0.1 Chromium считает доверенным
источником — на https-странице Zoom он не блокируется как mixed content.
CORS + video.crossOrigin='anonymous' обязательны: иначе canvas «портится»
(taint) и captureStream падает с SecurityError.
"""

import logging
import os
import re
import subprocess
import threading

log = logging.getLogger("vcam")

VIDEO_DIR = os.environ.get("VIDEO_DIR", "/data/video")
MAX_UPLOAD = 100 * 1024 * 1024   # 100 МБ на файл (требование)

# Видео идёт В КАМЕРУ (картинка + звук), музыка — ТОЛЬКО В МИКРОФОН: на камере
# при этом остаётся заставка. Отдельного плеера для музыки не нужно — тот же
# скрытый <video> играет и mp3, а draw() при videoWidth==0 сам рисует заставку.
VIDEO_EXT = (".mp4", ".webm")
# Chromium (проверено на живой странице бота): mp3/ogg/opus/flac — «probably»,
# wav — «maybe», а m4a/AAC не умеет вовсе → его перегоняем.
AUDIO_EXT = (".mp3", ".ogg", ".opus", ".wav", ".flac", ".m4a", ".aac")
ALLOWED_EXT = VIDEO_EXT + AUDIO_EXT
# Порт своего же API — страница ходит к нему за файлом ролика.
SELF_ORIGIN = os.environ.get("SELF_ORIGIN", "http://127.0.0.1:9100")

W, H, FPS = 1280, 720, 24


def ensure_dir() -> str:
    os.makedirs(VIDEO_DIR, exist_ok=True)
    return VIDEO_DIR


def safe_name(name: str) -> str:
    """Имя файла без путей и сюрпризов (загрузка идёт из веба)."""
    base = os.path.basename(name or "").strip()
    base = re.sub(r"[^\w.\- ]+", "_", base, flags=re.UNICODE)
    return base[:120]


def path_of(name: str) -> str:
    return os.path.join(VIDEO_DIR, safe_name(name))


def list_videos() -> list[dict]:
    ensure_dir()
    out = []
    for n in sorted(os.listdir(VIDEO_DIR)):
        p = os.path.join(VIDEO_DIR, n)
        if not os.path.isfile(p) or n.endswith(".part"):
            continue
        item = {"name": n, "size": os.path.getsize(p),
                "kind": "audio" if is_audio(n) else "video"}
        if n in _converting:
            item["converting"] = True
        item["playable"] = n not in _converting
        out.append(item)
    return out


# ---- перегон в WebM ----
#
# Бандловый Chromium собран без проприетарных кодеков: mp4 (H.264/AAC) в нём не
# играет вообще. Настоящий Chrome их умеет, но с ним Zoom ломается на нашем
# canvas-потоке (см. BROWSER_CHANNEL в docker-compose). Поэтому остаёмся на
# Chromium, а любой не-WebM ролик перегоняем ffmpeg'ом при загрузке.
_converting: set[str] = set()


def is_audio(name: str) -> bool:
    return name.lower().endswith(AUDIO_EXT)


def needs_convert(name: str) -> bool:
    """mp4 — из-за отсутствия H.264 в Chromium; m4a/aac — из-за отсутствия AAC.
    Остальное (webm, mp3, ogg, wav, flac) браузер играет как есть."""
    low = name.lower()
    if low.endswith(".webm"):
        return False
    if is_audio(name):
        return low.endswith((".m4a", ".aac"))
    return True


def webm_name(name: str) -> str:
    """Имя после перегона: видео → .webm, музыка → .ogg (opus)."""
    base = os.path.splitext(name)[0]
    return base + (".ogg" if is_audio(name) else ".webm")


def convert_to_webm(src_name: str) -> None:
    """Перегнать файл в то, что Chromium играет, рядом с исходником, в фоне.

    Видео → WebM (VP8/Opus): VP8, а не VP9, — жмёт заметно быстрее, качества для
    камеры хватает. Музыка → Ogg/Opus (только звук, без пустой видеодорожки).
    Исходник удаляем — иначе в списке два одинаковых файла, из которых играет
    только один.
    """
    src = path_of(src_name)
    dst = path_of(webm_name(src_name))
    tmp = dst + ".part"
    _converting.add(webm_name(src_name))
    try:
        log.info("перегон: %s → %s", src_name, os.path.basename(dst))
        if is_audio(src_name):
            cmd = ["ffmpeg", "-y", "-i", src, "-vn",
                   "-c:a", "libopus", "-b:a", "128k", "-f", "ogg", tmp]
        else:
            cmd = ["ffmpeg", "-y", "-i", src, "-c:v", "libvpx", "-b:v", "1500k",
                   "-deadline", "realtime", "-cpu-used", "4",
                   "-c:a", "libopus", "-b:a", "96k", "-f", "webm", tmp]
        subprocess.run(cmd, check=True, capture_output=True, timeout=3600)
        os.replace(tmp, dst)
        if os.path.exists(src) and src != dst:
            os.remove(src)
        log.info("перегон готов: %s", os.path.basename(dst))
    except subprocess.CalledProcessError as exc:
        log.warning("перегон не удался (%s): %s", src_name,
                    (exc.stderr or b"")[-300:].decode("utf-8", "replace"))
        _cleanup(tmp)
    except Exception as exc:  # noqa: BLE001
        log.warning("перегон сорвался (%s): %s", src_name, exc)
        _cleanup(tmp)
    finally:
        _converting.discard(webm_name(src_name))


def _cleanup(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def convert_async(src_name: str) -> str:
    """Запустить перегон в фоне, вернуть будущее имя ролика."""
    threading.Thread(target=convert_to_webm, args=(src_name,), daemon=True).start()
    return webm_name(src_name)


def file_url(name: str) -> str:
    from urllib.parse import quote
    return f"{SELF_ORIGIN}/video/file/{quote(safe_name(name))}"


def launch_args() -> list[str]:
    """Флаги Chromium для живой виртуальной камеры/микрофона."""
    return [
        # Авто-грант доступа к камере/микрофону + список устройств в
        # enumerateDevices (Zoom показывает их в UI), но САМ поток отдаёт наш
        # getUserMedia-перехват, а не файл.
        "--use-fake-ui-for-media-stream",
        "--use-fake-device-for-media-stream",
        # Без жеста пользователя <video>.play() и AudioContext не стартуют.
        "--autoplay-policy=no-user-gesture-required",
        # Подстраховка для http://127.0.0.1 на https-странице Zoom.
        "--allow-running-insecure-content",
        # Private/Local Network Access: Chrome запрещает странице с публичного
        # https://app.zoom.us ходить на 127.0.0.1 — fetch падает с «Failed to
        # fetch», а <video> отдаёт невнятное «Format error» (файл при этом
        # целый). Бандловый Chromium это пропускал, Chrome — нет.
        "--disable-features=LocalNetworkAccessChecks,"
        "PrivateNetworkAccessSendPreflights,"
        "PrivateNetworkAccessRespectPreflightResults",
    ]


# Скрипт ставится через add_init_script — то есть ДО загрузки страницы Zoom и в
# каждом фрейме, иначе Zoom успеет взять настоящий getUserMedia.
_INIT_JS = r"""
(() => {
  if (window.__vcam) return;
  const W = __W__, H = __H__, FPS = __FPS__;
  const S = { canvas:null, ctx:null, video:null, stream:null, ac:null, dest:null,
              gain:null, caption:"__CAPTION__", timer:null, playing:null, err:null };

  function draw() {
    const ctx = S.ctx, v = S.video;
    if (!ctx) return;
    ctx.fillStyle = "#001f3f";
    ctx.fillRect(0, 0, W, H);
    if (v && v.readyState >= 2 && !v.paused && !v.ended && v.videoWidth) {
      // Вписываем кадр с сохранением пропорций (по краям — фон).
      const r = Math.min(W / v.videoWidth, H / v.videoHeight);
      const w = v.videoWidth * r, h = v.videoHeight * r;
      try { ctx.drawImage(v, (W - w) / 2, (H - h) / 2, w, h); } catch (e) { S.err = String(e); }
    } else {
      ctx.fillStyle = "#ffffff";
      ctx.font = "bold 64px sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(S.caption, W / 2, H / 2);
    }
  }

  function ensure() {
    if (S.stream) return S.stream;
    const c = document.createElement("canvas");
    c.width = W; c.height = H;
    S.canvas = c;
    S.ctx = c.getContext("2d", { alpha: false });
    const v = document.createElement("video");
    v.crossOrigin = "anonymous";   // без этого canvas портится и captureStream падает
    v.loop = true; v.playsInline = true; v.preload = "auto";
    v.style.cssText = "position:fixed;left:-9999px;width:1px;height:1px;";
    S.video = v;
    (document.body || document.documentElement).appendChild(v);
    draw();
    S.stream = c.captureStream(FPS);
    // rAF в фоне тормозится браузером — рисуем по таймеру.
    S.timer = setInterval(draw, Math.round(1000 / FPS));
    return S.stream;
  }

  function audio() {
    ensure();
    if (!S.dest) {
      const AC = window.AudioContext || window.webkitAudioContext;
      S.ac = new AC();
      S.dest = S.ac.createMediaStreamDestination();
      S.gain = S.ac.createGain();
      S.gain.gain.value = 1;
      S.gain.connect(S.dest);
      try { S.ac.createMediaElementSource(S.video).connect(S.gain); }
      catch (e) { S.err = String(e); }
      // Тихий осциллятор держит граф живым: без источника dest отдаёт
      // «мёртвый» трек, и Zoom считает микрофон отвалившимся.
      const osc = S.ac.createOscillator(), g = S.ac.createGain();
      g.gain.value = 0;
      osc.connect(g); g.connect(S.dest); osc.start();
    }
    if (S.ac.state === "suspended") S.ac.resume().catch(() => {});
    return S.dest.stream.getAudioTracks()[0];
  }

  window.__vcam = {
    play(url) {
      ensure(); audio();
      S.video.src = url;
      S.playing = url;
      return S.video.play().then(() => true).catch(e => String(e));
    },
    stop() {
      if (S.video) { S.video.pause(); S.video.removeAttribute("src"); S.video.load(); }
      S.playing = null;
      return true;
    },
    pause() { if (S.video) S.video.pause(); return true; },
    resume() { return S.video ? S.video.play().then(() => true).catch(e => String(e)) : false; },
    volume(v) { if (S.gain) S.gain.gain.value = Math.max(0, Math.min(1, v / 100)); return true; },
    caption(t) { S.caption = t; return true; },
    state() {
      const v = S.video;
      return { playing: S.playing, ready: !!S.stream,
               paused: v ? v.paused : true,
               t: v ? v.currentTime : 0,
               dur: (v && isFinite(v.duration)) ? v.duration : 0,
               err: S.err };
    },
    // Диагностика: что РЕАЛЬНО нарисовано на холсте и жив ли трек, который
    // забрал Zoom (участники видели чёрный экран при живом треке).
    probe() {
      if (!S.ctx) return { noCanvas: true };
      const pts = [[W / 2, H / 2], [40, 40], [W - 40, H - 40]];
      const px = pts.map(p => {
        try {
          const d = S.ctx.getImageData(p[0] | 0, p[1] | 0, 1, 1).data;
          return d[0] + ',' + d[1] + ',' + d[2];
        } catch (e) { return 'ТAINT:' + String(e).slice(0, 40); }
      });
      const tr = S.stream ? S.stream.getVideoTracks()[0] : null;
      const v = S.video;
      return { px: px, timer: !!S.timer,
               track: tr ? { ready: tr.readyState, muted: tr.muted, enabled: tr.enabled } : null,
               video: v ? { rs: v.readyState, paused: v.paused, t: v.currentTime,
                            w: v.videoWidth, frames: v.getVideoPlaybackQuality
                              ? v.getVideoPlaybackQuality().totalVideoFrames : -1 } : null,
               err: S.err };
    },
  };

  const md = navigator.mediaDevices;
  if (md && md.getUserMedia) {
    const orig = md.getUserMedia.bind(md);
    md.getUserMedia = async (c) => {
      c = c || {};
      try {
        const out = new MediaStream();
        // Constraints Zoom'а игнорируем (deviceId и пр.) — отдаём свой поток.
        if (c.video) { ensure(); out.addTrack(S.stream.getVideoTracks()[0].clone()); }
        if (c.audio) { out.addTrack(audio().clone()); }
        if (out.getTracks().length) return out;
      } catch (e) { console.error("vcam:", e); S.err = String(e); }
      return orig(c);   // на всякий: не смогли — отдаём настоящий поток
    };
  }
})();
"""


def init_script(caption: str = "Тех. поддержка") -> str:
    return (_INIT_JS
            .replace("__W__", str(W))
            .replace("__H__", str(H))
            .replace("__FPS__", str(FPS))
            .replace("__CAPTION__", (caption or "").replace('"', "'")))
