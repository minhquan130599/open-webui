"""Tracker tests. Run from backend/ with DATA_DIR pointing to a scratch directory (a SQLite DB is created):

DATA_DIR=/tmp/owui-test WEBUI_SECRET_KEY=test USE_SLIM_DOCKER=true python -m pytest open_webui/tracker/tests -q
"""

import time
import uuid

import pytest
import pytest_asyncio

from open_webui.tracker import llm, service
from open_webui.tracker.prompts import is_trivial
from open_webui.tracker.people import Person, match_person
from open_webui.tracker.prompts import PromptMessage
from open_webui.tracker.schema import (
    ProjectState,
    StateDelta,
    WorkItem,
    apply_delta,
    load_state,
    normalize_key,
    reconcile,
    render_state_markdown,
    state_for_prompt,
)

# Pure logic


def test_is_trivial():
    assert is_trivial('ok', [])
    assert is_trivial('Oke!!', [])
    assert is_trivial('👍', [])
    assert is_trivial('  ', [])
    assert not is_trivial('ok 50%', [])
    assert not is_trivial('ok', ['bao_cao.pdf'])
    assert not is_trivial('API đăng nhập xong rồi', [])


def test_work_item_coercion():
    item = WorkItem.model_validate(
        {'ten': ' Đăng nhập ', 'trang_thai': 'done', 'tien_do': '60%', 'han': 'next week', 'phu_trach': 'Lan'}
    )
    assert item.ten == 'Đăng nhập'
    assert item.trang_thai == 'hoan_thanh'
    assert item.tien_do == 60
    assert item.han is None
    assert item.phu_trach == ['Lan']
    assert WorkItem.model_validate({'ten': 'x', 'tien_do': 250, 'trang_thai': '???'}).model_dump()['tien_do'] == 100
    assert WorkItem.model_validate({'ten': 'x', 'trang_thai': '???'}).trang_thai == 'dang_lam'


def test_unnamed_entries_are_dropped_and_text_stripped():
    state = ProjectState.model_validate(
        {
            'hang_muc': [{'ten': '  '}, {'ten': 'API'}],
            'rui_ro': [{'noi_dung': ' Trễ hạn ', 'muc_do': 'HIGH'}, {'noi_dung': None}],
        }
    )
    assert [i.ten for i in state.hang_muc] == ['API']
    assert state.rui_ro[0].noi_dung == 'Trễ hạn' and state.rui_ro[0].muc_do == 'cao'
    assert len(state.rui_ro) == 1


TODAY = '2026-11-12'


def _state(*items):
    return ProjectState(hang_muc=[WorkItem(**item) for item in items])


def test_apply_delta_updates_by_id_and_keeps_unmentioned_items():
    old = _state(
        {'id': 'a1', 'ten': 'Module đăng nhập', 'tien_do': 30, 'nguon': ['msg:old']},
        {'id': 'b2', 'ten': 'Báo cáo tuần', 'tien_do': 10},
    )
    delta = StateDelta.model_validate(
        {
            'cap_nhat': [{'id': '#a1', 'tien_do': '80%', 'nguon': ['m1', 'm9']}, {'id': 'zz', 'tien_do': 5}],
            'hang_muc_moi': [{'ten': 'Thiết kế database', 'phu_trach': ['Lan'], 'nguon': ['m2']}],
            'tom_tat_lo': 'x',
        }
    )
    state, merged = apply_delta(old, delta, {'m1': 'msg:a', 'm2': 'msg:b'}, TODAY)
    items = {i.ten: i for i in state.hang_muc}
    assert items['Module đăng nhập'].tien_do == 80
    assert items['Module đăng nhập'].nguon == ['msg:old', 'msg:a'], 'm9 is not in the batch'
    assert items['Báo cáo tuần'].tien_do == 10, 'an item the delta does not mention is kept'
    new = items['Thiết kế database']
    assert new.id and new.id not in ('a1', 'b2') and new.tao_luc == TODAY and new.nguon == ['msg:b']
    assert len(state.hang_muc) == 3 and not merged, 'unknown id zz is ignored'


