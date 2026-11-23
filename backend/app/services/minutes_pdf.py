"""Printable meeting form rendered only from the supplied version snapshot."""

from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable, PageBreak, Paragraph, SimpleDocTemplate, Table, TableStyle

from app.services.meeting_attendance import attendance_rows
from app.services.meeting_items import arrangement_groups, ordered_arrangements
from app.services.owner_results import result_text

FONT = "NotoSansSC"
BLUE = colors.HexColor("#4472C4")
GRID = colors.HexColor("#707070")
HEADERS = [
    "序号",
    "具体工作任务内容（写清标准、要求）",
    "责任人",
    "计划完成时间",
    "任务闭环完成结果",
    "备注",
]


class MinutesTaskTable(Table):
    """Continue on the next page, repeating column labels without section titles."""

    def split(self, availWidth, availHeight):
        parts = super().split(availWidth, availHeight)
        if len(parts) > 1:
            # A remainder with repeated headings can otherwise fit back into the
            # same frame after an in-row split, duplicating headings mid-page.
            return [parts[0], PageBreak(), *parts[1:]]
        return parts


class MeetingKind(Flowable):
    """Draw checkboxes rather than relying on a font's checkbox glyphs."""

    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.width, self.height = 150, 15

    def wrap(self, available_width, available_height):
        return min(self.width, available_width), self.height

    def draw(self):
        canvas = self.canv
        canvas.saveState()
        canvas.setStrokeColor(GRID)
        canvas.setLineWidth(0.6)
        canvas.setFont(FONT, 8.2)
        for x, value, label in [(0, "department", "部门周例会"), (78, "special", "专项工作会议")]:
            canvas.rect(x, 3, 6.5, 6.5)
            if self.kind == value:
                canvas.setStrokeColor(colors.black)
                canvas.setLineWidth(1)
                line = canvas.beginPath()
                line.moveTo(x + 1, 6)
                line.lineTo(x + 2.8, 4.3)
                line.lineTo(x + 5.6, 8.6)
                canvas.drawPath(line)
                canvas.setStrokeColor(GRID)
                canvas.setLineWidth(0.6)
            canvas.drawString(x + 10, 2.5, label)
        canvas.restoreState()


