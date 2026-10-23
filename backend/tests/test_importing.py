from datetime import date

from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import ImportBatch, ImportCandidate, ImportSource, User
from app.services.importing import ingest_file
from app.services.reporting import report


def test_matching_never_guesses_same_name(db, team):
    from app.services.importing import resolve_name

    assert resolve_name(db, "责任人甲") == team["a"].id
    db.add(User(name="责任人甲", department_id=team["other"].id))
    db.flush()
    assert resolve_name(db, "责任人甲") is None


def test_duplicate_file_and_scope(tmp_path, db, team, monkeypatch):
    import openpyxl

    from tests.test_parser import grid

    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path / "storage")
    path = tmp_path / "纪要.xlsx"
    w = openpyxl.Workbook()
    for row in grid("已完成"):
        w.active.append(row)
    old = w.create_sheet("七月")
    old_grid = grid("已完成")
    old_grid[4][2] = "2026/7/20"
    for row in old_grid:
        old.append(row)
    w.save(path)
    batch = ImportBatch(
        name="八月", creator_id=team["host"].id, scope_start=date(2026, 8, 1), scope_end=date(2026, 8, 31)
    )
    db.add(batch)
    db.flush()
    first = ingest_file(db, team["host"], batch, path)
    duplicate = ingest_file(db, team["host"], batch, path)
    assert duplicate.duplicate_of == first.id
    assert db.scalar(select(func.count()).select_from(ImportSource)) == 2
    assert db.scalar(select(func.count()).select_from(ImportCandidate)) == 2
    states = set(db.scalars(select(ImportCandidate.state)))
    assert states == {"pending", "out_of_scope"}
    result = report(db, team["host"], date(2026, 8, 1), date(2026, 8, 31))
    assert result["quality"] == "待校验"
    assert result["summary"]["person_count"] == 0


def test_import_without_month_scope_keeps_historical_dates(tmp_path, db, team, monkeypatch):
    import openpyxl

    from tests.test_parser import grid

    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path / "storage")
    path = tmp_path / "跨月纪要.xlsx"
    workbook = openpyxl.Workbook()
    for row in grid("已完成"):
        workbook.active.append(row)
    previous = workbook.create_sheet("七月")
    previous_grid = grid("已完成")
    previous_grid[4][2] = "2026/7/20"
    for row in previous_grid:
        previous.append(row)
    workbook.save(path)
    batch = ImportBatch(name="历史会议", creator_id=team["host"].id)
    db.add(batch)
    db.flush()

    source = ingest_file(db, team["host"], batch, path)
    states = list(db.scalars(select(ImportCandidate.state).where(ImportCandidate.source_id == source.id)))
    assert states == ["pending", "pending"]
