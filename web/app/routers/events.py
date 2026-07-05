from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("", response_model=list[schemas.EventOut])
def list_events(db: Session = Depends(get_db)):
    stmt = select(models.Event).order_by(models.Event.start_time)
    return list(db.scalars(stmt))


@router.post("", response_model=schemas.EventOut, status_code=status.HTTP_201_CREATED)
def create_event(data: schemas.EventCreate, db: Session = Depends(get_db)):
    payload = data.model_dump(exclude={"start_now"})
    if data.start_now:
        payload["start_time"] = datetime.now(timezone.utc)
    event = models.Event(**payload)
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@router.get("/{event_id}", response_model=schemas.EventOut)
def get_event(event_id: int, db: Session = Depends(get_db)):
    event = db.get(models.Event, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Мероприятие не найдено")
    return event


@router.patch("/{event_id}", response_model=schemas.EventOut)
def update_event(
    event_id: int, data: schemas.EventUpdate, db: Session = Depends(get_db)
):
    event = db.get(models.Event, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Мероприятие не найдено")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(event, field, value)
    db.commit()
    db.refresh(event)
    return event


@router.post("/{event_id}/start-now", response_model=schemas.EventOut)
def start_now(event_id: int, db: Session = Depends(get_db)):
    """Запустить сейчас: сдвигает время старта на текущий момент.

    Реально бот подключится, когда будет готов планировщик (этап 4) и
    bot-worker (этап 5). Пока это меняет расписание.
    """
    event = db.get(models.Event, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Мероприятие не найдено")
    event.start_time = datetime.now(timezone.utc)
    event.status = models.EventStatus.scheduled
    db.commit()
    db.refresh(event)
    return event


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_event(event_id: int, db: Session = Depends(get_db)):
    event = db.get(models.Event, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Мероприятие не найдено")
    db.delete(event)
    db.commit()
