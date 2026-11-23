import pytest
from openpyxl import Workbook, load_workbook

from app.importers.parser import parse_file
from app.services.exports import xlsx_minutes


def snapshot():
    return dict(title="测试会议-标题不必含纪要", meeting_date="2026-10-26", meeting_time="09:00",
                kind="department", department_name="技术部", host_name="主持人", location="会议室 A",
                decisions="保留决议", attendee_names=["甲", "乙"], recorder_name="记录人",
                items=[dict(section="review", content="核对项目", category="质量", owner_names=["甲", "乙"],
                            due_date="2026-10-25", status="未完成", deliverable="清单", notes="需要补充"),
                       dict(section="new", content="后续工作", owner_names=["乙"], due_date="2026-11-01",
                            verifier_name="主持人")])


def test_export_sections_become_one_meeting_with_provenance(tmp_path):
    path = tmp_path / "纪要.xlsx"
    path.write_bytes(xlsx_minutes(snapshot()))
    result = parse_file(path)
    assert not result.diagnostics
    assert not result.attachment_sheets
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    p = candidate.payload
    assert (p["title"], p["meeting_date"], p["kind"], p["host_name"], p["location"]) == (
        snapshot()["title"], "2026-10-26", "department", "主持人", "会议室 A")
    assert p["decisions"] == "保留决议"
    assert p["attendee_names"] == ["甲", "乙"]
    assert [x["section"] for x in p["items"]] == ["review", "new"]
    review, new = p["items"]
    assert review["owner_names"] == ["甲", "乙"]
    assert review["category"] == "质量" and review["status"] == "未完成"
    assert (review["source_sheet"], review["source_row"]) == ("上期任务回顾", 5)
    assert review["notes"] == "清单\n需要补充"
    assert any(x.code == "combined_notes" for x in candidate.issues)
    assert new["verifier_name"] == "主持人" and new["due_date"] == "2026-11-01"


def test_old_export_without_kind_requires_confirmation(tmp_path):
    path = tmp_path / "旧导出.xlsx"
    path.write_bytes(xlsx_minutes(snapshot()))
    book = load_workbook(path)
    for sheet in book:
        sheet['A2'] = sheet['A2'].value.split('  会议类型：')[0]
    book.save(path)
    candidate = parse_file(path).candidates[0]
    assert candidate.payload['kind'] == 'unknown'
    assert any(x.code == 'meeting_kind' for x in candidate.issues)


@pytest.mark.parametrize('tasks_only', [False, True])
def test_split_attendance_export_reimport_keeps_every_participant(tmp_path, tasks_only):
    data = snapshot()
    data['attendee_names'] = ['现场甲', '现场乙', '远程丙']
    data['online_attendee_names'] = ['远程丙']
    path = tmp_path / '现场线上纪要.xlsx'
    path.write_bytes(xlsx_minutes(data, tasks_only))
    result = parse_file(path)
    assert len(result.candidates) == 1
    assert result.candidates[0].payload['attendee_names'] == ['现场甲', '现场乙', '远程丙']


@pytest.mark.parametrize('tasks_only', [False, True])
def test_continuations_preserve_text_and_do_not_create_extra_tasks(tmp_path, tasks_only):
    data = snapshot()
    long_text = "甲" * 259 + " \n" + "乙" * 520
    data['items'][1]['content'] = long_text
    data['items'][1]['notes'] = long_text
    path = tmp_path / "长内容.xlsx"
    path.write_bytes(xlsx_minutes(data, tasks_only=tasks_only))
    candidate = parse_file(path).candidates[0]
    items = candidate.payload['items']
    assert len(items) == (1 if tasks_only else 2)
    assert items[-1]['content'] == long_text
    assert items[-1]['notes'] == long_text
    assert items[-1]['source_row'] == 5
    if not tasks_only:
        assert any(x.field_path == 'items.1.notes' for x in candidate.issues)


def test_similarly_named_attachment_is_not_imported_as_meeting(tmp_path):
    path = tmp_path / '附件.xlsx'
    book = Workbook()
    book.active.title = '上期任务回顾'
    book.active.append(['物料清单', '责任人'])
    book.save(path)
    result = parse_file(path)
    assert not result.candidates
    assert result.attachment_sheets == ['上期任务回顾']


def test_empty_section_not_duplicated_and_invalid_status_not_guessed(tmp_path):
    data = snapshot()
    data['items'] = data['items'][:1]
    data['items'][0]['status'] = '顺延'
    path = tmp_path / '空分区.xlsx'
    path.write_bytes(xlsx_minutes(data))
    candidate = parse_file(path).candidates[0]
    assert len(candidate.payload['items']) == 1
    assert candidate.payload['items'][0]['status'] is None
    assert candidate.payload['items'][0]['raw_status'] == '顺延'
    assert any(x.code == 'invalid_status' for x in candidate.issues)
