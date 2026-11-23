from app.models import User, Department
from tests.role_helpers import report_grant, grant as new_grant, DEFINITIONS
from datetime import date
from io import BytesIO

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import get_db
from app.core.security import issue_token
from app.main import app
from app.models import (
    Department,
    EmployeeRoleGrant,
    MeetingSeries,
    MeetingVersion,
    ReportDepartmentGrant,
    User,
)
from app.schemas import MeetingDraft, PublishRequest
from app.services.business import create_meeting, publish, revision, save_meeting
from app.services.report_access import report_directory
from app.services.reporting import report
from app.services.senior_meetings import SENIOR_PARTICIPANTS, senior_meeting_ids

PARAMS = {"start": "2026-08-01", "end": "2026-08-31", "group_by": "department_people", "senior_only": True}


def auth(user):
    return {"Authorization": "Bearer " + issue_token(user)}


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def leaders(db, team):
    people = [User(id=identifier, name=name, department_id=team["other"].id)
              for identifier, name in SENIOR_PARTICIPANTS.items()]
    db.add_all(people)
    db.flush()
    return people


@pytest.fixture
def issue(db, team, draft_factory):
    def build(**changes):
        draft = draft_factory(section="review", status="已完成")
        for key, value in changes.items():
            setattr(draft, key, value)
        meeting = create_meeting(db, team["host"], draft, historical=True)
        publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
        db.flush()
        return meeting
    return build


@pytest.mark.parametrize("relation,expected", [("host", True), ("attendee", True), ("both", True),
                                               ("recorder", False), ("owner", False), ("verifier", False),
                                               ("cc", False), ("none", False)])
def test_only_published_host_or_attendees_qualify(db, team, leaders, issue, relation, expected):
    changes = {"host_id": leaders[0].id} if relation == "host" else {}
    if relation in {"attendee", "both"}:
        changes["attendee_ids"] = [u.id for u in leaders] if relation == "both" else [leaders[1].id]
    if relation == "recorder":
        changes["recorder_id"] = leaders[0].id
    if relation == "cc":
        changes["cc_ids"] = [leaders[0].id]
    meeting = issue(**changes)
    if relation in {"owner", "verifier"}:
        # Even task responsibility on a published snapshot is not participation.
        version = db.scalar(select(MeetingVersion).where(MeetingVersion.meeting_id == meeting.id))
        snapshot = {**version.snapshot, "items": [{**version.snapshot["items"][0],
                    ("owner_ids" if relation == "owner" else "verifier_id"):
                    [leaders[0].id] if relation == "owner" else leaders[0].id}]}
        version.snapshot = snapshot
        db.flush()
    result = report(db, team["host"], date(2026, 8, 1), date(2026, 8, 31),
                    group_by="department_people", senior_only=True)
    assert (meeting.id in senior_meeting_ids(db)) == expected
    assert result["senior_meeting_count"] == int(expected)
    assert result["summary"]["items"] == (2 if expected else 0)
    if not expected:
        assert result["groups"] == result["details"] == []


def test_person_meeting_names_are_unique_filtered_and_available_without_details(db, team, leaders, issue, draft_factory):
    from uuid import uuid4

    items = draft_factory(section="review", status="已完成").items
    items.append(items[0].model_copy(update={"id": str(uuid4()), "status": "未完成"}))
    first = issue(title="经营协调会", meeting_date=date(2026, 8, 3),
                  attendee_ids=[leaders[0].id], items=items)
    second = issue(title="重点项目会", meeting_date=date(2026, 8, 17), attendee_ids=[leaders[1].id])
    issue(title="普通会议")
    issue(title="上月会议", meeting_date=date(2026, 7, 3), attendee_ids=[leaders[0].id])
    result = report(db, team["host"], date(2026, 8, 1), date(2026, 8, 31),
                    group_by="department_people", senior_only=True, include_details=False)
    assert result["details"] == []
    assert len(result["groups"]) == 2
    for row in result["groups"]:
        assert row["items"] == 3
        assert row["meetings"] == [
            {"id": first.id, "title": "经营协调会", "meeting_date": "2026-08-03"},
            {"id": second.id, "title": "重点项目会", "meeting_date": "2026-08-17"},
        ]
    filtered = report(db, team["host"], date(2026, 8, 1), date(2026, 8, 31),
                      group_by="department_people", senior_only=True, include_details=False,
                      leader_id=leaders[0].id, person_id=team["a"].id, status="未完成")
    assert len(filtered["groups"]) == 1
    assert filtered["groups"][0]["items"] == 1
    assert filtered["groups"][0]["meetings"] == result["groups"][0]["meetings"][:1]
    regular = report(db, team["host"], date(2026, 8, 1), date(2026, 8, 31), group_by="department_people")
    for row in regular["groups"]:
        expected = {r["meeting_id"] for r in regular["details"] if r["user_id"] == row["id"]}
        assert {m["id"] for m in row["meetings"]} == expected


