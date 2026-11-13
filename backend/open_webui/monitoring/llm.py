"""Timing of each call to an OpenAI-compatible provider (kind 'llm_call').

Every model request passes through routers/openai.generate_chat_completion: chat answers and their tool
rounds, channel @mentions, background tasks (titles, tags, follow-ups). One event per provider call with the
time to first token, total time, tokens and the generation speed.
"""

import os
import re
import time
from typing import AsyncIterator, Optional

from open_webui.monitoring import recorder as perf
from open_webui.monitoring.recorder import Timer  # noqa: F401  (used by routers/openai.py)
from open_webui.utils.json_codec import JSONCodec

# Ask streaming providers for token counts on every call (needed for per-user/per-model token statistics).
REQUEST_USAGE = os.getenv('PERF_REQUEST_USAGE', 'True').lower() == 'true'

# A delta that carries generated text, reasoning or a tool call: the first one is the time to first token.
_TOKEN_RE = re.compile(rb'"(?:content|reasoning_content|reasoning|arguments)"\s*:\s*"[^"]')


# Provider calls running right now in this worker (token -> start time), and the highest count since the
# last load sample. A call whose stream was never consumed is dropped after STALE_SECONDS.
_active: dict[int, float] = {}
_next_token = 0
_in_flight_peak = 0
STALE_SECONDS = 900


def in_flight_sample() -> tuple[int, int]:
    """(current, peak since the previous sample); resets the peak."""
    global _in_flight_peak
    cutoff = time.monotonic() - STALE_SECONDS
    for token in [t for t, started in _active.items() if started < cutoff]:
        _active.pop(token, None)
    current = len(_active)
    peak, _in_flight_peak = _in_flight_peak, current
    return current, max(peak, current)


def _begin(ctx: dict) -> None:
    global _next_token, _in_flight_peak
    _next_token += 1
    ctx['_token'] = _next_token
    _active[_next_token] = time.monotonic()
    _in_flight_peak = max(_in_flight_peak, len(_active))


def _end(ctx: dict) -> None:
    token = ctx.pop('_token', None)
    if token is not None:
        _active.pop(token, None)


def context(metadata: Optional[dict], user, model_id: str, url: str, track: bool = True) -> dict:
    metadata = metadata or {}
    chat_id = str(metadata.get('chat_id') or '')
    task = metadata.get('task')
    if task:
        source = 'task'
    elif chat_id.startswith('channel:'):
        source = 'channel'
    elif chat_id and not chat_id.startswith('local'):
        source = 'chat'
    else:
        source = 'api'
    from urllib.parse import urlsplit

    try:
        host = urlsplit(url).netloc.split('@')[-1]
    except Exception:
        host = ''
    ctx = {
        'source': source,
        'user_id': getattr(user, 'id', None),
        'model_id': model_id,
        'chat_id': chat_id or None,
        'message_id': metadata.get('message_id'),
        'name': str(task) if task else None,
        'meta': {'endpoint': host},
    }
    if track:
        _begin(ctx)
    return ctx


def _usage(usage: Optional[dict]) -> tuple[Optional[int], Optional[int]]:
    if not isinstance(usage, dict):
        return None, None
    return (
        usage.get('prompt_tokens', usage.get('input_tokens')),
        usage.get('completion_tokens', usage.get('output_tokens')),
    )


def record(ctx: dict, timer: perf.Timer, status: str = 'ok', usage=None, ttft_ms=None, error=None) -> None:
    _end(ctx)
    timer.stop()
    input_tokens, output_tokens = _usage(usage)
    meta = dict(ctx.get('meta') or {})
    if error:
        meta['error'] = str(error)[:300]
    generation_ms = timer.ms - ttft_ms if ttft_ms is not None else timer.ms
    if output_tokens and generation_ms and generation_ms > 0:
        meta['tokens_per_second'] = round(output_tokens * 1000 / generation_ms, 1)
    perf.record(
        'llm_call',
        ts=timer.ts,
        **{k: v for k, v in ctx.items() if k != 'meta' and not k.startswith('_')},
        status=status,
        duration_ms=timer.ms,
        ttft_ms=ttft_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        meta=meta,
    )


