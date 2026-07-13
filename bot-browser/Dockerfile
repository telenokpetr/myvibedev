# Браузерный бот-модератор: Zoom web-клиент через Playwright под Xvfb.
# Полностью headless Zoom web не заводит (детект автоматизации + WebRTC), поэтому
# гоняем headful Chromium на виртуальном дисплее Xvfb — как desktop-воркер.
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

# Xvfb + аудио-стек для fake-микрофона (музыкальный браузер) и x11-утилиты.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb \
        x11-utils \
        pulseaudio \
        pulseaudio-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
# Chromium уже вшит в playwright-образ; фиксируем на всякий случай.
RUN playwright install chromium

COPY . .
RUN chmod +x entrypoint.sh

ENV DISPLAY=:99
EXPOSE 9100
ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9100"]
