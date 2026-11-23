"""Explicit department roles follow the employee's current primary department."""
from sqlalchemy import select

from app.models import EmployeeRoleGrant

REPORT_ROLE = "department_reports"
MEETING_ROLE = "department_meetings"
DISPATCH_ROLE = "company_dispatch"


def employee_roles(db, user_id):
    return set(db.scalars(select(EmployeeRoleGrant.role).where(EmployeeRoleGrant.user_id == user_id)))


def role_department_roots(db, user, role):
    if not user.active or not user.department_id:
        return set()
    return {user.department_id} if role in employee_roles(db, user.id) else set()
