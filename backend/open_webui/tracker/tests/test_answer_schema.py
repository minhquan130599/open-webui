"""The answer schema sent to vLLM and the example in the prompt come from one declaration (schema.ANSWER_FIELDS).

Guided decoding writes an object's fields in schema order and can skip a field but never go back to it, so a model
told another order loses the fields it reaches too late (new items once came without deadline, status or progress).
These tests keep the schema, the prompt and the models in step.
"""

import typing

from open_webui.tracker import schema
from open_webui.tracker.schema import ANSWER_FIELDS, answer_shape, update_json_schema


def _definitions(built: dict) -> dict:
    return {'StateDelta': built, **(built.get('$defs') or {})}


def test_schema_follows_declared_order():
    built = _definitions(update_json_schema(['m1', 'm2'], ['a1']))
    for name, fields in ANSWER_FIELDS.items():
        assert list(built[name]['properties']) == fields, name
        assert built[name]['additionalProperties'] is False


def test_new_item_writes_what_before_who_and_source_last():
    fields = list(_definitions(update_json_schema(['m1'], ['a1']))['NewItem']['properties'])
    for early in ('han', 'trang_thai', 'tien_do'):
        assert fields.index(early) < fields.index('giao_cho')
    assert fields[-1] == 'nguon'


def test_internal_fields_are_not_asked():
    props = _definitions(update_json_schema(['m1'], ['a1']))['NewItem']['properties']
    for internal in ('id', 'phu_trach_ids', 'phu_trach_links', 'tao_luc', 'cap_nhat_luc', 'hoan_thanh_luc'):
        assert internal not in props


def test_relation_fields_only_with_listed_items():
    props = _definitions(update_json_schema(['m1']))['NewItem']['properties']
    assert 'viec_cu' not in props and 'quan_he' not in props
    with_items = _definitions(update_json_schema(['m1'], ['a1', 'a2']))['NewItem']['properties']
    assert with_items['viec_cu']['anyOf'][0]['enum'] == ['a1', 'a2']


def test_sources_limited_to_batch_refs_and_required_for_new_items():
    built = _definitions(update_json_schema(['m1', 'm2'], ['a1']))
    assert built['NewItem']['properties']['nguon']['items']['enum'] == ['m1', 'm2']
    assert 'nguon' in built['NewItem']['required']
    assert built['ItemChange']['properties']['nguon']['items']['enum'] == ['m1', 'm2']


def test_prompt_example_lists_the_schema_fields_in_order():
    from open_webui.tracker.prompts import SYSTEM_PROMPT

    shape = answer_shape()
    assert shape in SYSTEM_PROMPT
    assert '{ANSWER_SHAPE}' not in SYSTEM_PROMPT
    new_item = shape[shape.index('"hang_muc_moi"'):shape.index('"cap_nhat"')]
    positions = [new_item.index(f'"{field}":') for field in ANSWER_FIELDS['NewItem']]
    assert positions == sorted(positions)


def test_choices_match_the_models():
    def literal(model, field):
        annotation = model.model_fields[field].annotation
        candidates = (annotation,) if typing.get_origin(annotation) is typing.Literal else typing.get_args(annotation)
        for arg in candidates:
            if typing.get_origin(arg) is typing.Literal:
                return list(typing.get_args(arg))
        return None

    choices = schema._ANSWER_CHOICES
    assert choices['trang_thai'] == literal(schema.WorkItem, 'trang_thai') == list(schema.STATUSES)
    assert choices['muc_do'] == literal(schema.Risk, 'muc_do')
    assert choices['quan_he'] == literal(schema.NewItem, 'quan_he')
    from open_webui.tracker.assign import KINDS

    assert choices['kieu'] == list(KINDS)


def test_an_answer_in_schema_order_is_read_back():
    answer = {
        'hang_muc_moi': [
            {
                'ten': 'Bàn giao thiết bị',
                'han': '2026-12-03',
                'trang_thai': 'dang_lam',
                'tien_do': 20,
                'giao_cho': {'kieu': 'ten', 'ten': ['Hương']},
                'nguon': ['m1'],
            }
        ]
    }
    delta = schema.StateDelta.model_validate(answer)
    item = delta.hang_muc_moi[0]
    assert item.han == '2026-12-03' and item.trang_thai == 'dang_lam' and item.tien_do == 20
