"""Direct assignment statistics, scoped by the responsible employee's report rights."""

from datetime import date
from io import BytesIO

from fastapi import HTTPException
from openpyxl import Workbook
from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import aliased

from app.models import CompletionRequest, Task, TaskOwner, TaskVersion, User
from app.services.access import department_subtree_ids
from app.services.business import today
from app.services.exports import style_sheet, text_cell
from app.services.report_access import report_filters

STATUSES = ("已完成", "待验证", "未完成", "未到期")


def scoped_rows(db, actor, department_id=None, person_id=None):
    access, person_id = report_filters(db, actor, department_id, person_id)
    people = access.people().with_only_columns(User.id)
    if access.mode == "self":
        people = people.where(User.id == actor.id)
    if department_id:
        people = people.where(User.department_id.in_(department_subtree_ids(db, department_id)))
    if person_id:
        people = people.where(User.id == person_id)
    current_request = (
        select(CompletionRequest.id)
        .where(CompletionRequest.task_id == Task.id, CompletionRequest.task_version == Task.version)
        .correlate(Task)
    )
    status = case(
        (current_request.where(CompletionRequest.state == "approved").exists(), "已完成"),
        (current_request.where(CompletionRequest.state == "pending", Task.closed_at.is_(None)).exists(), "待验证"),
        (Task.due_date <= today(), "未完成"),
        else_="未到期",
    )
    return (
        select(
            Task.id.label("task_id"), User.id.label("user_id"), User.name.label("user_name"),
            User.active, User.department_id, Task.due_date, status.label("status"),
        )
        .select_from(Task)
        .join(TaskOwner, TaskOwner.task_id == Task.id)
        .join(User, User.id == TaskOwner.user_id)
        .where(
            Task.source_meeting_id.is_(None), Task.issuer_id.is_not(None),
            Task.withdrawn_at.is_(None), User.id.in_(people),
        )
    )


def aggregate_columns(rows, day):
    def count(condition):
        return func.count(distinct(case((condition, rows.c.task_id))))

    return [
        func.count(distinct(rows.c.task_id)).label("items"),
        *[count(rows.c.status == status).label(status) for status in STATUSES],
        count(rows.c.due_date <= day).label("due_items"),
        count((rows.c.due_date <= day) & (rows.c.status == "已完成")).label("due_completed"),
    ]


def metrics(row):
    return {
        "items": row["items"], "counts": {status: row[status] for status in STATUSES},
        "due_items": row["due_items"], "due_completed": row["due_completed"],
        "rate": row["due_completed"] / row["due_items"] if row["due_items"] else None,
    }


def direct_report(
    db, actor, start: date, end: date, department_id=None, person_id=None, status=None,
    *, include_details=False, details_only=False, offset=0, limit=20, all_details=False,
):
    if start > end:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    if status and status not in STATUSES:
        raise HTTPException(422, "无效的直接派发任务统计状态")
    scoped = scoped_rows(db, actor, department_id, person_id)
    rows = scoped.where(Task.due_date.between(start, end)).subquery()
    result = {"start": start.isoformat(), "end": end.isoformat(), "as_of": today().isoformat()}
    if not details_only:
        summary = db.execute(select(*aggregate_columns(rows, today()))).mappings().one()
        groups = db.execute(
            select(rows.c.user_id, rows.c.user_name, rows.c.active, *aggregate_columns(rows, today()))
            .group_by(rows.c.user_id, rows.c.user_name, rows.c.active)
            .order_by(rows.c.active.desc(), rows.c.user_name, rows.c.user_id)
        ).mappings()
        undated = scoped.where(Task.due_date.is_(None)).subquery()
        result.update(
            summary=metrics(summary),
            groups=[{"id": row["user_id"], "label": row["user_name"], "active": row["active"], **metrics(row)} for row in groups],
            undated_count=db.scalar(select(func.count(distinct(undated.c.task_id)))),
        )
    if include_details or details_only or all_details:
        identifiers = select(rows.c.task_id).distinct()
        if status:
            identifiers = identifiers.where(rows.c.status == status)
        result["details_total"] = db.scalar(select(func.count()).select_from(identifiers.subquery()))
        issuer = aliased(User)
        query = (
            select(Task, issuer.name.label("issuer_name"))
            .join(issuer, issuer.id == Task.issuer_id)
            .where(Task.id.in_(identifiers))
            .order_by(Task.due_date, Task.created_at, Task.id)
        )
        if not all_details:
            query = query.offset(offset).limit(limit)
        tasks = list(db.execute(query))
        task_ids = [task.id for task, _ in tasks]
        owners, states, histories = {}, {}, {}
        for row in db.execute(select(rows).where(rows.c.task_id.in_(task_ids))).mappings():
            owners.setdefault(row["task_id"], {})[row["user_id"]] = row["user_name"]
            states[row["task_id"]] = row["status"]
        for version in db.scalars(select(TaskVersion).where(TaskVersion.task_id.in_(task_ids)).order_by(TaskVersion.number)):
            value = version.snapshot.get("due_date")
            dates = histories.setdefault(version.task_id, [])
            if value and (not dates or dates[-1] != value):
                dates.append(value)
        result["details"] = [
            {
                "id": task.id, "content": task.content, "requirements": task.requirements,
                "deliverable": task.deliverable, "issuer_name": issuer_name,
                # Only expose owners within the viewer's report scope, even for shared tasks.
                "owner_names": sorted(owners.get(task.id, {}).values()),
                "due_date": task.due_date.isoformat(), "status": states[task.id],
                "date_history": [d for d in histories.get(task.id, []) if d != task.due_date.isoformat()],
            }
            for task, issuer_name in tasks
        ]
    return result


def export_direct_report(data):
    book = Workbook()
    summary = book.active
    summary.title = "派发任务汇总"
    rows = [
        ["按最新计划完成日期", data["start"], data["end"], "统计截至", data["as_of"]],
        ["姓名", "任务总数", *STATUSES, "已到期任务", "到期已完成", "到期完成率"],
    ]
    for group in [{"label": "合计（任务去重）", **data["summary"]}, *data["groups"]]:
        rows.append([
            group["label"], group["items"], *[group["counts"][s] for s in STATUSES],
            group["due_items"], group["due_completed"],
            "—" if group["rate"] is None else f"{group['rate']:.2%}",
        ])
    rows += [["口径", "共同负责的任务按每人计数，合计按任务去重；撤回任务不计入。"],
             ["到期完成率", "已到期且验证通过的任务 ÷ 已到期有效任务"],
             ["未设计划完成日期（不计入本期）", data["undated_count"]]]
    details = book.create_sheet("派发任务明细")
    detail_rows = [["任务内容", "派发人", "责任人（授权范围内）", "计划完成日期", "状态", "历史日期", "工作要求", "输出物"]]
    detail_rows += [
        [r["content"], r["issuer_name"], "、".join(r["owner_names"]), r["due_date"], r["status"],
         "、".join(r["date_history"]), r["requirements"], r["deliverable"]]
        for r in data["details"]
    ]
    for sheet, values, widths in [(summary, rows, [26] + [20] * 8), (details, detail_rows, [55, 16, 24, 20, 14, 30, 40, 30])]:
        for n, row in enumerate(values, 1):
            for c, value in enumerate(row, 1):
                text_cell(sheet.cell(n, c), value)
        style_sheet(sheet, widths)
    summary.freeze_panes = "B3"
    details.freeze_panes = "A2"
    out = BytesIO()
    book.save(out)
    return out.getvalue()
