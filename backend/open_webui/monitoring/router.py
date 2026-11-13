"""Internal API for the monitor container (not used by the NCS AI interface).

Protected by the shared MONITOR_API_KEY (header X-Monitor-Key); disabled (404) when the key is not set.
Returns what each component is configured to use (never secrets) and measures live response times.
"""

import asyncio
import hmac
import os
import time
from typing import Optional

import aiohttp
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from open_webui.models.config import Config

router = APIRouter()

MONITOR_API_KEY = os.getenv('MONITOR_API_KEY', '')


def _check(key: Optional[str]) -> None:
    if not MONITOR_API_KEY:
        raise HTTPException(status_code=404)
    if not key or not hmac.compare_digest(key, MONITOR_API_KEY):
        raise HTTPException(status_code=401)


def _mask_url(url: str) -> str:
    """Keep scheme://host[:port]/path; credentials in the URL are removed."""
    from urllib.parse import urlsplit, urlunsplit

    try:
        parts = urlsplit(url or '')
        host = parts.hostname or ''
        if parts.port:
            host += f':{parts.port}'
        return urlunsplit((parts.scheme, host, parts.path, '', ''))
    except Exception:
        return ''


def _cgroup_memory() -> dict:
    """Memory of this container from cgroup v2 (v1 fallback), in bytes."""
    for current, limit in (
        ('/sys/fs/cgroup/memory.current', '/sys/fs/cgroup/memory.max'),
        ('/sys/fs/cgroup/memory/memory.usage_in_bytes', '/sys/fs/cgroup/memory/memory.limit_in_bytes'),
    ):
        try:
            used = int(open(current).read().strip())
            raw = open(limit).read().strip()
            return {'used': used, 'limit': None if raw == 'max' or int(raw) > 1 << 60 else int(raw)}
        except Exception:
            continue
    return {}


def _processes() -> dict:
    import psutil

    rss = 0
    count = 0
    for proc in psutil.process_iter(['memory_info']):
        try:
            rss += proc.info['memory_info'].rss
            count += 1
        except Exception:
            continue
    vm = psutil.virtual_memory()
    return {
        'processes': count,
        'rss': rss,
        'cpu_percent': psutil.cpu_percent(interval=0.2),
        'cpu_count': psutil.cpu_count(),
        'host_memory_total': vm.total,
        'host_memory_available': vm.available,
        'load_avg': list(os.getloadavg()) if hasattr(os, 'getloadavg') else None,
    }


async def _settings() -> dict:
    keys = [
        'rag.bypass_embedding_and_retrieval',
        'rag.full_context',
        'rag.content_extraction_engine',
        'rag.pdf_extract_images',
        'rag.pdf_loader_mode',
        'rag.text_splitter',
        'rag.chunk_size',
        'rag.chunk_overlap',
        'rag.embedding_engine',
        'rag.embedding_model',
        'rag.embedding_batch_size',
        'rag.enable_hybrid_search',
        'rag.reranking_engine',
        'rag.reranking_model',
        'rag.top_k',
        'rag.top_k_reranker',
        'rag.relevance_threshold',
        'rag.file.max_size',
        'rag.file.max_count',
        'rag.tika_server_url',
        'rag.docling_server_url',
        'audio.stt.engine',
        'audio.stt.model',
        'audio.stt.whisper_model',
        'audio.tts.engine',
        'audio.tts.model',
        'image_generation.enable',
        'image_generation.engine',
        'image_generation.model',
        'code_interpreter.enable',
        'code_interpreter.engine',
        'task.model.default',
        'task.model.external',
        'task.title.enable',
        'task.follow_up.enable',
        'task.tags.enable',
        'openai.enable',
        'openai.api_base_urls',
        'openai.api_configs',
        'ollama.enable',
        'ollama.base_urls',
        'terminal_server.connections',
        'tool_server.connections',
        'web.search.enable',
        'web.search.engine',
    ]
    values = await Config.get_many(*keys)
    connections = []
    configs = values.get('openai.api_configs') or {}
    for idx, url in enumerate(values.get('openai.api_base_urls') or []):
        cfg = configs.get(str(idx), configs.get(url, {})) or {}
        connections.append(
            {
                'index': idx + 1,
                'url': _mask_url(url),
                'enabled': cfg.get('enable', True),
                'prefix_id': cfg.get('prefix_id') or '',
                'model_ids': cfg.get('model_ids') or [],
            }
        )
    terminals = [
        {
            'id': t.get('id'),
            'name': t.get('name'),
            'url': _mask_url(t.get('url', '')),
            'enabled': t.get('enabled', True),
        }
        for t in values.get('terminal_server.connections') or []
    ]
    tool_servers = [
        {
            'name': (t.get('info') or {}).get('name') or (t.get('info') or {}).get('id') or _mask_url(t.get('url', '')),
            'type': t.get('type', 'openapi'),
            'url': _mask_url(t.get('url', '')),
            'enabled': (t.get('config') or {}).get('enable', True),
        }
        for t in values.get('tool_server.connections') or []
    ]
    settings = {k: v for k, v in values.items() if not k.startswith(('openai.', 'terminal_server.', 'tool_server.'))}
    settings['ollama.base_urls'] = [_mask_url(u) for u in values.get('ollama.base_urls') or []]
    return {
        'settings': settings,
        'openai_connections': connections,
        'terminals': terminals,
        'tool_servers': tool_servers,
    }


