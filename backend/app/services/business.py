from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import delete, select
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
    RecurringRule,
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
    can_edit_meeting,
    can_manage_direct_task,
    department_ids,
    direct_assignees,
    meeting_access,
    task_access,
)
from app.services.departments import department_path


def today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def audit(db, actor, action, entity, before=None, after=None, reason=""):
    db.add(
        AuditLog(
            actor_id=actor.id,
            action=action,
            entity_type=entity.__tablename__,
            entity_id=entity.id,
            before=before,
            after=after,
            reason=reason,
        )
    )


def external_notifications_enabled(db: Session) -> bool:
    setting = db.get(SystemSetting, "reminders")
    return bool(setting.value.get("enabled", True)) if setting else True


def task_wecom_enabled(db: Session, task: Task) -> bool:
    return external_notifications_enabled(db) or bool(
        get_settings().wecom_direct_notifications_enabled
        and task.source_meeting_id is None and task.issuer_id
    )


def notification_enabled(db: Session, channel: str, payload: dict) -> bool:
    if channel not in {"wecom", "email"} or external_notifications_enabled(db):
        return True
    if channel != "wecom" or not get_settings().wecom_direct_notifications_enabled:
        return False
    identifiers = list(payload.get("reminder_task_ids", []))
    if payload.get("task_id"):
        identifiers.append(payload["task_id"])
    if "verification_request_ids" in payload:
        identifiers.extend(db.scalars(select(CompletionRequest.task_id).where(
            CompletionRequest.id.in_(payload["verification_request_ids"])
        )))
    return any(task_wecom_enabled(db, task) for identifier in identifiers if (task := db.get(Task, identifier)))


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
    if settings.native_todo_app_enabled and source == "direct" and not link:
        return  # New application todos require an explicit JS-SDK confirmation.
    if link and link.provider == "app":
        if action == "create":
            return
        suffix = key_suffix or f"v{task.version}"
        enqueue(db, f"native-todo:{task.id}:{action}:{suffix}", "native_todo", {
            "task_id": task.id, "task_version": task.version, "action": action,
        })
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


def task_snapshot(db, task):
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
        "owner_ids": sorted(owner_ids(db, task)),
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
                Assessment.status == "已完成",
                Assessment.meeting_version == Meeting.published_version,
            )
        )
    )


def validate_draft(
    db: Session, draft: MeetingDraft, actor: User, historical=False, previous=None, notify=True
):
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
        if (
            item.section == "review"
            and item.status == "未到期"
            and item.due_date
            and draft.meeting_date > item.due_date
        ):
            raise HTTPException(422, "会议日期晚于计划完成日期，不能标为未到期")
        if not historical and item.section == "review" and not item.task_id:
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
                and item.status == "已完成"
                and task.closed_at is None
            ):
                raise HTTPException(422, "关联任务尚未验证通过，不能计为已完成")
            if not historical and item.section == "review" and item.status == "已完成" and task.closed_at:
                closed = (
                    task.closed_at if task.closed_at.tzinfo else task.closed_at.replace(tzinfo=timezone.utc)
                )
                if draft.meeting_date < closed.astimezone(ZoneInfo("Asia/Shanghai")).date():
                    raise HTTPException(422, "会议日期早于任务验证关闭日期，不能提前记为已完成")
        if item.recurrence_key and (not task or task.recurrence_key != item.recurrence_key):
            raise HTTPException(422, "周期规则请在已发布任务详情中设置，不能直接填写周期编号")
        if prior and prior.get("historical_work_id") != item.historical_work_id:
            raise HTTPException(422, "已发布历史事项的追溯编号不能直接修改")
        review_owners = owner_ids(db, task) if task and item.section == "review" else []
        for identifier in item.owner_ids:
            check_person(
                identifier, preserved_assignment or identifier in prior_owners or identifier in review_owners
            )
        verifier_id = item.verifier_id or draft.host_id
        check_person(
            verifier_id,
            preserved_assignment
            or (
                item.section == "review"
                and (verifier_id == prior_verifier or (task and verifier_id == task.verifier_id))
            ),
        )


