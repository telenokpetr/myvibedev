"""Тесты планировщика: запуск по времени, один активный за раз, завершение.

bot-worker замокан — проверяем чистую оркестрацию над SQLite (StaticPool, чтобы
данные жили между вызовами tick).
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.database as database
from app import models, scheduler
from app.database import Base
from app.models import Event, EventStatus


class FakeWorker:
    """Один слот сессии, как реальный bot-worker."""

    def __init__(self):
        self.active = None

    def join(self, ev):
        if self.active:
            return "busy"
        self.active = ev.id
        return "ok"

    def status(self):
        return {"status": "live"} if self.active else {"status": "idle"}

    def leave(self):
        self.active = None
        return True


@pytest.fixture
def env(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", Session)
    monkeypatch.setattr(scheduler, "SessionLocal", Session)

    worker = FakeWorker()
    monkeypatch.setattr(scheduler.bot_client, "join", worker.join)
    monkeypatch.setattr(scheduler.bot_client, "status", worker.status)
    monkeypatch.setattr(scheduler.bot_client, "leave", worker.leave)
    return Session, worker


def _add(Session, title, minutes_ago=1, duration=60, status=EventStatus.scheduled,
         tz_aware=True):
    db = Session()
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=minutes_ago)
    if not tz_aware:
        start = start.replace(tzinfo=None)
    ev = Event(title=title, join_url="https://zoom.us/j/1", host_key="1",
               start_time=start, duration_min=duration, status=status)
    db.add(ev); db.commit(); db.refresh(ev); db.close()
    return ev.id


def _status(Session, eid):
    db = Session()
    st = db.get(Event, eid).status
    db.close()
    return st


def test_due_event_starts(env):
    Session, _ = env
    eid = _add(Session, "Лекция")
    scheduler.tick()
    assert _status(Session, eid) == EventStatus.joining


def test_only_one_active_at_a_time(env):
    Session, _ = env
    e1 = _add(Session, "Первое")
    e2 = _add(Session, "Второе")
    scheduler.tick()
    assert _status(Session, e1) == EventStatus.joining
    assert _status(Session, e2) == EventStatus.scheduled  # ждёт освобождения воркера


def test_finishes_when_worker_idle(env):
    Session, worker = env
    eid = _add(Session, "Лекция")
    scheduler.tick()          # joining
    scheduler.tick()          # live
    worker.leave()            # воркер вышел из сессии
    scheduler.tick()          # -> finished
    assert _status(Session, eid) == EventStatus.finished


def test_finishes_after_duration(env):
    Session, _ = env
    # началось 120 мин назад, длительность 60 → время истекло
    eid = _add(Session, "Долгая", minutes_ago=120, duration=60)
    scheduler.tick()          # joining
    scheduler.tick()          # end < now -> finished
    assert _status(Session, eid) == EventStatus.finished


def test_naive_start_time_does_not_crash(env):
    # Регресс: tz-naive start_time (как из SQLite) не должен ронять тик.
    Session, _ = env
    eid = _add(Session, "Naive", minutes_ago=120, duration=60, tz_aware=False)
    scheduler.tick()
    scheduler.tick()
    assert _status(Session, eid) == EventStatus.finished
