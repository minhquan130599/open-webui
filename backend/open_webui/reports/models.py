"""Report schedules, Telegram chats linked to users, and a named lease (one Telegram poller across workers)."""

import secrets
import time
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import JSON, BigInteger, Boolean, Column, Index, Text, and_, delete, or_, select, update

from open_webui.internal.db import Base, get_async_db_context


def now_ns() -> int:
    return time.time_ns()


class ReportSchedule(Base):
    __tablename__ = 'report_schedule'

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    # Everything the user sets: channels, timing, content, delivery (see schedule.normalize_config).
    config = Column(JSON, nullable=True)
    next_run_at = Column(BigInteger, nullable=True)
    last_run_at = Column(BigInteger, nullable=True)
    last_status = Column(Text, nullable=True)  # sent | skipped | error
    last_error = Column(Text, nullable=True)
    last_digest = Column(Text, nullable=True)
    lease_id = Column(Text, nullable=True)
    lease_until = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index('ix_report_schedule_user', 'user_id'),
        Index('ix_report_schedule_next_run', 'next_run_at'),
    )


class ReportTelegramLink(Base):
    __tablename__ = 'report_telegram_link'

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    code = Column(Text, nullable=True)  # set until the chat sends it to the bot
    chat_id = Column(Text, nullable=True)
    chat_title = Column(Text, nullable=True)
    chat_type = Column(Text, nullable=True)  # private | group | supergroup | channel
    created_at = Column(BigInteger, nullable=False)
    linked_at = Column(BigInteger, nullable=True)

    __table_args__ = (Index('ix_report_telegram_link_user', 'user_id'), Index('ix_report_telegram_link_code', 'code'))


class ReportLock(Base):
    __tablename__ = 'report_lock'

    name = Column(Text, primary_key=True)
    lease_id = Column(Text, nullable=True)
    lease_until = Column(BigInteger, nullable=True)
    data = Column(JSON, nullable=True)


class ScheduleModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    name: str
    enabled: bool
    config: dict = {}
    next_run_at: Optional[int] = None
    last_run_at: Optional[int] = None
    last_status: Optional[str] = None
    last_error: Optional[str] = None
    last_digest: Optional[str] = None
    created_at: int
    updated_at: int


class LinkModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    code: Optional[str] = None
    chat_id: Optional[str] = None
    chat_title: Optional[str] = None
    chat_type: Optional[str] = None
    created_at: int
    linked_at: Optional[int] = None


def _model(row) -> ScheduleModel:
    model = ScheduleModel.model_validate(row)
    model.config = row.config or {}
    return model


class ScheduleTable:
    async def list(self, user_id: Optional[str] = None) -> list[ScheduleModel]:
        async with get_async_db_context() as db:
            query = select(ReportSchedule).order_by(ReportSchedule.created_at)
            if user_id:
                query = query.where(ReportSchedule.user_id == user_id)
            return [_model(r) for r in (await db.execute(query)).scalars().all()]

    async def get(self, schedule_id: str) -> Optional[ScheduleModel]:
        async with get_async_db_context() as db:
            row = await db.get(ReportSchedule, schedule_id)
            return _model(row) if row else None

    async def count(self, user_id: str) -> int:
        return len(await self.list(user_id))

    async def create(self, user_id: str, name: str, enabled: bool, config: dict, next_run_at: Optional[int]) -> ScheduleModel:
        now = now_ns()
        row = ReportSchedule(
            id=str(uuid.uuid4()), user_id=user_id, name=name, enabled=enabled, config=config,
            next_run_at=next_run_at, created_at=now, updated_at=now,
        )
        async with get_async_db_context() as db:
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return _model(row)

    async def update(self, schedule_id: str, **fields) -> Optional[ScheduleModel]:
        async with get_async_db_context() as db:
            row = await db.get(ReportSchedule, schedule_id)
            if not row:
                return None
            for key, value in fields.items():
                setattr(row, key, value)
            row.updated_at = now_ns()
            await db.commit()
            await db.refresh(row)
            return _model(row)

    async def delete(self, schedule_id: str) -> bool:
        async with get_async_db_context() as db:
            result = await db.execute(delete(ReportSchedule).where(ReportSchedule.id == schedule_id))
            await db.commit()
            return bool(result.rowcount)

    async def claim_due(self, now: int, lease_seconds: int, limit: int = 5) -> list[tuple[ScheduleModel, str]]:
        """Enabled schedules whose time has come, each taken by one worker only (an atomic lease)."""
        claimed = []
        async with get_async_db_context() as db:
            ids = (
                await db.execute(
                    select(ReportSchedule.id)
                    .where(
                        ReportSchedule.enabled.is_(True),
                        ReportSchedule.next_run_at.is_not(None),
                        ReportSchedule.next_run_at <= now,
                        or_(ReportSchedule.lease_until.is_(None), ReportSchedule.lease_until < now),
                    )
                    .order_by(ReportSchedule.next_run_at)
                    .limit(limit)
                )
            ).scalars().all()
            for schedule_id in ids:
                lease_id = uuid.uuid4().hex
                result = await db.execute(
                    update(ReportSchedule)
                    .where(
                        ReportSchedule.id == schedule_id,
                        or_(ReportSchedule.lease_until.is_(None), ReportSchedule.lease_until < now),
                    )
                    .values(lease_id=lease_id, lease_until=now + lease_seconds * 1_000_000_000)
                )
                if result.rowcount:
                    claimed.append((schedule_id, lease_id))
            await db.commit()
        out = []
        for schedule_id, lease_id in claimed:
            schedule = await self.get(schedule_id)
            if schedule:
                out.append((schedule, lease_id))
        return out

    async def finish(self, schedule_id: str, lease_id: str, **fields) -> None:
        async with get_async_db_context() as db:
            await db.execute(
                update(ReportSchedule)
                .where(ReportSchedule.id == schedule_id, ReportSchedule.lease_id == lease_id)
                .values(lease_id=None, lease_until=None, **fields)
            )
            await db.commit()


