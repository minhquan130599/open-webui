import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

import aiohttp
from pydantic import ValidationError

from open_webui.models.config import Config
from open_webui.monitoring import recorder as perf
from open_webui.tracker.config import (
    TRACKER_DISABLE_THINKING,
    TRACKER_GUIDED_JSON,
    TRACKER_LLM_API_KEY,
    TRACKER_LLM_BASE_URL,
    TRACKER_LLM_COOLDOWN,
    TRACKER_LLM_TIMEOUT,
    TRACKER_MAX_TOKENS,
)
from open_webui.tracker.config import runtime as tracker_runtime
from open_webui.tracker.schema import StateDelta, update_json_schema
from open_webui.utils.json_codec import JSONCodec

log = logging.getLogger(__name__)

_THINK_RE = re.compile(r'<think>.*?</think>', re.DOTALL)

# Flipped off for the process lifetime once the server rejects response_format.
_guided_json_supported = TRACKER_GUIDED_JSON


class TrackerLLMError(Exception):
    pass


class TrackerOutputError(TrackerLLMError):
    """The model answered, but not with a usable state: cut at the token limit or invalid twice. Fewer messages
    per call fixes it, unlike a connection failure."""


# Model list per endpoint, refreshed periodically so a pod or model swap is picked up without a restart.
_MODEL_CACHE_SECONDS = 300
_model_cache: dict[str, tuple[Optional[list[str]], float]] = {}
# Endpoints that failed recently are tried last until this time (monotonic seconds).
_down_until: dict[str, float] = {}
# Model ids that cannot answer a chat completion; skipped when picking "the first model" of an endpoint.
_NON_CHAT = re.compile(
    r'embed|whisper|tts|dall-e|image|moderation|audio|realtime|transcri|search|rerank|davinci|babbage', re.I
)
# Failures worth trying another endpoint for: auth, missing model or route, rate limit, server errors.
_FAILOVER_STATUSES = {401, 402, 403, 404, 408, 409, 429}


@dataclass
class Endpoint:
    url: str
    key: str
    name: str  # for logs: "TRACKER_LLM_BASE_URL" or "connection #2"


async def _endpoints() -> list[Endpoint]:
    """TRACKER_LLM_BASE_URL (if set) first, then the enabled admin OpenAI connections in their order."""
    endpoints = []
    if TRACKER_LLM_BASE_URL:
        endpoints.append(Endpoint(TRACKER_LLM_BASE_URL, TRACKER_LLM_API_KEY, 'TRACKER_LLM_BASE_URL'))
        if not (await tracker_runtime())['failover']:
            return endpoints

    values = await Config.get_many('openai.enable', 'openai.api_base_urls', 'openai.api_keys', 'openai.api_configs')
    if values.get('openai.enable') is not False:
        urls = values.get('openai.api_base_urls') or []
        keys = values.get('openai.api_keys') or []
        configs = values.get('openai.api_configs') or {}
        for idx, url in enumerate(urls):
            if not url or not configs.get(str(idx), configs.get(url, {})).get('enable', True):
                continue
            url = url.rstrip('/')
            if all(e.url != url for e in endpoints):
                endpoints.append(Endpoint(url, keys[idx] if idx < len(keys) else '', f'connection #{idx + 1}'))
    if not endpoints:
        raise TrackerLLMError('No enabled OpenAI connection in admin settings and TRACKER_LLM_BASE_URL is not set')
    return endpoints


async def _list_models(session: aiohttp.ClientSession, endpoint: Endpoint) -> Optional[list[str]]:
    """Model ids the endpoint serves, or None when the list is unavailable (cached either way)."""
    cached = _model_cache.get(endpoint.url)
    if cached and cached[1] > time.monotonic():
        return cached[0]
    headers = {'Authorization': f'Bearer {endpoint.key}'} if endpoint.key else {}
    try:
        async with session.get(
            f'{endpoint.url}/models', headers=headers, timeout=aiohttp.ClientTimeout(total=15)
        ) as response:
            if response.status != 200:
                raise TrackerLLMError(f'GET /models failed ({response.status})')
            body = await response.json(content_type=None)
        data = body.get('data', []) if isinstance(body, dict) else []
        models: Optional[list[str]] = [item['id'] for item in data if isinstance(item, dict) and item.get('id')]
    except Exception as e:
        log.warning('Tracker could not list models of %s (%s): %s', endpoint.name, endpoint.url, e)
        models = None
    _model_cache[endpoint.url] = (models, time.monotonic() + _MODEL_CACHE_SECONDS)
    return models


