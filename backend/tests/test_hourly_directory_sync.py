from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import Mock

import httpx
from sqlalchemy import select

from app import worker
from app.core.config import get_settings
from app.models import AuditLog, MeetingVersion, Membership, Task, User, now
from app.schemas import PublishRequest
from app.services import business, directory_sync, integrations


def test_full_sync_adds_hires_disables_departures_and_preserves_published_history(
    db, team, draft_factory, monkeypatch,
):
    team["dep"].wecom_id, team["other"].wecom_id = "1", "2"
    team["a"].wecom_id, team["b"].wecom_id = "departed", "moved"
    meeting = business.create_meeting(db, team["host"], draft_factory())
    business.publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
    frozen = db.scalar(select(MeetingVersion)).snapshot
    task_id = db.scalar(select(Task.id))
    monkeypatch.setattr(get_settings(), "wecom_directory_secret", "directory-test-secret")

    members = [
        {"userid": "hire", "name": "金丽君", "status": 1, "department": [1]},
        {"userid": "moved", "name": "责任人乙", "status": 1, "department": [2]},
        {"userid": "disabled", "name": "停用成员", "status": 2, "department": [1]},
    ]

    class Provider:
        def __init__(self, directory=False):
            self.directory = directory

        def get(self, path, **params):
            if path == "agent/get":
                return {}
            if path == "department/list":
                return {"department": [
                    {"id": 1, "name": "技术部", "parentid": 0},
                    {"id": 2, "name": "财务部", "parentid": 0},
                ]}
            assert path == "user/list"
            users = [u for u in members if params["department_id"] in u["department"]]
            return {"userlist": [{**u, **({"biz_mail": u["userid"] + "@example.com"}
                                        if self.directory else {})} for u in users]}

    monkeypatch.setattr(integrations, "WeCom", Provider)
    result = directory_sync.synchronize_directory(db, team["host"], automatic=True)
    assert result["state"] == "succeeded" and result["result"]["users"] == 3
    hire = db.scalar(select(User).where(User.wecom_id == "hire"))
    assert hire.active and hire.name == "金丽君" and hire.email == "hire@example.com"
    assert not team["a"].active
    assert not db.scalar(select(User).where(User.wecom_id == "disabled")).active
    assert team["b"].department_id == team["other"].id
    assert list(db.scalars(select(Membership.department_id).where(Membership.user_id == team["b"].id))) == [team["other"].id]
    assert db.get(Task, task_id) and set(business.owner_ids(db, db.get(Task, task_id))) == {team["a"].id, team["b"].id}
    assert db.scalar(select(MeetingVersion)).snapshot == frozen
    assert team["host"].active  # Local accounts are never removed by remote directory sync.


def test_automatic_hour_is_deduplicated_but_manual_sync_can_run_immediately(db, team):
    call = Mock(return_value={"users": 1, "departments": 1})
    assert directory_sync.synchronize_directory(db, team["host"], automatic=True, synchronizer=call)["state"] == "succeeded"
    assert directory_sync.synchronize_directory(db, team["host"], automatic=True, synchronizer=call)["state"] == "skipped"
    assert directory_sync.synchronize_directory(db, team["host"], synchronizer=call)["state"] == "succeeded"
    assert call.call_count == 2
    automatic = db.scalar(select(AuditLog).where(AuditLog.after["mode"].as_string() == "automatic"))
    automatic.created_at = now() - timedelta(hours=1)
    db.flush()
    assert directory_sync.synchronize_directory(db, team["host"], automatic=True, synchronizer=call)["state"] == "succeeded"


def test_automatic_failure_rolls_back_partial_changes_and_keeps_safe_diagnostic(db, team):
    db.commit()

    def broken(session):
        team["a"].active = False
        session.add(User(name="半途写入不应保留", wecom_id="partial"))
        session.flush()
        raise httpx.ConnectError("https://provider.example.com?secret=private-secret")

    result = directory_sync.synchronize_directory(db, team["host"], automatic=True, synchronizer=broken)
    db.commit()
    assert result["state"] == "failed" and "private-secret" not in str(result)
    assert team["a"].active
    assert not db.scalar(select(User).where(User.wecom_id == "partial"))
    log = db.scalar(select(AuditLog).where(AuditLog.action == "directory.sync.failed"))
    assert log.after["mode"] == "automatic" and "ConnectError" in log.after["error"]


def test_worker_executes_hourly_task_with_automatic_mode_and_active_admin(db, team, monkeypatch):
    config = get_settings()
    for key in ("wecom_corp_id", "wecom_secret", "wecom_agent_id"):
        monkeypatch.setattr(config, key, "isolated-value")

    @contextmanager
    def transaction():
        yield db

    monkeypatch.setattr(worker, "SessionLocal", Mock(begin=transaction))
    execute = Mock(return_value={"state": "succeeded"})
    monkeypatch.setattr(directory_sync, "synchronize_directory", execute)
    assert worker.directory_sync.run() == {"state": "succeeded"}
    execute.assert_called_once_with(db, team["host"], automatic=True)
    schedule = worker.celery.conf.beat_schedule["directory-sync"]
    assert schedule["task"] == worker.directory_sync.name
    assert schedule["schedule"].minute == {0}
    assert schedule["schedule"].hour == set(range(24))
    assert schedule["options"]["expires"] < 3600


def test_concurrent_sync_skips_before_calling_provider():
    session = Mock()
    session.get_bind.return_value.dialect.name = "postgresql"
    session.scalar.return_value = False
    execute = Mock()
    assert directory_sync.synchronize_directory(session, Mock(), automatic=True, synchronizer=execute)["state"] == "busy"
    execute.assert_not_called()
