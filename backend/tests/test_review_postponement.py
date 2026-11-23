from copy import deepcopy
from datetime import date
from io import BytesIO

import pytest
from fastapi import HTTPException
from openpyxl import load_workbook
from pypdf import PdfReader
from sqlalchemy import func, select

from app.models import Assessment, MeetingVersion, OutboxEvent, Task, TaskVersion
from app.schemas import MeetingDraft, MeetingItem, PublishRequest
from app.services.business import (
    carry_forward,
    continue_meeting,
    create_meeting,
    publish,
    revision,
    save_meeting,
)
from app.services.exports import pdf_minutes, task_rows, xlsx_minutes


def setup_review(db, team, draft_factory):
    source = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], source.id, PublishRequest(expected_version=1, notify=False))
    task = db.get(Task, source.draft["items"][0]["task_id"])
    review = create_meeting(db, team["host"], draft_factory(
        section="review", when=date(2026, 8, 12), task_id=task.id, task_version=task.version,
    ))
    return source, task, review


def save_postponement(db, team, meeting, due=date(2026, 8, 28)):
    draft = MeetingDraft.model_validate(meeting.draft)
    draft.items[0].postponed_due_date = due
    save_meeting(db, team["host"], meeting.id, draft, meeting.draft_version)


def publish_review(db, team, meeting):
    publish(db, team["host"], meeting.id, PublishRequest(
        expected_version=meeting.draft_version, reason="会议延期", notify=False,
    ))


def test_postponement_is_draft_only_until_publication_then_carries_latest_deadline(
    db, team, draft_factory,
):
    source, task, review = setup_review(db, team, draft_factory)
    original = deepcopy(source.draft)
    save_postponement(db, team, review)
    assert task.due_date == date(2026, 8, 17) and task.version == 1
    assert carry_forward(db, team["host"], team["series"].id)[0]["due_date"] == "2026-08-17"
    publish_review(db, team, review)
    assert task.due_date == date(2026, 8, 28) and task.version == 2
    assert db.scalar(select(func.count()).select_from(Task)) == 1
    assert source.draft == original
    row = review.draft["items"][0]
    assert row["due_date"] == "2026-08-17"
    assert row["postponed_due_date"] == "2026-08-28" and row["task_version"] == 2
    assert db.scalar(select(Assessment).where(Assessment.meeting_id == review.id)).due_date == date(2026, 8, 17)
    for rows in [carry_forward(db, team["host"], team["series"].id),
                 continue_meeting(db, team["host"], review.id).draft["items"]]:
        assert len(rows) == 1 and str(rows[0]["due_date"]) == "2026-08-28"
        assert not rows[0].get("postponed_due_date")
    assert set(db.scalars(select(OutboxEvent.channel))) == {"export"}
    revision(db, team["host"], review.id, "补充备注")
    publish_review(db, team, review)
    assert task.version == 2  # Revisions never replay an already applied postponement.


@pytest.mark.parametrize("already_published", [False, True])
def test_cancelling_pending_postponement_preserves_effective_deadline(
    db, team, draft_factory, already_published,
):
    _, task, review = setup_review(db, team, draft_factory)
    if already_published:
        save_postponement(db, team, review)
        publish_review(db, team, review)
        revision(db, team["host"], review.id, "调整草稿")
    effective_due, task_version = task.due_date, task.version
    save_postponement(db, team, review, date(2026, 9, 1))
    draft = MeetingDraft.model_validate(review.draft)
    draft.items[0].postponed_due_date = effective_due if already_published else None
    save_meeting(db, team["host"], review.id, draft, review.draft_version)
    assert task.due_date == effective_due and task.version == task_version
    publish_review(db, team, review)
    assert task.due_date == effective_due and task.version == task_version
    assert db.scalar(select(func.count()).select_from(Task)) == 1
    assert carry_forward(db, team["host"], team["series"].id)[0]["due_date"] == effective_due.isoformat()


@pytest.mark.parametrize("due,status,section", [
    (date(2026, 8, 17), "未到期", "review"),
    (date(2026, 8, 16), "未到期", "review"),
    (date(2026, 8, 28), "未完成", "review"),
    (date(2026, 8, 28), "未到期", "new"),
])
def test_invalid_postponement_cannot_publish(db, team, draft_factory, due, status, section):
    _, task, review = setup_review(db, team, draft_factory)
    draft = MeetingDraft.model_validate(review.draft)
    draft.items[0].postponed_due_date = due
    draft.items[0].status, draft.items[0].section = status, section
    save_meeting(db, team["host"], review.id, draft, review.draft_version)
    with pytest.raises(HTTPException) as error:
        publish_review(db, team, review)
    assert error.value.status_code == 422
    assert task.due_date == date(2026, 8, 17) and task.version == 1


