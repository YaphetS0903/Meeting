"""Exports always read a published snapshot; formulas and HTML are never evaluated."""

import hashlib
import math
from io import BytesIO

from openpyxl import Workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy import select

from app.core.config import get_settings
from app.models import Attachment, MeetingVersion, uid
from app.services.meeting_attendance import attendance_rows
from app.services.meeting_items import arrangement_groups, ordered_arrangements
from app.services.minutes_pdf import render_minutes_pdf
from app.services.owner_results import result_text

HEADERS = [
    "序号",
    "项目 / 类别",
    "任务内容及要求",
    "责任人",
    "计划完成日期",
    "结果 / 验证人",
    "输出物 / 备注",
]


def attachment_filename(attachment, db):
    """Hide generated revision suffixes without changing stored file metadata."""
    if attachment.kind in {"minutes_xlsx", "tasks_xlsx", "minutes_pdf", "minutes_png"} and attachment.meeting_version:
        published = db.scalar(
            select(MeetingVersion).where(
                MeetingVersion.meeting_id == attachment.meeting_id,
                MeetingVersion.number == attachment.meeting_version,
            )
        )
        if published:
            title = published.snapshot["title"][:120]
            label = "任务单" if attachment.kind == "tasks_xlsx" else "纪要"
            extension = {"minutes_xlsx": "xlsx", "tasks_xlsx": "xlsx", "minutes_pdf": "pdf", "minutes_png": "png"}[attachment.kind]
            if attachment.filename == f"{title}-V{attachment.meeting_version}-{label}.{extension}":
                return f"{title}-{label}.{extension}"
    return attachment.filename


def task_rows(snapshot, section):
    rows = []
    items = snapshot.get("items", [])
    ordered = ordered_arrangements(items) if section == "new" else [i for i in items if i["section"] == section]
    for n, item in enumerate(ordered, 1):
        content = item.get("content", "")
        notes = "\n".join(
            filter(None, [item.get("deliverable"), item.get("notes"), item.get("exclusion_reason")])
        )
        result = result_text(item)
        if section == "review" and item.get("status") == "部分完成":
            lines = result.splitlines()
            result = lines[0]
            notes = "\n".join([*lines[1:], *([notes] if notes else [])])
        rows.append(
            [
                str(n),
                item.get("category", ""),
                content,
                "、".join(item.get("owner_names", [])),
                "\n".join(filter(None, [*item.get("source", {}).get("date_history", []), item.get("due_date"),
                    item.get("postponed_due_date")])),
                result
                if section == "review"
                else item.get("verifier_name", snapshot.get("host_name", "")),
                notes,
            ]
        )
    return rows


def text_cell(cell, value):
    cell.value = "" if value is None else str(value)
    cell.data_type = "s"  # A task beginning with = must remain literal text in Excel.


def style_sheet(ws, widths):
    line = Side(style="thin", color="CDD6E1")
    for i, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = width
    for row in ws:
        for cell in row:
            cell.font = Font(name="微软雅黑", size=10, color="24364D")
            cell.alignment = Alignment(wrap_text=True, vertical="center")
            cell.border = Border(left=line, right=line, top=line, bottom=line)
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.print_options.horizontalCentered = True
    ws.print_area = ws.dimensions
    ws.freeze_panes = "D5"
    ws.oddFooter.center.text = "第 &P 页 / 共 &N 页"


