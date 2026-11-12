import logging
import re
import time

import aiohttp
from pydantic import ValidationError

from open_webui.models.config import Config
from open_webui.tracker.config import (
    TRACKER_DISABLE_THINKING,
    TRACKER_GUIDED_JSON,
    TRACKER_LLM_API_KEY,
    TRACKER_LLM_BASE_URL,
    TRACKER_LLM_TIMEOUT,
    TRACKER_MAX_TOKENS,
    TRACKER_MODEL,
)
from open_webui.tracker.schema import StateUpdate, update_json_schema
from open_webui.utils.json_codec import JSONCodec

log = logging.getLogger(__name__)

_THINK_RE = re.compile(r'<think>.*?</think>', re.DOTALL)

# Flipped off for the process lifetime once the server rejects response_format.
_guided_json_supported = TRACKER_GUIDED_JSON


class TrackerLLMError(Exception):
    pass


# Resolved model per endpoint, refreshed periodically so a pod or model swap is picked up without a restart.
_MODEL_CACHE_SECONDS = 300
_model_cache: dict[str, tuple[str, float]] = {}


async def _connection() -> tuple[str, str]:
    """(base_url, api_key): TRACKER_LLM_BASE_URL if set, else the first enabled admin OpenAI connection."""
    if TRACKER_LLM_BASE_URL:
        return TRACKER_LLM_BASE_URL, TRACKER_LLM_API_KEY

    values = await Config.get_many('openai.enable', 'openai.api_base_urls', 'openai.api_keys', 'openai.api_configs')
    if values.get('openai.enable') is False:
        raise TrackerLLMError('OpenAI connections are disabled in admin settings and TRACKER_LLM_BASE_URL is not set')
    urls = values.get('openai.api_base_urls') or []
    keys = values.get('openai.api_keys') or []
    configs = values.get('openai.api_configs') or {}
    for idx, url in enumerate(urls):
        if url and configs.get(str(idx), configs.get(url, {})).get('enable', True):
            return url.rstrip('/'), keys[idx] if idx < len(keys) else ''
    raise TrackerLLMError('No enabled OpenAI connection in admin settings and TRACKER_LLM_BASE_URL is not set')


def extract_json(text: str) -> dict:
    text = _THINK_RE.sub('', text or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
    start, end = text.find('{'), text.rfind('}')
    if start == -1 or end <= start:
        raise ValueError('No JSON object in model output')
    return JSONCodec.loads(text[start : end + 1])


async def _list_models(session: aiohttp.ClientSession, base_url: str, api_key: str) -> list[str]:
    headers = {'Authorization': f'Bearer {api_key}'} if api_key else {}
    async with session.get(f'{base_url}/models', headers=headers) as response:
        if response.status != 200:
            raise TrackerLLMError(f'GET {base_url}/models failed ({response.status})')
        body = await response.json(content_type=None)
    data = body.get('data', []) if isinstance(body, dict) else []
    return [item['id'] for item in data if isinstance(item, dict) and item.get('id')]


async def _resolve_model(session: aiohttp.ClientSession, base_url: str, api_key: str) -> str:
    """TRACKER_MODEL when the endpoint serves it; otherwise the first model the endpoint lists."""
    cached = _model_cache.get(base_url)
    if cached and cached[1] > time.monotonic():
        return cached[0]

    try:
        model_ids = await _list_models(session, base_url, api_key)
    except Exception as e:
        if TRACKER_MODEL:
            return TRACKER_MODEL  # cannot verify it; try the configured name
        raise TrackerLLMError(f'TRACKER_MODEL is not set and the model list is unavailable: {e}') from e

    if TRACKER_MODEL and TRACKER_MODEL in model_ids:
        model = TRACKER_MODEL
    elif model_ids:
        model = model_ids[0]
    elif TRACKER_MODEL:
        model = TRACKER_MODEL
    else:
        raise TrackerLLMError(f'TRACKER_MODEL is not set and {base_url}/models lists no models')

    if not cached or cached[0] != model:
        if TRACKER_MODEL and model != TRACKER_MODEL:
            log.warning('Tracker model %r is not served by %s; using %r', TRACKER_MODEL, base_url, model)
        else:
            log.info('Tracker using model %r from %s', model, base_url)
    _model_cache[base_url] = (model, time.monotonic() + _MODEL_CACHE_SECONDS)
    return model


async def _post(session: aiohttp.ClientSession, payload: dict) -> tuple[int, dict | str]:
    base_url, api_key = await _connection()
    payload = {**payload, 'model': await _resolve_model(session, base_url, api_key)}
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    async with session.post(f'{base_url}/chat/completions', data=JSONCodec.dumps(payload), headers=headers) as response:
        body = await response.text()
        try:
            return response.status, JSONCodec.loads(body)
        except Exception:
            return response.status, body


def _payload(messages: list[dict], max_tokens: int) -> dict:
    payload = {
        'messages': messages,
        'temperature': 0.1,
        'max_tokens': max_tokens,
        'stream': False,
    }
    if TRACKER_DISABLE_THINKING:
        payload['chat_template_kwargs'] = {'enable_thinking': False}
    return payload


def _content(status: int, body) -> str:
    if status != 200 or not isinstance(body, dict):
        raise TrackerLLMError(f'LLM request failed ({status}): {str(body)[:500]}')
    try:
        return _THINK_RE.sub('', body['choices'][0]['message'].get('content') or '').strip()
    except (KeyError, IndexError, TypeError):
        raise TrackerLLMError(f'Unexpected LLM response: {str(body)[:500]}')


async def generate_text(messages: list[dict], max_tokens: int = 1024) -> str:
    """Plain completion, used for file and image summaries."""
    timeout = aiohttp.ClientTimeout(total=TRACKER_LLM_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        return _content(*await _post(session, _payload(messages, max_tokens)))


async def generate_state_update(messages: list[dict]) -> StateUpdate:
    """One chat completion returning a validated StateUpdate; retries once with the validation error."""
    global _guided_json_supported

    timeout = aiohttp.ClientTimeout(total=TRACKER_LLM_TIMEOUT)
    conversation = list(messages)
    last_error = ''

    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        for attempt in range(2):
            payload = _payload(conversation, TRACKER_MAX_TOKENS)
            if _guided_json_supported:
                payload['response_format'] = {
                    'type': 'json_schema',
                    'json_schema': {'name': 'state_update', 'schema': update_json_schema()},
                }

            status, body = await _post(session, payload)
            if status == 400 and _guided_json_supported:
                log.warning('Tracker LLM rejected response_format, falling back to plain JSON: %s', body)
                _guided_json_supported = False
                payload.pop('response_format', None)
                status, body = await _post(session, payload)

            content = _content(status, body)
            try:
                return StateUpdate.model_validate(extract_json(content))
            except (ValueError, ValidationError) as e:
                last_error = str(e)[:1000]
                log.warning('Tracker LLM returned invalid state (attempt %s): %s', attempt + 1, last_error)
                conversation = [
                    *messages,
                    {'role': 'assistant', 'content': content[:4000]},
                    {
                        'role': 'user',
                        'content': f'JSON không hợp lệ: {last_error}\nHãy trả về lại đúng một đối tượng JSON.',
                    },
                ]

    raise TrackerLLMError(f'Invalid state after retry: {last_error}')