def test_draft_changes_namesakes_and_same_series_do_not_reclassify_published_meetings(db, team, leaders, issue):
    qualified = issue(attendee_ids=[leaders[0].id])
    regular = issue()
    leaders[0].name = "更名后的领导"
    namesake = User(name="朱慧铃", department_id=team["dep"].id)
    db.add(namesake)
    db.flush()
    namesake_meeting = issue(attendee_ids=[namesake.id])
    revision(db, team["host"], regular.id, "补充参会记录")
    draft = MeetingDraft.model_validate(regular.draft)
    draft.attendee_ids = [leaders[1].id]
    save_meeting(db, team["host"], regular.id, draft, regular.draft_version)
    assert senior_meeting_ids(db) == {qualified.id}
    publish(db, team["host"], regular.id, PublishRequest(expected_version=regular.draft_version, notify=False))
    assert senior_meeting_ids(db) == {qualified.id, regular.id}
    assert namesake_meeting.id not in senior_meeting_ids(db)
    revision(db, team["host"], qualified.id, "更正参会名单")
    draft = MeetingDraft.model_validate(qualified.draft)
    draft.attendee_ids = []
    save_meeting(db, team["host"], qualified.id, draft, qualified.draft_version)
    assert qualified.id in senior_meeting_ids(db)
    publish(db, team["host"], qualified.id, PublishRequest(expected_version=qualified.draft_version, notify=False))
    assert senior_meeting_ids(db) == {regular.id}


@pytest.mark.parametrize("role", ["employee", "department_reports", "department_meetings", "manual_leader", "host_recorder"])
def test_other_roles_cannot_access_module_data_directory_or_export(db, team, leaders, issue, client, role):
    actor = team["colleague"]
    if role in {"department_reports", "department_meetings"}:
        db.add(new_grant(db,db.get(User,actor.id),DEFINITIONS[role]))
    elif role == "manual_leader":
        actor.manual_leader_department_ids = [team["dep"].id]
    issue(attendee_ids=[leaders[0].id], **({"host_id": actor.id, "recorder_id": actor.id} if role == "host_recorder" else {}))
    db.flush()
    assert not report_directory(db, actor)["can_view_senior_meetings"]
    for path in ["/api/reports/access", "/api/reports", "/api/reports/export", "/api/reports/new-arrangements"]:
        assert client.get(path, params=PARAMS, headers=auth(actor)).status_code == 403
    # General statistics retain their original permissions.
    assert client.get("/api/reports", params={**PARAMS, "senior_only": False}, headers=auth(actor)).status_code == 200


