from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select

from app.core.config import get_settings
from app.models import MeetingVersion, NativeTodoAttempt, NativeTodoLink, OutboxEvent, SystemSetting, Task
from app.schemas import MeetingDraft, MeetingItem, PublishRequest
from app.services import integrations
from app.services.business import continue_meeting, create_meeting, owner_ids, publish, revision, save_meeting


@pytest.fixture(autouse=True)
def isolated_notification_settings(monkeypatch):
    monkeypatch.setattr("app.services.business.today", lambda: date(2026, 8, 17))
    settings = get_settings()
    for name, value in {
        "public_url": "https://meetings.example.test",
        "native_todo_enabled": True,
        "native_todo_app_enabled": True,
        "native_todo_meeting_enabled": True,
    }.items():
        monkeypatch.setattr(settings, name, value)

    class NoExternalProvider:
        def send(self, *args, **kwargs):
            pytest.fail("A test must explicitly mock delivery before sending")

    monkeypatch.setattr(integrations, "WeCom", NoExternalProvider)


def publication_events(db, meeting):
    return list(
        db.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.channel == "wecom",
                OutboxEvent.event_key.like(f"meeting:{meeting.id}:v%:wecom"),
            )
            .order_by(OutboxEvent.event_key)
        )
    )


def source_with_native_link(db, team, draft_factory):
    source = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], source.id, PublishRequest(expected_version=1, notify=False))
    task = db.scalar(select(Task).where(Task.source_meeting_id == source.id))
    link = NativeTodoLink(
        task_id=task.id,
        todo_id="existing-native-todo",
        provider="app",
        task_version=task.version,
        follower_ids=[team["a"].id, team["b"].id],
        snapshot={},
        state="active",
    )
    db.add(link)
    db.flush()
    return source, task, link


def published_followup(db, team, draft_factory):
    source, task, link = source_with_native_link(db, team, draft_factory)
    meeting = continue_meeting(db, team["host"], source.id)
    draft = MeetingDraft.model_validate(meeting.draft)
    draft.title = "第二次部门周例会"
    for item in draft.items:
        if item.section == "review":
            item.status = "未到期"
    save_meeting(db, team["host"], meeting.id, draft, meeting.draft_version)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=meeting.draft_version))
    return meeting, task, link


def mock_sender(monkeypatch, team):
    for name in ("host", "a", "b", "colleague", "outsider"):
        team[name].wecom_id = "wecom-" + name
    sent = []

    class Provider:
        def send(self, users, text, **kwargs):
            sent.append({"users": users, "text": text, **kwargs})
            return {"msgid": "isolated-meeting-publication"}

    monkeypatch.setattr(integrations, "WeCom", Provider)
    return sent


def test_review_only_continuation_notifies_owners_without_recreating_existing_native_todo(
    db,
    team,
    draft_factory,
):
    team["host"].is_admin = False
    source, task, link = source_with_native_link(db, team, draft_factory)
    meeting = continue_meeting(db, team["host"], source.id)
    assert {item["section"] for item in meeting.draft["items"]} == {"review"}
    draft = MeetingDraft.model_validate(meeting.draft)
    for item in draft.items:
        item.status = "未到期"
    save_meeting(db, team["host"], meeting.id, draft, meeting.draft_version)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=meeting.draft_version))
    events = publication_events(db, meeting)
    assert len(events) == 1
    assert events[0].state == "pending"
    assert events[0].payload == {
        "meeting_publication": True,
        "meeting_id": meeting.id,
        "version": 1,
        "user_ids": sorted([team["a"].id, team["b"].id]),
    }
    assert db.scalar(select(func.count()).select_from(Task)) == 1
    assert db.scalar(select(func.count()).select_from(NativeTodoLink)) == 1
    assert db.get(NativeTodoLink, link.id).todo_id == "existing-native-todo"
    assert task.version == link.task_version == 1
    assert db.scalar(select(NativeTodoAttempt)) is None
    assert not list(db.scalars(select(OutboxEvent).where(OutboxEvent.channel == "native_todo")))


