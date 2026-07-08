from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import EventStatus


class EventBase(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    join_url: str = Field(min_length=1, description="Ссылка на конференцию")
    passcode: str | None = None
    host_key: str | None = Field(default=None, max_length=32)
    duration_min: int = Field(default=60, ge=1, le=1440)
    record: bool = False
    moderate: bool = True
    worker_slot: int = Field(default=0, ge=0, le=9, description="Слот воркера (вебинар)")


class EventCreate(EventBase):
    # Если start_now=True — запускаем немедленно, время проставит сервер.
    start_now: bool = False
    start_time: datetime | None = None

    @model_validator(mode="after")
    def _check_time(self):
        if not self.start_now and self.start_time is None:
            raise ValueError("Укажите время начала или включите режим «сейчас»")
        return self


class EventUpdate(BaseModel):
    """Частичное обновление — все поля опциональны."""

    title: str | None = Field(default=None, max_length=255)
    join_url: str | None = Field(default=None, min_length=1)
    passcode: str | None = None
    host_key: str | None = Field(default=None, max_length=32)
    start_time: datetime | None = None
    duration_min: int | None = Field(default=None, ge=1, le=1440)
    record: bool | None = None
    moderate: bool | None = None
    worker_slot: int | None = Field(default=None, ge=0, le=9)
    status: EventStatus | None = None


class EventOut(EventBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    start_time: datetime
    status: EventStatus
    created_at: datetime
    updated_at: datetime