def test_apply_delta_dedupes_merges_and_respects_archive():
    old = _state({'id': 'a1', 'ten': 'API login'}, {'id': 'b2', 'ten': 'Đăng nhập API'})
    delta = StateDelta.model_validate(
        {
            'hang_muc_moi': [
                {'ten': 'api LOGIN', 'trang_thai': 'done'},  # same as a1: updates it, no duplicate
                {'ten': 'Màn hình cũ'},  # closed by the owner: not resurrected
            ],
            'gop': [{'vao': 'a1', 'tu': ['b2']}],
        }
    )
    state, merged = apply_delta(old, delta, {}, TODAY, frozenset({normalize_key('Màn hình cũ')}))
    assert [i.id for i in state.hang_muc] == ['a1'] and merged == {'b2'}
    assert state.hang_muc[0].trang_thai == 'hoan_thanh' and state.hang_muc[0].hoan_thanh_luc == TODAY


def test_apply_delta_handles_many_items():
    old = _state(*({'id': f'i{n}', 'ten': f'Việc {n}'} for n in range(150)))
    state, _ = apply_delta(old, StateDelta.model_validate({'cap_nhat': [{'id': 'i149', 'tien_do': 5}]}), {}, TODAY)
    assert len(state.hang_muc) == 150 and state.hang_muc[-1].tien_do == 5
    assert '#i0 |' in state_for_prompt(state) and '#i149 |' in state_for_prompt(state)


def test_reconcile_keeps_open_items_and_archive_wins():
    computed = {'hang_muc': [{'id': 'a', 'ten': 'A'}, {'id': 'b', 'ten': 'B'}]}
    current = {'hang_muc': [{'id': 'a', 'ten': 'A'}, {'id': 'c', 'ten': 'C reopened'}, {'id': 'd', 'ten': 'D'}]}
    result = reconcile(computed, current, [{'id': 'b', 'ten': 'B'}], merged_away={'d'})
    assert [i['id'] for i in result['hang_muc']] == ['a', 'c'], 'b archived meanwhile, c kept, d merged away'


def test_match_person():
    people = [
        Person('u1', 'Nguyễn Văn Long'),
        Person('u2', 'Trần Thị Lan', 'lan.tran@ncs.vn'),
        Person('u3', 'Lê Minh Hùng'),
        Person('u4', 'Phạm Văn Hùng'),
    ]
    assert match_person('Long', people) == 'u1'
    assert match_person('anh Long', people) == 'u1'
    assert match_person('Văn Long', people) == 'u1'
    assert match_person('trần thị lan', people) == 'u2'
    assert match_person('Hùng', people) is None, 'two Hùng: not guessed'
    assert match_person('Minh Hùng', people) == 'u3'
    assert match_person('lantran', people) == 'u2'
    assert match_person('Bình', people) is None


