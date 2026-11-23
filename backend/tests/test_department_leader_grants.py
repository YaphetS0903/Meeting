"""Manual leader grants affect dispatch, meetings and reports without editor rights."""
from io import BytesIO

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import get_db
from app.main import app
from app.models import AuditLog, Membership, ReportDepartmentGrant, User
from app.services.access import assignment_department_ids, meeting_department_scope
from app.services.report_access import report_access
from tests.test_department_meeting_visibility import nested_meetings
from tests.test_department_people_report import issue
from tests.test_report_self_only import GROUPS, PARAMS, auth


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as connection:
            yield connection
    finally:
        app.dependency_overrides.clear()


def change(client, actor, target, departments, previous=(), reason="管理员指定部门负责人"):
    return client.put(f"/api/admin/users/{target.id}/department-leader", headers=auth(actor), json={
        "department_ids": departments, "expected_department_ids": list(previous), "reason": reason,
    })


def dispatch(client, actor, owner, verifier=None):
    from uuid import uuid4

    return client.post("/api/direct-assignments", headers=auth(actor), json={
        "idempotency_key": uuid4().hex, "content": "隔离数据库派发验证", "owner_ids": [owner.id],
        "verifier_id": (verifier or actor).id, "due_date": "2026-12-01",
    })