def test_unchanged_linked_arrangement_gets_this_meeting_notice_even_with_existing_native_todo(
    db,
    team,
    draft_factory,
):
    source, task, link = source_with_native_link(db, team, draft_factory)
    draft = draft_factory(task_id=task.id, task_version=task.version)
    draft.items[0].owner_ids = owner_ids(db, task)
    draft.title = "继续跟进原任务的会议"
    draft.meeting_date = date(2026, 8, 24)
    meeting = create_meeting(db, team["host"], draft)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    assert publication_events(db, meeting)[0].payload["meeting_id"] == meeting.id
    assert task.source_meeting_id == source.id and task.version == 1
    assert db.scalar(select(func.count()).select_from(Task)) == 1
    assert db.scalar(select(func.count()).select_from(NativeTodoLink)) == 1
    assert link.todo_id == "existing-native-todo" and link.state == "active"
    assert not list(db.scalars(select(OutboxEvent).where(OutboxEvent.channel == "native_todo")))


def test_review_and_new_arrangements_notify_each_owner_once_without_notifying_all_attendees(
    db,
    team,
    draft_factory,
):
    source, task, _ = source_with_native_link(db, team, draft_factory)
    meeting = continue_meeting(db, team["host"], source.id)
    draft = MeetingDraft.model_validate(meeting.draft)
    draft.attendee_ids = [team["host"].id, team["outsider"].id]
    for item in draft.items:
        if item.section == "review":
            item.status = "未到期"
    draft.items += [
        MeetingItem(
            section="new",
            content="新增工作甲",
            owner_ids=[team["a"].id, team["colleague"].id],
            due_date=date(2026, 12, 1),
        ),
        MeetingItem(
            section="new",
            content="新增工作乙",
            owner_ids=[team["a"].id],
            due_date=date(2026, 12, 2),
        ),
    ]
    save_meeting(db, team["host"], meeting.id, draft, meeting.draft_version)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=meeting.draft_version))
    events = publication_events(db, meeting)
    assert len(events) == 1
    assert events[0].payload["user_ids"] == sorted([team["a"].id, team["b"].id, team["colleague"].id])
    assert task.version == 1


def test_each_published_version_gets_one_notice_and_repeated_publication_is_idempotent(
    db,
    team,
    draft_factory,
):
    meeting = create_meeting(db, team["host"], draft_factory())
    first = PublishRequest(expected_version=1)
    publish(db, team["host"], meeting.id, first)
    publish(db, team["host"], meeting.id, first)
    assert len(publication_events(db, meeting)) == 1
    revision(db, team["host"], meeting.id, "补充会议议定事项")
    draft = MeetingDraft.model_validate(meeting.draft)
    draft.decisions = "新增议定事项，需要本场责任人重新查看"
    save_meeting(db, team["host"], meeting.id, draft, meeting.draft_version)
    second = PublishRequest(expected_version=meeting.draft_version, reason="补充会议议定事项")
    publish(db, team["host"], meeting.id, second)
    publish(db, team["host"], meeting.id, second)
    events = publication_events(db, meeting)
    assert meeting.published_version == 2
    assert [event.event_key for event in events] == [
        f"meeting:{meeting.id}:v1:wecom",
        f"meeting:{meeting.id}:v2:wecom",
    ]
    assert [event.payload["version"] for event in events] == [1, 2]
    assert db.scalar(select(func.count()).select_from(Task)) == 1


def test_repeated_publish_http_request_commits_only_one_meeting_notice(db, team, draft_factory):
    from fastapi.testclient import TestClient

    from app.core.db import get_db
    from app.core.security import issue_token
    from app.main import app

    meeting = create_meeting(db, team["host"], draft_factory())
    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer " + issue_token(team["host"])}
            for _ in range(2):
                response = client.post(
                    f"/api/meetings/{meeting.id}/publish",
                    json={"expected_version": 1},
                    headers=headers,
                )
                assert response.status_code == 200, response.text
                assert response.json()["published_version"] == 1
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
    assert len(publication_events(db, meeting)) == 1
    assert db.scalar(select(func.count()).select_from(Task)) == 1


@pytest.mark.parametrize("historical,notify", [(False, False), (True, True), (True, False)])
def test_notification_opt_out_and_historical_publication_do_not_create_meeting_notices(
    db,
    team,
    draft_factory,
    historical,
    notify,
):
    meeting = create_meeting(db, team["host"], draft_factory(), historical=historical)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=notify))
    assert publication_events(db, meeting) == []
    assert db.scalar(select(OutboxEvent).where(OutboxEvent.channel == "export")) is not None


