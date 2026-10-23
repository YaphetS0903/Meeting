from fastapi.testclient import TestClient

from app.core.db import get_db
from app.core.security import issue_token
from app.main import app


def test_suggestions_rank_people_and_dates_without_auto_linking(db, team, draft_factory):
    from datetime import date

    from sqlalchemy import select

    from app.models import Task
    from app.schemas import PublishRequest
    from app.services.business import create_meeting, publish

    exact = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], exact.id, PublishRequest(expected_version=1))
    other = draft_factory()
    other.items[0].owner_ids = [team["colleague"].id]
    other.items[0].due_date = date(2026, 12, 31)
    second = create_meeting(db, team["host"], other)
    publish(db, team["host"], second.id, PublishRequest(expected_version=1))
    original_task = db.scalar(select(Task).where(Task.source_meeting_id == exact.id))
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            result = client.get(
                "/api/task-suggestions",
                params={
                    "q": "完成检验流程修订",
                    "series_id": team["series"].id,
                    "owner_ids": team["a"].id,
                    "due_date": "2026-08-17",
                },
                headers={"Authorization": "Bearer " + issue_token(team["host"])},
            ).json()
            assert result[0]["id"] == original_task.id
            assert "截止日期相同" in result[0]["match_reasons"]
            assert result[0]["score"] > result[1]["score"]
            assert original_task.version == 1
    finally:
        app.dependency_overrides.clear()


def test_api_auth_and_roundtrip(db, team, draft_factory):
    def override():
        yield db

    app.dependency_overrides[get_db] = override
    try:
        with TestClient(app) as client:
            assert client.get("/api/meetings").status_code == 401
            token = issue_token(team["host"])
            headers = {"Authorization": "Bearer " + token}
            response = client.post(
                "/api/meetings", json=draft_factory().model_dump(mode="json"), headers=headers
            )
            assert response.status_code == 200, response.text
            identifier = response.json()["id"]
            published = client.post(
                f"/api/meetings/{identifier}/publish", headers=headers, json={"expected_version": 1}
            )
            assert published.status_code == 200, published.text
            assert len(client.get("/api/tasks", headers=headers).json()) == 1
            outsider = {"Authorization": "Bearer " + issue_token(team["outsider"])}
            assert client.get(f"/api/meetings/{identifier}", headers=outsider).status_code == 403
            assert client.get("/api/validation", headers=outsider).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_validation_list_returns_extraction_summary(db, team):
    from app.models import ImportBatch, ImportCandidate, ImportSource, ValidationIssue

    batch = ImportBatch(name="历史", creator_id=team["host"].id)
    db.add(batch)
    db.flush()
    source = ImportSource(batch_id=batch.id, filename="历史.xlsx", sha256="a" * 64)
    db.add(source)
    db.flush()
    payload = {
        "title": "历史例会",
        "department_name": "技术部",
        "meeting_date": "2026-08-10",
        "items": [
            {"id": "1", "section": "review"},
            {"id": "2", "section": "new"},
            {"id": "3", "section": "unknown"},
        ],
    }
    candidate = ImportCandidate(
        source_id=source.id,
        sheet_name="Sheet1",
        original=payload,
        corrected=payload,
        content_hash="b" * 64,
    )
    db.add(candidate)
    db.flush()
    db.add_all(
        [
            ValidationIssue(
                candidate_id=candidate.id,
                source_id=source.id,
                code="invalid_status",
                message="状态需确认",
            ),
            ValidationIssue(
                candidate_id=candidate.id,
                source_id=source.id,
                code="invalid_status",
                message="状态需确认",
            ),
        ]
    )
    db.flush()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/validation",
                headers={"Authorization": "Bearer " + issue_token(team["host"])},
            )
            assert response.status_code == 200
            result = response.json()
            assert result["total"] == 1
            assert result["items"][0]["issue_count"] == 2
            assert result["summary"] == {
                "item_count": 3,
                "section_counts": {"review": 1, "new": 1, "unknown": 1},
                "issue_counts": {"invalid_status": 2},
            }
    finally:
        app.dependency_overrides.clear()


