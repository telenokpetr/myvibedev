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
        ffmpeg \
        mpv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
# Chromium уже вшит в playwright-образ; фиксируем на всякий случай.
RUN playwright install chromium
# НАСТОЯЩИЙ Google Chrome: бандловый Chromium собран без проприетарных кодеков,
# и mp4 (H.264/AAC) в нём не играет — в камеру бота проходил только WebM.
# Chrome их умеет. Запуск — channel="chrome" (см. zoomweb.BROWSER_CHANNEL).
RUN playwright install --with-deps chrome

# x11vnc + noVNC + websockify — доступ к экрану браузера бота через браузер
# человека (http://localhost:6080): нужен для РУЧНОГО входа в Zoom-аккаунт бота
# (reCAPTCHA пропускает живую мышь), сессия оседает в профиле и держится долго.
# ОТДЕЛЬНЫМ слоем ПОСЛЕ playwright — чтобы не инвалидировать дорогие слои с
# Chromium/Chrome при добавлении этих пакетов.
# DEBIAN_FRONTEND=noninteractive: novnc тянет tzdata, а та в интерактиве спрашивает
# часовой пояс («Geographic area:») и ВЕШАЕТ сборку намертво.
RUN DEBIAN_FRONTEND=noninteractive apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        x11vnc \
        novnc \
        websockify \
    && rm -rf /var/lib/apt/lists/*

COPY . .
RUN chmod +x entrypoint.sh

ENV DISPLAY=:99
EXPOSE 9100 6080
ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9100"]
