from tests.role_helpers import grant as new_grant, dispatch_grant, report_grant
from app.services.role_migration import rule
from app.models import User, Department
from tests.role_helpers import report_grant, grant as new_grant, DEFINITIONS
from datetime import date

from sqlalchemy import select

from app.models import EmployeeRoleGrant, Meeting, MeetingSeries, Task
from app.schemas import MeetingDraft, PublishRequest
from app.services.business import continue_meeting, create_meeting, publish, save_meeting
from app.services.report_access import report_access
from tests.test_task_overview_order import client, fetch, tasks  # noqa: F401


def meeting(db, team, department, recorder=None):
    row = Meeting(title="部门周会", department_id=department.id,
                  meeting_date=date(2026, 11, 3), host_id=team["host"].id,
                  recorder_id=recorder.id if recorder else None)
    db.add(row)
    db.flush()
    return row


def test_department_filter_keeps_all_23_meeting_tasks_including_three_helpers(client, db, team, tasks):
    source = meeting(db, team, team["dep"])
    for index in range(23):
        tasks(f"purchase-{index:02}", [team["outsider"] if index < 3 else team["a"]], meeting=source)
    tasks("unrelated-foreign-direct", [team["outsider"]])
    first = fetch(client, team["host"], department_id=team["dep"].id, limit=20).json()
    last = fetch(client, team["host"], department_id=team["dep"].id, limit=20, offset=20).json()
    assert first["total"] == first["summary"]["total"] == last["total"] == 23
    assert len(first["items"]) == 20 and len(last["items"]) == 3
    assert {r["id"] for r in first["items"] + last["items"]} == {f"purchase-{i:02}" for i in range(23)}
    personal = fetch(client, team["host"], department_id=team["dep"].id,
                     person_id=team["outsider"].id, limit=100).json()
    assert personal["total"] == 3


def test_department_manager_sees_helpers_but_not_their_unrelated_work(client, db, team, tasks):
    actor = team["colleague"]
    actor.position = "部长"
    new_grant(db,actor,[rule("overview",["view"])])
    source = meeting(db, team, team["dep"])
    foreign = meeting(db, team, team["other"])
    tasks("helper-for-us", [team["outsider"]], meeting=source)
    tasks("helper-other-meeting", [team["outsider"]], meeting=foreign)
    tasks("helper-direct", [team["outsider"]])
    result = fetch(client, actor, department_id=team["dep"].id).json()
    assert [r["id"] for r in result["items"]] == ["helper-for-us"]
    assert fetch(client, actor, person_id=team["outsider"].id).json()["total"] == 1


def test_cross_department_recorder_scope_is_only_the_assigned_meeting(client, db, team, tasks):
    actor = team["colleague"]
    assigned = meeting(db, team, team["other"], recorder=actor)
    unassigned = meeting(db, team, team["other"])
    tasks("assigned-helper", [team["outsider"]], meeting=assigned)
    tasks("unassigned-same-helper", [team["outsider"]], meeting=unassigned)
    tasks("helper-direct", [team["outsider"]])
    response = fetch(client, actor, department_id=team["other"].id)
    assert response.status_code == 200
    assert [r["id"] for r in response.json()["items"]] == ["assigned-helper"]
    assert report_access(db, actor).mode == "self"
    assigned.recorder_id = None
    db.flush()
    assert fetch(client, actor).status_code == 403


def test_meeting_task_role_includes_department_meeting_helpers_only(client, db, team, tasks):
    actor = team["colleague"]
    db.add(new_grant(db,db.get(User,actor.id),DEFINITIONS["department_meetings"]))
    own = meeting(db, team, team["dep"])
    foreign = meeting(db, team, team["other"])
    tasks("department-helper", [team["outsider"]], meeting=own)
    tasks("foreign-helper", [team["outsider"]], meeting=foreign)
    tasks("foreign-direct", [team["outsider"]])
    assert [r["id"] for r in fetch(client, actor).json()["items"]] == ["department-helper"]
    assert report_access(db, actor).mode == "self"


def test_cross_department_recorder_can_save_publish_and_continue_without_moving_department(db, team, draft_factory):
    actor = team["colleague"]
    series = MeetingSeries(name="其他部门周会", department_id=team["other"].id)
    db.add(series)
    db.flush()
    draft = draft_factory()
    draft.department_id = team["other"].id
    draft.series_id = series.id
    draft.recorder_id = actor.id
    draft.items[0].owner_ids = [team["outsider"].id]
    source = create_meeting(db, team["host"], draft)
    edited = MeetingDraft.model_validate(source.draft)
    edited.items[0].content = "跨部门编制后正常发布"
    save_meeting(db, actor, source.id, edited, source.draft_version)
    publish(db, actor, source.id, PublishRequest(expected_version=source.draft_version, notify=False))
    task = db.scalar(select(Task).where(Task.source_meeting_id == source.id))
    followup = continue_meeting(db, actor, source.id)
    assert source.department_id == task.department_id == followup.department_id == team["other"].id
    assert actor.department_id == team["dep"].id
    assert followup.recorder_id == actor.id
    assert report_access(db, actor).mode == "self"
