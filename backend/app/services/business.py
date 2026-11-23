from copy import deepcopy
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import locked_get
from app.models import (
    Assessment,
    Attachment,
    AuditLog,
    CompletionRequest,
    Department,
    Meeting,
    MeetingAttendee,
    MeetingSeries,
    MeetingVersion,
    NativeTodoLink,
    OutboxEvent,
    SystemSetting,
    Task,
    TaskOwner,
    TaskVersion,
    User,
    now,
    uid,
)
from app.schemas import (
    ApplyCompletion,
    MeetingDraft,
    MeetingItem,
    PublishRequest,
    TaskChange,
    VerifyCompletion,
)
from app.services.access import (
    can_confirm_meeting_results,
    can_manage_direct_task,
    department_ids,
    direct_assignees,
    meeting_access,
    task_access,
)
from app.services.departments import department_path
from app.services.meeting_items import continuation_order, is_unfinished_extension
from app.services.owner_results import person_result
from app.services.role_permissions import allows, meeting_allowed, meeting_require
from app.services.role_permissions import require as require_permission


def today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def audit(db, actor, action, entity, before=None, after=None, reason="", context=None):
    from app.services.operation_logs import audit_context, sanitize

    db.add(
        AuditLog(
            actor_id=actor.id,
            action=action,
            entity_type=entity.__tablename__,
            entity_id=entity.id,
            before=sanitize(before),
            after=sanitize(after),
            reason=reason,
            context={**audit_context(actor, action, entity), **sanitize(context or {})},
        )
    )


def external_notifications_enabled(db: Session) -> bool:
    setting = db.get(SystemSetting, "reminders")
    return bool(setting.value.get("enabled", True)) if setting else True


def daily_reminders_enabled(db: Session) -> bool:
    setting = db.get(SystemSetting, "reminders")
    return bool(setting and setting.value.get("daily_enabled", False))


def task_wecom_enabled(db: Session, task: Task) -> bool:
    return external_notifications_enabled(db) or bool(
        get_settings().wecom_direct_notifications_enabled
        and task.source_meeting_id is None
        and task.issuer_id
    )


def notification_enabled(db: Session, channel: str, payload: dict) -> bool:
    if payload.get("deadline_reminder"):
        from app.services.deadline_reminders import enabled

        if not enabled(db):
            return False
    if (
        channel in {"wecom", "email"}
        and ("reminder_task_ids" in payload or "verification_request_ids" in payload)
        and not daily_reminders_enabled(db)
    ):
        return False
    if channel not in {"wecom", "email"} or external_notifications_enabled(db):
        return True
    if channel != "wecom" or not get_settings().wecom_direct_notifications_enabled:
        return False
    identifiers = list(payload.get("reminder_task_ids", []))
    identifiers.extend(item["task_id"] for item in payload.get("deadline_items", []))
    if payload.get("task_id"):
        identifiers.append(payload["task_id"])
    if "verification_request_ids" in payload:
        identifiers.extend(
            db.scalars(
                select(CompletionRequest.task_id).where(
                    CompletionRequest.id.in_(payload["verification_request_ids"])
                )
            )
        )
    return any(
        task_wecom_enabled(db, task) for identifier in identifiers if (task := db.get(Task, identifier))
    )


def enqueue(db: Session, key: str, channel: str, payload: dict):
    if not db.scalar(select(OutboxEvent.id).where(OutboxEvent.event_key == key)):
        disabled = not notification_enabled(db, channel, payload)
        db.add(
            OutboxEvent(
                event_key=key,
                channel=channel,
                payload=payload,
                state="skipped" if disabled else "pending",
                sent_at=now() if disabled else None,
                last_error="该来源的外部提醒已关闭，未发送且不会补发" if disabled else None,
            )
        )


def enqueue_native_todo(db: Session, task: Task, action: str, *, key_suffix: str | None = None):
    """Queue only enabled task sources and eligible enterprise members."""
    settings = get_settings()
    source = "meeting" if task.source_meeting_id else "direct"
    if not settings.native_todo_source_enabled(source):
        return
    link = db.scalar(select(NativeTodoLink).where(NativeTodoLink.task_id == task.id))
    if settings.native_todo_app_enabled and not link:
        return  # New application todos require an explicit JS-SDK confirmation.
    if link and link.provider == "app":
        if action == "create":
            return
        suffix = key_suffix or f"v{task.version}"
        enqueue(
            db,
            f"native-todo:{task.id}:{action}:{suffix}",
            "native_todo",
            {
                "task_id": task.id,
                "task_version": task.version,
                "action": action,
            },
        )
        return
    if task.due_date is None and action != "finish":
        return
    owners = [db.get(User, identifier) for identifier in owner_ids(db, task)]
    if action != "finish" and not any(
        user and settings.native_todo_member_allowed(user.wecom_id, user.active) for user in owners
    ):
        return
    if action == "update" and not link:
        action = "create"
    suffix = key_suffix or f"v{task.version}"
    enqueue(
        db,
        f"native-todo:{task.id}:{action}:{suffix}",
        "native_todo",
        {"task_id": task.id, "task_version": task.version, "action": action},
    )


def active_user(db: Session, user_id: str):
    user = db.get(User, user_id)
    if not user or not user.active:
        raise HTTPException(422, "人员不存在或已停用，请重新选择")
    return user


def owner_ids(db, task):
    return list(db.scalars(select(TaskOwner.user_id).where(TaskOwner.task_id == task.id)))


def task_snapshot(db, task, *, owners=None):
    return {
        "id": task.id,
        "code": task.code,
        "content": task.content,
        "category": task.category,
        "requirements": task.requirements,
        "deliverable": task.deliverable,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "status": task.status,
        "version": task.version,
        "owner_ids": sorted(owner_ids(db, task) if owners is None else owners),
        "verifier_id": task.verifier_id,
        "closed_at": task.closed_at.isoformat() if task.closed_at else None,
        "recurrence_key": task.recurrence_key,
        "recurrence_period": task.recurrence_period.isoformat() if task.recurrence_period else None,
        "issuer_id": task.issuer_id,
        "withdrawn_at": task.withdrawn_at.isoformat() if task.withdrawn_at else None,
        "withdrawal_reason": task.withdrawal_reason,
    }


def current_status(task):
    if task.status in {"关闭", "已完成", "不统计"}:
        return task.status
    return "未完成" if task.due_date and task.due_date < today() else "未到期"


def review_projection(document):
    """Compare complete review records, including additions/removals and source identity."""
    rows = {}
    for raw in document.get("items", []):
        if raw["section"] != "review":
            continue
        item = MeetingItem.model_validate(raw).model_dump(mode="json")
        item["owner_ids"] = sorted(item["owner_ids"])
        rows[item["id"]] = item
    return rows


def assignment_projection(item, host_id):
    """Only these fields change a live task; display ordering and meeting notes do not."""
    fields = ("content", "category", "requirements", "deliverable", "due_date", "recurrence_key")
    return {
        **{key: item.get(key) for key in fields},
        "owner_ids": sorted(item.get("owner_ids", [])),
        "verifier_id": item.get("verifier_id") or host_id,
    }


def effective_completed_task_ids(db, identifiers):
    if not identifiers:
        return set()
    return set(
        db.scalars(
            select(Assessment.task_id)
            .join(Meeting, Meeting.id == Assessment.meeting_id)
            .where(
                Assessment.task_id.in_(identifiers),
                Assessment.meeting_version == Meeting.published_version,
            )
            .group_by(Assessment.task_id, Assessment.meeting_id, Assessment.meeting_version, Assessment.item_id)
            .having(func.count().filter(Assessment.status != "已完成") == 0)
        )
    )


def effective_terminal_review_task_ids(db, identifiers):
    """Published terminal reviews stop carry without changing the live Task.

    A host may close or exclude a review row without performing task completion
    verification. Only each meeting's current published version is effective,
    so correcting/removing that result can make the work eligible again.
    """
    if not identifiers:
        return set()
    return set(
        db.scalars(
            select(Assessment.task_id)
            .join(Meeting, Meeting.id == Assessment.meeting_id)
            .where(
                Assessment.task_id.in_(identifiers),
                Assessment.meeting_version == Meeting.published_version,
            )
            .group_by(Assessment.task_id, Assessment.meeting_id, Assessment.meeting_version, Assessment.item_id)
            .having(func.count().filter(Assessment.status.notin_({"已完成", "关闭", "不统计"})) == 0)
        )
    )


def can_confirm_in_minutes(db: Session, actor: User, draft: MeetingDraft, task: Task):
    return bool(
        task.source_meeting_id and not task.withdrawn_at and can_confirm_meeting_results(db, actor, draft)
    )


def confirm_in_minutes(db, actor, meeting, task, item, version):
    """An authorized completion result in published minutes is the decision."""
    if task.closed_at:
        return
    before = task_snapshot(db, task)
    confirmed_at = now()
    task.status, task.closed_at = "关闭", confirmed_at
    capacity = (
        "主持人"
        if actor.id == meeting.host_id
        else "编制人（会议任务单管理员）"
        if actor.id == meeting.recorder_id and not actor.is_admin
        else "授权管理员"
    )
    opinion = f"会议《{meeting.draft['title']}》中由{capacity}确认已完成"
    for request in db.scalars(
        select(CompletionRequest).where(
            CompletionRequest.task_id == task.id,
            CompletionRequest.state == "pending",
        )
    ):
        request.state = "approved" if request.task_version == task.version else "invalidated"
        request.opinion = opinion
        request.verifier_id, request.verified_at = actor.id, confirmed_at
    audit(
        db,
        actor,
        "task.meeting_completion",
        task,
        before=before,
        after={
            **task_snapshot(db, task),
            "meeting_id": meeting.id,
            "meeting_version": version,
            "item_id": item.id,
            "meeting_host_id": meeting.draft["host_id"],
        },
        reason=opinion,
    )
    enqueue_native_todo(db, task, "finish", key_suffix=f"meeting:{meeting.id}:v{version}")


