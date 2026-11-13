"""NCS AI performance monitor (separate container). See __init__ for how to run it.

Environment:
  DATABASE_URL        postgresql://user:pass@postgres:5432/openwebui (same database as NCS AI)
  OPEN_WEBUI_URL      http://open-webui:8080
  MONITOR_API_KEY     shared with NCS AI (its internal /api/v1/monitor API)
  MONITOR_USERNAME    login for this page
  MONITOR_PASSWORD    login for this page (the page refuses to work without it)
  REDIS_URL           optional, for Redis memory
  DOCKER_SOCKET       optional, default /var/run/docker.sock (mounted read-only) for container CPU/RAM
"""

import asyncio
import base64
import hmac
import os
import time
from decimal import Decimal
from pathlib import Path
from typing import Optional

import aiohttp
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import logging

log = logging.getLogger('ncs.monitor')
logging.basicConfig(level=logging.INFO)

DATABASE_URL = os.getenv('DATABASE_URL', '')
OPEN_WEBUI_URL = os.getenv('OPEN_WEBUI_URL', 'http://open-webui:8080').rstrip('/')
MONITOR_API_KEY = os.getenv('MONITOR_API_KEY', '')
USERNAME = os.getenv('MONITOR_USERNAME', 'admin')
PASSWORD = os.getenv('MONITOR_PASSWORD', '')
REDIS_URL = os.getenv('REDIS_URL', '')
DOCKER_SOCKET = os.getenv('DOCKER_SOCKET', '/var/run/docker.sock')
# Public address of NCS AI, for "open this chat" links on the timeline (optional).
PUBLIC_URL = os.getenv('OPEN_WEBUI_PUBLIC_URL', '').rstrip('/')
STATIC = Path(__file__).parent / 'static'

RANGES = {  # range -> (milliseconds, chart bucket in ms)
    '1h': (3_600_000, 120_000),
    '24h': (86_400_000, 1_800_000),
    '7d': (7 * 86_400_000, 3 * 3_600_000),
    '30d': (30 * 86_400_000, 12 * 3_600_000),
    '90d': (90 * 86_400_000, 86_400_000),
}
# Built-in tools that read knowledge bases and files.
KNOWLEDGE_TOOLS = (
    'list_knowledge',
    'list_knowledge_bases',
    'search_knowledge_files',
    'search_knowledge_bases',
    'grep_knowledge_files',
    'query_knowledge_files',
    'query_knowledge_bases',
    'view_file',
    'view_knowledge_file',
    'list_chat_files',
    'query_chat_files',
    'grep_chat_files',
    'kb_exec',
)


def _async_url(url: str) -> str:
    if url.startswith('postgres://'):
        url = 'postgresql://' + url[len('postgres://') :]
    if url.startswith('postgresql://'):
        url = 'postgresql+psycopg://' + url[len('postgresql://') :]
    return url


engine = (
    create_async_engine(_async_url(DATABASE_URL), pool_size=5, max_overflow=5, pool_pre_ping=True)
    if DATABASE_URL
    else None
)
app = FastAPI(title='NCS AI Monitor', docs_url=None, redoc_url=None, openapi_url=None)


def require_login(request: Request) -> None:
    if not PASSWORD:
        raise HTTPException(503, 'MONITOR_PASSWORD is not set; the monitor is disabled.')
    header = request.headers.get('authorization', '')
    if header.lower().startswith('basic '):
        try:
            user, _, password = base64.b64decode(header[6:]).decode('utf-8').partition(':')
            if hmac.compare_digest(user, USERNAME) and hmac.compare_digest(password, PASSWORD):
                return
        except Exception:
            pass
    raise HTTPException(401, 'Login required', headers={'WWW-Authenticate': 'Basic realm="NCS AI Monitor"'})


async def rows(sql: str, **params) -> list[dict]:
    if engine is None:
        raise HTTPException(503, 'DATABASE_URL is not set')
    async with engine.connect() as conn:
        result = await conn.execute(text(sql), params)
        return [dict(r._mapping) for r in result]


def window(range_: str) -> tuple[int, int, int]:
    span, bucket = RANGES.get(range_, RANGES['24h'])
    now = int(time.time() * 1000)
    return now - span, now, bucket


def _num(value, digits: int = 0):
    if value is None:
        return None
    value = float(value)
    return round(value, digits) if digits else int(round(value))


def clean(items: list[dict]) -> list[dict]:
    for item in items:
        for key, value in list(item.items()):
            if isinstance(value, (Decimal, float)):
                item[key] = _num(value, 1)
    return items