def render_minutes_pdf(snapshot):
    if FONT not in pdfmetrics.getRegisteredFontNames():
        font_path = Path(__file__).resolve().parents[1] / "assets/fonts/NotoSansSC.ttf"
        pdfmetrics.registerFont(TTFont(FONT, str(font_path)))

    body = ParagraphStyle(
        "MinutesBody", fontName=FONT, fontSize=9, leading=13, wordWrap="CJK", textColor=colors.black
    )
    center = ParagraphStyle("MinutesCenter", parent=body, alignment=TA_CENTER)
    title_style = ParagraphStyle(
        "MinutesTitle", parent=center, fontSize=15, leading=21, spaceBefore=2, spaceAfter=2
    )
    band_style = ParagraphStyle(
        "MinutesBand", parent=center, fontSize=10.5, leading=15, textColor=colors.white
    )
    head_style = ParagraphStyle("MinutesHeader", parent=center, fontSize=8.5, leading=12)
    category_style = '<font size="8" color="#555555">项目 / 类别：{}</font><br/>'

    def markup(value):
        return escape(str(value or "")).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")

    def p(value, style=body):
        return Paragraph(markup(value), style)

    def base_style():
        return [
            ("GRID", (0, 0), (-1, -1), 0.5, GRID),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]

    out = BytesIO()
    doc = SimpleDocTemplate(
        out,
        pagesize=A4,
        leftMargin=24,
        rightMargin=24,
        topMargin=24,
        bottomMargin=30,
        title=str(snapshot.get("title", "会议纪要")),
        author=str(snapshot.get("recorder_name", "")),
    )
    width = doc.width
    title_table = Table(
        [[p(snapshot.get("title", "会议纪要"), title_style)]],
        colWidths=[width],
        minRowHeights=[34],
        splitByRow=1,
        splitInRow=1,
        hAlign="CENTER",
    )
    title_table.setStyle(
        TableStyle(
            base_style()
            + [
                ("TOPPADDING", (0, 0), (-1, 0), 7),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
            ]
        )
    )
    metadata = [
        [
            p("会议所属部门", center),
            p(snapshot.get("department_name", ""), center),
            p("会议类型", center),
            MeetingKind(snapshot.get("kind")),
        ],
        [
            p("会议召开日期", center),
            p(snapshot.get("meeting_date", ""), center),
            p("会议召开时间", center),
            p(snapshot.get("meeting_time", ""), center),
        ],
        [
            p("会议地点", center),
            p(snapshot.get("location", ""), center),
            p("主持人", center),
            p(snapshot.get("host_name", ""), center),
        ],
    ]
    metadata_table = Table(
        metadata,
        colWidths=[width * ratio for ratio in (0.30, 0.28, 0.12, 0.30)],
        minRowHeights=[26, 24, 24],
        repeatRows=0,
        splitByRow=1,
        splitInRow=1,
        hAlign="CENTER",
    )
    metadata_table.setStyle(TableStyle(base_style()))
    story = [title_table, metadata_table]
    for label, value in [
        *attendance_rows(snapshot),
        ("缺席人员及原因", snapshot.get("absentees", "") or "无"),
    ]:
        # Avoid horizontal spans on a long row: ReportLab can corrupt their geometry when splitting.
        table = Table(
            [[p(label, center), p(value)]],
            colWidths=[width * 0.30, width * 0.70],
            minRowHeights=[24],
            splitByRow=1,
            splitInRow=1,
            hAlign="CENTER",
        )
        table.setStyle(TableStyle(base_style() + [("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(table)
    column_widths = [width * ratio for ratio in (0.045, 0.425, 0.105, 0.13, 0.105, 0.19)]

    def task_table(section, label):
        rows = [[p(label, band_style), "", "", "", "", ""], [p(value, head_style) for value in HEADERS]]
        source_items = snapshot.get("items", [])
        items = ordered_arrangements(source_items) if section == "new" else [
            item for item in source_items if item.get("section") == section
        ]
        group_starts, group_rows, offset = {}, [], 0
        if section == "new":
            for group_label, group in arrangement_groups(source_items):
                if group_label:
                    group_starts[offset + 1] = group_label
                offset += len(group)
        for number, item in enumerate(items, 1):
            if number in group_starts:
                group_rows.append(len(rows))
                rows.append([p(group_starts[number]), "", "", "", "", ""])
            content = markup(item.get("content", ""))
            if item.get("category"):
                content = category_style.format(markup(item["category"])) + content
            notes = [
                "输出物：" + item["deliverable"] if item.get("deliverable") else "",
                item.get("notes", ""),
                "排除依据：" + item["exclusion_reason"] if item.get("exclusion_reason") else "",
            ]
            status = result_text(item) if section == "review" else item.get("status", "") or ""
            status_style = ParagraphStyle(
                "MinutesStatus",
                parent=center,
                textColor={"已完成": colors.HexColor("#15803D"),
                           "未完成": colors.HexColor("#D62929")}.get(status, colors.black),
            )
            result_cell = p(status, status_style)
            notes_cell = p("\n".join(filter(None, notes)))
            if section == "review" and item.get("status") == "部分完成":
                result_cell = p(status.splitlines()[0].replace("（", "\n（"), center)
                lines = []
                for line in status.splitlines()[1:]:
                    color = "#15803D" if line.endswith("：已完成") else "#D62929" if line.endswith("：未完成") else "#000000"
                    lines.append(f'<font color="{color}">{markup(line)}</font>')
                lines.extend(markup(value) for value in notes if value)
                notes_cell = Paragraph("<br/>".join(lines), body)
            due_cell = p(item.get("due_date", ""), center)
            historical_dates = list(item.get("source", {}).get("date_history", []))
            if item.get("postponed_due_date") or historical_dates:
                historical_dates += [item.get("due_date", "")] if item.get("postponed_due_date") else []
                due_cell = Paragraph(
                    ''.join(f'<strike>{markup(value)}</strike><br/>' for value in historical_dates)
                    + markup(item.get("postponed_due_date") or item.get("due_date", "")), center,
                )
            rows.append(
                [
                    p(number, center),
                    Paragraph(content, body),
                    p("\n".join(item.get("owner_names", [])), center),
                    due_cell,
                    result_cell,
                    notes_cell,
                ]
            )
        if not items:
            rows.append([p("本分区无工作事项", center), "", "", "", "", ""])
        commands = base_style() + [
            ("SPAN", (0, 0), (-1, 0)),
            ("BACKGROUND", (0, 0), (-1, 0), BLUE),
            ("LINEABOVE", (0, 1), (-1, 1), 0.5, GRID),
            ("LEFTPADDING", (0, 1), (0, -1), 3),
            ("RIGHTPADDING", (0, 1), (0, -1), 3),
            ("TOPPADDING", (0, 0), (-1, 0), 6),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
            ("VALIGN", (0, 2), (-1, -1), "TOP"),
        ]
        if not items:
            commands.append(("SPAN", (0, 2), (-1, 2)))
        for number in group_rows:
            commands.extend([
                ("SPAN", (0, number), (-1, number)),
                ("BACKGROUND", (0, number), (-1, number), colors.HexColor("#F4F4F5")),
            ])
        table = MinutesTaskTable(
            rows,
            colWidths=column_widths,
            minRowHeights=[27, 32] + [25] * (len(rows) - 2),
            repeatRows=(1,),  # Repeat only column labels; show each blue section title once.
            splitByRow=1,
            splitInRow=1,
            hAlign="CENTER",
        )
        table.setStyle(TableStyle(commands))
        return table

    decisions = Table(
        [[p("本次会议正式决议事项", band_style)], [p(snapshot.get("decisions", "") or "无")]],
        colWidths=[width],
        minRowHeights=[27, 40],
        repeatRows=1,
        splitByRow=1,
        splitInRow=1,
        hAlign="CENTER",
    )
    decisions.setStyle(
        TableStyle(
            base_style()
            + [
                ("BACKGROUND", (0, 0), (-1, 0), BLUE),
                ("TOPPADDING", (0, 0), (-1, 0), 6),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
                ("VALIGN", (0, 1), (-1, -1), "TOP"),
            ]
        )
    )
    story.extend([
        decisions,
        task_table("review", "上次会议核心工作部署内容"),
        task_table("new", "本次会议核心工作部署内容"),
    ])
    recorder_style = ParagraphStyle("MinutesRecorder", parent=body, fontSize=8, alignment=TA_LEFT)
    recorder = p("编制人：" + snapshot.get("recorder_name", ""), recorder_style)
    recorder.spaceBefore = 10
    story.append(recorder)

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.setFont(FONT, 7.5)
        canvas.drawCentredString(A4[0] / 2, 14, f"第 {document.page} 页")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()