def test_explicit_grant_uses_only_authorized_employees_including_cross_department_work(db, team, leaders, issue, client):
    actor = team["colleague"]
    child = Department(name="技术分组", parent_id=team["dep"].id)
    db.add(child)
    db.flush()
    team["a"].department_id = child.id
    db.add_all([report_grant(db,db.get(User,actor.id),db.get(Department,team["dep"].id)),
                new_grant(db,db.get(User,actor.id),DEFINITIONS["department_reports"])])
    # A separate general department role must not expand the privileged report.
    actor.department_id = team["other"].id
    allowed = issue(attendee_ids=[leaders[1].id], department_id=team["other"].id)
    own = issue(attendee_ids=[leaders[0].id])
    regular = issue()
    db.flush()
    params = {**PARAMS, "department_id": team["dep"].id}
    result = client.get("/api/reports", params=params, headers=auth(actor))
    assert result.status_code == 200
    data = result.json()
    assert data["summary"]["items"] == 4
    assert {r["meeting_id"] for r in data["details"]} == {allowed.id, own.id}
    assert regular.id not in {r["meeting_id"] for r in data["details"]}
    assert {r["id"] for r in data["groups"]} == {team["a"].id, team["b"].id}
    assert all({m["id"] for m in row["meetings"]} == {allowed.id, own.id} for row in data["groups"])
    selected = client.get("/api/reports", params={**params, "leader_id": leaders[1].id},
                          headers=auth(actor)).json()
    assert {m["id"] for m in selected["senior_meetings"]} == {allowed.id}
    assert {r["meeting_id"] for r in selected["details"]} == {allowed.id}
    assert all({m["id"] for m in row["meetings"]} == {allowed.id} for row in selected["groups"])
    assert team["outsider"].name not in str(selected)
    directory = client.get("/api/reports/access", params={"senior_only": True}, headers=auth(actor)).json()
    assert set(directory["department_ids"]) == {team["dep"].id, child.id}
    assert team["outsider"].id not in {p["id"] for p in directory["users"]}
    for path in ["/api/reports", "/api/reports/export"]:
        assert client.get(path, params={**PARAMS, "department_id": team["other"].id}, headers=auth(actor)).status_code == 403
        assert client.get(path, params={**PARAMS, "person_id": team["outsider"].id, "leader_id": leaders[1].id}, headers=auth(actor)).status_code == 403
    excel = client.get("/api/reports/export", params=params, headers=auth(actor))
    assert excel.status_code == 200
    book = openpyxl.load_workbook(BytesIO(excel.content))
    assert book["考核明细"].max_row == len(data["details"]) + 1
    assert int(book["考核汇总"]["B3"].value) == data["summary"]["items"]
    assert "Content-Disposition" in excel.headers


def test_topics_grant_revocation_and_current_published_results(db, team, leaders, issue, client):
    actor = team["colleague"]
    grant = report_grant(db,db.get(User,actor.id),db.get(Department,team["dep"].id))
    db.add(grant)
    separate = MeetingSeries(name="普通会议主题", department_id=team["dep"].id)
    db.add(separate)
    db.flush()
    meeting = issue(attendee_ids=[leaders[0].id])
    issue(series_id=separate.id)
    options = report_directory(db, actor, senior_only=True)
    assert {t["key"] for t in options["topics"]} == {f"series:{team['series'].id}"}
    revision(db, team["host"], meeting.id, "更新结果")
    draft = MeetingDraft.model_validate(meeting.draft)
    draft.items[0].status = "未完成"
    save_meeting(db, team["host"], meeting.id, draft, meeting.draft_version)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=meeting.draft_version, notify=False))
    data = client.get("/api/reports", params=PARAMS, headers=auth(actor)).json()
    assert data["summary"]["counts"]["已完成"] == 0
    assert data["summary"]["counts"]["未完成"] == 2
    assert data["senior_meeting_count"] == 1
    assert client.get("/api/reports", params={**PARAMS, "start": "2026-09-01", "end": "2026-09-30"},
                      headers=auth(actor)).json()["summary"]["items"] == 0
    db.delete(grant)
    db.flush()
    assert client.get("/api/reports", params=PARAMS, headers=auth(actor)).status_code == 403
    assert not report_directory(db, actor)["can_view_senior_meetings"]


