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
        {'type': 'function_call', 'name': 'search_project_evidence', 'duration': 0.5, 'status': 'failed'},
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
