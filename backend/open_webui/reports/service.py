"""Running the schedules through a queue.

1. Queue: a schedule sending within `reports.prepare_minutes` gets a job (one per send time, whatever the number
   of app workers).
2. Write: at most `reports.workers` writers across the app take the queued jobs, soonest first, and write the AI
   comments only while the model is not busy (load.status), so reports never slow down the chats.
3. Send: at the send time the figures are counted again (up to that moment) and sent with the comments written
   ahead. A job not written `reports.late_minutes` after its time is sent without comments, never later.

Every step takes its job with a lease on the row, so a report is never sent twice; a send cut in the middle
(the worker stopped) is marked as an error, not sent again.
"""

import asyncio
import datetime as dt
import logging
import random
import time

from open_webui.models.channels import Channels
from open_webui.models.users import Users
from open_webui.reports import build, load, telegram
from open_webui.reports.models import Jobs, Locks, Schedules, TelegramLinks
from open_webui.reports.schedule import TZ, next_run
from open_webui.reports.settings import get_settings
from open_webui.tracker.config import TRACKER_LLM_TIMEOUT

log = logging.getLogger(__name__)

CHECK_SECONDS = 30
SEND_LEASE_SECONDS = 900
WRITE_LEASE_SECONDS = TRACKER_LLM_TIMEOUT + 180
WRITE_RETRY_MINUTES = (2, 10)  # a failed write is tried again after these waits, then sent without comments
KEEP_DAYS = 30  # finished jobs kept for the monitor
MAIL_BATCH = 50  # recipients per email (Microsoft 365 takes up to 500)
MAIL_RETRY_SECONDS = (30, 120)  # waits when the mail server limits the sending rate
NO_COMMENT_NOTE = 'Nhận xét của AI chưa có ở kỳ này; số liệu vẫn đầy đủ.'
STALLED_NOTE = 'Bị gián đoạn khi đang gửi (máy chủ khởi động lại?); không gửi lại để tránh gửi trùng'


def _hhmm(ns: int) -> str:
    return dt.datetime.fromtimestamp(ns / 1e9, TZ).strftime('%H:%M %d/%m')


# ---------------------------------------------------------------- delivery


async def email_recipients(owner, config: dict, settings: dict, channels: list) -> tuple[list[str], list[str]]:
    """(addresses to send to, addresses refused with the reason)."""
    from open_webui.tracker import mailer

    wanted = ([owner.email.lower()] if config['email'].get('self') and owner.email else []) + list(config['email'].get('to') or [])
    sender = (await mailer.settings()).get('sender') or ''
    domain = sender.split('@')[-1].lower() if '@' in sender else ''
    member_ids = None
    out, refused = [], []
    for address in dict.fromkeys(a.lower() for a in wanted):
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


def mail_groups(addresses: list[str], mode: str, owner_email: str, sender: str) -> list[tuple[list[str], list[str]]]:
    """(to, bcc) of each email: one per recipient ('separate'), or one per MAIL_BATCH recipients, all visible ('to')
    or hidden from each other ('bcc': addressed to the report's creator if a recipient, else to the sender)."""
    if mode == 'separate':
        return [([a], []) for a in addresses]
    groups = []
    for start in range(0, len(addresses), MAIL_BATCH):
        chunk = addresses[start : start + MAIL_BATCH]
        if mode == 'to' or len(chunk) == 1:
            groups.append((chunk, []))
            continue
        head = owner_email if owner_email in chunk else sender
        groups.append(([head], [a for a in chunk if a != head]))
    return groups


async def _send_mail(to: list[str], bcc: list[str], subject: str, body: str, mail_settings: dict) -> list[str]:
    """One email, sent again a little later while the server is limiting the rate; returns refused addresses."""
    from open_webui.tracker import mailer

    for attempt in range(len(MAIL_RETRY_SECONDS) + 1):
        try:
            return await mailer.send(to, subject, body, mail_settings, bcc=bcc)
        except mailer.MailError as error:
            if not getattr(error, 'transient', False) or attempt == len(MAIL_RETRY_SECONDS):
                raise
            log.warning('Report email limited (%s); sending again in %ss', error, MAIL_RETRY_SECONDS[attempt])
            await asyncio.sleep(MAIL_RETRY_SECONDS[attempt])
    return []


