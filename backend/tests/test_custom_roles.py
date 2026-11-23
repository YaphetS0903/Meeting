"""Role operation/scope pairs must never be combined into a wider permission."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models import BusinessRole, BusinessRoleAssignment, EmployeeRoleGrant, ReportDepartmentGrant, Meeting, Department, User
from app.services.role_permissions import allows, meeting_allowed, permission_scope
from app.services.role_migration import migrate_explicit_grants, rule
from app.services.access import can_delete_meeting, can_confirm_meeting_results, direct_assignees, visible_meetings, can_oversee_tasks
from app.services.report_access import report_access, senior_report_access, report_export_access
from app.business_role_api import RoleInput, AssignmentInput, create, update, assign


def grant(db,person,rules,name="测试角色"):
    role=BusinessRole(name=name,rules=rules)
    db.add(role);db.flush()
    db.add(BusinessRoleAssignment(user_id=person.id,role_id=role.id));db.flush()
    return role


def meeting(db,team,**values):
    row=Meeting(title="跨部门会议",department_id=team["other"].id,meeting_date=__import__("datetime").date(2026,11,10),
        host_id=team["a"].id,recorder_id=team["b"].id,**values)
    db.add(row);db.flush();return row


def test_cross_module_and_cross_role_scopes_never_expand(db,team):
    person=team["colleague"]
    grant(db,person,[rule("reports",["view"],"all"),rule("meetings",["edit"],"department")])
    grant(db,person,[rule("meetings",["view"],"all")],"全公司只读")
    assert allows(db,person,"reports.view",team["other"].id)
    assert allows(db,person,"meetings.view",team["other"].id)
    assert not allows(db,person,"meetings.edit",team["other"].id)
    assert not direct_assignees(db,person)
    assert not allows(db,person,"reports.export",team["dep"].id)


def test_job_titles_and_hosting_do_not_grant_other_department_rights(db,team):
    person=team["colleague"]
    person.position="总经理";person.leader_department_ids=[team["dep"].id]
    db.flush()
    assert direct_assignees(db,person)==[]
    assert not can_oversee_tasks(db,person)
    row=meeting(db,team)
    assert db.scalar(visible_meetings(db,person).where(Meeting.id==row.id)) is None
    assert report_access(db,person).mode=="self"


@pytest.mark.parametrize("key",["a","b"])
def test_host_and_recorder_manage_their_cross_department_meetings_without_role(db,team,key):
    row=meeting(db,team)
    person=team[key]
    for action in ["edit","assess","preview","publish","revise","cancel_revision","continue"]:
        assert meeting_allowed(db,person,row,action)
    assert can_confirm_meeting_results(db,person,row)
    assert can_delete_meeting(db,person,row)
    row.published_version=1
    assert not can_delete_meeting(db,person,row)
    assert not meeting_allowed(db,person,row,"delete_draft")
    assert report_access(db,person).mode=="self"
    assert not direct_assignees(db,person)


def test_subtree_transfer_disabled_role_and_inactive_account(db,team):
    child=Department(name="研发组",parent_id=team["dep"].id);db.add(child);db.flush()
    child_user=User(name="研发员工",department_id=child.id);db.add(child_user);db.flush()
    person=team["colleague"]
    role=grant(db,person,[rule("dispatch",["create"]),rule("reports",["view","export"])])
    assert child_user.id in {u.id for u in direct_assignees(db,person)}
    assert team["outsider"].id not in {u.id for u in direct_assignees(db,person)}
    person.department_id=team["other"].id;db.flush()
    assert team["outsider"].id in {u.id for u in direct_assignees(db,person)}
    assert child_user.id not in {u.id for u in direct_assignees(db,person)}
    role.active=False;db.flush();assert not direct_assignees(db,person)
    role.active=True;person.active=False;db.flush()
    assert not allows(db,person,"reports.view",team["other"].id)


def test_report_export_may_be_narrower_than_view_and_senior_is_independent(db,team):
    person=team["colleague"]
    grant(db,person,[rule("reports",["view"],"all")])
    grant(db,person,[rule("reports",["export"],"department")],"本部门导出")
    report_export_access(db,person,team["dep"].id)
    for dep in [None,team["other"].id]:
        with pytest.raises(HTTPException) as e: report_export_access(db,person,dep)
        assert e.value.status_code==403
    with pytest.raises(HTTPException): senior_report_access(db,person)


def test_migration_preserves_explicit_scopes_but_not_job_titles(db,team):
    team["outsider"].position="总经理"
    db.add(EmployeeRoleGrant(user_id=team["a"].id,role="department_meetings"))
    db.add(ReportDepartmentGrant(user_id=team["b"].id,department_id=team["other"].id))
    db.flush()
    result=migrate_explicit_grants(db)
    assert result["assignments"]==2
    assert allows(db,team["a"],"meetings.edit",team["dep"].id)
    assert not allows(db,team["a"],"meetings.edit",team["other"].id)
    assert senior_report_access(db,team["b"]).departments=={team["other"].id}
    assert not direct_assignees(db,team["outsider"])


def test_versioned_role_creation_assignment_revocation_and_unknown_permissions(db,team):
    admin=team["host"];person=team["a"]
    body=RoleInput(name="报表专员",rules=[rule("reports",["view"],"selected",[team["other"].id])])
    saved=create(body,db,admin)
    assert saved["version"]==1
    result=assign(person.id,AssignmentInput(roles=[saved["id"]],expected_roles=[],expected_department_id=person.department_id),db,admin)
    assert result["roles"]==[saved["id"]]
    assert report_access(db,person).departments=={team["other"].id}
    with pytest.raises(HTTPException) as e:
        update(saved["id"],body.model_copy(update={"expected_version":3}),db,admin)
    assert e.value.status_code==409
    with pytest.raises(HTTPException):
        create(RoleInput(name="坏配置",rules=[rule("reports",["administrator"],"all")]),db,admin)
    disabled=update(saved["id"],body.model_copy(update={"active":False,"expected_version":1}),db,admin)
    assert disabled["version"]==2
    assert report_access(db,person).mode=="self"
    assign(person.id,AssignmentInput(roles=[],expected_roles=[saved["id"]],expected_department_id=person.department_id),db,admin)
    assert not db.scalar(select(BusinessRoleAssignment).where(BusinessRoleAssignment.user_id==person.id))