def validate_draft(
    db: Session, draft: MeetingDraft, actor: User, historical=False, previous=None, notify=True
):
    from types import SimpleNamespace

    from app.services.historical_backfill import validate_mode
    from app.services.meeting_sheet_import import validate_confirmations

    validate_mode(db, actor, draft, SimpleNamespace(draft=previous, published_version=1) if previous else None)
    validate_confirmations(db, actor, draft)
    missing_results = [
        str(index)
        for index, item in enumerate((row for row in draft.items if row.section == "review"), start=1)
        if not item.status
    ]
    if missing_results:
        raise HTTPException(422, "请选择上期任务第 " + "、".join(missing_results) + " 项的完成结果")
    if draft.kind == "department" and not draft.department_id:
        raise HTTPException(422, "部门会议必须选择所属部门")
    if draft.department_id and not db.get(Department, draft.department_id):
        raise HTTPException(422, "所属部门不存在")
    if draft.series_id:
        series = db.get(MeetingSeries, draft.series_id)
        if not series:
            raise HTTPException(422, "会议系列不存在")
    previous = previous or {}
    previous_items = {item["id"]: item for item in previous.get("items", [])}

    def check_person(identifier, preserved=False):
        person = db.get(User, identifier)
        if not person or (not person.active and not preserved):
            raise HTTPException(422, "人员不存在或已停用，请重新选择；历史记录可保留原人员")

    for role in ("host_id", "recorder_id", "reviewer_id"):
        identifier = getattr(draft, role)
        if identifier:
            check_person(identifier, previous.get(role) == identifier)
    for identifier in draft.attendee_ids:
        check_person(identifier, identifier in previous.get("attendee_ids", []))
    for role in ("recipient_ids", "cc_ids"):
        for identifier in getattr(draft, role):
            check_person(identifier, (historical or not notify) and identifier in previous.get(role, []))
    for item in draft.items:
        if not item.content.strip() or not item.owner_ids:
            raise HTTPException(422, "请补全每条任务的内容和责任人")
        prior = previous_items.get(item.id)
        prior_postponement = prior.get("postponed_due_date") if prior else None
        same_postponement = bool(
            prior_postponement
            and item.postponed_due_date
            and prior_postponement == item.postponed_due_date.isoformat()
        )
        if prior_postponement and not item.postponed_due_date:
            raise HTTPException(422, "已发布的延期记录不能清除")
        if item.postponed_due_date:
            if item.section != "review" or (item.status != "未到期" and not same_postponement):
                raise HTTPException(422, "只有上期未到期任务可以延期")
            if not item.due_date or item.postponed_due_date <= item.due_date:
                raise HTTPException(422, "延期日期必须晚于原计划完成日期")
            if not same_postponement and item.postponed_due_date < draft.meeting_date:
                raise HTTPException(422, "延期日期不能早于会议日期")
        preserved_assignment = bool(
            prior
            and prior["section"] == item.section
            and prior.get("task_id") == item.task_id
            and assignment_projection(prior, previous.get("host_id"))
            == assignment_projection(item.model_dump(mode="json"), draft.host_id)
        )
        if not historical and item.section == "new" and not item.due_date and not preserved_assignment:
            raise HTTPException(422, "请填写每条本次安排的计划完成时间")
        prior_owners = (
            prior.get("owner_ids", []) if prior and prior["section"] == item.section == "review" else []
        )
        prior_verifier = (prior.get("verifier_id") or previous.get("host_id")) if prior else None
        task = None
        source_item = None
        # Historical minutes preserve original results even when dates conflict.
        if (
            not historical
            and item.section == "review"
            and item.status == "未到期"
            and item.due_date
            and draft.meeting_date > (item.postponed_due_date or item.due_date)
        ):
            raise HTTPException(422, "会议日期晚于计划完成日期，不能标为未到期")
        if (
            not historical
            and item.section == "review"
            and not item.task_id
            and item.source.get("sheet_import_id")
            and not item.historical_reference
        ):
            from app.services.meeting_sheet_import import review_source

            source_item = review_source(db, actor, draft, item)
        elif not historical and item.section == "review" and not item.task_id:
            reference = item.historical_reference
            if not reference or not item.historical_work_id:
                raise HTTPException(422, "在线上期回顾必须关联已有任务或已确认的历史事项")
            from app.services.historical_links import work_identity

            origin = db.get(Meeting, reference.meeting_id)
            if not origin or not origin.historical or origin.published_version < reference.version:
                raise HTTPException(422, "历史事项来源不存在，请重新带入")
            meeting_access(db, actor, origin)
            if origin.meeting_date > draft.meeting_date:
                raise HTTPException(422, "会议日期不能早于历史事项来源日期")
            origin_version = db.scalar(
                select(MeetingVersion).where(
                    MeetingVersion.meeting_id == origin.id,
                    MeetingVersion.number == reference.version,
                )
            )
            source_item = (
                next(
                    (
                        row
                        for row in origin_version.snapshot.get("items", [])
                        if row["id"] == reference.item_id
                    ),
                    None,
                )
                if origin_version
                else None
            )
            if not source_item or work_identity(origin.id, source_item) != item.historical_work_id:
                raise HTTPException(422, "历史事项来源与追溯编号不一致")
        if item.task_id:
            task = db.get(Task, item.task_id)
            if task is None:
                raise HTTPException(422, "关联任务不存在")
            task_access(db, actor, task)
            if (
                not historical
                and item.section == "review"
                and item.status in {"已完成", "部分完成"}
                and task.closed_at is None
                and not can_confirm_in_minutes(db, actor, draft, task)
            ):
                raise HTTPException(
                    422, "该任务需由会议主持人或管理员（含获授权的本场编制人）确认完成，或先通过任务验证"
                )
            if (
                not historical
                and item.section == "review"
                and item.status in {"已完成", "部分完成"}
                and task.closed_at
                and not can_confirm_in_minutes(db, actor, draft, task)
            ):
                closed = (
                    task.closed_at if task.closed_at.tzinfo else task.closed_at.replace(tzinfo=timezone.utc)
                )
                if draft.meeting_date < closed.astimezone(ZoneInfo("Asia/Shanghai")).date():
                    raise HTTPException(422, "会议日期早于任务验证关闭日期，不能提前记为已完成")
        if item.recurrence_key and (not task or task.recurrence_key != item.recurrence_key):
            raise HTTPException(422, "历史任务标记不能新增或修改，请刷新任务单")
        if prior and prior.get("historical_work_id") != item.historical_work_id:
            raise HTTPException(422, "已发布历史事项的追溯编号不能直接修改")
        review_owners = owner_ids(db, task) if task and item.section == "review" else []
        historical_owner_record = (
            item.historical_owner_record
            and item.section == "new"
            and not item.task_id
            and not item.historical_work_id
            and not prior
        )
        for identifier in item.owner_ids:
            check_person(
                identifier,
                historical_owner_record
                or (historical and draft.history_backfill)
                or preserved_assignment
                or identifier in prior_owners
                or identifier in review_owners
                or (
                    item.section == "review"
                    and source_item
                    and identifier in source_item.get("owner_ids", [])
                ),
            )
        verifier_id = item.verifier_id or draft.host_id
        verifier = db.get(User, verifier_id)
        if (
            not historical
            and item.section == "review"
            and verifier
            and not verifier.active
            and not (prior and prior.get("verifier_id") == verifier_id)
        ):
            raise HTTPException(422, f"验证人 {verifier.name} 已停用，请在该上期任务中更换验证人")
        check_person(
            verifier_id,
            preserved_assignment
            or (
                item.section == "review"
                and (verifier_id == prior_verifier or (task and verifier_id == task.verifier_id))
            ),
        )


def validate_series_kind(db: Session, draft: MeetingDraft, previous=None):
    """A configured series fixes new meetings' type; existing meetings keep theirs."""
    if not draft.series_id:
        return
    series = db.get(MeetingSeries, draft.series_id)
    if not series:
        raise HTTPException(422, "会议系列不存在")
    if series.kind not in {"department", "special"}:
        return  # Older series without an explicit type keep their existing behavior.
    previous = previous or {}
    expected = (previous.get("kind") if previous.get("series_id") == draft.series_id else None) or series.kind
    if draft.kind != expected:
        raise HTTPException(422, "会议类型由所属系列固定，不能单独修改；请选择匹配的会议系列")


def create_meeting(
    db: Session,
    actor: User,
    draft: MeetingDraft,
    historical=False,
    continuation_from=None,
    *,
    sheet_import=None,
):
    from app.services.historical_backfill import validate_mode

    validate_mode(db, actor, draft)
    historical = historical or draft.history_backfill
    if draft.sheet_import_id and (not sheet_import or draft.sheet_import_id != sheet_import.id):
        raise HTTPException(422, "请使用任务单导入入口创建会议")
    if draft.department_id and not db.get(Department, draft.department_id):
        raise HTTPException(422, "请选择有效的所属部门")
    validate_series_kind(db, draft)
    own = actor.id in {draft.host_id, draft.recorder_id}
    if not own:
        require_permission(
            db, actor, "meetings.import" if sheet_import else "meetings.create", draft.department_id
        )
    for identifier in {i.task_id for i in draft.items if i.task_id}:
        task = db.get(Task, identifier)
        if not task:
            raise HTTPException(422, "关联任务不存在")
        task_access(db, actor, task)
    # Allow incomplete drafts at the row level only via schema; publishing validates all foreign references.
    active_user(db, draft.host_id)
    if draft.recorder_id:
        active_user(db, draft.recorder_id)
    meeting = Meeting(
        title=draft.title,
        department_id=draft.department_id,
        series_id=draft.series_id,
        kind=draft.kind,
        meeting_date=draft.meeting_date,
        host_id=draft.host_id,
        recorder_id=draft.recorder_id,
        draft=draft.model_dump(mode="json"),
        historical=historical,
    )
    db.add(meeting)
    db.flush()
    audit(db, actor, "meeting.create", meeting, after=meeting.draft)
    return meeting


