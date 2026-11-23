"""Who is writing to the bot and what they may do, checked again on every message.

The Telegram user is identified by `from.id` only (never by name). The linked NCS AI account must still be
active, verified (not pending) and allowed to use Telegram; a blocked or removed user is refused at once.
"""

import time
from datetime import timedelta
from typing import Optional

from fastapi import Request
from starlette.datastructures import Headers

from open_webui.models.users import UserModel, Users
from open_webui.telegram.models import AccountModel, Accounts

TOKEN_MINUTES = 60
_RATE: dict[str, list[float]] = {}


class Denied(Exception):
    """The message cannot be handled; the text says why (shown to the Telegram user)."""


async def can_use_bot(user: UserModel, settings: dict) -> bool:
    """May link a Telegram account: always when reports go by Telegram, else when allowed to ask."""
    if settings['telegram.reports']:
        return True
    return await can_ask(user, settings)


async def can_ask(user: UserModel, settings: dict) -> bool:
    if not settings['telegram.ask'] or user.role not in ('user', 'admin'):
        return False
    if user.role == 'admin':
        return True
    from open_webui.models.config import Config
    from open_webui.utils.access_control import has_permission

    return await has_permission(user.id, 'features.telegram', await Config.get('user.permissions'))


async def active_user(user_id: str) -> Optional[UserModel]:
    """The user, if the account still works (exists, active, not pending)."""
    from open_webui.models.auths import Auths

    user = await Users.get_user_by_id(user_id)
    if not user or user.role not in ('user', 'admin'):
        return None
    auth = await Auths.get_auth_by_id(user.id)
    if auth is None or not getattr(auth, 'active', True):
        return None
    return user


async def resolve(telegram_user_id: str) -> tuple[Optional[AccountModel], Optional[UserModel]]:
    account = await Accounts.by_telegram(str(telegram_user_id))
    if not account:
        return None, None
    return account, await active_user(account.user_id)


def rate_ok(telegram_user_id: str, per_minute: int) -> bool:
    """Sliding one-minute window per Telegram user (the poller handles all updates in one process)."""
    now = time.monotonic()
    hits = [t for t in _RATE.get(telegram_user_id, []) if now - t < 60]
    if len(hits) >= per_minute:
        _RATE[telegram_user_id] = hits
        return False
    hits.append(now)
    _RATE[telegram_user_id] = hits
    if len(_RATE) > 5000:
        for key in [k for k, v in _RATE.items() if not v or now - v[-1] > 60]:
            _RATE.pop(key, None)
    return True


async def user_request(app, user: UserModel) -> Request:
    """A request acting as `user` for the chat pipeline (same pattern as automations), with a short-lived token
    bound to the user's session stamp: logging out everywhere or a password change ends it."""
    from open_webui.models.auths import Auths
    from open_webui.utils.auth import create_token
    from fastapi.security import HTTPAuthorizationCredentials

    auth = await Auths.get_auth_by_id(user.id)
    token = create_token(
        data={'id': user.id, 'typ': 'automation', 'session_stamp': getattr(auth, 'session_stamp', None)},
        expires_delta=timedelta(minutes=TOKEN_MINUTES),
    )
    scope = {
        'type': 'http',
        'asgi': {'version': '3.0', 'spec_version': '2.0'},
        'method': 'POST',
        'path': '/api/v1/telegram/internal',
        'query_string': b'',
        'headers': Headers({'user-agent': 'NCS-AI-Telegram'}).raw,
        'client': ('', 0),
        'server': ('127.0.0.1', 80),
        'scheme': 'http',
        'app': app,
    }
    request = Request(scope)
    request.state.token = HTTPAuthorizationCredentials(scheme='Bearer', credentials=token)
    request.state.enable_api_keys = False
    return request
