from datetime import date, datetime, timezone
from html import unescape

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import get_db
from app.core.security import issue_token
from app.main import app
from app.models import OutboxEvent, SystemSetting, TaskOwner
from app.schemas import MeetingDraft, PublishRequest
from app.services import integrations
from app.services.business import publish, revision, save_meeting
from app.services.deadline_reminders import enqueue_due_reminders
from tests.test_deadline_reminders import assigned
from tests.test_meeting_publication_notification import mock_sender, publication_events

CLOCK = datetime(2026, 11, 5, 1, tzinfo=timezone.utc)


def digest(db):
    return db.scalar(select(OutboxEvent).where(OutboxEvent.event_key.like("deadline-digest:%")))


def capture(monkeypatch):
    calls = []

    class Provider:
        def send(self, users, content, **kwargs):
            calls.append(dict(users=users, content=content, **kwargs))
            return {"msgid": "isolated-card"}

    monkeypatch.setattr(integrations, "WeCom", Provider)
    monkeypatch.setattr("app.models.now", lambda: CLOCK)
    return calls


def test_daily_digest_is_personal_complete_and_idempotent(db, team, draft_factory, monkeypatch):
    first = assigned(db, team, draft_factory, 5)
    second = assigned(db, team, draft_factory, 6, direct=True)
    third = assigned(db, team, draft_factory, 7)
    for t, content in [(first, "今天的任务"), (second, "明天的任务"), (third, "后天的任务")]:
        t.content = content
    team["b"].wecom_id = "second-owner"
    db.add(TaskOwner(task_id=first.id, user_id=team["b"].id))
    db.flush()
    assert enqueue_due_reminders(db, CLOCK) == 2
    assert enqueue_due_reminders(db, CLOCK) == 0
    events = list(db.scalars(select(OutboxEvent).where(OutboxEvent.event_key.like("deadline-digest:%"))))
    calls = capture(monkeypatch)
    for event in events:
        integrations.send_wecom(db, event)
    by_user = {call["users"][0]: call for call in calls}
    primary = by_user["test-owner"]["content"]
    assert "card" not in by_user["test-owner"]
    assert "查看全部提醒任务</a>" in primary
    assert "reminder_day=2026-11-05" in primary
    assert "3 项任务" in primary
    assert primary.index("今天到期") < primary.index("明天到期") < primary.index("后天到期")
    assert "截止日期" not in primary and primary.count("提醒日期") == 1
    assert "明天的任务" not in by_user["second-owner"]["content"]
    assert len(primary.encode()) <= 2048


@pytest.mark.parametrize("change", ["completed", "postponed", "reassigned", "withdrawn", "deleted"])
def test_changed_task_is_removed_but_other_digest_items_are_sent(db, team, draft_factory, monkeypatch, change):
    first = assigned(db, team, draft_factory, 5)
    second = assigned(db, team, draft_factory, 6)
    first.content, second.content = "不应再显示", "仍需要提醒"
    enqueue_due_reminders(db, CLOCK)
    if change == "completed":
        first.status = "已完成"
    elif change == "postponed":
        first.due_date = date(2026, 12, 1)
    elif change == "reassigned":
        owner = db.scalar(select(TaskOwner).where(TaskOwner.task_id == first.id, TaskOwner.user_id == team["a"].id))
        db.delete(owner)
    elif change == "deleted":
        from fastapi import BackgroundTasks

        from app.main import remove_meeting_records
        from app.models import Meeting

        remove_meeting_records(db, team["host"], db.get(Meeting, first.source_meeting_id), BackgroundTasks())
    else:
        first.withdrawn_at = CLOCK
    db.flush()
    calls = capture(monkeypatch)
    result = integrations.send_wecom(db, digest(db))
    assert result["task_ids"] == [second.id]
    assert "仍需要提醒" in calls[0]["content"]
    assert "不应再显示" not in calls[0]["content"]


def test_long_names_escape_html_show_three_and_keep_total_and_link(db, team, draft_factory, monkeypatch):
    for _ in range(7):
        task = assigned(db, team, draft_factory, 5)
        task.content = "<script>&任务" * 80
    enqueue_due_reminders(db, CLOCK)
    calls = capture(monkeypatch)
    integrations.send_wecom(db, digest(db))
    content = calls[0]["content"]
    assert "card" not in calls[0]
    assert len(content.encode()) <= 2048
    assert "<script>" not in content
    assert "7 项任务" in content
    assert "另有 4 项" in content
    assert "3." in unescape(content)


