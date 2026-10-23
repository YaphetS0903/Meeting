# ruff: noqa: F811 -- imported pytest fixture
from sqlalchemy import select

from app.models import Task
from app.schemas import ApplyCompletion, PublishRequest, TaskChange, VerifyCompletion
from app.services.business import apply_completion, change_task, create_meeting, publish, verify_completion
from tests.test_admin_people import client, headers  # noqa: F401


def test_verifier_sees_application_version_requirements_and_deliverable(client, db, team, draft_factory):
    draft = draft_factory()
    draft.items[0].requirements = "初版：核对 10 个样品"
    draft.items[0].deliverable = "初版检验记录"
    meeting = create_meeting(db, team["host"], draft)
    publish(db, team["host"], meeting.id, PublishRequest(expected_version=1))
    task = db.scalar(select(Task))
    first = apply_completion(
        db,
        team["a"],
        task.id,
        ApplyCompletion(expected_version=1, idempotency_key="first-review", explanation="初版材料"),
    )
    data = client.get("/api/verifications", headers=headers(team["host"])).json()[0]
    assert data["task"]["requirements"] == "初版：核对 10 个样品" and data["can_verify"]
    assert data["task"]["deliverable"] == "初版检验记录" and data["source_meeting_id"] == meeting.id
    change_task(
        db,
        team["host"],
        task,
        TaskChange(
            expected_version=1,
            content=task.content,
            requirements="新版：核对 20 个样品",
            deliverable="新版检验记录",
            due_date=task.due_date,
            owner_ids=[team["a"].id],
            verifier_id=team["host"].id,
            reason="提高验收标准",
        ),
    )
    second = apply_completion(
        db,
        team["a"],
        task.id,
        ApplyCompletion(expected_version=2, idempotency_key="second-review", explanation="新版材料"),
    )
    data = {r["id"]: r for r in client.get("/api/verifications", headers=headers(team["host"])).json()}
    assert data[first.id]["state"] == "invalidated" and not data[first.id]["can_verify"]
    assert data[first.id]["task"]["requirements"] == "初版：核对 10 个样品"
    assert data[second.id]["task"]["requirements"] == "新版：核对 20 个样品" and data[second.id]["can_verify"]
    assert client.get("/api/verifications", headers=headers(team["outsider"])).json() == []
    verify_completion(
        db, team["host"], second.id, VerifyCompletion(decision="approved", opinion="新版验收通过")
    )
    data = {r["id"]: r for r in client.get("/api/verifications", headers=headers(team["host"])).json()}
    assert not data[second.id]["can_verify"] and data[second.id]["task"]["version"] == 2
