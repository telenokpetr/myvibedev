FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/zoom \
    XDG_RUNTIME_DIR=/tmp/runtime-zoom

# Виртуальный дисплей, оконный менеджер, управление окнами/вводом,
# захват экрана и звука, OCR (этап 6), звуковая подсистема, Python.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb \
        x11-utils \
        x11-xserver-utils \
        openbox \
        xdotool \
        wmctrl \
        xclip \
        scrot \
        ffmpeg \
        mpv \
        tesseract-ocr \
        tesseract-ocr-rus \
        pulseaudio \
        pulseaudio-utils \
        dbus-x11 \
        libgl1-mesa-glx \
        fonts-liberation \
        python3 \
        python3-pip \
        ca-certificates \
        curl \
        wget \
    && rm -rf /var/lib/apt/lists/*

# Официальный Zoom-клиент для Linux (deb тянет свои зависимости через apt).
RUN wget -q https://zoom.us/client/latest/zoom_amd64.deb -O /tmp/zoom.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends /tmp/zoom.deb \
    && rm -f /tmp/zoom.deb \
    && rm -rf /var/lib/apt/lists/*

# Библиотеки, которые Zoom грузит в рантайме, но не объявляет как зависимости
# (libEGL и набор xcb-плагинов Qt).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libegl1 \
        libgl1-mesa-dri \
        libxcb-xtest0 \
        libxcb-cursor0 \
        libxcb-image0 \
        libxcb-keysyms1 \
        libxcb-randr0 \
        libxcb-render-util0 \
        libxcb-shape0 \
        libxcb-xinerama0 \
        libxcb-xkb1 \
        libxcb-icccm4 \
        libxkbcommon-x11-0 \
        libnss3 \
        libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Непривилегированный пользователь (Zoom и PulseAudio не любят root).
RUN useradd -m -s /bin/bash zoom \
    && mkdir -p /data/recordings /data/screenshots /data/music /tmp/runtime-zoom \
    && chown -R zoom:zoom /data /tmp/runtime-zoom \
    && chmod 700 /tmp/runtime-zoom

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x entrypoint.sh && chown -R zoom:zoom /app

USER zoom
EXPOSE 9000

ENTRYPOINT ["./entrypoint.sh"]
