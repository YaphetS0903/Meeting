from copy import deepcopy
from datetime import date

import openpyxl
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import (
    Assessment,
    Attachment,
    AuditLog,
    Meeting,
    MeetingVersion,
    OutboxEvent,
    Task,
    TaskVersion,
)
from app.schemas import BackfillReference, MeetingDraft, PublishRequest
from app.services.business import (
    carry_forward,
    continue_meeting,
    create_meeting,
    lock_continuation_drafts,
    publish,
    revision,
    save_meeting,
    sync_continuation_drafts,
)
from app.services.historical_backfill import history_for_meeting, task_options
from app.services.meeting_sheet_import import ImportHeaders, create_from_source, recognize


@pytest.fixture
def backfill_file(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path / "storage")
    path = tmp_path / "1026历史任务单.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    for row in [
        ["技术部历史会议纪要"],
        ["会议所属部门", "技术部", None, "会议类型", "☑ 部门周例会"],
        ["会议召开日期", "2026年10月26日", None, "主持人", "主持人"],
        ["本次会议核心工作部署内容"],
        ["序号", "具体工作任务内容", "责任人", "计划完成时间", "任务闭环完成结果", "备注", "输出物"],
        [1, "完成检验流程修订", "责任人甲、责任人乙", "2026-11-30", None, "历史原始备注", "报告"],
    ]:
        sheet.append(row)
    book.save(path)
    return path


def headers(team, **extra):
    return ImportHeaders(
        candidate_index=0,
        title="10月26日历史会议",
        meeting_date=date(2026, 10, 26),
        department_id=team["dep"].id,
        host_id=team["host"].id,
        recorder_id=team["host"].id,
        history_backfill=True,
        **extra,
    )


def release(db, team, meeting):
    publish(
        db,
        team["host"],
        meeting.id,
        PublishRequest(
            expected_version=meeting.draft_version,
            notify=True if meeting.historical else False,
            reason="核对历史备注" if meeting.published_version else "",
        ),
    )


def protected(db, meeting_ids):
    result = {}
    for model, condition in [
        (Meeting, Meeting.id.in_(meeting_ids)),
        (MeetingVersion, MeetingVersion.meeting_id.in_(meeting_ids)),
        (Task, True),
        (TaskVersion, True),
        (Assessment, Assessment.meeting_id.in_(meeting_ids)),
        (Attachment, Attachment.meeting_id.in_(meeting_ids)),
    ]:
        table = model.__table__
        result[model.__name__] = deepcopy(
            [dict(row) for row in db.execute(select(table).where(condition).order_by(table.c.id)).mappings()]
        )
    return result


