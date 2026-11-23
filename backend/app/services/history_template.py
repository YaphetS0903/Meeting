"""Copy a confirmed historical document into a new, editable meeting draft."""

from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import select

from app.core.db import locked_get
from app.models import Meeting, MeetingVersion, Task, uid
from app.schemas import MeetingDraft
from app.services.access import meeting_access, task_access, visible_meetings
from app.services.business import audit, save_meeting
from app.services.historical_links import work_identity


def target_draft(db, actor, identifier, *, lock=False):
    target = locked_get(db, Meeting, identifier) if lock else db.get(Meeting, identifier)
    if not target:
        raise HTTPException(404, "本次会议不存在")
    meeting_access(db, actor, target, edit=True)
    if target.historical or target.published_version or not target.has_draft:
        raise HTTPException(409, "完整任务单只能填入尚未发布的新会议草稿")
    if not target.draft.get("series_id"):
        raise HTTPException(422, "请先为本次会议选择会议系列")
    return target


def templates(db, actor, target_id):
    target = target_draft(db, actor, target_id)
    rows = db.execute(
        visible_meetings(db, actor).where(
            Meeting.historical.is_(True),
            Meeting.series_id == target.draft["series_id"],
            Meeting.meeting_date <= target.meeting_date,
            Meeting.published_version > 0,
            Meeting.id != target.id,
        ).add_columns(MeetingVersion).join(MeetingVersion, (
            (MeetingVersion.meeting_id == Meeting.id)
            & (MeetingVersion.number == Meeting.published_version)
        )).order_by(Meeting.meeting_date.desc(), Meeting.created_at.desc())
    )
    return [{
        "id": source.id, "title": version.snapshot["title"],
        "meeting_date": source.meeting_date, "version": version.number,
        "review_count": sum(item["section"] == "review" for item in version.snapshot["items"]),
        "new_count": sum(item["section"] == "new" for item in version.snapshot["items"]),
    } for source, version in rows]


def import_template(db, actor, target_id, source_id, expected, source_version):
    target = target_draft(db, actor, target_id, lock=True)
    if target.draft_version != expected:
        raise HTTPException(409, "本次草稿已变化，请刷新后重新填入")
    source = db.get(Meeting, source_id)
    if not source:
        raise HTTPException(404, "历史会议不存在")
    meeting_access(db, actor, source)
    if source.id == target.id or not source.historical or not source.published_version:
        raise HTTPException(422, "请选择已确认入库的历史会议任务单")
    if source.published_version != source_version:
        raise HTTPException(409, "历史会议版本已变化，请重新选择任务单")
    if source.series_id != target.draft["series_id"]:
        raise HTTPException(422, "历史任务单必须与本次会议属于同一会议系列")
    if source.meeting_date > target.meeting_date:
        raise HTTPException(422, "历史会议日期不能晚于本次会议日期")
    version = db.scalar(select(MeetingVersion).where(
        MeetingVersion.meeting_id == source.id, MeetingVersion.number == source_version,
    ))
    if not version:
        raise HTTPException(404, "历史会议版本不存在")
    payload = deepcopy(target.draft)
    # The old carry action flattened both sections. Replace only those generated
    # review rows; independently entered/extended arrangements remain untouched.
    legacy = [row for row in payload.get("items", []) if (
        row["section"] == "review"
        and not row.get("source", {}).get("template_meeting_id")
        and (row.get("historical_reference") or {}).get("meeting_id") == source.id
        and (row.get("historical_reference") or {}).get("reason") == "从已确认的上期历史会议带入"
    )]
    legacy_ids = {row["id"] for row in legacy}
    payload["items"] = [row for row in payload.get("items", []) if row["id"] not in legacy_ids]
    known = {
        (row.get("source", {}).get("template_meeting_id"), row.get("source", {}).get("template_item_id"))
        for row in payload["items"]
    }
    added = 0
    for original in version.snapshot.get("items", []):
        if original["section"] not in {"review", "new"} or (source.id, original["id"]) in known:
            continue
        row = deepcopy(original)
        identity = work_identity(source.id, original)
        row.update(
            id=uid(), task_id=None, task_version=None, recurrence_key=None,
            historical_work_id=identity,
            historical_reference={
                "meeting_id": source.id, "version": source_version, "item_id": original["id"],
                "reason": "从已确认任务单按原分区填入本次会议草稿",
            },
        )
        # Preserve a live assignment when this historical work has already been
        # activated, instead of dispatching another copy at publication.
        if row["section"] == "new":
            task = db.scalar(select(Task).where(Task.historical_work_id == identity))
            if task:
                task_access(db, actor, task)
                row.update(task_id=task.id, task_version=task.version)
        row["source"] = {
            **row.get("source", {}), "template_meeting_id": source.id,
            "template_version": source_version, "template_item_id": original["id"],
        }
        payload["items"].append(row)
        known.add((source.id, original["id"]))
        added += 1
    decisions = version.snapshot.get("decisions", "").strip()
    if decisions and decisions not in payload.get("decisions", ""):
        payload["decisions"] = "\n\n".join(filter(None, [payload.get("decisions", ""), decisions]))
    summary = {
        "source_title": source.title, "added_count": added, "replaced_count": len(legacy),
        "review_count": sum(item["section"] == "review" for item in version.snapshot["items"]),
        "new_count": sum(item["section"] == "new" for item in version.snapshot["items"]),
    }
    if payload != target.draft:
        target = save_meeting(db, actor, target.id, MeetingDraft.model_validate(payload), expected)
        audit(db, actor, "meeting.import_template", target, after={
            **summary, "source_meeting_id": source.id, "source_version": source_version,
        }, reason="将已确认任务单的上期与本期内容按原分区填入新会议草稿")
    return target, summary
