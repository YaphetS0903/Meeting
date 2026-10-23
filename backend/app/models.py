"""Separate live task state from immutable published meeting assessments."""

from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def uid() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class Identity:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Department(Identity, Base):
    __tablename__ = "departments"
    name: Mapped[str] = mapped_column(String(120))
    wecom_id: Mapped[str | None] = mapped_column(String(80), unique=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"))


class User(Identity, Base):
    __tablename__ = "users"
    name: Mapped[str] = mapped_column(String(120), index=True)
    username: Mapped[str | None] = mapped_column(String(120), unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    auth_version: Mapped[int] = mapped_column(Integer, default=0)
    wecom_id: Mapped[str | None] = mapped_column(String(120), unique=True)
    native_todo_userid: Mapped[str | None] = mapped_column(String(120), unique=True)
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"))
    email: Mapped[str | None] = mapped_column(String(254))
    mobile: Mapped[str | None] = mapped_column(String(40))
    position: Mapped[str] = mapped_column(String(120), default="")
    leader_department_ids: Mapped[list] = mapped_column(JSON, default=list)
    manual_leader_department_ids: Mapped[list] = mapped_column(JSON, default=list)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Membership(Base):
    __tablename__ = "user_departments"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    department_id: Mapped[str] = mapped_column(ForeignKey("departments.id"), primary_key=True)


class UserAlias(Identity, Base):
    __tablename__ = "user_aliases"
    alias: Mapped[str] = mapped_column(String(120), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"))
    __table_args__ = (UniqueConstraint("alias", "user_id", "department_id"),)


class MeetingSeries(Identity, Base):
    __tablename__ = "meeting_series"
    name: Mapped[str] = mapped_column(String(250))
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(20), default="general")
    __table_args__ = (UniqueConstraint("department_id", "name"),)


class Meeting(Identity, Base):
    __tablename__ = "meetings"
    title: Mapped[str] = mapped_column(String(500))
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"), index=True, nullable=True)
    series_id: Mapped[str | None] = mapped_column(ForeignKey("meeting_series.id"))
    kind: Mapped[str] = mapped_column(String(20), default="department")
    meeting_date: Mapped[date] = mapped_column(Date, index=True)
    host_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    recorder_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # The draft can change; published versions cannot. Revision does not remove the current published version.
    draft: Mapped[dict] = mapped_column(JSON, default=dict)
    draft_version: Mapped[int] = mapped_column(Integer, default=1)
    published_version: Mapped[int] = mapped_column(Integer, default=0)
    has_draft: Mapped[bool] = mapped_column(Boolean, default=True)
    historical: Mapped[bool] = mapped_column(Boolean, default=False)
    source_identity: Mapped[str | None] = mapped_column(String(64), unique=True)
    permission_generation: Mapped[int] = mapped_column(Integer, default=0)


class MeetingEditRequest(Identity, Base):
    __tablename__ = "meeting_edit_requests"
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), index=True)
    applicant_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    host_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    permission_generation: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(20), default="pending")
    reviewer_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("meeting_id", "applicant_id", "permission_generation"),)


class MeetingVersion(Identity, Base):
    __tablename__ = "meeting_versions"
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text, default="首次发布")
    publisher_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    __table_args__ = (UniqueConstraint("meeting_id", "number"),)


class MeetingAttendee(Base):
    __tablename__ = "meeting_attendees"
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)


class Task(Identity, Base):
    __tablename__ = "tasks"
    code: Mapped[str] = mapped_column(String(32), unique=True)
    content: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(250), default="")
    requirements: Mapped[str] = mapped_column(Text, default="")
    deliverable: Mapped[str] = mapped_column(Text, default="")
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    source_meeting_id: Mapped[str | None] = mapped_column(ForeignKey("meetings.id"), nullable=True)
    issuer_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    source_item_id: Mapped[str] = mapped_column(String(36))
    series_id: Mapped[str | None] = mapped_column(ForeignKey("meeting_series.id"))
    verifier_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    due_date: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="未到期")
    version: Mapped[int] = mapped_column(Integer, default=1)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completion_reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    recurrence_key: Mapped[str | None] = mapped_column(String(100))
    recurrence_period: Mapped[date | None] = mapped_column(Date)
    historical_work_id: Mapped[str | None] = mapped_column(String(100))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawal_reason: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (
        CheckConstraint("status IN ('已完成','未完成','未到期','不统计','关闭')", name="task_status_five"),
        UniqueConstraint("source_meeting_id", "source_item_id"),
        UniqueConstraint("recurrence_key", "recurrence_period", name="uq_task_recurrence_period"),
        UniqueConstraint("historical_work_id", name="uq_task_historical_work"),
    )


class DirectAssignmentRequest(Identity, Base):
    __tablename__ = "direct_assignment_requests"
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    request_key: Mapped[str] = mapped_column(String(100))
    payload_hash: Mapped[str] = mapped_column(String(64))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    __table_args__ = (UniqueConstraint("actor_id", "request_key", name="uq_direct_assignment_request"),)