@pytest.mark.parametrize("state", ["sent", "sending", "uncertain", "pending"])
def test_legacy_individual_reminders_do_not_duplicate_after_upgrade(db, team, draft_factory, state):
    task = assigned(db, team, draft_factory, 5)
    old = OutboxEvent(event_key=f"deadline:2026-11-05:{task.id}:2026-11-05:{team['a'].id}", channel="wecom", state=state,
                      payload={"deadline_reminder": True, "reminder_day": "2026-11-05", "task_id": task.id,
                               "deadline_due_date": "2026-11-05", "user_ids": [team["a"].id]})
    db.add(old)
    db.flush()
    assert enqueue_due_reminders(db, CLOCK) == (1 if state == "pending" else 0)
    assert old.state == ("skipped" if state == "pending" else state)


def test_digest_respects_direct_switch_when_general_notifications_off(db, team, draft_factory, monkeypatch):
    assigned(db, team, draft_factory, 5, direct=True)
    assigned(db, team, draft_factory, 6)
    db.add(SystemSetting(key="reminders", value={"enabled": False, "deadline_enabled": True}))
    monkeypatch.setattr("app.services.business.get_settings", lambda: type("Settings", (), {"wecom_direct_notifications_enabled": True})())
    assert enqueue_due_reminders(db, CLOCK) == 1
    assert len(digest(db).payload["deadline_items"]) == 1
    calls = capture(monkeypatch)
    assert integrations.send_wecom(db, digest(db))["msgid"] == "isolated-card"
    assert len(calls) == 1


def test_digest_link_only_returns_own_still_valid_tasks(db, team, draft_factory):
    first = assigned(db, team, draft_factory, 5)
    second = assigned(db, team, draft_factory, 6)
    enqueue_due_reminders(db, CLOCK)
    second.status = "已完成"
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            for who, expected in [("a", [first.id]), ("b", []), ("host", [])]:
                response = client.get("/api/tasks?mine=false&reminder_day=2026-11-05&offset=0",
                                      headers={"Authorization": "Bearer " + issue_token(team[who])})
                assert response.status_code == 200, response.text
                assert [item["id"] for item in response.json()["items"]] == expected
            assert client.get("/api/tasks?reminder_day=garbage", headers={"Authorization": "Bearer " + issue_token(team["a"])}).status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_meeting_text_uses_published_snapshot_and_revision_title(db, team, draft_factory, monkeypatch):
    from app.services.business import create_meeting

    meeting = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    calls = mock_sender(monkeypatch, team)
    integrations.send_wecom(db, publication_events(db, meeting)[0])
    content = calls[-1]["text"]
    assert "card" not in calls[-1]
    assert content.startswith("【会议纪要已发布】\n\n")
    assert "查看会议任务单</a>" in content
    assert content.count("会议日期") == 1
    revision(db, team["host"], meeting.id, "补充事项")
    draft = MeetingDraft.model_validate(meeting.draft)
    draft.decisions = "新增内容"
    save_meeting(db, team["host"], meeting.id, draft, meeting.draft_version)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=meeting.draft_version, reason="补充事项"))
    integrations.send_wecom(db, publication_events(db, meeting)[-1])
    assert calls[-1]["text"].startswith("【会议纪要已更新】")
    assert "card" not in calls[-1]


def test_wecom_transport_really_sends_textcard_without_raw_text(monkeypatch):
    from app.core.config import get_settings

    for name, value in [("wecom_corp_id", "test"), ("wecom_secret", "fake"), ("wecom_agent_id", "1")]:
        monkeypatch.setattr(get_settings(), name, value)
    calls = []
    monkeypatch.setattr(integrations.WeCom, "request", lambda self, path, **kwargs: calls.append(kwargs["body"]) or {"msgid": "fake"})
    card = integrations.notification_card("通知", ["内容"], "http://example.test/tasks", "查看任务")
    integrations.WeCom().send(["fake-user"], "fallback", card=card)
    assert calls[0]["msgtype"] == "textcard" and calls[0]["textcard"] == card
    assert "text" not in calls[0] and calls[0]["enable_duplicate_check"] == 1


