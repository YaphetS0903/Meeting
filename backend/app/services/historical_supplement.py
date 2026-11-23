"""Supplement the nearest later meeting's working copy from released history."""

from collections import Counter
from copy import deepcopy
from datetime import date

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.models import AuditLog, Meeting, MeetingVersion, uid
from app.schemas import MeetingDraft, MeetingItem
from app.services.historical_links import work_identity
from app.services.meeting_items import continuation_order
from app.services.role_permissions import meeting_allowed


def inherited_rows(source, version, target):
    from app.services.business import automatically_carried_row, continuation_result

    # Use the historical snapshot, not today's live assignments or later results.
    # New arrangements take precedence when a document repeats one work identity.
    eligible = {}
    for original in sorted(version.snapshot.get("items", []), key=lambda row: row["section"] != "new"):
        if automatically_carried_row(original) and original.get("owner_ids"):
            eligible.setdefault(work_identity(source.id, original), original)
    result = []
    for original in continuation_order(version.snapshot.get("items", [])):
        key = work_identity(source.id, original)
        if eligible.get(key) is not original:
            continue
        due_text = original.get("postponed_due_date") or original.get("due_date")
        due = date.fromisoformat(due_text) if due_text else None
        row = deepcopy(original)
        row.update(
            id=uid(), section="review", task_id=None, task_version=None,
            recurrence_key=None, postponed_due_date=None, backfill_reference=None,
            historical_owner_record=False, historical_work_id=key,
            historical_reference={
                "meeting_id": source.id, "version": version.number, "item_id": original["id"],
                "reason": "历史会议发布后自动补齐下期上期任务",
            },
            due_date=due_text, status=continuation_result(original, due, target.meeting_date),
            owner_results={}, exclusion_reason="", change_reason="",
        )
        row["source"] = {
            **row.get("source", {}),
            "history_supplement": {"meeting_id": source.id, "version": version.number, "item_id": original["id"]},
        }
        if row["status"]:
            row["source"]["continuation_default_status"] = row["status"]
        else:
            row["source"].pop("continuation_default_status", None)
        result.append(MeetingItem.model_validate(row).model_dump(mode="json"))
    return result


def signature(row):
    return (
        " ".join(row.get("content", "").split()), tuple(sorted(row.get("owner_ids", []))),
        row.get("due_date"), row.get("category", ""), row.get("requirements", ""),
        row.get("deliverable", ""),
    )


def supplement_next_meeting(db, actor, source):
    # Invalid/stale target drafts must not prevent the historical meeting releasing.
    try:
        with db.begin_nested():
            return _supplement_next_meeting(db, actor, source)
    except (HTTPException, ValidationError):
        return {"added": 0, "skipped": 1, "reason": "target_needs_review"}


def _supplement_next_meeting(db, actor, source):
    from app.services.business import audit, revision, save_meeting

    result = {"added": 0, "skipped": 0}
    if not source.historical or not source.published_version or not source.series_id:
        return result
    # Do not jump over an intervening historical meeting or modify several periods.
    target = db.scalar(select(Meeting).where(
        Meeting.series_id == source.series_id, Meeting.meeting_date > source.meeting_date,
    ).order_by(Meeting.meeting_date, Meeting.created_at.desc(), Meeting.id.desc()).limit(1).with_for_update())
    if not target or target.historical:
        return result
    if not all(meeting_allowed(db, actor, target, operation) for operation in (
        "edit", "assess", *(('revise',) if target.published_version and not target.has_draft else ()),
    )):
        return {**result, "skipped": 1, "reason": "permission"}
    version = db.scalar(select(MeetingVersion).where(
        MeetingVersion.meeting_id == source.id, MeetingVersion.number == source.published_version,
    ))
    if version is None:
        return result
    incoming = inherited_rows(source, version, target)
    payload = deepcopy(target.draft)
    review = [row for row in payload.get("items", []) if row.get("section") == "review"]
    known = {row.get("historical_work_id") for row in payload.get("items", [])}
    # A manually removed inherited row stays removed on a subsequent publication.
    for entry in db.scalars(select(AuditLog).where(
        AuditLog.action == "meeting.history.supplement", AuditLog.entity_id == target.id,
        AuditLog.after["source_meeting_id"].as_string() == source.id,
    )):
        known.update(entry.after.get("handled_work_ids", []))
    old_signatures, new_signatures = Counter(map(signature, review)), Counter(map(signature, incoming))
    additions, handled = [], []
    for row in incoming:
        key, match = row["historical_work_id"], signature(row)
        if key in known:
            continue
        # Existing manual review is preserved. Ambiguous matches are left for an editor.
        if old_signatures[match]:
            handled.append(key)
            result["skipped"] += old_signatures[match] != 1 or new_signatures[match] != 1
            continue
        additions.append(row)
        handled.append(key)
        known.add(key)
    if not additions and not handled:
        return result
    if additions:
        if target.published_version and not target.has_draft:
            revision(db, actor, target.id, "补齐前期历史会议的上期任务，原已发布版本保留")
        payload["items"] = review + additions + [
            row for row in payload.get("items", []) if row.get("section") != "review"
        ]
        save_meeting(db, actor, target.id, MeetingDraft.model_validate(payload), target.draft_version)
    result.update(added=len(additions), target_id=target.id, target_title=target.title,
                  target_date=target.meeting_date.isoformat(), revision=bool(target.published_version))
    audit(db, actor, "meeting.history.supplement", target, after={
        **result, "source_meeting_id": source.id, "source_version": source.published_version,
        "handled_work_ids": handled, "draft_version": target.draft_version,
    }, reason="按持续新建规则补齐上期任务，保留本次安排及已发布内容，不发送通知")
    db.flush()
    return result
