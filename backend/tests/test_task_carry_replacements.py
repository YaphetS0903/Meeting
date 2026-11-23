from copy import deepcopy
from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models import MeetingVersion, OutboxEvent, Task, TaskCarryReplacement
from app.services.business import carry_forward, continue_meeting, task_snapshot
from app.services.task_continuations import confirm_carry_replacement
from tests.test_review_duplicates import replacement


def pair(db, team, draft_factory, *, expand_owners=False):
    def factory(**kwargs):
        draft = draft_factory(**kwargs)
        draft.items[0].content = "OA，SRM，ERP培训"
        return draft

    changes = ({"owner_ids": [team["a"].id, team["b"].id, team["colleague"].id]}
               if expand_owners else {"content": "0A，SRM，ERP培训"})
    return replacement(db, team, factory, **changes)


def confirm(db, team, source, old, new, **kwargs):
    return confirm_carry_replacement(
        db, team["host"], source.id, old.id, new.id,
        expected_version=kwargs.get("version", source.published_version), reason="用户确认同一工作延续",
    )


@pytest.mark.parametrize("expand_owners", [False, True])
def test_confirmed_legacy_identity_survives_typo_or_owner_expansion(db, team, draft_factory, expand_owners):
    first, second, old, new = pair(db, team, draft_factory, expand_owners=expand_owners)
    before = {task.id: task_snapshot(db, task) for task in (old, new)}
    frozen = deepcopy(second.draft)
    event_count = db.scalar(select(func.count()).select_from(OutboxEvent))
    assert {row["task_id"] for row in carry_forward(db, team["host"], team["series"].id)} == {old.id, new.id}
    link = confirm(db, team, second, old, new)
    assert confirm(db, team, second, old, new).id == link.id
    assert db.scalar(select(func.count()).select_from(TaskCarryReplacement)) == 1
    db.commit()
    for _ in range(2):
        rows = carry_forward(db, team["host"], team["series"].id)
        assert [row["task_id"] for row in rows] == [new.id]
        assert set(rows[0]["owner_ids"]) == set(task_snapshot(db, new)["owner_ids"])
    assert [row["task_id"] for row in continue_meeting(db, team["host"], second.id).draft["items"]] == [new.id]
    assert {task.id: task_snapshot(db, task) for task in (old, new)} == before
    assert second.draft == frozen
    assert db.scalar(select(func.count()).select_from(Task)) == 2
    assert db.scalar(select(func.count()).select_from(OutboxEvent)) == event_count
    assert [row["task_id"] for row in carry_forward(
        db, team["host"], team["series"].id, meeting_date=date(2026, 8, 20))] == [old.id]
    assert [row["task_id"] for row in carry_forward(
        db, team["host"], team["series"].id, exclude_meeting_id=second.id)] == [old.id]
    assert carry_forward(db, team["outsider"], team["series"].id) == []


@pytest.mark.parametrize("change", ["edited", "withdrawn", "periodic", "removed", "moved"])
def test_confirmations_do_not_hide_independently_changed_or_invalid_work(db, team, draft_factory, change):
    _, second, old, new = pair(db, team, draft_factory)
    confirm(db, team, second, old, new)
    if change == "edited":
        old.version += 1
        old.requirements = "旧任务后来作为独立安排修改过"
    elif change == "withdrawn":
        new.withdrawn_at = datetime.now(timezone.utc)
    elif change == "periodic":
        new.recurrence_key = "monthly"
    elif change == "moved":
        new.series_id = None
    else:
        snapshot = deepcopy(second.draft)
        snapshot["items"] = [row for row in snapshot["items"] if row["section"] == "review"]
        second.published_version = 2
        db.add(MeetingVersion(meeting_id=second.id, number=2, snapshot=snapshot,
                              publisher_id=team["host"].id, reason="去掉安排"))
    db.flush()
    assert old.id in {row["task_id"] for row in carry_forward(db, team["host"], team["series"].id)}


@pytest.mark.parametrize("invalid", ["same", "stale", "periodic", "scope", "no_evidence", "no_permission"])
def test_registration_requires_authorized_current_published_evidence(db, team, draft_factory, invalid):
    _, source, old, new = pair(db, team, draft_factory)
    actor = team["host"]
    version = source.published_version
    if invalid == "same":
        new = old
    elif invalid == "stale":
        version += 1
    elif invalid == "periodic":
        old.recurrence_key = "monthly"
    elif invalid == "scope":
        new.series_id = None
    elif invalid == "no_evidence":
        record = db.scalar(select(MeetingVersion).where(MeetingVersion.meeting_id == source.id))
        snapshot = deepcopy(record.snapshot)
        snapshot["items"] = [row for row in snapshot["items"] if row["section"] == "new"]
        record.snapshot = snapshot
    else:
        actor = team["outsider"]
    db.flush()
    with pytest.raises(HTTPException):
        confirm_carry_replacement(db, actor, source.id, old.id, new.id,
                                 expected_version=version, reason="确认延续")
    assert db.scalar(select(func.count()).select_from(TaskCarryReplacement)) == 0


def test_confirmed_links_do_not_block_normal_series_deletion(db, team, draft_factory):
    from fastapi import BackgroundTasks

    from app.main import DeleteSeriesRequest, delete_series, series_deletion_impact

    _, source, old, new = pair(db, team, draft_factory)
    confirm(db, team, source, old, new)
    impact = series_deletion_impact(team["series"].id, db, team["host"])
    command = DeleteSeriesRequest(impact_token=impact["impact_token"], confirmed_name=impact["name"])
    result = delete_series(team["series"].id, command, BackgroundTasks(), db, team["host"])
    assert result["deleted"] and result["deleted_tasks"] == 2
    assert db.scalar(select(func.count()).select_from(TaskCarryReplacement)) == 0
