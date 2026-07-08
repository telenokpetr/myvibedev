import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EventStatus(str, enum.Enum):
    scheduled = "scheduled"   # запланировано, ждёт времени
    joining = "joining"       # бот подключается
    claiming = "claiming"     # забирает роль организатора (host key)
    live = "live"             # бот в конференции
    recording = "recording"   # идёт запись
    finished = "finished"     # завершено
    canceled = "canceled"     # отменено вручную
    error = "error"           # ошибка входа/захвата хоста


class Event(Base):
    """Мероприятие: бот заходит по ссылке, забирает хоста по host key, ведёт запись/модерацию."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Ссылка на конференцию (её создаёт организатор — мы только присоединяемся).
    join_url: Mapped[str] = mapped_column(Text)
    passcode: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Код организатора (host key) — чтобы бот стал хостом/со-хостом.
    host_key: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Время старта (UTC). Для режима «сейчас» ставится текущее время.
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_min: Mapped[int] = mapped_column(Integer, default=60)

    # Поведение бота.
    record: Mapped[bool] = mapped_column(Boolean, default=False)
    moderate: Mapped[bool] = mapped_column(Boolean, default=True)

    # На каком воркере (слоте) вести мероприятие — для параллельных вебинаров.
    worker_slot: Mapped[int] = mapped_column(Integer, default=0, index=True)

    status: Mapped[EventStatus] = mapped_column(
        Enum(EventStatus, native_enum=False, length=20),
        default=EventStatus.scheduled,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ModerationEvent(Base):
    """Событие модерации чата (нарушение + действие бота)."""

    __tablename__ = "moderation_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    sender: Mapped[str] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(20))   # profanity | spam
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(20))     # muted | deleted | flagged
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