STATS = """
    count(*) AS count,
    sum(CASE WHEN status <> 'ok' THEN 1 ELSE 0 END) AS errors,
    avg(duration_ms) AS avg_ms,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms) AS p50_ms,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms
"""


@app.get('/api/overview', dependencies=[Depends(require_login)])
async def overview(range: str = '24h'):
    start, end, bucket = window(range)
    responses = (
        await rows(
            f"""SELECT {STATS}, sum(count) AS tool_calls,
                count(DISTINCT user_id) AS users
            FROM perf_event WHERE kind = 'response' AND ts >= :start""",
            start=start,
        )
    )[0]
    calls = (
        await rows(
            f"""SELECT {STATS},
                sum(coalesce(input_tokens, 0)) AS input_tokens, sum(coalesce(output_tokens, 0)) AS output_tokens,
                avg(ttft_ms) AS avg_ttft, percentile_cont(0.95) WITHIN GROUP (ORDER BY ttft_ms) AS p95_ttft,
                avg((meta->>'tokens_per_second')::float) AS tokens_per_second
            FROM perf_event WHERE kind = 'llm_call' AND ts >= :start""",
            start=start,
        )
    )[0]
    series = await rows(
        """SELECT (ts / :bucket) * :bucket AS t,
                sum(CASE WHEN kind = 'response' THEN 1 ELSE 0 END) AS responses,
                avg(CASE WHEN kind = 'response' THEN duration_ms END) AS avg_ms,
                avg(CASE WHEN kind = 'llm_call' THEN ttft_ms END) AS avg_ttft,
                sum(CASE WHEN kind = 'llm_call' THEN coalesce(input_tokens, 0) + coalesce(output_tokens, 0) END)
                    AS tokens,
                sum(CASE WHEN status <> 'ok' AND kind IN ('response', 'llm_call') THEN 1 ELSE 0 END) AS errors
            FROM perf_event WHERE ts >= :start AND kind IN ('response', 'llm_call')
            GROUP BY 1 ORDER BY 1""",
        start=start,
        bucket=bucket,
    )
    by_source = await rows(
        """SELECT coalesce(source, 'api') AS source, count(*) AS calls,
                sum(coalesce(input_tokens, 0) + coalesce(output_tokens, 0)) AS tokens
            FROM perf_event WHERE kind = 'llm_call' AND ts >= :start GROUP BY 1 ORDER BY 2 DESC""",
        start=start,
    )
    errors = await rows(
        """SELECT p.ts, p.kind, p.name, p.model_id, p.source, p.chat_id, p.duration_ms,
                coalesce(p.meta->>'error', p.meta->>'error_type', p.meta->>'http_status') AS error,
                coalesce(u.name, u.email) AS user_name
            FROM perf_event p LEFT JOIN "user" u ON u.id = p.user_id
            WHERE p.status <> 'ok' AND p.ts >= :start AND p.kind IN ('llm_call', 'response', 'tool', 'tracker', 'ingest', 'rag')
            ORDER BY p.ts DESC LIMIT 30""",
        start=start,
    )
    return {
        'errors': clean(errors),
        'range': range,
        'start': start,
        'end': end,
        'bucket': bucket,
        'responses': clean([responses])[0],
        'calls': clean([calls])[0],
        'series': clean(series),
        'by_source': clean(by_source),
    }


@app.get('/api/tokens', dependencies=[Depends(require_login)])
async def tokens(range: str = '24h', source: Optional[str] = None):
    start, _, _ = window(range)
    filter_source = 'AND p.source = :source' if source else ''
    matrix = await rows(
        f"""SELECT p.user_id, coalesce(u.name, u.email, p.user_id, '(hệ thống)') AS user_name, u.email,
                p.model_id, count(*) AS calls,
                sum(coalesce(p.input_tokens, 0)) AS input_tokens, sum(coalesce(p.output_tokens, 0)) AS output_tokens
            FROM perf_event p LEFT JOIN "user" u ON u.id = p.user_id
            WHERE p.kind IN ('llm_call', 'tracker') AND p.ts >= :start {filter_source}
            GROUP BY p.user_id, u.name, u.email, p.model_id
            ORDER BY sum(coalesce(p.input_tokens, 0) + coalesce(p.output_tokens, 0)) DESC""",
        start=start,
        **({'source': source} if source else {}),
    )
    missing = (
        await rows(
            """SELECT count(*) AS calls FROM perf_event
            WHERE kind = 'llm_call' AND ts >= :start AND input_tokens IS NULL AND status = 'ok'""",
            start=start,
        )
    )[0]['calls']
    return {'matrix': clean(matrix), 'calls_without_usage': missing}


