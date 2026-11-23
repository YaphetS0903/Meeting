from app.models import User, Department
from tests.role_helpers import report_grant, grant as new_grant, DEFINITIONS
"""Meeting responsibilities permit department selection without extra employee roles."""
import pytest
from sqlalchemy import select

from app.models import EmployeeRoleGrant, MeetingSeries, Task
from app.schemas import MeetingDraft, PublishRequest
from app.services.access import direct_assignees
from app.services.business import continue_meeting, create_meeting, publish, revision, save_meeting
from app.services.employee_roles import MEETING_ROLE
from app.services.report_access import report_access
from tests.test_report_permissions import client  # noqa: F401
from tests.test_report_self_only import auth


@pytest.mark.parametrize("responsibility", ["host", "recorder"])
def test_personally_responsible_employee_can_create_and_publish_in_another_department(
    db, team, client, draft_factory, responsibility,
):
    actor = team["colleague"]
    series = MeetingSeries(name="跨部门记录周会", department_id=team["other"].id)
    db.add(series)
    db.flush()
    options = client.get("/api/meeting-people", headers=auth(actor)).json()
    assert {d["id"] for d in options["departments"]} == {team["dep"].id, team["other"].id}
    # The global meeting selector does not expand the ordinary directory.
    directory = client.get("/api/directory", headers=auth(actor)).json()
    assert team["other"].id not in {d["id"] for d in directory["departments"]}
    draft = draft_factory()
    draft.department_id, draft.series_id = team["other"].id, series.id
    draft.host_id = actor.id if responsibility == "host" else team["outsider"].id
    draft.recorder_id = actor.id if responsibility == "recorder" else None
    draft.items[0].owner_ids = [team["outsider"].id]
    created = client.post("/api/meetings", headers=auth(actor), json=draft.model_dump(mode="json"))
    assert created.status_code == 200, created.text
    identifier = created.json()["id"]
    published = client.post(f"/api/meetings/{identifier}/publish", headers=auth(actor), json={
        "expected_version": created.json()["draft_version"], "notify": False,
    })
    assert published.status_code == 200, published.text
    assert db.scalar(select(Task).where(Task.source_meeting_id == identifier)).department_id == team["other"].id
    assert actor.department_id == team["dep"].id
    assert report_access(db, actor).mode == "self"
    assert direct_assignees(db, actor) == []


def test_existing_recorder_can_correct_department_without_changing_account_scope(db, team, client, draft_factory):
    actor = team["colleague"]
    db.add(new_grant(db,db.get(User,actor.id),DEFINITIONS[MEETING_ROLE]))
    db.flush()
    allowed_before = {p.id for p in direct_assignees(db, actor)}
    draft = draft_factory()
    draft.recorder_id = actor.id
    meeting = create_meeting(db, team["host"], draft)
    options = client.get("/api/meeting-people", params={"meeting_id": meeting.id}, headers=auth(actor)).json()
    assert team["other"].id in {d["id"] for d in options["departments"]}
    draft.department_id = team["other"].id
    saved = client.put(f"/api/meetings/{meeting.id}", headers=auth(actor), json={
        "expected_version": meeting.draft_version, "draft": draft.model_dump(mode="json"),
    })
    assert saved.status_code == 200, saved.text
    assert meeting.department_id == team["other"].id
    assert actor.department_id == team["dep"].id
    assert {p.id for p in direct_assignees(db, actor)} == allowed_before
    assert report_access(db, actor).mode == "self"


def test_role_only_editor_cannot_move_meeting_or_claim_recorder_to_bypass_scope(db, team, client, draft_factory):
    actor = team["colleague"]
    db.add(new_grant(db,db.get(User,actor.id),DEFINITIONS[MEETING_ROLE]))
    db.flush()
    meeting = create_meeting(db, team["host"], draft_factory())
    token = auth(actor)
    options = client.get("/api/meeting-people", params={"meeting_id": meeting.id}, headers=token).json()
    assert team["other"].id not in {d["id"] for d in options["departments"]}
    draft = MeetingDraft.model_validate(meeting.draft)
    draft.department_id = team["other"].id
    draft.recorder_id = actor.id
    saved = client.put(f"/api/meetings/{meeting.id}", headers=token, json={
        "expected_version": meeting.draft_version, "draft": draft.model_dump(mode="json"),
    })
    assert saved.status_code == 403
    draft.recorder_id = None
    draft.host_id = team["outsider"].id
    assert client.post("/api/meetings", headers=token, json=draft.model_dump(mode="json")).status_code == 403
    assert client.get("/api/meeting-people", params={"meeting_id": meeting.id},
                      headers=auth(team["a"])).status_code == 403


def test_recorder_can_republish_department_only_but_not_rewrite_assessment_results(db, team, draft_factory):
    actor = team["colleague"]
    draft = draft_factory()
    draft.recorder_id = actor.id
    source = create_meeting(db, team["host"], draft)
    publish(db, team["host"], source.id, PublishRequest(expected_version=1, notify=False))
    meeting = continue_meeting(db, actor, source.id)
    follow = MeetingDraft.model_validate(meeting.draft)
    follow.meeting_date = draft.meeting_date
    for item in follow.items:
        item.status = "未到期"
    save_meeting(db, actor, meeting.id, follow, meeting.draft_version)
    publish(db, actor, meeting.id, PublishRequest(expected_version=meeting.draft_version, notify=False))
    revision(db, actor, meeting.id, "纠正会议归属部门")
    corrected = MeetingDraft.model_validate(meeting.draft)
    corrected.department_id = team["other"].id
    save_meeting(db, actor, meeting.id, corrected, meeting.draft_version)
    publish(db, actor, meeting.id, PublishRequest(expected_version=meeting.draft_version, notify=False))
    assert meeting.published_version == 2 and meeting.department_id == team["other"].id
    assert all(item["status"] == "未到期" for item in meeting.draft["items"])
    revision(db, actor, meeting.id, "尝试改变评定结果")
    altered = MeetingDraft.model_validate(meeting.draft)
    altered.items[0].status = "未完成"
    save_meeting(db, actor, meeting.id, altered, meeting.draft_version)
    publish(db, actor, meeting.id, PublishRequest(expected_version=meeting.draft_version, notify=False))
    assert meeting.published_version==3
