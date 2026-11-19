"""Scheduled reports API (/api/v1/reports): the user's schedules, preview and test send, reports they receive,
Telegram chats they linked, and the admin settings."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from open_webui.models.users import Users
from open_webui.reports import build, service, telegram
from open_webui.reports.models import Schedules, TelegramLinks
from open_webui.reports.schedule import describe, next_run, normalize_config
from open_webui.reports.settings import get_settings, public, save_settings
from open_webui.utils.auth import get_admin_user, get_verified_user

router = APIRouter()


class ScheduleForm(BaseModel):
    name: str
    enabled: bool = True
    config: dict = {}


class SettingsForm(BaseModel):
    changes: dict


def _bad(message: str):
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


async def _settings_or_404() -> dict:
    settings = await get_settings()
    if not settings['reports.enable']:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Báo cáo định kỳ đang tắt')
    return settings


async def _require_creator(user) -> dict:
    settings = await _settings_or_404()
    if not await build.can_create(user, settings):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Bạn không có quyền tạo báo cáo định kỳ')
    return settings


def _view(schedule, users: Optional[dict] = None) -> dict:
    out = schedule.model_dump(exclude={'last_digest'})
    out['when'] = describe(schedule.config or {})
    if users is not None:
        owner = users.get(schedule.user_id)
        out['owner'] = {'id': schedule.user_id, 'name': owner.name if owner else '', 'email': owner.email if owner else ''}
    return out


async def _validated(user, form: ScheduleForm, settings: dict) -> dict:
    name = (form.name or '').strip()[:200]
    if not name:
        _bad('Đặt tên cho báo cáo')
    try:
        config = normalize_config(form.config)
    except ValueError as error:
        _bad(str(error))
    allowed = {c.id for c in await build.eligible_channels(user, settings)}
    if config['scope'] == 'pick':
        config['channel_ids'] = [c for c in config['channel_ids'] if c in allowed]
        if not config['channel_ids']:
            _bad('Không có kênh nào bạn được chọn trong báo cáo')
        if len(config['channel_ids']) > settings['reports.max_channels']:
            _bad(f'Tối đa {settings["reports.max_channels"]} kênh mỗi báo cáo')
    if not settings['reports.allow_prompt']:
        config['prompt'] = ''
    if config['email']['enabled'] and not settings['reports.email.enable']:
        _bad('Quản trị viên chưa bật gửi qua email')
    if config['telegram']['enabled']:
        if not settings['reports.telegram.enable']:
            _bad('Quản trị viên chưa bật gửi qua Telegram')
        mine = {l.id for l in await TelegramLinks.list(user.id)}
        config['telegram']['link_ids'] = [i for i in config['telegram']['link_ids'] if i in mine]
        if not config['telegram']['link_ids']:
            _bad('Telegram: chọn ít nhất một chat đã liên kết')
    return {'name': name, 'config': config}


# ---------------------------------------------------------------- user


@router.get('/access')
async def access(user=Depends(get_verified_user)):
    """What the reports page shows this user (also whether the sidebar shows it)."""
    settings = await get_settings()
    can = await build.can_create(user, settings)
    receiving = await _receiving(user) if settings['reports.enable'] else []
    show = settings['reports.enable'] and (can or (settings['reports.hidden_mode'] == 'receiving' and bool(receiving)))
    return {
        'enabled': settings['reports.enable'],
        'can_create': can,
        'show': bool(show),
        'allow_prompt': settings['reports.allow_prompt'],
        'allow_outside': settings['reports.allow_outside'],
        'email_enabled': settings['reports.email.enable'],
        'telegram_enabled': settings['reports.telegram.enable'] and bool(settings['reports.telegram.bot_token']),
        'telegram_bot': settings['reports.telegram.bot_username'],
        'allow_groups': settings['reports.telegram.allow_groups'],
        'max_schedules': settings['reports.max_schedules'],
        'max_channels': settings['reports.max_channels'],
        'default_prompt': settings['reports.default_prompt'],
        'owners_only': settings['reports.who'] == 'owners' and user.role != 'admin',
        'email': user.email,
    }


@router.get('/channels')
async def channels(user=Depends(get_verified_user)):
    settings = await _require_creator(user)
    return [{'id': c.id, 'name': c.name} for c in await build.eligible_channels(user, settings)]


@router.get('/')
async def list_schedules(user=Depends(get_verified_user)):
    await _settings_or_404()
    return [_view(s) for s in await Schedules.list(user.id)]


@router.post('/')
async def create_schedule(form: ScheduleForm, user=Depends(get_verified_user)):
    settings = await _require_creator(user)
    if await Schedules.count(user.id) >= settings['reports.max_schedules']:
        _bad(f'Tối đa {settings["reports.max_schedules"]} lịch báo cáo mỗi người')
    data = await _validated(user, form, settings)
    schedule = await Schedules.create(user.id, data['name'], form.enabled, data['config'], next_run(data['config']))
    return _view(schedule)


async def _own(schedule_id: str, user):
    schedule = await Schedules.get(schedule_id)
    if not schedule or (schedule.user_id != user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Không có lịch báo cáo này')
    return schedule


@router.put('/{schedule_id}')
async def update_schedule(schedule_id: str, form: ScheduleForm, user=Depends(get_verified_user)):
    settings = await _require_creator(user)
    await _own(schedule_id, user)
    data = await _validated(user, form, settings)
    schedule = await Schedules.update(
        schedule_id, name=data['name'], enabled=form.enabled, config=data['config'], next_run_at=next_run(data['config'])
    )
    await service.cancel_jobs(schedule_id)
    return _view(schedule)


@router.delete('/{schedule_id}')
async def delete_schedule(schedule_id: str, user=Depends(get_verified_user)):
    await _own(schedule_id, user)
    await service.cancel_jobs(schedule_id)
    return {'ok': await Schedules.delete(schedule_id)}


@router.post('/preview')
async def preview(form: ScheduleForm, user=Depends(get_verified_user)):
    """The report as it would be sent now, from the settings on the form (saved or not)."""
    settings = await _require_creator(user)
    data = await _validated(user, form, settings)
    built = await build.build(data['name'], user, data['config'], settings)
    return {k: built[k] for k in ('subject', 'html', 'telegram', 'totals', 'channels')}


@router.post('/test')
async def send_test(form: ScheduleForm, user=Depends(get_verified_user)):
    """Send the report now to the user's own email and the chosen Telegram chats (nobody else)."""
    settings = await _require_creator(user)
    data = await _validated(user, form, settings)
    built = await build.build(data['name'], user, data['config'], settings)
    return await service.deliver(data['name'], user, data['config'], settings, built, test=True)


