"""Exercise actual HTTP boundaries rather than trusting hidden frontend controls."""
from datetime import date
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db import get_db
from app.core.security import issue_token
from app.main import app
from app.models import BusinessRoleAssignment, Meeting, MeetingAttendee, MeetingEditRequest, Task, TaskOwner
from app.schemas import MeetingDraft, PublishRequest
from app.services.business import create_meeting, publish
from app.services.role_migration import rule
from tests.test_custom_roles import grant


def auth(person):
    return {"Authorization":"Bearer "+issue_token(person)}


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db]=lambda:db
    with TestClient(app) as value:
        yield value
    app.dependency_overrides.clear()


def api_role(client,admin,person,rules):
    r=client.post("/api/admin/roles",headers=auth(admin),json={"name":"权限验证 "+uuid4().hex[:8],"rules":rules})
    assert r.status_code==201,r.text
    identifier=r.json()["id"]
    r=client.put(f"/api/admin/users/{person.id}/business-roles",headers=auth(admin),json={
        "roles":[identifier],"expected_roles":[],"expected_department_id":person.department_id})
    assert r.status_code==200,r.text
    return identifier


@pytest.mark.parametrize("path",["role-catalog","roles","role-assignments"])
def test_catalog_and_grants_are_admin_only(client,team,path):
    assert client.get("/api/admin/"+path,headers=auth(team["a"])).status_code==403


@pytest.mark.parametrize("endpoint,body",[
    ("employee-roles",{"roles":["department_meetings"],"expected_roles":[],"expected_department_id":None,"reason":"旧页面"}),
    ("report-access",{"department_ids":[],"expected_department_ids":[],"reason":"旧页面"}),
    ("department-leader",{"department_ids":[],"expected_department_ids":[],"reason":"旧页面"}),
])
def test_retired_authorization_writes_cannot_create_shadow_grants(client,team,endpoint,body):
    response=client.put(f'/api/admin/users/{team["a"].id}/{endpoint}',headers=auth(team["host"]),json=body)
    assert response.status_code==410,response.text


def test_reusing_token_observes_assignment_update_disable_and_revocation(client,db,team):
    admin,person=team["host"],team["colleague"]
    token=auth(person)
    assert client.get("/api/reports/access",headers=token).json()["mode"]=="self"
    identifier=api_role(client,admin,person,[rule("reports",["view"],"all")])
    assert client.get("/api/reports/access",headers=token).json()["mode"]=="all"
    definition=client.get("/api/admin/roles",headers=auth(admin)).json()[0]
    assert definition["member_count"]==1
    assert client.get(f"/api/admin/roles/{identifier}/members",headers=auth(admin)).json()[0]["id"]==person.id
    assert client.get(f"/api/admin/roles/{identifier}/members",headers=token).status_code==403
    changed=client.put(f"/api/admin/roles/{identifier}",headers=auth(admin),json={**{k:definition[k] for k in ("name","rules")},"expected_version":1,"active":False})
    assert changed.status_code==200,changed.text
    assert client.get("/api/reports/access",headers=token).json()["mode"]=="self"
    assert client.put(f"/api/admin/roles/{identifier}",headers=auth(admin),json={"name":"过期提交","rules":[],"expected_version":1}).status_code==409
    assert client.put(f"/api/admin/users/{person.id}/business-roles",headers=auth(admin),json={"roles":[],"expected_roles":[],"expected_department_id":person.department_id}).status_code==409
    assert client.put(f"/api/admin/users/{person.id}/business-roles",headers=auth(admin),json={"roles":[],"expected_roles":[identifier],"expected_department_id":person.department_id}).status_code==200


@pytest.mark.parametrize("kind",["host","recorder"])
def test_own_cross_department_draft_results_publish_and_published_deletion(client,db,team,draft_factory,kind):
    person=team["colleague"]
    draft=draft_factory(section="review",status="已完成")
    draft.department_id=team["other"].id
    setattr(draft,kind+"_id",person.id)
    meeting=create_meeting(db,team["host"],draft,historical=True)
    detail=client.get(f"/api/meetings/{meeting.id}",headers=auth(person))
    assert detail.status_code==200,detail.text
    assert detail.json()["permissions"]["assess"]
    assert detail.json()["can_delete"]
    published=client.post(f"/api/meetings/{meeting.id}/publish",headers=auth(person),json={"expected_version":1,"notify":False})
    assert published.status_code==200,published.text
    assert client.delete(f"/api/meetings/{meeting.id}",headers=auth(person)).status_code==403
    assert not client.get(f"/api/meetings/{meeting.id}",headers=auth(person)).json()["can_delete"]
    assert client.get("/api/reports/access",headers=auth(person)).json()["mode"]=="self"
    assert not client.get("/api/direct-assignments/options",headers=auth(person)).json()["can_assign"]


def test_publish_only_cannot_edit_revise_or_download_but_can_publish(client,db,team,draft_factory,monkeypatch):
    meeting=create_meeting(db,team["host"],draft_factory())
    person=team["colleague"]
    api_role(client,team["host"],person,[rule("meetings",["publish"])])
    info=client.get(f"/api/meetings/{meeting.id}",headers=auth(person)).json()
    assert not info["can_edit"] and info["permissions"]["preview"] and info["permissions"]["publish"]
    assert not info["file_permissions"]["download"]
    assert client.put(f"/api/meetings/{meeting.id}",headers=auth(person),json={"expected_version":1,"draft":meeting.draft}).status_code==403
    assert client.post(f"/api/meetings/{meeting.id}/revision",headers=auth(person),json={"reason":"越权"}).status_code==403
    assert client.post(f"/api/meetings/{meeting.id}/preview",headers=auth(person),json={"expected_version":1,"format":"tasks"}).status_code==403
    assert client.post(f"/api/meetings/{meeting.id}/publish",headers=auth(person),json={"expected_version":1,"notify":False}).status_code==200


