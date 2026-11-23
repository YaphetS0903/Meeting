from copy import deepcopy
from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models import Assessment, MeetingSeries, OutboxEvent, Task
from app.schemas import MeetingDraft, MeetingItem, PublishRequest
from app.services.business import create_meeting, publish, save_meeting
from app.services.history_template import import_template, templates


def setup_documents(db, team):
    rows = [
        MeetingItem(id="old-done", section="review", content="框架与问题点", status="已完成",
                    owner_ids=[team["a"].id], due_date=date(2026, 10, 15), notes="需优化"),
        MeetingItem(id="old-open", section="review", content="同一工作原安排", status="未完成",
                    owner_ids=[team["a"].id, team["b"].id], due_date=date(2026, 10, 9), notes="延期到10/20"),
        MeetingItem(id="new-work", section="new", content="同一工作原安排", status="未到期",
                    owner_ids=[team["a"].id, team["b"].id], due_date=date(2026, 10, 30),
                    requirements="具体要求", deliverable="报告", notes="每个月第一周周五"),
    ]
    base = dict(title="历史任务单", department_id=team["dep"].id, series_id=team["series"].id,
                host_id=team["host"].id, recorder_id=team["host"].id, meeting_date=date(2026, 10, 17))
    source = create_meeting(db, team["host"], MeetingDraft(
        **base, items=rows, decisions="历史会议决议",
    ), historical=True)
    publish(db, team["host"], source.id, PublishRequest(expected_version=1, notify=False))
    current = create_meeting(db, team["host"], MeetingDraft(
        **{**base, "title": "本次会议", "meeting_date": date(2026, 10, 27)},
        decisions="本次另填决议", items=[MeetingItem(
            id="manual", section="new", content="本次手工新安排", owner_ids=[team["b"].id],
            due_date=date(2026, 11, 1),
        )],
    ))
    return source, current


def test_full_template_keeps_sections_completed_results_notes_and_decisions(db, team):
    source, current = setup_documents(db, team)
    original = deepcopy(source.draft)
    events = db.scalar(select(func.count()).select_from(OutboxEvent))
    assessments = db.scalar(select(func.count()).select_from(Assessment))
    options = templates(db, team["host"], current.id)
    assert options[0]["review_count"] == 2 and options[0]["new_count"] == 1
    current, summary = import_template(db, team["host"], current.id, source.id, 1, 1)
    assert summary["added_count"] == 3
    copied = [r for r in current.draft["items"] if r["id"] != "manual"]
    for row, expected in zip(copied, source.draft["items"], strict=True):
        for field in ("section", "content", "status", "notes", "due_date", "owner_ids", "requirements", "deliverable"):
            assert row[field] == expected[field]
        assert row["historical_reference"]["item_id"] == expected["id"]
        assert row["source"]["template_item_id"] == expected["id"]
    assert current.draft["title"] == "本次会议" and current.draft["meeting_date"] == "2026-10-27"
    assert current.draft["decisions"] == "本次另填决议\n\n历史会议决议"
    assert source.draft == original
    assert db.scalar(select(func.count()).select_from(Task)) == 0
    assert db.scalar(select(func.count()).select_from(OutboxEvent)) == events
    assert db.scalar(select(func.count()).select_from(Assessment)) == assessments
    publish(db, team["host"], current.id, PublishRequest(expected_version=2, notify=False))
    assert db.scalar(select(func.count()).select_from(Task)) == 2  # only current arrangements


def test_repeat_fill_preserves_edited_imported_rows_without_duplicates(db, team):
    source, current = setup_documents(db, team)
    current, _ = import_template(db, team["host"], current.id, source.id, 1, 1)
    draft = deepcopy(current.draft)
    draft["items"][1]["notes"] = "人工补充的进展"
    current = save_meeting(db, team["host"], current.id, MeetingDraft.model_validate(draft), 2)
    current, summary = import_template(db, team["host"], current.id, source.id, 3, 1)
    assert current.draft_version == 3 and summary["added_count"] == 0
    assert len(current.draft["items"]) == 4
    assert current.draft["items"][1]["notes"] == "人工补充的进展"
    assert current.draft["decisions"].count("历史会议决议") == 1


def test_legacy_flattened_rows_replaced_but_manual_arrangements_retained(db, team):
    source, current = setup_documents(db, team)
    legacy = deepcopy(source.draft["items"][2])
    legacy.update(section="review", id="legacy", historical_reference={
        "meeting_id": source.id, "version": 1, "item_id": "new-work",
        "reason": "从已确认的上期历史会议带入",
    })
    current.draft = {**current.draft, "items": [*current.draft["items"], legacy]}
    current, summary = import_template(db, team["host"], current.id, source.id, 1, 1)
    assert summary["replaced_count"] == 1
    assert len(current.draft["items"]) == 4
    assert current.draft["items"][0]["id"] == "manual"
    assert all(r["id"] != "legacy" for r in current.draft["items"])


@pytest.mark.parametrize("case", ["target_access", "source_access", "series", "date", "version", "draft_version", "published"])
def test_full_import_validates_permissions_scope_versions_and_new_draft(db, team, case):
    source, current = setup_documents(db, team)
    actor = team["host"]
    expected, source_version = 1, 1
    if case == "target_access":
        actor = team["outsider"]
    elif case == "source_access":
        actor = team["colleague"]
        current.host_id = actor.id
        source.department_id = team["other"].id
    elif case == "series":
        other = MeetingSeries(name="其他系列", department_id=team["dep"].id)
        db.add(other)
        db.flush()
        source.series_id = other.id
    elif case == "date":
        source.meeting_date = date(2026, 11, 1)
    elif case == "version":
        source_version = 2
    elif case == "draft_version":
        expected = 9
    elif case == "published":
        current.published_version = 1
    before = deepcopy(current.draft)
    with pytest.raises(HTTPException):
        import_template(db, actor, current.id, source.id, expected, source_version)
    assert current.draft == before


def test_template_list_excludes_wrong_series_future_and_invisible_sources(db, team):
    source, current = setup_documents(db, team)
    source.meeting_date = date(2026, 11, 1)
    assert templates(db, team["host"], current.id) == []
