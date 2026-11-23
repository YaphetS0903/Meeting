from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import current_user
from app.models import User
from app.services.direct_reporting import direct_report, export_direct_report
from app.services.performance_reporting import export_performance_report, performance_report
from app.services.report_access import report_export_access

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/direct")
def statistics(
    start: date, end: date, department_id: str | None = None, person_id: str | None = None,
    status: str | None = None, include_details: bool = False, details_only: bool = False,
    offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db), user: User = Depends(current_user),
):
    return direct_report(
        db, user, start, end, department_id, person_id, status,
        include_details=include_details, details_only=details_only, offset=offset, limit=limit,
    )


@router.get("/direct/export")
def export(
    start: date, end: date, department_id: str | None = None, person_id: str | None = None,
    db: Session = Depends(get_db), user: User = Depends(current_user),
):
    report_export_access(db, user, department_id, person_id)
    data = direct_report(db, user, start, end, department_id, person_id, all_details=True)
    return Response(
        export_direct_report(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote("直接派发任务统计.xlsx")},
    )


@router.get("/performance")
def performance_statistics(
    start: date, end: date, department_id: str | None = None, person_id: str | None = None,
    db: Session = Depends(get_db), user: User = Depends(current_user),
):
    return performance_report(db, user, start, end, department_id, person_id)


@router.get("/performance/export")
def performance_export(
    start: date, end: date, department_id: str | None = None, person_id: str | None = None,
    db: Session = Depends(get_db), user: User = Depends(current_user),
):
    report_export_access(db, user, department_id, person_id)
    data = performance_report(db, user, start, end, department_id, person_id)
    return Response(
        export_performance_report(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote("绩效统计.xlsx")},
    )