async def _receiving(user) -> list:
    email = (user.email or '').lower()
    if not email:
        return []
    out = []
    for schedule in await Schedules.list():
        if schedule.user_id != user.id and email in ((schedule.config or {}).get('email') or {}).get('to', []):
            out.append(schedule)
    return out


@router.get('/receiving')
async def receiving(user=Depends(get_verified_user)):
    """Reports others send to this user's email, so they can stop receiving them."""
    await _settings_or_404()
    schedules = await _receiving(user)
    owners = {u.id: u for u in await Users.get_users_by_user_ids(list({s.user_id for s in schedules}))} if schedules else {}
    return [
        {
            'id': s.id,
            'name': s.name,
            'when': describe(s.config or {}),
            'enabled': s.enabled,
            'owner': owners[s.user_id].name if s.user_id in owners else '',
            'channels': len(s.config.get('channel_ids') or []) if s.config.get('scope') == 'pick' else None,
        }
        for s in schedules
    ]


@router.post('/receiving/{schedule_id}/unsubscribe')
async def unsubscribe(schedule_id: str, user=Depends(get_verified_user)):
    schedule = await Schedules.get(schedule_id)
    email = (user.email or '').lower()
    if not schedule or email not in ((schedule.config or {}).get('email') or {}).get('to', []):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Bạn không nhận báo cáo này')
    config = dict(schedule.config)
    config['email'] = {**config['email'], 'to': [a for a in config['email']['to'] if a != email]}
    await Schedules.update(schedule_id, config=config)
    return {'ok': True}


# ---------------------------------------------------------------- Telegram


