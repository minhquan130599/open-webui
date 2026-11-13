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

DATABASE_URL = os.getenv('DATABASE_URL', '')
OPEN_WEBUI_URL = os.getenv('OPEN_WEBUI_URL', 'http://open-webui:8080').rstrip('/')
MONITOR_API_KEY = os.getenv('MONITOR_API_KEY', '')
USERNAME = os.getenv('MONITOR_USERNAME', 'admin')
PASSWORD = os.getenv('MONITOR_PASSWORD', '')
REDIS_URL = os.getenv('REDIS_URL', '')
DOCKER_SOCKET = os.getenv('DOCKER_SOCKET', '/var/run/docker.sock')
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
    return {
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
        """SELECT p.ts, p.model_id, p.source, p.duration_ms, p.ttft_ms, p.output_tokens, p.status,
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
    return {'tools': clean(per_tool), 'distribution': clean(distribution), 'recent': clean(recent)}


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
    return {'status': status, 'docker': docker, 'database': database, 'redis': redis, 'time': time.time()}


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
