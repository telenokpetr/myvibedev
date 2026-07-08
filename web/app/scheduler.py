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


def _active_events(db):
    stmt = select(models.Event).where(models.Event.status.in_(ACTIVE))
    return list(db.scalars(stmt))


def _due_events(db, now):
    stmt = (
        select(models.Event)
        .where(models.Event.status == EventStatus.scheduled)
        .where(models.Event.start_time <= now)
        .order_by(models.Event.start_time)
    )
    return list(db.scalars(stmt))


def tick():
    """Один тик: по каждому слоту — не более одного активного мероприятия.
    Параллельные вебинары идут на разных слотах (воркерах)."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        busy_slots: set[int] = set()

        # 1) Синхронизируем/завершаем активные мероприятия (по одному на слот).
        for ev in _active_events(db):
            slot = ev.worker_slot
            worker = bot_client.get_worker(slot)
            end = ev.start_time + timedelta(minutes=ev.duration_min)
            st = worker.status() if worker else None
            worker_done = st is not None and st.get("status") in ("idle", "finished")
            if now >= end or worker_done or worker is None:
                if worker:
                    worker.leave()
                ev.status = EventStatus.finished
                log.info("мероприятие #%s (слот %s) завершено", ev.id, slot)
            else:
                if st and st.get("status") in _SYNC:
                    ev.status = EventStatus(st["status"])
                busy_slots.add(slot)
        db.commit()

        # 2) Запускаем подошедшие мероприятия на свободных слотах.
        for ev in _due_events(db, now):
            slot = ev.worker_slot
            if slot in busy_slots:
                continue  # слот занят другим вебинаром — ждём следующего тика
            worker = bot_client.get_worker(slot)
            if worker is None:
                continue
            log.info("запускаю мероприятие #%s на слоте %s: %s", ev.id, slot, ev.join_url)
            ev.status = EventStatus.joining
            db.commit()
            result = worker.join(ev)
            if result == "busy":
                ev.status = EventStatus.scheduled
            elif result == "error":
                ev.status = EventStatus.error
            else:
                busy_slots.add(slot)
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
