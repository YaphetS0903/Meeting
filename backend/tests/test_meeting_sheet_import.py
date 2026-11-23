from copy import deepcopy

import openpyxl
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import Assessment, Meeting, OutboxEvent, Task, User
from app.schemas import MeetingDraft, PublishRequest
from app.services.business import continue_meeting, create_meeting, publish, save_meeting
from app.services.meeting_sheet_import import ImportHeaders, create_from_source, recognize


@pytest.fixture
def workbook(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), 'storage_dir', tmp_path/'storage')
    path = tmp_path/'任务单.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '会议纪要'
    for row in [
        ['历史会议纪要'],
        ['会议所属部门', '技术部', None, '会议类型', '☑ 部门周例会'],
        ['会议召开日期', '2026年10月15日', None, '会议召开时间', '10点00分'],
        ['会议地点', '会议室', None, '主持人', '主持人'],
        ['参会人员', '责任人甲、责任人乙'],
        ['上次会议核心工作部署内容'],
        ['序号', '具体工作任务内容', '责任人', '计划完成时间', '任务闭环完成结果', '备注', '输出物'],
        [1, '已评定任务', '责任人甲\n责任人乙', '10/10', '已完成', '完整备注'],
        [7, '待延续任务', '责任人甲', '10/10', '未完成', '延期到10/20'],
        [12, '未知结果任务', '不存在的人', '7/31\n8/12\n10/20', '暂停', '保留原文'],
        ['本次会议正式决议事项'],
        ['会议决议'],
        ['本次会议核心工作部署内容'],
        ['序号', '具体工作任务内容', '责任人', '计划完成时间', '任务闭环完成结果', '备注', '输出物'],
        [1, '待延续任务', '责任人甲', '10/20', None, '延期安排', '报告'],
        [24, '独立新安排', '责任人乙', '10/30', None, '新备注', '图纸'],
    ]:
        ws.append(row)
    ws.merge_cells('A1:G1')
    extra = wb.create_sheet('开发计划')
    extra.append(['开发计划', '不并入任务'])
    wb.save(path)
    return path


def headers(source, team):
    p = source.candidates[0]['payload']
    return ImportHeaders(candidate_index=0,title=p['title'],meeting_date=p['meeting_date'],
                         department_id=team['dep'].id, host_id=team['host'].id,
                         recorder_id=team['host'].id,kind='department')


def imported(db, team, workbook):
    source = recognize(db,team['host'],team['series'].id,workbook)
    meeting, _ = create_from_source(db,team['host'],source.id,headers(source,team))
    return source, meeting


def correct(db, team, source, meeting, *, linked=False, not_due=False):
    draft = deepcopy(meeting.draft)
    draft['import_confirmed_issues'] = [i['id'] for i in source.candidates[0]['issues']]
    review = [i for i in draft['items'] if i['section']=='review']
    review[2].update(status='不统计', owner_ids=[team['b'].id], exclusion_reason='暂停')
    if not_due:
        review[1].update(status="未到期", due_date="2026-10-20")
    if linked:
        arrangement = next(i for i in draft['items'] if i['section']=='new')
        arrangement['historical_work_id'] = review[1]['historical_work_id']
        arrangement['source']['extended_from_item_id'] = review[1]['id']
    return save_meeting(db,team['host'],meeting.id,MeetingDraft.model_validate(draft),meeting.draft_version)


def test_recognize_and_create_only_one_draft_preserves_sections_people_and_originals(db,team,workbook):
    source, meeting = imported(db,team,workbook)
    assert not meeting.historical and meeting.published_version==0
    assert db.scalar(select(func.count()).select_from(Meeting))==1
    assert db.scalar(select(func.count()).select_from(Task))==0
    assert db.scalar(select(func.count()).select_from(Assessment))==0
    assert db.scalar(select(func.count()).select_from(OutboxEvent))==0
    rows=meeting.draft['items']
    assert len(rows)==5 and [i['section'] for i in rows]==['review']*3+['new']*2
    assert rows[0]['owner_ids']==[team['a'].id,team['b'].id]
    assert rows[1]['due_date']=='2026-10-10' and '延期到10/20' in rows[1]['notes']
    assert rows[2]['status']=='' and rows[2]['source']['raw_status']=='暂停'
    assert rows[2]['due_date']=='2026-10-20'
    assert rows[2]['source']['date_history']==['2026-07-31','2026-08-12']
    assert rows[3]['deliverable']=='报告' and rows[4]['notes']=='新备注'
    codes={i['code'] for i in source.candidates[0]['issues']}
    assert {'date_note_conflict','invalid_status','owner_unmatched','continuation_check'} <= codes