@router.get('/status')
async def status(request: Request, x_monitor_key: Optional[str] = Header(None)):
    """Configuration of every component, which local models are loaded, and this container's resources."""
    _check(x_monitor_key)
    from open_webui.env import DATABASE_URL, REDIS_URL
    from open_webui.tracker import config as tracker_config

    state = request.app.state
    data = await _settings()
    ef = getattr(state, 'ef', None)
    rf = getattr(state, 'rf', None)
    # Models NCS AI currently lists for each connection (what users can pick), from the shared model cache.
    try:
        listed: dict[int, list[str]] = {}
        for model in list((getattr(state, 'MODELS', None) or {}).values()):
            if isinstance(model, dict) and 'urlIdx' in model:
                listed.setdefault(int(model['urlIdx']) + 1, []).append(model.get('id'))
        for connection in data['openai_connections']:
            connection['models'] = sorted(listed.get(connection['index'], []))
    except Exception:
        pass
    data['loaded'] = {
        'embedding_model': type(ef).__name__ if ef is not None else None,
        'reranker': type(rf).__name__ if rf is not None else None,
        'whisper': getattr(state, 'faster_whisper_model', None) is not None,
        'tts_local': getattr(state, 'speech_synthesiser', None) is not None,
    }
    data['infra'] = {
        'database': DATABASE_URL.split('://', 1)[0] if DATABASE_URL else 'sqlite',
        'vector_db': os.getenv('VECTOR_DB', 'chroma'),
        'redis': bool(REDIS_URL),
        'storage': os.getenv('STORAGE_PROVIDER', 'local') or 'local',
        'workers': int(os.getenv('UVICORN_WORKERS', '1') or 1),
    }
    data['tracker'] = {
        'enabled': tracker_config.ENABLE_PROJECT_TRACKER,
        **(await tracker_config.runtime()),
        'base_url': _mask_url(tracker_config.TRACKER_LLM_BASE_URL),
    }
    try:
        from open_webui.tracker import llm as tracker_llm

        used = getattr(tracker_llm, '_last_used', None)
        data['tracker']['last_used'] = {'url': _mask_url(used[0]), 'model': used[1]} if used else None
    except Exception:
        pass
    data['container'] = {'memory': _cgroup_memory(), **await asyncio.to_thread(_processes)}
    data['time'] = time.time()
    return data


async def _timed(coro) -> dict:
    started = time.perf_counter()
    try:
        detail = await coro
        return {'ok': True, 'ms': round((time.perf_counter() - started) * 1000, 1), 'detail': detail}
    except Exception as e:
        return {'ok': False, 'ms': round((time.perf_counter() - started) * 1000, 1), 'error': str(e)[:300]}


