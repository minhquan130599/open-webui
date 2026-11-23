"""Monitor page for the Telegram bot (scheduled reports + asking the AI): status for the page login; settings,
bot check and linked accounts behind the admin edit session, like the other settings."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from open_webui.monitoring.server.admin import require_session

router = APIRouter()


def _result(result):
    if isinstance(result, dict) and result.get('error') and len(result) == 1:
        raise HTTPException(502, result['error'])
    return result


@router.get('/api/telegram/status')
async def status():
    from open_webui.monitoring.server.app import webui

    return _result(await webui('/telegram/status'))


@router.get('/api/admin/telegram/settings')
async def get_settings(token: str = Depends(require_session)):
    from open_webui.monitoring.server.app import webui

    return _result(await webui('/telegram/settings'))


class SettingsForm(BaseModel):
    changes: dict[str, Any]


@router.post('/api/admin/telegram/settings')
async def save_settings(form: SettingsForm, token: str = Depends(require_session)):
    from open_webui.monitoring.server.app import webui

    return _result(await webui('/telegram/settings', method='POST', json_body={'changes': form.changes}, timeout=30))


class TestForm(BaseModel):
    token: str = ''


@router.post('/api/admin/telegram/test')
async def test_bot(form: TestForm, token: str = Depends(require_session)):
    from open_webui.monitoring.server.app import webui

    return _result(await webui('/telegram/test', method='POST', json_body={'token': form.token}, timeout=30))


@router.get('/api/admin/telegram/accounts')
async def accounts(token: str = Depends(require_session)):
    from open_webui.monitoring.server.app import webui

    result = await webui('/telegram/accounts')
    if isinstance(result, dict) and result.get('error'):
        raise HTTPException(502, result['error'])
    return {'items': result}


@router.delete('/api/admin/telegram/accounts/{user_id}')
async def unlink(user_id: str, token: str = Depends(require_session)):
    from open_webui.monitoring.server.app import webui

    return _result(await webui(f'/telegram/accounts/{user_id}', method='DELETE'))