@app.get('/api/models', dependencies=[Depends(require_login)])
async def models(range: str = '24h'):
    start, _, bucket = window(range)
    per_model = await rows(
        f"""SELECT model_id, {STATS},
                avg(ttft_ms) AS avg_ttft,
                percentile_cont(0.5) WITHIN GROUP (ORDER BY ttft_ms) AS p50_ttft,
                percentile_cont(0.95) WITHIN GROUP (ORDER BY ttft_ms) AS p95_ttft,
                avg((meta->>'tokens_per_second')::float) AS tokens_per_second,
                sum(coalesce(output_tokens, 0)) AS output_tokens,
                max(meta->>'endpoint') AS endpoint
            FROM perf_event WHERE kind = 'llm_call' AND ts >= :start
            GROUP BY model_id ORDER BY count(*) DESC""",
        start=start,
    )
    series = await rows(
        """SELECT model_id, (ts / :bucket) * :bucket AS t, avg(ttft_ms) AS avg_ttft, avg(duration_ms) AS avg_ms,
                avg((meta->>'tokens_per_second')::float) AS tokens_per_second, count(*) AS calls
            FROM perf_event WHERE kind = 'llm_call' AND ts >= :start GROUP BY 1, 2 ORDER BY 2""",
        start=start,
        bucket=bucket,
    )
    slowest = await rows(
        """SELECT p.ts, p.model_id, p.source, p.duration_ms, p.ttft_ms, p.output_tokens, p.status, p.message_id,
                coalesce(u.name, u.email) AS user_name, p.chat_id, p.meta->>'error' AS error
            FROM perf_event p LEFT JOIN "user" u ON u.id = p.user_id
            WHERE p.kind = 'llm_call' AND p.ts >= :start ORDER BY p.duration_ms DESC NULLS LAST LIMIT 20""",
        start=start,
    )
    return {'models': clean(per_model), 'series': clean(series), 'slowest': clean(slowest), 'bucket': bucket}


@app.get('/api/tools', dependencies=[Depends(require_login)])
async def tools(range: str = '24h', name: Optional[str] = None, limit: int = Query(100, le=500)):
    start, _, _ = window(range)
    per_tool = await rows(
        f"""SELECT name, {STATS}, max(duration_ms) AS max_ms, count(DISTINCT message_id) AS responses
            FROM perf_event WHERE kind = 'tool' AND ts >= :start GROUP BY name ORDER BY count(*) DESC""",
        start=start,
    )
    distribution = await rows(
        """SELECT CASE WHEN count >= 10 THEN 10 ELSE coalesce(count, 0) END AS calls, count(*) AS responses
            FROM perf_event WHERE kind = 'response' AND ts >= :start GROUP BY 1 ORDER BY 1""",
        start=start,
    )
    filter_name = "AND jsonb_exists((p.meta->'tools')::jsonb, :name)" if name else ''
    recent = await rows(
        f"""SELECT p.ts, p.model_id, p.source, p.chat_id, p.message_id, p.duration_ms, p.count, p.status,
                p.input_tokens, p.output_tokens, p.meta->'tools' AS tools, coalesce(u.name, u.email) AS user_name
            FROM perf_event p LEFT JOIN "user" u ON u.id = p.user_id
            WHERE p.kind = 'response' AND p.ts >= :start AND coalesce(p.count, 0) > 0 {filter_name}
            ORDER BY p.ts DESC LIMIT :limit""",
        start=start,
        limit=limit,
        **({'name': name} if name else {}),
    )
    errors = await rows(
        """SELECT p.ts, p.name, p.model_id, p.source, p.chat_id, p.duration_ms,
                p.meta->>'error_type' AS error_type, p.meta->>'error' AS error, p.meta->>'arguments' AS arguments,
                coalesce(u.name, u.email) AS user_name
            FROM perf_event p LEFT JOIN "user" u ON u.id = p.user_id
            WHERE p.kind = 'tool' AND p.status <> 'ok' AND p.ts >= :start
            ORDER BY p.ts DESC LIMIT 50""",
        start=start,
    )
    return {
        'tools': clean(per_tool),
        'distribution': clean(distribution),
        'recent': clean(recent),
        'errors': clean(errors),
    }


CONCURRENCY_LEVELS = [
    (0, 1, '0–1'),
    (2, 3, '2–3'),
    (4, 5, '4–5'),
    (6, 10, '6–10'),
    (11, 20, '11–20'),
    (21, 10**9, '> 20'),
]