def test_stale_postponement_and_duplicate_arrangement_are_rejected(db, team, draft_factory):
    _, task, review = setup_review(db, team, draft_factory)
    save_postponement(db, team, review)
    task.version += 1
    with pytest.raises(HTTPException) as error:
        publish_review(db, team, review)
    assert error.value.status_code == 409
    draft = MeetingDraft.model_validate(review.draft)
    draft.items[0].task_version = task.version
    next_row = draft.items[0].model_copy(deep=True)
    next_row.id, next_row.section, next_row.postponed_due_date = "duplicate", "new", None
    draft.items.append(next_row)
    save_meeting(db, team["host"], review.id, draft, review.draft_version)
    with pytest.raises(HTTPException) as error:
        publish_review(db, team, review)
    assert error.value.status_code == 422


def test_old_revision_does_not_overwrite_later_deadline_and_repostponement_checks_version(
    db, team, draft_factory,
):
    _, task, review = setup_review(db, team, draft_factory)
    save_postponement(db, team, review)
    publish_review(db, team, review)
    snapshot = deepcopy(review.draft)
    task.due_date, task.version = date(2026, 9, 10), 3
    revision(db, team["host"], review.id, "补充备注")
    publish_review(db, team, review)
    assert task.due_date == date(2026, 9, 10) and task.version == 3
    assert db.scalar(select(MeetingVersion).where(
        MeetingVersion.meeting_id == review.id, MeetingVersion.number == 1,
    )).snapshot == snapshot
    revision(db, team["host"], review.id, "再次延期")
    save_postponement(db, team, review, date(2026, 9, 20))
    with pytest.raises(HTTPException) as error:
        publish_review(db, team, review)
    assert error.value.status_code == 409


def test_postponement_can_be_extended_again_in_next_meeting(db, team, draft_factory):
    _, task, review = setup_review(db, team, draft_factory)
    save_postponement(db, team, review)
    publish_review(db, team, review)
    next_meeting = continue_meeting(db, team["host"], review.id)
    draft = MeetingDraft.model_validate(next_meeting.draft)
    draft.meeting_date = date(2026, 8, 20)
    draft.items[0].status, draft.items[0].postponed_due_date = "未到期", date(2026, 9, 10)
    save_meeting(db, team["host"], next_meeting.id, draft, next_meeting.draft_version)
    publish_review(db, team, next_meeting)
    assert task.due_date == date(2026, 9, 10) and task.version == 3
    assert db.scalar(select(func.count()).select_from(TaskVersion)) == 3
    assert db.scalar(select(func.count()).select_from(Task)) == 1


def test_old_postponement_does_not_block_unfinished_extension_in_revision(db, team, draft_factory):
    _, task, review = setup_review(db, team, draft_factory)
    save_postponement(db, team, review)
    publish_review(db, team, review)
    revision(db, team["host"], review.id, "更正结果并延续")
    draft = MeetingDraft.model_validate(review.draft)
    draft.items[0].status = "未完成"
    arrangement = draft.items[0].model_copy(deep=True)
    arrangement.id, arrangement.section, arrangement.status = "extension", "new", "未到期"
    arrangement.due_date, arrangement.postponed_due_date = date(2026, 9, 1), None
    draft.items.append(arrangement)
    save_meeting(db, team["host"], review.id, draft, review.draft_version)
    publish_review(db, team, review)
    assert task.due_date == date(2026, 9, 1) and task.version == 3
    assert review.draft["items"][0]["postponed_due_date"] == "2026-08-28"
    assert db.scalar(select(func.count()).select_from(Task)) == 1


def test_unactivated_history_uses_postponed_date_in_both_carry_paths(db, team):
    from tests.test_historical_links import confirmed

    _, history = confirmed(db, team, status="未到期", due_date="2026-09-01")
    row = carry_forward(db, team["host"], team["series"].id)[0]
    row.update(status="未到期", postponed_due_date="2026-12-01")
    current = create_meeting(db, team["host"], MeetingDraft(
        title="历史任务延期", host_id=team["host"].id, department_id=team["dep"].id,
        series_id=team["series"].id, meeting_date=date(2026, 8, 25),
        items=[MeetingItem.model_validate(row)],
    ))
    publish_review(db, team, current)
    assert db.scalar(select(func.count()).select_from(Task)) == 0
    for rows in [carry_forward(db, team["host"], team["series"].id),
                 continue_meeting(db, team["host"], current.id).draft["items"]]:
        assert len(rows) == 1 and str(rows[0]["due_date"]) == "2026-12-01"
        assert not rows[0].get("postponed_due_date")


def test_exports_display_original_and_latest_date_without_duplicate_rows(db, team, draft_factory):
    _, _, review = setup_review(db, team, draft_factory)
    save_postponement(db, team, review)
    publish_review(db, team, review)
    rows = task_rows(review.draft, "review")
    assert len(rows) == 1 and rows[0][4] == "2026-08-17\n2026-08-28"
    workbook = load_workbook(BytesIO(xlsx_minutes(review.draft)), rich_text=True)
    dates = workbook["上期任务回顾"]["E5"].value
    assert str(dates) == rows[0][4]
    assert dates[0].font.strike is True
    assert dates[2].font.strike is False and dates[2].font.b is False
    output = "".join(page.extract_text() for page in PdfReader(BytesIO(pdf_minutes(review.draft))).pages)
    compact = "".join(output.split())
    assert "2026-08-17" in compact and "2026-08-28" in compact and "最新日期" not in compact
