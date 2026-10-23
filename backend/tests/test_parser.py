from pathlib import Path

import pytest

from app.importers.parser import parse_date, parse_file, parse_grid, split_people


@pytest.mark.parametrize(
    "raw,expected",
    [
        (46245, "2026-08-11"),
        ("__2026__年__8__月_10_日", "2026-08-10"),
        ("2026/8/10", "2026-08-10"),
        ("8/10", "2026-08-10"),
    ],
)
def test_dates(raw, expected):
    assert parse_date(raw, 2026) == (expected, None)


@pytest.mark.parametrize(
    "raw", ["每周五", "2026/7/30\n2026/8/7", "完成 2026/8/10", "2026/2/30", "", "TBD", "月底"]
)
def test_ambiguous_dates_are_not_guessed(raw):
    assert parse_date(raw, 2026)[0] is None
    assert parse_date(raw, 2026)[1]


def test_people():
    assert split_people("甲/乙、丙 甲（线上）") == ["甲", "乙", "丙"]
    assert split_people("各工厂库管员") == ["各工厂库管员"]


def grid(status="顺延"):
    return [
        ["会议纪要（统一标准模板）"],
        ["技术部会议纪要"],
        ["填报说明"],
        ["会议所属部门", "", "技术部", "", "会议类型", "☑ 部门周例会"],
        ["会议召开日期", "", "2026/8/18", "", "会议召开时间", "10:00"],
        ["会议地点", "", "会议室", "", "主持人", "主持人"],
        ["上期会议任务回顾核对情况"],
        ["序号", "具体工作任务内容", "", "责任人", "计划完成时间", "任务闭环完成结果", "备注"],
        [1, "项目", "验证样件", "甲/乙", "2026/8/17", status, ""],
        [2, "", "", "", "", "", ""],
        ["本次会议核心工作部署内容"],
        ["序号", "具体工作任务内容", "", "责任人", "计划完成时间", "任务闭环完成结果", "备注"],
        [1, "项目", "提交报告", "甲", "2026/8/20", "已完成", ""],
    ]


def test_sections_preserve_raw_status_and_skip_blank_numbered_rows():
    candidate = parse_grid("Sheet1", grid())
    assert candidate.payload["kind"] == "department"
    assert len(candidate.payload["items"]) == 2
    assert candidate.payload["items"][0]["section"] == "review"
    assert candidate.payload["items"][0]["status"] is None
    assert candidate.payload["items"][0]["raw_status"] == "顺延"
    assert candidate.payload["items"][1]["section"] == "new"
    assert any(i.code == "invalid_status" for i in candidate.issues)


def test_overdue_undue_source_coordinates():
    candidate = parse_grid("Sheet1", grid("未到期"))
    issue = next(i for i in candidate.issues if i.code == "overdue_undue")
    assert issue.row == 9
    assert issue.field_path == "items.0.status"


def test_attachments_are_not_tasks():
    assert parse_grid("物料清单", [["物料编码", "责任人"], ["A001", "甲"]]) is None


def test_real_word_keeps_top_unknown_and_new_outside_review():
    files = list(Path("../8月会议纪要").rglob("*.docx"))
    if not files:
        pytest.skip("未提供历史样本")
    parsed = parse_file(files[0])
    assert not parsed.diagnostics
    candidate = parsed.candidates[0]
    items = candidate.payload["items"]
    assert len([x for x in items if x["section"] == "unknown"]) == 5
    assert len([x for x in items if x["section"] == "review"]) == 23
    assert len([x for x in items if x["section"] == "new"]) == 14
    assert candidate.payload["meeting_date"] == "2026-08-10"


def test_real_exception_is_found():
    files = list(Path("../8月会议纪要").rglob("√臻瑞出入库流程优化会议纪要 - 0818.xlsx"))
    if not files:
        pytest.skip("未提供历史样本")
    candidate = parse_file(files[0]).candidates[0]
    issue = next(i for i in candidate.issues if i.code == "overdue_undue" and i.row == 18)
    assert issue.field_path


def test_unreadable_file_is_visible(tmp_path):
    path = tmp_path / "坏文件.xls"
    path.write_bytes(b"not-an-excel")
    result = parse_file(path)
    assert result.diagnostics[0]["code"] == "file_parse_error"
