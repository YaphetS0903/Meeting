"""Regular personnel reports show meeting names from exactly the counted records."""
from datetime import date
from uuid import uuid4

from app.schemas import MeetingDraft, PublishRequest
from app.services.business import create_meeting, publish, revision, save_meeting
from tests.role_helpers import report_grant
from tests.test_department_people_report import issue, result


def test_regular_names_are_distinct_ordered_and_available_without_details(db, team, draft_factory):
    draft = draft_factory(section="review", status="已完成")
    draft.items.append(draft.items[0].model_copy(update={"id": str(uuid4()), "content": "第二项"}))
    first = create_meeting(db, team["host"], draft, historical=True)
    publish(db, team["host"], first.id, PublishRequest(expected_version=1, notify=False))
    later = issue(db, team, owners=[team["a"].id], status="未完成")
    later.meeting_date = date(2026, 8, 20)
    db.flush()
    data = result(db, team, department=None, include_details=False)
    row = next(row for row in data["groups"] if row["id"] == team["a"].id)
    assert row["items"] == 3 and row["rate"] == 2 / 3
    assert row["meetings"] == [
        {"id": first.id, "title": first.title, "meeting_date": "2026-08-10"},
        {"id": later.id, "title": later.title, "meeting_date": "2026-08-20"},
    ]
    assert data["details"] == [] and data["details_paginated"] is True
    assert next(row for row in data["groups"] if row["id"] == team["colleague"].id)["meetings"] == []


def test_regular_meeting_names_follow_period_series_person_and_status_filters(db, team):
    expected = issue(db, team, owners=[team["a"].id], status="未完成")
    expected.series_id = team["series"].id
    issue(db, team, owners=[team["a"].id])  # another series
    other_person = issue(db, team, owners=[team["b"].id], status="未完成")
    other_person.series_id = team["series"].id
    out_of_period = issue(db, team, owners=[team["a"].id], status="未完成")
    out_of_period.series_id = team["series"].id
    out_of_period.meeting_date = date(2026, 9, 1)
    db.flush()
    row = result(db, team, person_id=team["a"].id, status="未完成",
                 series_key=f"series:{team['series'].id}", include_details=False)["groups"][0]
    assert row["items"] == 1
    assert [meeting["id"] for meeting in row["meetings"]] == [expected.id]


def test_regular_names_do_not_leak_other_people_meetings_to_self_or_delegate(db, team):
    own = issue(db, team, owners=[team["a"].id])
    foreign = issue(db, team, department=team["other"], owners=[team["outsider"].id])
    personal = result(db, team, actor=team["a"], department=None, include_details=False)
    assert {row["id"] for row in personal["groups"]} == {team["a"].id}
    assert [m["id"] for m in personal["groups"][0]["meetings"]] == [own.id]
    report_grant(db, team["colleague"], team["other"])
    delegated = result(db, team, actor=team["colleague"], department=None, include_details=False)
    rows = {row["id"]: row for row in delegated["groups"]}
    assert set(rows) == {team["outsider"].id, team["colleague"].id}
    assert [m["id"] for m in rows[team["outsider"].id]["meetings"]] == [foreign.id]
    assert rows[team["colleague"].id]["meetings"] == []


def test_regular_names_exclude_old_published_assessments_after_revision(db, team):
    meeting = issue(db, team, owners=[team["a"].id])
    revision(db, team["host"], meeting.id, "更正责任人")
    draft = MeetingDraft.model_validate(meeting.draft)
    draft.items[0].owner_ids = [team["b"].id]
    save_meeting(db, team["host"], meeting.id, draft, meeting.draft_version)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=meeting.draft_version, notify=False))
    rows = {row["id"]: row for row in result(db, team, include_details=False)["groups"]}
    assert rows[team["a"].id]["items"] == 0 and rows[team["a"].id]["meetings"] == []
    assert rows[team["b"].id]["items"] == 1
    assert [m["id"] for m in rows[team["b"].id]["meetings"]] == [meeting.id]
