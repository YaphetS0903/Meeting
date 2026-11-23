"""Published current arrangements, separate from immutable review assessments."""
from sqlalchemy import select

from app.models import Meeting, MeetingVersion, User
from app.services.access import department_subtree_ids
from app.services.meeting_views import apply_series_key
from app.services.report_access import report_filters
from app.services.senior_meetings import SENIOR_PARTICIPANTS, is_senior_snapshot, validate_senior_leader


def senior_arrangements(db, user, start, end, department_id=None, person_id=None,
                        series_key=None, kind=None, *, include_details=True,
                        offset=0, limit=None, leader_id=None):
    access, person_id = report_filters(db, user, department_id, person_id, senior_only=True)
    validate_senior_leader(leader_id)
    people_query = access.people()
    if department_id:
        people_query = people_query.where(User.department_id.in_(department_subtree_ids(db, department_id)))
    if person_id:
        people_query = people_query.where(User.id == person_id)
    people = {p.id: p for p in db.scalars(people_query)}
    query = select(Meeting, MeetingVersion.snapshot).join(
        MeetingVersion, (MeetingVersion.meeting_id == Meeting.id)
        & (MeetingVersion.number == Meeting.published_version)
    ).where(Meeting.meeting_date >= start, Meeting.meeting_date <= end)
    if kind:
        query = query.where(Meeting.kind == kind)
    query = apply_series_key(query, series_key).order_by(Meeting.meeting_date, Meeting.id)
    total, meeting_count, details, meetings = 0, 0, [], []
    for meeting, snapshot in db.execute(query):
        if not is_senior_snapshot(snapshot, leader_id):
            continue
        items = snapshot.get("items", [])
        # A delegated report covers authorized employees, without revealing the
        # other owners or granting access to the full meeting and attachments.
        visible_meeting = any(set(item.get("owner_ids") or []).intersection(people) for item in items)
        if visible_meeting or (access.mode == "all" and not department_id and not person_id):
            meeting_count += 1
            participants = {snapshot.get("host_id"), *(snapshot.get("attendee_ids") or [])}
            meetings.append({"id": meeting.id, "title": meeting.title,
                             "meeting_date": meeting.meeting_date.isoformat(), "kind": meeting.kind,
                             "leaders": [name for identifier, name in SENIOR_PARTICIPANTS.items()
                                         if identifier in participants]})
        for item in items:
            if item.get("section") != "new":
                continue
            owners = [people[identifier] for identifier in dict.fromkeys(item.get("owner_ids") or [])
                      if identifier in people]
            if not owners:
                continue
            position = total
            total += 1
            if not include_details or position < offset or (limit is not None and len(details) >= limit):
                continue
            details.append({
                "id": f"{meeting.id}:{item['id']}", "item_id": item["id"],
                "meeting_id": meeting.id, "meeting_title": meeting.title,
                "meeting_date": meeting.meeting_date.isoformat(), "meeting_version": meeting.published_version,
                "user_name": "、".join(owner.name for owner in owners),
                "content": item.get("content", ""), "requirements": item.get("requirements", ""),
                "due_date": item.get("postponed_due_date") or item.get("due_date"),
                "deliverable": item.get("deliverable", ""), "notes": item.get("notes", ""),
            })
    return {"total": total, "meeting_count": meeting_count, "details": details, "meetings": meetings,
            "offset": offset, "limit": limit}
