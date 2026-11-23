"""System administrators assign employee roles from the employee directory."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import admin_user
from app.models import Department, EmployeeRoleGrant, User
from app.services.business import audit
from app.services.employee_roles import MEETING_ROLE, REPORT_ROLE, employee_roles

router = APIRouter(prefix="/api")
Role = Literal["department_reports", "department_meetings", "company_dispatch"]


@router.get("/admin/employee-roles")
def grants(db: Session = Depends(get_db), user: User = Depends(admin_user)):
    by_user = {}
    for identifier, role in db.execute(
        select(EmployeeRoleGrant.user_id, EmployeeRoleGrant.role).order_by(
            EmployeeRoleGrant.user_id, EmployeeRoleGrant.role
        )
    ):
        by_user.setdefault(identifier, []).append(role)
    return [{"user_id": identifier, "roles": roles} for identifier, roles in by_user.items()]


class EmployeeRoleChange(BaseModel):
    roles: list[Role] = Field(max_length=3)
    expected_roles: list[Role] = Field(max_length=3)
    expected_department_id: str | None
    reason: str = Field(min_length=1, max_length=1000)


@router.put("/admin/users/{identifier}/employee-roles")
def change_roles(
    identifier: str, body: EmployeeRoleChange, db: Session = Depends(get_db), user: User = Depends(admin_user)
):
    raise HTTPException(410, "请在角色管理中配置权限，并从员工通讯录分配角色")
    people = list(
        db.scalars(
            select(User)
            .where(User.id.in_({user.id, identifier}))
            .order_by(User.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    actor = next((person for person in people if person.id == user.id), None)
    if not actor or not actor.active or not actor.is_admin:
        raise HTTPException(403, "管理员权限已变化，请刷新")
    target = next((person for person in people if person.id == identifier), None)
    if not target:
        raise HTTPException(404, "人员不存在")
    existing = employee_roles(db, target.id)
    desired = set(body.roles)
    if existing != set(body.expected_roles) or target.department_id != body.expected_department_id:
        raise HTTPException(409, "员工角色或所属部门已变化，请刷新通讯录后重试")
    if desired - existing and not target.active:
        raise HTTPException(422, "已停用员工不能授予新角色")
    if desired - existing and target.is_admin:
        raise HTTPException(422, "系统管理员已有全部权限，无需单独授权")
    if (desired - existing) & {REPORT_ROLE, MEETING_ROLE} and (
        not target.department_id or not db.get(Department, target.department_id)
    ):
        raise HTTPException(422, "请先为员工配置有效的所属部门")
    if not body.reason.strip():
        raise HTTPException(422, "请填写角色变更原因")
    db.execute(delete(EmployeeRoleGrant).where(EmployeeRoleGrant.user_id == target.id))
    db.add_all(EmployeeRoleGrant(user_id=target.id, role=role) for role in sorted(desired))
    audit(
        db,
        actor,
        "user.department_roles",
        target,
        before={"roles": sorted(existing), "department_id": target.department_id},
        after={"roles": sorted(desired), "department_id": target.department_id},
        reason=body.reason.strip(),
    )
    db.flush()
    db.commit()
    return {"user_id": target.id, "roles": sorted(desired)}
