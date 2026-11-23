import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


def test_employee_role_schema_is_additive_constrained_and_reversible():
    path = Path(__file__).parents[1] / "migrations/versions/20261103_employee_roles.py"
    spec = importlib.util.spec_from_file_location("employee_roles_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, name VARCHAR(120))"))
        connection.execute(text("INSERT INTO users VALUES ('member', 'kept')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert inspect(connection).get_pk_constraint("employee_role_grants")["constrained_columns"] == ["user_id", "role"]
            connection.execute(text("INSERT INTO employee_role_grants VALUES ('member', 'department_reports')"))
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(text("INSERT INTO employee_role_grants VALUES ('member', 'arbitrary_admin')"))
            migration.downgrade()
            assert not inspect(connection).has_table("employee_role_grants")
            assert connection.scalar(text("SELECT name FROM users")) == "kept"
    engine.dispose()