async def deliver(schedule_name: str, owner, config: dict, settings: dict, built: dict, test: bool = False) -> dict:
    """Send by email and Telegram; a test goes to the owner's own email and the chosen chats only."""
    from open_webui.tracker import mailer

    sent, errors = [], []
    channels = await build.report_channels(owner, config, settings)
    subject = ('[Gửi thử] ' if test else '') + built['subject']
    if config['email'].get('enabled') and settings['reports.email.enable']:
        if test:
            addresses, refused = ([owner.email.lower()] if owner.email else []), []
        else:
            addresses, refused = await email_recipients(owner, config, settings, channels)
        errors += [f'Không gửi email tới {r}' for r in refused]
        mail_settings = await mailer.settings()
        missing = mailer.missing(mail_settings)
        if addresses and missing:
            errors.append('Email chưa cấu hình: ' + ', '.join(missing))
        elif addresses:
            sender = (mail_settings.get('sender') or '').lower()
            mode = 'separate' if test else settings['reports.email.mode']
            for to, bcc in mail_groups(addresses, mode, (owner.email or '').lower(), sender):
                group = [a for a in [*to, *bcc] if a in addresses]
                try:
                    refused_now = {a.lower() for a in await _send_mail(to, bcc, subject, built['html'], mail_settings)}
                except Exception as error:
                    errors.append(f'Email {", ".join(group)}: {error}')
                    continue
                sent += [f'email {a}' for a in group if a not in refused_now]
                errors += [f'Email {a}: máy chủ từ chối người nhận' for a in group if a in refused_now]
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


async def run(schedule, force: bool = False, job=None) -> dict:
    """Send one schedule now, with the figures counted now; the comments are the ones the job wrote ahead, or
    written now when there is no job (send now). Records the result and the next send time."""
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
        data, text = await build.figures(owner, schedule.config, settings, schedule.last_run_at)
        now_digest = build.digest(text)
        if not force and schedule.config.get('skip_unchanged') and schedule.last_digest == now_digest and schedule.last_status == 'sent':
            result = {'status': 'skipped', 'sent': [], 'errors': []}
        else:
            if job is None:
                try:
                    comment = await build.comments(schedule.name, schedule.config, settings, text) if data['projects'] else ''
                except Exception as error:
                    log.warning('Report "%s": AI comments failed (%s)', schedule.name, error)
                    comment = ''
            else:
                comment = job.comment or ''
            note = NO_COMMENT_NOTE if data['projects'] and not comment else ''
            built = await build.render(schedule.name, owner, schedule.config, data, text, comment, note)
            delivered = await deliver(schedule.name, owner, schedule.config, settings, built)
            digest = now_digest
            result = {'status': 'sent' if delivered['sent'] else 'error', **delivered}
            if not delivered['sent'] and not delivered['errors']:
                result['errors'] = ['Không có người nhận nào']
    except Exception as error:
        log.warning('Report %s (%s) failed: %s', schedule.id, schedule.name, error)
        result['errors'] = [str(error)]
    await Schedules.update(
        schedule.id,
        last_run_at=started,
        last_status=result['status'],
        last_error='; '.join(result['errors'])[:2000] or None,
        last_digest=digest,
        next_run_at=next_run(schedule.config),
    )
    log.info('Report %s (%s): %s %s', schedule.id, schedule.name, result['status'], result)
    return result


# ---------------------------------------------------------------- queue


def _current(schedule, job) -> bool:
    """The job still matches its schedule (not deleted, paused or moved to another time)."""
    return bool(schedule and schedule.enabled and schedule.next_run_at == job.run_at)


async def _close(job, status: str, note: str, **fields) -> bool:
    return await Jobs.update(job.id, job.lease_id, status=status, note=note[:1000], lease_id=None, lease_until=None, **fields)


async def write(job, settings: dict) -> None:
    """Write the comments of one queued job (the writer holds its lease)."""
    schedule = await Schedules.get(job.schedule_id)
    if not _current(schedule, job):
        await _close(job, 'cancelled', 'Lịch đã đổi giờ, tạm dừng hoặc bị xóa')
        return
    owner = await Users.get_user_by_id(schedule.user_id)
    if not owner or not await build.can_create(owner, settings):
        await _close(job, 'ready', 'Người tạo không còn tài khoản hoặc quyền: lúc gửi sẽ báo lỗi')
        return
    data, text = await build.figures(owner, schedule.config, settings, schedule.last_run_at)
    digest = build.digest(text)
    if not data['projects']:
        await _close(job, 'ready', 'Không có kênh nào trong báo cáo: không cần viết nhận xét')
        return
    if schedule.config.get('skip_unchanged') and schedule.last_status == 'sent' and schedule.last_digest == digest:
        await _close(job, 'ready', 'Số liệu chưa đổi so với lần gửi trước: chưa cần viết (lúc gửi sẽ kiểm tra lại)')
        return
    attempts = job.attempts + 1
    try:
        comment = await build.comments(schedule.name, schedule.config, settings, text)
        if not comment:
            raise RuntimeError('model trả về nội dung rỗng')
    except Exception as error:
        now = time.time_ns()
        retry_at = now + WRITE_RETRY_MINUTES[attempts - 1] * 60 * 10**9 if attempts <= len(WRITE_RETRY_MINUTES) else None
        if retry_at and retry_at < job.run_at + settings['reports.late_minutes'] * 60 * 10**9:
            await _close(job, 'queued', f'Viết lỗi lần {attempts} ({error}); thử lại lúc {_hhmm(retry_at)}',
                         attempts=attempts, retry_at=retry_at)
        else:
            await _close(job, 'ready', f'Không viết được nhận xét ({error}): sẽ gửi số liệu không kèm nhận xét',
                         attempts=attempts)
        log.warning('Report %s: writing the comments failed (%s)', schedule.name, error)
        return
    await _close(job, 'ready', f'Đã viết sẵn nhận xét lúc {_hhmm(time.time_ns())}', attempts=attempts,
                 comment=comment, comment_digest=digest, written_at=time.time_ns())


