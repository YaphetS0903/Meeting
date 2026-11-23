"""Public PDF regression checks for the formal meeting-minutes template."""

import re
from copy import deepcopy
from io import BytesIO

import pytest
from pypdf import PdfReader

from app.services.exports import pdf_minutes


def compact(text):
    return "".join(text.split())


def test_completed_and_incomplete_results_use_distinct_actual_pdf_text_colors():
    reader = read_pdf({"items": [dict(section="review", content="颜色验证", status=status)
                                 for status in ("已完成", "未完成", "未到期")]})
    fill, found = (0, 0, 0), {}

    def operand(operator, arguments, *_):
        nonlocal fill
        if operator == b"rg":
            fill = tuple(float(value) for value in arguments)

    def capture(value, *_):
        if value.strip() in {"已完成", "未完成", "未到期"}:
            found[value.strip()] = fill

    for page in reader.pages:
        page.extract_text(visitor_operand_before=operand, visitor_text=capture)
    assert found["已完成"] == pytest.approx((21 / 255, 128 / 255, 61 / 255), abs=0.00001)
    assert found["未完成"] == pytest.approx((214 / 255, 41 / 255, 41 / 255), abs=0.00001)
    assert found["未到期"] == (0, 0, 0)


def read_pdf(snapshot):
    return PdfReader(BytesIO(pdf_minutes(snapshot)))


def test_formal_minutes_preserves_snapshot_fields_and_literal_text():
    snapshot = {
        "title": "信息&系统<优化>会议纪要",
        "meeting_date": "2026-10-27",
        "meeting_time": "13:30",
        "location": "三楼<会议室>&办公室",
        "department_name": "公司 / 信息部",
        "kind": "special",
        "host_name": "主持甲",
        "attendee_names": ["参会甲", "参会乙"],
        "absentees": "缺席丙：外出培训",
        "decisions": "决议：A < B & C > D，按原文执行。",
        "recorder_name": "编制丁",
        "items": [
            {
                "section": "review",
                "category": "系统改进",
                "content": "历史事项<复核>&收尾",
                "owner_names": ["责任甲", "责任乙"],
                "due_date": "2026-10-20",
                "status": "未完成",
                "deliverable": "输出物<报告>",
                "notes": "备注&复核依据",
                "exclusion_reason": "排除依据：范围调整",
            },
            {
                "section": "new",
                "category": "本期计划",
                "content": "新工作<script>literal</script>及交付",
                "owner_names": ["责任戊", "责任己"],
                "due_date": "2026-11-02",
                "status": "未到期",
                "verifier_name": "验证负责人",
                "deliverable": "交付清单",
                "notes": "保留原始说明",
            },
        ],
    }
    reader = read_pdf(snapshot)
    output = compact("\n".join(page.extract_text() for page in reader.pages))
    expected = [
        snapshot["title"],
        snapshot["location"],
        snapshot["department_name"],
        snapshot["host_name"],
        *snapshot["attendee_names"],
        snapshot["absentees"],
        snapshot["decisions"],
        snapshot["recorder_name"],
        "2026",
        "10",
        "27",
        "13",
        "30",
    ]
    for item in snapshot["items"]:
        expected.extend(
            item[key] for key in ("category", "content", "due_date", "status", "deliverable", "notes")
        )
        expected.extend(item["owner_names"])
    expected.append("排除依据：范围调整")
    for value in expected:
        assert compact(value) in output, f"PDF lost literal snapshot field: {value}"
    assert "专项" in output
    assert "验证负责人" not in output
    assert output.index(compact(snapshot["absentees"])) < output.index("本次会议正式决议事项")
    assert output.index(compact(snapshot["decisions"])) < output.index("上次会议核心工作部署内容")
    assert output.index("上次会议核心工作部署内容") < output.index("本次会议核心工作部署内容")
    assert float(reader.pages[0].mediabox.width) < float(reader.pages[0].mediabox.height)


def test_empty_sections_remain_visible_without_fabricated_tasks():
    reader = read_pdf({"title": "空事项会议纪要", "meeting_date": "2026-10-27", "items": []})
    output = compact("\n".join(page.extract_text() for page in reader.pages))
    for title in ("上次会议核心工作部署内容", "本次会议正式决议事项", "本次会议核心工作部署内容"):
        assert title in output
    assert "责任人" in output
    assert "计划完成时间" in output
    assert "编制人" in output
    assert len(reader.pages) == 1


@pytest.mark.parametrize("section", ["review", "new"])
def test_task_table_continuations_repeat_columns_but_show_section_titles_once(section):
    items = [
        {
            "section": section,
            "content": f"工作标记{index:03d}：核对系统清单和整改结果，完成相关工作记录。",
            "owner_names": ["责任人甲", "责任人乙"],
            "due_date": "2026-11-01",
            "status": "未到期",
            "notes": "逐项复核实际完成情况和验收要求。",
        }
        for index in range(75)
    ]
    reader = read_pdf({"title": "多页任务纪要", "meeting_date": "2026-10-27", "items": items})
    page_texts = [compact(page.extract_text()) for page in reader.pages]
    output = "".join(page_texts)
    assert len(reader.pages) >= 3
    for index in range(len(items)):
        assert f"工作标记{index:03d}" in output
    title = "上次会议核心工作部署内容" if section == "review" else "本次会议核心工作部署内容"
    assert output.count(title) == 1
    for label in ("上次会议核心工作部署内容", "本次会议核心工作部署内容"):
        assert output.count(label) == 1
    for text in page_texts:
        if "工作标记" in text:
            assert "责任人" in text and "计划完成时间" in text
        assert text.count(title) <= 1, "A task section must not repeat its header within one page"


