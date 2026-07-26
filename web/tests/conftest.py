"""Общая фикстура: изолированное FastAPI-приложение с событийным роутером
поверх SQLite in-memory. Не поднимаем полный main.app, чтобы не запускать
lifespan (Postgres + планировщик + сеть к bot-worker) в юнит-тестах.
"""

import os

# Подменяем БД на SQLite ДО импорта app.* — иначе app.database создаст движок
# Postgres (нужен psycopg2 и живой сервер) прямо на импорте.
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401  (регистрирует таблицы в metadata)
from app.database import Base, get_db
from app.routers import events


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # одна общая in-memory БД на все соединения
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(events.router)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as c:
        yield c
