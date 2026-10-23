from datetime import date
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

Status = Literal["已完成", "未完成", "未到期", "不统计", "关闭"]


class HistoricalReference(BaseModel):
    meeting_id: str
    version: int
    item_id: str
    reason: str = Field(min_length=1, max_length=1000)


class MeetingItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()), max_length=36)
    section: Literal["review", "new"]
    category: str = ""
    content: str = Field(default="", max_length=20000)
    requirements: str = ""
    owner_ids: list[str] = Field(default_factory=list)
    due_date: date | None = None
    status: Status = "未到期"
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
    absentees: str = ""
    decisions: str = ""
    items: list[MeetingItem] = Field(default_factory=list)
    attachment_ids: list[str] = Field(default_factory=list)
    recipient_ids: list[str] = Field(default_factory=list)
    cc_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def distinct_items(self):
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
