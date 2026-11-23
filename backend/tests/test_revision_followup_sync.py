from copy import deepcopy
from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models import AuditLog, MeetingVersion, OutboxEvent
from app.schemas import MeetingDraft, MeetingItem, PublishRequest
from app.services.business import continue_meeting, create_meeting, publish, revision, save_meeting


def released(db, team, draft_factory):
    draft = draft_factory(when=date(2026, 11, 9))
    draft.items[0].due_date = date(2026, 12, 1)
    draft.items[0].notes = "原备注"
    source = create_meeting(db, team["host"], draft)
    publish(db, team["host"], source.id, PublishRequest(expected_version=1, notify=False))
    return source


def edit_source(db, team, source, *, content="修订后的内容", extra=False):
    revision(db, team["host"], source.id, "核对任务内容")
    draft = MeetingDraft.model_validate(source.draft)
    draft.items[0].content = content
    draft.items[0].notes = "修订备注"
    draft.items[0].due_date = date(2026, 12, 3)
    draft.items[0].change_reason = "修订任务内容及日期"
    if extra:
        draft.items.append(
            MeetingItem(
                section="new",
                content="补充上期遗漏任务",
                owner_ids=[team["a"].id],
                due_date=date(2026, 12, 5),
            )
        )
    save_meeting(db, team["host"], source.id, draft, source.draft_version)


def release_revision(db, team, source):
    return publish(
        db,
        team["host"],
        source.id,
        PublishRequest(expected_version=source.draft_version, notify=False, reason="修订上期任务"),
    )


def test_revision_publication_updates_inherited_rows_preserving_next_meeting_and_manual_fields(
    db, team, draft_factory
):
    source = released(db, team, draft_factory)
    next_meeting = continue_meeting(db, team["host"], source.id)
    draft = MeetingDraft.model_validate(next_meeting.draft)
    draft.meeting_date = date(2026, 11, 20)
    draft.title = "下期自定义会议名"
    draft.decisions = "下期议定事项"
    draft.items[0].status = "未到期"
    draft.items[0].postponed_due_date = date(2026, 12, 20)
    draft.items[0].notes = "下期人工备注"
    draft.items.append(
        MeetingItem(
            section="new", content="下期新增工作", owner_ids=[team["b"].id], due_date=date(2026, 12, 15)
        )
    )
    save_meeting(db, team["host"], next_meeting.id, draft, next_meeting.draft_version)
    before = deepcopy(next_meeting.draft)
    old_version = next_meeting.draft_version
    previous_release = deepcopy(
        db.scalar(select(MeetingVersion).where(MeetingVersion.meeting_id == source.id)).snapshot
    )
    edit_source(db, team, source, extra=True)
    assert next_meeting.draft == before  # Saving an unpublished revision never syncs.
    release_revision(db, team, source)
    assert next_meeting.draft_version == old_version + 1
    assert next_meeting.meeting_date == date(2026, 11, 20)
    assert next_meeting.draft["title"] == "下期自定义会议名"
    assert next_meeting.draft["decisions"] == "下期议定事项"
    row = next_meeting.draft["items"][0]
    assert row["id"] == before["items"][0]["id"]
    assert row["content"] == "修订后的内容"
    assert row["due_date"] == "2026-12-03"
    assert row["status"] == "未到期"
    assert row["postponed_due_date"] == "2026-12-20"
    assert row["notes"] == "下期人工备注"
    assert before["items"][1] in next_meeting.draft["items"]
    assert next_meeting.draft["items"][-1]["content"] == "补充上期遗漏任务"
    assert next_meeting.draft["items"][-1]["status"] == "未到期"
    assert next_meeting.draft["items"][-1]["source"]["continuation_default_status"] == "未到期"
    assert (
        db.scalar(
            select(MeetingVersion).where(MeetingVersion.meeting_id == source.id, MeetingVersion.number == 1)
        ).snapshot
        == previous_release
    )
    assert db.info["continuation_sync_result"] == {"updated": 1, "skipped": 0}
    assert not list(db.scalars(select(OutboxEvent).where(OutboxEvent.channel.in_(["email", "wecom"]))))
    with pytest.raises(HTTPException, match="已被其他人修改"):
        save_meeting(db, team["host"], next_meeting.id, draft, old_version)
    # Synced linkage and task version allow normal publication after selecting new results.
    completed = MeetingDraft.model_validate(next_meeting.draft)
    completed.items[-1].status = "未到期"
    save_meeting(db, team["host"], next_meeting.id, completed, next_meeting.draft_version)
    publish(
        db,
        team["host"],
        next_meeting.id,
        PublishRequest(expected_version=next_meeting.draft_version, notify=False),
    )
    assert next_meeting.published_version == 1


def test_unrelated_and_published_followups_are_untouched(db, team, draft_factory):
    source = released(db, team, draft_factory)
    published_next = continue_meeting(db, team["host"], source.id)
    draft = MeetingDraft.model_validate(published_next.draft)
    draft.items[0].status = "未到期"
    save_meeting(db, team["host"], published_next.id, draft, published_next.draft_version)
    publish(
        db,
        team["host"],
        published_next.id,
        PublishRequest(expected_version=published_next.draft_version, notify=False),
    )
    unrelated = create_meeting(db, team["host"], draft_factory())
    before = [(m.draft_version, deepcopy(m.draft)) for m in (published_next, unrelated)]
    edit_source(db, team, source)
    release_revision(db, team, source)
    assert before == [(m.draft_version, m.draft) for m in (published_next, unrelated)]
    assert db.info["continuation_sync_result"] == {"updated": 0, "skipped": 0}


