"""Run the real cutover revision against a fresh legacy-shaped database."""
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.models import Base, BusinessRole, BusinessRoleAssignment, Department, EmployeeRoleGrant, ReportDepartmentGrant, User


def test_alembic_upgrade_persists_roles_and_legacy_scope_once(tmp_path):
    url="sqlite:///"+(tmp_path/"migration.sqlite").as_posix()
    engine=create_engine(url)
    Base.metadata.create_all(engine,tables=[t for t in Base.metadata.sorted_tables if t.name not in {"business_roles","business_role_assignments"}])
    with Session(engine) as db:
        dep=Department(name="源部门");db.add(dep);db.flush()
        a=User(name="显式授权",department_id=dep.id)
        b=User(name="仅有职务",department_id=dep.id,position="总经理")
        db.add_all([a,b]);db.flush()
        a.manual_leader_department_ids=[dep.id]
        db.add(EmployeeRoleGrant(user_id=a.id,role="department_meetings"))
        db.add(ReportDepartmentGrant(user_id=a.id,department_id=dep.id))
        identifier,other=a.id,b.id
        db.commit()
    env={**os.environ,"DATABASE_URL":url,"PYTHONIOENCODING":"utf-8"}
    cwd=Path(__file__).resolve().parents[1]
    def run(*args):
        result=subprocess.run([sys.executable,"-m","alembic",*args],cwd=cwd,env=env,capture_output=True,text=True,encoding="utf-8")
        assert result.returncode==0,result.stdout+result.stderr
    run("stamp","20261105_operation_logs")
    run("upgrade","head")
    with Session(engine) as db:
        roles=list(db.scalars(select(BusinessRole)))
        assert len(roles)==5
        assert len(list(db.scalars(select(BusinessRoleAssignment).where(BusinessRoleAssignment.user_id==identifier))))==3
        assert not db.scalar(select(BusinessRoleAssignment).where(BusinessRoleAssignment.user_id==other))
        assert db.scalar(text("SELECT version_num FROM alembic_version"))=="20261110_custom_roles"
    run("upgrade","head")
    with Session(engine) as db:
        assert len(list(db.scalars(select(BusinessRole))))==5
    engine.dispose()
