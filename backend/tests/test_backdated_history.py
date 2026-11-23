from copy import deepcopy
from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.extra_api import Activate, PreviewRequest, activate, preview_minutes
from app.models import Assessment, MeetingVersion, OutboxEvent, Task, TaskVersion
from app.schemas import BackfillReference, MeetingDraft, PublishRequest
from app.services.business import (
    carry_forward,
    create_meeting,
    publish,
    revision,
    save_meeting,
    task_snapshot,
)
from app.services.historical_backfill import history_for_meeting, task_options


@pytest.mark.parametrize("mode", ["backfill", "legacy_import"])
def test_historical_date_conflict_can_preview_publish_and_revise_without_rewriting_original(
    db, team, draft_factory, mode
):
    draft = draft_factory(section="review", status="未到期", when=date(2026, 10, 26))
    draft.history_backfill = mode == "backfill"
    item = draft.items[0]
    item.due_date = date(2026, 10, 23)
    item.content = "EXH项目：完成设变后的价格谈判"
    item.notes = "销售下周去客户端面谈价格OK后才能和供应商定价"
    item.source = {"row": 48, "raw_due_date": "2026/10/23", "raw_status": "未到期"}
    original = item.model_dump(mode="json")
    history = create_meeting(db, team["host"], draft, historical=mode == "legacy_import")
    before = deepcopy(history.draft)
    for fmt in ("pdf", "xlsx"):
        preview = preview_minutes(
            history.id, PreviewRequest(expected_version=history.draft_version, format=fmt), db, team["host"]
        )
        assert preview.status_code == 200
        assert preview.body.startswith(b"%PDF" if fmt == "pdf" else b"PK")
    assert history.draft == before
    for number in (1, 2):
        publish(
            db,
            team["host"],
            history.id,
            PublishRequest(expected_version=history.draft_version, notify=True, reason="核对历史"),
        )
        snapshot = db.scalar(
            select(MeetingVersion).where(
                MeetingVersion.meeting_id == history.id, MeetingVersion.number == number
            )
        ).snapshot
        for key, value in original.items():
            assert snapshot["items"][0][key] == value
        assessments = list(
            db.scalars(
                select(Assessment).where(
                    Assessment.meeting_id == history.id, Assessment.meeting_version == number
                )
            )
        )
        assert len(assessments) == len(item.owner_ids)
        assert all(a.status == "未到期" and a.due_date == date(2026, 10, 23) for a in assessments)
        if number == 1:
            revision(db, team["host"], history.id, "核对历史")
    assert db.scalar(select(func.count()).select_from(Task)) == 0
    assert db.scalar(select(func.count()).select_from(TaskVersion)) == 0
    events = list(db.scalars(select(OutboxEvent)))
    assert len(events) == 2 and all(event.channel == "export" for event in events)


@pytest.mark.parametrize("action", ["preview", "publish"])
def test_online_meeting_still_rejects_not_due_after_deadline(db, team, draft_factory, action):
    draft = draft_factory(section="review", status="未到期", when=date(2026, 10, 26))
    draft.items[0].due_date = date(2026, 10, 23)
    meeting = create_meeting(db, team["host"], draft)
    with pytest.raises(HTTPException, match="不能标为未到期") as error:
        if action == "preview":
            preview_minutes(meeting.id, PreviewRequest(expected_version=1), db, team["host"])
        else:
            publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    assert error.value.status_code == 422
    assert meeting.published_version == 0
    assert meeting.draft["items"][0]["status"] == "未到期"
    assert db.scalar(select(func.count()).select_from(Assessment)) == 0


def release(db, team, meeting):
    publish(
        db,
        team["host"],
        meeting.id,
        PublishRequest(
            expected_version=meeting.draft_version,
            notify=False,
            reason="历史核对" if meeting.published_version else "",
        ),
    )


