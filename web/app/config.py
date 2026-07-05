from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg2://zoombot:change_me@db:5432/zoombot"
    redis_url: str = "redis://redis:6379/0"

    # Zoom (этап 3)
    zoom_account_id: str = ""
    zoom_client_id: str = ""
    zoom_client_secret: str = ""

    # Bot-worker
    bot_worker_url: str = "http://bot-worker:9000"
    internal_api_token: str = "change_me_too"


settings = Settings()