def xlsx_minutes(snapshot, tasks_only=False):
    book = Workbook()
    book.remove(book.active)
    sections = (
        [("本次工作部署", "new")] if tasks_only else [("上期任务回顾", "review"), ("本次工作部署", "new")]
    )
    for name, section in sections:
        ws = book.create_sheet(name)
        ws.merge_cells("A1:G1")
        text_cell(ws["A1"], snapshot["title"])
        ws.merge_cells("A2:G2")
        text_cell(
            ws["A2"],
            f"日期：{snapshot['meeting_date']}  时间：{snapshot.get('meeting_time', '')}  地点：{snapshot.get('location', '')}  部门：{snapshot.get('department_name', '')}  主持人：{snapshot.get('host_name', '')}  会议类型：{ {'department': '部门例会', 'special': '专项会议'}.get(snapshot.get('kind'), '') }",
        )
        ws.merge_cells("A3:G3")
        text_cell(
            ws["A3"], name + ("（仅此分区纳入本期考核）" if section == "review" else "（不计本期考核）")
        )
        for col, val in enumerate(HEADERS, 1):
            text_cell(ws.cell(4, col), val)
        group_starts, group_rows, offset = {}, [], 0
        if section == "new":
            for label, items in arrangement_groups(snapshot.get("items", [])):
                if label:
                    group_starts[offset] = label
                offset += len(items)
        for index, values in enumerate(task_rows(snapshot, section)):
            if index in group_starts:
                row = ws.max_row + 1
                ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=7)
                text_cell(ws.cell(row, 1), group_starts[index])
                ws.row_dimensions[row].height = 26
                group_rows.append(row)
            # Split very long content into continuation rows rather than clipping Excel's max row height.
            chunks = [[str(v)[j : j + 260] for j in range(0, max(1, len(str(v))), 260)] for v in values]
            for part in range(max(map(len, chunks))):
                row = ws.max_row + 1
                for col, pieces in enumerate(chunks, 1):
                    text_cell(
                        ws.cell(row, col), pieces[part] if part < len(pieces) else ("续" if col == 1 else "")
                    )
                dates = values[4].split("\n")
                if part == 0 and len(dates) >= 2:
                    rich_dates = CellRichText()
                    for index, value in enumerate(dates):
                        if index:
                            rich_dates.append("\n")
                        rich_dates.append(TextBlock(InlineFont(rFont="微软雅黑", sz=10, color="24364D",
                                                              b=False, strike=index < len(dates) - 1), value))
                    ws.cell(row, 5).value = rich_dates
                ws.row_dimensions[row].height = max(
                    42,
                    min(
                        380,
                        max(
                            sum(max(1, math.ceil(len(line) / w)) for line in str(ws.cell(row, c).value or "").split("\n"))
                            for c, w in enumerate([4, 10, 30, 10, 12, 12, 18], 1)
                        )
                        * 15
                        + 15,
                    ),
                )
        if ws.max_row == 4:
            ws.merge_cells("A5:G5")
            text_cell(ws["A5"], "本分区无工作事项")
        last = ws.max_row + 1
        for label, value in [
            ("正式决议", snapshot.get("decisions", "")),
            *attendance_rows(snapshot),
            ("缺席及原因", snapshot.get("absentees", "")),
            ("编制人", snapshot.get("recorder_name", "")),
        ]:
            for part in [str(value)[i : i + 250] for i in range(0, max(1, len(str(value))), 250)]:
                ws.merge_cells(start_row=last, start_column=2, end_row=last, end_column=7)
                text_cell(ws.cell(last, 1), label)
                text_cell(ws.cell(last, 2), part)
                ws.row_dimensions[last].height = max(35, math.ceil(len(part) / 75) * 16 + 12)
                last += 1
        style_sheet(ws, [7, 16, 48, 19, 18, 22, 28])
        for number in group_rows:
            for cell in ws[number]:
                cell.fill = PatternFill("solid", fgColor="F4F4F5")
                cell.font = Font(name="微软雅黑", size=10, bold=True, color="27272A")
        for row in [1, 3, 4]:
            for cell in ws[row]:
                cell.fill = PatternFill("solid", fgColor="E8ECF0" if row == 1 else "DCE8F8")
                cell.font = Font(name="微软雅黑", size=16 if row == 1 else 10, bold=True, color="254B7B")
        ws.row_dimensions[1].height = 36
        ws.row_dimensions[2].height = 34
        ws.row_dimensions[3].height = 28
        ws.print_title_rows = "1:4"
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def pdf_minutes(snapshot):
    return render_minutes_pdf(snapshot)


def export_report(data):
    person_view = data.get("group_by") == "department_people"
    book = Workbook()
    ws = book.active
    ws.title = "考核汇总"
    rows = [
        ["统计范围", data["start"], data["end"], data["quality"]],
        ["姓名" if person_view else "统计对象", "事项数",
         *([] if person_view else ["考核人次"]),
         "已完成", "未完成", "未到期", "不统计", "关闭", "达成率"],
    ]
    for group in [{"label": "合计", **data["summary"]}, *data["groups"]]:
        rows.append(
            [
                group["label"] + ("（已停用）" if group.get("active") is False else ""),
                group["items"],
                *([] if person_view else [group["person_count"]]),
                *[group["counts"][s] for s in ["已完成", "未完成", "未到期", "不统计", "关闭"]],
                "—" if group["rate"] is None else f"{group['rate']:.2%}",
            ]
        )
    for row in rows:
        n = ws.max_row + 1 if ws.max_row > 1 or ws.cell(1, 1).value is not None else 1
        for c, v in enumerate(row, 1):
            text_cell(ws.cell(n, c), v)
    style_sheet(ws, [24] * (8 if person_view else 9))
    ws.print_title_rows = "1:2"
    detail = book.create_sheet("考核明细")
    keys = [
        "meeting_title",
        "meeting_date",
        "meeting_version",
        "user_name",
        "department_name",
        "person_department_name",
        "content",
        "due_date",
        "status",
        "notes",
    ]
    rows = [
        [
            "会议",
            "会议日期",
            "版本",
            "责任人",
            "会议部门",
            "考核时主部门",
            "工作内容",
            "截止日期",
            "状态",
            "备注",
        ]
    ] + [[r.get(k, "") for k in keys] for r in data["details"]]
    for n, row in enumerate(rows, 1):
        for c, v in enumerate(row, 1):
            text_cell(detail.cell(n, c), v)
    style_sheet(detail, [30, 16, 8, 14, 18, 18, 60, 16, 12, 35])
    detail.print_title_rows = "1:1"
    if data.get("senior_only"):
        meetings = book.create_sheet("会议清单")
        rows = [["会议名称", "召开日期", "参会领导", "筛选领导"]]
        rows += [[m["title"], m["meeting_date"], "、".join(m["leaders"]), data.get("leader_name", "全部参会领导")]
                 for m in data.get("senior_meetings", [])]
        for n, row in enumerate(rows, 1):
            for c, value in enumerate(row, 1):
                text_cell(meetings.cell(n, c), value)
        style_sheet(meetings, [45, 16, 25, 25])
        meetings.print_title_rows = "1:1"
        arranged = book.create_sheet("本次新安排")
        keys = ["meeting_title", "meeting_date", "meeting_version", "user_name", "content",
                "requirements", "due_date", "deliverable", "notes"]
        rows = [["会议", "会议日期", "版本", "责任人", "工作内容", "工作要求", "计划完成日期", "输出物", "备注"]]
        rows += [[r.get(key, "") for key in keys] for r in data.get("new_arrangements", [])]
        for n, row in enumerate(rows, 1):
            for c, value in enumerate(row, 1):
                text_cell(arranged.cell(n, c), value)
        style_sheet(arranged, [30, 16, 8, 22, 60, 35, 16, 28, 35])
        arranged.print_title_rows = "1:1"
    out = BytesIO()
    book.save(out)
    return out.getvalue()