class NativeTodoLink(Identity, Base):
    __tablename__ = "native_todo_links"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), unique=True)
    todo_id: Mapped[str] = mapped_column(String(200), unique=True)
    provider: Mapped[str] = mapped_column(String(20), default="cli", server_default="cli")
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    task_version: Mapped[int] = mapped_column(Integer)
    follower_ids: Mapped[list] = mapped_column(JSON, default=list)
    state: Mapped[str] = mapped_column(String(30), default="active")
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class NativeTodoAttempt(Identity, Base):
    __tablename__ = "native_todo_attempts"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), unique=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    task_version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(20), default="pending")


class RecurringRule(Identity, Base):
    __tablename__ = "recurring_rules"
    source_task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), unique=True)
    creator_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    frequency: Mapped[str] = mapped_column(String(20))
    anchor_date: Mapped[date] = mapped_column(Date)
    next_due_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    template: Mapped[dict] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    last_error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint("frequency IN ('weekly','monthly')", name="recurrence_frequency"),)


class TaskOwner(Base):
    __tablename__ = "task_owners"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)


class TaskVersion(Identity, Base):
    __tablename__ = "task_versions"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    number: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    source_meeting_id: Mapped[str | None] = mapped_column(ForeignKey("meetings.id"))
    __table_args__ = (UniqueConstraint("task_id", "number"),)


class Assessment(Identity, Base):
    __tablename__ = "assessment_records"
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), index=True)
    meeting_version: Mapped[int] = mapped_column(Integer)
    item_id: Mapped[str] = mapped_column(String(36))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    person_department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"))
    user_name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    due_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (
        UniqueConstraint("meeting_id", "meeting_version", "item_id", "user_id"),
        CheckConstraint(
            "status IN ('已完成','未完成','未到期','不统计','关闭')", name="assessment_status_five"
        ),
    )


class CompletionRequest(Identity, Base):
    __tablename__ = "completion_requests"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    task_version: Mapped[int] = mapped_column(Integer)
    applicant_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    explanation: Mapped[str] = mapped_column(Text)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    state: Mapped[str] = mapped_column(String(20), default="pending")
    verifier_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    opinion: Mapped[str] = mapped_column(Text, default="")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Attachment(Identity, Base):
    __tablename__ = "attachments"
    filename: Mapped[str] = mapped_column(String(500))
    storage_key: Mapped[str] = mapped_column(String(250), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    mime_type: Mapped[str] = mapped_column(String(150))
    size: Mapped[int] = mapped_column(Integer)
    uploader_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    meeting_id: Mapped[str | None] = mapped_column(ForeignKey("meetings.id"))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    kind: Mapped[str] = mapped_column(String(30), default="attachment")
    meeting_version: Mapped[int | None] = mapped_column(Integer)


class ImportBatch(Identity, Base):
    __tablename__ = "import_batches"
    name: Mapped[str] = mapped_column(String(250))
    creator_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    state: Mapped[str] = mapped_column(String(30), default="pending")
    scope_start: Mapped[date | None] = mapped_column(Date)
    scope_end: Mapped[date | None] = mapped_column(Date)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


class ImportSource(Identity, Base):
    __tablename__ = "import_sources"
    batch_id: Mapped[str] = mapped_column(ForeignKey("import_batches.id"))
    attachment_id: Mapped[str | None] = mapped_column(ForeignKey("attachments.id"))
    filename: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    duplicate_of: Mapped[str | None] = mapped_column(ForeignKey("import_sources.id"))
    diagnostics: Mapped[list] = mapped_column(JSON, default=list)


class ImportCandidate(Identity, Base):
    __tablename__ = "import_candidates"
    source_id: Mapped[str] = mapped_column(ForeignKey("import_sources.id"))
    sheet_name: Mapped[str] = mapped_column(String(250))
    original: Mapped[dict] = mapped_column(JSON)
    corrected: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    identity_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    state: Mapped[str] = mapped_column(String(30), default="pending")
    version: Mapped[int] = mapped_column(Integer, default=1)
    meeting_id: Mapped[str | None] = mapped_column(ForeignKey("meetings.id"))
    duplicate_of: Mapped[str | None] = mapped_column(ForeignKey("import_candidates.id"))


class ValidationIssue(Identity, Base):
    __tablename__ = "validation_issues"
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("import_candidates.id"), index=True)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("import_sources.id"))
    field_path: Mapped[str] = mapped_column(String(250), default="")
    code: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text)
    original: Mapped[dict] = mapped_column(JSON, default=dict)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution: Mapped[str | None] = mapped_column(Text)
    resolver_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class OutboxEvent(Identity, Base):
    __tablename__ = "outbox_events"
    event_key: Mapped[str] = mapped_column(String(250), unique=True)
    channel: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationLog(Identity, Base):
    __tablename__ = "notification_logs"
    event_id: Mapped[str] = mapped_column(ForeignKey("outbox_events.id"), index=True)
    result: Mapped[str] = mapped_column(String(30))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class AuditLog(Identity, Base):
    __tablename__ = "audit_logs"
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text, default="")


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


Index("ix_assessment_current", Assessment.meeting_id, Assessment.meeting_version)
