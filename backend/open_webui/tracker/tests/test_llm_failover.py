"""Choosing the connection that serves TRACKER_MODEL, and moving to the next one when a connection fails."""

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer

from open_webui.tracker import llm


def _endpoint_app(models: list[str], state: dict) -> web.Application:
    async def list_models(request):
        if state.get('down'):
            return web.Response(status=502, text='pod stopped')
        return web.json_response({'data': [{'id': m} for m in models]})

    async def complete(request):
        body = await request.json()
        state.setdefault('calls', []).append(body['model'])
        if state.get('down'):
            return web.Response(status=502, text='pod stopped')
        return web.json_response({'choices': [{'message': {'content': f'ok from {body["model"]}'}}]})

    app = web.Application()
    app.router.add_get('/v1/models', list_models)
    app.router.add_post('/v1/chat/completions', complete)
    return app


@pytest_asyncio.fixture
async def endpoints(monkeypatch):
    openai_state, runpod_state = {}, {}
    openai = TestServer(_endpoint_app(['text-embedding-3-small', 'gpt-4o-mini'], openai_state))
    runpod = TestServer(_endpoint_app(['nai'], runpod_state))
    await openai.start_server()
    await runpod.start_server()

    async def get_many(*keys):
        return {
            'openai.enable': True,
            'openai.api_base_urls': [str(openai.make_url('/v1')), str(runpod.make_url('/v1'))],
            'openai.api_keys': ['k1', 'k2'],
            'openai.api_configs': {},
        }

    monkeypatch.setattr(llm.Config, 'get_many', get_many)
    monkeypatch.setattr(llm, 'TRACKER_LLM_BASE_URL', '')
    settings = {'model': 'nai', 'failover': True, 'max_concurrency': 2, 'idle_seconds': 90, 'batch_size': 30}

    async def runtime():
        return settings

    monkeypatch.setattr(llm, 'tracker_runtime', runtime)
    llm._model_cache.clear()
    llm._down_until.clear()
    yield openai_state, runpod_state, settings
    await openai.close()
    await runpod.close()


@pytest.mark.asyncio
async def test_uses_the_connection_that_serves_tracker_model(endpoints):
    openai_state, runpod_state, settings = endpoints
    assert await llm.generate_text([{'role': 'user', 'content': 'hi'}]) == 'ok from nai'
    assert runpod_state['calls'] == ['nai'] and 'calls' not in openai_state


@pytest.mark.asyncio
async def test_fails_over_to_next_connection_and_back(endpoints):
    openai_state, runpod_state, settings = endpoints
    runpod_state['down'] = True
    # nai is unavailable, so the other connection answers with its first chat model (not the embedding one).
    assert await llm.generate_text([{'role': 'user', 'content': 'hi'}]) == 'ok from gpt-4o-mini'

    runpod_state['down'] = False
    llm._model_cache.clear()
    llm._down_until.clear()  # cooldown over
    assert await llm.generate_text([{'role': 'user', 'content': 'hi'}]) == 'ok from nai'


@pytest.mark.asyncio
async def test_listed_model_down_moves_on(endpoints):
    openai_state, runpod_state, settings = endpoints
    await llm.generate_text([{'role': 'user', 'content': 'warm'}])  # model lists cached: runpod serves nai
    runpod_state['down'] = True
    assert await llm.generate_text([{'role': 'user', 'content': 'hi'}]) == 'ok from gpt-4o-mini'
    assert runpod_state['calls'][-1] == 'nai', 'tried the preferred connection first'
    # While cooling down, the failed connection is tried last.
    assert await llm.generate_text([{'role': 'user', 'content': 'again'}]) == 'ok from gpt-4o-mini'
    assert runpod_state['calls'].count('nai') == 2


@pytest.mark.asyncio
async def test_no_failover_reports_the_error(endpoints, monkeypatch):
    openai_state, runpod_state, settings = endpoints
    settings['failover'] = False
    await llm.generate_text([{'role': 'user', 'content': 'warm'}])
    runpod_state['down'] = True
    with pytest.raises(llm.TrackerLLMError, match='All LLM connections failed'):
        await llm.generate_text([{'role': 'user', 'content': 'hi'}])
    assert 'calls' not in openai_state
