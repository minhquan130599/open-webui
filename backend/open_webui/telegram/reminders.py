"""Due-date reminders by Telegram, for people who switched them on (Settings → Notifications, off by default) and
linked their Telegram. The same items as the reminder email (due today, tomorrow, and overdue ones riding along),
sent by the bot to the person's private chat; the message is logged, so the daily cleanup deletes it too."""

import html
import logging

log = logging.getLogger(__name__)

# User setting (settings.ui) that turns Telegram reminders on for one person; off when absent.
USER_OPT_IN = 'trackerTelegramReminders'
LABELS = {'today': 'Đến hạn hôm nay', 'tomorrow': 'Đến hạn ngày mai', 'overdue': 'Quá hạn'}


def _text(name: str, entries: list) -> str:
    lines = [f'🔔 <b>Nhắc việc</b> cho {html.escape(name or "bạn")}']
    for kind in ('overdue', 'today', 'tomorrow'):
        items = [entry for k, entry in entries if k == kind]
        if not items:
            continue
        lines.append(f'\n<b>{LABELS[kind]}</b>')
        for entry in items:
            due = f' · hạn {html.escape(entry.han)}' if entry.han else ''
            lines.append(f'• {html.escape(entry.ten)} — #{html.escape(entry.channel_name or "")}{due}')
    lines.append('\nMở NCS AI để xem và cập nhật tiến độ.')
    return '\n'.join(lines)


async def send_reminders(by_user: dict[str, list]) -> dict:
    """by_user: user id -> [(kind, entry)], as for the reminder email. Returns counts; never raises."""
    result = {'sent': 0, 'skipped': 0, 'failed': 0}
    try:
        from open_webui.models.users import Users
        from open_webui.telegram import api
        from open_webui.telegram.models import Accounts
        from open_webui.telegram.settings import bot_ready, get_settings

        settings = await get_settings()
        if not bot_ready(settings) or not by_user:
            return result
        token = settings['telegram.bot_token']
        accounts = await Accounts.by_users(list(by_user))
        users = {u.id: u for u in await Users.get_users_by_user_ids(list(accounts))}
    except Exception:
        log.exception('Telegram reminders could not start')
        return result
    for user_id, entries in by_user.items():
        account, user = accounts.get(user_id), users.get(user_id)
        ui = ((user.settings.model_dump() if hasattr(user.settings, 'model_dump') else user.settings) or {}).get('ui') if user else {}
        if not account or not user or not (ui or {}).get(USER_OPT_IN) or not entries:
            result['skipped'] += 1
            continue
        try:
            await api.send_message(token, account.telegram_user_id, _text(user.name, entries))
            result['sent'] += 1
        except Exception as e:
            result['failed'] += 1
            log.warning('Telegram reminder to %s failed: %s', user_id, e)
    log.info('Telegram reminders: %s', result)
    return result
