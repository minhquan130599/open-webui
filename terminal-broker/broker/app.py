"""Terminal broker: one Open Terminal container per user, behind a single terminal connection in Open WebUI.

Routes:
- /_broker/*      monitor API (status, stop / remove a terminal, egress summary), BROKER_ADMIN_KEY only;
- /healthz        liveness, no key;
- everything else forwarded (HTTP and websockets) to the caller's terminal.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse

from broker import egress
from broker.config import settings
from broker.docker_api import DockerApi
from broker.pool import Pool
from broker.proxy import Proxy
from broker.store import Store

logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'), format='%(asctime)s %(levelname)s %(name)s %(message)s')
log = logging.getLogger('broker')


async def probe(url: str, key: str) -> bool:
    """A terminal is ready when its API answers (any answer below 500)."""
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(f'{url}/api/config', headers={'Authorization': f'Bearer {key}'})
            return response.status_code < 500
    except httpx.HTTPError:
        return False


async def _every(seconds: int, job, name: str) -> None:
    while True:
        await asyncio.sleep(seconds)
        try:
            await job()
        except Exception:
            log.exception('%s failed', name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.api_key:
        raise RuntimeError('BROKER_API_KEY is required')
    docker = DockerApi(settings.docker_url)
    store = Store(os.path.join(settings.data_dir, 'broker.db'))
    pool = Pool(settings, docker, store, probe)
    proxy = Proxy(settings, pool)
    try:
        await pool.reconcile()
    except Exception:
        log.exception('could not read the terminal containers at start')
    tasks = [
        asyncio.create_task(_every(settings.reap_every, pool.reap, 'housekeeping')),
        asyncio.create_task(_every(settings.disk_check_every, pool.check_disk, 'disk check')),
    ]
    app.state.pool, app.state.proxy, app.state.store = pool, proxy, store
    log.info('terminal broker ready: at most %s terminals (%s for admins), %s CPU / %s MB each',
             settings.max_running, settings.admin_reserved, settings.cpus, settings.memory_mb)
    yield
    for task in tasks:
        task.cancel()
    await proxy.close()
    await docker.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def _admin(request: Request) -> bool:
    value = request.headers.get('authorization', '')
    token = value[7:] if value.lower().startswith('bearer ') else value
    return bool(settings.admin_key) and hmac.compare_digest(token.strip(), settings.admin_key) \
        and not request.headers.get('x-user-id')


@app.get('/healthz')
async def healthz():
    return {'ok': True}


@app.get('/_broker/status')
async def status(request: Request):
    if not _admin(request):
        return JSONResponse({'error': 'forbidden'}, status_code=403)
    return request.app.state.pool.status()


@app.post('/_broker/terminals/{terminal_id}/stop')
async def stop(terminal_id: str, request: Request):
    if not _admin(request):
        return JSONResponse({'error': 'forbidden'}, status_code=403)
    return {'ok': await request.app.state.pool.stop_user(terminal_id)}


@app.delete('/_broker/terminals/{terminal_id}')
async def remove(terminal_id: str, request: Request):
    if not _admin(request):
        return JSONResponse({'error': 'forbidden'}, status_code=403)
    return {'ok': await request.app.state.pool.remove_user(terminal_id)}


@app.get('/_broker/egress')
async def egress_summary(request: Request, hours: int = 24):
    if not _admin(request):
        return JSONResponse({'error': 'forbidden'}, status_code=403)
    hours = max(1, min(hours, 24 * 90))
    since = time.time() - hours * 3600
    records = await asyncio.to_thread(egress.read_log, settings.egress_log, since)
    leases = request.app.state.store.leases_since(since)
    return {'hours': hours, **egress.summary(records, leases, settings.upload_alert_mb)}


@app.api_route('/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'])
async def forward(path: str, request: Request):
    if path.startswith('_broker'):
        return JSONResponse({'error': 'forbidden'}, status_code=403)
    return await request.app.state.proxy.http(request, path)


@app.websocket('/{path:path}')
async def forward_ws(websocket: WebSocket, path: str):
    if path.startswith('_broker'):
        with suppress(Exception):
            await websocket.close(code=4003)
        return
    await websocket.app.state.proxy.websocket(websocket, path)