def test_view_all_and_edit_department_does_not_allow_foreign_write(client,db,team,draft_factory):
    person=team["colleague"]
    grant(db,person,[rule("meetings",["view"],"all")],"全公司查看")
    grant(db,person,[rule("meetings",["edit","delete_draft"])],"部门编辑")
    draft=draft_factory();draft.department_id=team["other"].id
    meeting=create_meeting(db,team["host"],draft)
    detail=client.get(f"/api/meetings/{meeting.id}",headers=auth(person)).json()
    assert detail["draft"] and not detail["can_edit"] and not detail["can_delete"]
    assert client.delete(f"/api/meetings/{meeting.id}",headers=auth(person)).status_code==403
    assert client.post(f"/api/meetings/{meeting.id}/publish",headers=auth(person),json={"expected_version":1,"notify":False}).status_code==403
    meeting.department_id=None;meeting.kind="special";db.flush()
    assert client.get(f"/api/meetings/{meeting.id}",headers=auth(person)).status_code==200


def test_department_draft_delete_and_edit_only_cannot_assess(client,db,team,draft_factory):
    person=team["colleague"]
    api_role(client,team["host"],person,[rule("meetings",["edit","delete_draft"])])
    draft=draft_factory(section="review",status="未完成")
    meeting=create_meeting(db,team["host"],draft,historical=True)
    changed=MeetingDraft.model_validate(meeting.draft);changed.items[0].status="已完成"
    r=client.put(f"/api/meetings/{meeting.id}",headers=auth(person),json={"expected_version":1,"draft":changed.model_dump(mode="json")})
    assert r.status_code==403,r.text
    deleted=client.request("DELETE",f"/api/meetings/{meeting.id}",headers=auth(person),json={"expected_draft_version":1})
    assert deleted.status_code==200,deleted.text
    assert db.get(Meeting,meeting.id) is None


def test_task_overview_grants_detail_without_unrelated_meeting_content(client,db,team,draft_factory):
    person=team["colleague"]
    api_role(client,team["host"],person,[rule("overview",["view"])])
    draft=draft_factory();draft.department_id=team["other"].id
    meeting=create_meeting(db,team["host"],draft)
    publish(db,team["host"],meeting.id,PublishRequest(expected_version=1,notify=False))
    task=db.scalar(select(Task).where(Task.source_meeting_id==meeting.id))
    assert client.get("/api/task-overview",headers=auth(person)).status_code==200
    detail=client.get(f"/api/tasks/{task.id}",headers=auth(person))
    assert detail.status_code==200,detail.text
    assert not detail.json()["can_edit"] and not detail.json()["can_view_source"]
    assert client.get(f"/api/meetings/{meeting.id}",headers=auth(person)).status_code==403


def test_existing_single_meeting_approval_is_retained_and_host_generation_revokes(client,db,team,draft_factory):
    person=team["colleague"]
    draft=draft_factory();draft.department_id=team["other"].id
    meeting=create_meeting(db,team["host"],draft)
    db.add(MeetingEditRequest(meeting_id=meeting.id,applicant_id=person.id,host_id=meeting.host_id,
        permission_generation=meeting.permission_generation,state="approved"));db.flush()
    info=client.get(f"/api/meetings/{meeting.id}",headers=auth(person)).json()
    assert info["can_edit"] and info["permissions"]["continue"]
    assert not info["permissions"]["assess"] and not info["can_delete"]
    meeting.permission_generation+=1;db.flush()
    assert client.get(f"/api/meetings/{meeting.id}",headers=auth(person)).status_code==403


def test_edit_and_withdraw_dispatch_without_create_permission(client,db,team):
    person=team["colleague"]
    task=Task(code="R-HTTP",content="原派发",department_id=team["dep"].id,issuer_id=person.id,
        source_item_id="http",due_date=date(2027,1,1),verifier_id=person.id,status="未到期")
    db.add(task);db.flush();db.add(TaskOwner(task_id=task.id,user_id=team["a"].id));db.flush()
    api_role(client,team["host"],person,[rule("dispatch",["edit","withdraw"])])
    assert not client.get("/api/direct-assignments/options",headers=auth(person)).json()["can_assign"]
    changed=client.put(f"/api/tasks/{task.id}",headers=auth(person),json={"expected_version":1,"content":"已修改",
        "due_date":"2027-01-02","owner_ids":[team["a"].id],"verifier_id":person.id,"reason":"调整"})
    assert changed.status_code==200,changed.text
    assert client.post(f"/api/tasks/{task.id}/withdraw",headers=auth(person),json={"expected_version":2,"reason":"取消"}).status_code==200


def test_ordinary_job_title_cannot_create_series_or_dispatch_and_logs_remain_admin_only(client,db,team):
    person=team["colleague"];person.position="董事长";db.flush()
    grant(db,person,[rule("reports",["view","export"],"all")])
    assert client.post("/api/series",headers=auth(person),json={"name":"越权系列"}).status_code==403
    assert client.get("/api/admin/roles",headers=auth(person)).status_code==403
    assert client.get("/api/admin/operation-logs",headers=auth(person)).status_code==403
    assert not client.get("/api/direct-assignments/options",headers=auth(person)).json()["can_assign"]