def test_new_arrangements_without_assessments_are_counted_paginated_and_exported(db, team, leaders, draft_factory, client):
    from uuid import uuid4
    draft = draft_factory()
    draft.attendee_ids = [leaders[1].id]
    template = draft.items[0]
    draft.items = [template.model_copy(update={"id": str(uuid4()), "content": f"新安排 {n:02}",
                   "requirements": "逐项核对", "deliverable": "整改记录", "notes": "备注"}) for n in range(28)]
    meeting = create_meeting(db, team["host"], draft, historical=True)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    params = {**PARAMS, "series_key": f"series:{team['series'].id}"}
    result = client.get("/api/reports", params={**params, "include_details": False}, headers=auth(team["host"])).json()
    assert result["senior_published_meeting_count"] == 1
    assert result["senior_meeting_count"] == result["summary"]["items"] == 0
    assert result["summary"]["rate"] is None
    assert result["new_arrangements_count"] == 28  # Two co-owners do not duplicate a task row.
    assert result["new_arrangements"] == []
    pages = [client.get("/api/reports/new-arrangements", params={**params, "offset": offset, "limit": 20},
                        headers=auth(team["host"])).json() for offset in (0, 20)]
    assert [len(p["details"]) for p in pages] == [20, 8]
    rows = pages[0]["details"] + pages[1]["details"]
    assert len({r["id"] for r in rows}) == 28
    assert rows[0]["requirements"] == "逐项核对"
    assert rows[0]["deliverable"] == "整改记录"
    assert all(p["total"] == 28 for p in pages)
    book = openpyxl.load_workbook(BytesIO(client.get("/api/reports/export", params=params,
                                                    headers=auth(team["host"])).content))
    assert book["本次新安排"].max_row == 29
    assert int(book["考核汇总"]["B3"].value) == 0
    # An unpublished revision does not change arrangement counts or contents.
    revision(db, team["host"], meeting.id, "核对本次新安排")
    edited = MeetingDraft.model_validate(meeting.draft)
    edited.items = edited.items[:1]
    edited.items[0].content = "修改后的新安排"
    save_meeting(db, team["host"], meeting.id, edited, meeting.draft_version)
    assert client.get("/api/reports/new-arrangements", params=params,
                      headers=auth(team["host"])).json()["total"] == 28
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=meeting.draft_version, notify=False))
    current = client.get("/api/reports/new-arrangements", params=params, headers=auth(team["host"])).json()
    assert current["total"] == 1
    assert current["details"][0]["content"] == "修改后的新安排"


def test_new_arrangements_apply_employee_scope_without_disclosing_other_owners(db, team, leaders, draft_factory, client):
    from uuid import uuid4
    actor = team["colleague"]
    grant = report_grant(db,db.get(User,actor.id),db.get(Department,team["dep"].id))
    db.add(grant)
    draft = draft_factory()
    draft.department_id = team["other"].id
    draft.attendee_ids = [leaders[1].id]
    draft.items[0].owner_ids = [team["a"].id, team["outsider"].id]
    draft.items.append(draft.items[0].model_copy(update={"id": str(uuid4()),
                       "content": "无权查看的其他部门任务", "owner_ids": [team["outsider"].id]}))
    meeting = create_meeting(db, team["host"], draft, historical=True)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    result = client.get("/api/reports/new-arrangements", params=PARAMS, headers=auth(actor)).json()
    assert result["total"] == result["meeting_count"] == 1
    assert result["details"][0]["user_name"] == team["a"].name
    assert team["outsider"].name not in str(result)
    assert "无权查看的其他部门任务" not in str(result)
    for key, identifier in [("department_id", team["other"].id), ("person_id", team["outsider"].id)]:
        assert client.get("/api/reports/new-arrangements", params={**PARAMS, key: identifier},
                          headers=auth(actor)).status_code == 403
    mine = client.get("/api/reports/new-arrangements", params={**PARAMS, "person_id": team["b"].id},
                      headers=auth(actor)).json()
    assert mine["total"] == mine["meeting_count"] == 0
    db.delete(grant)
    db.flush()
    assert client.get("/api/reports/new-arrangements", params=PARAMS, headers=auth(actor)).status_code == 403