def test_global_pause_records_skipped_notice_and_sender_does_not_send_after_switch_is_paused(
    db,
    team,
    draft_factory,
    monkeypatch,
):
    db.add(SystemSetting(key="reminders", value={"enabled": False}))
    meeting = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    event = publication_events(db, meeting)[0]
    assert event.state == "skipped" and event.sent_at is not None
    assert "不会补发" in event.last_error
    sent = mock_sender(monkeypatch, team)
    assert "skipped" in integrations.send_wecom(db, event)
    assert sent == []


def test_publication_without_work_owners_does_not_create_empty_notice(db, team, draft_factory):
    draft = draft_factory()
    draft.items = []
    draft.attendee_ids = [team["a"].id]
    meeting = create_meeting(db, team["host"], draft)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    assert publication_events(db, meeting) == []


def test_sender_uses_this_meeting_snapshot_and_link_even_after_original_task_changes_or_closes(
    db,
    team,
    draft_factory,
    monkeypatch,
):
    meeting, task, _ = published_followup(db, team, draft_factory)
    event = publication_events(db, meeting)[0]
    published_date = meeting.meeting_date.isoformat()
    meeting.title = "不应出现在已发布提醒中的后来草稿标题"
    task.content = "不应当替代本场纪要的后来任务内容"
    task.version += 1
    task.status = "关闭"
    task.closed_at = datetime(2026, 10, 27, tzinfo=timezone.utc)
    sent = mock_sender(monkeypatch, team)
    assert integrations.send_wecom(db, event) == {"msgid": "isolated-meeting-publication"}
    assert len(sent) == 1
    assert set(sent[0]["users"]) == {"wecom-a", "wecom-b"}
    text = sent[0]["text"]
    assert "会议纪要已发布" in text and "第二次部门周例会" in text
    assert published_date in text
    assert "发布版本" not in text and "V1" not in text
    from tests.test_wecom_content import message_link

    assert message_link(text) == f"https://meetings.example.test/meetings/{meeting.id}"
    assert task.source_meeting_id not in text and task.id not in text
    assert "后来草稿标题" not in text and "后来任务内容" not in text
    assert len(text.encode("utf-8")) <= 2048


@pytest.mark.parametrize("unavailable", ["meeting", "snapshot", "superseded", "historical"])
def test_sender_skips_invalid_or_superseded_meeting_publication(
    db,
    team,
    draft_factory,
    monkeypatch,
    unavailable,
):
    meeting, _, _ = published_followup(db, team, draft_factory)
    event = publication_events(db, meeting)[0]
    if unavailable == "meeting":
        event.payload = {**event.payload, "meeting_id": "removed-meeting"}
    elif unavailable == "snapshot":
        db.execute(delete(MeetingVersion).where(MeetingVersion.meeting_id == meeting.id))
    elif unavailable == "superseded":
        revision(db, team["host"], meeting.id, "补充纪要")
        publish(
            db,
            team["host"],
            meeting.id,
            PublishRequest(expected_version=meeting.draft_version, reason="补充纪要"),
        )
    else:
        meeting.historical = True
    sent = mock_sender(monkeypatch, team)
    assert "skipped" in integrations.send_wecom(db, event)
    assert sent == []


@pytest.mark.parametrize("recipient_problem", ["inactive", "unbound", "access"])
def test_sender_rechecks_recipient_binding_activity_and_meeting_access_before_delivery(
    db,
    team,
    draft_factory,
    monkeypatch,
    recipient_problem,
):
    meeting, _, _ = published_followup(db, team, draft_factory)
    event = publication_events(db, meeting)[0]
    sent = mock_sender(monkeypatch, team)
    if recipient_problem == "inactive":
        team["a"].active = False
    elif recipient_problem == "unbound":
        team["a"].wecom_id = None
    else:
        event.payload = {**event.payload, "user_ids": [team["outsider"].id]}
    with pytest.raises((integrations.NotConfigured, HTTPException)):
        integrations.send_wecom(db, event)
    assert sent == []
