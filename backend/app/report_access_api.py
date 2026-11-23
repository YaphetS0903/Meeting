from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import admin_user, current_user
from app.models import Department, ReportDepartmentGrant, User
from app.services.business import audit
from app.services.report_access import report_directory

router = APIRouter(prefix="/api")


@router.get("/reports/access")
def access_options(
    senior_only: bool = False, leader_id: str | None = None,
    db: Session = Depends(get_db), user: User = Depends(current_user)
):
    return report_directory(db, user, senior_only=senior_only, leader_id=leader_id)


@router.get("/admin/report-access")
def grants(db: Session = Depends(get_db), user: User = Depends(admin_user)):
    by_user = {}
    for person_id, department_id in db.execute(
        select(ReportDepartmentGrant.user_id, ReportDepartmentGrant.department_id).order_by(
            ReportDepartmentGrant.user_id, ReportDepartmentGrant.department_id
        )
    ):
        by_user.setdefault(person_id, []).append(department_id)
    return [{"user_id": identifier, "department_ids": ids} for identifier, ids in by_user.items()]


class ReportGrantChange(BaseModel):
    department_ids: list[str] = Field(max_length=500)
    expected_department_ids: list[str] = Field(max_length=500)
    reason: str = Field(min_length=1, max_length=1000)


@router.put("/admin/users/{identifier}/report-access")
def change_grants(
    identifier: str, body: ReportGrantChange, db: Session = Depends(get_db), user: User = Depends(admin_user)
):
    raise HTTPException(410, "请在角色管理中配置权限，并从员工通讯录分配角色")
    # Serialize changes with role management and recheck the actor after locking.
    locked = list(
        db.scalars(
            select(User)
            .where(User.id.in_({user.id, identifier}))
            .order_by(User.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    actor = next((p for p in locked if p.id == user.id), None)
    if not actor or not actor.active or not actor.is_admin:
        raise HTTPException(403, "管理员权限已变化，请刷新")
    target = next((p for p in locked if p.id == identifier), None)
    if not target:
        raise HTTPException(404, "人员不存在")
    desired = set(body.department_ids)
    existing = set(
        db.scalars(
            select(ReportDepartmentGrant.department_id).where(ReportDepartmentGrant.user_id == identifier)
        )
    )
    if existing != set(body.expected_department_ids):
        raise HTTPException(409, "报表授权已变化，请刷新后重试")
    if desired and not target.active:
        raise HTTPException(422, "已停用人员不能授予报表权限")
    if desired and target.is_admin:
        raise HTTPException(422, "系统管理员已有全部报表权限，无需单独授权")
    if not body.reason.strip():
        raise HTTPException(422, "请填写授权变更原因")
    if desired - set(db.scalars(select(Department.id).where(Department.id.in_(desired)))):
        raise HTTPException(422, "部门不存在，请刷新通讯录")
    db.execute(delete(ReportDepartmentGrant).where(ReportDepartmentGrant.user_id == identifier))
    db.add_all(ReportDepartmentGrant(user_id=identifier, department_id=d) for d in sorted(desired))
    audit(
        db,
        actor,
        "user.report_access",
        target,
        before={"department_ids": sorted(existing)},
        after={"department_ids": sorted(desired)},
        reason=body.reason.strip(),
    )
    db.flush()
    return {"user_id": identifier, "department_ids": sorted(desired)}
