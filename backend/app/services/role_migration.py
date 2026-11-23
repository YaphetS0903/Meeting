"""Copy explicit legacy grants once, without deriving permissions from job titles."""

from collections import defaultdict

from sqlalchemy import select

from app.models import BusinessRole, BusinessRoleAssignment, EmployeeRoleGrant, ReportDepartmentGrant, User
from app.services.role_permissions import ACTION_KEYS


def rule(module, actions, scope="department", departments=()):
    return {"module": module, "actions": list(actions), "scope": scope, "department_ids": sorted(departments)}


def migrate_explicit_grants(db):
    if db.scalar(select(BusinessRole.id).limit(1)):
        raise RuntimeError("自定义角色已存在，不能重复迁移")
    definitions = {
        "department_reports": ("部门报表查看员", [rule("reports", ["view", "export"])]),
        "department_meetings": (
            "会议任务单管理员",
            [
                rule("series", ["view", "create"]),
                rule("meetings", sorted(ACTION_KEYS["meetings"] - {"rename", "move"})),
                rule("files", sorted(ACTION_KEYS["files"])),
                rule("overview", ["view"]),
                rule("dispatch", ["create", "view", "edit", "withdraw"]),
            ],
        ),
        "company_dispatch": (
            "派发任务单管理员",
            [
                rule("dispatch", ["create", "view", "edit", "withdraw"], "all"),
                rule("overview", ["view"], "self"),
            ],
        ),
    }
    roles = {}
    for key, (name, rules) in definitions.items():
        role = BusinessRole(name=name, description="由原显式角色迁移，可修改权限和范围", rules=rules)
        db.add(role)
        db.flush()
        roles[key] = role
    memberships = set()
    for person, key in db.execute(select(EmployeeRoleGrant.user_id, EmployeeRoleGrant.role)):
        memberships.add((person, roles[key].id))
    reports = defaultdict(set)
    for person, department in db.execute(
        select(ReportDepartmentGrant.user_id, ReportDepartmentGrant.department_id)
    ):
        reports[person].add(department)
    scoped = {}
    for person, roots in sorted(reports.items()):
        key = tuple(sorted(roots))
        if key not in scoped:
            role = BusinessRole(
                name=f"会议纪要统计员 · 原授权范围{len(scoped) + 1}",
                description="保留原报表授权的部门及下属组织，不扩大其他员工范围",
                rules=[
                    rule("reports", ["view", "export"], "selected", roots),
                    rule("senior_reports", ["view", "export"], "selected", roots),
                ],
            )
            db.add(role)
            db.flush()
            scoped[key] = role
        memberships.add((person, scoped[key].id))
    manual_count = 0
    for person in db.scalars(select(User).order_by(User.id)):
        roots = set(person.manual_leader_department_ids or [])
        if roots:
            manual_count += 1
            role = BusinessRole(
                name=f"原部门显式授权 · {person.name} · {person.id[:8]}",
                description="保留原手工部门授权，不使用职务推断",
                rules=[
                    rule("meetings", ["view"], "selected", roots),
                    rule("overview", ["view"], "selected", roots),
                    rule("dispatch", ["create", "view", "edit", "withdraw"], "selected", roots),
                    rule("reports", ["view", "export"], "selected", roots),
                ],
            )
            db.add(role)
            db.flush()
            memberships.add((person.id, role.id))
    for person, role_id in sorted(memberships):
        db.add(BusinessRoleAssignment(user_id=person, role_id=role_id))
    db.flush()
    return {"roles": len(roles) + len(scoped) + manual_count, "assignments": len(memberships)}