@pytest.mark.parametrize("section", ["review", "new"])
def test_task_row_near_page_boundary_does_not_restart_header_on_the_same_page(section):
    items = [dict(section=section, content=f"事项标记{index:03d}：核对整改结果和完成记录。",
                  owner_names=["责任甲"], due_date="2026-11-15", status="未到期",
                  notes="记录工作要求。" * (50 if index == 39 else 1)) for index in range(40)]
    reader = read_pdf(dict(title="分页边界回归", meeting_date="2026-11-09", items=items))
    title = "上次会议核心工作部署内容" if section == "review" else "本次会议核心工作部署内容"
    texts = [compact(page.extract_text()) for page in reader.pages]
    for text in texts:
        assert text.count(title) <= 1
        if "事项标记" in text:
            assert "责任人" in text and "计划完成时间" in text
    output = "".join(texts)
    assert output.count(title) == 1
    for index in range(40):
        assert output.count(f"事项标记{index:03d}") == 1
    assert output.count("记录工作要求。") == 89


def test_long_decision_and_single_task_keep_their_final_text_across_pages():
    snapshot = {
        "title": "长文本会议纪要",
        "meeting_date": "2026-10-27",
        "decisions": "核对会议决议、管理要求和后续执行安排。" * 240 + "决议最终验收标记",
        "items": [
            {
                "section": "new",
                "content": "核对责任范围、验收结果、整改要求与相关记录。" * 350 + "任务最终验收标记",
                "owner_names": ["责任人甲", "责任人乙"],
                "due_date": "2026-11-01",
                "status": "未到期",
                "verifier_name": "验证人甲",
                "notes": "备注最终标记",
            }
        ],
    }
    reader = read_pdf(snapshot)
    output = compact("\n".join(page.extract_text() for page in reader.pages))
    assert len(reader.pages) > 3
    for marker in ("决议最终验收标记", "任务最终验收标记", "备注最终标记"):
        assert marker in output
    assert "验证人甲" not in output
    for label in ("上次会议核心工作部署内容", "本次会议核心工作部署内容"):
        assert output.count(label) == 1
    assert output.index("决议最终验收标记") < output.index("任务最终验收标记")


@pytest.mark.parametrize("verifier", ["内部验证负责人", ""])
def test_task_notes_do_not_append_verifier_or_host_but_preserve_original_fields(verifier):
    snapshot = {
        "title": "备注展示回归",
        "host_name": "会议主持人甲",
        "items": [
            {
                "section": "new",
                "content": "按清单完成配置",
                "verifier_name": verifier,
                "notes": "与负责人约定后执行",
                "deliverable": "配置清单终稿",
                "exclusion_reason": "范围调整说明",
            }
        ],
    }
    original = deepcopy(snapshot)
    output = compact("\n".join(page.extract_text() for page in read_pdf(snapshot).pages))
    for value in ["与负责人约定后执行", "输出物：配置清单终稿", "排除依据：范围调整说明"]:
        assert value in output
    assert "验证人：" not in output
    assert "内部验证负责人" not in output
    assert output.count("会议主持人甲") == 1
    assert snapshot == original  # Rendering must not alter task verification data.


def test_user_entered_verifier_words_in_notes_are_not_removed():
    snapshot = {"items": [{"section": "new", "notes": "验证人：用户原始备注", "verifier_name": "内部人员"}]}
    output = compact("\n".join(page.extract_text() for page in read_pdf(snapshot).pages))
    assert "验证人：用户原始备注" in output
    assert "内部人员" not in output


def test_large_meeting_metadata_spans_pages_without_losing_attendees_or_tail_fields():
    attendees = [f"参会人员{index:03d}" for index in range(1, 563)]
    title = "跨部门系统优化工作会议纪要" * 18 + "标题末尾标记"
    snapshot = {
        "title": title,
        "meeting_date": "2026-10-27",
        "department_name": "公司 / 信息部",
        "attendee_names": attendees,
        "absentees": "缺席人员因外出培训需要补阅纪要并确认后续工作。" * 180 + "缺席原因末尾标记",
        "items": [],
        "recorder_name": "编制人员末尾标记",
    }
    reader = read_pdf(snapshot)
    page_texts = [compact(page.extract_text()) for page in reader.pages]
    # A name may straddle a page boundary. Remove pagination before reassembling it.
    output = re.sub(r"第\d+页", "", "".join(page_texts).replace(compact(title), ""))
    assert len(reader.pages) > 1
    for page_output in page_texts:
        assert re.sub(r"第\d+页", "", page_output.replace(compact(title), "")), (
            "Metadata pagination must not create pages containing only repeated titles"
        )
    for name in attendees:
        assert name in output, f"Missing attendee in multipage metadata: {name}"
    assert "标题末尾标记" in "".join(page_texts)
    assert "缺席原因末尾标记" in output
    assert "编制人员末尾标记" in output
    for page in reader.pages:
        positions = []

        def text_position(text, current_matrix, text_matrix, font, font_size):
            if text.strip():
                x = (
                    text_matrix[4] * current_matrix[0]
                    + text_matrix[5] * current_matrix[2]
                    + current_matrix[4]
                )
                y = (
                    text_matrix[4] * current_matrix[1]
                    + text_matrix[5] * current_matrix[3]
                    + current_matrix[5]
                )
                positions.append((x, y))

        page.extract_text(visitor_text=text_position)
        assert all(0 <= x <= float(page.mediabox.width) + 1 for x, _ in positions)
        assert all(0 <= y <= float(page.mediabox.height) + 1 for _, y in positions), (
            "Extractable metadata text must also be drawn inside the visible page"
        )
