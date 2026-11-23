"""System administrators manage explicit department leader scopes."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import admin_user
from app.models import Department, User
from app.services.business import audit

router = APIRouter(prefix="/api")


@router.get("/admin/department-leaders")
def grants(db: Session = Depends(get_db), user: User = Depends(admin_user)):
    # JSON (rather than JSONB) is used in existing deployments. Filter in Python
    # instead of relying on a PostgreSQL JSON equality operator.
    return [
        {"user_id": person.id, "department_ids": sorted(person.manual_leader_department_ids)}
        for person in db.scalars(select(User).order_by(User.name, User.id))
        if person.manual_leader_department_ids
    ]


class DepartmentLeaderChange(BaseModel):
    department_ids: list[str] = Field(max_length=500)
    expected_department_ids: list[str] = Field(max_length=500)
    reason: str = Field(min_length=1, max_length=1000)


@router.put("/admin/users/{identifier}/department-leader")
def change_grants(
    identifier: str,
    body: DepartmentLeaderChange,
    db: Session = Depends(get_db),
    user: User = Depends(admin_user),
):
    raise HTTPException(410, "请在角色管理中配置权限，并从员工通讯录分配角色")
    locked = list(
        db.scalars(
            select(User)
            .where(User.id.in_({user.id, identifier}))
            .order_by(User.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    actor = next((person for person in locked if person.id == user.id), None)
    if not actor or not actor.active or not actor.is_admin:
        raise HTTPException(403, "管理员权限已变化，请刷新")
    target = next((person for person in locked if person.id == identifier), None)
    if not target:
        raise HTTPException(404, "人员不存在")
    existing = set(target.manual_leader_department_ids or [])
    if existing != set(body.expected_department_ids):
        raise HTTPException(409, "部门负责人授权已变化，请重新加载后再保存")
    desired = set(body.department_ids)
    if desired and not target.active:
        raise HTTPException(422, "已停用人员不能授予部门负责人权限")
    if desired and target.is_admin:
        raise HTTPException(422, "系统管理员已有全部权限，无需单独授权")
    if not body.reason.strip():
        raise HTTPException(422, "请填写授权变更原因")
    if desired - set(db.scalars(select(Department.id).where(Department.id.in_(desired)))):
        raise HTTPException(422, "部门不存在，请刷新通讯录")
    target.manual_leader_department_ids = sorted(desired)
    audit(
        db,
        actor,
        "user.department_leader",
        target,
        before={"department_ids": sorted(existing)},
        after={"department_ids": sorted(desired)},
        reason=body.reason.strip(),
    )
    db.flush()
    return {"user_id": target.id, "department_ids": sorted(desired)}
