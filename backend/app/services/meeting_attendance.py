"""Display attendance modes without changing the complete participant snapshot."""


def attendance_rows(snapshot):
    attendees = snapshot.get("attendee_names") or []
    online = snapshot.get("online_attendee_names") or []
    attendee_ids = snapshot.get("attendee_ids") or []
    online_ids = set(snapshot.get("online_attendee_ids") or [])
    if online_ids and len(attendee_ids) == len(attendees):
        onsite = [name for identifier, name in zip(attendee_ids, attendees) if identifier not in online_ids]
    else:
        # Earlier snapshots may contain names only. Keep those exports readable.
        online_names = set(online)
        onsite = [name for name in attendees if name not in online_names]
    return [
        ("现场参会" if online else "参会人员", "、".join(onsite)),
        *([("线上参会", "、".join(online))] if online else []),
    ]
