from app.models import User, Department
from tests.role_helpers import report_grant, grant as new_grant, DEFINITIONS
"""Department roles are independent from report delegates and system administration."""
from io import BytesIO

import openpyxl
import pytest
from sqlalchemy import select

from app.models import AuditLog, Department, MeetingSeries, ReportDepartmentGrant, Task, User
from app.schemas import MeetingDraft, PublishRequest
from app.services.access import can_edit_meeting, meeting_editor_department_ids
from app.services.business import create_meeting, publish, revision, save_meeting
from app.services.employee_roles import MEETING_ROLE, REPORT_ROLE, employee_roles
from app.services.report_access import report_access
from tests.test_department_people_report import issue
from tests.test_report_permissions import client  # noqa: F401
from tests.test_report_self_only import GROUPS, PARAMS, auth
from tests.role_helpers import change_fixed_fixture as change


@pytest.mark.parametrize("roles", [[], [REPORT_ROLE], [MEETING_ROLE], [REPORT_ROLE, MEETING_ROLE]])
def test_role_combinations_enforce_department_reports_editing_and_export_scope(db, team, client, roles):
    child = Department(name="下属组", parent_id=team["dep"].id)
    db.add(child)
    db.flush()
    member = User(name="下属员工", department_id=child.id)
    db.add(member)
    db.flush()
    own = issue(db, team, department=child, owners=[member.id])
    foreign = issue(db, team, department=team["other"], owners=[team["outsider"].id])
    actor = team["colleague"]
    headers = auth(actor)
    assert change(client, team["host"], actor, roles).status_code == 200
    assert report_access(db, actor).mode == ("departments" if REPORT_ROLE in roles else "self")
    detail = client.get(f"/api/meetings/{own.id}", headers=headers)
    assert detail.status_code == (200 if MEETING_ROLE in roles else 403)
    if MEETING_ROLE in roles:
        assert detail.json()["can_edit"] and not detail.json()["can_delete"]
    assert client.get(f"/api/meetings/{foreign.id}", headers=headers).status_code == 403
    assert client.get("/api/admin/config", headers=headers).status_code == 403
    assert client.get("/api/direct-assignments/options", headers=headers).json()["can_assign"] == (MEETING_ROLE in roles)
    assert client.delete(f"/api/meetings/{own.id}", headers=headers).status_code == 403
    for group in GROUPS:
        params = {**PARAMS, "group_by": group}
        response = client.get("/api/reports", params=params, headers=headers)
        assert response.status_code == 200
        assert {r["user_id"] for r in response.json()["details"]} == ({member.id} if REPORT_ROLE in roles else set())
        excel = client.get("/api/reports/export", params=params, headers=headers)
        assert excel.status_code == 200
        sheet = openpyxl.load_workbook(BytesIO(excel.content))["考核明细"]
        assert sheet.max_row == (2 if REPORT_ROLE in roles else 1)
        for endpoint in ("/api/reports", "/api/reports/export"):
            assert client.get(endpoint, params={**params, "person_id": team["outsider"].id}, headers=headers).status_code == 403


def test_meeting_role_can_revise_results_publish_continue_and_edit_tasks(db, team, client, draft_factory):
    actor = team["colleague"]
    assert change(client, team["host"], actor, [MEETING_ROLE]).status_code == 200
    source = issue(db, team, owners=[team["a"].id], status="未完成")
    source.series_id = team["series"].id
    db.flush()
    revision(db, actor, source.id, "修正完成结果")
    draft = MeetingDraft.model_validate(source.draft)
    draft.series_id = team["series"].id
    draft.items[0].status = "已完成"
    save_meeting(db, actor, source.id, draft, source.draft_version)
    publish(db, actor, source.id, PublishRequest(expected_version=source.draft_version, notify=False))
    assert source.published_version == 2
    headers = auth(actor)
    continuation = client.post(f"/api/meetings/{source.id}/continue", headers=headers)
    assert continuation.status_code == 200, continuation.text
    assert continuation.json()["host_id"] == team["host"].id
    assert continuation.json()["recorder_id"] == actor.id
    assert client.get(f"/api/meetings/{source.id}/deletion-impact", headers=headers).status_code == 403
    live = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], live.id, PublishRequest(expected_version=1, notify=False))
    task = client.get(f"/api/tasks/{live.draft['items'][0]['task_id']}", headers=headers)
    assert task.status_code == 200 and task.json()["can_edit"]
    assert change(client, team["host"], actor, [], [MEETING_ROLE]).status_code == 200
    assert not can_edit_meeting(db, actor, source)
    assert client.post(f"/api/meetings/{source.id}/revision", headers=headers, json={"reason": "撤销后尝试"}).status_code == 403


