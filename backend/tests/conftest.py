import os
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models import Base, Department, MeetingSeries, User
from app.schemas import MeetingDraft, MeetingItem


@pytest.fixture
def db():
    url = os.getenv("TEST_DATABASE_URL")
    schema = "test_" + uuid4().hex
    if url:
        root = create_engine(url)
        with root.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    else:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
        session.rollback()
    engine.dispose()
    if url:
        with root.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        root.dispose()


@pytest.fixture
def team(db):
    dep, other = Department(name="技术部"), Department(name="财务部")
    db.add_all([dep, other])
    db.flush()
    host = User(name="主持人", username="host", department_id=dep.id, is_admin=True)
    a = User(name="责任人甲", department_id=dep.id)
    b = User(name="责任人乙", department_id=dep.id)
    colleague = User(name="同部门员工", department_id=dep.id)
    outsider = User(name="外部门员工", department_id=other.id)
    db.add_all([host, a, b, colleague, outsider])
    db.flush()
    series = MeetingSeries(name="技术部周例会", department_id=dep.id)
    db.add(series)
    db.flush()
    return dict(
        dep=dep, other=other, host=host, a=a, b=b, colleague=colleague, outsider=outsider, series=series
    )


@pytest.fixture
def draft_factory(team):
    def build(section="new", status="未到期", when=date(2026, 8, 10), **item_args):
        item = MeetingItem(
            section=section,
            status=status,
            content="完成检验流程修订",
            owner_ids=[team["a"].id, team["b"].id],
            due_date=date(2026, 8, 17),
            **item_args,
        )
        return MeetingDraft(
            title="技术部周例会",
            department_id=team["dep"].id,
            series_id=team["series"].id,
            host_id=team["host"].id,
            recorder_id=team["host"].id,
            meeting_date=when,
            items=[item],
        )

    return build
