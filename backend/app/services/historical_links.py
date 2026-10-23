"""Human-confirmed identities across historical minutes, without creating active tasks."""

from datetime import date
from difflib import SequenceMatcher

from fastapi import HTTPException
from sqlalchemy import select

from app.models import Meeting, MeetingVersion, Task, uid
from app.schemas import HistoricalReference
from app.services.access import meeting_access, task_access, visible_meetings


def work_identity(meeting_id, item):
    return item.get("historical_work_id") or f"{meeting_id}:{item['id']}"


def suggestions(db, actor, payload, item_id):
    item = next((row for row in payload.get("items", []) if row["id"] == item_id), None)
    if not item:
        raise HTTPException(404, "来源条目不存在")
    if not payload.get("meeting_date"):
        raise HTTPException(422, "请先确认会议日期并保存更正")
    if payload.get("kind") != "special" and not payload.get("department_id"):
        raise HTTPException(422, "请先确认会议部门和日期并保存更正")
    scope = (
        (Meeting.kind == "special") & Meeting.department_id.is_(None)
        if payload.get("kind") == "special"
        else Meeting.department_id == payload["department_id"]
    )
    rows = []
    for meeting, version in db.execute(
        select(Meeting, MeetingVersion)
        .join(
            MeetingVersion,
            (
                (MeetingVersion.meeting_id == Meeting.id)
                & (MeetingVersion.number == Meeting.published_version)
            ),
        )
        .where(
            Meeting.historical.is_(True),
            Meeting.id.in_(visible_meetings(db, actor).with_only_columns(Meeting.id)),
            scope,
            Meeting.meeting_date <= date.fromisoformat(payload["meeting_date"]),
        )
    ):
        for previous in version.snapshot.get("items", []):
            score = SequenceMatcher(None, item["content"], previous["content"]).ratio()
            reasons = []
            overlap = set(item.get("owner_ids", [])).intersection(previous.get("owner_ids", []))
            if overlap:
                score += 0.25
                reasons.append(f"{len(overlap)} 位责任人相同")
            if payload.get("series_id") and meeting.series_id == payload["series_id"]:
                score += 0.25
                reasons.append("同一会议系列")
            if item.get("due_date") and previous.get("due_date"):
                distance = abs(
                    (date.fromisoformat(item["due_date"]) - date.fromisoformat(previous["due_date"])).days
                )
                if distance <= 30:
                    score += 0.15 * (1 - distance / 31)
                    reasons.append(f"期限相差 {distance} 天")
            if score < 0.2:
                continue
            rows.append(
                {
                    "meeting_id": meeting.id,
                    "version": version.number,
                    "item_id": previous["id"],
                    "title": meeting.title,
                    "meeting_date": meeting.meeting_date,
                    "content": previous["content"],
                    "due_date": previous.get("due_date"),
                    "owner_names": previous.get("owner_names", []),
                    "status": previous["status"],
                    "source": previous.get("source", {}),
                    "match_reasons": reasons,
                    "score": round(score, 3),
                    "historical_work_id": work_identity(meeting.id, previous),
                }
            )
    return sorted(rows, key=lambda row: (-row["score"], str(row["meeting_date"]), row["item_id"]))[:30]


def resolve_link(db, actor, payload, item):
    reference = item.get("historical_reference")
    if not reference:
        return {
            "historical_work_id": uid(),
            "historical_reference": None,
            "task_id": None,
            "task_version": None,
        }
    reference = HistoricalReference.model_validate(reference)
    if not reference.reason.strip():
        raise HTTPException(422, "关联历史任务必须说明人工核对依据")
    meeting = db.get(Meeting, reference.meeting_id)
    if not meeting or not meeting.historical or meeting.published_version != reference.version:
        raise HTTPException(409, "关联会议版本已变化，请重新选择历史条目")
    meeting_access(db, actor, meeting)
    if not payload.get("meeting_date"):
        raise HTTPException(422, "请先确认会议日期，再关联历史条目")
    if meeting.meeting_date > date.fromisoformat(payload["meeting_date"]):
        raise HTTPException(422, "不能关联晚于本次会议的历史记录")
    snapshot = db.scalar(
        select(MeetingVersion).where(
            MeetingVersion.meeting_id == meeting.id,
            MeetingVersion.number == reference.version,
        )
    ).snapshot
    previous = next((r for r in snapshot["items"] if r["id"] == reference.item_id), None)
    if not previous:
        raise HTTPException(422, "关联的历史条目不存在")
    identity = work_identity(meeting.id, previous)
    task = db.scalar(select(Task).where(Task.historical_work_id == identity))
    if task:
        task_access(db, actor, task)
    return {
        "historical_work_id": identity,
        "historical_reference": reference.model_dump(),
        "task_id": task.id if task else None,
        "task_version": task.version if task else None,
    }
