import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select

from app.main import DeleteSeriesRequest, delete_series, series_deletion_impact
from app.models import AuditLog, Meeting, MeetingSeries, NativeTodoLink, OutboxEvent, Task
from app.schemas import PublishRequest
from app.services.business import continue_meeting, create_meeting, publish


@pytest.fixture(autouse=True)
def isolate_notifications(monkeypatch):
    monkeypatch.setattr("app.services.business.enqueue_native_todo", lambda *_a, **_k: None)


def issue(db, team, draft):
    meeting = create_meeting(db, team["host"], draft)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    return meeting


def command(impact):
    return DeleteSeriesRequest(impact_token=impact["impact_token"], confirmed_name=impact["name"])


def test_delete_empty_series_requires_admin_and_exact_name(db, team):
    series = team["series"]
    impact = series_deletion_impact(series.id, db, team["host"])
    assert impact["meeting_count"] == 0 and impact["can_delete"]
    for action in (
        lambda: series_deletion_impact(series.id, db, team["a"]),
        lambda: delete_series(series.id, command(impact), BackgroundTasks(), db, team["a"]),
    ):
        with pytest.raises(HTTPException) as error:
            action()
        assert error.value.status_code == 403
    wrong = command(impact).model_copy(update={"confirmed_name": "wrong"})
    with pytest.raises(HTTPException) as error:
        delete_series(series.id, wrong, BackgroundTasks(), db, team["host"])
    assert error.value.status_code == 422
    assert delete_series(series.id, command(impact), BackgroundTasks(), db, team["host"])["deleted"]
    assert db.get(MeetingSeries, series.id) is None


def test_delete_entire_series_allows_internal_review_references(db, team, draft_factory):
    meeting = issue(db, team, draft_factory())
    followup = continue_meeting(db, team["host"], meeting.id)
    followup.draft = {**followup.draft, "items": [
        {**item, "status": "未完成"} for item in followup.draft["items"]
    ]}
    publish(db, team["host"], followup.id, PublishRequest(expected_version=1, notify=False))
    series_id = team["series"].id
    impact = series_deletion_impact(series_id, db, team["host"])
    assert impact["meeting_count"] == 2
    assert impact["task_count"] == 1
    assert impact["assessment_count"] == 2
    assert impact["can_delete"]
    result = delete_series(series_id, command(impact), BackgroundTasks(), db, team["host"])
    assert result == {"deleted": True, "deleted_meetings": 2, "deleted_tasks": 1}
    assert not list(db.scalars(select(Meeting)))
    assert not list(db.scalars(select(Task)))
    assert db.scalar(select(AuditLog).where(AuditLog.action == "meeting_series.delete"))


def test_external_review_reference_blocks_entire_deletion(db, team, draft_factory):
    meeting = issue(db, team, draft_factory())
    followup = continue_meeting(db, team["host"], meeting.id)
    other = MeetingSeries(name="保留系列")
    db.add(other)
    db.flush()
    followup.series_id = other.id
    db.flush()
    impact = series_deletion_impact(team["series"].id, db, team["host"])
    assert impact["blocking_meetings"][0]["id"] == followup.id
    assert not impact["can_delete"]
    with pytest.raises(HTTPException) as error:
        delete_series(team["series"].id, command(impact), BackgroundTasks(), db, team["host"])
    assert error.value.status_code == 409
    assert db.get(Meeting, meeting.id)


@pytest.mark.parametrize("change", ["add_meeting", "change_task"])
def test_changed_impact_requires_new_confirmation(db, team, draft_factory, change):
    meeting = issue(db, team, draft_factory())
    impact = series_deletion_impact(team["series"].id, db, team["host"])
    if change == "add_meeting":
        create_meeting(db, team["host"], draft_factory())
    else:
        db.scalar(select(Task)).version += 1
    db.flush()
    with pytest.raises(HTTPException) as error:
        delete_series(team["series"].id, command(impact), BackgroundTasks(), db, team["host"])
    assert error.value.status_code == 409
    assert db.get(Meeting, meeting.id)


def test_active_native_todo_blocks_delete(db, team, draft_factory):
    issue(db, team, draft_factory())
    task = db.scalar(select(Task))
    db.add(NativeTodoLink(task_id=task.id, todo_id="native-1", task_version=1,
                          follower_ids=[], state="active"))
    db.flush()
    impact = series_deletion_impact(team["series"].id, db, team["host"])
    assert impact["active_native_todos"] == 1 and not impact["can_delete"]
    with pytest.raises(HTTPException):
        delete_series(team["series"].id, command(impact), BackgroundTasks(), db, team["host"])
    assert db.get(Task, task.id)


def test_late_failure_rolls_back_previously_deleted_member(db, team, draft_factory):
    issue(db, team, draft_factory())
    issue(db, team, draft_factory())
    meetings = list(db.scalars(select(Meeting).order_by(Meeting.id)))
    db.add(OutboxEvent(event_key=f"meeting:{meetings[-1].id}:busy", channel="export",
                      payload={"meeting_id": meetings[-1].id}, state="sending"))
    db.flush()
    impact = series_deletion_impact(team["series"].id, db, team["host"])
    callbacks = BackgroundTasks()
    with pytest.raises(HTTPException) as error:
        delete_series(team["series"].id, command(impact), callbacks, db, team["host"])
    assert error.value.status_code == 409
    assert len(list(db.scalars(select(Meeting)))) == 2
    assert len(list(db.scalars(select(Task)))) == 2
    assert callbacks.tasks == []