async def _ping_db():
    from sqlalchemy import text

    from open_webui.internal.db import get_async_db

    async with get_async_db() as db:
        await db.execute(text('SELECT 1'))


async def _ping_redis():
    from open_webui.env import REDIS_URL

    if not REDIS_URL:
        return 'not configured'
    import redis.asyncio as aioredis

    client = aioredis.from_url(REDIS_URL)
    try:
        await client.ping()
    finally:
        await client.aclose()


async def _ping_s3():
    if (os.getenv('STORAGE_PROVIDER') or '') != 's3':
        return 'local storage'
    import boto3

    def head():
        boto3.client(
            's3',
            endpoint_url=os.getenv('S3_ENDPOINT_URL'),
            aws_access_key_id=os.getenv('S3_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('S3_SECRET_ACCESS_KEY'),
            region_name=os.getenv('S3_REGION_NAME') or 'us-east-1',
        ).head_bucket(Bucket=os.getenv('S3_BUCKET_NAME'))

    await asyncio.to_thread(head)


async def _embed(request: Request):
    fn = getattr(request.app.state, 'EMBEDDING_FUNCTION', None)
    if fn is None:
        return 'no embedding function'
    result = fn('Kiểm tra tốc độ embedding của hệ thống NCS AI.', prefix=None)
    if asyncio.iscoroutine(result):
        result = await result
    return {'dimensions': len(result) if isinstance(result, list) else None}


async def _llm_connection(session: aiohttp.ClientSession, url: str, key: str, chat_model: Optional[str]):
    headers = {'Authorization': f'Bearer {key}'} if key else {}
    started = time.perf_counter()
    async with session.get(f'{url}/models', headers=headers) as response:
        body = await response.json(content_type=None)
        if response.status != 200:
            raise RuntimeError(f'GET /models {response.status}')
    models_ms = round((time.perf_counter() - started) * 1000, 1)
    ids = [m.get('id') for m in (body.get('data') or []) if isinstance(m, dict)] if isinstance(body, dict) else []
    result = {'models_ms': models_ms, 'models': ids[:50]}
    model = chat_model if chat_model in ids else (ids[0] if ids else None)
    if model:
        # A tiny streamed completion: time to first token and generation speed.
        payload = {
            'model': model,
            'messages': [{'role': 'user', 'content': 'Đếm từ 1 đến 20, cách nhau bởi dấu phẩy.'}],
            'max_tokens': 64,
            'stream': True,
            'stream_options': {'include_usage': True},
            'chat_template_kwargs': {'enable_thinking': False},
        }
        started = time.perf_counter()
        first = None
        chunks = 0
        usage = None
        async with session.post(f'{url}/chat/completions', json=payload, headers=headers) as response:
            if response.status != 200:
                raise RuntimeError(f'chat/completions {response.status}: {(await response.text())[:200]}')
            async for raw in response.content:
                line = raw.decode('utf-8', 'ignore').strip()
                if not line.startswith('data:') or line.endswith('[DONE]'):
                    continue
                import json

                try:
                    event = json.loads(line[5:])
                except ValueError:
                    continue
                usage = event.get('usage') or usage
                delta = ((event.get('choices') or [{}])[0].get('delta') or {}) if event.get('choices') else {}
                if delta.get('content') or delta.get('reasoning_content'):
                    chunks += 1
                    if first is None:
                        first = time.perf_counter()
        total = time.perf_counter() - started
        tokens = (usage or {}).get('completion_tokens') or chunks
        gen = total - (first - started) if first else total
        result['completion'] = {
            'model': model,
            'ttft_ms': round((first - started) * 1000, 1) if first else None,
            'total_ms': round(total * 1000, 1),
            'output_tokens': tokens,
            'tokens_per_second': round(tokens / gen, 1) if gen > 0 and tokens else None,
        }
    return result


@router.post('/probe')
async def probe(request: Request, llm: bool = True, x_monitor_key: Optional[str] = Header(None)):
    """Measure live response times: database, Redis, file storage, embedding, every model connection."""
    _check(x_monitor_key)
    from open_webui.tracker.config import runtime as tracker_runtime

    TRACKER_MODEL = (await tracker_runtime())['model']

    values = await Config.get_many('openai.api_base_urls', 'openai.api_keys', 'openai.api_configs')
    urls = values.get('openai.api_base_urls') or []
    keys = values.get('openai.api_keys') or []
    configs = values.get('openai.api_configs') or {}
    preferred = (TRACKER_MODEL.split(',')[0].strip() or None) if TRACKER_MODEL else None

    results = {
        'database': _timed(_ping_db()),
        'redis': _timed(_ping_redis()),
        'storage': _timed(_ping_s3()),
        'embedding': _timed(_embed(request)),
    }
    timeout = aiohttp.ClientTimeout(total=60, sock_connect=10)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        llm_tasks = {}
        for idx, url in enumerate(urls):
            cfg = configs.get(str(idx), configs.get(url, {})) or {}
            if not url or not cfg.get('enable', True):
                continue
            key = keys[idx] if idx < len(keys) else ''
            coro = (
                _llm_connection(session, url.rstrip('/'), key, preferred)
                if llm
                else _llm_connection(session, url.rstrip('/'), key, '__none__')
            )
            llm_tasks[f'connection #{idx + 1} ({_mask_url(url)})'] = _timed(coro)
        names = list(results) + list(llm_tasks)
        done = await asyncio.gather(*results.values(), *llm_tasks.values())
    return {'time': time.time(), 'results': dict(zip(names, done))}


@router.post('/reindex')
async def reindex(request: Request, x_monitor_key: Optional[str] = Header(None)):
    """Rebuild knowledge and memory vectors in the background (same as the Documents settings button)."""
    _check(x_monitor_key)
    from open_webui.monitoring.reindex import start

    return await start(request.app)


@router.get('/models')
async def models_access(request: Request, x_monitor_key: Optional[str] = Header(None)):
    """Every model users can pick, with whether it is public."""
    _check(x_monitor_key)
    from open_webui.monitoring.model_access import list_models
    from open_webui.utils.models import default_model_access

    return {'models': await list_models(request.app), 'default_access': await default_model_access()}


class ModelAccessForm(BaseModel):
    model_id: str
    public: bool
    name: str = ''


@router.post('/models/access')
async def set_model_access(form: ModelAccessForm, request: Request, x_monitor_key: Optional[str] = Header(None)):
    _check(x_monitor_key)
    from open_webui.monitoring.model_access import set_public

    try:
        return await set_public(request.app, form.model_id, form.public, form.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class ExtractionTestForm(BaseModel):
    overrides: dict = {}


@router.post('/extraction/test')
async def extraction_test(form: ExtractionTestForm, x_monitor_key: Optional[str] = Header(None)):
    """Run sample files through the document extraction engine (saved settings + unsaved form values)."""
    _check(x_monitor_key)
    from open_webui.monitoring.extraction_test import run_test

    return await run_test(form.overrides)


@router.get('/files/{file_id}/original')
async def file_original(file_id: str, x_monitor_key: Optional[str] = Header(None)):
    """The uploaded file itself (to compare with the text the extraction engine produced)."""
    _check(x_monitor_key)
    from fastapi.responses import FileResponse

    from open_webui.monitoring.files_admin import original_path

    try:
        path, name, content_type = await original_path(file_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail='Không tìm thấy file gốc')
    return FileResponse(path, media_type=content_type, filename=name, content_disposition_type='inline')


@router.post('/files/{file_id}/reprocess')
async def file_reprocess(file_id: str, request: Request, x_monitor_key: Optional[str] = Header(None)):
    """Extract and embed one file again with the current settings."""
    _check(x_monitor_key)
    from open_webui.monitoring.files_admin import reprocess

    try:
        return await reprocess(request.app, file_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail='Không tìm thấy file')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Xử lý lại thất bại: {e}'[:500])
