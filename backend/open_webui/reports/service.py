"""Sending a report and running due schedules.

Each due schedule is taken by one worker only (a lease on its row), so several app workers never send it twice.
"""

import asyncio
import logging
import random
import time
from typing import Optional

from open_webui.models.channels import Channels
from open_webui.models.users import Users
from open_webui.reports import build, telegram
from open_webui.reports.models import Schedules, TelegramLinks
from open_webui.reports.schedule import next_run
from open_webui.reports.settings import get_settings

log = logging.getLogger(__name__)

CHECK_SECONDS = 60
LEASE_SECONDS = 900


async def email_recipients(owner, config: dict, settings: dict, channels: list) -> tuple[list[str], list[str]]:
    """(addresses to send to, addresses refused with the reason)."""
    from open_webui.tracker import mailer

    wanted = ([owner.email.lower()] if config['email'].get('self') and owner.email else []) + list(config['email'].get('to') or [])
    sender = (await mailer.settings()).get('sender') or ''
    domain = sender.split('@')[-1].lower() if '@' in sender else ''
    member_ids = None
    out, refused = [], []
    for address in dict.fromkeys(wanted):
        if settings['reports.email.internal_only'] and domain and not address.endswith('@' + domain):
            refused.append(f'{address} (chỉ được gửi tới @{domain})')
            continue
        if address != (owner.email or '').lower() and not settings['reports.allow_outside']:
            if member_ids is None:
                member_ids = {m.user_id for m in await Channels.get_members_by_channel_ids([c.id for c in channels])} if channels else set()
            user = await Users.get_user_by_email(address)
            if not user or user.id not in member_ids:
                refused.append(f'{address} (không phải thành viên các kênh trong báo cáo)')
                continue
        out.append(address)
    return out, refused


async def deliver(schedule_name: str, owner, config: dict, settings: dict, built: dict, test: bool = False) -> dict:
    """Send by email and Telegram; a test goes to the owner's own email and the chosen chats only."""
    from open_webui.tracker import mailer

    sent, errors = [], []
    channels = await build.report_channels(owner, config, settings)
    subject = ('[Gửi thử] ' if test else '') + built['subject']
    if config['email'].get('enabled') and settings['reports.email.enable']:
        if test:
            addresses, refused = ([owner.email] if owner.email else []), []
        else:
            addresses, refused = await email_recipients(owner, config, settings, channels)
        errors += [f'Không gửi email tới {r}' for r in refused]
        mail_settings = await mailer.settings()
        missing = mailer.missing(mail_settings)
        if addresses and missing:
            errors.append('Email chưa cấu hình: ' + ', '.join(missing))
        else:
            for address in addresses:
                try:
                    await mailer.send(address, subject, built['html'], mail_settings)
                    sent.append(f'email {address}')
                except Exception as error:
                    errors.append(f'Email {address}: {error}')
    if config['telegram'].get('enabled') and settings['reports.telegram.enable']:
        token = settings['reports.telegram.bot_token']
        links = [l for l in await TelegramLinks.by_ids(config['telegram'].get('link_ids') or []) if l.user_id == owner.id and l.chat_id]
        if not token:
            errors.append('Telegram chưa cấu hình bot token')
        for link in links:
            if link.chat_type != 'private' and not settings['reports.telegram.allow_groups']:
                errors.append(f'Telegram "{link.chat_title}": không được gửi vào nhóm')
                continue
            text = ('<i>[Gửi thử]</i>\n' if test else '') + built['telegram']
            try:
                await telegram.send(token, link.chat_id, text, ('Mở bảng tiến độ', built['link']) if built['link'] else None)
                sent.append(f'Telegram {link.chat_title}')
            except Exception as error:
                errors.append(f'Telegram "{link.chat_title}": {error}')
    return {'sent': sent, 'errors': errors}


async def run(schedule, lease_id: Optional[str] = None, force: bool = False) -> dict:
    """Build and send one schedule now; records the result and the next send time."""
    settings = await get_settings()
    owner = await Users.get_user_by_id(schedule.user_id)
    started = time.time_ns()
    result = {'status': 'error', 'sent': [], 'errors': []}
    digest = schedule.last_digest
    try:
        if not owner:
            raise RuntimeError('Người tạo báo cáo không còn tài khoản')
        if not await build.can_create(owner, settings):
            raise RuntimeError('Người tạo không còn quyền báo cáo định kỳ (theo cài đặt quản trị)')
        built = await build.build(schedule.name, owner, schedule.config, settings, schedule.last_run_at)
        if not force and schedule.config.get('skip_unchanged') and schedule.last_digest == built['digest'] and schedule.last_status == 'sent':
            result = {'status': 'skipped', 'sent': [], 'errors': []}
        else:
            delivered = await deliver(schedule.name, owner, schedule.config, settings, built)
            digest = built['digest']
            result = {'status': 'sent' if delivered['sent'] else 'error', **delivered}
            if not delivered['sent'] and not delivered['errors']:
                result['errors'] = ['Không có người nhận nào']
    except Exception as error:
        log.warning('Report %s (%s) failed: %s', schedule.id, schedule.name, error)
        result['errors'] = [str(error)]
    fields = dict(
        last_run_at=started,
        last_status=result['status'],
        last_error='; '.join(result['errors'])[:2000] or None,
        last_digest=digest,
        next_run_at=next_run(schedule.config),
    )
    if lease_id:
        await Schedules.finish(schedule.id, lease_id, **fields)
    else:
        await Schedules.update(schedule.id, **fields)
    log.info('Report %s (%s): %s %s', schedule.id, schedule.name, result['status'], result)
    return result


async def run_due() -> int:
    settings = await get_settings()
    if not settings['reports.enable']:
        return 0
    count = 0
    for schedule, lease_id in await Schedules.claim_due(time.time_ns(), LEASE_SECONDS):
        await run(schedule, lease_id)
        count += 1
    return count


async def report_loop() -> None:
    log.info('Scheduled reports started')
    poller = asyncio.create_task(telegram.poll_loop(get_settings))
    try:
        while True:
            try:
                await run_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception('Scheduled reports run failed')
            await asyncio.sleep(CHECK_SECONDS + random.uniform(0, 5))
    finally:
        poller.cancel()
