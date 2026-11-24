"""Monitor page for the per-user terminals (terminal broker, docs/terminal/PHUONG-AN-TACH-TERMINAL.md):

- the terminals (page login): who has one, running or not, busy, idle time, disk use; capacity; recent events;
- Internet use through the egress proxy, stopping / removing a terminal (admin edit session, like the settings).

The monitor calls the broker's /_broker/* API with TERMINAL_BROKER_ADMIN_KEY; Open WebUI never has that key.
"""

import os
from typing import Optional

import aiohttp
from fastapi import APIRouter, Depends, HTTPException

from open_webui.monitoring.server.admin import require_session

router = APIRouter()

BROKER_URL = os.getenv('TERMINAL_BROKER_URL', '').rstrip('/')
BROKER_KEY = os.getenv('TERMINAL_BROKER_ADMIN_KEY', '')


async def _broker(path: str, method: str = 'GET', **params):
    if not BROKER_URL or not BROKER_KEY:
        raise HTTPException(404, 'Terminal broker chưa được cấu hình (TERMINAL_BROKER_URL / TERMINAL_BROKER_ADMIN_KEY)')
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.request(method, f'{BROKER_URL}{path}', params=params or None,
                                       headers={'Authorization': f'Bearer {BROKER_KEY}'}) as response:
                data = await response.json(content_type=None)
                if response.status >= 400:
                    raise HTTPException(502, (data or {}).get('error') or f'Broker HTTP {response.status}')
                return data
    except aiohttp.ClientError as error:
        raise HTTPException(502, f'Không kết nối được terminal broker: {error}')


async def _names(user_ids: list[str]) -> dict[str, dict]:
    ids = [i for i in set(user_ids) if i and not i.startswith('?')]
    if not ids:
        return {}
    from open_webui.monitoring.server.app import rows

    found = await rows('SELECT id, name, email, role FROM "user" WHERE id = ANY(:ids)', ids=ids)
    return {r['id']: r for r in found}


@router.get('/api/terminal/status')
async def status():
    data = await _broker('/_broker/status')
    names = await _names([t['user_id'] for t in data.get('terminals', [])] +
                         [e.get('user_id') or '' for e in data.get('recent_events', [])])
    for item in data.get('terminals', []) + data.get('recent_events', []):
        user = names.get(item.get('user_id') or '')
        item['user_name'] = (user or {}).get('name') or (user or {}).get('email') or item.get('user_id') or ''
        item['user_email'] = (user or {}).get('email') or ''
    return data


@router.get('/api/admin/terminal/egress')
async def egress(hours: int = 24, token: str = Depends(require_session)):
    data = await _broker('/_broker/egress', hours=max(1, min(hours, 24 * 90)))
    names = await _names([u['user_id'] for u in data.get('users', [])] + [a['user_id'] for a in data.get('alerts', [])])
    for item in data.get('users', []) + data.get('alerts', []):
        user = names.get(item.get('user_id') or '')
        item['user_name'] = (user or {}).get('name') or (user or {}).get('email') or item.get('user_id') or ''
    return data


@router.post('/api/admin/terminal/{terminal_id}/stop')
async def stop(terminal_id: str, token: str = Depends(require_session)):
    return await _broker(f'/_broker/terminals/{terminal_id}/stop', method='POST')


@router.delete('/api/admin/terminal/{terminal_id}')
async def remove(terminal_id: str, token: str = Depends(require_session)):
    return await _broker(f'/_broker/terminals/{terminal_id}', method='DELETE')
