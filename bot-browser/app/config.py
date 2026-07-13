import os


class Config:
    bot_name: str = os.environ.get("BOT_NAME", "Веб-модератор")
    # Куда сообщать события модерации (web-сервис), необязательно.
    callback_url: str = os.environ.get("WEB_CALLBACK_URL", "")
    internal_token: str = os.environ.get("INTERNAL_API_TOKEN", "")
    # Директория для отладочных скриншотов.
    shots_dir: str = os.environ.get("SHOTS_DIR", "/data")


config = Config()