@pytest.mark.parametrize("body", ["超长任务" * 1000, '<a href="https://wrong.example">伪造链接</a>&' * 200, "中文🚀é" * 1000])
def test_text_markup_only_contains_generated_link_and_preserves_metadata(body):
    from html.parser import HTMLParser

    class Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links = []
            self.tags = []

        def handle_starttag(self, tag, attrs):
            self.tags.append(tag)
            if tag == "a":
                self.links.append(dict(attrs)["href"])

    url = "http://example.test/tasks?id=abc&reminder_day=2026-11-12"
    content = integrations.wecom_text("会议纪要已发布", body, url, action="查看会议任务单",
        details=["会议日期：2026-11-12"], due_date=date(2026, 11, 13), source_title="会议<&标题")
    parser = Parser()
    parser.feed(content)
    assert parser.tags == ["a"] and parser.links == [url]
    assert len(content.encode()) <= 2048 and "�" not in content
    assert content.startswith("【会议纪要已发布】\n\n")
    assert "会议日期：2026-11-12" in content and "计划完成日期：2026-11-13" in content
    assert "来源会议：会议&lt;&amp;标题" in content


def test_publication_transport_uses_text_payload(monkeypatch):
    from app.core.config import get_settings

    for name, value in [("wecom_corp_id", "test"), ("wecom_secret", "fake"), ("wecom_agent_id", "1")]:
        monkeypatch.setattr(get_settings(), name, value)
    calls = []
    monkeypatch.setattr(integrations.WeCom, "request", lambda self, path, **kwargs: calls.append(kwargs["body"]) or {"msgid": "fake"})
    content = integrations.wecom_text("会议纪要已发布", "会议名称：测试会议", "http://example.test/meetings/id", action="查看会议任务单")
    integrations.WeCom().send(["fake-user"], content)
    assert calls[0]["msgtype"] == "text" and calls[0]["text"]["content"] == content
    assert "textcard" not in calls[0] and calls[0]["enable_duplicate_check"] == 1


def test_assignment_verification_and_rejection_use_actionable_cards(db, team, draft_factory, monkeypatch):
    from app.schemas import ApplyCompletion, VerifyCompletion
    from app.services.business import apply_completion, verify_completion

    task = assigned(db, team, draft_factory, 6, direct=True)
    task.content = "设备验收资料整理"
    team["host"].wecom_id = "isolated-verifier"
    calls = capture(monkeypatch)
    event = OutboxEvent(event_key="isolated-direct-card", channel="wecom", payload={
        "task_id": task.id, "task_version": task.version, "user_ids": [team["a"].id],
        "title": "新的直接派发任务", "content": task.content,
    })
    integrations.send_wecom(db, event)
    assert calls[-1]["card"]["title"] == "你收到一项新任务"
    assert calls[-1]["card"]["description"].count(task.content) == 1
    assert task.code not in calls[-1]["card"]["description"]
    request = apply_completion(db, team["a"], task.id,
        ApplyCompletion(expected_version=task.version, idempotency_key="isolated-card-request", explanation="验收完成"))
    pending = db.scalar(select(OutboxEvent).where(OutboxEvent.event_key == f"request:{request.id}:verify"))
    integrations.send_wecom(db, pending)
    assert calls[-1]["card"]["title"] == "有任务待你验证"
    assert calls[-1]["card"]["btntxt"] == "去验证"
    assert request.id in calls[-1]["card"]["url"]
    verify_completion(db, team["host"], request.id, VerifyCompletion(decision="rejected", opinion="请补充签字"))
    rejected = db.scalar(select(OutboxEvent).where(OutboxEvent.event_key == f"request:{request.id}:result"))
    integrations.send_wecom(db, rejected)
    assert calls[-1]["card"]["title"] == "任务验证未通过"
    assert calls[-1]["card"]["btntxt"] == "查看原因"
    assert "请补充签字" in calls[-1]["card"]["description"]


def test_sending_logs_keep_digest_task_ids_and_recipient(db, team, draft_factory, monkeypatch):
    from app.models import AuditLog
    from app.services.operation_logs import log_delivery

    team["host"].username = "admin"
    task = assigned(db, team, draft_factory, 5)
    enqueue_due_reminders(db, CLOCK)
    event = digest(db)
    capture(monkeypatch)
    result = integrations.send_wecom(db, event)
    event.state = "sent"
    log_delivery(db, event, result)
    db.flush()
    entry = db.scalar(select(AuditLog).where(AuditLog.entity_id == event.id))
    assert entry.after["deadline_items"] == [{"task_id": task.id, "due_date": "2026-11-05"}]
    assert entry.after["detail"]["task_ids"] == [task.id]
    assert team["a"].name in str(entry.after["recipients"])
