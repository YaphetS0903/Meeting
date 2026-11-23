from copy import deepcopy
from datetime import date
from io import BytesIO

import openpyxl
import pytest
from pypdf import PdfReader
from sqlalchemy import func, select

from app.models import Task, TaskVersion
from app.schemas import MeetingDraft, MeetingItem, PublishRequest
from app.services import business
from app.services.exports import pdf_minutes, task_rows, xlsx_minutes
from app.services.meeting_items import arrangement_groups, continuation_order


def grouped_snapshot():
    def row(identifier, section="new", **extra):
        return dict(id=identifier, section=section, content=identifier, owner_names=["责任人甲"],
                    due_date="2026-12-20", status="未到期", **extra)
    items = [row("新工作甲"), row("未到期甲", "review"),
             row("延续乙", source={"extended_from_item_id": "failed-b"}),
             row("未到期乙", "review"), row("新工作乙"),
             row("延续甲", source={"extended_from_item_id": "failed-a"})]
    return dict(title="任务分组核对", meeting_date="2026-11-12", kind="department",
                department_name="技术部", host_name="主持人", items=items)


@pytest.mark.parametrize("legacy", [False, True])
def test_published_extensions_keep_identity_reason_and_next_meeting_order(db, team, draft_factory, monkeypatch, legacy):
    monkeypatch.setattr(business, "today", lambda: date(2026, 11, 12))
    draft = draft_factory(when=date(2026, 11, 9))
    template = draft.items[0]
    draft.items = [template.model_copy(deep=True) for _ in range(5)]
    for index, item in enumerate(draft.items):
        item.id, item.content = f"original-{index}", f"原任务{index}"
        item.due_date = date(2026, 12, 31) if index in {0, 2} else date(2026, 11, 10)
    first = business.create_meeting(db, team["host"], draft)
    business.publish(db, team["host"], first.id, PublishRequest(expected_version=1, notify=False))
    current = business.continue_meeting(db, team["host"], first.id)
    draft = MeetingDraft.model_validate(current.draft)
    for index, item in enumerate(draft.items):
        item.status = "未到期" if index in {0, 2} else "未完成"
    reviews = draft.items
    extensions = []
    for index in [4, 1]:
        item = reviews[index].model_copy(deep=True)
        item.id, item.section, item.status = f"extended-{index}", "new", "未到期"
        item.due_date, item.change_reason = date(2026, 12, 20), ""
        item.source = {} if legacy else {"extended_from_item_id": reviews[index].id}
        extensions.append(item)
    fresh = [MeetingItem(id=f"new-{index}", section="new", content=f"新工作{index}",
                        owner_ids=[team["a"].id], due_date=date(2026, 12, 25)) for index in [1, 2]]
    draft.items = [fresh[0], reviews[0], extensions[0], reviews[1], reviews[2], fresh[1], reviews[3], reviews[4], extensions[1]]
    business.save_meeting(db, team["host"], current.id, draft, current.draft_version)
    business.publish(db, team["host"], current.id, PublishRequest(
        expected_version=current.draft_version, notify=False, reason="",
    ))
    frozen = deepcopy(current.draft)
    expected = [reviews[i].task_id for i in [0, 2, 4, 1]] + [
        row["task_id"] for row in current.draft["items"] if row["id"].startswith("new-")
    ]
    followup = business.continue_meeting(db, team["host"], current.id)
    assert [row["task_id"] for row in followup.draft["items"]] == expected
    assert all(row["status"] == "未到期" for row in followup.draft["items"])
    assert [row["source"]["continuation_origin"] for row in followup.draft["items"]] == [
        "review", "review", "extension", "extension", "new", "new",
    ]
    # Legacy drafts recover exact origins by task identity, without data writes.
    from app.services.meeting_context import continuation_origins
    before_followup = deepcopy(followup.draft)
    mapping = continuation_origins(db, followup)
    assert [mapping[row["id"]] for row in followup.draft["items"]] == [
        "review", "review", "extension", "extension", "new", "new",
    ]
    assert followup.draft == before_followup
    assert len(expected) == len(set(expected)) == 6
    assert db.scalar(select(func.count()).select_from(Task)) == 7
    assert current.draft == frozen
    for item in extensions:
        assert db.scalar(select(TaskVersion).where(
            TaskVersion.task_id == item.task_id, TaskVersion.number == 2,
        )).reason == "上期未完成事项延续安排"
    business.publish(db, team["host"], followup.id, PublishRequest(expected_version=1, notify=False))
    again = business.continue_meeting(db, team["host"], followup.id)
    assert [row["task_id"] for row in again.draft["items"]] == expected


def test_grouped_exports_preserve_order_labels_and_snapshot():
    snapshot = grouped_snapshot()
    before = deepcopy(snapshot)
    assert [row["id"] for row in continuation_order(snapshot["items"])] == [
        "未到期甲", "未到期乙", "延续乙", "延续甲", "新工作甲", "新工作乙",
    ]
    assert [label for label, _ in arrangement_groups(snapshot["items"])] == ["本次新建工作", "上次未完成延续事项"]
    assert [row[2] for row in task_rows(snapshot, "new")] == ["新工作甲", "新工作乙", "延续乙", "延续甲"]
    sheet = openpyxl.load_workbook(BytesIO(xlsx_minutes(snapshot)))["本次工作部署"]
    assert [sheet.cell(i, 1).value for i in [5, 8]] == ["本次新建工作", "上次未完成延续事项"]
    assert [sheet.cell(i, 3).value for i in [6, 7, 9, 10]] == ["新工作甲", "新工作乙", "延续乙", "延续甲"]
    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(pdf_minutes(snapshot))).pages)
    ordered = ["本次新建工作", "新工作甲", "新工作乙", "上次未完成延续事项", "延续乙", "延续甲"]
    assert [text.index(value) for value in ordered] == sorted(text.index(value) for value in ordered)
    assert snapshot == before
