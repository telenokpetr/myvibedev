FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    PYTHONUNBUFFERED=1

# Виртуальный дисплей (Xvfb), захват экрана (ffmpeg/scrot), OCR (этап 6) и Python.
# Сам Zoom-клиент ставится на этапе 5 — здесь готовим базу.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb \
        x11-utils \
        x11-xserver-utils \
        xdotool \
        scrot \
        ffmpeg \
        tesseract-ocr \
        tesseract-ocr-rus \
        python3 \
        python3-pip \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x entrypoint.sh

EXPOSE 9000

ENTRYPOINT ["./entrypoint.sh"]