def test_history_search_dates_and_uploaded_file_location(db, team):
    from app.models import ImportBatch, ImportCandidate, ImportSource

    batch = ImportBatch(name="历史上传", creator_id=team["host"].id)
    db.add(batch)
    db.flush()
    source = ImportSource(batch_id=batch.id, filename="八月会议.xlsx", sha256="1" * 64)
    db.add(source)
    db.flush()
    duplicate = ImportSource(
        batch_id=batch.id, filename="八月会议副本.xlsx", sha256="1" * 64, duplicate_of=source.id
    )
    db.add(duplicate)
    db.flush()
    for title, day, state in [
        ("项目复盘会", "2026-08-15", "pending"),
        ("七月例会", "2026-07-12", "out_of_scope"),
        ("九月例会", "2026-09-01", "confirmed"),
    ]:
        payload = {
            "title": title,
            "meeting_date": day,
            "kind": "department",
            "department_id": team["dep"].id,
            "host_id": team["a"].id,
            "items": [],
        }
        db.add(
            ImportCandidate(
                source_id=source.id,
                sheet_name=title,
                original=payload,
                corrected=payload,
                content_hash=(title.encode().hex() + "0" * 64)[:64],
                state=state,
            )
        )
    db.flush()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer " + issue_token(team["host"])}
            response = client.get(
                "/api/validation",
                params={
                    "state": "actionable",
                    "q": "项目复盘",
                    "start": "2026-08-01",
                    "end": "2026-08-31",
                },
                headers=headers,
            )
            assert response.status_code == 200, response.text
            assert response.json()["total"] == 1
            assert response.json()["items"][0]["title"] == "项目复盘会"
            assert response.json()["state_counts"] == {"pending": 1}
            assert client.get(
                "/api/validation",
                params={"q": "八月会议.xlsx", "start": "2026-08-01", "end": "2026-08-31"},
                headers=headers,
            ).json()["total"] == 1

            sources = {row["id"]: row for row in client.get("/api/import-sources", headers=headers).json()}
            assert sources[duplicate.id]["duplicate_of"] == source.id
            assert sources[duplicate.id]["original_batch_id"] == batch.id
            assert sources[source.id]["state_counts"] == {
                "pending": 1,
                "out_of_scope": 1,
                "confirmed": 1,
            }
            host_headers = {"Authorization": "Bearer " + issue_token(team["a"])}
            no_dates = client.get(
                "/api/validation",
                params={"start": "2027-01-01"},
                headers=host_headers,
            )
            assert no_dates.status_code == 200 and no_dates.json()["total"] == 0
    finally:
        app.dependency_overrides.clear()


def test_meeting_archive_can_show_only_related_meetings(db, team, draft_factory):
    from datetime import date

    from app.schemas import MeetingDraft, PublishRequest
    from app.services.business import create_meeting, publish

    related = create_meeting(db, team["host"], draft_factory(), historical=True)
    publish(db, team["host"], related.id, PublishRequest(expected_version=1, notify=False))
    other = create_meeting(
        db,
        team["host"],
        MeetingDraft(
            title="其他部门会议",
            department_id=team["other"].id,
            meeting_date=date(2026, 8, 12),
            host_id=team["outsider"].id,
            recorder_id=team["outsider"].id,
            items=[],
        ),
        historical=True,
    )
    publish(db, team["host"], other.id, PublishRequest(expected_version=1, notify=False))
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer " + issue_token(team["host"])}
            all_items = client.get("/api/meetings", params={"scope": "all"}, headers=headers).json()["items"]
            related_items = client.get(
                "/api/meetings", params={"scope": "related"}, headers=headers
            ).json()["items"]
            assert {item["id"] for item in all_items} == {related.id, other.id}
            assert {item["id"] for item in related_items} == {related.id}
            responsible = client.get(
                "/api/meetings",
                headers={"Authorization": "Bearer " + issue_token(team["a"])},
            ).json()["items"]
            assert {item["id"] for item in responsible} == {related.id}
    finally:
        app.dependency_overrides.clear()