async def _candidates(session: aiohttp.ClientSession, model: Optional[str] = None) -> list[tuple[Endpoint, str]]:
    """(endpoint, model) pairs in the order to try.

    Endpoints serving a TRACKER_MODEL come first (in the order the models are listed in TRACKER_MODEL), then
    the others with their first chat model. Endpoints that failed recently go last. Without failover only
    the best pair is returned.
    """
    settings = await tracker_runtime()
    preferred = [m.strip() for m in (model or settings['model']).split(',') if m.strip()]
    ranked: list[tuple[tuple, Endpoint, str]] = []
    now = time.monotonic()
    for order, endpoint in enumerate(await _endpoints()):
        models = await _list_models(session, endpoint)
        down = _down_until.get(endpoint.url, 0) > now
        if models is None:
            # The list is unavailable: the endpoint may still answer for a configured model name.
            if preferred:
                ranked.append(((down, 1, 0, order), endpoint, preferred[0]))
            continue
        served = [m for m in preferred if m in models]
        if served:
            ranked.append(((down, 0, preferred.index(served[0]), order), endpoint, served[0]))
            continue
        chat = [m for m in models if not _NON_CHAT.search(m)]
        if chat:
            ranked.append(((down, 2, 0, order), endpoint, chat[0]))
    ranked.sort(key=lambda entry: entry[0])
    pairs = [(endpoint, model) for _, endpoint, model in ranked]
    if not pairs:
        raise TrackerLLMError('No connection serves a usable model (check TRACKER_MODEL and the admin connections)')
    return pairs if settings['failover'] else pairs[:1]


_last_used: Optional[tuple[str, str]] = None


_TITLE_RE = re.compile(r'<title>(.*?)</title>', re.IGNORECASE | re.DOTALL)


def _short(text: str) -> str:
    """Readable error text: the <title> of an HTML error page (nginx, Cloudflare), else the first 200 chars."""
    match = _TITLE_RE.search(text or '')
    if match:
        return ' '.join(match.group(1).split())[:200]
    return ' '.join((text or '').split())[:200]


async def _read_stream(response) -> str:
    """Collect a streamed (SSE) completion into the JSON body a non-streamed call returns.

    Streaming keeps bytes flowing, so proxies that cut a request after ~100 s without a response byte
    (RunPod, Cloudflare 524) do not cut long summaries.
    """
    parts, usage, finish = [], None, None
    async for raw in response.content:
        line = raw.decode('utf-8', 'replace').strip()
        if not line.startswith('data:'):
            continue
        data = line[5:].strip()
        if data == '[DONE]':
            break
        try:
            chunk = JSONCodec.loads(data)
        except Exception:
            continue
        usage = chunk.get('usage') or usage
        for choice in chunk.get('choices') or []:
            content = (choice.get('delta') or {}).get('content')
            if content:
                parts.append(content)
            finish = choice.get('finish_reason') or finish
    return JSONCodec.dumps(
        {'choices': [{'message': {'content': ''.join(parts)}, 'finish_reason': finish}], 'usage': usage or {}}
    )


async def _post(
    session: aiohttp.ClientSession, payload: dict, model: Optional[str] = None, purpose: Optional[str] = None
) -> tuple[int, dict | str]:
    """POST /chat/completions, moving to the next endpoint when one is down, refuses or lacks the model.

    A 400 is returned to the caller (it may be about response_format) and never fails over.
    """
    global _last_used
    errors = []
    pairs = await _candidates(session, model)
    for index, (endpoint, pair_model) in enumerate(pairs):
        model = pair_model
        headers = {'Content-Type': 'application/json'}
        if endpoint.key:
            headers['Authorization'] = f'Bearer {endpoint.key}'
        timer = perf.Timer()
        try:
            async with session.post(
                f'{endpoint.url}/chat/completions',
                data=JSONCodec.dumps({**payload, 'model': model}),
                headers=headers,
            ) as response:
                status = response.status
                text = await _read_stream(response) if payload.get('stream') and status == 200 else await response.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            status, text = 0, f'{type(e).__name__}: {e}'
        timer.stop()
        failed = status == 0 or status in _FAILOVER_STATUSES or status >= 500
        _record(endpoint, model, timer, status, text, failover=index > 0, failed=failed, purpose=purpose)
        if failed:
            errors.append(f'{endpoint.name} ({model}): {status or "no response"} {_short(text)}')
            _down_until[endpoint.url] = time.monotonic() + TRACKER_LLM_COOLDOWN
            _model_cache.pop(endpoint.url, None)  # it may come back with another model list
            if index + 1 < len(pairs):
                log.warning(
                    'Tracker LLM %s failed (%s); trying %s',
                    endpoint.name,
                    status or _short(text),
                    pairs[index + 1][0].name,
                )
                continue
            raise TrackerLLMError('All LLM connections failed: ' + ' | '.join(errors))
        _down_until.pop(endpoint.url, None)
        if _last_used != (endpoint.url, model):
            log.info('Tracker using model %r from %s (%s)', model, endpoint.name, endpoint.url)
            _last_used = (endpoint.url, model)
        try:
            return status, JSONCodec.loads(text)
        except Exception:
            return status, text
    raise TrackerLLMError('No LLM connection to try')


