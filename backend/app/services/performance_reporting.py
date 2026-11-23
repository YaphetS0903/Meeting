"""Per-person assessment combining the two existing, authorized report sources."""

from datetime import date
from io import BytesIO

from fastapi import HTTPException
from openpyxl import Workbook

from app.services.business import today
from app.services.direct_reporting import direct_report
from app.services.exports import style_sheet, text_cell
from app.services.reporting import report


def metrics(items=0, assessed=0, completed=0):
    return dict(items=items, assessed=assessed, completed=completed,
                incomplete=assessed - completed, rate=completed / assessed if assessed else None)


def performance_report(db, actor, start: date, end: date, department_id=None, person_id=None):
    if start > end:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    regular = report(db, actor, start, end, "department_people", department_id=department_id,
                     person_id=person_id, include_details=False)
    direct = direct_report(db, actor, start, end, department_id, person_id)
    groups = {}
    for source, rows in [("regular", regular["groups"]), ("direct", direct["groups"])]:
        for row in rows:
            group = groups.setdefault(row["id"], dict(
                id=row["id"], label=row["label"], active=row["active"],
                regular=metrics(), direct=metrics(), meetings=[],
            ))
            if source == "regular":
                completed = row["counts"]["已完成"]
                group[source] = metrics(row["items"], completed + row["counts"]["未完成"], completed)
                group["meetings"] = row.get("meetings", [])
            else:
                group[source] = metrics(row["items"], row["due_items"], row["due_completed"])
    ordered = sorted(groups.values(), key=lambda row: (not row["active"], row["label"], row["id"]))
    for row in ordered:
        row["total"] = metrics(**{
            key: row["regular"][key] + row["direct"][key] for key in ("items", "assessed", "completed")
        })
    summary = {}
    for source in ("regular", "direct", "total"):
        summary[source] = metrics(**{
            key: sum(row[source][key] for row in ordered) for key in ("items", "assessed", "completed")
        })
    return dict(start=start.isoformat(), end=end.isoformat(), as_of=today().isoformat(),
                groups=ordered, summary=summary, undated_count=direct["undated_count"])


def export_performance_report(data):
    book = Workbook()
    sheet = book.active
    sheet.title = "绩效统计"
    labels = ["姓名"]
    for source in ("会议纪要", "直接派发", "合计"):
        labels += [source + label for label in ("事项数", "考核数", "完成数", "未完成数", "达成率")]
    values = [["统计周期", data["start"], data["end"], "统计截至", data["as_of"]], labels]
    for row in [dict(label="合计（按人统计）", **data["summary"]), *data["groups"]]:
        result = [row["label"] + ("（已停用）" if row.get("active") is False else "")]
        for source in ("regular", "direct", "total"):
            value = row[source]
            result += [value[key] for key in ("items", "assessed", "completed", "incomplete")]
            result += ["—" if value["rate"] is None else f"{value['rate']:.2%}"]
        values.append(result)
    values += [
        ["会议纪要口径", "按会议召开日期；仅已完成、未完成计入考核。"],
        ["派发口径", "按最新计划完成日期；已到期任务计入考核，仅验证通过算完成，待验证不算完成。"],
        ["合计口径", "两类完成数之和 ÷ 两类考核数之和。共同负责的任务按每人计数。"],
        ["未设计划完成日期（未计入）", data["undated_count"]],
    ]
    for n, row in enumerate(values, 1):
        for column, value in enumerate(row, 1):
            text_cell(sheet.cell(n, column), value)
    style_sheet(sheet, [24] + [20] * 15)
    sheet.freeze_panes = "B3"
    output = BytesIO()
    book.save(output)
    return output.getvalue()
