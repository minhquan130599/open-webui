import time
import uuid

import pytest

from open_webui.tracker.schema import ProjectState, drop_marked_files


def _state():
    return ProjectState.model_validate(
        {
            'hang_muc': [
                {'id': 'a', 'ten': 'Chỉ từ tệp', 'nguon': ['msg:m1', 'file:f1', 'table:s:1']},
                {'id': 'b', 'ten': 'Tệp và tin khác', 'nguon': ['msg:m1', 'file:f1', 'msg:m2']},
                {'id': 'c', 'ten': 'Không liên quan', 'nguon': ['msg:m2']},
                {'id': 'd', 'ten': 'Tệp khác', 'nguon': ['msg:m3', 'file:f2']},
            ]
        }
    )


def test_marked_file_drops_only_items_it_alone_backs():
    state = _state()
    removed = drop_marked_files(state, {'f1': {'msg': 'm1'}})

    assert removed == ['Chỉ từ tệp']
    kept = {i.ten: i.nguon for i in state.hang_muc}
    assert kept['Tệp và tin khác'] == ['msg:m1', 'msg:m2'], 'other messages keep the item, only the file goes'
    assert kept['Không liên quan'] == ['msg:m2']
    assert kept['Tệp khác'] == ['msg:m3', 'file:f2'], 'another file is untouched'


def test_no_marks_changes_nothing():
    state = _state()
    before = [list(i.nguon) for i in state.hang_muc]
    assert drop_marked_files(state, {}) == []
    assert [list(i.nguon) for i in state.hang_muc] == before


def test_message_of_another_marked_file_keeps_the_item():
    """An item citing f1 and the message of another marked file (f3, attached to m5) is backed by m5's text too."""
    state = ProjectState.model_validate(
        {'hang_muc': [{'id': 'a', 'ten': 'Việc', 'nguon': ['msg:m1', 'file:f1', 'msg:m5']}]}
    )
    removed = drop_marked_files(state, {'f1': {'msg': 'm1'}, 'f3': {'msg': 'm5'}})

    assert removed == []
    assert state.hang_muc[0].nguon == ['msg:m1', 'msg:m5']


def test_file_name_said_without_spaces_matches():
    from open_webui.tracker.service import _name_matches, _name_words

    def matches(said, file_name):
        return _name_matches(_name_words(said), said, _name_words(file_name), file_name)

    assert matches('file kế hoạch T11', 'KeHoach_T11.xlsx')
    assert matches('Ke hoach Q4', 'ke_hoach_q4_final.docx')
    assert not matches('kế hoạch T12', 'KeHoach_T11.xlsx')
    assert not matches('file abc', 'KeHoach_T11.xlsx'), 'too short to match inside another name'


@pytest.mark.asyncio
async def test_mark_set_during_a_run_holds_at_commit():
    """The run read no marks and still has the file's item; the mark set meanwhile drops it when the run commits."""
    from open_webui.tracker.models import Trackers

    channel_id = str(uuid.uuid4())
    await Trackers.mark_dirty(channel_id)
    claimed = [
        c
        for c in await Trackers.claim_due(
            now=time.time_ns(), batch_size=1000, idle_ns=0, lease_ns=60 * 10**9, max_active=1000
        )
        if c[0] == channel_id
    ]
    assert claimed, 'channel should be due'
    lease_id = claimed[0][1]

    await Trackers.set_file_mark(channel_id, 'f1', {'msg': 'm1', 'ten': 'tl.pdf'}, 'm1')
    run_state = _state().model_dump()
    assert await Trackers.complete(channel_id, lease_id, state=run_state, watermark=0, edit_watermark=0)

    names = {i['ten'] for i in (await Trackers.get_state(channel_id)).state['hang_muc']}
    assert 'Chỉ từ tệp' not in names and 'Tệp và tin khác' in names
