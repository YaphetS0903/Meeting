from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.main import DirectAssignmentCreate, create_direct_assignment, direct_assignment_options
from app.models import OutboxEvent, SystemSetting


@pytest.mark.parametrize('global_enabled,direct_enabled,expected', [
    (False, False, 'skipped'), (False, True, 'pending'), (True, False, 'pending'),
])
def test_dispatch_response_reports_actual_queue_state(db, team, monkeypatch,
                                                     global_enabled, direct_enabled, expected):
    monkeypatch.setattr(get_settings(), 'wecom_direct_notifications_enabled', direct_enabled)
    monkeypatch.setattr(get_settings(), 'native_todo_app_enabled', False)
    db.add(SystemSetting(key='reminders', value={'enabled': global_enabled}))
    db.flush()
    options = direct_assignment_options(db, team['host'])
    assert options['wecom_notifications_enabled'] == (global_enabled or direct_enabled)
    command = DirectAssignmentCreate(idempotency_key='delivery-state-test', content='通知状态验证',
        owner_ids=[team['a'].id], verifier_id=team['host'].id, due_date=date.today() + timedelta(days=30))
    first = create_direct_assignment(command, db, team['host'])
    assert first['wecom_notification_state'] == expected
    event = db.scalar(select(OutboxEvent).where(OutboxEvent.event_key == f"task:{first['id']}:assigned"))
    assert event.state == expected
    if expected == 'pending':
        event.state = 'sent'  # Simulated worker acknowledgement; no provider is contacted.
        db.flush()
    replay = create_direct_assignment(command, db, team['host'])
    assert replay['id'] == first['id']
    assert replay['wecom_notification_state'] == event.state
