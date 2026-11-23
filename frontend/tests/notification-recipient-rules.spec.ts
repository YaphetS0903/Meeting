import { test, expect } from "@playwright/test";
import { normalizeMailbox } from "../src/emailAddresses";
import { syncAttendeeRecipients } from "../src/notificationRecipients";

test("changing attendees drops old defaults while preserving explicit extras", () => {
  const draft = {
    host_id: "host",
    attendee_ids: ["b", "a", "b"],
    recipient_ids: ["old", "extra", "host", "a"],
    cc_ids: ["a", "cc"],
  };
  const next = syncAttendeeRecipients(draft, ["host", "old", "a"]);
  expect(next.recipients).toEqual(["host", "b", "a", "extra"]);
  expect(next.cc).toEqual(["cc"]);
  expect(syncAttendeeRecipients(draft, [], true).recipients).toEqual([
    "host",
    "b",
    "a",
  ]);
});

test("mailbox parsing accepts copied addresses and rejects header injection", () => {
  for (const value of [
    "张三 <Person@EXAMPLE.com>",
    "Ｐｅｒｓｏｎ＠ＥＸＡＭＰＬＥ．ｃｏｍ",
    "mailto:Person@example.com",
  ])
    expect(normalizeMailbox(value)).toBe("Person@example.com");
  for (const value of [
    "x@example.com\r\nBcc: hidden@example.com",
    "x@bad..com",
    "张三",
    "a@example.com,b@example.com",
  ])
    expect(normalizeMailbox(value)).toBeNull();
});

test("manual removals including the host survive synchronization and reload", () => {
  const draft = {
    host_id: "host",
    attendee_ids: ["a", "b"],
    recipient_ids: ["b", "extra"],
    recipient_customized: true,
    cc_ids: ["cc"],
  };
  for (let index = 0; index < 3; index++) {
    const next = syncAttendeeRecipients(draft, ["host", "a", "b"]);
    expect(next.recipients).toEqual(["b", "extra"]);
    expect(next.attendees).toEqual(["host", "a", "b"]);
    draft.recipient_ids = next.recipients;
  }
  expect(
    syncAttendeeRecipients({ ...draft, recipient_ids: [] }, ["host", "a", "b"])
      .recipients,
  ).toEqual([]);
});

test("new attendees join while removed people stay excluded until explicit restore", () => {
  const draft = {
    host_id: "host",
    attendee_ids: ["b", "new"],
    recipient_ids: ["old", "b", "extra"],
    recipient_customized: true,
    cc_ids: ["new", "cc"],
  };
  expect(syncAttendeeRecipients(draft, ["host", "old", "b"])).toMatchObject({
    recipients: ["b", "new", "extra"],
    cc: ["cc"],
  });
  expect(
    syncAttendeeRecipients(draft, ["host", "old", "b"], true).recipients,
  ).toEqual(["host", "b", "new"]);
});
