from copy import deepcopy
from datetime import date

import pytest
from sqlalchemy import select

from app.models import Assessment, AuditLog, MeetingVersion, OutboxEvent, Task, TaskVersion
from app.schemas import MeetingDraft, MeetingItem, PublishRequest
from app.services.business import create_meeting, publish, revision, save_meeting, validate_draft
from app.services.historical_supplement import supplement_next_meeting
from app.services.role_migration import rule
from tests.role_helpers import grant


def release(db, actor, meeting):
    return publish(db, actor, meeting.id, PublishRequest(
        expected_version=meeting.draft_version, notify=False, reason="核对历史补齐",
    ))


def history_draft(team):
    def item(content, status="未到期", section="review", due="2026-11-20"):
        return MeetingItem(content=content, status=status, section=section,
                           owner_ids=[team["a"].id], due_date=due)

    return MeetingDraft(
        title="10月26日历史会议", meeting_date=date(2026, 10, 26), history_backfill=True,
        department_id=team["dep"].id, series_id=team["series"].id, host_id=team["host"].id,
        items=[item("未到期事项"), item("已完成事项", "已完成"), item("未完成未延续", "未完成"),
               item("已到期的原未到期事项", due="2026-10-30"), item("关闭事项", "关闭"),
               item("不统计事项", "不统计"), item("本次新安排", section="new")],
    )


def later_meeting(db, team, day=2, *, published=True, actor=None):
    draft = MeetingDraft(
        title=f"11月{day}日会议", meeting_date=date(2026, 11, day),
        department_id=team["dep"].id, series_id=team["series"].id,
        host_id=(actor or team["host"]).id,
        items=[MeetingItem(section="new", content="本期已有安排", owner_ids=[team["b"].id],
                           due_date=date(2026, 11, 30))],
    )
    meeting = create_meeting(db, team["host"], draft)
    if published:
        release(db, team["host"], meeting)
    return meeting


def table_rows(db, model):
    table = model.__table__
    return deepcopy(list(db.execute(select(table).order_by(*table.primary_key.columns)).mappings()))


@pytest.mark.parametrize("target_published", [False, True])
def test_historical_release_supplements_only_nearest_working_copy(db, team, target_published):
    target = later_meeting(db, team, published=target_published)
    future = later_meeting(db, team, day=9)
    original_target, original_future = deepcopy(target.draft), deepcopy(future.draft)
    versions = table_rows(db, MeetingVersion)
    tasks, task_versions = table_rows(db, Task), table_rows(db, TaskVersion)
    outbox_before = table_rows(db, OutboxEvent)
    source = create_meeting(db, team["host"], history_draft(team))
    release(db, team["host"], source)
    assert target.published_version == int(target_published) and target.has_draft
    assert future.draft == original_future and not future.has_draft and future.published_version == 1
    review = [row for row in target.draft["items"] if row["section"] == "review"]
    assert [row["content"] for row in review] == ["未到期事项", "已到期的原未到期事项", "本次新安排"]
    assert [row["status"] for row in review] == ["未到期", "", "未到期"]
    assert all(row["historical_reference"]["meeting_id"] == source.id for row in review)
    assert all(row["task_id"] is None for row in review)
    original_items = MeetingDraft.model_validate(original_target).model_dump(mode="json")["items"]
    assert [row for row in target.draft["items"] if row["section"] == "new"] == original_items
    assert table_rows(db, Task) == tasks and table_rows(db, TaskVersion) == task_versions
    assert [row for row in table_rows(db, MeetingVersion) if row["meeting_id"] != source.id] == versions
    assert [row for row in table_rows(db, OutboxEvent) if row not in outbox_before][0]["channel"] == "export"
    assert not list(db.scalars(select(Assessment).where(Assessment.meeting_id == target.id)))
    assert db.info["history_supplement_result"]["added"] == 3


def test_supplement_keeps_manual_results_postponements_deleted_rows_and_new_arrangements(db, team):
    target = later_meeting(db, team)
    source = create_meeting(db, team["host"], history_draft(team))
    release(db, team["host"], source)
    payload = MeetingDraft.model_validate(target.draft)
    removed = payload.items.pop(0).historical_work_id
    row = payload.items[0]
    row.status = "未完成"
    row.notes = "手动评定及备注"
    row.postponed_due_date = date(2026, 12, 10)
    save_meeting(db, team["host"], target.id, payload, target.draft_version)
    before = deepcopy(target.draft)
    before_version = target.draft_version
    revision(db, team["host"], source.id, "历史更正")
    release(db, team["host"], source)
    assert target.draft == before and target.draft_version == before_version
    assert supplement_next_meeting(db, team["host"], source)["added"] == 0
    assert removed not in {r["historical_work_id"] for r in target.draft["items"]}
    revision(db, team["host"], source.id, "历史新增事项")
    draft = MeetingDraft.model_validate(source.draft)
    draft.items.append(MeetingItem(section="new", content="补录的新工作", owner_ids=[team["a"].id],
                                   due_date=date(2026, 12, 1)))
    save_meeting(db, team["host"], source.id, draft, source.draft_version)
    release(db, team["host"], source)
    assert db.info["history_supplement_result"]["added"] == 1
    assert [r for r in target.draft["items"] if r["content"] != "补录的新工作"] == before["items"]


