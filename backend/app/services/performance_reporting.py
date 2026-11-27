"""Per-person assessment combining the two existing, authorized report sources."""

from copy import copy
from datetime import date
from io import BytesIO

from fastapi import HTTPException
from openpyxl import Workbook

from app.services.business import today
from app.services.direct_reporting import direct_report
from app.services.exports import style_sheet, text_cell
from app.services.reporting import report


STATUS_FIELDS = ("completed", "incomplete", "not_due", "excluded", "closed")


def metrics(completed=0, incomplete=0, not_due=0, excluded=0, closed=0):
    assessed = completed + incomplete
    return dict(items=assessed + not_due + excluded + closed, assessed=assessed,
                completed=completed, incomplete=incomplete, not_due=not_due,
                excluded=excluded, closed=closed,
                rate=completed / assessed if assessed else None)


def combine_metrics(values):
    totals = dict.fromkeys(STATUS_FIELDS, 0)
    for value in values:
        for key in STATUS_FIELDS:
            totals[key] += value[key]
    return metrics(**totals)


def performance_meeting_report(db, actor, start, end, department_id, person_id, **options):
    if start > end:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    return report(db, actor, start, end, "department_people", department_id=department_id,
                  person_id=person_id, include_details=False, **options)


def performance_report(db, actor, start: date, end: date, department_id=None, person_id=None):
    regular = performance_meeting_report(db, actor, start, end, department_id, person_id)
    direct = direct_report(db, actor, start, end, department_id, person_id)
    groups = {}
    for source, rows in [("regular", regular["groups"]), ("direct", direct["groups"])]:
        for row in rows:
            group = groups.setdefault(row["id"], dict(
                id=row["id"], label=row["label"], active=row["active"],
                regular=metrics(), direct=metrics(), meetings=[],
            ))
            if source == "regular":
                counts = row["counts"]
                group[source] = metrics(
                    completed=counts["已完成"], incomplete=counts["未完成"],
                    not_due=counts["未到期"], excluded=counts["不统计"], closed=counts["关闭"],
                )
                group["meetings"] = row.get("meetings", [])
            else:
                group[source] = metrics(
                    completed=row["due_completed"],
                    incomplete=row["due_items"] - row["due_completed"],
                    not_due=row["items"] - row["due_items"],
                )
    ordered = sorted(groups.values(), key=lambda row: (not row["active"], row["label"], row["id"]))
    for row in ordered:
        row["total"] = combine_metrics((row["regular"], row["direct"]))
    summary = {
        source: combine_metrics(row[source] for row in ordered)
        for source in ("regular", "direct", "total")
    }
    return dict(start=start.isoformat(), end=end.isoformat(), as_of=today().isoformat(),
                groups=ordered, summary=summary, undated_count=direct["undated_count"])


def export_performance_report(data):
    book = Workbook()
    sheet = book.active
    sheet.title = "绩效统计"
    labels = ["姓名", "任务总数", "已完成", "未完成", "未到期", "不统计", "已关闭", "绩效达成率"]
    values = [["统计周期", data["start"], data["end"], "统计截至", data["as_of"]], labels]
    rows = list(data["groups"])
    if len(rows) > 1:
        rows.append(dict(label="合计（按人）", **data["summary"]))
    for row in rows:
        name = row["label"] + ("（已停用）" if row.get("active") is False else "")
        total = row["total"]
        values.append([name, total["items"],
                       *(total[key] for key in STATUS_FIELDS),
                       "—" if total["rate"] is None else total["rate"]])
    values += [
        ["会议纪要口径", "未到期任务按最新已发布的期限计一次；其他结果按会议日期保留每次评定。"],
        ["派发口径", "按最新计划完成日期；已到期任务计入考核，仅验证通过算完成，待验证不算完成。"],
        ["合计口径", "已完成 ÷（已完成＋未完成）。共同负责的任务按每人计数。"],
        ["未设计划完成日期（未计入）", data["undated_count"]],
    ]
    for n, row in enumerate(values, 1):
        for column, value in enumerate(row, 1):
            text_cell(sheet.cell(n, column), value)
    style_sheet(sheet, [24] + [14] * (len(labels) - 1))
    for index, row in enumerate(rows, 3):
        value = row["total"]["rate"]
        if value is None:
            continue
        cell = sheet.cell(index, len(labels))
        cell.number_format = "0.0%"
        if value < 0.8:
            font = copy(cell.font)
            font.color = "B91C1C"
            cell.font = font
    sheet.freeze_panes = "B3"
    output = BytesIO()
    book.save(output)
    return output.getvalue()
