"""Administrator-only versioned role definitions and multi-role employee assignments."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.core.db import get_db
from app.core.security import admin_user
from app.models import BusinessRole, BusinessRoleAssignment, Department, User
from app.services.business import audit
from app.services.role_permissions import ACTION_KEYS, CATALOG

router = APIRouter(prefix="/api/admin")


class RuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    module: str
    actions: list[str] = Field(max_length=30)
    scope: Literal["self", "department", "selected", "all"]
    department_ids: list[str] = Field(default_factory=list, max_length=500)


class RoleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    active: bool = True
    rules: list[RuleInput] = Field(max_length=8)
    expected_version: int | None = Field(default=None, ge=1)


def normalized_rules(db, body):
    rules = []
    seen = set()
    for item in body.rules:
        if item.module not in ACTION_KEYS or item.module in seen:
            raise HTTPException(422, "权限模块不存在或重复")
        seen.add(item.module)
        actions = set(item.actions)
        if not actions <= ACTION_KEYS[item.module]:
            raise HTTPException(422, "存在未知权限，不能保存")
        if not actions:
            continue
        if item.module == "meetings" and "assess" in actions:
            actions.add("edit")
        if item.module == "meetings" and "publish" in actions:
            actions.add("preview")
        ids = set(item.department_ids) if item.scope == "selected" else set()
        if item.scope == "selected" and not ids:
            raise HTTPException(422, "请为指定部门范围选择至少一个部门")
        if ids - set(db.scalars(select(Department.id).where(Department.id.in_(ids)))):
            raise HTTPException(422, "所选部门不存在，请刷新通讯录")
        rules.append(
            {
                "module": item.module,
                "actions": sorted(actions),
                "scope": item.scope,
                "department_ids": sorted(ids),
            }
        )
    return sorted(rules, key=lambda r: r["module"])


def describe(db, role):
    return {
        "id": role.id,
        "name": role.name,
        "description": role.description,
        "active": role.active,
        "version": role.version,
        "rules": role.rules,
        "member_count": db.scalar(
            select(func.count())
            .select_from(BusinessRoleAssignment)
            .where(BusinessRoleAssignment.role_id == role.id)
        ),
    }


@router.get("/role-catalog")
def catalog(user=Depends(admin_user)):
    return CATALOG


@router.get("/roles")
def roles(db=Depends(get_db), user=Depends(admin_user)):
    return [
        describe(db, role)
        for role in db.scalars(select(BusinessRole).order_by(BusinessRole.created_at, BusinessRole.id))
    ]


@router.get("/roles/{identifier}/members")
def members(identifier: str, db=Depends(get_db), user=Depends(admin_user)):
    if not db.get(BusinessRole, identifier):
        raise HTTPException(404, "角色不存在")
    return [
        {"id": p.id, "name": p.name, "department_id": p.department_id, "active": p.active}
        for p in db.scalars(
            select(User)
            .join(BusinessRoleAssignment, BusinessRoleAssignment.user_id == User.id)
            .where(BusinessRoleAssignment.role_id == identifier)
            .order_by(User.active.desc(), User.name, User.id)
        )
    ]


def save_role(db, user, body, identifier=None):
    # A lock on the actor serializes role changes and assignment writes.
    actor = db.scalar(
        select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True)
    )
    if not actor or not actor.active or not actor.is_admin:
        raise HTTPException(403, "管理员权限已变化")
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "请输入角色名称")
    rules = normalized_rules(db, body)
    if identifier:
        role = db.scalar(
            select(BusinessRole)
            .where(BusinessRole.id == identifier)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if not role:
            raise HTTPException(404, "角色不存在")
        if role.version != body.expected_version:
            raise HTTPException(409, "角色已被修改，请重新加载")
        before = describe(db, role)
    else:
        role = BusinessRole()
        before = None
    old = db.scalar(select(BusinessRole.id).where(BusinessRole.name == name))
    if old and old != role.id:
        raise HTTPException(409, "角色名称已存在")
    if not identifier:
        db.add(role)
    role.name, role.description, role.rules, role.active = name, body.description.strip(), rules, body.active
    role.version = (role.version or 0) + 1
    try:
        db.flush()
    except IntegrityError as error:
        raise HTTPException(409, "角色名称已存在，请重新命名") from error
    after = describe(db, role)
    audit(db, actor, "role.update" if identifier else "role.create", role, before=before, after=after)
    db.flush()
    return after


@router.post("/roles", status_code=201)
def create(body: RoleInput, db=Depends(get_db), user=Depends(admin_user)):
    return save_role(db, user, body)


@router.put("/roles/{identifier}")
def update(identifier: str, body: RoleInput, db=Depends(get_db), user=Depends(admin_user)):
    return save_role(db, user, body, identifier)


class RoleDeleteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    expected_member_count: int = Field(ge=0)


@router.delete("/roles/{identifier}")
def remove(identifier: str, body: RoleDeleteInput, db=Depends(get_db), user=Depends(admin_user)):
    actor = db.scalar(
        select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True)
    )
    if not actor or not actor.active or not actor.is_admin:
        raise HTTPException(403, "管理员权限已变化")
    role = db.scalar(
        select(BusinessRole)
        .where(BusinessRole.id == identifier)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not role:
        raise HTTPException(404, "角色不存在，请刷新列表")
    member_ids = sorted(
        db.scalars(select(BusinessRoleAssignment.user_id).where(BusinessRoleAssignment.role_id == identifier))
    )
    if role.version != body.expected_version or len(member_ids) != body.expected_member_count:
        raise HTTPException(409, "角色配置或已分配人数已变化，请重新加载后确认删除")
    before = {**describe(db, role), "member_ids": member_ids}
    db.execute(delete(BusinessRoleAssignment).where(BusinessRoleAssignment.role_id == identifier))
    audit(db, actor, "role.delete", role, before=before, after={"deleted": True})
    db.delete(role)
    db.flush()
    db.info.pop("business_role_cache", None)
    return {"id": identifier, "deleted": True, "revoked_assignments": len(member_ids)}


@router.get("/role-assignments")
def assignments(db=Depends(get_db), user=Depends(admin_user)):
    result = {}
    for person, role in db.execute(select(BusinessRoleAssignment.user_id, BusinessRoleAssignment.role_id)):
        result.setdefault(person, []).append(role)
    return [{"user_id": person, "roles": sorted(ids)} for person, ids in result.items()]


class AssignmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    roles: list[str] = Field(max_length=100)
    expected_roles: list[str] = Field(max_length=100)
    expected_department_id: str | None


@router.put("/users/{identifier}/business-roles")
def assign(identifier: str, body: AssignmentInput, db=Depends(get_db), user=Depends(admin_user)):
    locked = list(
        db.scalars(
            select(User)
            .where(User.id.in_({identifier, user.id}))
            .order_by(User.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    actor = next((p for p in locked if p.id == user.id), None)
    person = next((p for p in locked if p.id == identifier), None)
    if not actor or not actor.active or not actor.is_admin:
        raise HTTPException(403, "管理员权限已变化")
    if not person:
        raise HTTPException(404, "员工不存在")
    existing = set(
        db.scalars(select(BusinessRoleAssignment.role_id).where(BusinessRoleAssignment.user_id == identifier))
    )
    desired = set(body.roles)
    if existing != set(body.expected_roles) or person.department_id != body.expected_department_id:
        raise HTTPException(409, "员工角色或所属部门已变化，请重新打开设置")
    added = desired - existing
    if added and (not person.active or person.is_admin):
        raise HTTPException(422, "仅可为在用普通员工添加角色")
    rows = list(
        db.scalars(
            select(BusinessRole)
            .where(BusinessRole.id.in_(desired))
            .order_by(BusinessRole.id)
            .with_for_update()
        )
    )
    if desired - {r.id for r in rows}:
        raise HTTPException(422, "角色不存在")
    if any(not r.active and r.id in added for r in rows):
        raise HTTPException(422, "不能添加已停用角色")
    if not person.department_id and any(
        r.id in added and any(rule["scope"] == "department" for rule in r.rules) for r in rows
    ):
        raise HTTPException(422, "请先配置员工所属部门，或选择其他范围的角色")
    db.execute(delete(BusinessRoleAssignment).where(BusinessRoleAssignment.user_id == identifier))
    db.add_all(BusinessRoleAssignment(user_id=identifier, role_id=r) for r in sorted(desired))
    audit(
        db,
        actor,
        "user.business_roles",
        person,
        before={"roles": sorted(existing)},
        after={"roles": sorted(desired)},
    )
    db.flush()
    db.info.pop("business_role_cache", None)
    return {"user_id": identifier, "roles": sorted(desired)}
