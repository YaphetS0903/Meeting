"""Fixtures for the new role API used by existing workflow regression tests."""
from uuid import uuid4

from app.core.security import issue_token
from app.models import BusinessRole, BusinessRoleAssignment
from app.services.role_migration import rule
from app.services.role_permissions import ACTION_KEYS


def grant(db,person,rules,name=None):
    role=BusinessRole(name=name or "回归角色 "+uuid4().hex[:8],rules=rules)
    db.add(role);db.flush()
    db.add(BusinessRoleAssignment(user_id=person.id,role_id=role.id));db.flush()
    return role


def report_grant(db,person,department,senior=True):
    modules=["reports","senior_reports"] if senior else ["reports"]
    return grant(db,person,[rule(m,["view","export"],"selected",[department.id]) for m in modules])


def dispatch_grant(db,person,scope="department"):
    return grant(db,person,[rule("dispatch",["create","view","edit","withdraw","transfer"],scope)])


DEFINITIONS={
    "department_reports":[rule("reports",["view","export"])],
    "department_meetings":[rule("series",["view","create"]),rule("meetings",sorted(ACTION_KEYS["meetings"]-{"rename","move"})),
        rule("files",sorted(ACTION_KEYS["files"])),rule("overview",["view"]),
        rule("dispatch",["create","view","edit","withdraw"])],
    "company_dispatch":[rule("dispatch",["create","view","edit","withdraw"],"all"),rule("overview",["view"],"self")],
}


def change_fixed_fixture(client,actor,target,roles,previous=(),**overrides):
    """Use editable role definitions; the old fixed-role endpoint is retired."""
    headers={"Authorization":"Bearer "+issue_token(actor)}
    response=client.get("/api/admin/roles",headers=headers)
    if response.status_code!=200:return response
    by_name={r["name"]:r for r in response.json()}
    mapped={}
    for key,rules in DEFINITIONS.items():
        name="回归预设 "+key
        if name not in by_name:
            response=client.post("/api/admin/roles",headers=headers,json={"name":name,"rules":rules})
            if response.status_code!=201:return response
            by_name[name]=response.json()
        mapped[key]=by_name[name]["id"]
    rows=client.get("/api/admin/role-assignments",headers=headers).json()
    current=next((set(r["roles"]) for r in rows if r["user_id"]==target.id),set())
    others=current-set(mapped.values())
    body={"roles":sorted(others|{mapped.get(r,r) for r in roles}),
        "expected_roles":sorted(others|{mapped.get(r,r) for r in previous}),"expected_department_id":target.department_id}
    body.update({k:v for k,v in overrides.items() if k!="reason"})
    return client.put(f"/api/admin/users/{target.id}/business-roles",headers=headers,json=body)
