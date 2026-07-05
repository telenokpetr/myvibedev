import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LectureStatus(str, enum.Enum):
    scheduled = "scheduled"   # запланирована, ждёт времени
    joining = "joining"       # бот подключается
    live = "live"             # идёт трансляция
    recording = "recording"   # идёт запись
    finished = "finished"     # завершена
    canceled = "canceled"     # отменена вручную
    error = "error"           # ошибка входа/запуска


class Lecture(Base):
    __tablename__ = "lectures"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))

    # Время начала (с таймзоной, храним в UTC) и длительность в минутах.
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_min: Mapped[int] = mapped_column(Integer, default=60)

    # Реквизиты митинга. Могут задаваться вручную или создаваться через Zoom API (этап 3).
    zoom_meeting_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    zoom_join_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    zoom_passcode: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Поведение бота.
    record: Mapped[bool] = mapped_column(Boolean, default=False)
    moderate: Mapped[bool] = mapped_column(Boolean, default=True)

    status: Mapped[LectureStatus] = mapped_column(
        Enum(LectureStatus, native_enum=False, length=20),
        default=LectureStatus.scheduled,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
