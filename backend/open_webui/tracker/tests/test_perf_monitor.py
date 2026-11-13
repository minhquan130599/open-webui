"""Performance events: recording, stream timing, response/tool events, and the monitor API key check."""

import asyncio

import pytest
from sqlalchemy import select

from open_webui.monitoring import llm as perf_llm
from open_webui.monitoring import recorder as perf


async def _rows(kind: str, **where):
    from open_webui.internal.db import get_async_db

    await perf.flush()
    async with get_async_db() as db:
        query = select(perf.PerfEvent).where(perf.PerfEvent.kind == kind)
        for key, value in where.items():
            query = query.where(getattr(perf.PerfEvent, key) == value)
        return (await db.scalars(query)).all()


class _User:
    id = 'u-perf'


@pytest.mark.asyncio(loop_scope='module')
async def test_stream_records_ttft_tokens_and_speed():
    async def provider():
        yield b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
        await asyncio.sleep(0.05)
        yield b'data: {"choices":[{"delta":{"content":"Xin"}}]}\n\n'
        await asyncio.sleep(0.05)
        # usage line split across two chunks
        yield b'data: {"choices":[],"usage":{"prompt_tokens":12,'
        yield b'"completion_tokens":30}}\n\ndata: [DONE]\n\n'

    ctx = perf_llm.context({'chat_id': 'channel:abc', 'message_id': 'm1'}, _User(), 'nai', 'http://x:8000/v1')
    chunks = [c async for c in perf_llm.wrap_stream(provider(), ctx, perf.Timer())]
    assert len(chunks) == 4
    row = (await _rows('llm_call', message_id='m1'))[0]
    assert (row.source, row.model_id, row.input_tokens, row.output_tokens) == ('channel', 'nai', 12, 30)
    assert 40 <= row.ttft_ms < row.duration_ms and row.meta['tokens_per_second'] > 0


@pytest.mark.asyncio(loop_scope='module')
async def test_response_and_tool_events():
    output = [
        {'type': 'function_call', 'name': 'build_project_report', 'duration': 2.3, 'status': 'completed'},
        {'type': 'function_call', 'name': 'search_project_evidence', 'duration': 0.4, 'status': 'completed'},
        {
            'type': 'function_call',
            'call_id': 'c3',
            'name': 'search_project_evidence',
            'arguments': '{"project": "OSINT3"}',
            'duration': 0.5,
            'status': 'failed',
        },
        {
            'type': 'function_call_output',
            'call_id': 'c3',
            'status': 'failed',
            'output': [{'type': 'input_text', 'text': 'Project not found: OSINT3'}],
        },
        {'type': 'message', 'content': []},
    ]
    meta = {'chat_id': 'chat-1', 'message_id': 'm2', 'perf_started_ms': perf.now_ms() - 3000}
    perf_llm.record_response(meta, _User(), 'nai', output, {'input_tokens': 100, 'output_tokens': 50})
    response = (await _rows('response', message_id='m2'))[0]
    assert response.count == 3 and response.duration_ms >= 3000 and response.source == 'chat'
    assert response.meta['tools']['search_project_evidence'] == {'count': 2, 'ms': 900, 'errors': 1}
    tools = await _rows('tool', message_id='m2')
    assert sorted((t.name, t.duration_ms, t.status) for t in tools) == [
        ('build_project_report', 2300, 'ok'),
        ('search_project_evidence', 400, 'ok'),
        ('search_project_evidence', 500, 'error'),
    ]
    failed = next(t for t in tools if t.status == 'error')
    assert failed.meta['error'] == 'Project not found: OSINT3' and 'OSINT3' in failed.meta['arguments']


def test_in_flight_counter_counts_each_call_once():
    before, _ = perf_llm.in_flight_sample()
    a = perf_llm.context({}, _User(), 'nai', 'http://h/v1')
    b = perf_llm.context({}, _User(), 'nai', 'http://h/v1')
    current, peak = perf_llm.in_flight_sample()
    assert current == before + 2 and peak >= before + 2
    timer = perf.Timer()
    perf_llm.record(a, timer)
    perf_llm.record(a, timer)  # recorded twice by mistake: released once
    assert perf_llm.in_flight_sample()[0] == before + 1
    perf_llm.record(b, perf.Timer())
    assert perf_llm.in_flight_sample() == (before, before + 1), 'the peak since the last sample is kept once'
    assert perf_llm.in_flight_sample() == (before, before)


@pytest.mark.asyncio(loop_scope='module')
async def test_task_source_and_record_never_raises():
    ctx = perf_llm.context({'task': 'title_generation', 'chat_id': 'c'}, _User(), 'nai', 'http://h/v1')
    assert ctx['source'] == 'task' and ctx['name'] == 'title_generation'
    perf.record('llm_call', duration_ms='not a number')  # bad input is swallowed, not raised


def test_monitor_api_requires_key(monkeypatch):
    from fastapi import HTTPException

    from open_webui.monitoring import router

    monkeypatch.setattr(router, 'MONITOR_API_KEY', '')
    with pytest.raises(HTTPException) as disabled:
        router._check('anything')
    assert disabled.value.status_code == 404
    monkeypatch.setattr(router, 'MONITOR_API_KEY', 'secret-key')
    with pytest.raises(HTTPException) as wrong:
        router._check('nope')
    assert wrong.value.status_code == 401
    router._check('secret-key')
    assert router._mask_url('https://user:pass@api.example.com:8443/v1?key=1') == 'https://api.example.com:8443/v1'