def test_long_message_is_read_in_parts():
    from open_webui.tracker.config import TRACKER_MESSAGE_MAX_CHARS
    from open_webui.tracker.service import _prompt_groups, _split_long

    long_text = ''.join(f'Việc {n}; ' for n in range(TRACKER_MESSAGE_MAX_CHARS // 4))
    messages = [PromptMessage(id='x', created_at=1, user_name='Lan', content=long_text)]
    parts = _split_long(messages)
    assert len(parts) > 1 and ''.join(p.content for p in parts) == long_text, 'nothing is cut'
    assert parts[0].part == f'1/{len(parts)}'
    assert sum(len(g) for g in _prompt_groups(parts)) == len(parts)


def test_normalize_key():
    assert normalize_key('Đăng  nhập!') == normalize_key('dang nhap')


def test_extract_json_handles_think_and_fences():
    text = '<think>hmm</think>\n```json\n{"trang_thai": {"tong_quan": "a"}, "tom_tat_lo": "b"}\n```'
    assert llm.extract_json(text)['tom_tat_lo'] == 'b'
    with pytest.raises(ValueError):
        llm.extract_json('no json here')


def test_render_and_load_state():
    state = load_state({'tong_quan': 'Tốt', 'hang_muc': [{'ten': 'API', 'tien_do': 50, 'han': '2026-11-20'}]})
    text = render_state_markdown(state)
    assert 'Tổng quan: Tốt' in text and 'API' in text and '50%' in text and '2026-11-20' in text
    assert load_state({'hang_muc': 'broken'}).hang_muc == []


# Integration against the real tables (SQLite)


@pytest_asyncio.fixture(loop_scope='module')
async def project():
    from open_webui.models.channels import Channels, CreateChannelForm
    from open_webui.models.users import Users

    user = await Users.insert_new_user(str(uuid.uuid4()), 'Lan', f'{uuid.uuid4().hex}@test.local', role='admin')
    channel = await Channels.insert_new_channel(CreateChannelForm(name=f'AV-{uuid.uuid4().hex[:6]}'), user.id)
    return user, channel


async def _post(channel_id: str, user_id: str, content: str, **extra):
    from open_webui.models.messages import MessageForm, Messages
    from open_webui.tracker.models import Trackers

    message = await Messages.insert_new_message(MessageForm(content=content, **extra), channel_id, user_id)
    await Trackers.mark_dirty(channel_id)
    time.sleep(0.002)  # distinct created_at
    return message


async def _claim(max_active: int = 10):
    from open_webui.tracker.models import Trackers

    return await Trackers.claim_due(
        now=time.time_ns(), batch_size=1000, idle_ns=0, lease_ns=60 * 10**9, max_active=max_active
    )


@pytest.mark.asyncio(loop_scope='module')
async def test_end_to_end(project, monkeypatch):
    from open_webui.models.messages import MessageForm, Messages
    from open_webui.tracker.models import Trackers
    from open_webui.tracker.tools import get_project_status, list_my_projects

    user, channel = project
    m1 = await _post(channel.id, user.id, 'Module đăng nhập xong UI, API khoảng 50%')
    await _post(channel.id, user.id, 'ok')
    m3 = await _post(channel.id, user.id, 'Hạn demo là 2026-11-20')
    await _post(channel.id, user.id, 'đang trả lời', meta={'model_id': 'qwen'})  # model reply: not evidence

    prompts = []

    async def fake_update(messages):
        prompts.append(messages[-1]['content'])
        return StateDelta.model_validate(
            {
                'tong_quan': 'Đang làm đăng nhập',
                'hang_muc_moi': [
                    {
                        'ten': 'Module đăng nhập',
                        'phu_trach': ['Lan'],
                        'tien_do': 50,
                        'han': '2026-11-20',
                        'nguon': ['m1', 'm2', 'm7'],
                    }
                ],
                'tom_tat_lo': 'UI xong, API 50%',
            }
        )

    monkeypatch.setattr(llm, 'generate_state_update', fake_update)

    claimed = [c for c in await _claim() if c[0] == channel.id]
    assert len(claimed) == 1
    assert channel.id not in [c[0] for c in await _claim()], 'leased channel must not be claimed twice'

    await service.process_channel(*claimed[0])

    prompt = prompts[0]
    assert '[m1]' in prompt and '[m2]' in prompt and '[m3]' not in prompt
    assert ': ok' not in prompt and 'đang trả lời' not in prompt

    row = await Trackers.get_state(channel.id)
    item = row.state['hang_muc'][0]
    assert item['tien_do'] == 50
    assert item['nguon'] == [f'msg:{m1.id}', f'msg:{m3.id}'], 'm7 is not in the batch and must be dropped'
    assert item['id'] and item['phu_trach_ids'] == [user.id], 'owner name linked to the channel member'
    assert row.dirty_at is None and row.pending_count == 0
    assert row.watermark >= m3.created_at
    items = await Trackers.get_items(channel.id, 0, time.time_ns())
    assert items[0].summary == 'UI xong, API 50%'

    # New message after processing shows up in the tail, unless excluded
    m5 = await _post(channel.id, user.id, 'API đăng nhập xong rồi')
    context = await service.build_channel_context(channel.id)
    assert 'Module đăng nhập' in context and 'API đăng nhập xong rồi' in context
    assert 'API đăng nhập xong rồi' not in await service.build_channel_context(channel.id, {m5.id})
    assert (await Trackers.get_state(channel.id)).force, 'asking should prioritise the pending channel'

    # Edit of an already processed message is re-sent, marked as edited
    time.sleep(0.002)
    await Messages.update_message_by_id(m1.id, MessageForm(content='Đính chính: API mới 30%'))
    await Trackers.mark_dirty(channel.id)
    claimed = [c for c in await _claim() if c[0] == channel.id]
    await service.process_channel(*claimed[0])
    assert '(đã sửa)' in prompts[1] and 'API mới 30%' in prompts[1] and 'API đăng nhập xong rồi' in prompts[1]

    # Tools respect access and return the state
    status = await get_project_status(channel.name, __user__={'id': user.id})
    assert f'Dự án: {channel.name}' in status and 'Module đăng nhập' in status
    assert channel.name in await list_my_projects(__user__={'id': user.id})
    outsider = {'id': str(uuid.uuid4())}
    assert 'not found' in (await get_project_status(channel.name, __user__=outsider)).lower()

    # Inside another channel the answer is visible to its members: tools only see that channel.
    from open_webui.models.channels import Channels, CreateChannelForm

    shared = await Channels.insert_new_channel(CreateChannelForm(name=f'shared-{uuid.uuid4().hex[:6]}'), user.id)
    in_shared = {'__user__': {'id': user.id}, '__chat_id__': f'channel:{shared.id}'}
    listed = await list_my_projects(**in_shared)
    assert shared.name in listed and channel.name not in listed
    assert f'Dự án: {shared.name}' in await get_project_status(channel.name, **in_shared)


@pytest.mark.asyncio(loop_scope='module')
async def test_failure_backs_off_and_lease_is_checked(project, monkeypatch):
    from open_webui.tracker.models import Trackers

    user, channel = project
    await _post(channel.id, user.id, 'Bắt đầu thiết kế database')

    async def boom(messages):
        raise llm.TrackerLLMError('vLLM down')

    monkeypatch.setattr(llm, 'generate_state_update', boom)
    claimed = [c for c in await _claim() if c[0] == channel.id]
    await service.process_channel(*claimed[0])

    row = await Trackers.get_state(channel.id)
    assert row.fail_count == 1 and 'vLLM down' in row.last_error
    assert row.dirty_at is not None, 'failed work stays queued'
    assert channel.id not in [c[0] for c in await _claim()], 'backoff delays the retry'

    assert not await Trackers.complete(channel.id, 'stale-lease', state={}, watermark=0, edit_watermark=0)


@pytest.mark.asyncio(loop_scope='module')
async def test_concurrency_cap(project):
    from open_webui.models.channels import Channels, CreateChannelForm
    from open_webui.tracker.models import Trackers

    user, channel = project
    await _post(channel.id, user.id, 'Viết tài liệu SRS')
    other = await Channels.insert_new_channel(CreateChannelForm(name='Other'), user.id)
    await _post(other.id, user.id, 'Chuẩn bị môi trường test')

    first = await _claim(max_active=1)
    assert len(first) <= 1
    assert await _claim(max_active=1) == [], 'cap counts leases held by other workers'
    for channel_id, lease_id in first:
        await Trackers.fail(channel_id, lease_id, 'released by test')


@pytest.mark.asyncio(loop_scope='module')
async def test_archive_survives_a_concurrent_run(project, monkeypatch):
    from open_webui.models.channels import Channels, CreateChannelForm
    from open_webui.tracker.models import Trackers

    user, _ = project
    channel = await Channels.insert_new_channel(CreateChannelForm(name=f'arc-{uuid.uuid4().hex[:6]}'), user.id)
    await _post(channel.id, user.id, 'Việc A xong, việc B đang làm')

    async def first(messages):
        return StateDelta.model_validate(
            {'hang_muc_moi': [{'ten': 'Việc A', 'trang_thai': 'done'}, {'ten': 'Việc B'}], 'tom_tat_lo': 's'}
        )

    monkeypatch.setattr(llm, 'generate_state_update', first)
    await service.process_channel(*[c for c in await _claim() if c[0] == channel.id][0])
    items = {i['ten']: i['id'] for i in (await Trackers.get_state(channel.id)).state['hang_muc']}

    # The owner closes A while a run that started before the close is still computing.
    await _post(channel.id, user.id, 'Việc B 60%, việc C mới')
    claimed = [c for c in await _claim() if c[0] == channel.id][0]

    async def second(messages):
        assert await Trackers.archive_items(channel.id, [items['Việc A']], 'dong', user.id) == 1
        return StateDelta.model_validate(
            {'cap_nhat': [{'id': items['Việc B'], 'tien_do': 60}], 'hang_muc_moi': [{'ten': 'Việc C'}]}
        )

    monkeypatch.setattr(llm, 'generate_state_update', second)
    await service.process_channel(*claimed)
    row = await Trackers.get_state(channel.id)
    assert sorted(i['ten'] for i in row.state['hang_muc']) == ['Việc B', 'Việc C'], 'closed item stays closed'
    assert [e['ten'] for e in row.archive] == ['Việc A'] and row.archive[0]['archive_kind'] == 'dong'

    assert await Trackers.reopen_item(channel.id, items['Việc A'])
    row = await Trackers.get_state(channel.id)
    assert 'Việc A' in [i['ten'] for i in row.state['hang_muc']] and not row.archive
