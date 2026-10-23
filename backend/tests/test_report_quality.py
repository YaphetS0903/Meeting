from datetime import date

from app.models import ImportBatch, ImportCandidate, ImportSource, ValidationIssue
from app.services.reporting import report


def source(db, team, start=date(2026, 8, 1), end=date(2026, 8, 31)):
    batch = ImportBatch(name="导入测试", creator_id=team["host"].id, scope_start=start, scope_end=end)
    db.add(batch)
    db.flush()
    source = ImportSource(batch_id=batch.id, filename="测试.xlsx", sha256="f" * 64)
    db.add(source)
    db.flush()
    return source


def candidate(db, source, document):
    value = ImportCandidate(
        source_id=source.id, sheet_name="会议", original=document, corrected=document, content_hash="a" * 64
    )
    db.add(value)
    db.flush()
    return value


def result(db, user, **kwargs):
    return report(db, user, date(2026, 8, 1), date(2026, 8, 31), **kwargs)


def test_department_user_sees_own_quality_without_unrelated_counts(db, team):
    s = source(db, team)
    candidate(db, s, {"department_id": team["dep"].id, "meeting_date": "2026-08-10"})
    candidate(db, s, {"department_id": team["other"].id, "meeting_date": "2026-08-10"})
    candidate(db, s, {"department_id": team["dep"].id, "meeting_date": "2026-07-10"})
    r = result(db, team["a"])
    assert r["quality"] == "待校验" and r["pending_meetings"] == 1
    assert result(db, team["a"], department_id=team["other"].id)["pending_meetings"] == 0
    assert result(db, team["host"])["pending_meetings"] == 2


def test_unknown_scope_warns_without_exposing_other_department_data(db, team):
    s = source(db, team)
    c = candidate(db, s, {"title": "待确认", "meeting_date": "2026-08-10"})
    r = result(db, team["a"])
    assert r["pending_meetings"] == 0 and r["quality"] == "待校验" and r["quality_note"]
    c.corrected = {"department_id": team["other"].id, "meeting_date": "2026-08-10"}
    assert result(db, team["a"])["quality"] == "已确认数据"
    db.add(ValidationIssue(source_id=s.id, code="parse_error", message="无法读取"))
    r = result(db, team["a"])
    assert r["pending_file_issues"] == 0 and r["quality"] == "待校验"
    assert result(db, team["host"])["pending_file_issues"] == 1


def test_batch_outside_period_does_not_affect_report(db, team):
    s = source(db, team, date(2026, 7, 1), date(2026, 7, 31))
    candidate(db, s, {"meeting_date": "日期未确定"})
    db.add(ValidationIssue(source_id=s.id, code="parse_error", message="无法读取"))
    assert result(db, team["host"])["quality"] == "已确认数据"


def test_person_department_filter_uses_owner_department_for_staging(db, team):
    s = source(db, team)
    candidate(
        db,
        s,
        {
            "department_id": team["other"].id,
            "meeting_date": "2026-08-10",
            "items": [{"section": "review", "owner_ids": [team["a"].id]}],
        },
    )
    r = result(db, team["a"], group_by="person_department", department_id=team["dep"].id)
    assert r["pending_meetings"] == 1 and r["quality"] == "待校验"


def test_special_staging_is_not_counted_under_each_department_filter(db, team):
    s = source(db, team)
    candidate(
        db,
        s,
        {
            "kind": "special",
            "department_id": None,
            "host_id": team["host"].id,
            "meeting_date": "2026-08-10",
            "items": [],
        },
    )

    assert result(db, team["host"])["pending_meetings"] == 1
    assert result(db, team["host"], department_id=team["dep"].id)["pending_meetings"] == 0