@app.get('/api/load', dependencies=[Depends(require_login)])
async def load(range: str = '24h'):
    """Users online and model calls running at the same time, and how response times change with them."""
    start, _, bucket = window(range)
    samples = """WITH s AS (
            SELECT (ts / :bucket) * :bucket AS t, name, max(count) AS current,
                max((meta->>'in_flight_peak')::int) AS peak, max((meta->>'online_users')::int) AS online,
                max((meta->>'sessions')::int) AS sessions
            FROM perf_event WHERE kind = 'load' AND ts >= :start GROUP BY 1, 2)
        SELECT t, sum(peak) AS in_flight_peak, sum(current) AS in_flight, max(online) AS online_users,
            max(sessions) AS sessions
        FROM s GROUP BY t ORDER BY t"""
    series = clean(await rows(samples, start=start, bucket=bucket))
    activity = {
        r['t']: r
        for r in clean(
            await rows(
                """SELECT (ts / :bucket) * :bucket AS t, count(DISTINCT user_id) AS active_users,
                    avg(CASE WHEN kind = 'llm_call' THEN ttft_ms END) AS avg_ttft,
                    avg(CASE WHEN kind = 'response' THEN duration_ms END) AS avg_response_ms
                FROM perf_event WHERE kind IN ('llm_call', 'response') AND ts >= :start GROUP BY 1""",
                start=start,
                bucket=bucket,
            )
        )
    }
    for point in series:
        point.update({k: v for k, v in activity.get(point['t'], {}).items() if k != 't'})
    latest = (
        await rows(
            """SELECT sum(count) AS in_flight, max((meta->>'online_users')::int) AS online_users,
                max((meta->>'sessions')::int) AS sessions
            FROM (SELECT DISTINCT ON (name) name, count, meta FROM perf_event
                  WHERE kind = 'load' AND ts >= :recent ORDER BY name, ts DESC) last""",
            recent=int(time.time() * 1000) - 90_000,
        )
    )[0]
    # Per minute: concurrency peak vs. speed, grouped by concurrency level, to see where it slows down.
    minutes = clean(await rows(samples, start=start, bucket=60_000))
    speed = {
        r['t']: r
        for r in clean(
            await rows(
                """SELECT (ts / 60000) * 60000 AS t, avg(ttft_ms) AS ttft, avg(duration_ms) AS duration,
                    avg((meta->>'tokens_per_second')::float) AS tps, count(*) AS calls,
                    sum(CASE WHEN status <> 'ok' THEN 1 ELSE 0 END) AS errors
                FROM perf_event WHERE kind = 'llm_call' AND ts >= :start GROUP BY 1""",
                start=start,
            )
        )
    }
    levels = []
    for low, high, label in CONCURRENCY_LEVELS:
        picked = [speed[m['t']] for m in minutes if low <= (m['in_flight_peak'] or 0) <= high and m['t'] in speed]
        if not picked:
            continue

        def avg(key):
            values = [p[key] for p in picked if p[key] is not None]
            return sum(values) / len(values) if values else None

        calls = sum(p['calls'] for p in picked)
        levels.append(
            {
                'level': label,
                'minutes': len(picked),
                'calls': calls,
                'avg_ttft': avg('ttft'),
                'avg_ms': avg('duration'),
                'tokens_per_second': avg('tps'),
                'errors': sum(p['errors'] for p in picked),
            }
        )
    return {
        'series': series,
        'bucket': bucket,
        'now': clean([latest])[0],
        'peak_online': max((p['online_users'] or 0 for p in series), default=0),
        'peak_in_flight': max((p['in_flight_peak'] or 0 for p in series), default=0),
        'levels': levels,
    }


def _text(content) -> str:
    """Plain text of a stored message (string, JSON string or a list of content blocks)."""
    import json

    if content is None:
        return ''
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            return content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get('text') or block.get('content') or ''))
            elif isinstance(block, str):
                parts.append(block)
        return ' '.join(p for p in parts if p).strip()
    if isinstance(content, dict):
        return str(content.get('text') or content.get('content') or '')
    return str(content)