async def wrap_stream(iterator: AsyncIterator[bytes], ctx: dict, timer: perf.Timer) -> AsyncIterator[bytes]:
    """Pass the SSE stream through unchanged, noting the first token and the final usage."""
    ttft_ms = None
    usage = None
    pending = b''
    status = 'ok'
    error = None
    try:
        async for chunk in iterator:
            try:
                if ttft_ms is None and _TOKEN_RE.search(chunk):
                    ttft_ms = timer.elapsed_ms()
                # Keep only the unfinished last line between chunks; parse complete lines that carry usage.
                buffer = pending + chunk
                cut = buffer.rfind(b'\n')
                complete, pending = buffer[: cut + 1], buffer[cut + 1 :][-65536:]
                if b'"usage"' in complete:
                    for line in complete.split(b'\n'):
                        line = line.strip()
                        if line.startswith(b'data:') and b'"usage"' in line:
                            try:
                                usage = (JSONCodec.loads(line[5:].strip()) or {}).get('usage') or usage
                            except Exception:
                                pass
            except Exception:
                pass
            yield chunk
    except BaseException as e:
        status, error = 'error', type(e).__name__
        raise
    finally:
        record(ctx, timer, status=status, usage=usage, ttft_ms=ttft_ms, error=error)


def _output_text(item: dict) -> str:
    output = item.get('output')
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        return ' '.join(str(p.get('text') or '') for p in output if isinstance(p, dict)).strip()
    return ''


def record_response(metadata: Optional[dict], user, model_id: Optional[str], output, usage, status: str = 'ok') -> None:
    """One finished answer (kind 'response') and each function call in it (kind 'tool')."""
    try:
        metadata = metadata or {}
        started = metadata.get('perf_started_ms')
        ts = int(started) if started else perf.now_ms()
        ctx = context(metadata, user, model_id or '', '', track=False)
        ctx.pop('meta', None)
        calls = [i for i in output or [] if isinstance(i, dict) and i.get('type') == 'function_call']
        results = {
            i.get('call_id'): i for i in output or [] if isinstance(i, dict) and i.get('type') == 'function_call_output'
        }
        tools: dict[str, dict] = {}
        for call in calls:
            name = call.get('name') or 'unknown'
            duration = call.get('duration')
            duration_ms = int(float(duration) * 1000) if isinstance(duration, (int, float)) else None
            result = results.get(call.get('call_id')) or {}
            failed = call.get('status') == 'failed' or result.get('status') == 'failed' or bool(call.get('error_type'))
            started_at = call.get('started_at')
            meta = None
            if failed:
                meta = {
                    'error_type': call.get('error_type') or result.get('error_type'),
                    'error': _output_text(result)[:800] or None,
                    'arguments': str(call.get('arguments') or '')[:400] or None,
                }
            perf.record(
                'tool',
                ts=int(started_at * 1000) if isinstance(started_at, (int, float)) else ts,
                **{**ctx, 'name': name},
                status='error' if failed else 'ok',
                duration_ms=duration_ms,
                meta={k: v for k, v in (meta or {}).items() if v} or None,
            )
            summary = tools.setdefault(name, {'count': 0, 'ms': 0, 'errors': 0})
            summary['count'] += 1
            summary['ms'] += duration_ms or 0
            summary['errors'] += int(failed)
        input_tokens, output_tokens = _usage(usage)
        perf.record(
            'response',
            ts=ts,
            **ctx,
            status=status,
            duration_ms=perf.now_ms() - ts,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            count=len(calls),
            meta={'tools': tools} if tools else None,
        )
    except Exception:
        pass
