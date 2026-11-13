"""Per-person work across channels, the channel owner's team view and due reminders.

Run from backend/ like the other tracker tests (DATA_DIR pointing to a scratch directory).
"""

import datetime as dt
import time
import uuid

import pytest

from open_webui.tracker import mywork, reminders


async def _project(owner, members, items):
    from open_webui.models.channels import Channels, CreateChannelForm
    from open_webui.models.messages import MessageForm, Messages
    from open_webui.tracker import llm, service
    from open_webui.tracker.models import Trackers
    from open_webui.tracker.schema import StateDelta

    channel = await Channels.insert_new_channel(
        CreateChannelForm(name=f'mw-{uuid.uuid4().hex[:6]}', type='group'), owner.id
    )
    await Channels.add_members_to_channel(channel.id, owner.id, user_ids=[m.id for m in members])
    for member in members:
        await Messages.insert_new_message(MessageForm(content=f'{member.name} nhận việc'), channel.id, member.id)
    await Trackers.mark_dirty(channel.id)
    time.sleep(0.002)

    async def fake(messages):
        return StateDelta.model_validate({'hang_muc_moi': items, 'tom_tat_lo': 's'})

    original = llm.generate_state_update
    llm.generate_state_update = fake
    try:
        claimed = [c for c in await Trackers.claim_due(time.time_ns(), 1000, 0, 60 * 10**9, 100) if c[0] == channel.id]
        await service.process_channel(*claimed[0])
    finally:
        llm.generate_state_update = original
    return channel


def _day(offset: int) -> str:
    return (dt.date.fromisoformat(mywork.today()) + dt.timedelta(days=offset)).isoformat()


@pytest.mark.asyncio(loop_scope='module')
async def test_my_work_team_and_reminders():
    from open_webui.models.config import Config
    from open_webui.models.notifications import Notification
    from open_webui.models.users import Users
    from open_webui.internal.db import get_async_db
    from sqlalchemy import select

    await Config.upsert({'notifications.enable': True, 'channels.enable': True})
    tag = uuid.uuid4().hex[:4]
    owner = await Users.insert_new_user(str(uuid.uuid4()), f'Chủ {tag}', f'{uuid.uuid4().hex}@t.local', role='user')
    hai = await Users.insert_new_user(str(uuid.uuid4()), f'Hải {tag}', f'{uuid.uuid4().hex}@t.local', role='admin')
    vy = await Users.insert_new_user(str(uuid.uuid4()), f'Vy {tag}', f'{uuid.uuid4().hex}@t.local', role='admin')
    channel = await _project(
        owner,
        [hai, vy],
        [
            {'ten': 'Viết API đăng nhập', 'phu_trach': [f'anh Hải {tag}'], 'han': _day(0)},
            {'ten': 'Thiết kế màn hình', 'phu_trach': [f'Hải {tag}'], 'han': _day(1)},
            {'ten': 'Kiểm thử', 'phu_trach': [f'Vy {tag}'], 'han': _day(-2)},
            {'ten': 'Viết tài liệu', 'phu_trach': [f'Hải {tag}'], 'han': _day(30), 'trang_thai': 'hoan_thanh'},
            {'ten': 'Việc chưa giao'},
        ],
    )

    mine = {e.ten: e for e in await mywork.work_of(hai, hai.id) if e.channel_id == channel.id}
    assert set(mine) == {'Viết API đăng nhập', 'Thiết kế màn hình', 'Viết tài liệu'}
    assert (mine['Viết API đăng nhập'].bucket, mine['Thiết kế màn hình'].bucket) == ('today', 'week')
    assert mine['Viết tài liệu'].bucket == 'done'

    # The channel owner sees a member's work in the channels they own; a member cannot look at others.
    seen_by_owner = [e.ten for e in await mywork.work_of(owner, vy.id)]
    assert seen_by_owner == ['Kiểm thử'] and (await mywork.work_of(owner, vy.id))[0].bucket == 'overdue'
    member = await Users.insert_new_user(str(uuid.uuid4()), 'Khách', f'{uuid.uuid4().hex}@t.local', role='user')
    assert await mywork.work_of(member, vy.id) is None

    # An admin who has not joined the channel sees none of its work, for themselves or as a manager.
    outsider = await Users.insert_new_user(str(uuid.uuid4()), 'Admin', f'{uuid.uuid4().hex}@t.local', role='admin')
    assert all(e.channel_id != channel.id for e in await mywork.work_of(outsider, hai.id) or [])
    assert channel.id not in [c['id'] for c in (await mywork.team_of(outsider))['channels']]
    # An admin who is in the channel sees every member's work there.
    assert [e.ten for e in await mywork.work_of(vy, hai.id) if e.channel_id == channel.id]

    team = await mywork.team_of(owner)
    counts = {p['id']: p['counts'] for p in team['people']}
    assert counts[hai.id]['today'] == 1 and counts[hai.id]['week'] == 1 and counts[vy.id]['overdue'] == 1
    assert team['unassigned'] == 1

    created = await reminders.send_due_reminders(day_before=True)
    assert created >= 2
    assert await reminders.send_due_reminders(day_before=True) == 0, 'each reminder is sent once'
    async with get_async_db() as db:
        rows = (await db.scalars(select(Notification).where(Notification.resource_id == channel.id))).all()
    messages = sorted(r.data['message'] for r in rows if r.user_id == hai.id)
    assert len(messages) == 2 and all(channel.name in m for m in messages)
    assert any('đến hạn hôm nay' in m and 'Viết API đăng nhập' in m for m in messages)
    assert not any(r.user_id == vy.id for r in rows), 'overdue items are not due-date reminders'


