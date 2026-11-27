"""Read-only task archive, including published rows without a live Task."""

from sqlalchemy import select

from app.models import Meeting, MeetingVersion, Task, User
from app.services.access import visible_meetings
from app.services.owner_results import person_result


def task_history(db, user, tasks, *, mine=True, state=None, q="", offset=0, limit=20):
    # Live task identities occur once; old imported rows may have no Task at all.
    rows = {task["id"]: dict(task, history_only=False) for task in tasks}
    identities = {
        (source, item): identifier
        for identifier, source, item in db.execute(
            select(Task.id, Task.source_meeting_id, Task.source_item_id).where(Task.id.in_(rows))
        )
    }
    publications = db.execute(
        select(Meeting, MeetingVersion.snapshot)
        .join(
            MeetingVersion,
            (MeetingVersion.meeting_id == Meeting.id)
            & (MeetingVersion.number == Meeting.published_version),
        )
        .where(Meeting.id.in_(visible_meetings(db, user).with_only_columns(Meeting.id)))
        .order_by(Meeting.meeting_date.desc(), Meeting.created_at.desc(), Meeting.id.desc())
    )
    # Names are projected only for the owners/verifier of returned, authorized rows.
    people = dict(db.execute(select(User.id, User.name)).all())
    seen = set()
    for meeting, snapshot in publications:
        for index, item in enumerate(
            sorted(snapshot.get("items", []), key=lambda row: row.get("section") != "new")
        ):
            if item.get("section") not in {"new", "review"}:
                continue
            owners = item.get("owner_ids", [])
            if mine and user.id not in owners:
                continue
            identifier = item.get("task_id") or identities.get((meeting.id, item.get("id")))
            result = person_result(item, user.id) if mine else item.get("status", "")
            if identifier in rows:
                if identifier not in seen:
                    seen.add(identifier)
                    row = rows[identifier]
                    row["recorded_status"] = result
                    row["notes"] = item.get("notes", "")
                    row["inbox_meeting_date"] = meeting.meeting_date
                    row["inbox_meeting_title"] = meeting.title
                    if (
                        item.get("section") == "review"
                        and result
                        and row["status"] not in {"关闭", "已完成", "不统计"}
                    ):
                        row["todo_status"] = result
                continue
            identifier = f"history:{meeting.id}:{item.get('id') or index}"
            verifier = item.get("verifier_id") or snapshot.get("host_id")
            rows[identifier] = {
                "id": identifier,
                "code": "",
                "history_only": True,
                "content": item.get("content", ""),
                "requirements": item.get("requirements", ""),
                "deliverable": item.get("deliverable", ""),
                "due_date": item.get("due_date"),
                "status": result,
                "todo_status": result,
                "recorded_status": result,
                "notes": item.get("notes", ""),
                "owner_ids": owners,
                "owner_names": [people.get(person, "历史员工") for person in owners],
                "verifier_id": verifier,
                "verifier_name": people.get(verifier, item.get("verifier_name", "未指定")),
                "source_meeting_id": meeting.id,
                "source_meeting_title": meeting.title,
                "inbox_meeting_title": meeting.title,
                "inbox_meeting_date": meeting.meeting_date,
                "can_view_source": True,
                "closed_at": None,
                "withdrawn_at": None,
                "requests": [],
                "versions": [],
            }
    result = []
    needle = q.strip().casefold()
    for row in rows.values():
        status = row.get("todo_status") or row.get("status")
        if row.get("withdrawn_at"):
            status = "已撤回"
        elif row.get("closed_at"):
            status = "关闭"
        row["todo_status"] = status
        if state and status != state:
            continue
        if needle and not any(
            needle in str(row.get(key) or "").casefold()
            for key in ("content", "code", "inbox_meeting_title", "source_meeting_title", "inbox_meeting_date")
        ):
            continue
        result.append(row)
    result.sort(
        key=lambda row: (str(row.get("inbox_meeting_date") or row.get("start_date") or ""), row["id"]),
        reverse=True,
    )
    return {"items": result[offset:offset + limit], "total": len(result)}
