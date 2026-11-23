"""The deploy cutover only revokes the confirmed legacy grants, atomically."""
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import select, text
from app.models import AuditLog, ReportDepartmentGrant


@pytest.fixture
def cutover(db, team):
    path = Path(__file__).parents[3] / '.validation/employee-roles-20261103/manage-legacy.py'
    spec = importlib.util.spec_from_file_location('legacy_cutover', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.EXPECTED = {team['a'].id: [team['dep'].id], team['b'].id: [team['other'].id]}
    for key in ('a','b'):
        team[key].manual_leader_department_ids = module.EXPECTED[team[key].id]
    db.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)'))
    db.execute(text("INSERT INTO alembic_version VALUES ('20261030_report_grants')"))
    db.add(ReportDepartmentGrant(user_id=team['colleague'].id, department_id=team['other'].id))
    db.commit()
    return module, module.snapshot(db)


def test_cutover_revokes_and_restores_only_old_grants_with_audit(db, team, cutover):
    module, before = cutover
    assert module.update(db, before)['status']=='revoked'
    assert team['a'].manual_leader_department_ids == team['b'].manual_leader_department_ids == []
    assert module.reports(db) == before['report_grants']
    logs = list(db.scalars(select(AuditLog).where(AuditLog.reason==module.REASON)))
    assert len(logs)==2 and all(row.actor_id==team['host'].id for row in logs)
    assert {row.entity_id for row in logs}==set(module.EXPECTED)
    assert module.update(db,before,restore=True)['status']=='restored'
    assert team['a'].manual_leader_department_ids == [team['dep'].id]
    assert team['b'].manual_leader_department_ids == [team['other'].id]
    assert module.reports(db) == before['report_grants']


def test_cutover_stale_grant_stops_without_partial_revocation(db, team, cutover):
    module, before = cutover
    team['b'].manual_leader_department_ids = [team['dep'].id]
    db.flush()
    with pytest.raises(AssertionError, match='Legacy grant changed'):
        module.update(db,before)
    assert team['a'].manual_leader_department_ids == [team['dep'].id]
    assert not list(db.scalars(select(AuditLog)))


def test_failed_cutover_does_not_restore_unrelated_changes(db, team, cutover):
    module, before = cutover
    assert module.update(db,before,restore=True)['status']=='no_revocation_to_restore'
    module.update(db,before)
    team['a'].manual_leader_department_ids = [team['other'].id]
    db.flush()
    with pytest.raises(AssertionError, match='Legacy grant changed'):
        module.update(db,before,restore=True)
    assert team['a'].manual_leader_department_ids == [team['other'].id]
    assert team['b'].manual_leader_department_ids == []