@router.get('/telegram')
async def telegram_links(user=Depends(get_verified_user)):
    await _settings_or_404()
    return [l.model_dump(exclude={'code'}) for l in await TelegramLinks.list(user.id)]


@router.post('/telegram/link')
async def telegram_new_link(user=Depends(get_verified_user)):
    """A one-time code: open the t.me link (private chat) or send "/link <code>" in a group with the bot."""
    settings = await _require_creator(user)
    if not (settings['reports.telegram.enable'] and settings['reports.telegram.bot_token']):
        _bad('Quản trị viên chưa bật Telegram')
    bot = settings['reports.telegram.bot_username']
    link = await TelegramLinks.new_code(user.id)
    return {
        'id': link.id,
        'code': link.code,
        'bot': bot,
        'url': f'https://t.me/{bot}?start={link.code}' if bot else '',
        'group_command': f'/link {link.code}',
        'allow_groups': settings['reports.telegram.allow_groups'],
    }


@router.get('/telegram/link/{link_id}')
async def telegram_link_status(link_id: str, user=Depends(get_verified_user)):
    link = await TelegramLinks.get_code(link_id, user.id)
    if not link:
        return {'linked': False, 'gone': True}
    return {'linked': bool(link.chat_id), 'chat_title': link.chat_title, 'chat_type': link.chat_type}


@router.delete('/telegram/{link_id}')
async def telegram_unlink(link_id: str, user=Depends(get_verified_user)):
    return {'ok': await TelegramLinks.delete(link_id, user.id)}


# ---------------------------------------------------------------- admin


@router.get('/admin/settings')
async def admin_get_settings(user=Depends(get_admin_user)):
    return public(await get_settings())


@router.post('/admin/settings')
async def admin_save_settings(form: SettingsForm, user=Depends(get_admin_user)):
    try:
        values = await save_settings(form.changes)
    except ValueError as error:
        _bad(str(error))
    return public(await _refresh_bot(values))


async def _refresh_bot(values: dict) -> dict:
    """The bot's username follows the token (used for the t.me link)."""
    token = values.get('reports.telegram.bot_token')
    if token and values.get('reports.telegram.enable'):
        try:
            info = await telegram.bot_info(token)
            if info['username'] != values.get('reports.telegram.bot_username'):
                values = await save_settings({'reports.telegram.bot_username': info['username']})
        except telegram.TelegramError:
            pass
    return values


class TelegramTestForm(BaseModel):
    token: str = ''


@router.post('/admin/telegram/test')
async def admin_telegram_test(form: TelegramTestForm, user=Depends(get_admin_user)):
    token = form.token or (await get_settings())['reports.telegram.bot_token']
    try:
        return {'ok': True, **(await telegram.bot_info(token))}
    except telegram.TelegramError as error:
        return {'ok': False, 'error': str(error)}


@router.get('/admin/schedules')
async def admin_schedules(user=Depends(get_admin_user)):
    schedules = await Schedules.list()
    users = {u.id: u for u in await Users.get_users_by_user_ids(list({s.user_id for s in schedules}))} if schedules else {}
    return [_view(s, users) for s in schedules]


@router.post('/admin/schedules/{schedule_id}/enabled')
async def admin_set_enabled(schedule_id: str, enabled: bool, user=Depends(get_admin_user)):
    schedule = await Schedules.get(schedule_id)
    if not schedule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Không có lịch báo cáo này')
    await Schedules.update(schedule_id, enabled=enabled, next_run_at=next_run(schedule.config) if enabled else schedule.next_run_at)
    await service.cancel_jobs(schedule_id)
    return {'ok': True}


@router.post('/admin/schedules/{schedule_id}/run')
async def admin_run(schedule_id: str, user=Depends(get_admin_user)):
    schedule = await Schedules.get(schedule_id)
    if not schedule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Không có lịch báo cáo này')
    return await service.run(schedule, force=True)


@router.delete('/admin/schedules/{schedule_id}')
async def admin_delete(schedule_id: str, user=Depends(get_admin_user)):
    await service.cancel_jobs(schedule_id)
    return {'ok': await Schedules.delete(schedule_id)}
