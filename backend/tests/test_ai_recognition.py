from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.services.ai_recognition import AIRecognitionFailed, AIRecognitionUnavailable, suggest


def configure(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_base_url", "https://example.invalid/v3")
    monkeypatch.setattr(settings, "ai_api_key", "test-key")
    monkeypatch.setattr(settings, "ai_model", "test-model")


def historical_candidate(department_id: str):
    return SimpleNamespace(
        original={
            "title": "生产例会",
            "department_name": "生产部",
            "meeting_date": "2026-08-18",
            "items": [
                {
                    "id": "row-1",
                    "category": "质量",
                    "content": "完成整改并提交报告",
                    "section": "unknown",
                    "raw_status": "完成",
                    "raw_due_date": "8月17日",
                    "owner_names": ["责任人呀"],
                    "verifier_name": "李四",
                }
            ],
        },
        corrected={
            "department_id": department_id,
            "items": [{"id": "row-1", "owner_ids": []}],
        },
    )


def test_ai_suggestions_are_validated_and_do_not_mutate_candidate(monkeypatch, db, team):
    configure(monkeypatch)
    candidate = historical_candidate(team["dep"].id)
    original = candidate.original.copy()
    sent = {}

    def sender(body):
        sent.update(body)
        return {
            "choices": [
                {
                    "message": {
                        "content": """```json
                        {"suggestions":[{"id":"row-1","section":"review","status":"已完成",
                        "due_date":"2026-08-17","confidence":0.96,"reason":"原状态为完成",
                        "owner_interpretation":"原文为单人姓名",
                        "owner_matches":[{"original_name":"责任人呀",
                        "user_id":"%s","confidence":0.88,"reason":"姓名相近且同部门"}]}]}
                        ```"""
                        % team["a"].id
                    }
                }
            ]
        }

    result = suggest(db, candidate, ["row-1"], sender=sender)
    assert result["model"] == "test-model"
    assert result["suggestions"][0]["status"] == "已完成"
    assert result["suggestions"][0]["due_date"] == "2026-08-17"
    assert sent["temperature"] == 0
    assert sent["thinking"] == {"type": "disabled"}
    assert result["suggestions"][0]["owner_matches"][0]["name"] == "责任人甲"
    assert sent["max_tokens"] == 1000
    owner_candidates = sent["messages"][1]["content"]
    assert team["a"].id in owner_candidates
    assert "责任人甲" in owner_candidates
    assert candidate.original == original


def test_ai_rejects_hallucinated_or_invalid_results(monkeypatch, db, team):
    configure(monkeypatch)

    def sender(_):
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"suggestions":[{"id":"other","section":"review",'
                        '"status":"大概完成","confidence":2,"reason":"猜测"}]}'
                    }
                }
            ]
        }

    with pytest.raises(AIRecognitionFailed):
        suggest(db, historical_candidate(team["dep"].id), ["row-1"], sender=sender)


def test_ai_rejects_people_outside_server_candidates(monkeypatch, db, team):
    configure(monkeypatch)

    def sender(_):
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"suggestions":[{"id":"row-1","confidence":0.7,'
                        '"reason":"测试","owner_matches":[{"original_name":"责任人呀",'
                        '"user_id":"invented-user","confidence":0.8,"reason":"猜测"}]}]}'
                    }
                }
            ]
        }

    with pytest.raises(AIRecognitionFailed, match="候选名单之外"):
        suggest(db, historical_candidate(team["dep"].id), ["row-1"], sender=sender)


def test_ai_requires_complete_server_configuration(monkeypatch, db, team):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_base_url", "")
    monkeypatch.setattr(settings, "ai_api_key", "")
    monkeypatch.setattr(settings, "ai_model", "")
    with pytest.raises(AIRecognitionUnavailable):
        suggest(db, historical_candidate(team["dep"].id), ["row-1"], sender=lambda _: {})
