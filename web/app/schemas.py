from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import LectureStatus


class LectureBase(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    start_time: datetime
    duration_min: int = Field(default=60, ge=1, le=1440)
    zoom_meeting_id: str | None = None
    zoom_join_url: str | None = None
    zoom_passcode: str | None = None
    record: bool = False
    moderate: bool = True


class LectureCreate(LectureBase):
    pass


class LectureUpdate(BaseModel):
    """Частичное обновление — все поля опциональны."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    start_time: datetime | None = None
    duration_min: int | None = Field(default=None, ge=1, le=1440)
    zoom_meeting_id: str | None = None
    zoom_join_url: str | None = None
    zoom_passcode: str | None = None
    record: bool | None = None
    moderate: bool | None = None
    status: LectureStatus | None = None


class LectureOut(LectureBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: LectureStatus
    created_at: datetime
    updated_at: datetime
