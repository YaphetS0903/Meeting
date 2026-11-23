import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_report_grant_migration_is_additive_and_reversible():
    path = Path(__file__).parents[1] / "migrations/versions/20261030_report_department_grants.py"
    spec = importlib.util.spec_from_file_location("report_grant_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, name VARCHAR(120))"))
        connection.execute(text("CREATE TABLE departments (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(text("INSERT INTO users VALUES ('member', 'kept')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            schema = inspect(connection)
            assert schema.get_pk_constraint("report_department_grants")["constrained_columns"] == [
                "user_id", "department_id"
            ]
            assert len(schema.get_foreign_keys("report_department_grants")) == 2
            assert connection.scalar(text("SELECT COUNT(*) FROM report_department_grants")) == 0
            assert connection.scalar(text("SELECT name FROM users")) == "kept"
            migration.downgrade()
            assert not inspect(connection).has_table("report_department_grants")
            assert connection.scalar(text("SELECT name FROM users")) == "kept"
    engine.dispose()
