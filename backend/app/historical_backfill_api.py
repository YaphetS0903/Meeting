from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import current_user
from app.models import User
from app.services.historical_backfill import task_options

router = APIRouter()


@router.get("/api/backfill-task-options")
def options(
    series_id: str,
    meeting_date: date,
    q: str = Query(default="", max_length=500),
    db: Session = Depends(get_db),
    actor: User = Depends(current_user),
):
    return {"items": task_options(db, actor, series_id, meeting_date, q)}