def _record(
    endpoint: Endpoint,
    model: str,
    timer,
    status: int,
    text: str,
    failover: bool,
    failed: bool,
    purpose: Optional[str] = None,
) -> None:
    """One tracker LLM call for the monitor: endpoint, model, duration, tokens, failover.

    Calls made for another purpose (file OCR repair, file summaries) are recorded as kind 'file_llm'.
    """
    usage = {}
    if not failed:
        try:
            usage = (JSONCodec.loads(text) or {}).get('usage') or {}
        except Exception:
            usage = {}
    perf.record(
        'file_llm' if purpose else 'tracker',
        ts=timer.ts,
        source='file' if purpose else 'tracker',
        model_id=model,
        name=endpoint.name,
        status='error' if failed or status >= 400 else 'ok',
        duration_ms=timer.ms,
        input_tokens=usage.get('prompt_tokens'),
        output_tokens=usage.get('completion_tokens'),
        meta={'url': endpoint.url, 'http_status': status, 'failover': failover}
        | ({'purpose': purpose} if purpose else {})
        | ({'error': text[:300]} if failed else {}),
    )


def extract_json(text: str) -> dict:
    text = _THINK_RE.sub('', text or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
    start, end = text.find('{'), text.rfind('}')
    if start == -1 or end <= start:
        raise ValueError('No JSON object in model output')
    return JSONCodec.loads(text[start : end + 1])


def _payload(messages: list[dict], max_tokens: int, disable_thinking: Optional[bool] = None) -> dict:
    payload = {
        'messages': messages,
        'temperature': 0.1,
        'max_tokens': max_tokens,
        'stream': False,
    }
    if TRACKER_DISABLE_THINKING if disable_thinking is None else disable_thinking:
        payload['chat_template_kwargs'] = {'enable_thinking': False}
    return payload


def _content(status: int, body) -> str:
    if status != 200 or not isinstance(body, dict):
        raise TrackerLLMError(f'LLM request failed ({status}): {str(body)[:500]}')
    try:
        return _THINK_RE.sub('', body['choices'][0]['message'].get('content') or '').strip()
    except (KeyError, IndexError, TypeError):
        raise TrackerLLMError(f'Unexpected LLM response: {str(body)[:500]}')


async def generate_text(
    messages: list[dict],
    max_tokens: int = 1024,
    model: Optional[str] = None,
    purpose: Optional[str] = None,
    disable_thinking: Optional[bool] = None,
) -> str:
    """Plain completion, used for file and image summaries.

    model overrides TRACKER_MODEL (comma-separated ids, first served wins); purpose labels the call for the monitor.
    """
    payload = _payload(messages, max_tokens, disable_thinking)
    if purpose:
        # File summaries and OCR repairs can run longer than a proxy waits for a first byte.
        payload.update(stream=True, stream_options={'include_usage': True})
    timeout = aiohttp.ClientTimeout(total=TRACKER_LLM_TIMEOUT, sock_connect=20)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        return _content(*await _post(session, payload, model=model, purpose=purpose))


async def generate_state_update(messages: list[dict]) -> StateDelta:
    """One chat completion returning a validated StateDelta; retries once with the validation error."""
    global _guided_json_supported

    timeout = aiohttp.ClientTimeout(total=TRACKER_LLM_TIMEOUT, sock_connect=20)
    conversation = list(messages)
    last_error = ''

    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        for attempt in range(2):
            payload = _payload(conversation, TRACKER_MAX_TOKENS)
            # Streamed so a proxy waiting for the first byte (nginx 504 after ~60 s) does not cut a long update.
            payload.update(stream=True, stream_options={'include_usage': True})
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
            if _finish_reason(body) == 'length':
                # Cut at the token limit: asking again for the same messages is cut again.
                raise TrackerOutputError(
                    f'State update cut at {TRACKER_MAX_TOKENS} tokens ({len(content)} characters): too many messages in one call'
                )
            try:
                return StateDelta.model_validate(extract_json(content))
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

    raise TrackerOutputError(f'Invalid state after retry: {last_error}')


def _finish_reason(body) -> Optional[str]:
    try:
        return body['choices'][0].get('finish_reason')
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
