"""Is the model busy? Asked before writing the comments of a report, so reports are written in a quiet moment and
do not slow down the chats.

First the vLLM metrics of the endpoint (requests waiting in its queue); when the endpoint does not expose them
(a proxy in front), a one-token request: a model that takes longer than the limit to answer it is busy.
"""

import asyncio
import logging
import re
import time
from typing import Optional

import aiohttp

from open_webui.tracker import llm

log = logging.getLogger(__name__)

_METRIC = re.compile(r'^vllm:num_requests_(waiting|running)(?:\{[^}]*\})?\s+([0-9.eE+-]+)\s*$', re.M)
_METRICS_RETRY_SECONDS = 600
# Endpoints without readable metrics are not asked for them again until this time (monotonic seconds).
_no_metrics: dict[str, float] = {}


async def _metrics(session: aiohttp.ClientSession, url: str, headers: dict) -> Optional[tuple[float, float]]:
    """(waiting, running) requests of the vLLM server, or None when not available."""
    if _no_metrics.get(url, 0) > time.monotonic():
        return None
    base = re.sub(r'/v1/?$', '', url.rstrip('/'))
    try:
        async with session.get(f'{base}/metrics', headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
            text = await response.text() if response.status == 200 else ''
    except (aiohttp.ClientError, asyncio.TimeoutError):
        text = ''
    counts = {'waiting': 0.0, 'running': 0.0}
    found = False
    for kind, value in _METRIC.findall(text):
        try:
            counts[kind] += float(value)
            found = True
        except ValueError:
            pass
    if not found:
        _no_metrics[url] = time.monotonic() + _METRICS_RETRY_SECONDS
        return None
    return counts['waiting'], counts['running']


async def _probe(session: aiohttp.ClientSession, url: str, model: str, headers: dict, limit: int) -> dict:
    """Time to the first token of a one-token request."""
    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': 'OK'}],
        'max_tokens': 1,
        'temperature': 0,
        'stream': True,
        'chat_template_kwargs': {'enable_thinking': False},
    }
    started = time.monotonic()

    async def first_token() -> int:
        async with session.post(f'{url}/chat/completions', json=payload, headers=headers) as response:
            if response.status != 200:
                return response.status
            async for raw in response.content:
                if raw.startswith(b'data:'):
                    break
            return 200

    try:
        status = await asyncio.wait_for(first_token(), timeout=limit)
    except asyncio.TimeoutError:
        return {'busy': True, 'source': 'probe', 'reason': f'LLM trả lời chậm: quá {limit} giây chưa có token đầu'}
    except aiohttp.ClientError as error:
        return {'busy': True, 'source': 'probe', 'reason': f'Không gọi được LLM: {type(error).__name__}'}
    seconds = time.monotonic() - started
    if status != 200:
        return {'busy': True, 'source': 'probe', 'reason': f'LLM trả lỗi HTTP {status}'}
    return {'busy': False, 'source': 'probe', 'reason': f'LLM rảnh: token đầu sau {seconds:.1f} giây'}


async def status(model: Optional[str], waiting_limit: int, probe_seconds: int) -> dict:
    """{'busy': bool, 'reason': text for the monitor, 'source': 'metrics' | 'probe' | 'none'}."""
    timeout = aiohttp.ClientTimeout(total=probe_seconds + 30, sock_connect=10)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        try:
            endpoint, model_id = (await llm._candidates(session, model))[0]
        except Exception as error:
            return {'busy': True, 'source': 'none', 'reason': f'Chưa có kết nối LLM dùng được: {error}'}
        headers = {'Authorization': f'Bearer {endpoint.key}'} if endpoint.key else {}
        counts = await _metrics(session, endpoint.url, headers)
        if counts is not None:
            waiting, running = counts
            busy = waiting > waiting_limit
            return {
                'busy': busy,
                'source': 'metrics',
                'reason': f'LLM {"bận" if busy else "rảnh"}: {waiting:g} request đang chờ, {running:g} đang chạy',
            }
        return await _probe(session, endpoint.url, model_id, headers, probe_seconds)