def save_meeting(db: Session, actor: User, meeting_id: str, draft: MeetingDraft, expected: int):
    if draft.department_id and not db.get(Department, draft.department_id):
        raise HTTPException(422, "请选择有效的所属部门")
    meeting = locked_get(db, Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(404, "会议不存在")
    meeting_access(db, actor, meeting, edit=True)
    if draft.sheet_import_id != meeting.draft.get("sheet_import_id"):
        raise HTTPException(422, "任务单导入来源不能修改或清除")
    for role in ("host_id", "recorder_id"):
        identifier = getattr(draft, role)
        if identifier and (not meeting.published_version or identifier != getattr(meeting, role)):
            active_user(db, identifier)
    if meeting.draft_version != expected:
        raise HTTPException(409, "会议已被其他人修改，请刷新")
    if not meeting.has_draft:
        raise HTTPException(409, "已发布，请先创建修订版本")
    from app.services.historical_backfill import validate_mode

    validate_mode(db, actor, draft, meeting)
    if not meeting.published_version:
        imported_history = meeting.historical and not meeting.draft.get("history_backfill")
        meeting.historical = bool(imported_history or draft.history_backfill)
    validate_series_kind(db, draft, {"series_id": meeting.series_id, "kind": meeting.kind})
    known = {i.get("task_id") for i in meeting.draft.get("items", []) if i.get("task_id")}
    for identifier in {i.task_id for i in draft.items if i.task_id} - known:
        task = db.get(Task, identifier)
        if not task:
            raise HTTPException(422, "关联任务不存在")
        task_access(db, actor, task)
    if draft.department_id != meeting.department_id:
        if not (
            actor.id in {meeting.host_id, meeting.recorder_id}
            or allows(db, actor, "meetings.edit", draft.department_id)
        ):
            raise HTTPException(403, "不能将会议移至无权管理的部门")
    if (
        not actor.is_admin
        and actor.id != meeting.host_id
        and (
            draft.host_id != meeting.draft.get("host_id", meeting.host_id)
            or draft.recorder_id != meeting.draft.get("recorder_id", meeting.recorder_id)
        )
    ):
        raise HTTPException(403, "只有会议主持人或管理员可以更换主持人和编制人")
    if not meeting_allowed(db, actor, meeting, "assess"):
        old_results = {
            i["id"]: i.get("status", "")
            for i in meeting.draft.get("items", [])
            if i.get("section") == "review"
        }
        new_results = {i.id: i.status for i in draft.items if i.section == "review"}
        if old_results != new_results:
            raise HTTPException(403, "没有修改任务完成结果的权限")
    from app.services.role_permissions import file_allowed

    prior_files = set(meeting.draft.get("attachment_ids", []))
    next_files = set(draft.attachment_ids)
    if prior_files - next_files and not file_allowed(db, actor, meeting, "delete"):
        raise HTTPException(403, "没有移除会议附件的权限")
    if next_files - prior_files and not file_allowed(db, actor, meeting, "upload"):
        raise HTTPException(403, "没有添加会议附件的权限")
    before = meeting.draft
    meeting.draft = draft.model_dump(mode="json")
    meeting.draft_version += 1
    if meeting.published_version == 0:
        meeting.title, meeting.meeting_date = draft.title, draft.meeting_date
        if meeting.host_id != draft.host_id:
            meeting.permission_generation += 1
        meeting.host_id, meeting.recorder_id = draft.host_id, draft.recorder_id
        meeting.department_id, meeting.series_id, meeting.kind = (
            draft.department_id,
            draft.series_id,
            draft.kind,
        )
    audit(db, actor, "meeting.save", meeting, before=before, after=meeting.draft)
    return meeting


def sync_meeting_task_scope(db: Session, actor: User, meeting: Meeting, department_id, series_id):
    """Move live source tasks together; published documents remain historical snapshots."""
    for task in db.scalars(
        select(Task).where(Task.source_meeting_id == meeting.id).order_by(Task.id).with_for_update()
    ):
        before = {"department_id": task.department_id, "series_id": task.series_id}
        after = {"department_id": department_id, "series_id": series_id}
        if before == after:
            continue
        task.department_id, task.series_id = department_id, series_id
        audit(db, actor, "task.scope.move", task, before=before, after=after, reason="同步会议归属")


def move_meeting_series(db: Session, actor: User, meeting_id: str, series_id: str, expected_version: int):
    meeting = locked_get(db, Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(404, "会议不存在")
    meeting_require(db, actor, meeting, "move")
    series = db.get(MeetingSeries, series_id)
    if series is None:
        raise HTTPException(422, "会议系列不存在")
    if meeting.draft_version != expected_version:
        raise HTTPException(409, "会议已被其他人修改，请刷新后重试")
    old_series_id = meeting.series_id
    if old_series_id == series_id:
        return meeting
    sync_meeting_task_scope(db, actor, meeting, meeting.department_id, series_id)
    meeting.series_id = series_id
    meeting.draft = {**meeting.draft, "series_id": series_id}
    if meeting.has_draft:
        meeting.draft_version += 1
    audit(
        db,
        actor,
        "meeting.series.move",
        meeting,
        before={"series_id": old_series_id},
        after={"series_id": series_id},
        reason="调整会议归档系列",
    )
    return meeting


def revision(db: Session, actor: User, meeting_id: str, reason: str):
    meeting = locked_get(db, Meeting, meeting_id)
    if not meeting:
        raise HTTPException(404, "会议不存在")
    meeting_require(db, actor, meeting, "revise")
    if not reason.strip():
        raise HTTPException(422, "请填写修订原因")
    if meeting.has_draft:
        raise HTTPException(409, "已经存在未发布草稿")
    meeting.has_draft = True
    meeting.draft_version += 1
    audit(db, actor, "meeting.revision", meeting, reason=reason)
    return meeting


def cancel_revision(db: Session, actor: User, meeting_id: str, expected: int):
    meeting = locked_get(db, Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(404, "会议不存在")
    meeting_require(db, actor, meeting, "cancel_revision")
    if meeting.draft_version != expected:
        raise HTTPException(409, "会议已被其他人修改，请刷新核对后再取消修订")
    if not meeting.published_version or not meeting.has_draft:
        raise HTTPException(409, "当前会议没有可取消的修订")
    version = db.scalar(
        select(MeetingVersion).where(
            MeetingVersion.meeting_id == meeting.id,
            MeetingVersion.number == meeting.published_version,
        )
    )
    if version is None:
        raise HTTPException(409, "已发布版本不存在，暂时无法取消修订")
    before = deepcopy(meeting.draft)
    restored = deepcopy(version.snapshot)
    # Display-name and archive changes take effect independently of publication.
    # Keep the effective meeting metadata while discarding only pending edits.
    for field in ("title", "department_id", "series_id", "kind", "host_id", "recorder_id"):
        restored[field] = getattr(meeting, field)
    restored["meeting_date"] = meeting.meeting_date.isoformat()
    meeting.draft = restored
    meeting.has_draft = False
    meeting.draft_version += 1
    audit(
        db,
        actor,
        "meeting.revision.cancel",
        meeting,
        before=before,
        after=restored,
        reason="放弃未发布的修订，恢复已发布会议内容",
    )
    return meeting


def change_task(
    db: Session,
    actor: User,
    task: Task,
    change: TaskChange,
    source_meeting_id=None,
    notify=True,
    notify_task_message=True,
):
    if task.source_meeting_id:
        meeting_access(db, actor, db.get(Meeting, task.source_meeting_id), edit=True)
    elif not can_manage_direct_task(db, actor, task):
        raise HTTPException(403, "只有管理员或当前有派发权限的派发人可以修改此任务")
    if task.source_meeting_id is None:
        allowed = {person.id for person in direct_assignees(db, actor, "edit")}
        if not set(change.owner_ids) <= allowed or change.verifier_id not in allowed | {actor.id}:
            raise HTTPException(403, "责任人或验证人不在可派发范围")
    if task.version != change.expected_version:
        raise HTTPException(409, "任务版本已变化，请刷新")
    if task.closed_at:
        raise HTTPException(409, "任务已关闭，需要新建后续任务")
    if not change.due_date:
        raise HTTPException(422, "请填写计划完成时间")
    for identifier in [*change.owner_ids, change.verifier_id]:
        active_user(db, identifier)
    before = task_snapshot(db, task)
    for key in ["content", "category", "requirements", "deliverable", "due_date", "verifier_id"]:
        setattr(task, key, getattr(change, key))
    task.version += 1
    task.status = current_status(task)
    db.execute(delete(TaskOwner).where(TaskOwner.task_id == task.id))
    for identifier in set(change.owner_ids):
        db.add(TaskOwner(task_id=task.id, user_id=identifier))
    for request in db.scalars(
        select(CompletionRequest).where(
            CompletionRequest.task_id == task.id, CompletionRequest.state == "pending"
        )
    ):
        request.state = "invalidated"
        request.opinion = "任务要求、责任人或期限更新，原申请失效，请重新申请"
    db.flush()
    snapshot = task_snapshot(db, task)
    db.add(
        TaskVersion(
            task_id=task.id,
            number=task.version,
            snapshot=snapshot,
            reason=change.reason,
            actor_id=actor.id,
            source_meeting_id=source_meeting_id,
        )
    )
    audit(db, actor, "task.change", task, before=before, after=snapshot, reason=change.reason)
    if notify and notify_task_message:
        enqueue(
            db,
            f"task:{task.id}:v{task.version}:changed",
            "wecom",
            {
                "task_id": task.id,
                "task_version": task.version,
                "user_ids": sorted(set(change.owner_ids)),
                "title": "任务安排已更新",
                "content": task.content,
            },
        )
    if notify:
        enqueue_native_todo(db, task, "update")
        from app.services.app_native_todo import enqueue_issuer_confirmation

        enqueue_issuer_confirmation(db, task)
    return task


def postpone_review_task(db, actor, task, due_date, meeting, notify, shared_recipients):
    """Publish a deadline-only change under the current meeting's edit permission."""
    before = task_snapshot(db, task)
    task.due_date = due_date
    task.version += 1
    task.status = current_status(task)
    for request in db.scalars(
        select(CompletionRequest).where(
            CompletionRequest.task_id == task.id,
            CompletionRequest.state == "pending",
        )
    ):
        request.state = "invalidated"
        request.opinion = "会议延期更新了任务期限，请按最新日期重新申请"
    db.flush()
    snapshot = task_snapshot(db, task)
    db.add(
        TaskVersion(
            task_id=task.id,
            number=task.version,
            snapshot=snapshot,
            reason="会议未到期任务延期",
            actor_id=actor.id,
            source_meeting_id=meeting.id,
        )
    )
    audit(db, actor, "task.change", task, before=before, after=snapshot, reason="会议未到期任务延期")
    if notify:
        active_owners = [identifier for identifier in owner_ids(db, task) if db.get(User, identifier).active]
        if not shared_recipients and active_owners:
            enqueue(
                db,
                f"task:{task.id}:v{task.version}:changed",
                "wecom",
                {
                    "task_id": task.id,
                    "task_version": task.version,
                    "user_ids": active_owners,
                    "title": "任务期限已延期",
                    "content": task.content,
                },
            )
        enqueue_native_todo(db, task, "update")


def publish(db: Session, actor: User, meeting_id: str, command: PublishRequest):
    meeting = locked_get(db, Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(404, "会议不存在")
    meeting_require(db, actor, meeting, "publish")
    if meeting.draft_version != command.expected_version:
        raise HTTPException(409, "草稿已变化，请重新预览")
    if not meeting.has_draft:
        return meeting  # Already committed this exact draft.
    # Lock dependent drafts before task rows. Saving/publishing the next meeting
    # must either finish first or observe its incremented draft version.
    followup_targets = lock_continuation_drafts(db, meeting) if meeting.published_version else []
    if meeting.published_version and not command.reason.strip():
        raise HTTPException(422, "修订必须说明原因")
    draft = MeetingDraft.model_validate(meeting.draft)
    validate_series_kind(db, draft, {"series_id": meeting.series_id, "kind": meeting.kind})
    if command.shared_recipients:
        draft.recipient_ids = list(dict.fromkeys(draft.recipient_ids))
        draft.cc_ids = list(
            dict.fromkeys(identifier for identifier in draft.cc_ids if identifier not in draft.recipient_ids)
        )
    previous_version = db.scalar(
        select(MeetingVersion).where(
            MeetingVersion.meeting_id == meeting.id, MeetingVersion.number == meeting.published_version
        )
    )
    validate_draft(
        db,
        draft,
        actor,
        meeting.historical,
        previous=previous_version.snapshot if previous_version else None,
        notify=command.notify,
    )
    if command.shared_recipients and command.notify and not meeting.historical:
        if not draft.recipient_ids:
            raise HTTPException(422, "请选择至少一位通知接收人，或取消发送通知后仅发布")
        related = {draft.host_id, draft.recorder_id, draft.reviewer_id, *draft.attendee_ids}
        for item in draft.items:
            related.update([*item.owner_ids, item.verifier_id or draft.host_id])
        for identifier in draft.recipient_ids + draft.cc_ids:
            person = db.get(User, identifier)
            if (
                identifier in draft.recipient_ids
                and identifier not in related
                and (not draft.department_id or draft.department_id not in department_ids(db, person))
                and not person.is_admin
            ):
                raise HTTPException(403, f"{person.name} 无权查看该会议，请调整通知名单")
            missing = []
            if not person.email:
                missing.append("邮箱")
            if identifier in draft.recipient_ids and not person.wecom_id:
                missing.append("企业微信绑定")
            if missing:
                raise HTTPException(422, f"{person.name} 尚未配置{'和'.join(missing)}，请完善后再发送通知")
    previous_items = {i["id"]: i for i in previous_version.snapshot["items"]} if previous_version else {}
    if previous_version:
        previous_review = review_projection(previous_version.snapshot)
        next_review = review_projection(draft.model_dump(mode="json"))
        # A recorder may correct department attribution while preserving results.
        # Changes to dates, meeting type or assessments still require correction rights.
        scope_changed = any(
            previous_version.snapshot.get(key) != draft.model_dump(mode="json").get(key)
            for key in ("meeting_date", "department_id", "kind")
            if key != "department_id" or actor.id != meeting.recorder_id
        )
        if (
            previous_review != next_review
            or ((previous_review or next_review) and scope_changed)
            or draft.host_id != meeting.host_id
        ):
            meeting_access(db, actor, meeting, correction=True)
    previous_assessments = {
        (a.item_id, a.user_id): a
        for a in db.scalars(
            select(Assessment).where(
                Assessment.meeting_id == meeting.id, Assessment.meeting_version == meeting.published_version
            )
        )
    }
    linked_ids = sorted(
        {item.task_id for item in draft.items if item.task_id}
        | {
            item["task_id"]
            for item in previous_items.values()
            if item.get("task_id") and item["section"] == "review"
        }
    )
    new_links = [item.task_id for item in draft.items if item.section == "new" and item.task_id]
    if len(new_links) != len(set(new_links)):
        raise HTTPException(422, "同一任务在本次安排中只能更新一次")
    locked_tasks = {identifier: locked_get(db, Task, identifier) for identifier in linked_ids}
    frozen_assignments = set()
    frozen_postponements = set()

    def preserved_postponement(item):
        prior = previous_items.get(item.id)
        return bool(
            prior
            and prior["section"] == "review"
            and prior.get("task_id") == item.task_id
            and prior.get("task_version") == item.task_version
            and prior.get("postponed_due_date")
            == (item.postponed_due_date.isoformat() if item.postponed_due_date else None)
        )

    postponement_links = [
        item.task_id
        for item in draft.items
        if item.section == "review"
        and item.postponed_due_date
        and item.task_id
        and not preserved_postponement(item)
    ]
    review_links = [item.task_id for item in draft.items if item.section == "review" and item.task_id]
    if any(review_links.count(identifier) > 1 for identifier in postponement_links) or set(
        postponement_links
    ) & set(new_links):
        raise HTTPException(422, "同一任务不能重复延期或同时在本次安排中更新")
    for item in draft.items:
        previous = previous_items.get(item.id)
        if previous and previous["section"] == "new" and previous.get("task_id"):
            if item.section != "new" or item.task_id != previous["task_id"]:
                raise HTTPException(422, "已发布工作行的任务关联不能清除或替换，请保留原记录并新增后续安排")
            if item.task_version == previous.get("task_version") and assignment_projection(
                item.model_dump(mode="json"), draft.host_id
            ) == assignment_projection(previous, previous_version.snapshot["host_id"]):
                frozen_assignments.add(item.id)
        frozen_review = (
            item.section == "review"
            and previous
            and previous.get("task_id") == item.task_id
            and previous.get("task_version") == item.task_version
        )
        if preserved_postponement(item):
            frozen_postponements.add(item.id)
        if item.postponed_due_date and item.id not in frozen_postponements and item.task_id:
            task = locked_tasks[item.task_id]
            if item.task_version != task.version:
                raise HTTPException(409, "关联任务发生变化，请重新带入并核对延期日期")
            if task.closed_at or task.status in {"已完成", "关闭", "不统计"}:
                raise HTTPException(409, "任务已结束，不能延期")
            if task.due_date and item.postponed_due_date <= task.due_date:
                raise HTTPException(422, "延期日期必须晚于任务的最新计划完成日期")
        if (
            item.task_id
            and not frozen_review
            and item.id not in frozen_assignments
            and item.task_version != locked_tasks[item.task_id].version
        ):
            raise HTTPException(409, "关联任务发生变化，请重新带入并核对")
        if item.task_id and item.section == "review" and not meeting.historical:
            task = locked_tasks[item.task_id]
            next_verifier = item.verifier_id or draft.host_id
            published_verifier = (
                (previous.get("verifier_id") or previous_version.snapshot["host_id"]) if previous else None
            )
            if (
                next_verifier != task.verifier_id
                and next_verifier != published_verifier
                and not db.get(User, task.verifier_id).active
            ):
                if item.task_version != task.version:
                    raise HTTPException(409, "关联任务发生变化，请重新带入并核对验证人")
                linked_arrangement = next(
                    (row for row in draft.items if row.section == "new" and row.task_id == task.id), None
                )
                if linked_arrangement and (linked_arrangement.verifier_id or draft.host_id) != next_verifier:
                    raise HTTPException(422, "同一任务在上期回顾与本次安排中的新验证人必须一致")
            expected_owners = previous["owner_ids"] if frozen_review else owner_ids(db, task)
            expected_due = (
                date.fromisoformat(previous["due_date"])
                if frozen_review and previous.get("due_date")
                else task.due_date
            )
            if set(item.owner_ids) != set(expected_owners) or item.due_date != expected_due:
                raise HTTPException(
                    422,
                    "上期回顾的责任人与原期限必须与关联任务一致；未到期任务请使用延期，未完成任务请使用延续安排",
                )
        if previous and item.section == "review" and previous.get("status") != item.status:
            meeting_access(db, actor, meeting, correction=True)
    for identifier in draft.attachment_ids:
        attachment = db.get(Attachment, identifier)
        if not attachment or (
            attachment.uploader_id != actor.id and attachment.meeting_id != meeting.id and not actor.is_admin
        ):
            raise HTTPException(403, "无权使用该附件")
        if attachment.meeting_id and attachment.meeting_id != meeting.id:
            raise HTTPException(422, "附件已关联其他会议")
    number = meeting.published_version + 1
    snapshot = draft.model_dump(mode="json")
    if command.shared_recipients:
        snapshot["notification_recipient_ids"] = list(draft.recipient_ids)
        snapshot["notification_cc_ids"] = list(draft.cc_ids)
        snapshot["notification_cc_emails"] = list(draft.cc_emails)
    snapshot["department_name"] = department_path(db, draft.department_id)
    snapshot["host_name"] = db.get(User, draft.host_id).name
    snapshot["recorder_name"] = db.get(User, draft.recorder_id).name if draft.recorder_id else ""
    snapshot["reviewer_name"] = db.get(User, draft.reviewer_id).name if draft.reviewer_id else ""
    snapshot["attendee_names"] = [db.get(User, x).name for x in draft.attendee_ids]
    snapshot["online_attendee_names"] = [db.get(User, x).name for x in draft.online_attendee_ids]
    for i, item in enumerate(draft.items):
        target = snapshot["items"][i]
        target["owner_names"] = [db.get(User, x).name for x in item.owner_ids]
        target["verifier_name"] = db.get(User, item.verifier_id or draft.host_id).name
        task = locked_tasks.get(item.task_id)
        if item.section == "new" and not meeting.historical:
            if item.id in frozen_assignments:
                # A revision of old minutes is not a new instruction to the current task.
                # Keep its original task version even when the live task has since changed/closed.
                continue
            if task:
                candidate = TaskChange(
                    expected_version=item.task_version,
                    content=item.content,
                    category=item.category,
                    requirements=item.requirements,
                    deliverable=item.deliverable,
                    due_date=item.due_date,
                    owner_ids=item.owner_ids,
                    verifier_id=item.verifier_id or draft.host_id,
                    reason=item.change_reason or (
                        "上期未完成事项延续安排"
                        if is_unfinished_extension(target, snapshot["items"])
                        else command.reason or "会议任务安排更新"
                    ),
                )
                before = task_snapshot(db, task)
                if any(before[k] != v for k, v in candidate.model_dump(mode="json").items() if k in before):
                    change_task(
                        db,
                        actor,
                        task,
                        candidate,
                        meeting.id,
                        notify=command.notify,
                        notify_task_message=not command.shared_recipients,
                    )
            else:
                task = db.scalar(
                    select(Task).where(Task.source_meeting_id == meeting.id, Task.source_item_id == item.id)
                )
                if task:
                    # Published item revised in place; it is still the same task.
                    candidate = TaskChange(
                        expected_version=task.version,
                        content=item.content,
                        category=item.category,
                        requirements=item.requirements,
                        deliverable=item.deliverable,
                        due_date=item.due_date,
                        owner_ids=item.owner_ids,
                        verifier_id=item.verifier_id or draft.host_id,
                        reason=command.reason,
                    )
                    before = task_snapshot(db, task)
                    if any(
                        before[k] != v for k, v in candidate.model_dump(mode="json").items() if k in before
                    ):
                        change_task(
                            db,
                            actor,
                            task,
                            candidate,
                            meeting.id,
                            notify=command.notify,
                            notify_task_message=not command.shared_recipients,
                        )
                else:
                    task = Task(
                        code="RW-" + uid().replace("-", "")[:12].upper(),
                        content=item.content,
                        category=item.category,
                        requirements=item.requirements,
                        deliverable=item.deliverable,
                        department_id=draft.department_id,
                        source_meeting_id=meeting.id,
                        source_item_id=item.id,
                        historical_work_id=item.historical_work_id,
                        series_id=draft.series_id,
                        due_date=item.due_date,
                        verifier_id=item.verifier_id or draft.host_id,
                        status="未完成" if item.due_date and item.due_date < today() else "未到期",
                        recurrence_key=item.recurrence_key,
                    )
                    db.add(task)
                    db.flush()
                    for identifier in item.owner_ids:
                        db.add(TaskOwner(task_id=task.id, user_id=identifier))
                    db.flush()
                    db.add(
                        TaskVersion(
                            task_id=task.id,
                            number=1,
                            snapshot=task_snapshot(db, task),
                            reason="历史任务补录" if item.historical_owner_record else "会议发布",
                            actor_id=actor.id,
                            source_meeting_id=meeting.id,
                        )
                    )
                    active_owners = [
                        identifier for identifier in item.owner_ids if db.get(User, identifier).active
                    ]
                    if command.notify and not command.shared_recipients and active_owners:
                        enqueue(
                            db,
                            f"task:{task.id}:assigned",
                            "wecom",
                            {
                                "task_id": task.id,
                                "task_version": task.version,
                                "user_ids": active_owners,
                                "title": "新的会议任务",
                                "content": task.content,
                            },
                        )
                    if command.notify:
                        enqueue_native_todo(db, task, "create")
            target["task_id"], target["task_version"] = task.id, task.version
        elif item.section == "review":
            prior_item = previous_items.get(item.id)
            next_verifier = item.verifier_id or draft.host_id
            if (
                task
                and not meeting.historical
                and next_verifier != task.verifier_id
                and not (
                    prior_item
                    and (prior_item.get("verifier_id") or previous_version.snapshot["host_id"])
                    == next_verifier
                )
                and task.id not in new_links
                and not db.get(User, task.verifier_id).active
            ):
                before = task_snapshot(db, task)
                task.verifier_id = next_verifier
                task.version += 1
                for request in db.scalars(
                    select(CompletionRequest).where(
                        CompletionRequest.task_id == task.id,
                        CompletionRequest.state == "pending",
                    )
                ):
                    request.state, request.opinion = (
                        "invalidated",
                        "原验证人已停用，会议已更换验证人，请重新申请",
                    )
                db.flush()
                changed = task_snapshot(db, task)
                db.add(
                    TaskVersion(
                        task_id=task.id,
                        number=task.version,
                        snapshot=changed,
                        reason="会议替换已停用验证人",
                        actor_id=actor.id,
                        source_meeting_id=meeting.id,
                    )
                )
                audit(
                    db,
                    actor,
                    "task.change",
                    task,
                    before=before,
                    after=changed,
                    reason="会议替换已停用验证人",
                )
                target["task_version"] = task.version
            if (
                task
                and not meeting.historical
                and item.postponed_due_date
                and item.id not in frozen_postponements
            ):
                postpone_review_task(
                    db,
                    actor,
                    task,
                    item.postponed_due_date,
                    meeting,
                    command.notify,
                    command.shared_recipients,
                )
                target["task_version"] = task.version
            for identifier in item.owner_ids:
                person = db.get(User, identifier)
                prior = previous_assessments.get((item.id, identifier))
                db.add(
                    Assessment(
                        meeting_id=meeting.id,
                        meeting_version=number,
                        item_id=item.id,
                        task_id=item.task_id,
                        user_id=identifier,
                        person_department_id=prior.person_department_id if prior else person.department_id,
                        user_name=prior.user_name if prior else person.name,
                        status=person_result(target, identifier),
                        content=item.content,
                        due_date=item.due_date,
                        notes=item.notes,
                    )
                )
    if not meeting.historical:
        completed = set()
        for item in draft.items:
            task = locked_tasks.get(item.task_id)
            if (
                task
                and item.section == "review"
                and item.status == "已完成"
                and task.id not in completed
                and can_confirm_in_minutes(db, actor, draft, task)
            ):
                confirm_in_minutes(db, actor, meeting, task, item, number)
                completed.add(task.id)
    if meeting.department_id != draft.department_id or meeting.series_id != draft.series_id:
        sync_meeting_task_scope(db, actor, meeting, draft.department_id, draft.series_id)
    meeting.title, meeting.meeting_date = draft.title, draft.meeting_date
    meeting.department_id, meeting.series_id, meeting.kind = draft.department_id, draft.series_id, draft.kind
    if meeting.host_id != draft.host_id:
        meeting.permission_generation += 1
    meeting.host_id, meeting.recorder_id = draft.host_id, draft.recorder_id
    db.execute(delete(MeetingAttendee).where(MeetingAttendee.meeting_id == meeting.id))
    related_ids = set(draft.attendee_ids)
    if draft.reviewer_id:
        related_ids.add(draft.reviewer_id)
    for item in draft.items:
        related_ids.update([*item.owner_ids, item.verifier_id or draft.host_id])
    for identifier in related_ids - {None}:
        db.add(MeetingAttendee(meeting_id=meeting.id, user_id=identifier))
    for identifier in draft.attachment_ids:
        db.get(Attachment, identifier).meeting_id = meeting.id
    db.add(
        MeetingVersion(
            meeting_id=meeting.id,
            number=number,
            snapshot=snapshot,
            publisher_id=actor.id,
            reason=command.reason,
        )
    )
    meeting.published_version, meeting.has_draft = number, False
    # The working document now retains assigned task IDs for subsequent revisions.
    meeting.draft = snapshot
    enqueue(
        db, f"meeting:{meeting.id}:v{number}:export", "export", {"meeting_id": meeting.id, "version": number}
    )
    responsible_ids = (
        list(draft.recipient_ids)
        if command.shared_recipients
        else sorted(
            {
                identifier
                for item in snapshot["items"]
                for identifier in item["owner_ids"]
                if db.get(User, identifier).active
            }
        )
    )
    if not meeting.historical and command.notify and responsible_ids:
        enqueue(
            db,
            f"meeting:{meeting.id}:v{number}:wecom",
            "wecom",
            {
                "meeting_publication": True,
                "meeting_id": meeting.id,
                "version": number,
                "user_ids": responsible_ids,
                **({"shared_recipients": True} if command.shared_recipients else {}),
            },
        )
    if not meeting.historical and command.notify and (draft.recipient_ids or draft.cc_ids or draft.cc_emails):
        related = {draft.host_id, *draft.attendee_ids}
        if draft.recorder_id:
            related.add(draft.recorder_id)
        if draft.reviewer_id:
            related.add(draft.reviewer_id)
        for item in draft.items:
            related.update([*item.owner_ids, item.verifier_id or draft.host_id])
        for identifier in set(draft.recipient_ids + draft.cc_ids):
            person = db.get(User, identifier)
            if (
                identifier in draft.recipient_ids
                and identifier not in related
                and (not draft.department_id or draft.department_id not in department_ids(db, person))
                and not person.is_admin
            ):
                raise HTTPException(403, "邮件收件人无权查看该会议，请调整名单")
            if not person.email:
                raise HTTPException(422, f"{person.name} 尚未配置邮箱")
        enqueue(
            db,
            f"meeting:{meeting.id}:v{number}:mail",
            "email",
            {
                "meeting_id": meeting.id,
                "version": number,
                "recipient_ids": draft.recipient_ids,
                "cc_ids": draft.cc_ids,
                "cc_emails": list(draft.cc_emails),
                **({"shared_recipients": True} if command.shared_recipients else {}),
            },
        )
    from app.services.operation_logs import publication_plan

    audit(
        db,
        actor,
        "meeting.publish",
        meeting,
        after={
            "version": number,
            "notify": command.notify,
            "recipient_ids": draft.recipient_ids,
            "cc_ids": draft.cc_ids,
            "cc_emails": list(draft.cc_emails),
            **({"shared_recipients": True} if command.shared_recipients else {}),
        },
        reason=command.reason,
        context={"notification_plan": publication_plan(db, meeting.id, number)},
    )
    db.flush()
    completed = effective_completed_task_ids(db, linked_ids)
    for identifier, task in locked_tasks.items():
        task.completion_reviewed = identifier in completed
    db.info["continuation_sync_result"] = sync_continuation_drafts(db, actor, meeting, followup_targets)
    from app.services.historical_supplement import supplement_next_meeting

    db.info["history_supplement_result"] = supplement_next_meeting(db, actor, meeting)
    return meeting


def apply_completion(db: Session, actor: User, task_id: str, command: ApplyCompletion):
    task = locked_get(db, Task, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if actor.id not in owner_ids(db, task):
        raise HTTPException(403, "只有共同责任人可以申请完成")
    old = db.scalar(
        select(CompletionRequest).where(CompletionRequest.idempotency_key == command.idempotency_key)
    )
    if old:
        if old.task_id != task.id or old.applicant_id != actor.id:
            raise HTTPException(409, "申请标识已使用")
        return old
    if task.closed_at:
        raise HTTPException(409, "任务已关闭")
    if task.version != command.expected_version:
        raise HTTPException(409, "任务版本已变化")
    if db.scalar(
        select(CompletionRequest.id).where(
            CompletionRequest.task_id == task.id, CompletionRequest.state == "pending"
        )
    ):
        raise HTTPException(409, "共同责任人已提交申请，请等待验证")
    for identifier in command.evidence_ids:
        evidence = db.get(Attachment, identifier)
        if (
            not evidence
            or evidence.uploader_id != actor.id
            or evidence.kind != "evidence"
            or evidence.meeting_id is not None
            or (evidence.task_id and evidence.task_id != task.id)
        ):
            raise HTTPException(403, "不能使用该完成证据")
        evidence.task_id = task.id
    request = CompletionRequest(
        task_id=task.id,
        task_version=task.version,
        applicant_id=actor.id,
        idempotency_key=command.idempotency_key,
        explanation=command.explanation,
        evidence_ids=command.evidence_ids,
    )
    db.add(request)
    db.flush()
    enqueue(
        db,
        f"request:{request.id}:verify",
        "wecom",
        {
            "task_id": task.id,
            "task_version": task.version,
            "request_id": request.id,
            "user_ids": [task.verifier_id],
            "title": "请验证任务完成情况",
            "content": task.content,
        },
    )
    audit(db, actor, "task.completion_request", task, after={"request_id": request.id})
    return request


def verify_completion(db: Session, actor: User, request_id: str, command: VerifyCompletion):
    request = db.get(CompletionRequest, request_id)
    if not request:
        raise HTTPException(404, "完成申请不存在")
    task = locked_get(db, Task, request.task_id)
    db.refresh(request)
    if actor.id != task.verifier_id:
        raise HTTPException(403, "只有指定验证人可以验证关闭")
    if request.state in {"approved", "rejected"}:
        if request.state == command.decision:
            return request
        raise HTTPException(409, "申请已处理，不能重复变更")
    if request.state != "pending" or request.task_version != task.version:
        raise HTTPException(409, "申请已失效，请责任人重新申请")
    request.state, request.opinion = command.decision, command.opinion
    request.verifier_id, request.verified_at = actor.id, now()
    if command.decision == "approved":
        task.status, task.closed_at = "关闭", now()
        enqueue_native_todo(db, task, "finish", key_suffix=request.id)
    else:
        task.status = current_status(task)
    enqueue(
        db,
        f"request:{request.id}:result",
        "wecom",
        {
            "task_id": task.id,
            "task_version": task.version,
            "user_ids": owner_ids(db, task),
            "title": "任务验证通过，已关闭" if command.decision == "approved" else "完成申请被驳回",
            "content": command.opinion,
            "allow_closed": True,
        },
    )
    audit(
        db,
        actor,
        "task.verify",
        task,
        after={"request_id": request.id, "decision": request.state},
        reason=request.opinion,
    )
    return request


def superseded_review_task_ids(db: Session, source: Meeting, snapshot: dict) -> set[str]:
    """Prefer an unambiguous replacement arrangement in the same published minutes.

    Legacy minutes could create a second Task when an unfinished review was
    copied into the new arrangements. This is a document carry rule only: it
    never merges Task identities, changes completion, or touches native todos.
    A title match alone, or two independent new rows, is insufficient.
    """
    if source.historical:
        return set()
    from app.services.task_continuations import confirmed_superseded_task_ids

    confirmed = confirmed_superseded_task_ids(db, source, snapshot)
    groups = {}
    for row in snapshot.get("items", []):
        if not row.get("task_id") or row.get("recurrence_key"):
            continue
        content = row.get("content", "").strip()
        owners = tuple(sorted(set(row.get("owner_ids", []))))
        if not content or not owners:
            continue
        key = (
            content,
            owners,
            row.get("category", ""),
            row.get("requirements", ""),
            row.get("deliverable", ""),
            row.get("verifier_id") or snapshot.get("host_id") or source.host_id,
        )
        groups.setdefault(key, {"review": [], "new": []})[row["section"]].append(row)
    pairs = [
        (rows["review"][0], rows["new"][0])
        for rows in groups.values()
        if len(rows["review"]) == len(rows["new"]) == 1
    ]
    if not pairs:
        return confirmed
    identifiers = {row["task_id"] for pair in pairs for row in pair}
    tasks = {task.id: task for task in db.scalars(select(Task).where(Task.id.in_(identifiers)))}
    suppressed = set(confirmed)
    for previous, arrangement in pairs:
        old, new = tasks.get(previous["task_id"]), tasks.get(arrangement["task_id"])
        if (
            old
            and new
            and old.id != new.id
            and previous.get("status") not in {"已完成", "关闭", "不统计"}
            and old.source_meeting_id
            and old.source_meeting_id != source.id
            and new.source_meeting_id == source.id
            and old.version == previous.get("task_version")
            and old.series_id == new.series_id == source.series_id
            and not old.recurrence_key
            and not new.recurrence_key
            and not old.recurrence_period
            and not new.recurrence_period
            and not new.withdrawn_at
        ):
            suppressed.add(old.id)
    return suppressed


def automatically_carried_row(row):
    """An assessed failure needs an explicit new arrangement to continue."""
    return not row.get("backfill_reference") and row.get("status") not in {"已完成", "关闭", "不统计"} and not (
        row.get("section") == "review" and row.get("status") in {"未完成", "部分完成"}
    )


def reviewed_unfinished_task_ids(db, identifiers, *, meeting_date=None, exclude_meeting_id=None):
    """Use the latest released occurrence, with an explicit new arrangement taking precedence.

    Failed review records are historical assessments, not renewed assignments.
    Include arrangement meetings as well as assessment meetings so reissuing the
    same task makes it eligible without changing its old assessment.
    """
    if not identifiers:
        return set()
    query = select(Meeting, MeetingVersion).join(MeetingVersion, (
        (MeetingVersion.meeting_id == Meeting.id) & (MeetingVersion.number == Meeting.published_version)
    )).where(Meeting.id.in_(
        select(Assessment.meeting_id).where(Assessment.task_id.in_(identifiers))
    ) | Meeting.id.in_(
        select(TaskVersion.source_meeting_id).where(TaskVersion.task_id.in_(identifiers))
    ))
    if meeting_date:
        query = query.where(Meeting.meeting_date <= meeting_date)
    if exclude_meeting_id:
        query = query.where(Meeting.id != exclude_meeting_id)
    seen, failed = set(), set()
    for source, version in db.execute(query.order_by(
        Meeting.meeting_date.desc(), Meeting.created_at.desc(), Meeting.id.desc()
    )):
        for row in sorted(version.snapshot.get("items", []), key=lambda row: row["section"] != "new"):
            identifier = row.get("task_id")
            if identifier not in identifiers or identifier in seen:
                continue
            seen.add(identifier)
            if row.get("section") == "review" and row.get("status") in {"未完成", "部分完成"}:
                failed.add(identifier)
    return failed


def noncarried_historical_work_ids(db, series_id):
    """Do not resurrect an older import after a later released assessment."""
    from app.services.historical_links import work_identity

    if not series_id:
        return set()
    versions = db.execute(select(Meeting, MeetingVersion).join(MeetingVersion, (
        (MeetingVersion.meeting_id == Meeting.id) & (MeetingVersion.number == Meeting.published_version)
    )).where(Meeting.series_id == series_id).order_by(
        Meeting.meeting_date.desc(), Meeting.created_at.desc(), Meeting.id.desc(),
    ))
    seen, stopped = set(), set()
    for source, version in versions:
        for row in sorted(version.snapshot.get("items", []), key=lambda row: row["section"] != "new"):
            key = work_identity(source.id, row) if source.historical else row.get("historical_work_id")
            if not key or key in seen:
                continue
            seen.add(key)
            if not automatically_carried_row(row):
                stopped.add(key)
    return stopped


def continuation_result(row, due_date, meeting_date, *, closed=False):
    """Only a still-valid not-due result can be reused in the next review."""
    return "未到期" if row.get("status") == "未到期" and due_date and due_date >= meeting_date and not closed else ""


def continuation_items(db, actor, source, version, host_id, *, include_terminal=False, meeting_date=None):
    """Build inherited review rows without creating a meeting or changing tasks."""
    from app.services.historical_links import work_identity
    from app.services.meeting_items import continuation_origin

    snapshot = version.snapshot
    meeting_date = meeting_date or today()
    seen = set()
    items = []
    positions = {}
    source_order = {row["id"]: index for index, row in enumerate(continuation_order(snapshot.get("items", [])))}
    # An arrangement in the last minutes supersedes its earlier review row.
    candidates = sorted(snapshot.get("items", []), key=lambda row: row["section"] != "new")
    superseded = superseded_review_task_ids(db, source, snapshot)
    identifiers = {row["task_id"] for row in candidates if row.get("task_id")}
    failed_reviews = reviewed_unfinished_task_ids(db, identifiers) if not include_terminal else set()
    terminal_reviews = effective_terminal_review_task_ids(db, identifiers) if not include_terminal else set()
    stopped_history = noncarried_historical_work_ids(db, source.series_id) if not include_terminal else set()
    for row in candidates:
        if row.get("backfill_reference"):
            continue
        if row.get("task_id") in superseded:
            continue
        if not include_terminal and not automatically_carried_row(row):
            continue
        task = db.get(Task, row["task_id"]) if row.get("task_id") else None
        identity = (
            work_identity(source.id, row)
            if source.historical
            else row.get("historical_work_id") or row.get("task_id") or row["id"]
        )
        if identity in seen:
            continue
        seen.add(identity)
        if not task and row.get("historical_work_id"):
            task = db.scalar(select(Task).where(Task.historical_work_id == row["historical_work_id"]))
        if not task and identity in stopped_history:
            continue
        if task:
            if task.source_meeting_id != source.id:
                task_access(db, actor, task)
            if task.id in failed_reviews or task.id in terminal_reviews or not owner_ids(db, task):
                continue
            owners = owner_ids(db, task)
            fields = {
                "content": task.content,
                "category": task.category,
                "requirements": task.requirements,
                "deliverable": task.deliverable,
                "owner_ids": owners,
                "due_date": task.due_date,
                "status": continuation_result(row, task.due_date, meeting_date, closed=bool(task.closed_at)),
                "verifier_id": task.verifier_id,
                "task_id": task.id,
                "task_version": task.version,
                "recurrence_key": task.recurrence_key,
            }
            reference = None
            historical_work_id = task.historical_work_id
        else:
            owners = list(row.get("owner_ids", []))
            if not owners:
                continue
            reference = (
                row.get("historical_reference")
                if not source.historical
                else {
                    "meeting_id": source.id,
                    "version": version.number,
                    "item_id": row["id"],
                    "reason": "接续上期历史会议事项",
                }
            )
            if not reference and not row.get("source", {}).get("sheet_import_id"):
                # A published online assignment should always have a live task.
                continue
            effective_due = row.get("postponed_due_date") or row.get("due_date")
            due = date.fromisoformat(effective_due) if effective_due else None
            fields = {
                "content": row["content"],
                "category": row.get("category", ""),
                "requirements": row.get("requirements", ""),
                "deliverable": row.get("deliverable", ""),
                "owner_ids": owners,
                "due_date": due,
                "status": continuation_result(row, due, meeting_date),
                "verifier_id": row.get("verifier_id") or host_id,
            }
            historical_work_id = identity
        provenance = deepcopy(row.get("source", {})) if row.get("source", {}).get("sheet_import_id") else {}
        provenance["continuation_origin"] = continuation_origin(row, snapshot.get("items", []))
        if fields["status"]:
            provenance["continuation_default_status"] = fields["status"]
        else:
            provenance.pop("continuation_default_status", None)
        items.append(
            MeetingItem(
                id=uid(),
                section="review",
                **fields,
                notes=row.get("notes", ""),
                exclusion_reason="",
                historical_work_id=historical_work_id,
                historical_reference=reference,
                source=provenance,
            )
        )
        positions[items[-1].id] = source_order[row["id"]]
    return sorted(items, key=lambda item: positions[item.id])


def continuation_key(row):
    return row.get("historical_work_id") or row.get("task_id")


def continuation_source_rows(source, snapshot):
    from app.services.historical_links import work_identity

    rows = {}
    for row in sorted(snapshot.get("items", []), key=lambda row: row["section"] != "new"):
        key = work_identity(source.id, row) if source.historical else continuation_key(row)
        if key:
            rows.setdefault(key, row)
    return rows


def prune_unextended_failed_rows(draft, baseline, latest, host_id):
    """Remove only untouched, unassessed inherited duplicates; keep manual work."""
    result = deepcopy(draft)
    removed = []
    retained = []
    for row in result.get("items", []):
        key = continuation_key(row)
        old, current = baseline.get(key), latest.get(key)
        untouched = bool(
            old and current and current.get("section") == "review"
            and current.get("status") in {"未完成", "部分完成"}
            and row.get("section") == "review"
            and (not row.get("status") or (
                row.get("status") == "未到期"
                and row.get("source", {}).get("continuation_default_status") == "未到期"
            ))
            and not row.get("postponed_due_date") and not row.get("exclusion_reason")
            and all(row.get(field, "") == old.get(field, "") for field in (
                "content", "category", "requirements", "deliverable", "notes"
            ))
            and set(row.get("owner_ids", [])) == set(old.get("owner_ids", []))
            and row.get("due_date") == (old.get("postponed_due_date") or old.get("due_date"))
            and row.get("verifier_id") == (old.get("verifier_id") or host_id)
        )
        if untouched:
            removed.append(row["id"])
        else:
            retained.append(row)
    result["items"] = retained
    return result, removed


def lock_continuation_drafts(db, source):
    """Only drafts with a recorded continuation of this exact meeting qualify."""
    if source.draft.get("history_backfill"):
        return []
    links = list(db.scalars(select(AuditLog).where(
        AuditLog.action == "meeting.continue",
        AuditLog.after["source_meeting_id"].as_string() == source.id,
    )))
    links_by_id = {link.entity_id: link for link in links}
    if not links_by_id:
        return []
    drafts = list(db.scalars(select(Meeting).where(
        Meeting.id.in_(links_by_id),
        Meeting.id != source.id,
        Meeting.series_id == source.series_id,
        Meeting.published_version == 0,
        Meeting.has_draft.is_(True),
        Meeting.historical.is_(False),
    ).order_by(Meeting.id).with_for_update().execution_options(populate_existing=True)))
    return [(draft, links_by_id[draft.id]) for draft in drafts]


def sync_continuation_drafts(db, actor, source, targets):
    """Refresh inherited work after release, preserving the next meeting's edits."""
    result = {"updated": 0, "skipped": 0}
    if not targets or source.draft.get("history_backfill"):
        return result
    version = db.scalar(select(MeetingVersion).where(
        MeetingVersion.meeting_id == source.id,
        MeetingVersion.number == source.published_version,
    ))
    open_keys = {
        continuation_key(item.model_dump(mode="json"))
        for item in continuation_items(db, actor, source, version, source.host_id)
    }
    for target, link in targets:
        if target.draft.get("history_backfill"):
            continue
        if not meeting_allowed(db, actor, target, "edit"):
            result["skipped"] += 1
            continue
        incoming = {
            continuation_key(row): row
            for item in continuation_items(
                db, actor, source, version, source.host_id,
                include_terminal=True, meeting_date=target.meeting_date,
            )
            if (row := item.model_dump(mode="json")) and continuation_key(row)
        }
        synced_versions = list(db.scalars(select(AuditLog).where(
            AuditLog.action == "meeting.continuation.sync",
            AuditLog.entity_id == target.id,
            AuditLog.after["source_meeting_id"].as_string() == source.id,
        )))
        base_number = max([link.after["source_version"]] + [
            entry.after["source_version"] for entry in synced_versions
        ])
        base = db.scalar(select(MeetingVersion).where(
            MeetingVersion.meeting_id == source.id, MeetingVersion.number == base_number,
        ))
        if base is None:
            result["skipped"] += 1
            continue
        baseline = continuation_source_rows(source, base.snapshot)
        before = deepcopy(target.draft)
        updated, removed_rows = prune_unextended_failed_rows(
            before, baseline, continuation_source_rows(source, version.snapshot), base.snapshot.get("host_id")
        )
        changed_rows, added_rows = 0, 0
        existing_keys = {continuation_key(row) for row in updated.get("items", [])}
        for row in updated.get("items", []):
            key = continuation_key(row)
            if row.get("section") != "review" or key not in baseline or key not in incoming:
                continue
            original = deepcopy(row)
            latest, old = incoming[key], baseline[key]
            # Review assignments must match the current task. The separately
            # selected completion result and postponement remain the user's.
            for field in ("owner_ids", "due_date", "verifier_id", "task_id", "task_version", "historical_reference"):
                row[field] = deepcopy(latest.get(field))
            if row.get("status") == row.get("source", {}).get("continuation_default_status") == "未到期":
                effective_due = row.get("postponed_due_date") or row.get("due_date")
                task = db.get(Task, row["task_id"]) if row.get("task_id") else None
                row["status"] = continuation_result(
                    {"status": "未到期"}, date.fromisoformat(effective_due) if effective_due else None,
                    target.meeting_date, closed=bool(task and task.closed_at),
                )
                if row["status"]:
                    row["source"]["continuation_default_status"] = row["status"]
                else:
                    row["source"].pop("continuation_default_status", None)
            for field in ("content", "category", "requirements", "deliverable", "notes"):
                if row.get(field, "") == old.get(field, ""):
                    row[field] = latest.get(field, "")
            changed_rows += row != original
        for key, row in incoming.items():
            # An inherited row explicitly deleted in the next draft stays deleted.
            if key in open_keys and key not in baseline and key not in existing_keys:
                updated["items"].append(deepcopy(row))
                existing_keys.add(key)
                added_rows += 1
        # Carried arrangements can contain manually changed owners/deadlines.
        # Keep their data, refreshing only the concurrency token for the task.
        inherited_keys = {continuation_key(row) for row in updated.get("items", []) if row.get("section") == "review" and continuation_key(row) in baseline}
        for row in updated.get("items", []):
            key = continuation_key(row)
            if row.get("section") == "new" and key in inherited_keys and key in incoming:
                row["task_version"] = incoming[key].get("task_version")
        if updated != before:
            target.draft = updated
            target.draft_version += 1
            result["updated"] += 1
        audit(db, actor, "meeting.continuation.sync", target, before=before, after={
            "source_meeting_id": source.id,
            "source_version": source.published_version,
            "updated_items": changed_rows,
            "added_items": added_rows,
            "removed_unassessed_items": removed_rows,
            "draft_version": target.draft_version,
        }, reason="上期会议修订发布后同步带入任务，保留下期日期和手动填写内容")
    return result


def continue_meeting(db: Session, actor: User, meeting_id: str):
    """Create an editable follow-up draft from the latest published minutes."""
    source = locked_get(db, Meeting, meeting_id)
    if not source:
        raise HTTPException(404, "会议不存在")
    meeting_require(db, actor, source, "continue")
    if not source.published_version:
        raise HTTPException(409, "请先发布原会议，再接续新建")
    version = db.scalar(
        select(MeetingVersion).where(
            MeetingVersion.meeting_id == source.id,
            MeetingVersion.number == source.published_version,
        )
    )
    if not version:
        raise HTTPException(409, "已发布会议版本不存在")
    snapshot = version.snapshot
    if not source.series_id:
        raise HTTPException(422, "请先将原会议归入会议系列")

    def active(identifier):
        person = db.get(User, identifier) if identifier else None
        return bool(person and person.active)

    host_id = source.host_id if active(source.host_id) else actor.id
    recorder_id = actor.id
    items = continuation_items(db, actor, source, version, host_id)
    series = db.get(MeetingSeries, source.series_id)
    kind = series.kind if series and series.kind in {"department", "special"} else source.kind
    draft = MeetingDraft(
        title=source.title,
        kind=kind,
        department_id=(source.department_id or (series.department_id if series else None))
        if kind == "department" else None,
        series_id=source.series_id,
        meeting_date=today(),
        meeting_time=snapshot.get("meeting_time", ""),
        location=snapshot.get("location", ""),
        host_id=host_id,
        recorder_id=recorder_id,
        reviewer_id=None,
        attendee_ids=[identifier for identifier in snapshot.get("attendee_ids", []) if active(identifier)],
        online_attendee_ids=[
            identifier for identifier in snapshot.get("online_attendee_ids", [])
            if active(identifier) and identifier in {host_id, *snapshot.get("attendee_ids", [])}
        ],
        items=items,
    )
    meeting = create_meeting(db, actor, draft, continuation_from=source)
    audit(
        db,
        actor,
        "meeting.continue",
        meeting,
        after={
            "source_meeting_id": source.id,
            "source_version": version.number,
        },
    )
    return meeting


def carry_forward(db: Session, actor: User, series_id: str, *, meeting_date=None, exclude_meeting_id=None):
    from app.services.access import visible_meetings, visible_tasks
    from app.services.historical_links import work_identity

    if not db.get(MeetingSeries, series_id):
        raise HTTPException(404, "会议系列不存在")
    query = visible_tasks(db, actor).where(Task.series_id == series_id, Task.withdrawn_at.is_(None))
    if meeting_date:
        query = query.where(
            Task.source_meeting_id.in_(select(Meeting.id).where(Meeting.meeting_date <= meeting_date))
        )
    if exclude_meeting_id:
        query = query.where(Task.source_meeting_id != exclude_meeting_id)
    tasks = db.scalars(query.order_by(Task.created_at)).all()
    result = []
    terminal_reviews = effective_terminal_review_task_ids(db, [task.id for task in tasks])
    failed_reviews = reviewed_unfinished_task_ids(
        db, {task.id for task in tasks}, meeting_date=meeting_date, exclude_meeting_id=exclude_meeting_id,
    )
    for task in tasks:
        if task.id in terminal_reviews or task.id in failed_reviews:
            continue
        result.append(
            {
                "id": uid(),
                "section": "review",
                "content": task.content,
                "category": task.category,
                "requirements": task.requirements,
                "deliverable": task.deliverable,
                "owner_ids": owner_ids(db, task),
                "due_date": task.due_date.isoformat() if task.due_date else None,
                "status": "已完成" if task.closed_at else current_status(task),
                "verifier_id": task.verifier_id,
                "task_id": task.id,
                "task_version": task.version,
                "historical_work_id": task.historical_work_id,
                "notes": "",
                "exclusion_reason": "",
            }
        )

    # Historical imports deliberately do not create active tasks. Carry their
    # confirmed snapshots as review references, preserving that distinction.
    history = visible_meetings(db, actor).where(
        Meeting.series_id == series_id,
        Meeting.published_version > 0,
        Meeting.meeting_date <= (meeting_date or today()),
    )
    if exclude_meeting_id:
        history = history.where(Meeting.id != exclude_meeting_id)
    snapshots = db.execute(
        history.add_columns(MeetingVersion)
        .join(
            MeetingVersion,
            (
                (MeetingVersion.meeting_id == Meeting.id)
                & (MeetingVersion.number == Meeting.published_version)
            ),
        )
        .order_by(Meeting.meeting_date.desc(), Meeting.created_at.desc(), Meeting.id)
    ).all()
    superseded = set().union(
        *(superseded_review_task_ids(db, source, version.snapshot) for source, version in snapshots)
    )
    result = [item for item in result if item["task_id"] not in superseded]
    identities = {
        work_identity(source.id, row) if source.historical else row.get("historical_work_id")
        for source, version in snapshots
        for row in version.snapshot.get("items", [])
    } - {None}
    activated = set(
        db.scalars(select(Task.historical_work_id).where(Task.historical_work_id.in_(identities)))
    )
    # Older online continuations kept the history identity in the document but
    # not on Task. Their existing task still supersedes the imported snapshot.
    document_links = {
        row["task_id"]: row["historical_work_id"]
        for source, version in reversed(snapshots)
        if not source.historical
        for row in version.snapshot.get("items", [])
        if row.get("task_id") and row.get("historical_work_id")
    }
    existing_links = set(db.scalars(select(Task.id).where(Task.id.in_(document_links))))
    activated.update(document_links[identifier] for identifier in existing_links)
    for item in result:
        if not item.get("historical_work_id") and item["task_id"] in document_links:
            item["historical_work_id"] = document_links[item["task_id"]]
    seen = set()
    for source, version in snapshots:
        for row in sorted(version.snapshot.get("items", []), key=lambda item: item["section"] != "new"):
            identity = work_identity(source.id, row) if source.historical else row.get("historical_work_id")
            if not identity or identity in seen:
                continue
            seen.add(identity)
            if identity in activated or not automatically_carried_row(row):
                continue
            reference = (
                {
                    "meeting_id": source.id,
                    "version": version.number,
                    "item_id": row["id"],
                    "reason": "从已确认的上期历史会议带入",
                }
                if source.historical
                else row.get("historical_reference")
            )
            if not reference and not row.get("source", {}).get("sheet_import_id"):
                continue
            owners = list(row.get("owner_ids", []))
            if not owners:
                continue
            effective_due = row.get("postponed_due_date") or row.get("due_date")
            due = date.fromisoformat(effective_due) if effective_due else None
            verifier = db.get(User, row.get("verifier_id")) if row.get("verifier_id") else None
            result.append(
                {
                    "id": uid(),
                    "section": "review",
                    "content": row["content"],
                    "category": row.get("category", ""),
                    "requirements": row.get("requirements", ""),
                    "deliverable": row.get("deliverable", ""),
                    "owner_ids": owners,
                    "due_date": effective_due,
                    "status": "未完成"
                    if due and due < (meeting_date or today())
                    else row.get("status", "未到期"),
                    "verifier_id": verifier.id if verifier else None,
                    "task_id": None,
                    "task_version": None,
                    "historical_work_id": identity,
                    "historical_reference": reference,
                    "notes": "",
                    "exclusion_reason": "",
                    "source": deepcopy(row.get("source", {}))
                    if row.get("source", {}).get("sheet_import_id")
                    else {},
                }
            )
    return result
