from datetime import date
from typing import Literal
from unicodedata import normalize
from uuid import uuid4

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

Status = Literal["已完成", "未完成", "未到期", "不统计", "关闭"]


class HistoricalReference(BaseModel):
    meeting_id: str
    version: int
    item_id: str
    reason: str = Field(min_length=1, max_length=1000)


class BackfillReference(BaseModel):
    meeting_id: str
    version: int = Field(ge=1)
    item_id: str
    task_id: str
    meeting_title: str = ""
    meeting_date: str = ""


class MeetingItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()), max_length=36)
    section: Literal["review", "new"]
    category: str = ""
    content: str = Field(default="", max_length=20000)
    requirements: str = ""
    owner_ids: list[str] = Field(default_factory=list)
    # An initial manual backfill may preserve departed original owners.
    historical_owner_record: bool = False
    due_date: date | None = None
    # Preserve the reviewed deadline; a published postponement changes the live task.
    postponed_due_date: date | None = None
    # Review results may remain unselected while a draft is being edited.
    status: Status | Literal["", "部分完成"] = "未到期"
    owner_results: dict[str, Literal["已完成", "未完成"]] = Field(default_factory=dict)
    verifier_id: str | None = None
    deliverable: str = ""
    notes: str = ""
    exclusion_reason: str = ""
    task_id: str | None = None
    task_version: int | None = None
    change_reason: str = ""
    recurrence_key: str | None = None
    source: dict = Field(default_factory=dict)
    historical_work_id: str | None = None
    historical_reference: HistoricalReference | None = None
    backfill_reference: BackfillReference | None = None

    @model_validator(mode="after")
    def blank_result_only_in_review(self):
        if self.section != "review" and self.status == "":
            raise ValueError("本次安排的任务状态不能为空")
        if self.status == "部分完成":
            if self.section != "review":
                raise ValueError("只有上期任务可以按责任人评定部分完成")
            if not self.owner_ids or set(self.owner_results) != set(self.owner_ids):
                raise ValueError("请为本条任务的每位责任人选择完成结果")
            results = set(self.owner_results.values())
            if len(results) == 1:
                self.status = next(iter(results))
                self.owner_results = {}
        else:
            self.owner_results = {}
        return self

    @field_validator("owner_ids")
    @classmethod
    def unique_owners(cls, values):
        return list(dict.fromkeys(values))


class MeetingDraft(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    department_id: str | None = None
    series_id: str | None = None
    kind: Literal["department", "special"] = "department"
    meeting_date: date
    meeting_time: str = ""
    location: str = ""
    host_id: str
    recorder_id: str | None = None
    reviewer_id: str | None = None
    attendee_ids: list[str] = Field(default_factory=list)
    online_attendee_ids: list[str] = Field(default_factory=list)
    absentees: str = ""
    decisions: str = ""
    items: list[MeetingItem] = Field(default_factory=list)
    attachment_ids: list[str] = Field(default_factory=list)
    recipient_ids: list[str] = Field(default_factory=list)
    recipient_customized: bool = False
    cc_ids: list[str] = Field(default_factory=list)
    cc_emails: list[EmailStr] = Field(default_factory=list, max_length=100)
    sheet_import_id: str | None = None
    import_confirmed_issues: list[str] = Field(default_factory=list, max_length=2000)
    history_backfill: bool = False

    @field_validator("cc_emails")
    @classmethod
    def unique_cc_emails(cls, values):
        unique = {}
        for value in values:
            unique.setdefault(value.casefold(), value)
        return list(unique.values())

    @field_validator("cc_emails", mode="before")
    @classmethod
    def normalize_cc_emails(cls, values):
        if not isinstance(values, list):
            return values
        result = []
        for value in values:
            if isinstance(value, str):
                if any(ord(char) < 32 or ord(char) == 127 for char in value):
                    raise ValueError("邮箱不能包含换行或控制字符")
                value = normalize("NFKC", value).strip()
                if value.lower().startswith("mailto:"):
                    value = value[7:]
            result.append(value)
        return result

    @model_validator(mode="after")
    def distinct_items(self):
        self.online_attendee_ids = list(dict.fromkeys(self.online_attendee_ids))
        if set(self.online_attendee_ids) - {self.host_id, *self.attendee_ids}:
            raise ValueError("线上参会人员必须从参会人员或主持人中选择")
        if self.kind == "department" and not self.department_id:
            raise ValueError("部门会议必须选择所属部门")
        if len({x.id for x in self.items}) != len(self.items):
            raise ValueError("会议条目编号不能重复")
        return self


class SaveDraft(BaseModel):
    expected_version: int
    draft: MeetingDraft


class PublishRequest(BaseModel):
    expected_version: int
    reason: str = "首次发布"
    notify: bool = True
    # New clients use one explicitly selected list for both delivery channels.
    # Omission preserves the notification semantics of older clients.
    shared_recipients: bool = False


class TaskChange(BaseModel):
    expected_version: int
    content: str = Field(min_length=1)
    category: str = ""
    requirements: str = ""
    deliverable: str = ""
    due_date: date | None = None
    owner_ids: list[str] = Field(min_length=1)
    verifier_id: str
    reason: str = Field(min_length=1)


class ApplyCompletion(BaseModel):
    expected_version: int
    idempotency_key: str = Field(min_length=8, max_length=100)
    explanation: str = Field(min_length=1, max_length=20000)
    evidence_ids: list[str] = Field(default_factory=list)


class VerifyCompletion(BaseModel):
    decision: Literal["approved", "rejected"]
    opinion: str = Field(min_length=1, max_length=10000)


class LoginRequest(BaseModel):
    username: str
    password: str