def test_new_arrangements_respect_dates_series_and_publication(db, team, leaders, draft_factory, client):
    for senior, when, published in [(True, date(2026, 8, 10), True),
                                    (False, date(2026, 8, 10), True),
                                    (True, date(2026, 9, 10), True),
                                    (True, date(2026, 8, 10), False)]:
        draft = draft_factory(when=when)
        draft.attendee_ids = [leaders[0].id] if senior else []
        meeting = create_meeting(db, team["host"], draft, historical=True)
        if published:
            publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    result = client.get("/api/reports/new-arrangements", params=PARAMS, headers=auth(team["host"])).json()
    assert result["total"] == result["meeting_count"] == 1
    assert client.get("/api/reports/new-arrangements", params={**PARAMS, "series_key": "series:other"},
                      headers=auth(team["host"])).json()["total"] == 0
    for changes in [{"start": "2026-09-01"}, {"offset": -1}, {"limit": 101}]:
        assert client.get("/api/reports/new-arrangements", params={**PARAMS, **changes},
                          headers=auth(team["host"])).status_code == 422


def test_selected_leader_filters_meetings_people_details_arrangements_and_export(
    db, team, leaders, issue, draft_factory, client,
):
    first = issue(attendee_ids=[leaders[0].id])
    second = issue(host_id=leaders[1].id)
    both = issue(attendee_ids=[u.id for u in leaders])
    issue(recorder_id=leaders[0].id)
    new_draft = draft_factory()
    new_draft.attendee_ids = [leaders[0].id]
    new_meeting = create_meeting(db, team["host"], new_draft, historical=True)
    publish(db, team["host"], new_meeting.id, PublishRequest(expected_version=1, notify=False))
    for leader, reviews, meetings, new_count in [
        (None, {first.id, second.id, both.id}, {first.id, second.id, both.id, new_meeting.id}, 1),
        (leaders[0].id, {first.id, both.id}, {first.id, both.id, new_meeting.id}, 1),
        (leaders[1].id, {second.id, both.id}, {second.id, both.id}, 0),
    ]:
        params = {**PARAMS, **({"leader_id": leader} if leader else {})}
        response = client.get("/api/reports", params=params, headers=auth(team["host"]))
        assert response.status_code == 200, response.text
        data = response.json()
        assert {m["id"] for m in data["senior_meetings"]} == meetings
        assert {r["meeting_id"] for r in data["details"]} == reviews
        assert data["summary"]["items"] == 2 * len(reviews)
        assert all(g["items"] == len(reviews) for g in data["groups"])
        assert data["senior_published_meeting_count"] == len(meetings)
        assert data["new_arrangements_count"] == new_count
        paged = client.get("/api/reports", params={**params, "details_only": True, "limit": 1},
                           headers=auth(team["host"])).json()
        assert paged["details_total"] == 2 * len(reviews)
        assert paged["details"][0]["meeting_id"] in reviews
        arrangements = client.get("/api/reports/new-arrangements", params=params,
                                  headers=auth(team["host"])).json()
        assert arrangements["total"] == new_count
        assert all(r["meeting_id"] == new_meeting.id for r in arrangements["details"])
        book = openpyxl.load_workbook(BytesIO(client.get("/api/reports/export", params=params,
                                                        headers=auth(team["host"])).content))
        assert book["会议清单"].max_row == len(meetings) + 1
        assert book["考核明细"].max_row == 2 * len(reviews) + 1
        assert book["本次新安排"].max_row == new_count + 1
    directory = client.get("/api/reports/access", params={"senior_only": True},
                           headers=auth(team["host"])).json()
    assert directory["senior_leaders"] == [{"id": p.id, "name": p.name} for p in leaders]


@pytest.mark.parametrize("path", ["/api/reports", "/api/reports/access", "/api/reports/new-arrangements", "/api/reports/export"])
def test_unknown_leader_cannot_expand_report_scope(client, team, path):
    assert client.get(path, params={**PARAMS, "leader_id": team["host"].id},
                      headers=auth(team["host"])).status_code == 422
    if not path.endswith("new-arrangements"):
        assert client.get(path, params={**PARAMS, "senior_only": False,
                          "leader_id": next(iter(SENIOR_PARTICIPANTS))},
                          headers=auth(team["host"])).status_code == 422
