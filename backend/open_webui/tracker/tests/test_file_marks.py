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
