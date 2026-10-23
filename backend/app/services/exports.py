"""Exports always read a published snapshot; formulas and HTML are never evaluated."""

import hashlib
import math
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select

from app.core.config import get_settings
from app.models import Attachment, MeetingVersion, uid

HEADERS = [
    "序号",
    "项目 / 类别",
    "任务内容及要求",
    "责任人",
    "计划完成日期",
    "结果 / 验证人",
    "输出物 / 备注",
]


def task_rows(snapshot, section):
    rows = []
    for n, item in enumerate([i for i in snapshot.get("items", []) if i["section"] == section], 1):
        content = item.get("content", "")
        notes = "\n".join(
            filter(None, [item.get("deliverable"), item.get("notes"), item.get("exclusion_reason")])
        )
        rows.append(
            [
                str(n),
                item.get("category", ""),
                content,
                "、".join(item.get("owner_names", [])),
                item.get("due_date") or "",
                item.get("status", "")
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
            f"日期：{snapshot['meeting_date']}  时间：{snapshot.get('meeting_time', '')}  地点：{snapshot.get('location', '')}  部门：{snapshot.get('department_name', '')}  主持人：{snapshot.get('host_name', '')}",
        )
        ws.merge_cells("A3:G3")
        text_cell(
            ws["A3"], name + ("（仅此分区纳入本期考核）" if section == "review" else "（不计本期考核）")
        )
        for col, val in enumerate(HEADERS, 1):
            text_cell(ws.cell(4, col), val)
        for values in task_rows(snapshot, section):
            # Split very long content into continuation rows rather than clipping Excel's max row height.
            chunks = [[str(v)[j : j + 260] for j in range(0, max(1, len(str(v))), 260)] for v in values]
            for part in range(max(map(len, chunks))):
                row = ws.max_row + 1
                for col, pieces in enumerate(chunks, 1):
                    text_cell(
                        ws.cell(row, col), pieces[part] if part < len(pieces) else ("续" if col == 1 else "")
                    )
                ws.row_dimensions[row].height = max(
                    42,
                    min(
                        380,
                        max(
                            math.ceil(len(str(ws.cell(row, c).value or "")) / w)
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
            ("参会人员", "、".join(snapshot.get("attendee_names", []))),
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
    if "NotoSansSC" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(
            TTFont("NotoSansSC", str(Path(__file__).resolve().parents[1] / "assets/fonts/NotoSansSC.ttf"))
        )
    style = ParagraphStyle("Chinese", fontName="NotoSansSC", fontSize=9, leading=14, wordWrap="CJK")
    heading = ParagraphStyle("Heading", parent=style, fontSize=15, leading=23, spaceAfter=10)

    def p(text):
        return Paragraph(escape(str(text or "")).replace("\n", "<br/>"), style)

    out = BytesIO()
    doc = SimpleDocTemplate(
        out, pagesize=landscape(A4), leftMargin=28, rightMargin=28, topMargin=28, bottomMargin=32
    )
    story = [
        Paragraph(escape(snapshot["title"]), heading),
        p(
            f"日期：{snapshot['meeting_date']}    部门：{snapshot.get('department_name', '')}    主持人：{snapshot.get('host_name', '')}"
        ),
        p(f"时间：{snapshot.get('meeting_time', '')}    地点：{snapshot.get('location', '')}"),
        p("参会人员：" + "、".join(snapshot.get("attendee_names", []))),
        p("缺席及原因：" + snapshot.get("absentees", "")),
        Spacer(1, 14),
    ]
    for title, section in [("一、上期任务回顾", "review"), ("三、本次工作部署", "new")]:
        if section == "new":
            story.extend(
                [
                    Spacer(1, 12),
                    Paragraph("二、正式决议", heading),
                    p(snapshot.get("decisions", "") or "无"),
                    Spacer(1, 12),
                ]
            )
        story.append(Paragraph(title, heading))
        values = [[p(v) for v in HEADERS]] + [[p(v) for v in row] for row in task_rows(snapshot, section)]
        if len(values) == 1:
            values.append([p("—")] * 7)
        table = Table(
            values, colWidths=[34, 66, 244, 90, 77, 102, 172 - 55], repeatRows=1, splitByRow=1, splitInRow=1
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dce8f8")),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c6d1df")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        story.append(table)
    story.extend(
        [
            Spacer(1, 16),
            p("编制人：" + snapshot.get("recorder_name", "")),
        ]
    )

    def footer(canvas, document):
        canvas.setFont("NotoSansSC", 8)
        canvas.drawRightString(810, 16, f"第 {document.page} 页 · 会议纪要")

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()


def export_report(data):
    book = Workbook()
    ws = book.active
    ws.title = "考核汇总"
    rows = [
        ["统计范围", data["start"], data["end"], data["quality"]],
        ["统计对象", "事项数", "考核人次", "已完成", "未完成", "未到期", "不统计", "关闭", "达成率"],
    ]
    for group in [{"label": "合计", **data["summary"]}, *data["groups"]]:
        rows.append(
            [
                group["label"],
                group["items"],
                group["person_count"],
                *[group["counts"][s] for s in ["已完成", "未完成", "未到期", "不统计", "关闭"]],
                "—" if group["rate"] is None else f"{group['rate']:.2%}",
            ]
        )
    for row in rows:
        n = ws.max_row + 1 if ws.max_row > 1 or ws.cell(1, 1).value is not None else 1
        for c, v in enumerate(row, 1):
            text_cell(ws.cell(n, c), v)
    style_sheet(ws, [24] * 9)
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
            filename=f"{published.snapshot['title'][:120]}-V{version}-{'任务单' if fmt == 'tasks' else '纪要'}.{extension}",
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
