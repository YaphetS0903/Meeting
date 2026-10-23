"""Isolated browser-test fixture. Never imports into the user's database."""

import os
from copy import deepcopy
from datetime import date
from pathlib import Path

if not os.environ.get("DATABASE_URL", "").startswith("sqlite:///") or "e2e" not in os.environ["DATABASE_URL"]:
    raise RuntimeError("Browser fixture requires its own e2e SQLite database")
from sqlalchemy import select

from app.core.db import Base, SessionLocal, engine
from app.core.security import passwords
from app.models import Department, ImportBatch, ImportCandidate, MeetingSeries, User
from app.schemas import MeetingDraft, MeetingItem, PublishRequest
from app.services.business import create_meeting, publish
from app.services.importing import ingest_file

Base.metadata.drop_all(engine)
Base.metadata.create_all(engine)
with SessionLocal.begin() as db:
    department = Department(name="浏览器验收部")
    db.add(department)
    db.flush()
    user = User(
        name="验收管理员",
        username="browser-test",
        password_hash=passwords.hash("local-test-only-123"),
        department_id=department.id,
        is_admin=True,
    )
    db.add(user)
    db.flush()
    members = [
        User(name=f"通讯录成员{i:03d}", department_id=department.id, wecom_id=f"e2e-user-{i:03d}")
        for i in range(560)
    ]
    db.add_all(members)
    db.flush()
    series = MeetingSeries(name="浏览器验收例会", department_id=department.id)
    db.add(series)
    batch = ImportBatch(name="浏览器验收历史资料", creator_id=user.id)
    db.add(batch)
    db.flush()
    import openpyxl

    from tests.test_parser import grid

    path = Path(os.environ["STORAGE_DIR"]) / "fixture.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    w = openpyxl.Workbook()
    for row in grid("顺延"):
        w.active.append(row)
    w.save(path)
    ingest_file(db, user, batch, path)
    ingest_file(db, user, batch, path)
    candidate = db.scalar(select(ImportCandidate))
    original = deepcopy(candidate.original)
    mapped = deepcopy(candidate.corrected)
    while len(original["items"]) < 63:
        index = len(original["items"])
        source = deepcopy(original["items"][0])
        source.update(
            id=f"e2e-history-{index}",
            content=f"历史性能校验条目 {index + 1}",
            source_row=100 + index,
        )
        original["items"].append(source)
        mapped["items"].append(deepcopy(source))
    candidate.original = original
    mapped.update(
        department_id=department.id, host_id=user.id, recorder_id=user.id, meeting_date="2026-08-18"
    )
    for item in original["items"]:
        item["category"] = ""
    original["items"][0]["owner_names"] = [user.name, "通讯录成原000"]
    for item in mapped["items"]:
        item.update(category="", owner_ids=[user.id], verifier_id=user.id, due_date="2026-08-17")
    candidate.corrected = mapped
    historic = create_meeting(
        db,
        user,
        MeetingDraft(
            title="历史关联基准会议",
            department_id=department.id,
            series_id=series.id,
            host_id=user.id,
            recorder_id=user.id,
            meeting_date=date(2026, 8, 1),
            items=[
                MeetingItem(
                    section="review",
                    status="未完成",
                    content=mapped["items"][0]["content"],
                    owner_ids=[user.id],
                    due_date=date(2026, 8, 17),
                )
            ],
        ),
        historical=True,
    )
    publish(db, user, historic.id, PublishRequest(expected_version=1, notify=False))
print("Isolated browser fixture ready")
