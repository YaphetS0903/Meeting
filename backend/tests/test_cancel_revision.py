from copy import deepcopy

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models import Assessment, Attachment, AuditLog, MeetingAttendee, MeetingSeries, MeetingVersion, OutboxEvent, Task, TaskOwner, TaskVersion
from app.schemas import MeetingDraft, PublishRequest
from app.services.business import cancel_revision, continue_meeting, create_meeting, publish, revision, save_meeting
from app.services.meeting_views import revision_in_progress
from tests.test_report_permissions import client  # noqa: F401
from tests.test_report_self_only import auth


def issued(db, team, draft_factory):
    draft = draft_factory()
    draft.recorder_id = team['colleague'].id
    draft.attendee_ids = [team['a'].id, team['b'].id]
    draft.recipient_ids = [team['a'].id]
    meeting = create_meeting(db, team['host'], draft)
    publish(db, team['host'], meeting.id, PublishRequest(expected_version=1, notify=False))
    revision(db, team['host'], meeting.id, '准备修订')
    return meeting


def records(db):
    db.flush()
    return {model.__tablename__: [{c.name: deepcopy(getattr(row, c.name)) for c in model.__table__.columns}
            for row in db.scalars(select(model))] for model in
            [MeetingVersion, Assessment, Task, TaskOwner, TaskVersion, MeetingAttendee, Attachment, OutboxEvent]}


def test_cancel_restores_published_content_without_task_or_delivery_changes(db, team, draft_factory, client):
    meeting = issued(db, team, draft_factory)
    snapshot = deepcopy(meeting.draft)
    draft = MeetingDraft.model_validate(snapshot)
    draft.items[0].content = '不要发布的工作修改'
    draft.attendee_ids = []
    draft.recipient_ids = []
    save_meeting(db, team['host'], meeting.id, draft, meeting.draft_version)
    before = records(db)
    version = meeting.draft_version
    response = client.post(f'/api/meetings/{meeting.id}/revision/cancel',
                           headers=auth(team['colleague']), json={'expected_version': version})
    assert response.status_code == 200, response.text
    assert response.json()['has_draft'] is False
    assert meeting.draft == snapshot
    assert meeting.draft_version == version + 1
    assert meeting.published_version == 1
    assert records(db) == before
    assert not revision_in_progress(meeting, snapshot)
    log = db.scalar(select(AuditLog).where(AuditLog.action == 'meeting.revision.cancel'))
    assert log.actor_id == team['colleague'].id
    assert log.before['items'][0]['content'] == '不要发布的工作修改'
    assert log.after['items'][0]['content'] == snapshot['items'][0]['content']
    # A stale autosave cannot recreate the cancelled draft.
    with pytest.raises(HTTPException) as error:
        save_meeting(db, team['host'], meeting.id, draft, version)
    assert error.value.status_code == 409
    next_meeting = continue_meeting(db, team['host'], meeting.id)
    assert next_meeting.draft['attendee_ids'] == snapshot['attendee_ids']
    assert next_meeting.draft['items'][0]['content'] == snapshot['items'][0]['content']
    revision(db, team['host'], meeting.id, '以后仍可修订')
    assert meeting.has_draft
    assert not revision_in_progress(meeting, snapshot)


def test_cancel_preserves_effective_title_and_series(db, team, draft_factory):
    meeting = issued(db, team, draft_factory)
    series = MeetingSeries(name='已调整的归档系列', department_id=team['dep'].id)
    db.add(series)
    db.flush()
    meeting.title = '已生效的显示名称'
    meeting.series_id = series.id
    cancel_revision(db, team['host'], meeting.id, meeting.draft_version)
    assert meeting.draft['title'] == meeting.title
    assert meeting.draft['series_id'] == series.id
    assert db.scalar(select(MeetingVersion)).snapshot['title'] != meeting.title


@pytest.mark.parametrize('actor', ['a', 'outsider'])
def test_cancel_requires_existing_edit_permission(db, team, draft_factory, client, actor):
    meeting = issued(db, team, draft_factory)
    before = (deepcopy(meeting.draft), meeting.draft_version, meeting.has_draft)
    response = client.post(f'/api/meetings/{meeting.id}/revision/cancel',
                           headers=auth(team[actor]), json={'expected_version': meeting.draft_version})
    assert response.status_code == 403
    assert (meeting.draft, meeting.draft_version, meeting.has_draft) == before


def test_cancel_rejects_stale_version_and_preserves_other_editor_changes(db, team, draft_factory, client):
    meeting = issued(db, team, draft_factory)
    version = meeting.draft_version
    draft = MeetingDraft.model_validate(meeting.draft)
    draft.decisions = '另一位编制人刚保存的内容'
    save_meeting(db, team['colleague'], meeting.id, draft, version)
    response = client.post(f'/api/meetings/{meeting.id}/revision/cancel',
                           headers=auth(team['host']), json={'expected_version': version})
    assert response.status_code == 409
    assert meeting.has_draft
    assert meeting.draft['decisions'] == draft.decisions


@pytest.mark.parametrize('state', ['new', 'published', 'missing_snapshot'])
def test_cancel_requires_a_published_meeting_with_revision(db, team, draft_factory, state):
    meeting = create_meeting(db, team['host'], draft_factory())
    if state != 'new':
        publish(db, team['host'], meeting.id, PublishRequest(expected_version=1, notify=False))
    if state == 'missing_snapshot':
        revision(db, team['host'], meeting.id, '准备修改')
        meeting.published_version = 2
    before = (deepcopy(meeting.draft), meeting.draft_version, meeting.has_draft)
    with pytest.raises(HTTPException) as error:
        cancel_revision(db, team['host'], meeting.id, meeting.draft_version)
    assert error.value.status_code == 409
    assert (meeting.draft, meeting.draft_version, meeting.has_draft) == before