def test_meeting_role_can_edit_child_tasks_and_confirm_in_continued_minutes(db, team, client, draft_factory):
    actor = team["colleague"]
    child = Department(name="下属组", parent_id=team["dep"].id)
    db.add(child)
    db.flush()
    series = MeetingSeries(name="下属组周会", department_id=child.id)
    db.add(series)
    db.flush()
    draft = draft_factory()
    draft.department_id, draft.series_id = child.id, series.id
    source = create_meeting(db, team["host"], draft)
    publish(db, team["host"], source.id, PublishRequest(expected_version=1, notify=False))
    assert change(client, team["host"], actor, [MEETING_ROLE]).status_code == 200
    headers = auth(actor)
    task_id = source.draft["items"][0]["task_id"]
    task = client.get(f"/api/tasks/{task_id}", headers=headers).json()
    assert task["can_edit"] and not task["can_transfer"]
    changed = client.put(f"/api/tasks/{task_id}", headers=headers, json={
        "expected_version": task["version"], "content": "角色二调整下属任务",
        "due_date": "2026-08-18", "owner_ids": [team["a"].id],
        "verifier_id": team["host"].id, "reason": "部门任务调整",
    })
    assert changed.status_code == 200, changed.text
    assert changed.json()["content"] == "角色二调整下属任务"
    continuation = client.post(f"/api/meetings/{source.id}/continue", headers=headers)
    assert continuation.status_code == 200, continuation.text
    follow = continuation.json()
    follow["draft"]["meeting_date"] = "2026-08-19"
    follow["draft"]["items"][0]["status"] = "已完成"
    saved = client.put(f"/api/meetings/{follow['id']}", headers=headers,
                       json={"draft": follow["draft"], "expected_version": 1})
    assert saved.status_code == 200, saved.text
    issued = client.post(f"/api/meetings/{follow['id']}/publish", headers=headers,
                        json={"expected_version": saved.json()["draft_version"], "notify": False})
    assert issued.status_code == 200, issued.text
    assert db.get(Task, task_id).status == "关闭"
    assert report_access(db, actor).mode == "self"
    assert client.delete(f"/api/meetings/{source.id}", headers=headers).status_code == 403


def test_roles_follow_primary_department_and_union_only_report_delegate_scope(db, team, client):
    actor = team["colleague"]
    assert change(client, team["host"], actor, [REPORT_ROLE, MEETING_ROLE]).status_code == 200
    assert report_access(db, actor).departments == meeting_editor_department_ids(db, actor) == {team["dep"].id}
    actor.department_id = team["other"].id
    db.flush()
    assert report_access(db, actor).departments == meeting_editor_department_ids(db, actor) == {team["other"].id}
    db.add(report_grant(db,db.get(User,actor.id),db.get(Department,team["dep"].id)))
    db.flush()
    assert report_access(db, actor).departments == {team["dep"].id, team["other"].id}
    assert meeting_editor_department_ids(db, actor) == {team["other"].id}
    assert change(client, team["host"], actor, [], [REPORT_ROLE, MEETING_ROLE]).status_code == 200
    assert report_access(db, actor).departments == {team["dep"].id}
    assert not meeting_editor_department_ids(db, actor)
