"""Only explicit report/department-leader grants widen an employee's reports."""
from io import BytesIO

import openpyxl
import pytest

from app.models import MeetingSeries
from app.services.report_access import report_directory
from tests.test_department_people_report import issue
from tests.test_report_permissions import client  # noqa: F401
from tests.test_report_self_only import GROUPS, PARAMS, auth


@pytest.mark.parametrize("role", ["host", "recorder", "position", "wecom_leader"])
def test_implicit_roles_never_expand_personal_statistics_details_topics_or_exports(db, team, client, role):
    actor = team["a"]
    issue(db, team, owners=[actor.id])
    issue(db, team, owners=[actor.id], status="未完成")
    other_topic = MeetingSeries(name="其他人员的会议主题", department_id=team["dep"].id)
    db.add(other_topic)
    db.flush()
    other_meeting = issue(db, team, owners=[team["b"].id])
    other_meeting.series_id = other_topic.id
    if role in {"host", "recorder"}:
        setattr(other_meeting, "host_id" if role == "host" else "recorder_id", actor.id)
    elif role == "position":
        actor.position = "部门部长"
    else:
        actor.leader_department_ids = [team["dep"].id]
    db.flush()
    headers = auth(actor)
    directory = client.get("/api/reports/access", headers=headers).json()
    assert directory["mode"] == "self"
    assert directory["department_ids"] == directory["departments"] == []
    assert {row["id"] for row in directory["users"]} == {actor.id}
    assert {row["key"] for row in directory["topics"]} == {"unassigned"}
    assert report_directory(db, team["host"])["mode"] == "all"

    for group in GROUPS:
        params = {**PARAMS, "group_by": group}
        response = client.get("/api/reports", params=params, headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["summary"]["items"] == 2
        assert data["summary"]["counts"]["已完成"] == data["summary"]["counts"]["未完成"] == 1
        assert {row["user_id"] for row in data["details"]} == {actor.id}
        excel = client.get("/api/reports/export", params=params, headers=headers)
        assert excel.status_code == 200
        sheet = openpyxl.load_workbook(BytesIO(excel.content))["考核明细"]
        assert sheet.max_row == 3
        assert {sheet.cell(row, 4).value for row in (2, 3)} == {actor.name}
        for endpoint in ("/api/reports", "/api/reports/export"):
            denied = client.get(endpoint, params={**params, "person_id": team["b"].id}, headers=headers)
            assert denied.status_code == 403
            assert denied.json()["detail"] == "只能查看本人的任务统计"
