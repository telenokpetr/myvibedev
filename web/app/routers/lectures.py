from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db

router = APIRouter(prefix="/api/lectures", tags=["lectures"])


@router.get("", response_model=list[schemas.LectureOut])
def list_lectures(db: Session = Depends(get_db)):
    stmt = select(models.Lecture).order_by(models.Lecture.start_time)
    return list(db.scalars(stmt))


@router.post("", response_model=schemas.LectureOut, status_code=status.HTTP_201_CREATED)
def create_lecture(data: schemas.LectureCreate, db: Session = Depends(get_db)):
    lecture = models.Lecture(**data.model_dump())
    db.add(lecture)
    db.commit()
    db.refresh(lecture)
    return lecture


@router.get("/{lecture_id}", response_model=schemas.LectureOut)
def get_lecture(lecture_id: int, db: Session = Depends(get_db)):
    lecture = db.get(models.Lecture, lecture_id)
    if lecture is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Лекция не найдена")
    return lecture


@router.patch("/{lecture_id}", response_model=schemas.LectureOut)
def update_lecture(
    lecture_id: int, data: schemas.LectureUpdate, db: Session = Depends(get_db)
):
    lecture = db.get(models.Lecture, lecture_id)
    if lecture is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Лекция не найдена")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(lecture, field, value)
    db.commit()
    db.refresh(lecture)
    return lecture


@router.delete("/{lecture_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lecture(lecture_id: int, db: Session = Depends(get_db)):
    lecture = db.get(models.Lecture, lecture_id)
    if lecture is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Лекция не найдена")
    db.delete(lecture)
    db.commit()