async def write_round() -> int:
    """Take a writer slot and write the queued jobs one by one while the model is free."""
    settings = await get_settings()
    if not settings['reports.enable'] or not await Jobs.has_writable(time.time_ns()):
        return 0
    slot = lease = None
    for index in range(settings['reports.workers']):
        lease = await Locks.acquire(f'report_writer:{index}', WRITE_LEASE_SECONDS)
        if lease:
            slot = f'report_writer:{index}'
            break
    if not lease:
        return 0
    written = 0
    try:
        while await Jobs.has_writable(time.time_ns()):
            state = await load.status(
                settings['reports.model'] or None, settings['reports.busy_waiting'], settings['reports.busy_seconds']
            )
            if state['busy']:
                await Jobs.note_waiting(f'Chờ LLM rảnh (kiểm tra lúc {_hhmm(time.time_ns())}): {state["reason"]}')
                break
            if not await Locks.acquire(slot, WRITE_LEASE_SECONDS, lease['lease_id']):
                break
            job = await Jobs.claim_write(time.time_ns(), WRITE_LEASE_SECONDS)
            if not job:
                break
            await write(job, settings)
            written += 1
            settings = await get_settings()
            if not settings['reports.enable']:
                break
    finally:
        await Locks.release(slot, lease['lease_id'])
    return written


async def send_job(job) -> dict:
    schedule = await Schedules.get(job.schedule_id)
    if not _current(schedule, job):
        await _close(job, 'cancelled', 'Lịch đã đổi giờ, tạm dừng hoặc bị xóa')
        return {'status': 'cancelled'}
    result = await run(schedule, job=job)
    note = {'sent': 'Đã gửi', 'skipped': 'Bỏ qua: không có thay đổi'}.get(result['status'], 'Lỗi khi gửi')
    if result['status'] != 'skipped' and not job.comment:
        note += ' (không kèm nhận xét AI)'
    await _close(job, result['status'], note, result=result)
    return result


async def tick() -> int:
    """Queue the coming sends, close the cut ones, send the due ones; returns how many were sent."""
    settings = await get_settings()
    if not settings['reports.enable']:
        return 0
    now = time.time_ns()
    for schedule in await Schedules.due_for_queue(now + settings['reports.prepare_minutes'] * 60 * 10**9):
        await Jobs.enqueue(schedule.id, schedule.next_run_at)
    for job in await Jobs.stalled_sends(now):
        if await _close(job, 'error', STALLED_NOTE):
            schedule = await Schedules.get(job.schedule_id)
            if schedule and schedule.next_run_at == job.run_at:
                await Schedules.update(schedule.id, last_run_at=job.run_at, last_status='error', last_error=STALLED_NOTE,
                                       next_run_at=next_run(schedule.config))
    count = 0
    late = settings['reports.late_minutes'] * 60 * 10**9
    while job := await Jobs.claim_send(time.time_ns(), late, SEND_LEASE_SECONDS):
        await send_job(job)
        count += 1
    if random.random() < 0.01:
        await Jobs.prune(now - KEEP_DAYS * 86_400 * 10**9)
    return count


async def _forever(name: str, step) -> None:
    while True:
        try:
            await step()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('Scheduled reports: %s failed', name)
        await asyncio.sleep(CHECK_SECONDS + random.uniform(0, 5))


async def report_loop() -> None:
    log.info('Scheduled reports started')
    poller = asyncio.create_task(telegram.poll_loop(get_settings))
    # Writing waits for the model and can take minutes: it runs beside sending, which must stay on time.
    writer = asyncio.create_task(_forever('writing', write_round))
    try:
        await _forever('sending', tick)
    finally:
        poller.cancel()
        writer.cancel()


async def cancel_jobs(schedule_id: str) -> None:
    """The schedule changed: its comments written ahead no longer fit (queued again on the next round)."""
    await Jobs.cancel(schedule_id)


async def queue_view(limit: int = 200) -> list[dict]:
    """The queue for the monitor, with the schedule names."""
    jobs = await Jobs.list(limit)
    names = {s.id: s for s in await Schedules.list()} if jobs else {}
    out = []
    for job in jobs:
        schedule = names.get(job.schedule_id)
        out.append({
            'id': job.id,
            'schedule_id': job.schedule_id,
            'name': schedule.name if schedule else '(đã xóa)',
            'run_at': job.run_at,
            'status': job.status,
            'note': job.note,
            'attempts': job.attempts,
            'written_at': job.written_at,
            'has_comment': bool(job.comment),
            'comment': job.comment,
            'result': job.result,
            'updated_at': job.updated_at,
        })
    return out