async def questions(pairs: list[tuple[str, str]]) -> dict[str, dict]:
    """Question and answer text for (chat_id, message_id) pairs, for chats and channel @mentions."""
    result: dict[str, dict] = {}
    chat_ids = [f'{c}-{m}' for c, m in pairs if c and not c.startswith('channel:')]
    channel_ids = [m for c, m in pairs if c and c.startswith('channel:')]
    if chat_ids:
        try:
            for r in await rows(
                """SELECT a.id, a.chat_id, q.content AS question, a.content AS answer
                FROM chat_message a LEFT JOIN chat_message q ON q.id = a.chat_id || '-' || a.parent_id
                WHERE a.id = ANY(:ids)""",
                ids=chat_ids,
            ):
                message_id = r['id'][len(r['chat_id']) + 1 :]
                result[message_id] = {'question': _text(r['question']), 'answer': _text(r['answer'])}
        except Exception:
            pass
    if channel_ids:
        try:
            for r in await rows(
                """SELECT a.id, q.content AS question, a.content AS answer
                FROM message a LEFT JOIN message q ON q.id = coalesce(a.reply_to_id, a.parent_id)
                WHERE a.id = ANY(:ids)""",
                ids=channel_ids,
            ):
                result[r['id']] = {'question': _text(r['question']), 'answer': _text(r['answer'])}
        except Exception:
            pass
    return result


def open_link(chat_id: Optional[str], message_id: Optional[str]) -> Optional[str]:
    if not PUBLIC_URL or not chat_id:
        return None
    if chat_id.startswith('channel:'):
        return f'{PUBLIC_URL}/channels/{chat_id[len("channel:") :]}?message={message_id or ""}'
    return f'{PUBLIC_URL}/c/{chat_id}'


@app.get('/api/traces', dependencies=[Depends(require_login)])
async def traces(
    range: str = '24h',
    q: str = '',
    min_ms: int = 0,
    source: Optional[str] = None,
    limit: int = Query(100, le=300),
):
    """Answers, newest first, with how many model calls and tool calls each one needed."""
    start, _, _ = window(range)
    where = ["p.kind = 'response'", 'p.ts >= :start', 'coalesce(p.duration_ms, 0) >= :min_ms']
    params = {'start': start, 'min_ms': min_ms, 'limit': limit}
    if source:
        where.append('p.source = :source')
        params['source'] = source
    if q.strip():
        where.append(
            '(p.message_id ILIKE :q OR p.chat_id ILIKE :q OR u.name ILIKE :q OR u.email ILIKE :q OR p.model_id ILIKE :q)'
        )
        params['q'] = f'%{q.strip()}%'
    items = clean(
        await rows(
            f"""SELECT p.ts, p.message_id, p.chat_id, p.source, p.model_id, p.duration_ms, p.count AS tool_calls,
                p.status, p.input_tokens, p.output_tokens, coalesce(u.name, u.email) AS user_name,
                (SELECT count(*) FROM perf_event l WHERE l.kind = 'llm_call' AND l.message_id = p.message_id
                    AND l.source <> 'task') AS llm_calls,
                (SELECT min(l.ttft_ms) FROM perf_event l WHERE l.kind = 'llm_call' AND l.message_id = p.message_id
                    AND l.source <> 'task' AND l.ts = (SELECT min(f.ts) FROM perf_event f WHERE f.kind = 'llm_call'
                    AND f.message_id = p.message_id AND f.source <> 'task')) AS first_ttft
            FROM perf_event p LEFT JOIN "user" u ON u.id = p.user_id
            WHERE {' AND '.join(where)} ORDER BY p.ts DESC LIMIT :limit""",
            **params,
        )
    )
    return {'items': items}