@pytest.mark.asyncio(loop_scope='module')
async def test_reminder_runs_once_a_day_after_the_configured_time(monkeypatch):
    calls = []

    async def fake_send(day_before):
        calls.append(day_before)
        return 0

    monkeypatch.setattr(reminders, 'send_due_reminders', fake_send)
    await reminders.set_settings(True, '8:30', False)
    assert (await reminders.get_settings())['time'] == '08:30'
    with pytest.raises(ValueError):
        await reminders.set_settings(True, '25:00', True)
    tz = reminders.TZ
    assert not await reminders.run_if_due(dt.datetime(2030, 1, 5, 8, 0, tzinfo=tz))
    assert await reminders.run_if_due(dt.datetime(2030, 1, 5, 8, 31, tzinfo=tz))
    assert not await reminders.run_if_due(dt.datetime(2030, 1, 5, 9, 0, tzinfo=tz))
    assert calls == [False]
    await reminders.set_settings(False, '08:00', True)
    assert not await reminders.run_if_due(dt.datetime(2030, 1, 6, 9, 0, tzinfo=tz))
    await reminders.set_settings(True, '08:00', True)


@pytest.mark.asyncio(loop_scope='module')
async def test_close_marks_done_and_reopen_restores():
    from open_webui.models.users import Users
    from open_webui.tracker.models import Trackers

    owner = await Users.insert_new_user(str(uuid.uuid4()), 'Chủ', f'{uuid.uuid4().hex}@t.local', role='user')
    channel = await _project(owner, [owner], [{'ten': 'Viết báo cáo', 'tien_do': 40, 'trang_thai': 'dang_lam'}])
    item = (await Trackers.get_state(channel.id)).state['hang_muc'][0]

    assert await Trackers.archive_items(channel.id, [item['id']], 'dong', owner.id) == 1
    closed = (await Trackers.get_state(channel.id)).archive[0]
    assert (closed['trang_thai'], closed['tien_do'], closed['hoan_thanh_luc']) == ('hoan_thanh', 100, mywork.today())

    assert await Trackers.reopen_item(channel.id, item['id'])
    reopened = (await Trackers.get_state(channel.id)).state['hang_muc'][0]
    assert (reopened['trang_thai'], reopened['tien_do'], reopened['hoan_thanh_luc']) == ('dang_lam', 40, None)

    # Owner of the channel may manage the item from "my work".
    entries = await mywork.work_of(owner, owner.id)
    assert entries == [] or all(e.can_manage for e in entries if e.channel_id == channel.id)