def setup_link(db, team, draft_factory, section="review"):
    later = create_meeting(db, team["host"], draft_factory())
    release(db, team, later)
    option = task_options(db, team["host"], team["series"].id, date(2026, 8, 5))[0]
    draft = draft_factory(section=section, status="已完成", when=date(2026, 8, 5))
    draft.title = "补录8月5日会议"
    draft.history_backfill = True
    draft.items[0].content = "历史原文，不能用后续内容覆盖"
    draft.items[0].backfill_reference = BackfillReference.model_validate(option)
    return later, draft


@pytest.mark.parametrize("section", ["review", "new"])
def test_backdated_publication_preserves_later_snapshot_task_results_and_notifications(
    db,
    team,
    draft_factory,
    section,
):
    later, draft = setup_link(db, team, draft_factory, section)
    task = db.get(Task, later.draft["items"][0]["task_id"])
    before = deepcopy(later.draft), task_snapshot(db, task)
    versions = [(v.id, deepcopy(v.snapshot)) for v in db.scalars(select(TaskVersion))]
    outbox = [(v.id, v.channel, deepcopy(v.payload)) for v in db.scalars(select(OutboxEvent))]
    history = create_meeting(db, team["host"], draft)
    assert history.historical
    assert history_for_meeting(db, team["host"], later) == []
    # Even a caller requesting notifications cannot notify or dispatch historical work.
    publish(db, team["host"], history.id, PublishRequest(expected_version=1, notify=True))
    assert (later.draft, task_snapshot(db, task)) == before
    assert [(v.id, v.snapshot) for v in db.scalars(select(TaskVersion))] == versions
    assert db.scalar(select(func.count()).select_from(Task)) == 1
    assert (
        db.scalar(select(MeetingVersion).where(MeetingVersion.meeting_id == later.id)).snapshot == before[0]
    )
    assert [
        (v.id, v.channel, v.payload)
        for v in db.scalars(select(OutboxEvent))
        if v.id in {x[0] for x in outbox}
    ] == outbox
    extra = [v for v in db.scalars(select(OutboxEvent)) if v.id not in {x[0] for x in outbox}]
    assert len(extra) == 1 and extra[0].channel == "export"
    rows = list(db.scalars(select(Assessment).where(Assessment.meeting_id == history.id)))
    assert len(rows) == (2 if section == "review" else 0)
    assert all(r.task_id is None and r.status == "已完成" for r in rows)
    assert history.draft["items"][0]["content"] == draft.items[0].content
    assert history.draft["items"][0]["task_id"] is None
    assert history_for_meeting(db, team["host"], later) == [
        dict(
            id=history.id,
            title=history.title,
            meeting_date="2026-08-05",
            task_count=1,
        )
    ]
    carried = carry_forward(db, team["host"], team["series"].id)
    assert len(carried) == 1 and carried[0]["task_id"] == task.id
    assert not carried[0].get("backfill_reference")


@pytest.mark.parametrize("invalid", ["mode", "series", "date", "version", "item", "task", "live"])
def test_reject_invalid_backdated_links(db, team, draft_factory, invalid):
    _, draft = setup_link(db, team, draft_factory)
    ref = draft.items[0].backfill_reference
    if invalid == "mode":
        draft.history_backfill = False
    elif invalid == "series":
        draft.series_id = None
    elif invalid == "date":
        draft.meeting_date = date(2026, 8, 10)
    elif invalid == "version":
        ref.version = 2
    elif invalid == "item":
        ref.item_id = "missing"
    elif invalid == "task":
        ref.task_id = "missing"
    else:
        draft.items[0].task_id = ref.task_id
    with pytest.raises(HTTPException):
        create_meeting(db, team["host"], draft)