def test_existing_review_is_preserved_and_new_arrangements_do_not_suppress_review(db, team):
    target = later_meeting(db, team, published=False)
    draft = MeetingDraft.model_validate(target.draft)
    draft.items.extend([
        MeetingItem(section="review", content="未到期事项", owner_ids=[team["a"].id],
                    due_date=date(2026, 11, 20), status="已完成", notes="人工选择的结果"),
        MeetingItem(section="new", content="本次新安排", owner_ids=[team["a"].id], due_date=date(2026, 11, 20)),
    ])
    save_meeting(db, team["host"], target.id, draft, target.draft_version)
    original = deepcopy(target.draft["items"][1])
    source = create_meeting(db, team["host"], history_draft(team))
    release(db, team["host"], source)
    assert target.draft["items"][0] == original
    assert sum(r["content"] == "本次新安排" for r in target.draft["items"]) == 2
    assert sum(r["content"] == "未到期事项" for r in target.draft["items"]) == 1
    before = deepcopy(target.draft)
    assert supplement_next_meeting(db, team["host"], source)["added"] == 0
    assert target.draft == before


def test_target_permission_required_even_when_source_publish_is_allowed(db, team):
    target = later_meeting(db, team, actor=team["outsider"])
    # The employee may publish the history they host but cannot edit another host's meeting.
    actor = team["colleague"]
    draft = history_draft(team)
    draft.host_id = actor.id
    grant(db, actor, [rule("meetings", ["create"], "self")])
    source = create_meeting(db, team["host"], draft)
    original = deepcopy(target.draft), target.draft_version
    release(db, actor, source)
    assert (target.draft, target.draft_version) == original and not target.has_draft
    assert db.info["history_supplement_result"] == {"added": 0, "skipped": 1, "reason": "permission"}


def test_intervening_history_other_series_and_online_sources_are_not_modified(db, team):
    target = later_meeting(db, team)
    middle_draft = history_draft(team)
    middle_draft.meeting_date = date(2026, 10, 30)
    middle = create_meeting(db, team["host"], middle_draft)
    original = deepcopy(target.draft), target.draft_version
    source = create_meeting(db, team["host"], history_draft(team))
    release(db, team["host"], source)
    assert (target.draft, target.draft_version) == original
    assert middle.published_version == 0 and middle.draft_version == 1
    assert supplement_next_meeting(db, team["host"], target)["added"] == 0
    source.series_id = None
    assert supplement_next_meeting(db, team["host"], source)["added"] == 0


def test_imported_new_rows_validate_by_published_historical_reference(db, team):
    target = later_meeting(db, team, published=False)
    draft = history_draft(team)
    draft.items = [draft.items[-1]]
    # This provenance is retained, but must not use the imported-review-only branch.
    draft.items[0].source = {"sheet_import_id": "legacy-import", "sheet_import_item_id": "original-new"}
    source = create_meeting(db, team["host"], draft)
    release(db, team["host"], source)
    inherited = MeetingDraft.model_validate(target.draft)
    validate_draft(db, inherited, team["host"], notify=False)
    inherited.items[0].historical_reference.item_id = "forged-row"
    from fastapi import HTTPException

    with pytest.raises(HTTPException, match="历史事项来源与追溯编号不一致"):
        validate_draft(db, inherited, team["host"], notify=False)


def test_invalid_target_draft_does_not_block_source_publication(db, team):
    target = later_meeting(db, team)
    # A legacy draft with invalid email data requires a human to repair it first.
    target.draft = {**target.draft, "cc_emails": ["not-an-email"]}
    before = deepcopy(target.draft), target.draft_version
    source = create_meeting(db, team["host"], history_draft(team))
    release(db, team["host"], source)
    assert source.published_version == 1
    assert (target.draft, target.draft_version) == before and not target.has_draft
    assert db.info["history_supplement_result"]["reason"] == "target_needs_review"
    assert not list(db.scalars(select(AuditLog).where(AuditLog.action == "meeting.history.supplement")))
