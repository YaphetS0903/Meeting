"""Deletion acknowledgements must describe committed, externally visible state."""
import asyncio

import httpx
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from app.core import db as database
from app.core.security import current_user
from app.main import app
from app.models import Base, Meeting
from app.services.business import create_meeting


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        "sqlite:///" + (tmp_path / "deletion.db").as_posix(),
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


@pytest.mark.parametrize("fail_commit", [False, True])
def test_delete_commits_before_http_success(
    db, team, draft_factory, monkeypatch, fail_commit,
):
    meeting = create_meeting(db, team["host"], draft_factory())
    identifier, version = meeting.id, meeting.draft_version
    db.commit()

    class RequestSession(Session):
        def commit(self):
            if fail_commit:
                raise RuntimeError("isolated commit failure")
            return super().commit()

    monkeypatch.setattr(database, "SessionLocal", sessionmaker(
        bind=db.get_bind(), class_=RequestSession, expire_on_commit=False,
    ))
    original = dict(app.dependency_overrides)
    app.dependency_overrides[current_user] = lambda: team["host"]
    observations = []

    async def observed_app(scope, receive, send):
        async def observe(message):
            if message["type"] == "http.response.start":
                # A separate connection sees only committed state.
                with db.get_bind().connect() as connection:
                    present = connection.scalar(select(Meeting.id).where(Meeting.id == identifier))
                observations.append((message["status"], present is not None))
            await send(message)

        await app(scope, receive, observe)

    async def request():
        transport = httpx.ASGITransport(app=observed_app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://isolated.test") as client:
            return await client.request("DELETE", "/api/meetings/" + identifier, json={
                "expected_draft_version": version,
            })

    try:
        response = asyncio.run(request())
        if fail_commit:
            assert response.status_code == 500
            assert observations == [(500, True)]
        else:
            assert response.status_code == 200
            assert response.json()["deleted"] is True
            assert observations == [(200, False)]
        with db.get_bind().connect() as connection:
            present = connection.scalar(select(Meeting.id).where(Meeting.id == identifier))
        assert (present is not None) is fail_commit
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original)