@pytest.mark.asyncio(loop_scope='module')
async def test_tracker_settings_from_database_and_watch_apply():
    from open_webui.models.config import Config
    from open_webui.monitoring import config_watch
    from open_webui.tracker import config as tracker_config

    tracker_config.reset_runtime_cache()
    defaults = await tracker_config.runtime()
    await Config.upsert(
        {'tracker.model': 'nai,gpt-4o-mini', 'tracker.max_concurrency': 5, 'tracker.llm_failover': False}
    )
    assert (await tracker_config.runtime()) == defaults, 'cached until the watcher applies the change'

    class _App:
        class state:
            faster_whisper_model = object()

    await config_watch.apply(_App, {'tracker', 'stt'})
    settings = await tracker_config.runtime()
    assert (settings['model'], settings['max_concurrency'], settings['failover']) == ('nai,gpt-4o-mini', 5, False)
    assert _App.state.faster_whisper_model is None, 'whisper is reloaded with the new model on next use'
    await Config.upsert({'tracker.model': '', 'tracker.max_concurrency': None, 'tracker.llm_failover': None})
    tracker_config.reset_runtime_cache()
    assert (await tracker_config.runtime())['model'] == defaults['model'], 'empty value falls back to .env'


def test_totp_codes_work_once_and_secret_is_encrypted():
    import pyotp

    from open_webui.monitoring.server import security

    secret = security.new_secret()
    token = security.encrypt_secret(secret, 'key-1')
    assert secret not in token and security.decrypt_secret(token, 'key-1') == secret
    assert security.decrypt_secret(token, 'other-key') is None
    code = pyotp.TOTP(secret).now()
    step = security.verify_code(secret, code, -1)
    assert step is not None and security.verify_code(secret, code, step) is None, 'a used code is rejected'
    assert security.verify_code(secret, '12345', -1) is None


@pytest.mark.asyncio(loop_scope='module')
async def test_connection_change_clears_model_caches():
    from open_webui.monitoring import config_watch

    class _App:
        class state:
            redis = None
            BASE_MODELS = ['old']
            OPENAI_MODELS = {'old': {}}
            MODELS = {'old': {}}

    await config_watch.apply(_App, {'connections'})
    assert _App.state.BASE_MODELS == [] and _App.state.OPENAI_MODELS == {} and _App.state.MODELS == {}


@pytest.mark.asyncio(loop_scope='module')
async def test_reindex_runs_once_in_background(monkeypatch):
    from open_webui.models.config import Config
    from open_webui.models.users import Users
    from open_webui.monitoring import reindex
    from open_webui.routers import knowledge, memories

    if not await Users.get_super_admin_user():
        await Users.insert_new_user('admin-reindex', 'Admin', 'admin-reindex@t.local', role='admin')
    calls = []
    gate = asyncio.Event()

    async def files(request, user, db):
        calls.append('files')
        await gate.wait()

    async def search(request, user):
        calls.append('search')

    async def mems(request, user):
        calls.append('memories')

    monkeypatch.setattr(knowledge, 'reindex_knowledge_files', files)
    monkeypatch.setattr(knowledge, 'reindex_knowledge_base_metadata_embeddings', search)
    monkeypatch.setattr(memories, 'reindex_memories_from_vector_db', mems)
    await Config.upsert({reindex.STATE_KEY: {}})

    class _App:
        class state:
            pass

    first = await reindex.start(_App)
    assert first['state'] == 'running'
    second = await reindex.start(_App)
    assert second.get('already_running'), 'a second run is refused while one is going'
    gate.set()
    await _App.state.monitor_reindex_task
    state = await Config.get(reindex.STATE_KEY)
    assert state['state'] == 'done' and calls == ['files', 'search', 'memories']


@pytest.mark.asyncio(loop_scope='module')
async def test_model_public_private_and_default_policy():
    from open_webui.models.config import Config
    from open_webui.models.models import Models
    from open_webui.models.users import Users
    from open_webui.monitoring import model_access
    from open_webui.utils import models as model_utils

    if not await Users.get_super_admin_user():
        await Users.insert_new_user('admin-models', 'Admin', 'admin-models@t.local', role='admin')

    class _App:
        class state:
            pass

    # A connection model without a row becomes public by creating the row with a public grant.
    assert (await model_access.set_public(_App, 'pod-model-x', True, 'Pod X'))['public'] is True
    assert (await Models.get_model_by_id('pod-model-x')).name == 'Pod X'
    assert (await model_access.set_public(_App, 'pod-model-x', False))['public'] is False
    assert (await model_access.set_public(_App, 'pod-model-x', True))['public'] is True

    # Default policy: the database value wins over DEFAULT_MODEL_ACCESS; public_all covers every connection.
    await Config.upsert({model_utils.DEFAULT_ACCESS_KEY: 'public_all'})
    assert await model_utils.default_model_access() == 'public_all'
    models = [
        {'id': 'conn1-model', 'owned_by': 'openai', 'urlIdx': 0},
        {'id': 'conn2-model', 'owned_by': 'openai', 'urlIdx': 1},
    ]
    custom = await Models.get_all_models()
    assert await model_utils.register_default_public_models(models, custom, all_connections=True)
    for model_id in ('conn1-model', 'conn2-model'):
        row = await Models.get_model_by_id(model_id)
        assert any(g.principal_id == '*' for g in row.access_grants)
    await Config.upsert({model_utils.DEFAULT_ACCESS_KEY: ''})
    assert await model_utils.default_model_access() == ''
