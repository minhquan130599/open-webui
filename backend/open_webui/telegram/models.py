"""Telegram accounts linked to NCS AI users, one-time link codes (confirmed by a code sent by email), the state of
each chat with the bot, the bot messages mapped to channel / chat messages, and the log of the bot's chat messages
(deleted every day)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import JSON, BigInteger, Column, Index, Integer, Text, and_, delete, or_, select, update
from sqlalchemy.exc import IntegrityError

from open_webui.internal.db import Base, get_async_db_context

CODE_SECONDS = 600  # a link code works for 10 minutes
OTP_SECONDS = 600  # the code sent by email works for 10 minutes
OTP_ATTEMPTS = 5  # wrong codes allowed for one link (all codes sent for it together)
OTP_RESEND_SECONDS = 60  # Start pressed again sooner does not send another email
MAP_KEEP_DAYS = 30


def now_ns() -> int:
    return time.time_ns()


def code_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def otp_hash(link_hash: str, otp: str) -> str:
    # Bound to its link code: the same 6 digits give another hash for another link.
    return hashlib.sha256(f'{link_hash}:{otp}'.encode()).hexdigest()


def new_otp() -> str:
    return f'{secrets.randbelow(10**6):06d}'


def mask_email(email: str) -> str:
    """ng***@ncsgroup.vn: enough for the user to recognise the address, not to read it."""
    local, _, domain = (email or '').partition('@')
    if not domain:
        return '***'
    return f'{local[:2] if len(local) > 2 else local[:1]}***@{domain}'


class TelegramAccount(Base):
    __tablename__ = 'telegram_account'

    telegram_user_id = Column(Text, primary_key=True)  # from.id; also the chat id of the private chat with the bot
    user_id = Column(Text, nullable=False, unique=True)
    username = Column(Text, nullable=True)
    display_name = Column(Text, nullable=True)
    linked_at = Column(BigInteger, nullable=False)
    last_seen_at = Column(BigInteger, nullable=True)


class TelegramLinkCode(Base):
    __tablename__ = 'telegram_link_code'

    code_hash = Column(Text, primary_key=True)  # only the hash is kept
    user_id = Column(Text, nullable=False)
    created_at = Column(BigInteger, nullable=False)
    used_by = Column(Text, nullable=True)  # telegram user id that used it
    used_at = Column(BigInteger, nullable=True)
    # Start pressed in Telegram: who asked, and the code emailed to the account (only its hash is kept). The link
    # is made when that code is entered on the web page that is waiting.
    pending_by = Column(Text, nullable=True)  # telegram user id waiting for the email code
    pending_data = Column(JSON, nullable=True)  # chat_id, username, display_name, sent_at, email (masked), error
    otp_hash = Column(Text, nullable=True)
    otp_expires_at = Column(BigInteger, nullable=True)
    otp_attempts = Column(Integer, nullable=False, default=0)

    __table_args__ = (Index('ix_telegram_link_code_user', 'user_id'),)


class TelegramSession(Base):
    __tablename__ = 'telegram_session'

    chat_id = Column(Text, primary_key=True)
    telegram_user_id = Column(Text, nullable=False)
    mode = Column(Text, nullable=False, default='direct')  # direct | channel
    model_id = Column(Text, nullable=True)
    chat_ref = Column(Text, nullable=True)  # current chat (asking directly)
    last_message_id = Column(Text, nullable=True)  # last assistant message of that chat
    channel_id = Column(Text, nullable=True)
    data = Column(JSON, nullable=True)  # recent channels, choice lists behind buttons
    updated_at = Column(BigInteger, nullable=False)


class TelegramMessageMap(Base):
    __tablename__ = 'telegram_message_map'

    chat_id = Column(Text, primary_key=True)
    tg_message_id = Column(BigInteger, primary_key=True)
    target = Column(Text, nullable=False)  # channel | chat
    target_id = Column(Text, nullable=False)  # channel id / chat id
    message_id = Column(Text, nullable=False)  # the answer message
    thread_id = Column(Text, nullable=True)  # channel thread the answer is in
    question = Column(Text, nullable=True)
    data = Column(JSON, nullable=True)  # files sent for the export buttons
    created_at = Column(BigInteger, nullable=False)

    __table_args__ = (Index('ix_telegram_message_map_created', 'created_at'),)


class TelegramChatLog(Base):
    """Messages in private chats with the bot (sent by it or received), deleted from Telegram once a day."""

    __tablename__ = 'telegram_chat_log'

    chat_id = Column(Text, primary_key=True)
    tg_message_id = Column(BigInteger, primary_key=True)
    created_at = Column(BigInteger, nullable=False)

    __table_args__ = (Index('ix_telegram_chat_log_created', 'created_at'),)


class AccountModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    telegram_user_id: str
    user_id: str
    username: Optional[str] = None
    display_name: Optional[str] = None
    linked_at: int
    last_seen_at: Optional[int] = None


class SessionModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chat_id: str
    telegram_user_id: str
    mode: str = 'direct'
    model_id: Optional[str] = None
    chat_ref: Optional[str] = None
    last_message_id: Optional[str] = None
    channel_id: Optional[str] = None
    data: Optional[dict] = None
    updated_at: int


class MapModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chat_id: str
    tg_message_id: int
    target: str
    target_id: str
    message_id: str
    thread_id: Optional[str] = None
    question: Optional[str] = None
    data: Optional[dict] = None
    created_at: int


class AccountTable:
    async def by_telegram(self, telegram_user_id: str) -> Optional[AccountModel]:
        async with get_async_db_context() as db:
            row = await db.get(TelegramAccount, str(telegram_user_id))
            return AccountModel.model_validate(row) if row else None

    async def by_user(self, user_id: str) -> Optional[AccountModel]:
        async with get_async_db_context() as db:
            row = (await db.execute(select(TelegramAccount).where(TelegramAccount.user_id == user_id))).scalars().first()
            return AccountModel.model_validate(row) if row else None

    async def by_users(self, user_ids: list[str]) -> dict[str, AccountModel]:
        if not user_ids:
            return {}
        async with get_async_db_context() as db:
            rows = (await db.execute(select(TelegramAccount).where(TelegramAccount.user_id.in_(user_ids)))).scalars().all()
            return {r.user_id: AccountModel.model_validate(r) for r in rows}

    async def list(self) -> list[AccountModel]:
        async with get_async_db_context() as db:
            rows = (await db.execute(select(TelegramAccount).order_by(TelegramAccount.linked_at.desc()))).scalars().all()
            return [AccountModel.model_validate(r) for r in rows]

    async def new_code(self, user_id: str) -> str:
        """A one-time code for the t.me link; earlier unused codes of the user stop working."""
        code = secrets.token_urlsafe(12).replace('-', 'x').replace('_', 'y')
        now = now_ns()
        async with get_async_db_context() as db:
            await db.execute(
                delete(TelegramLinkCode).where(
                    or_(
                        and_(TelegramLinkCode.user_id == user_id, TelegramLinkCode.used_by.is_(None)),
                        TelegramLinkCode.created_at < now - 86_400 * 10**9,
                    )
                )
            )
            db.add(TelegramLinkCode(code_hash=code_hash(code), user_id=user_id, created_at=now))
            await db.commit()
        return code

    async def code_status(self, user_id: str, code: str) -> dict:
        """What the web page waiting for the link shows: still waiting for Start, waiting for the email code (who
        asked, where the code went), linked, or gone (expired, too many wrong codes, email not configured)."""
        async with get_async_db_context() as db:
            row = await db.get(TelegramLinkCode, code_hash(code))
            if not row or row.user_id != user_id:
                return {'linked': False, 'gone': True}
            if row.used_by:
                return {'linked': True}
            now = now_ns()
            data = row.pending_data or {}
            if data.get('error'):
                return {'linked': False, 'gone': True, 'error': data['error']}
            if row.otp_hash:
                if row.otp_attempts >= OTP_ATTEMPTS:
                    return {'linked': False, 'gone': True, 'error': 'locked'}
                if (row.otp_expires_at or 0) < now:
                    return {'linked': False, 'gone': True, 'error': 'expired'}
                return {
                    'linked': False,
                    'gone': False,
                    'otp': True,
                    'email': data.get('email') or '',
                    'telegram': {'username': data.get('username') or '', 'display_name': data.get('display_name') or ''},
                    'otp_seconds': max(0, (row.otp_expires_at - now) // 10**9),
                    'attempts_left': OTP_ATTEMPTS - row.otp_attempts,
                }
            if row.otp_attempts >= OTP_ATTEMPTS:
                return {'linked': False, 'gone': True, 'error': 'locked'}
            return {'linked': False, 'gone': row.created_at < now - CODE_SECONDS * 10**9}

    async def request_otp(self, code: str, telegram_user_id: str, chat_id: str, username: str,
                          display_name: str) -> tuple[str, Optional[str], Optional[str]]:
        """Start pressed with a link code: a new 6-digit code for the email, remembered (as a hash) with who asked.
        Returns (status, otp, user_id): 'ok' (send `otp` to the account's email) | 'recent' (one was just sent to this
        Telegram) | 'already' (this Telegram is already linked to the account) | 'other' (another Telegram is
        confirming this link) | 'locked' (too many wrong codes) | 'invalid' (unknown, used or expired)."""
        hashed = code_hash(code)
        telegram_user_id = str(telegram_user_id)
        now = now_ns()
        async with get_async_db_context() as db:
            row = await db.get(TelegramLinkCode, hashed)
            if not row:
                return 'invalid', None, None
            account = await db.get(TelegramAccount, telegram_user_id)
            if row.used_by:
                if row.used_by == telegram_user_id and account and account.user_id == row.user_id:
                    return 'already', None, row.user_id
                return 'invalid', None, None
            if (row.pending_data or {}).get('error'):
                return 'invalid', None, None  # the link already stopped (the web page stopped waiting): start again
            if row.pending_by and row.pending_by != telegram_user_id:
                return 'other', None, None
            if row.otp_attempts >= OTP_ATTEMPTS:
                return 'locked', None, None
            sent_at = (row.pending_data or {}).get('sent_at') or 0
            if row.otp_hash and (row.otp_expires_at or 0) > now and now - sent_at < OTP_RESEND_SECONDS * 10**9:
                return 'recent', None, row.user_id
            # The link itself works for 10 minutes; a new email code is only sent within them.
            if row.created_at < now - CODE_SECONDS * 10**9:
                return 'invalid', None, None
            if account and account.user_id == row.user_id:
                return 'already', None, row.user_id
            otp = new_otp()
            # Atomic: two Start presses at once (app and browser, two people) cannot both take the link.
            result = await db.execute(
                update(TelegramLinkCode)
                .where(
                    TelegramLinkCode.code_hash == hashed,
                    TelegramLinkCode.used_by.is_(None),
                    or_(TelegramLinkCode.pending_by.is_(None), TelegramLinkCode.pending_by == telegram_user_id),
                )
                .values(
                    pending_by=telegram_user_id,
                    pending_data={
                        'chat_id': str(chat_id),
                        'username': username or '',
                        'display_name': display_name or '',
                        'sent_at': now,
                    },
                    otp_hash=otp_hash(hashed, otp),
                    otp_expires_at=now + OTP_SECONDS * 10**9,
                )
            )
            await db.commit()
            if not result.rowcount:
                return 'other', None, None
            return 'ok', otp, row.user_id

    async def otp_sent(self, code: str, email_masked: str) -> None:
        """The email went out: the waiting web page now asks for the code (and shows where it was sent)."""
        await self._set_pending(code, {'email': email_masked})

    async def link_failed(self, code: str, error: str) -> None:
        """The link cannot go on (email not configured, email not sent): nothing is linked, the email code no longer
        works, and the waiting web page shows why."""
        await self._set_pending(code, {'error': error}, clear_otp=True)

    async def _set_pending(self, code: str, values: dict, clear_otp: bool = False) -> None:
        async with get_async_db_context() as db:
            row = await db.get(TelegramLinkCode, code_hash(code))
            if not row or row.used_by:
                return
            row.pending_data = {**(row.pending_data or {}), **values}
            if clear_otp:
                row.otp_hash, row.otp_expires_at = None, None
            await db.commit()

    async def verify_otp(self, user_id: str, code: str, otp: str) -> tuple[str, Optional[AccountModel], int]:
        """The code from the email, entered on the web by the account's owner. Returns (status, account, attempts
        left): 'linked' | 'wrong' | 'expired' | 'locked' | 'invalid'."""
        hashed = code_hash(code)
        now = now_ns()
        async with get_async_db_context() as db:
            row = await db.get(TelegramLinkCode, hashed)
            if not row or row.user_id != user_id or row.used_by or not row.otp_hash or not row.pending_by:
                return 'invalid', None, 0
            if row.otp_attempts >= OTP_ATTEMPTS:
                return 'locked', None, 0
            if (row.otp_expires_at or 0) < now:
                return 'expired', None, 0
            # Counted before comparing, atomically: guesses sent in parallel cannot go past the limit.
            result = await db.execute(
                update(TelegramLinkCode)
                .where(
                    TelegramLinkCode.code_hash == hashed,
                    TelegramLinkCode.used_by.is_(None),
                    TelegramLinkCode.otp_attempts < OTP_ATTEMPTS,
                )
                .values(otp_attempts=TelegramLinkCode.otp_attempts + 1)
            )
            await db.commit()
            if not result.rowcount:
                return 'locked', None, 0
            await db.refresh(row)
            left = OTP_ATTEMPTS - row.otp_attempts
            if not hmac.compare_digest(row.otp_hash or '', otp_hash(hashed, (otp or '').strip())):
                return ('wrong' if left > 0 else 'locked'), None, left
            telegram_user_id = row.pending_by
            data = row.pending_data or {}
            taken = await db.execute(
                update(TelegramLinkCode)
                .where(TelegramLinkCode.code_hash == hashed, TelegramLinkCode.used_by.is_(None))
                .values(used_by=telegram_user_id, used_at=now, otp_hash=None)
            )
            if not taken.rowcount:
                await db.rollback()
                return 'invalid', None, 0
            # One Telegram account per user and one user per Telegram account: the new link replaces older ones.
            await db.execute(
                delete(TelegramAccount).where(
                    or_(TelegramAccount.user_id == row.user_id, TelegramAccount.telegram_user_id == telegram_user_id)
                )
            )
            fields = {
                'telegram_user_id': telegram_user_id,
                'user_id': row.user_id,
                'username': data.get('username') or None,
                'display_name': data.get('display_name') or None,
                'linked_at': now,
                'last_seen_at': now,
            }
            db.add(TelegramAccount(**fields))
            try:
                await db.commit()
            except IntegrityError:  # the same Telegram user / user linked at the same moment with another code
                await db.rollback()
                return 'invalid', None, 0
            return 'linked', AccountModel(**fields), left

    async def pending_chat(self, code: str) -> Optional[str]:
        """The Telegram chat waiting for this link (to tell it the link was made)."""
        async with get_async_db_context() as db:
            row = await db.get(TelegramLinkCode, code_hash(code))
            return ((row.pending_data or {}).get('chat_id') or row.pending_by) if row else None

    async def touch(self, telegram_user_id: str, username: str = '', display_name: str = '') -> None:
        async with get_async_db_context() as db:
            values = {'last_seen_at': now_ns()}
            if username:
                values['username'] = username
            if display_name:
                values['display_name'] = display_name
            await db.execute(update(TelegramAccount).where(TelegramAccount.telegram_user_id == str(telegram_user_id)).values(**values))
            await db.commit()

    async def unlink_user(self, user_id: str) -> Optional[AccountModel]:
        async with get_async_db_context() as db:
            row = (await db.execute(select(TelegramAccount).where(TelegramAccount.user_id == user_id))).scalars().first()
            if not row:
                return None
            account = AccountModel.model_validate(row)
            await db.execute(delete(TelegramAccount).where(TelegramAccount.user_id == user_id))
            await db.execute(delete(TelegramSession).where(TelegramSession.telegram_user_id == account.telegram_user_id))
            await db.commit()
            return account

    async def unlink_telegram(self, telegram_user_id: str) -> Optional[AccountModel]:
        account = await self.by_telegram(telegram_user_id)
        return await self.unlink_user(account.user_id) if account else None


class SessionTable:
    async def get(self, chat_id: str, telegram_user_id: str) -> SessionModel:
        async with get_async_db_context() as db:
            row = await db.get(TelegramSession, str(chat_id))
            if row and row.telegram_user_id == str(telegram_user_id):
                model = SessionModel.model_validate(row)
                model.data = row.data or {}
                return model
            if row:  # the chat now belongs to another linked user: start clean
                await db.delete(row)
                await db.commit()
            return SessionModel(chat_id=str(chat_id), telegram_user_id=str(telegram_user_id), data={}, updated_at=0)

    async def save(self, session: SessionModel) -> None:
        async with get_async_db_context() as db:
            row = await db.get(TelegramSession, session.chat_id)
            fields = session.model_dump()
            fields['updated_at'] = now_ns()
            if row is None:
                db.add(TelegramSession(**fields))
            else:
                for key, value in fields.items():
                    setattr(row, key, value)
            await db.commit()


class MapTable:
    async def add(self, chat_id: str, tg_message_id: int, target: str, target_id: str, message_id: str,
                  thread_id: Optional[str] = None, question: str = '', data: Optional[dict] = None) -> None:
        async with get_async_db_context() as db:
            await db.merge(
                TelegramMessageMap(
                    chat_id=str(chat_id), tg_message_id=int(tg_message_id), target=target, target_id=target_id,
                    message_id=message_id, thread_id=thread_id, question=(question or '')[:2000], data=data or {},
                    created_at=now_ns(),
                )
            )
            await db.commit()

    async def get(self, chat_id: str, tg_message_id: int) -> Optional[MapModel]:
        async with get_async_db_context() as db:
            row = await db.get(TelegramMessageMap, (str(chat_id), int(tg_message_id)))
            if not row:
                return None
            model = MapModel.model_validate(row)
            model.data = row.data or {}
            return model

    async def set_data(self, chat_id: str, tg_message_id: int, data: dict) -> None:
        async with get_async_db_context() as db:
            await db.execute(
                update(TelegramMessageMap)
                .where(TelegramMessageMap.chat_id == str(chat_id), TelegramMessageMap.tg_message_id == int(tg_message_id))
                .values(data=data)
            )
            await db.commit()

    async def prune(self, before: Optional[int] = None) -> int:
        """Remove mappings older than `before` (default: MAP_KEEP_DAYS ago)."""
        before = before if before is not None else now_ns() - MAP_KEEP_DAYS * 86_400 * 10**9
        async with get_async_db_context() as db:
            result = await db.execute(delete(TelegramMessageMap).where(TelegramMessageMap.created_at < before))
            await db.commit()
            return result.rowcount or 0


class ChatLogTable:
    async def add(self, chat_id, tg_message_id) -> None:
        async with get_async_db_context() as db:
            await db.merge(TelegramChatLog(chat_id=str(chat_id), tg_message_id=int(tg_message_id), created_at=now_ns()))
            await db.commit()

    async def before(self, before: int, limit: int = 20000) -> dict[str, list[tuple[int, int]]]:
        """Logged messages older than `before`, by chat: (message id, logged at)."""
        async with get_async_db_context() as db:
            rows = (
                await db.execute(
                    select(TelegramChatLog.chat_id, TelegramChatLog.tg_message_id, TelegramChatLog.created_at)
                    .where(TelegramChatLog.created_at < before)
                    .order_by(TelegramChatLog.created_at)
                    .limit(limit)
                )
            ).all()
        out: dict[str, list[tuple[int, int]]] = {}
        for chat_id, message_id, created_at in rows:
            out.setdefault(chat_id, []).append((int(message_id), int(created_at)))
        return out

    async def forget(self, chat_id: str, message_ids: list[int]) -> None:
        if not message_ids:
            return
        async with get_async_db_context() as db:
            await db.execute(
                delete(TelegramChatLog).where(
                    TelegramChatLog.chat_id == str(chat_id), TelegramChatLog.tg_message_id.in_(message_ids)
                )
            )
            await db.execute(
                delete(TelegramMessageMap).where(
                    TelegramMessageMap.chat_id == str(chat_id), TelegramMessageMap.tg_message_id.in_(message_ids)
                )
            )
            await db.commit()


Accounts = AccountTable()
Sessions = SessionTable()
MessageMap = MapTable()
ChatLog = ChatLogTable()