def create_meeting(db: Session, actor: User, draft: MeetingDraft, historical=False, continuation_from=None):
    if draft.department_id and not db.get(Department, draft.department_id):
        raise HTTPException(422, "请选择有效的所属部门")
    if historical and not actor.is_admin and actor.id != draft.host_id:
        raise HTTPException(403, "历史导入需要管理员或该会议主持人")
    if not actor.is_admin:
        if not historical and not continuation_from and draft.department_id and draft.department_id not in department_ids(db, actor):
            raise HTTPException(403, "只能创建本人所属部门的会议")
        if not historical and draft.recorder_id not in {None, actor.id}:
            raise HTTPException(403, "编制人只能选择本人或留空")
        if not draft.department_id and actor.id not in {draft.host_id, draft.recorder_id}:
            raise HTTPException(403, "无部门的专项会议必须由本人主持或编制")
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
    for role in ("host_id", "recorder_id"):
        identifier = getattr(draft, role)
        if identifier and (not meeting.published_version or identifier != getattr(meeting, role)):
            active_user(db, identifier)
    if meeting.draft_version != expected:
        raise HTTPException(409, "会议已被其他人修改，请刷新")
    if not meeting.has_draft:
        raise HTTPException(409, "已发布，请先创建修订版本")
    if not actor.is_admin and draft.department_id and draft.department_id not in department_ids(db, actor):
        if not (can_edit_meeting(db, actor, meeting) and draft.department_id == meeting.department_id):
            raise HTTPException(403, "不能将会议移至无权管理的部门")
    if not actor.is_admin and actor.id != meeting.host_id and (
        draft.host_id != meeting.draft.get("host_id", meeting.host_id)
        or draft.recorder_id != meeting.draft.get("recorder_id", meeting.recorder_id)
    ):
        raise HTTPException(403, "只有会议主持人或管理员可以更换主持人和编制人")
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
    source_tasks = select(Task.id).where(Task.source_meeting_id == meeting.id)
    # Generation holds a rule lock before reading the source task's scope. Do not
    # wait while holding the meeting lock: its new instance may need that same
    # meeting for its foreign key. Retry the move once generation has committed.
    rule_ids = set(db.scalars(
        select(RecurringRule.id).where(RecurringRule.source_task_id.in_(source_tasks))
    ))
    locked_rule_ids = set(db.scalars(
        select(RecurringRule.id)
        .where(RecurringRule.id.in_(rule_ids))
        .order_by(RecurringRule.id)
        .with_for_update(skip_locked=True)
    ))
    if locked_rule_ids != rule_ids:
        raise HTTPException(409, "周期任务正在生成或维护，请稍后重试调整会议归属")
    for task in db.scalars(
        select(Task)
        .where(Task.source_meeting_id == meeting.id)
        .order_by(Task.id)
        .with_for_update()
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
    meeting_access(db, actor, meeting, edit=True)
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
    meeting_access(db, actor, meeting, edit=True)
    if not reason.strip():
        raise HTTPException(422, "请填写修订原因")
    if meeting.has_draft:
        raise HTTPException(409, "已经存在未发布草稿")
    meeting.has_draft = True
    meeting.draft_version += 1
    audit(db, actor, "meeting.revision", meeting, reason=reason)
    return meeting


def change_task(
    db: Session, actor: User, task: Task, change: TaskChange, source_meeting_id=None, notify=True
):
    if task.source_meeting_id:
        meeting_access(db, actor, db.get(Meeting, task.source_meeting_id), edit=True)
    elif not can_manage_direct_task(db, actor, task):
        raise HTTPException(403, "只有管理员或当前有派发权限的派发人可以修改此任务")
    if task.source_meeting_id is None:
        allowed = {person.id for person in direct_assignees(db, actor)}
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
    if notify:
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
        enqueue_native_todo(db, task, "update")
        from app.services.app_native_todo import enqueue_issuer_confirmation

        enqueue_issuer_confirmation(db, task)
    return task


def publish(db: Session, actor: User, meeting_id: str, command: PublishRequest):
    meeting = locked_get(db, Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(404, "会议不存在")
    meeting_access(db, actor, meeting, edit=True)
    if meeting.draft_version != command.expected_version:
        raise HTTPException(409, "草稿已变化，请重新预览")
    if not meeting.has_draft:
        return meeting  # Already committed this exact draft.
    if meeting.published_version and not command.reason.strip():
        raise HTTPException(422, "修订必须说明原因")
    draft = MeetingDraft.model_validate(meeting.draft)
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
    previous_items = {i["id"]: i for i in previous_version.snapshot["items"]} if previous_version else {}
    if previous_version:
        previous_review = review_projection(previous_version.snapshot)
        next_review = review_projection(draft.model_dump(mode="json"))
        scope_changed = any(
            previous_version.snapshot.get(key) != draft.model_dump(mode="json").get(key)
            for key in ("meeting_date", "department_id", "kind")
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
        if (
            item.task_id
            and not frozen_review
            and item.id not in frozen_assignments
            and item.task_version != locked_tasks[item.task_id].version
        ):
            raise HTTPException(409, "关联任务发生变化，请重新带入并核对")
        if item.task_id and item.section == "review" and not meeting.historical:
            task = locked_tasks[item.task_id]
            expected_owners = previous["owner_ids"] if frozen_review else owner_ids(db, task)
            expected_due = (
                date.fromisoformat(previous["due_date"])
                if frozen_review and previous.get("due_date")
                else task.due_date
            )
            if set(item.owner_ids) != set(expected_owners) or item.due_date != expected_due:
                raise HTTPException(422, "上期回顾的责任人与原期限必须与关联任务一致；延期请在本次安排中录入")
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
    snapshot["department_name"] = department_path(db, draft.department_id)
    snapshot["host_name"] = db.get(User, draft.host_id).name
    snapshot["recorder_name"] = db.get(User, draft.recorder_id).name if draft.recorder_id else ""
    snapshot["reviewer_name"] = db.get(User, draft.reviewer_id).name if draft.reviewer_id else ""
    snapshot["attendee_names"] = [db.get(User, x).name for x in draft.attendee_ids]
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
                    reason=item.change_reason or command.reason,
                )
                before = task_snapshot(db, task)
                if any(before[k] != v for k, v in candidate.model_dump(mode="json").items() if k in before):
                    change_task(db, actor, task, candidate, meeting.id, notify=command.notify)
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
                        change_task(db, actor, task, candidate, meeting.id, notify=command.notify)
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
                            reason="会议发布",
                            actor_id=actor.id,
                            source_meeting_id=meeting.id,
                        )
                    )
                    if command.notify:
                        enqueue(
                            db,
                            f"task:{task.id}:assigned",
                            "wecom",
                            {
                                "task_id": task.id,
                                "task_version": task.version,
                                "user_ids": item.owner_ids,
                                "title": "新的会议任务",
                                "content": task.content,
                            },
                        )
                        enqueue_native_todo(db, task, "create")
            target["task_id"], target["task_version"] = task.id, task.version
        elif item.section == "review":
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
                        status=item.status,
                        content=item.content,
                        due_date=item.due_date,
                        notes=item.notes,
                    )
                )
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
    if not meeting.historical and command.notify and (draft.recipient_ids or draft.cc_ids):
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
                identifier not in related
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
            },
        )
    audit(db, actor, "meeting.publish", meeting, after={"version": number}, reason=command.reason)
    db.flush()
    completed = effective_completed_task_ids(db, linked_ids)
    for identifier, task in locked_tasks.items():
        task.completion_reviewed = identifier in completed
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


