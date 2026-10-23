from copy import deepcopy

import pytest
from fastapi import HTTPException

from app.models import ImportBatch, ImportCandidate, ImportSource, User, ValidationIssue
from app.services.importing import confirm_candidate, correct_candidate, remaining_problems, resolve_name


def candidate(db, team):
    batch = ImportBatch(name="校验", creator_id=team["host"].id)
    db.add(batch)
    db.flush()
    source = ImportSource(batch_id=batch.id, filename="会议.xlsx", sha256="b" * 64)
    db.add(source)
    db.flush()
    p = {
        "title": "周例会",
        "kind": "department",
        "department_id": team["dep"].id,
        "department_name": "技术部",
        "host_id": team["host"].id,
        "recorder_id": team["host"].id,
        "meeting_date": "2026-08-18",
        "items": [
            {
                "id": "line-1",
                "section": "review",
                "content": "检验流程",
                "owner_ids": [team["a"].id],
                "owner_names": ["责任人甲", "责任人乙"],
                "due_date": "2026-08-17",
                "status": "未完成",
                "source_row": 18,
                "source_sheet": "Sheet1",
                "verifier_name": "",
            }
        ],
    }
    c = ImportCandidate(
        source_id=source.id,
        sheet_name="Sheet1",
        original=deepcopy(p),
        corrected=deepcopy(p),
        content_hash="c" * 64,
    )
    db.add(c)
    db.flush()
    issue = ValidationIssue(
        candidate_id=c.id,
        source_id=source.id,
        field_path="items.0.owner_ids",
        code="owners_unmatched",
        message="责任人未全部匹配",
    )
    db.add(issue)
    db.flush()
    return c, issue


def test_partial_people_need_explicit_acknowledgement(db, team):
    c, issue = candidate(db, team)
    with pytest.raises(HTTPException):
        confirm_candidate(db, team["host"], c.id, 1, "确认")
    p = deepcopy(c.corrected)
    p["items"][0]["owner_ids"].append(team["b"].id)
    p["confirmed_issue_ids"] = [issue.id]
    correct_candidate(db, team["host"], c.id, p, 1, "逐一核对责任人")
    meeting = confirm_candidate(db, team["host"], c.id, 2, "已核对原文")
    assert meeting.historical and meeting.published_version == 1


def test_named_verifier_cannot_fall_back_to_host(db, team):
    c, issue = candidate(db, team)
    p = deepcopy(c.corrected)
    p["confirmed_issue_ids"] = [issue.id]
    p["items"][0]["verifier_name"] = "未知验证人"
    correct_candidate(db, team["host"], c.id, p, 1, "选择人员")
    assert any("原文指定" in p["message"] for p in remaining_problems(db, c))


def test_duplicate_name_prefers_single_member_in_meeting_department(db, team):
    local = User(name="同名成员", department_id=team["dep"].id)
    remote = User(name="同名成员", department_id=team["other"].id)
    db.add_all([local, remote])
    db.flush()

    assert resolve_name(db, "同名成员", team["dep"].id) == local.id
    assert resolve_name(db, "同名成员") is None


def test_special_import_does_not_require_department_or_recorder(db, team):
    c, issue = candidate(db, team)
    value = deepcopy(c.corrected)
    value.update(kind="special", department_id=None, recorder_id=None)
    value["items"][0]["owner_ids"].append(team["b"].id)
    value["confirmed_issue_ids"] = [issue.id]
    correct_candidate(db, team["host"], c.id, value, 1, "专项会议无需部门，编制人留空")

    assert not any(
        problem["field"] in {"department_id", "recorder_id"} for problem in remaining_problems(db, c)
    )
    meeting = confirm_candidate(db, team["host"], c.id, 2, "整场会议已核对")
    assert meeting.kind == "special"
    assert meeting.department_id is None
    assert meeting.recorder_id is None


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(items="bad"),
        lambda p: p["items"].clear(),
        lambda p: p["items"][0].update(due_date="每周五"),
        lambda p: p["items"][0].update(source_row=999),
    ],
)
def test_malformed_and_lost_provenance_rejected(db, team, mutation):
    c, _ = candidate(db, team)
    p = deepcopy(c.corrected)
    mutation(p)
    with pytest.raises(HTTPException) as error:
        correct_candidate(db, team["host"], c.id, p, 1, "test")
    assert error.value.status_code == 422


def test_meeting_host_can_correct_own_candidate_but_other_member_cannot(db, team):
    host = User(name="历史会议主持人", department_id=team["dep"].id, is_admin=False)
    db.add(host)
    db.flush()
    c, issue = candidate(db, team)
    c.corrected["host_id"] = host.id
    c.original["host_id"] = host.id
    payload = deepcopy(c.corrected)
    payload["confirmed_issue_ids"] = [issue.id]
    payload["items"][0]["owner_ids"].append(team["b"].id)

    correct_candidate(db, host, c.id, payload, 1, "会议主持人核对原文")
    with pytest.raises(HTTPException) as error:
        correct_candidate(db, team["outsider"], c.id, payload, 2, "无权核对")
    assert error.value.status_code == 403


def test_meeting_host_can_confirm_own_candidate(db, team):
    host = User(name="非管理员主持人", department_id=team["dep"].id, is_admin=False)
    db.add(host)
    db.flush()
    c, issue = candidate(db, team)
    c.corrected["host_id"] = host.id
    c.original["host_id"] = host.id
    payload = deepcopy(c.corrected)
    payload["confirmed_issue_ids"] = [issue.id]
    payload["items"][0]["owner_ids"].append(team["b"].id)
    correct_candidate(db, host, c.id, payload, 1, "主持人核对历史会议")
    meeting = confirm_candidate(db, host, c.id, 2, "主持人确认无误并入库")
    assert meeting.historical and meeting.host_id == host.id and c.state == "confirmed"


def test_cross_department_host_can_confirm_historical_candidate(db, team):
    host = User(name="跨部门历史主持人", department_id=team["other"].id, is_admin=False)
    db.add(host)
    db.flush()
    c, issue = candidate(db, team)
    c.corrected["host_id"] = host.id
    c.original["host_id"] = host.id
    payload = deepcopy(c.corrected)
    payload["confirmed_issue_ids"] = [issue.id]
    payload["items"][0]["owner_ids"].append(team["b"].id)
    correct_candidate(db, host, c.id, payload, 1, "跨部门主持人核对历史会议")
    meeting = confirm_candidate(db, host, c.id, 2, "跨部门主持人确认")
    assert meeting.historical and meeting.host_id == host.id
