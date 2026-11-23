import importlib.util
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest

from app.schemas import MeetingDraft, PublishRequest
from app.services.business import create_meeting, publish, save_meeting

script = Path(__file__).resolve().parents[3] / ".validation/backdated-meeting-link-20261111/oct26-conversion/convert.py"
spec = importlib.util.spec_from_file_location("oct26_converter", script)
converter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(converter)


@pytest.mark.parametrize("selected_result", [False, True])
def test_existing_online_draft_converts_without_touching_later_work(db, team, draft_factory, selected_result):
    later = create_meeting(db, team["host"], draft_factory())
    publish(db, team["host"], later.id, PublishRequest(expected_version=1, notify=False))
    draft = MeetingDraft.model_validate(later.draft)
    draft.meeting_date = date(2026, 8, 5)
    draft.items[0].section = "review"
    draft.items[0].status = "已完成" if selected_result else ""
    draft.items[0].notes = "历史备注完整保留"
    older = create_meeting(db, team["host"], draft)
    before = deepcopy(older.draft)
    preserved = converter.encoded(converter.protected(db, older.series_id, older.id))
    if selected_result:
        with pytest.raises(AssertionError, match="manually reassessed"):
            converter.prepare(db, older, later, 1)
        assert older.draft == before
        return
    converted = converter.prepare(db, older, later, 1)
    assert older.draft == before
    save_meeting(db, team["host"], older.id, converted, older.draft_version)
    db.flush()
    assert older.historical and older.draft["history_backfill"]
    assert older.published_version == 0 and older.has_draft
    assert older.draft["items"][0]["section"] == "new"
    assert older.draft["items"][0]["notes"] == "历史备注完整保留"
    assert older.draft["items"][0]["task_id"] is None
    assert older.draft["items"][0]["backfill_reference"]["task_id"] == before["items"][0]["task_id"]
    assert converter.encoded(converter.protected(db, older.series_id, older.id)) == preserved
