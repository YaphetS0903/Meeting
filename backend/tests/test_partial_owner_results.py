from copy import deepcopy
from datetime import date
from io import BytesIO

import openpyxl
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pypdf import PdfReader
from sqlalchemy import select

from app.core.db import get_db
from app.core.security import issue_token
from app.main import app
from app.models import Assessment, MeetingVersion, Task
from app.schemas import MeetingDraft, MeetingItem, PublishRequest
from app.services import business
from app.services.exports import pdf_minutes, xlsx_minutes
from app.services.reporting import report


def release(db, actor, meeting):
    business.publish(db, actor, meeting.id, PublishRequest(
        expected_version=meeting.draft_version, notify=False, reason="核对逐人完成结果"))


def mixed_review(db, team, draft_factory, extend=False):
    owners = [team[key].id for key in ("a", "b", "colleague", "outsider", "host")]
    initial = draft_factory(when=date(2026, 11, 9))
    initial.items[0].owner_ids = owners
    initial.items[0].due_date = date(2026, 12, 31)
    first = business.create_meeting(db, team["host"], initial)
    release(db, team["host"], first)
    current = business.continue_meeting(db, team["host"], first.id)
    draft = MeetingDraft.model_validate(current.draft)
    draft.meeting_date = date(2026, 11, 13)
    item = draft.items[0]
    item.status = "部分完成"
    item.owner_results = {owner: "已完成" if index < 2 else "未完成" for index, owner in enumerate(owners)}
    if extend:
        arrangement = item.model_copy(deep=True)
        arrangement.id, arrangement.section, arrangement.status = "extension", "new", "未到期"
        arrangement.owner_results = {}
        arrangement.owner_ids = owners[2:]
        arrangement.source = {"extended_from_item_id": item.id}
        draft.items.append(arrangement)
    business.save_meeting(db, team["host"], current.id, draft, current.draft_version)
    release(db, team["host"], current)
    return first, current, owners


@pytest.mark.parametrize("results", [{}, {"a": "已完成"}, {"a": "已完成", "b": "未完成", "foreign": "已完成"},
                                    {"a": "已完成", "b": "未到期"}])
def test_mixed_requires_exact_owners_and_binary_results(results):
    with pytest.raises(ValidationError):
        MeetingItem(section="review", status="部分完成", owner_ids=["a", "b"], owner_results=results)


@pytest.mark.parametrize("status", ["已完成", "未完成"])
def test_uniform_person_results_normalize_to_existing_status(status):
    item = MeetingItem(section="review", status="部分完成", owner_ids=["a", "b"],
                       owner_results={"a": status, "b": status})
    assert item.status == status and item.owner_results == {}
    assert MeetingItem(section="review", status=status, owner_ids=["a", "b"]).owner_results == {}


def test_mixed_is_not_a_new_arrangement_status():
    with pytest.raises(ValidationError):
        MeetingItem(section="new", status="部分完成", owner_ids=["a", "b"],
                    owner_results={"a": "已完成", "b": "未完成"})


@pytest.mark.parametrize("extend", [False, True])
def test_partial_publish_keeps_per_person_counts_and_continues_only_unfinished(db, team, draft_factory, extend):
    first, current, owners = mixed_review(db, team, draft_factory, extend)
    task_id = first.draft["items"][0]["task_id"]
    task = db.get(Task, task_id)
    assert task.closed_at is None and not task.completion_reviewed
    assert business.effective_completed_task_ids(db, [task_id]) == set()
    assert business.effective_terminal_review_task_ids(db, [task_id]) == set()
    assessments = list(db.scalars(select(Assessment).where(Assessment.meeting_id == current.id)))
    assert {a.user_id: a.status for a in assessments} == {
        owner: "已完成" if owner in owners[:2] else "未完成" for owner in owners}
    data = report(db, team["host"], date(2026,11,1), date(2026,11,30), group_by="department_people")
    assert data["summary"]["counts"]["已完成"] == 2
    assert data["summary"]["counts"]["未完成"] == 3
    assert data["summary"]["rate"] == pytest.approx(0.4)
    for selected in (first, current):
        followup = business.continue_meeting(db, team["host"], selected.id)
        assert len(followup.draft["items"]) == (1 if extend else 0)
        if extend:
            row = followup.draft["items"][0]
            assert set(row["owner_ids"]) == set(owners[2:])
            assert row["status"] == "未到期" and row["owner_results"] == {}
    carried = business.carry_forward(db, team["host"], team["series"].id)
    assert len(carried) == (1 if extend else 0)
    if extend:
        assert set(carried[0]["owner_ids"]) == set(owners[2:])


def test_revision_of_individual_results_keeps_history_and_updates_latest_statistics(db, team, draft_factory):
    _, current, owners = mixed_review(db, team, draft_factory)
    original = deepcopy(current.draft)
    business.revision(db, team["host"], current.id, "调整人员完成结果")
    draft = MeetingDraft.model_validate(current.draft)
    draft.items[0].owner_results[owners[2]] = "已完成"
    business.save_meeting(db, team["host"], current.id, draft, current.draft_version)
    release(db, team["host"], current)
    old = db.scalar(select(MeetingVersion).where(MeetingVersion.meeting_id == current.id, MeetingVersion.number == 1))
    assert old.snapshot == original
    data = report(db, team["host"], date(2026,11,1), date(2026,11,30), group_by="department_people")
    assert data["summary"]["items"] == 5 and data["summary"]["rate"] == pytest.approx(0.6)


def test_partial_results_survive_reload_preview_exports_and_meeting_list_rate(db, team, draft_factory):
    _, current, owners = mixed_review(db, team, draft_factory)
    before = deepcopy(current.draft)
    book = openpyxl.load_workbook(BytesIO(xlsx_minutes(before)))
    result = book["上期任务回顾"]["F5"].value
    assert "部分完成（2/5）" in result
    details = book["上期任务回顾"]["G5"].value
    assert f"{team['a'].name}：已完成" in details and f"{team['outsider'].name}：未完成" in details
    pdf = pdf_minutes(before)
    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)
    assert "部分完成" in text and team["a"].name in text and team["outsider"].name in text
    app.dependency_overrides[get_db] = lambda: db
    headers = {"Authorization": "Bearer " + issue_token(team["host"])}
    try:
        with TestClient(app) as client:
            loaded = client.get(f"/api/meetings/{current.id}", headers=headers)
            assert loaded.status_code == 200
            assert loaded.json()["draft"]["items"][0]["owner_results"] == before["items"][0]["owner_results"]
            listed = client.get("/api/meetings", headers=headers).json()["items"]
            assert next(m for m in listed if m["id"] == current.id)["rate"] == pytest.approx(0.4)
            business.revision(db, team["host"], current.id, "核对预览")
            preview = client.post(f"/api/meetings/{current.id}/preview", headers=headers,
                                  json={"expected_version": current.draft_version})
            assert preview.status_code == 200 and preview.content.startswith(b"%PDF")
    finally:
        app.dependency_overrides.clear()