def test_meeting_series_archive_groups_visible_meetings_and_filters_before_listing(db, team, draft_factory):
    from datetime import date

    from app.schemas import MeetingDraft, PublishRequest
    from app.services.business import create_meeting, publish

    def published(draft):
        meeting = create_meeting(db, team["host"], draft, historical=True)
        publish(db, team["host"], meeting.id, PublishRequest(expected_version=1, notify=False))
        return meeting

    first = draft_factory(section="review", status="已完成")
    first.title = "技术部第一周会议纪要"
    first.series_id = team["series"].id
    first_meeting = published(first)
    second = draft_factory(section="review", status="未完成", when=date(2026, 8, 12))
    second.title = "技术部第二周会议纪要"
    second.series_id = team["series"].id
    second_meeting = published(second)
    special = published(
        MeetingDraft(
            title="未归系列专项会议",
            kind="special",
            meeting_date=date(2026, 8, 13),
            host_id=team["host"].id,
            items=[],
        )
    )
    published(
        MeetingDraft(
            title="外部门会议",
            department_id=team["other"].id,
            meeting_date=date(2026, 8, 14),
            host_id=team["outsider"].id,
            items=[],
        )
    )
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            admin = {"Authorization": "Bearer " + issue_token(team["host"])}
            member = {"Authorization": "Bearer " + issue_token(team["a"])}
            summary = client.get("/api/meeting-series", headers=admin).json()
            series = next(item for item in summary["items"] if item["key"] == f'series:{team["series"].id}')
            assert series["meeting_count"] == 2
            assert series["latest_date"] == "2026-08-12"
            assert any(item["key"] == "unassigned" for item in summary["items"])
            listing = client.get(
                "/api/meetings", params={"series_key": series["key"], "limit": 1}, headers=admin
            ).json()
            assert listing["total"] == 2
            assert listing["items"][0]["id"] == second_meeting.id
            unassigned = client.get(
                "/api/meetings", params={"series_key": "unassigned"}, headers=admin
            ).json()["items"]
            assert any(item["id"] == special.id for item in unassigned)
            assert client.get(
                "/api/meeting-series", params={"q": "技术部周例会"}, headers=admin
            ).json()["items"][0]["meeting_count"] == 2
            filtered = client.get(
                "/api/meeting-series", params={"status": "已完成"}, headers=admin
            ).json()["items"]
            assert next(item for item in filtered if item["key"] == series["key"])["meeting_count"] == 1
            assert client.get(
                "/api/meeting-series", params={"start": "2026-08-11"}, headers=admin
            ).json()["meeting_total"] == 3
            member_summary = client.get("/api/meeting-series", headers=member).json()
            assert member_summary["meeting_total"] == 2
            assert member_summary["items"][0]["key"] == series["key"]
            assert first_meeting.id != second_meeting.id
    finally:
        app.dependency_overrides.clear()


def test_directory_scopes_ordinary_members_to_authorized_departments(db, team):
    from app.models import User

    def auth(person):
        return {"Authorization": "Bearer " + issue_token(person)}

    finance_colleague = User(name="财务同事", department_id=team["other"].id)
    db.add(finance_colleague)
    db.flush()

    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            # A member with no meeting/task relationship in the technical department
            # must not receive technical users or departments from the global selector.
            directory = client.get("/api/directory", headers=auth(team["outsider"])).json()
            user_ids = {row["id"] for row in directory["users"]}
            department_ids = {row["id"] for row in directory["departments"]}
            assert team["outsider"].id in user_ids
            assert finance_colleague.id in user_ids
            assert team["host"].id not in user_ids
            assert team["dep"].id not in department_ids
            assert team["other"].id in department_ids

            # Administrators retain the full directory required for historical matching.
            admin_directory = client.get("/api/directory", headers=auth(team["host"])).json()
            admin_user_ids = {row["id"] for row in admin_directory["users"]}
            admin_department_ids = {row["id"] for row in admin_directory["departments"]}
            assert team["host"].id in admin_user_ids
            assert team["dep"].id in admin_department_ids
    finally:
        app.dependency_overrides.clear()


def test_history_validation_scope_is_limited_to_candidate_host(db, team):
    from app.models import ImportBatch, ImportCandidate, ImportSource

    batch = ImportBatch(name="主持人权限", creator_id=team["host"].id)
    db.add(batch)
    db.flush()
    source = ImportSource(batch_id=batch.id, filename="主持人会议.xlsx", sha256="d" * 64)
    db.add(source)
    db.flush()
    payload = {
        "title": "主持人历史会议",
        "kind": "department",
        "department_id": team["dep"].id,
        "host_id": team["a"].id,
        "meeting_date": "2026-08-20",
        "items": [],
    }
    candidate = ImportCandidate(
        source_id=source.id,
        sheet_name="Sheet1",
        original=payload,
        corrected=payload,
        content_hash="e" * 64,
    )
    db.add(candidate)
    db.flush()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            own = client.get(
                "/api/validation",
                headers={"Authorization": "Bearer " + issue_token(team["a"])},
            )
            assert own.status_code == 200 and own.json()["total"] == 1
            detail = client.get(
                f"/api/validation/{candidate.id}",
                headers={"Authorization": "Bearer " + issue_token(team["a"])},
            )
            assert detail.status_code == 200
            unrelated = client.get(
                "/api/validation",
                headers={"Authorization": "Bearer " + issue_token(team["outsider"])},
            )
            assert unrelated.status_code == 403
    finally:
        app.dependency_overrides.clear()
