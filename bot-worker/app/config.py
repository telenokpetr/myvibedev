import os


class Config:
    display: str = os.environ.get("DISPLAY", ":99")
    geometry: str = os.environ.get("SCREEN_GEOMETRY", "1280x720x24")
    bot_name: str = os.environ.get("ZOOM_BOT_NAME", "Модератор")
    recordings_dir: str = os.environ.get("RECORDINGS_DIR", "/data/recordings")
    screenshots_dir: str = os.environ.get("SCREENSHOTS_DIR", "/data/screenshots")
    # Куда сообщать статус (web-сервис). Необязательно.
    callback_url: str = os.environ.get("WEB_CALLBACK_URL", "")

    @property
    def width(self) -> int:
        return int(self.geometry.split("x")[0])

    @property
    def height(self) -> int:
        return int(self.geometry.split("x")[1])


config = Config()