def test_options_filter_visibility_search_date_and_draft_state(db, team, draft_factory):
    later, _ = setup_link(db, team, draft_factory)
    create_meeting(db, team["host"], draft_factory())
    assert len(task_options(db, team["host"], team["series"].id, date(2026, 8, 5), "流程修订")) == 1
    assert task_options(db, team["host"], team["series"].id, date(2026, 8, 5), "不存在") == []
    assert task_options(db, team["host"], team["series"].id, later.meeting_date) == []
    assert task_options(db, team["outsider"], team["series"].id, date(2026, 8, 5)) == []


def test_history_revision_preserves_reference_after_later_revision(db, team, draft_factory):
    later, draft = setup_link(db, team, draft_factory)
    history = create_meeting(db, team["host"], draft)
    release(db, team, history)
    revision(db, team["host"], later.id, "修正说明")
    release(db, team, later)
    revision(db, team["host"], history.id, "核对历史")
    changed = MeetingDraft.model_validate(history.draft)
    changed.items[0].notes = "历史备注补充"
    save_meeting(db, team["host"], history.id, changed, history.draft_version)
    release(db, team, history)
    assert history.draft["items"][0]["backfill_reference"]["version"] == 1
    changed.history_backfill = False
    with pytest.raises(HTTPException):
        save_meeting(db, team["host"], history.id, changed, history.draft_version)


def test_linked_history_cannot_activate_duplicate_and_can_keep_inactive_owner(db, team, draft_factory):
    _, draft = setup_link(db, team, draft_factory)
    team["a"].active = False
    history = create_meeting(db, team["host"], draft)
    release(db, team, history)
    with pytest.raises(HTTPException, match="无需重复激活"):
        activate(
            history.id,
            Activate(
                item_id=draft.items[0].id,
                expected_version=1,
                due_date=date(2026, 8, 30),
                owner_ids=[team["b"].id],
                reason="继续",
            ),
            db,
            team["host"],
        )


def test_links_preserve_existing_assessments_and_block_target_deletion(db, team, draft_factory):
    from app.main import meeting_deletion_dependencies
    from app.services.access import visible_meetings
    from app.services.meeting_views import apply_meeting_view
    from tests.test_failed_review_carry import assessed

    _, later = assessed(db, team, draft_factory, status="已完成")
    before = [(x.id, x.status, x.meeting_version) for x in db.scalars(select(Assessment))]
    task_id = later.draft["items"][0]["task_id"]
    option = next(
        x
        for x in task_options(db, team["host"], team["series"].id, date(2026, 8, 5))
        if x["meeting_id"] == later.id
    )
    draft = draft_factory(section="review", status="未完成", when=date(2026, 8, 5))
    draft.history_backfill = True
    draft.items[0].backfill_reference = BackfillReference.model_validate(option)
    history = create_meeting(db, team["host"], draft)
    assert meeting_deletion_dependencies(db, later, [task_id])[0]["draft"] is True
    release(db, team, history)
    assert [
        (x.id, x.status, x.meeting_version)
        for x in db.scalars(select(Assessment))
        if x.meeting_id == later.id
    ] == before
    dependencies = meeting_deletion_dependencies(db, later, [task_id])
    assert dependencies[0]["published_versions"] == [1]
    current = list(
        db.scalars(apply_meeting_view(visible_meetings(db, team["host"]), db, team["host"], "current"))
    )
    assert later in current and history not in current
    assert carry_forward(db, team["host"], team["series"].id) == []


def test_duplicate_links_and_stale_draft_are_rejected(db, team, draft_factory):
    later, draft = setup_link(db, team, draft_factory)
    duplicate = draft.items[0].model_copy(deep=True)
    duplicate.id = "duplicate"
    draft.items.append(duplicate)
    with pytest.raises(HTTPException, match="不能重复关联"):
        create_meeting(db, team["host"], draft)
    draft.items.pop()
    history = create_meeting(db, team["host"], draft)
    revision(db, team["host"], later.id, "更新发布版本")
    release(db, team, later)
    with pytest.raises(HTTPException, match="发布版本已变化"):
        release(db, team, history)
