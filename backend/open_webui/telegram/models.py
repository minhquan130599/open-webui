"""Telegram accounts linked to NCS AI users, one-time link codes, the state of each chat with the bot, and the
bot messages mapped to channel / chat messages (reply = ask again in that thread, export buttons)."""

from __future__ import annotations

import hashlib
import secrets
import time
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import JSON, BigInteger, Column, Index, Text, and_, delete, or_, select, update
from sqlalchemy.exc import IntegrityError

from open_webui.internal.db import Base, get_async_db_context

CODE_SECONDS = 600  # a link code works for 10 minutes
MAP_KEEP_DAYS = 30


def now_ns() -> int:
    return time.time_ns()


def code_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


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
        async with get_async_db_context() as db:
            row = await db.get(TelegramLinkCode, code_hash(code))
            if not row or row.user_id != user_id:
                return {'linked': False, 'gone': True}
            if row.used_by:
                return {'linked': True}
            return {'linked': False, 'gone': row.created_at < now_ns() - CODE_SECONDS * 10**9}

    async def confirm(self, code: str, telegram_user_id: str, username: str, display_name: str) -> tuple[str, Optional[AccountModel]]:
        """Use a code from Telegram user `telegram_user_id`. Returns (status, account):
        'linked' | 'already' (this Telegram user used this code before) | 'invalid'."""
        hashed = code_hash(code)
        now = now_ns()
        telegram_user_id = str(telegram_user_id)
        async with get_async_db_context() as db:
            # Atomic: only one Telegram user can take a code, once.
            result = await db.execute(
                update(TelegramLinkCode)
                .where(
                    TelegramLinkCode.code_hash == hashed,
                    TelegramLinkCode.used_by.is_(None),
                    TelegramLinkCode.created_at >= now - CODE_SECONDS * 10**9,
                )
                .values(used_by=telegram_user_id, used_at=now)
            )
            if not result.rowcount:
                await db.rollback()
                row = await db.get(TelegramLinkCode, hashed)
                if row and row.used_by == telegram_user_id:
                    account = await db.get(TelegramAccount, telegram_user_id)
                    if account and account.user_id == row.user_id:
                        return 'already', AccountModel.model_validate(account)
                return 'invalid', None
            row = await db.get(TelegramLinkCode, hashed)
            # One Telegram account per user and one user per Telegram account: the new link replaces older ones.
            await db.execute(
                delete(TelegramAccount).where(
                    or_(TelegramAccount.user_id == row.user_id, TelegramAccount.telegram_user_id == telegram_user_id)
                )
            )
            fields = {
                'telegram_user_id': telegram_user_id,
                'user_id': row.user_id,
                'username': username or None,
                'display_name': display_name or None,
                'linked_at': now,
                'last_seen_at': now,
            }
            db.add(TelegramAccount(**fields))
            try:
                await db.commit()
            except IntegrityError:  # the same Telegram user / user linked at the same moment with another code
                await db.rollback()
                return 'invalid', None
            return 'linked', AccountModel(**fields)

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

    async def prune(self) -> None:
        async with get_async_db_context() as db:
            await db.execute(delete(TelegramMessageMap).where(TelegramMessageMap.created_at < now_ns() - MAP_KEEP_DAYS * 86_400 * 10**9))
            await db.commit()


Accounts = AccountTable()
Sessions = SessionTable()
MessageMap = MapTable()