def continue_meeting(db: Session, actor: User, meeting_id: str):
    """Create an editable follow-up draft from the latest published minutes."""
    from app.services.historical_links import work_identity

    source = locked_get(db, Meeting, meeting_id)
    if not source:
        raise HTTPException(404, "会议不存在")
    meeting_access(db, actor, source, edit=True)
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
    seen = set()
    items = []
    # An arrangement in the last minutes supersedes its earlier review row.
    candidates = sorted(snapshot.get("items", []), key=lambda row: row["section"] != "new")
    for row in candidates:
        if row.get("status") in {"已完成", "关闭", "不统计"}:
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
        if task:
            if task.source_meeting_id != source.id:
                task_access(db, actor, task)
            if (
                (task.closed_at and task.id in effective_completed_task_ids(db, [task.id]))
                or not owner_ids(db, task)
                or not all(active(identifier) for identifier in owner_ids(db, task))
            ):
                continue
            owners = owner_ids(db, task)
            if not active(task.verifier_id):
                continue
            fields = {
                "content": task.content,
                "category": task.category,
                "requirements": task.requirements,
                "deliverable": task.deliverable,
                "owner_ids": owners,
                "due_date": task.due_date,
                "status": "已完成" if task.closed_at else current_status(task),
                "verifier_id": task.verifier_id,
                "task_id": task.id,
                "task_version": task.version,
                "recurrence_key": task.recurrence_key,
            }
            reference = None
            historical_work_id = task.historical_work_id
        else:
            owners = [identifier for identifier in row.get("owner_ids", []) if active(identifier)]
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
            if not reference:
                # A published online assignment should always have a live task.
                continue
            due = date.fromisoformat(row["due_date"]) if row.get("due_date") else None
            fields = {
                "content": row["content"],
                "category": row.get("category", ""),
                "requirements": row.get("requirements", ""),
                "deliverable": row.get("deliverable", ""),
                "owner_ids": owners,
                "due_date": due,
                "status": "未完成" if due and due < today() else row.get("status", "未到期"),
                "verifier_id": row.get("verifier_id") if active(row.get("verifier_id")) else host_id,
            }
            historical_work_id = identity
        items.append(
            MeetingItem(
                id=uid(),
                section="review",
                **fields,
                notes="",
                exclusion_reason="",
                historical_work_id=historical_work_id,
                historical_reference=reference,
            )
        )
    draft = MeetingDraft(
        title=source.title,
        kind=source.kind,
        department_id=source.department_id,
        series_id=source.series_id,
        meeting_date=today(),
        meeting_time=snapshot.get("meeting_time", ""),
        location=snapshot.get("location", ""),
        host_id=host_id,
        recorder_id=recorder_id,
        reviewer_id=None,
        attendee_ids=[identifier for identifier in snapshot.get("attendee_ids", []) if active(identifier)],
        items=items,
    )
    meeting = create_meeting(db, actor, draft, continuation_from=source)
    audit(db, actor, "meeting.continue", meeting, after={"source_meeting_id": source.id})
    return meeting


def carry_forward(db: Session, actor: User, series_id: str):
    from app.services.access import visible_tasks

    tasks = db.scalars(
        visible_tasks(db, actor).where(Task.series_id == series_id).order_by(Task.created_at)
    ).all()
    result = []
    completed = effective_completed_task_ids(db, [task.id for task in tasks])
    for task in tasks:
        if task.closed_at and task.id in completed:
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
                "notes": "",
                "exclusion_reason": "",
            }
        )
    return result
