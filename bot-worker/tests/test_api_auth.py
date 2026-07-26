"""Аутентификация управляющего API bot-worker (порт 9000).

Все эндпоинты, кроме /health, требуют общий секрет в заголовке X-Internal-Token —
иначе любой в docker-сети мог бы командовать ботом.
"""

from fastapi.testclient import TestClient

from app.config import config
from app.main import app

client = TestClient(app)


def test_health_is_open():
    # /health не требует токена (может вернуть 503 без дисплея, но не 401).
    assert client.get("/health").status_code != 401


def test_control_endpoint_requires_token():
    r = client.post("/moderation/test", json={"text": "привет"})
    assert r.status_code == 401


def test_control_endpoint_rejects_wrong_token():
    r = client.post(
        "/moderation/test", json={"text": "привет"},
        headers={"X-Internal-Token": "wrong"},
    )
    assert r.status_code == 401


def test_control_endpoint_accepts_valid_token():
    r = client.post(
        "/moderation/test", json={"text": "добрый день"},
        headers={"X-Internal-Token": config.internal_api_token},
    )
    assert r.status_code == 200
    assert r.json() == {"detected": False}
