"""Планировщик: раз в несколько секунд проверяет расписание и командует bot-worker.

Правила:
- одновременно активно не более одного мероприятия (bot-worker обслуживает одну сессию);
- мероприятие стартует, когда наступило его время (start_time <= now) и воркер свободен;
- активное мероприятие синхронизирует статус с воркером;
- по истечении длительности бот выходит, мероприятие помечается завершённым.
"""

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from app import bot_client, models
from app.database import SessionLocal
from app.models import EventStatus

log = logging.getLogger("scheduler")

TICK_SECONDS = 10
ACTIVE = (
    EventStatus.joining,
    EventStatus.claiming,
    EventStatus.live,
    EventStatus.recording,
)
# Статусы воркера, которые переносим напрямую в статус мероприятия.
_SYNC = {"joining", "claiming", "live", "recording", "error"}


def _active_event(db):
    stmt = select(models.Event).where(models.Event.status.in_(ACTIVE))
    return db.scalars(stmt).first()


def _due_event(db, now):
    stmt = (
        select(models.Event)
        .where(models.Event.status == EventStatus.scheduled)
        .where(models.Event.start_time <= now)
        .order_by(models.Event.start_time)
    )
    return db.scalars(stmt).first()


def tick():
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        active = _active_event(db)

        if active:
            # start_time может прийти tz-naive (напр. из SQLite) — считаем UTC,
            # иначе сравнение с now (aware) роняет тик и событие виснет в joining.
            start = active.start_time
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            end = start + timedelta(minutes=active.duration_min)
            st = bot_client.status()

            # Завершение по времени или если воркер уже не в сессии.
            worker_done = st is not None and st.get("status") in ("idle", "finished")
            if now >= end or worker_done:
                bot_client.leave()
                active.status = EventStatus.finished
                log.info("мероприятие #%s завершено", active.id)
            elif st and st.get("status") in _SYNC:
                active.status = EventStatus(st["status"])
            db.commit()
            return  # одно активное за раз

        due = _due_event(db, now)
        if due:
            log.info("запускаю мероприятие #%s: %s", due.id, due.join_url)
            due.status = EventStatus.joining
            db.commit()
            result = bot_client.join(due)
            if result == "busy":
                due.status = EventStatus.scheduled  # попробуем на следующем тике
            elif result == "error":
                due.status = EventStatus.error
            db.commit()
    except Exception as exc:  # noqa: BLE001
        log.exception("ошибка тика планировщика: %s", exc)
    finally:
        db.close()


_scheduler: BackgroundScheduler | None = None


def start():
    global _scheduler
    if _scheduler:
        return
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(tick, "interval", seconds=TICK_SECONDS, id="tick",
                       max_instances=1, coalesce=True)
    _scheduler.start()
    log.info("планировщик запущен (тик %sс)", TICK_SECONDS)


def stop():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