@app.get('/api/trace/{message_id}', dependencies=[Depends(require_login)])
async def trace(message_id: str):
    """Everything recorded for one answer, in order: preparation, each model call, each tool call."""
    events = clean(
        await rows(
            """SELECT p.kind, p.ts, p.source, p.name, p.model_id, p.chat_id, p.status, p.duration_ms, p.ttft_ms,
                p.input_tokens, p.output_tokens, p.count, p.meta, coalesce(u.name, u.email) AS user_name, u.email
            FROM perf_event p LEFT JOIN "user" u ON u.id = p.user_id
            WHERE p.message_id = :m AND p.kind IN ('response', 'llm_call', 'tool') ORDER BY p.ts, p.kind""",
            m=message_id,
        )
    )
    if not events:
        raise HTTPException(404, 'Không có số liệu cho message id này')
    response = next((e for e in events if e['kind'] == 'response'), None)
    calls = [e for e in events if e['kind'] == 'llm_call' and e['source'] != 'task']
    tools = [e for e in events if e['kind'] == 'tool']
    tasks = [e for e in events if e['kind'] == 'llm_call' and e['source'] == 'task']
    steps = sorted(calls + tools, key=lambda e: e['ts'])
    start = response['ts'] if response else steps[0]['ts'] if steps else events[0]['ts']
    ends = [e['ts'] + (e['duration_ms'] or 0) for e in steps]
    if response:
        ends.append(response['ts'] + (response['duration_ms'] or 0))
    end = max(ends) if ends else start

    # Time covered by at least one model or tool call; the rest is preparation and NCS AI's own work.
    spans = sorted((e['ts'], e['ts'] + (e['duration_ms'] or 0)) for e in steps)
    covered, cursor = 0, start
    for a, b in spans:
        a = max(a, cursor)
        if b > a:
            covered += b - a
            cursor = b
    first = steps[0]['ts'] if steps else end
    chat_id = (response or events[0]).get('chat_id')
    return {
        'message_id': message_id,
        'chat_id': chat_id,
        'link': open_link(chat_id, message_id),
        'user_name': (response or events[0]).get('user_name'),
        'email': (response or events[0]).get('email'),
        'model_id': (response or events[0]).get('model_id'),
        'source': (response or events[0]).get('source'),
        'status': response['status'] if response else None,
        'start': start,
        'total_ms': end - start,
        'summary': {
            'prepare_ms': max(first - start, 0),
            'llm_ms': sum(e['duration_ms'] or 0 for e in calls),
            'ttft_ms': sum(e['ttft_ms'] or 0 for e in calls),
            'tool_ms': sum(e['duration_ms'] or 0 for e in tools),
            'other_ms': max(end - start - covered - max(first - start, 0), 0),
            'llm_calls': len(calls),
            'tool_calls': len(tools),
            'input_tokens': sum(e['input_tokens'] or 0 for e in calls),
            'output_tokens': sum(e['output_tokens'] or 0 for e in calls),
        },
        'steps': steps,
        'tasks': tasks,
    }


# Message content is only sent after the reveal key is entered; wrong keys lock the client out for a while.
REVEAL_KEY = os.getenv('MONITOR_REVEAL_KEY', '') or MONITOR_API_KEY
log.info(
    'Monitor: reveal key %s',
    'from MONITOR_REVEAL_KEY'
    if os.getenv('MONITOR_REVEAL_KEY')
    else 'not set, using MONITOR_API_KEY'
    if MONITOR_API_KEY
    else 'not set: message content cannot be revealed',
)
REVEAL_MAX_FAILURES = 5
REVEAL_LOCK_SECONDS = 600
_reveal_failures: dict[str, list[float]] = {}


def _client(request: Request) -> str:
    forwarded = request.headers.get('x-forwarded-for', '').split(',')[0].strip()
    return forwarded or (request.client.host if request.client else '?')


@app.post('/api/trace/{message_id}/content', dependencies=[Depends(require_login)])
async def trace_content(message_id: str, request: Request):
    """The user's question and the answer of one message, for whoever knows the reveal key."""
    if not REVEAL_KEY:
        raise HTTPException(
            503, 'Chưa đặt MONITOR_REVEAL_KEY (hoặc MONITOR_API_KEY) nên không truy xuất được nội dung.'
        )
    client = _client(request)
    now = time.time()
    recent = [t for t in _reveal_failures.get(client, []) if now - t < REVEAL_LOCK_SECONDS]
    _reveal_failures[client] = recent
    if len(recent) >= REVEAL_MAX_FAILURES:
        wait = int(REVEAL_LOCK_SECONDS - (now - recent[0]))
        raise HTTPException(429, f'Nhập sai quá {REVEAL_MAX_FAILURES} lần. Thử lại sau {max(wait // 60, 1)} phút.')
    key = request.headers.get('x-reveal-key', '')
    if not key or not hmac.compare_digest(key, REVEAL_KEY):
        recent.append(now)
        log.warning('Monitor: wrong reveal key from %s for message %s', client, message_id)
        raise HTTPException(403, f'Khóa không đúng (còn {REVEAL_MAX_FAILURES - len(recent)} lần thử).')
    _reveal_failures.pop(client, None)
    found = await rows(
        'SELECT chat_id FROM perf_event WHERE message_id = :m AND chat_id IS NOT NULL LIMIT 1', m=message_id
    )
    if not found:
        raise HTTPException(404, 'Không có số liệu cho message id này')
    text = (await questions([(found[0]['chat_id'], message_id)])).get(message_id, {})
    log.info('Monitor: message %s content revealed to %s (%s)', message_id, client, USERNAME)
    return {'question': text.get('question', ''), 'answer': text.get('answer', '')[:5000]}