def generate_exports(db, meeting_id, version):
    published = db.scalar(
        select(MeetingVersion).where(
            MeetingVersion.meeting_id == meeting_id, MeetingVersion.number == version
        )
    )
    if not published:
        raise ValueError("发布版本不存在")
    results = []
    for fmt, kind, extension, mime in [
        ("xlsx", "minutes_xlsx", "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("tasks", "tasks_xlsx", "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("pdf", "minutes_pdf", "pdf", "application/pdf"),
    ]:
        existing = db.scalar(
            select(Attachment).where(
                Attachment.meeting_id == meeting_id,
                Attachment.meeting_version == version,
                Attachment.kind == kind,
            )
        )
        if existing:
            results.append(existing)
            continue
        content = (
            pdf_minutes(published.snapshot)
            if fmt == "pdf"
            else xlsx_minutes(published.snapshot, fmt == "tasks")
        )
        key = f"exports/{uid()}.{extension}"
        path = get_settings().storage_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        item = Attachment(
            filename=f"{published.snapshot['title'][:120]}-{'任务单' if fmt == 'tasks' else '纪要'}.{extension}",
            storage_key=key,
            sha256=hashlib.sha256(content).hexdigest(),
            mime_type=mime,
            size=len(content),
            uploader_id=published.publisher_id,
            meeting_id=meeting_id,
            meeting_version=version,
            kind=kind,
        )
        db.add(item)
        db.flush()
        results.append(item)
    return results


def minutes_image(pdf_content):
    """Render every PDF page in order as one readable, white-backed PNG."""
    import pypdfium2 as pdfium
    from PIL import Image

    with pdfium.PdfDocument(pdf_content) as document:
        sizes = [document.get_page_size(i) for i in range(len(document))]
        if not sizes:
            raise ValueError("会议纪要没有可转换的页面")
        scale = min(1800 / max(w for w, _ in sizes), 2.5)
        area = max(w for w, _ in sizes) * sum(h for _, h in sizes)
        scale = min(scale, math.sqrt(48_000_000 / area))
        if max(w for w, _ in sizes) * scale < 1000:
            raise ValueError("会议纪要过长，无法生成清晰的单张图片，请拆分会议内容后重试")
        dimensions = [(math.ceil(w * scale), math.ceil(h * scale)) for w, h in sizes]
        with Image.new(
            "RGB", (max(w for w, _ in dimensions), sum(h for _, h in dimensions)), "white"
        ) as image:
            top = 0
            for index, (_, height) in enumerate(dimensions):
                page = document[index]
                try:
                    bitmap = page.render(scale=scale)
                    try:
                        with bitmap.to_pil() as rendered:
                            image.paste(rendered, (0, top))
                    finally:
                        bitmap.close()
                finally:
                    page.close()
                top += height
            output = BytesIO()
            image.save(output, format="PNG")
            return output.getvalue()


def generate_mail_exports(db, meeting_id, version):
    """Keep PDF downloads, while mail has exactly a task workbook and one image."""
    exports = generate_exports(db, meeting_id, version)
    image = db.scalar(
        select(Attachment).where(
            Attachment.meeting_id == meeting_id,
            Attachment.meeting_version == version,
            Attachment.kind == "minutes_png",
        )
    )
    if image is None:
        pdf = next(a for a in exports if a.kind == "minutes_pdf")
        content = minutes_image((get_settings().storage_dir / pdf.storage_key).read_bytes())
        key = f"exports/{uid()}.png"
        (get_settings().storage_dir / key).write_bytes(content)
        image = Attachment(
            filename=attachment_filename(pdf, db).removesuffix(".pdf") + ".png",
            storage_key=key,
            sha256=hashlib.sha256(content).hexdigest(),
            mime_type="image/png",
            size=len(content),
            uploader_id=pdf.uploader_id,
            meeting_id=meeting_id,
            meeting_version=version,
            kind="minutes_png",
        )
        db.add(image)
        db.flush()
    return [next(a for a in exports if a.kind == "tasks_xlsx"), image]