@pytest.mark.parametrize("entry", ["manual", "import"])
@pytest.mark.parametrize("followup_published", [False, True])
def test_both_backfill_entries_link_1102_without_changing_1109(
    db,
    team,
    draft_factory,
    backfill_file,
    entry,
    followup_published,
):
    draft = draft_factory(when=date(2026, 11, 2))
    draft.items[0].due_date = date(2026, 11, 30)
    later = create_meeting(db, team["host"], draft)
    release(db, team, later)
    followup = continue_meeting(db, team["host"], later.id)
    next_draft = MeetingDraft.model_validate(followup.draft)
    next_draft.meeting_date = date(2026, 11, 9)
    next_draft.items[0].status = "未到期"
    next_draft.items[0].notes = "11月9日已手动填写的内容"
    next_draft.items[0].postponed_due_date = date(2026, 12, 15)
    save_meeting(db, team["host"], followup.id, next_draft, followup.draft_version)
    if followup_published:
        release(db, team, followup)
    # The published target may already have an unsubmitted revision open.
    revision(db, team["host"], later.id, "后续会议修订尚未提交")
    preserved = protected(db, [later.id, followup.id])
    prior_events = set(db.scalars(select(OutboxEvent.id)))
    if entry == "import":
        source = recognize(db, team["host"], team["series"].id, backfill_file)
        history, duplicate = create_from_source(db, team["host"], source.id, headers(team))
        assert not duplicate
        assert history.historical and history.draft["history_backfill"]
        assert history.draft["items"][0]["notes"] == "历史原始备注"
    else:
        old = draft_factory(when=date(2026, 10, 26))
        old.history_backfill = True
        old.items[0].due_date = date(2026, 11, 30)
        history = create_meeting(db, team["host"], old)
    old = MeetingDraft.model_validate(history.draft)
    if entry == "import":
        old.import_confirmed_issues = [issue["id"] for issue in source.candidates[0]["issues"]]
    option = next(
        row
        for row in task_options(db, team["host"], team["series"].id, old.meeting_date)
        if row["meeting_id"] == later.id
    )
    old.items[0].backfill_reference = BackfillReference.model_validate(option)
    save_meeting(db, team["host"], history.id, old, history.draft_version)
    release(db, team, history)
    assert protected(db, [later.id, followup.id]) == preserved
    assert history_for_meeting(db, team["host"], later)[0]["id"] == history.id
    # An accidental or legacy continuation pointer must not allow a historical revision to sync Nov 9.
    link = AuditLog(
        actor_id=team["host"].id,
        action="meeting.continue",
        entity_type="Meeting",
        entity_id=followup.id,
        after={"source_meeting_id": history.id, "source_version": 1},
    )
    db.add(link)
    db.flush()
    assert lock_continuation_drafts(db, history) == []
    assert sync_continuation_drafts(db, team["host"], history, [(followup, link)]) == {
        "updated": 0,
        "skipped": 0,
    }
    revision(db, team["host"], history.id, "历史备注核对")
    corrected = MeetingDraft.model_validate(history.draft)
    corrected.items[0].notes = "历史备注更正，后续任务保持原值"
    save_meeting(db, team["host"], history.id, corrected, history.draft_version)
    release(db, team, history)
    assert protected(db, [later.id, followup.id]) == preserved
    extra = list(db.scalars(select(OutboxEvent).where(OutboxEvent.id.not_in(prior_events))))
    assert len(extra) == 2 and all(event.channel == "export" for event in extra)
    carried = carry_forward(db, team["host"], team["series"].id)
    assert len(carried) == 1 and carried[0]["task_id"] == later.draft["items"][0]["task_id"]


def test_all_new_is_explicit_preserves_original_section_and_handles_unknown_sections(db, team, backfill_file):
    source = recognize(db, team["host"], team["series"].id, backfill_file)
    candidates = deepcopy(source.candidates)
    candidates[0]["usable"] = False
    candidates[0]["payload"]["items"][0]["section"] = "unknown"
    source.candidates = candidates
    meeting, _ = create_from_source(db, team["host"], source.id, headers(team, backfill_all_new=True))
    row = meeting.draft["items"][0]
    assert row["section"] == "new" and row["status"] == "未到期"
    assert row["source"]["original_section"] == "unknown"
    assert row["notes"] == "历史原始备注" and row["task_id"] is None
    assert db.scalar(select(func.count()).select_from(Task)) == 0
    again, duplicate = create_from_source(db, team["host"], source.id, headers(team, backfill_all_new=True))
    assert duplicate and again.id == meeting.id
    wrong = headers(team).model_copy(update={"history_backfill": False})
    with pytest.raises(HTTPException, match="不能切换"):
        create_from_source(db, team["host"], source.id, wrong)
    with pytest.raises(ValidationError, match="只有补录历史"):
        headers(team, backfill_all_new=True).model_copy().model_validate(
            {
                **headers(team, backfill_all_new=True).model_dump(),
                "history_backfill": False,
            }
        )


def test_reupload_mode_mismatch_never_creates_or_converts_existing_meeting(db, team, backfill_file):
    source = recognize(db, team["host"], team["series"].id, backfill_file)
    meeting, _ = create_from_source(db, team["host"], source.id, headers(team))
    another = recognize(db, team["host"], team["series"].id, backfill_file)
    with pytest.raises(HTTPException, match="不能切换"):
        create_from_source(
            db, team["host"], another.id, headers(team).model_copy(update={"history_backfill": False})
        )
    assert db.scalar(select(func.count()).select_from(Meeting)) == 1
    assert meeting.historical and another.meeting_id is None


def test_online_revision_never_syncs_into_converted_historical_draft(db, team, draft_factory):
    source = create_meeting(db, team["host"], draft_factory())
    release(db, team, source)
    history_draft = draft_factory(when=date(2026, 8, 5))
    history_draft.history_backfill = True
    history = create_meeting(db, team["host"], history_draft)
    db.add(
        AuditLog(
            actor_id=team["host"].id,
            action="meeting.continue",
            entity_type="Meeting",
            entity_id=history.id,
            after={"source_meeting_id": source.id, "source_version": 1},
        )
    )
    db.flush()
    assert lock_continuation_drafts(db, source) == []
