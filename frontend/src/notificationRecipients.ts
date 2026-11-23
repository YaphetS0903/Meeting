type AudienceDraft = {
  host_id?: string;
  attendee_ids?: string[];
  recipient_ids?: string[];
  recipient_customized?: boolean;
  cc_ids?: string[];
};

export function attendeeRecipientIds(draft: AudienceDraft): string[] {
  return [...new Set([draft.host_id, ...(draft.attendee_ids || [])])].filter(
    (id): id is string => Boolean(id),
  );
}

export function syncAttendeeRecipients(
  draft: AudienceDraft,
  previousAttendees: string[],
  reset = false,
) {
  const attendees = attendeeRecipientIds(draft);
  const inherited = new Set([...previousAttendees, ...attendees]);
  const extras = reset
    ? []
    : (draft.recipient_ids || []).filter((id) => !inherited.has(id));
  const chosen = new Set(draft.recipient_ids || []);
  const previous = new Set(previousAttendees);
  // Default the audience once, but honor deliberate removals across reopening
  // and reloads. Newly added attendees still join the default audience.
  const defaults =
    reset || !draft.recipient_customized
      ? attendees
      : attendees.filter((id) => chosen.has(id) || !previous.has(id));
  const recipients = [...new Set([...defaults, ...extras])];
  return {
    attendees,
    recipients,
    cc: [...new Set(draft.cc_ids || [])].filter(
      (id) => !recipients.includes(id),
    ),
  };
}