def test_publish_requires_confirmation_then_only_current_tasks_dispatch_and_assess_once(db,team,workbook):
    source, meeting=imported(db,team,workbook)
    with pytest.raises(HTTPException, match='待核对'):
        publish(db,team['host'],meeting.id,PublishRequest(expected_version=1,notify=False))
    meeting=correct(db,team,source,meeting,linked=True)
    publish(db,team['host'],meeting.id,PublishRequest(expected_version=2,notify=False))
    assert db.scalar(select(func.count()).select_from(Task))==2
    assert db.scalar(select(func.count()).select_from(Assessment))==4
    assert not list(db.scalars(select(OutboxEvent).where(OutboxEvent.channel.in_(['mail','wecom','native_todo']))))
    next_meeting=continue_meeting(db,team['host'],meeting.id)
    assert len(next_meeting.draft['items'])==2
    assert all(i['status']=='' for i in next_meeting.draft['items'])


def test_unlinked_review_can_continue_and_publish_without_a_fake_history_meeting(db,team,workbook):
    source,meeting=imported(db,team,workbook)
    meeting=correct(db,team,source,meeting,not_due=True)
    publish(db,team['host'],meeting.id,PublishRequest(expected_version=2,notify=False))
    next_meeting=continue_meeting(db,team['host'],meeting.id)
    reviews=deepcopy(next_meeting.draft)
    assert len(reviews['items'])==3
    unlinked=next(i for i in reviews['items'] if not i['task_id'])
    assert unlinked['source']['sheet_import_id']==source.id
    for row in reviews['items']:
        row['status']='未完成'
    next_meeting=save_meeting(db,team['host'],next_meeting.id,MeetingDraft.model_validate(reviews),1)
    publish(db,team['host'],next_meeting.id,PublishRequest(expected_version=2,notify=False))
    assert db.scalar(select(func.count()).select_from(Meeting).where(Meeting.historical.is_(True)))==0


def test_retry_and_reupload_do_not_create_duplicate_meetings(db,team,workbook):
    source,meeting=imported(db,team,workbook)
    again,duplicate=create_from_source(db,team['host'],source.id,headers(source,team))
    assert duplicate and again.id==meeting.id
    another=recognize(db,team['host'],team['series'].id,workbook)
    again,duplicate=create_from_source(db,team['host'],another.id,headers(another,team))
    assert duplicate and again.id==meeting.id
    assert db.scalar(select(func.count()).select_from(Meeting))==1


def test_import_permissions_and_provenance_cannot_be_forged(db,team,workbook):
    with pytest.raises(HTTPException):
        recognize(db,team['outsider'],team['series'].id,workbook)
    source,meeting=imported(db,team,workbook)
    with pytest.raises(HTTPException):
        create_from_source(db,team['outsider'],source.id,headers(source,team))
    payload=deepcopy(meeting.draft)
    with pytest.raises(HTTPException,match='导入入口'):
        create_meeting(db,team['host'],MeetingDraft.model_validate(payload))
    payload['sheet_import_id']=None
    with pytest.raises(HTTPException,match='来源不能'):
        save_meeting(db,team['host'],meeting.id,MeetingDraft.model_validate(payload),1)
    meeting=correct(db,team,source,meeting)
    payload=deepcopy(meeting.draft)
    payload['items'][0]['historical_work_id']='forged'
    meeting=save_meeting(db,team['host'],meeting.id,MeetingDraft.model_validate(payload),2)
    with pytest.raises(HTTPException,match='追溯编号'):
        publish(db,team['host'],meeting.id,PublishRequest(expected_version=3,notify=False))


def test_ambiguous_same_name_never_guessed_even_in_department(db,team,workbook):
    db.add(User(name='责任人甲',department_id=team['other'].id))
    db.flush()
    source=recognize(db,team['host'],team['series'].id,workbook)
    row=source.candidates[0]['payload']['items'][0]
    assert row['owner_ids']==[team['b'].id]
    assert any(i['code']=='owner_unmatched' and i['raw']=='责任人甲' for i in source.candidates[0]['issues'])