def test_manually_removed_inherited_work_is_not_reintroduced_and_repeated_sync_keeps_custom_notes(
    db, team, draft_factory
):
    source = released(db, team, draft_factory)
    next_meeting = continue_meeting(db, team["host"], source.id)
    draft = MeetingDraft.model_validate(next_meeting.draft)
    draft.items = []
    save_meeting(db, team["host"], next_meeting.id, draft, next_meeting.draft_version)
    edit_source(db, team, source, extra=True)
    release_revision(db, team, source)
    assert len(next_meeting.draft["items"]) == 1
    assert next_meeting.draft["items"][0]["content"] == "补充上期遗漏任务"
    draft = MeetingDraft.model_validate(next_meeting.draft)
    draft.items[0].notes = "保留手写备注"
    save_meeting(db, team["host"], next_meeting.id, draft, next_meeting.draft_version)
    edit_source(db, team, source, content="再次修订内容")
    release_revision(db, team, source)
    assert len(next_meeting.draft["items"]) == 1
    assert next_meeting.draft["items"][0]["notes"] == "保留手写备注"
    assert (
        len(
            list(
                db.scalars(
                    select(AuditLog).where(
                        AuditLog.action == "meeting.continuation.sync", AuditLog.entity_id == next_meeting.id
                    )
                )
            )
        )
        == 2
    )


def test_sync_respects_target_edit_permissions(db, team, draft_factory):
    source = released(db, team, draft_factory)
    next_meeting = continue_meeting(db, team["host"], source.id)
    next_meeting.host_id = team["outsider"].id
    next_meeting.recorder_id = team["outsider"].id
    next_meeting.draft = {
        **next_meeting.draft,
        "host_id": team["outsider"].id,
        "recorder_id": team["outsider"].id,
    }
    db.flush()
    before = deepcopy(next_meeting.draft)
    team["host"].is_admin = False
    edit_source(db, team, source)
    release_revision(db, team, source)
    assert next_meeting.draft == before
    assert db.info["continuation_sync_result"] == {"updated": 0, "skipped": 1}


def test_existing_pending_results_and_carry_arrangement_are_preserved(db, team, draft_factory):
    source = released(db, team, draft_factory)
    next_meeting = continue_meeting(db, team["host"], source.id)
    draft = MeetingDraft.model_validate(next_meeting.draft)
    draft.items[0].status = "未完成"
    extension = draft.items[0].model_copy(deep=True)
    extension.id = "manual-carry"
    extension.section = "new"
    extension.status = "未到期"
    extension.content = "下期手动安排内容"
    extension.due_date = date(2026, 12, 20)
    extension.change_reason = "下期重新安排"
    draft.items.append(extension)
    save_meeting(db, team["host"], next_meeting.id, draft, next_meeting.draft_version)
    edit_source(db, team, source)
    release_revision(db, team, source)
    rows = next_meeting.draft["items"]
    assert rows[0]["status"] == "未完成"
    assert rows[1]["content"] == "下期手动安排内容"
    assert rows[1]["due_date"] == "2026-12-20"
    assert rows[0]["task_version"] == rows[1]["task_version"] == 2
    publish(
        db,
        team["host"],
        next_meeting.id,
        PublishRequest(expected_version=next_meeting.draft_version, notify=False),
    )
    assert next_meeting.published_version == 1


def test_historical_source_revision_syncs_without_creating_live_tasks(db, team, draft_factory):
    draft = draft_factory(when=date(2026, 11, 9))
    draft.items[0].due_date = date(2026, 12, 1)
    source = create_meeting(db, team["host"], draft, historical=True)
    publish(db, team["host"], source.id, PublishRequest(expected_version=1, notify=False))
    next_meeting = continue_meeting(db, team["host"], source.id)
    before_id = next_meeting.draft["items"][0]["id"]
    edit_source(db, team, source)
    release_revision(db, team, source)
    row = next_meeting.draft["items"][0]
    assert row["id"] == before_id
    assert row["content"] == "修订后的内容"
    assert row["notes"] == "修订备注"
    assert row["due_date"] == "2026-12-03"
    assert row["historical_reference"]["version"] == 2
    assert row["task_id"] is None
    from app.models import Task
    assert not list(db.scalars(select(Task)))


def test_source_reassignment_refreshes_inherited_people_and_publishes_normally(db, team, draft_factory):
    source = released(db, team, draft_factory)
    next_meeting = continue_meeting(db, team["host"], source.id)
    edit_source(db, team, source)
    draft = MeetingDraft.model_validate(source.draft)
    draft.items[0].owner_ids = [team["outsider"].id]
    draft.items[0].verifier_id = team["colleague"].id
    save_meeting(db, team["host"], source.id, draft, source.draft_version)
    release_revision(db, team, source)
    row = next_meeting.draft["items"][0]
    assert row["owner_ids"] == [team["outsider"].id]
    assert row["verifier_id"] == team["colleague"].id
    draft = MeetingDraft.model_validate(next_meeting.draft)
    draft.items[0].status = "未到期"
    save_meeting(db, team["host"], next_meeting.id, draft, next_meeting.draft_version)
    publish(db, team["host"], next_meeting.id, PublishRequest(expected_version=next_meeting.draft_version, notify=False))
    assert next_meeting.published_version == 1
