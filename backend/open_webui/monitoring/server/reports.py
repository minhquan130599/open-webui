"""Monitor page for scheduled reports: every schedule (page login), and the settings and edits (admin edit
session, like the other settings)."""

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from open_webui.monitoring.server.admin import require_session

router = APIRouter()


def _result(result: dict):
    if isinstance(result, dict) and result.get('error') and len(result) == 1:
        raise HTTPException(502, result['error'])
    return result


@router.get('/api/reports/schedules')
async def schedules():
    from open_webui.monitoring.server.app import webui

    result = await webui('/reports/schedules', timeout=30)
    if isinstance(result, dict) and result.get('error'):
        raise HTTPException(502, result['error'])
    return {'items': result}


@router.get('/api/reports/queue')
async def queue():
    from open_webui.monitoring.server.app import webui

    result = await webui('/reports/queue', timeout=30)
    if isinstance(result, dict) and result.get('error'):
        raise HTTPException(502, result['error'])
    return {'items': result}


@router.get('/api/admin/reports/settings')
async def get_settings(token: str = Depends(require_session)):
    from open_webui.monitoring.server.app import webui

    return _result(await webui('/reports/settings'))


class SettingsForm(BaseModel):
    changes: dict[str, Any]


@router.post('/api/admin/reports/settings')
async def save_settings(form: SettingsForm, token: str = Depends(require_session)):
    from open_webui.monitoring.server.app import webui

    return _result(await webui('/reports/settings', method='POST', json_body={'changes': form.changes}, timeout=30))


class ScheduleForm(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    config: Optional[dict] = None


@router.put('/api/admin/reports/schedules/{schedule_id}')
async def update_schedule(schedule_id: str, form: ScheduleForm, token: str = Depends(require_session)):
    from open_webui.monitoring.server.app import webui

    body = {k: v for k, v in form.model_dump().items() if v is not None}
    return _result(await webui(f'/reports/schedules/{schedule_id}', method='PUT', json_body=body))


@router.post('/api/admin/reports/schedules/{schedule_id}/run')
async def run_schedule(schedule_id: str, token: str = Depends(require_session)):
    from open_webui.monitoring.server.app import webui

    # Writing the comments calls the model: allow a few minutes.
    return _result(await webui(f'/reports/schedules/{schedule_id}/run', method='POST', timeout=360))


@router.delete('/api/admin/reports/schedules/{schedule_id}')
async def delete_schedule(schedule_id: str, token: str = Depends(require_session)):
    from open_webui.monitoring.server.app import webui

    return _result(await webui(f'/reports/schedules/{schedule_id}', method='DELETE'))


class TelegramForm(BaseModel):
    token: str = ''


@router.post('/api/admin/reports/telegram/test')
async def telegram_test(form: TelegramForm, token: str = Depends(require_session)):
    from open_webui.monitoring.server.app import webui

    return _result(await webui('/reports/telegram/test', method='POST', json_body={'token': form.token}, timeout=30))
