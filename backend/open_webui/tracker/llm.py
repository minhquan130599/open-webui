import logging
import re

import aiohttp
from pydantic import ValidationError

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


def is_configured() -> bool:
    return bool(TRACKER_LLM_BASE_URL and TRACKER_MODEL)


def extract_json(text: str) -> dict:
    text = _THINK_RE.sub('', text or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
    start, end = text.find('{'), text.rfind('}')
    if start == -1 or end <= start:
        raise ValueError('No JSON object in model output')
    return JSONCodec.loads(text[start : end + 1])


async def _post(session: aiohttp.ClientSession, payload: dict) -> tuple[int, dict | str]:
    headers = {'Content-Type': 'application/json'}
    if TRACKER_LLM_API_KEY:
        headers['Authorization'] = f'Bearer {TRACKER_LLM_API_KEY}'
    async with session.post(
        f'{TRACKER_LLM_BASE_URL}/chat/completions', data=JSONCodec.dumps(payload), headers=headers
    ) as response:
        body = await response.text()
        try:
            return response.status, JSONCodec.loads(body)
        except Exception:
            return response.status, body


async def generate_state_update(messages: list[dict]) -> StateUpdate:
    """One chat completion returning a validated StateUpdate; retries once with the validation error."""
    global _guided_json_supported

    if not is_configured():
        raise TrackerLLMError('TRACKER_LLM_BASE_URL and TRACKER_MODEL must be set')

    timeout = aiohttp.ClientTimeout(total=TRACKER_LLM_TIMEOUT)
    conversation = list(messages)
    last_error = ''

    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        for attempt in range(2):
            payload = {
                'model': TRACKER_MODEL,
                'messages': conversation,
                'temperature': 0.1,
                'max_tokens': TRACKER_MAX_TOKENS,
                'stream': False,
            }
            if TRACKER_DISABLE_THINKING:
                payload['chat_template_kwargs'] = {'enable_thinking': False}
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

            if status != 200 or not isinstance(body, dict):
                raise TrackerLLMError(f'LLM request failed ({status}): {str(body)[:500]}')

            try:
                content = body['choices'][0]['message'].get('content') or ''
            except (KeyError, IndexError, TypeError):
                raise TrackerLLMError(f'Unexpected LLM response: {str(body)[:500]}')

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