def test_api_upload_errors_creation_commit_and_selected_source_only(db, team, workbook):
    import asyncio

    import httpx

    from app.core.db import get_db
    from app.core.security import current_user
    from app.main import app
    from app.models import MeetingSheetImport

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: team['host']

    async def requests():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://isolated.test') as client:
            endpoint = f"/api/series/{team['series'].id}/task-sheet-imports"
            for name, content in [('bad.pdf', b'x'), ('empty.xlsx', b''), ('bad.xlsx', b'bad')]:
                response = await client.post(endpoint, files={'file': (name, content)})
                assert response.status_code == 422
            response = await client.post(endpoint, files={'file': (workbook.name, workbook.read_bytes())})
            assert response.status_code == 200
            source_id = response.json()['id']
            source = db.get(MeetingSheetImport, source_id)
            source.candidates = [*source.candidates, {**deepcopy(source.candidates[0]), 'sheet': 'private second meeting'}]
            db.commit()
            body = headers(source, team).model_dump(mode='json')
            invalid = await client.post(f'/api/meeting-sheet-imports/{source_id}/draft', json={**body, 'candidate_index': 99})
            assert invalid.status_code == 422
            assert db.scalar(select(func.count()).select_from(Meeting)) == 0
            response = await client.post(f'/api/meeting-sheet-imports/{source_id}/draft', json=body)
            assert response.status_code == 200
            with db.get_bind().connect() as connection:
                assert connection.scalar(select(Meeting.id)) == response.json()['id']
            result = await client.get(f'/api/meeting-sheet-imports/{source_id}')
            assert len(result.json()['candidates']) == 1
            assert result.json()['selected_index'] == 0
            app.dependency_overrides[current_user] = lambda: team['outsider']
            assert (await client.get(f'/api/meeting-sheet-imports/{source_id}')).status_code == 403
    try:
        asyncio.run(requests())
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_delete_imported_draft_retains_original_and_allows_retry(db, team, workbook):
    from fastapi import BackgroundTasks

    from app.main import DeleteMeetingRequest, delete_meeting
    from app.models import Attachment
    source, meeting = imported(db, team, workbook)
    old_id = meeting.id
    delete_meeting(old_id, BackgroundTasks(), DeleteMeetingRequest(expected_draft_version=1), db, team['host'])
    assert source.meeting_id is None and source.fingerprint is None
    assert db.get(Attachment, source.attachment_id).meeting_id is None
    assert db.get(Meeting, old_id) is None
    new_meeting, duplicate = create_from_source(db, team['host'], source.id, headers(source, team))
    assert not duplicate and new_meeting.id != old_id


def test_taskless_review_protects_source_and_series_deletion_keeps_provenance(db, team, workbook):
    from fastapi import BackgroundTasks

    from app.main import DeleteSeriesRequest, delete_series, deletion_impact, series_deletion_impact
    from app.models import Attachment, MeetingSeries
    source, meeting = imported(db, team, workbook)
    meeting = correct(db, team, source, meeting, not_due=True)
    publish(db, team['host'], meeting.id, PublishRequest(expected_version=2, notify=False))
    followup = continue_meeting(db, team['host'], meeting.id)
    taskless = next(i for i in followup.draft['items'] if not i['task_id'])
    followup.draft = {**followup.draft, 'items': [taskless]}
    db.flush()
    assert deletion_impact(meeting.id, db, team['host'])['blocking_meeting_count'] == 1
    series_id = team['series'].id
    impact = series_deletion_impact(series_id, db, team['host'])
    assert impact['can_delete']
    delete_series(series_id, DeleteSeriesRequest(impact_token=impact['impact_token'], confirmed_name=impact['name']),
                  BackgroundTasks(), db, team['host'])
    db.expire_all()
    assert db.get(MeetingSeries, series_id) is None
    assert source.series_id is None and source.meeting_id is None
    assert db.get(Attachment, source.attachment_id) is not None


def test_multiple_dates_survive_excel_export_with_history_struck_through():
    from io import BytesIO

    from app.services.exports import xlsx_minutes
    snapshot = dict(title='日期核对', meeting_date='2026-10-15', items=[dict(
        section='review', content='原日期', owner_names=['甲'], due_date='2026-10-20',
        status='未完成', source={'date_history': ['2026-07-31', '2026-08-12']},
    )])
    book = openpyxl.load_workbook(BytesIO(xlsx_minutes(snapshot)), rich_text=True)
    value = book['上期任务回顾']['E5'].value
    assert str(value) == '2026-07-31\n2026-08-12\n2026-10-20'
    assert value[0].font.strike and value[2].font.strike and not value[-1].font.strike