async def webui(path: str, method: str = 'GET', timeout: int = 20, **params):
    if not MONITOR_API_KEY:
        return {'error': 'MONITOR_API_KEY chưa đặt'}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.request(
                method,
                f'{OPEN_WEBUI_URL}/api/v1/monitor{path}',
                params=params,
                headers={'X-Monitor-Key': MONITOR_API_KEY},
            ) as response:
                if response.status != 200:
                    return {'error': f'NCS AI trả về {response.status}'}
                return await response.json()
    except Exception as e:
        return {'error': f'Không gọi được NCS AI: {e}'}


@app.get('/api/knowledge', dependencies=[Depends(require_login)])
async def knowledge(range: str = '24h'):
    start, _, _ = window(range)
    status = await webui('/status')
    ingest = await rows(
        """SELECT p.ts, p.name, p.source, p.duration_ms, p.status, p.meta, coalesce(u.name, u.email) AS user_name
            FROM perf_event p LEFT JOIN "user" u ON u.id = p.user_id
            WHERE p.kind = 'ingest' AND p.ts >= :start ORDER BY p.ts DESC LIMIT 100""",
        start=start,
    )
    ingest_stats = (
        await rows(
            f"""SELECT {STATS}, avg((meta->>'chars')::float) AS avg_chars, sum((meta->>'chars')::float) AS chars,
                avg((meta->>'extract_ms')::float) AS avg_extract_ms, avg((meta->>'embed_ms')::float) AS avg_embed_ms
            FROM perf_event WHERE kind = 'ingest' AND ts >= :start""",
            start=start,
        )
    )[0]
    retrieval = (
        await rows(
            f"""SELECT {STATS}, avg((meta->>'embed_ms')::float) AS avg_embed_ms,
                avg((meta->>'search_ms')::float) AS avg_search_ms, avg(count) AS avg_chunks
            FROM perf_event WHERE kind = 'rag' AND ts >= :start""",
            start=start,
        )
    )[0]
    tool_usage = await rows(
        f"""SELECT name, {STATS} FROM perf_event
            WHERE kind = 'tool' AND ts >= :start AND name = ANY(:names) GROUP BY name ORDER BY count(*) DESC""",
        start=start,
        names=list(KNOWLEDGE_TOOLS),
    )
    totals = (
        await rows(
            """SELECT (SELECT count(*) FROM knowledge) AS knowledge_bases,
                (SELECT count(*) FROM knowledge_file) AS knowledge_files,
                (SELECT count(*) FROM file) AS files,
                (SELECT coalesce(sum(length(data->>'content')), 0) FROM file) AS stored_chars"""
        )
    )[0]
    return {
        'status': status,
        'ingest': clean(ingest),
        'ingest_stats': clean([ingest_stats])[0],
        'retrieval': clean([retrieval])[0],
        'tools': clean(tool_usage),
        'totals': clean([totals])[0],
    }


@app.get('/api/tracker', dependencies=[Depends(require_login)])
async def tracker(range: str = '24h'):
    start, _, bucket = window(range)
    per_endpoint = await rows(
        f"""SELECT name AS endpoint, model_id, {STATS},
                sum(coalesce(input_tokens, 0)) AS input_tokens, sum(coalesce(output_tokens, 0)) AS output_tokens,
                sum(CASE WHEN (meta->>'failover')::boolean THEN 1 ELSE 0 END) AS failovers,
                max(meta->>'url') AS url
            FROM perf_event WHERE kind = 'tracker' AND ts >= :start GROUP BY name, model_id ORDER BY count(*) DESC""",
        start=start,
    )
    series = await rows(
        """SELECT (ts / :bucket) * :bucket AS t, count(*) AS calls, avg(duration_ms) AS avg_ms,
                sum(CASE WHEN status <> 'ok' THEN 1 ELSE 0 END) AS errors
            FROM perf_event WHERE kind = 'tracker' AND ts >= :start GROUP BY 1 ORDER BY 1""",
        start=start,
        bucket=bucket,
    )
    errors = await rows(
        """SELECT ts, name AS endpoint, model_id, meta->>'http_status' AS http_status, meta->>'error' AS error
            FROM perf_event WHERE kind = 'tracker' AND status <> 'ok' AND ts >= :start ORDER BY ts DESC LIMIT 20""",
        start=start,
    )
    try:
        channels = (
            await rows(
                """SELECT count(*) AS channels, sum(pending_count) AS pending,
                    sum(CASE WHEN fail_count > 0 THEN 1 ELSE 0 END) AS failing,
                    sum(CASE WHEN lease_until > :now_ns THEN 1 ELSE 0 END) AS running,
                    max(processed_at) AS last_processed
                FROM tracker_state""",
                now_ns=time.time_ns(),
            )
        )[0]
    except Exception:
        channels = {}
    return {
        'endpoints': clean(per_endpoint),
        'series': clean(series),
        'errors': clean(errors),
        'channels': clean([channels])[0],
    }