class LinkTable:
    async def list(self, user_id: str, linked_only: bool = True) -> list[LinkModel]:
        async with get_async_db_context() as db:
            query = select(ReportTelegramLink).where(ReportTelegramLink.user_id == user_id)
            if linked_only:
                query = query.where(ReportTelegramLink.chat_id.is_not(None))
            rows = (await db.execute(query.order_by(ReportTelegramLink.created_at))).scalars().all()
            return [LinkModel.model_validate(r) for r in rows]

    async def by_ids(self, ids: list[str]) -> list[LinkModel]:
        if not ids:
            return []
        async with get_async_db_context() as db:
            rows = (await db.execute(select(ReportTelegramLink).where(ReportTelegramLink.id.in_(ids)))).scalars().all()
            return [LinkModel.model_validate(r) for r in rows]

    async def new_code(self, user_id: str) -> LinkModel:
        """A one-time code the user sends to the bot (privately or in a group) to link that chat."""
        async with get_async_db_context() as db:
            # Codes not used within a day are dropped.
            await db.execute(
                delete(ReportTelegramLink).where(
                    ReportTelegramLink.chat_id.is_(None), ReportTelegramLink.created_at < now_ns() - 86_400 * 10**9
                )
            )
            row = ReportTelegramLink(
                id=str(uuid.uuid4()), user_id=user_id, code=secrets.token_hex(4), created_at=now_ns()
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return LinkModel.model_validate(row)

    async def confirm(self, code: str, chat_id: str, title: str, chat_type: str) -> Optional[LinkModel]:
        async with get_async_db_context() as db:
            row = (
                await db.execute(
                    select(ReportTelegramLink).where(
                        ReportTelegramLink.code == code,
                        ReportTelegramLink.chat_id.is_(None),
                        ReportTelegramLink.created_at >= now_ns() - 86_400 * 10**9,
                    )
                )
            ).scalars().first()
            if not row:
                return None
            # The same chat linked again by the same user replaces the earlier link.
            await db.execute(
                delete(ReportTelegramLink).where(
                    and_(ReportTelegramLink.user_id == row.user_id, ReportTelegramLink.chat_id == chat_id)
                )
            )
            row.code, row.chat_id, row.chat_title, row.chat_type, row.linked_at = None, chat_id, title, chat_type, now_ns()
            await db.commit()
            await db.refresh(row)
            return LinkModel.model_validate(row)

    async def get_code(self, link_id: str, user_id: str) -> Optional[LinkModel]:
        async with get_async_db_context() as db:
            row = await db.get(ReportTelegramLink, link_id)
            return LinkModel.model_validate(row) if row and row.user_id == user_id else None

    async def delete(self, link_id: str, user_id: str) -> bool:
        async with get_async_db_context() as db:
            result = await db.execute(
                delete(ReportTelegramLink).where(ReportTelegramLink.id == link_id, ReportTelegramLink.user_id == user_id)
            )
            await db.commit()
            return bool(result.rowcount)

    async def forget_chat(self, chat_id: str) -> None:
        """The bot was removed from a chat (or blocked): links to it no longer work."""
        async with get_async_db_context() as db:
            await db.execute(delete(ReportTelegramLink).where(ReportTelegramLink.chat_id == chat_id))
            await db.commit()


class LockTable:
    async def acquire(self, name: str, lease_seconds: int, lease_id: Optional[str] = None) -> Optional[dict]:
        """Take (or renew, with the same lease_id) a named lease; returns its data, or None when someone holds it."""
        now = now_ns()
        lease_id = lease_id or uuid.uuid4().hex
        async with get_async_db_context() as db:
            row = await db.get(ReportLock, name)
            if row is None:
                db.add(ReportLock(name=name, lease_id=lease_id, lease_until=now + lease_seconds * 10**9, data={}))
                try:
                    await db.commit()
                except Exception:
                    await db.rollback()
                    return None
                return {'lease_id': lease_id, 'data': {}}
            result = await db.execute(
                update(ReportLock)
                .where(
                    ReportLock.name == name,
                    or_(ReportLock.lease_until.is_(None), ReportLock.lease_until < now, ReportLock.lease_id == lease_id),
                )
                .values(lease_id=lease_id, lease_until=now + lease_seconds * 10**9)
            )
            await db.commit()
            if not result.rowcount:
                return None
            row = await db.get(ReportLock, name)
            await db.refresh(row)
            return {'lease_id': lease_id, 'data': dict(row.data or {})}

    async def save(self, name: str, lease_id: str, data: dict) -> None:
        async with get_async_db_context() as db:
            await db.execute(update(ReportLock).where(ReportLock.name == name, ReportLock.lease_id == lease_id).values(data=data))
            await db.commit()


Schedules = ScheduleTable()
TelegramLinks = LinkTable()
Locks = LockTable()
