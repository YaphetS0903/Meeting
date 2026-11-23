"""Match the two verified directory identities against published participation only."""
from fastapi import HTTPException
from sqlalchemy import select

from app.models import Meeting, MeetingVersion

# Verified company directory accounts. Names are explanatory; matching uses IDs so
# renaming an account or adding a namesake never reclassifies historical meetings.
SENIOR_PARTICIPANTS = {
    "4437f664-9bb9-42b7-aaa7-8047b95b2860": "朱慧铃",
    "ca542db9-8884-4b81-8280-1228e7c85fcb": "侯雨茜",
}


def validate_senior_leader(leader_id, *, senior_only=True):
    if leader_id and (not senior_only or leader_id not in SENIOR_PARTICIPANTS):
        raise HTTPException(422, "参会领导仅支持在中高层会议统计中选择朱慧铃或侯雨茜")


def is_senior_snapshot(snapshot, leader_id=None):
    validate_senior_leader(leader_id)
    return bool(({leader_id} if leader_id else set(SENIOR_PARTICIPANTS)).intersection(
        {snapshot.get("host_id"), *(snapshot.get("attendee_ids") or [])}
    ))


def senior_meeting_ids(db, start=None, end=None, leader_id=None):
    validate_senior_leader(leader_id)
    query = select(Meeting.id, MeetingVersion.snapshot).join(
        MeetingVersion, (MeetingVersion.meeting_id == Meeting.id)
        & (MeetingVersion.number == Meeting.published_version)
    )
    if start:
        query = query.where(Meeting.meeting_date >= start)
    if end:
        query = query.where(Meeting.meeting_date <= end)
    return {identifier for identifier, snapshot in db.execute(query)
            if is_senior_snapshot(snapshot, leader_id)}