async def docker_stats() -> dict:
    if not os.path.exists(DOCKER_SOCKET):
        return {'available': False, 'reason': 'Chưa gắn Docker socket vào container giám sát'}
    connector = aiohttp.UnixConnector(path=DOCKER_SOCKET)
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.get('http://docker/containers/json') as response:
                containers = await response.json()

            async def stats(container):
                async with session.get(f'http://docker/containers/{container["Id"]}/stats?stream=false') as r:
                    s = await r.json()
                mem = s.get('memory_stats') or {}
                cache = (mem.get('stats') or {}).get('inactive_file', 0)
                used = max((mem.get('usage') or 0) - cache, 0)
                cpu = s.get('cpu_stats') or {}
                pre = s.get('precpu_stats') or {}
                cpu_delta = (cpu.get('cpu_usage') or {}).get('total_usage', 0) - (pre.get('cpu_usage') or {}).get(
                    'total_usage', 0
                )
                sys_delta = (cpu.get('system_cpu_usage') or 0) - (pre.get('system_cpu_usage') or 0)
                cpus = cpu.get('online_cpus') or 1
                return {
                    'name': (container.get('Names') or ['?'])[0].lstrip('/'),
                    'image': container.get('Image'),
                    'state': container.get('State'),
                    'status': container.get('Status'),
                    'memory': used,
                    'memory_limit': mem.get('limit'),
                    'cpu_percent': round(cpu_delta / sys_delta * cpus * 100, 1) if sys_delta > 0 else 0.0,
                }

            items = await asyncio.gather(*(stats(c) for c in containers), return_exceptions=True)
            return {'available': True, 'containers': [i for i in items if isinstance(i, dict)]}
    except Exception as e:
        return {'available': False, 'reason': f'Không đọc được Docker: {e}'}


async def database_stats() -> dict:
    try:
        info = (
            await rows(
                """SELECT pg_database_size(current_database()) AS size,
                    (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()) AS connections,
                    (SELECT count(*) FROM perf_event) AS perf_events,
                    version() AS version"""
            )
        )[0]
        tables = await rows(
            """SELECT relname AS name, pg_total_relation_size(relid) AS size, n_live_tup AS rows
                FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 8"""
        )
        return {**info, 'tables': tables}
    except Exception as e:
        return {'error': str(e)}


async def redis_stats() -> dict:
    if not REDIS_URL:
        return {}
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(REDIS_URL)
        try:
            started = time.perf_counter()
            await client.ping()
            ping = round((time.perf_counter() - started) * 1000, 1)
            memory = await client.info('memory')
            clients = await client.info('clients')
        finally:
            await client.aclose()
        return {
            'ping_ms': ping,
            'memory': memory.get('used_memory'),
            'peak': memory.get('used_memory_peak'),
            'clients': clients.get('connected_clients'),
        }
    except Exception as e:
        return {'error': str(e)}


@app.get('/api/system', dependencies=[Depends(require_login)])
async def system():
    status, docker, database, redis = await asyncio.gather(
        webui('/status'), docker_stats(), database_stats(), redis_stats()
    )
    try:
        last = await rows(
            """SELECT ts, name, model_id, meta->>'url' AS url, status FROM perf_event
                WHERE kind = 'tracker' AND status = 'ok' ORDER BY ts DESC LIMIT 1"""
        )
    except Exception:
        last = []
    return {
        'status': status,
        'docker': docker,
        'database': database,
        'redis': redis,
        'tracker_last': clean(last)[0] if last else None,
        'time': time.time(),
    }


@app.post('/api/probe', dependencies=[Depends(require_login)])
async def probe(llm: bool = True):
    return await webui('/probe', method='POST', timeout=120, llm=str(llm).lower())


@app.get('/healthz')
async def health():
    return {'ok': True}


@app.get('/', dependencies=[Depends(require_login)])
async def index():
    return FileResponse(STATIC / 'index.html', headers={'Cache-Control': 'no-store'})


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse({'detail': exc.detail}, status_code=exc.status_code, headers=getattr(exc, 'headers', None))
