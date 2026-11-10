"""Tracker tables and queries.

`tracker_state` doubles as the work queue: posting a message bumps `pending_count`/`dirty_at`, and
workers claim dirty rows with a lease (FOR UPDATE SKIP LOCKED on PostgreSQL), so any number of
processes can run the loop without double-processing a channel.
"""

import logging
import time
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import JSON, BigInteger, Boolean, Column, Index, Integer, Text, and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import Base, get_async_db_context
from open_webui.models.messages import Message

log = logging.getLogger(__name__)


class TrackerState(Base):
    __tablename__ = 'tracker_state'

    channel_id = Column(Text, primary_key=True)
    state = Column(JSON, nullable=True)
    schema_version = Column(Integer, nullable=False, default=1)
    version = Column(Integer, nullable=False, default=0)

    # created_at (ns) of the last message folded into the state, and the latest edit seen.
    watermark = Column(BigInteger, nullable=False, default=0)
    edit_watermark = Column(BigInteger, nullable=False, default=0)

    dirty_at = Column(BigInteger, nullable=True)
    last_activity_at = Column(BigInteger, nullable=True)
    pending_count = Column(Integer, nullable=False, default=0)
    force = Column(Boolean, nullable=False, default=False)

    lease_id = Column(Text, nullable=True)
    lease_until = Column(BigInteger, nullable=True)
    fail_count = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(BigInteger, nullable=True)
    last_error = Column(Text, nullable=True)

    processed_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index('ix_tracker_state_dirty', 'dirty_at'),
        Index('ix_tracker_state_lease', 'lease_until'),
    )


class TrackerItem(Base):
    """One processed unit (a message batch for now; files and images later) with its summary."""

    __tablename__ = 'tracker_item'

    id = Column(Text, primary_key=True)
    channel_id = Column(Text, nullable=False)
    source_type = Column(Text, nullable=False)
    source_ids = Column(JSON, nullable=False)
    period_start = Column(BigInteger, nullable=False)
    period_end = Column(BigInteger, nullable=False)
    summary = Column(Text, nullable=True)
    data = Column(JSON, nullable=True)
    created_at = Column(BigInteger, nullable=False)

    __table_args__ = (Index('ix_tracker_item_channel_period', 'channel_id', 'period_end'),)


class TrackerStateModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    channel_id: str
    state: Optional[dict] = None
    schema_version: int = 1
    version: int = 0
    watermark: int = 0
    edit_watermark: int = 0
    dirty_at: Optional[int] = None
    pending_count: int = 0
    force: bool = False
    fail_count: int = 0
    last_error: Optional[str] = None
    processed_at: Optional[int] = None
    updated_at: int


class TrackerItemModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    channel_id: str
    source_type: str
    source_ids: list[str]
    period_start: int
    period_end: int
    summary: Optional[str] = None
    created_at: int


def _new_state_row(channel_id: str, now: int, **values) -> TrackerState:
    defaults = {
        'schema_version': 1,
        'version': 0,
        'watermark': 0,
        'edit_watermark': 0,
        'pending_count': 0,
        'force': False,
        'fail_count': 0,
        'created_at': now,
        'updated_at': now,
    }
    return TrackerState(channel_id=channel_id, **{**defaults, **values})


class TrackerTable:
    async def mark_dirty(self, channel_id: str, force: bool = False, db: Optional[AsyncSession] = None) -> None:
        """Record channel activity. Cheap enough to call on every message."""
        now = time.time_ns()
        values = {
            'pending_count': TrackerState.pending_count + 1,
            'last_activity_at': now,
            'dirty_at': func.coalesce(TrackerState.dirty_at, now),
            'updated_at': now,
        }
        if force:
            values['force'] = True

        async with get_async_db_context(db) as db:
            result = await db.execute(
                update(TrackerState).where(TrackerState.channel_id == channel_id).values(**values)
            )
            if result.rowcount == 0:
                db.add(
                    _new_state_row(channel_id, now, pending_count=1, last_activity_at=now, dirty_at=now, force=force)
                )
                try:
                    await db.commit()
                    return
                except IntegrityError:
                    # Another request created the row first; apply the increment to it.
                    await db.rollback()
                    await db.execute(update(TrackerState).where(TrackerState.channel_id == channel_id).values(**values))
            await db.commit()

    async def request_refresh(self, channel_id: str, db: Optional[AsyncSession] = None) -> None:
        """Process the channel on the next poll if anything is pending (used when someone asks)."""
        async with get_async_db_context(db) as db:
            await db.execute(
                update(TrackerState)
                .where(TrackerState.channel_id == channel_id, TrackerState.dirty_at.is_not(None))
                .values(force=True, next_attempt_at=None)
            )
            await db.commit()

    async def claim_due(
        self,
        now: int,
        batch_size: int,
        idle_ns: int,
        lease_ns: int,
        max_active: int,
        db: Optional[AsyncSession] = None,
    ) -> list[tuple[str, str]]:
        """Lease up to (max_active - active leases) due channels. Returns (channel_id, lease_id) pairs."""
        async with get_async_db_context(db) as db:
            active = (
                await db.execute(select(func.count()).select_from(TrackerState).where(TrackerState.lease_until > now))
            ).scalar() or 0
            limit = max_active - active
            if limit <= 0:
                return []

            stmt = (
                select(TrackerState)
                .where(
                    TrackerState.dirty_at.is_not(None),
                    or_(TrackerState.lease_until.is_(None), TrackerState.lease_until <= now),
                    or_(TrackerState.next_attempt_at.is_(None), TrackerState.next_attempt_at <= now),
                    or_(
                        TrackerState.force == True,  # noqa: E712
                        TrackerState.pending_count >= batch_size,
                        TrackerState.last_activity_at <= now - idle_ns,
                    ),
                )
                .order_by(TrackerState.force.desc(), TrackerState.dirty_at)
                .limit(limit)
            )
            if db.bind.dialect.name == 'postgresql':
                stmt = stmt.with_for_update(skip_locked=True)

            rows = (await db.execute(stmt)).scalars().all()
            claimed = []
            for row in rows:
                row.lease_id = str(uuid.uuid4())
                row.lease_until = now + lease_ns
                claimed.append((row.channel_id, row.lease_id))
            await db.commit()
            return claimed

    async def get_state(self, channel_id: str, db: Optional[AsyncSession] = None) -> Optional[TrackerStateModel]:
        async with get_async_db_context(db) as db:
            row = await db.get(TrackerState, channel_id)
            return TrackerStateModel.model_validate(row) if row else None

    async def get_states(self, channel_ids: list[str], db: Optional[AsyncSession] = None) -> list[TrackerStateModel]:
        if not channel_ids:
            return []
        async with get_async_db_context(db) as db:
            rows = (await db.execute(select(TrackerState).where(TrackerState.channel_id.in_(channel_ids)))).scalars()
            return [TrackerStateModel.model_validate(row) for row in rows]

    async def get_new_messages(
        self, channel_id: str, watermark: int, limit: int, db: Optional[AsyncSession] = None
    ) -> list[Message]:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(Message)
                .where(Message.channel_id == channel_id, Message.created_at > watermark)
                .order_by(Message.created_at)
                .limit(limit)
            )
            return list(result.scalars().all())

    async def get_edited_messages(
        self, channel_id: str, watermark: int, edit_watermark: int, limit: int, db: Optional[AsyncSession] = None
    ) -> list[Message]:
        """Already-processed messages edited after the last run."""
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(Message)
                .where(
                    Message.channel_id == channel_id,
                    Message.created_at <= watermark,
                    Message.updated_at > edit_watermark,
                    Message.updated_at > Message.created_at,
                )
                .order_by(Message.updated_at)
                .limit(limit)
            )
            return list(result.scalars().all())

    async def get_tail_messages(
        self, channel_id: str, watermark: int, limit: int, db: Optional[AsyncSession] = None
    ) -> list[Message]:
        """The newest unprocessed messages, oldest first."""
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(Message)
                .where(Message.channel_id == channel_id, Message.created_at > watermark)
                .order_by(Message.created_at.desc())
                .limit(limit)
            )
            return list(reversed(result.scalars().all()))

    async def complete(
        self,
        channel_id: str,
        lease_id: str,
        *,
        state: Optional[dict],
        watermark: int,
        edit_watermark: int,
        item: Optional[dict] = None,
        db: Optional[AsyncSession] = None,
    ) -> bool:
        """Store a processed batch if the lease is still ours, and requeue if messages remain."""
        now = time.time_ns()
        async with get_async_db_context(db) as db:
            stmt = select(TrackerState).where(TrackerState.channel_id == channel_id)
            if db.bind.dialect.name == 'postgresql':
                stmt = stmt.with_for_update()
            row = (await db.execute(stmt)).scalar_one_or_none()
            if not row or row.lease_id != lease_id:
                await db.rollback()
                log.warning('Tracker lease lost for channel %s; dropping result', channel_id)
                return False

            # Counted under the row lock, so a concurrent mark_dirty either waits for us or is counted here.
            remaining = (
                await db.execute(
                    select(func.count())
                    .select_from(Message)
                    .where(Message.channel_id == channel_id, Message.created_at > watermark)
                )
            ).scalar() or 0

            if state is not None:
                row.state = state
                row.version = (row.version or 0) + 1
                row.processed_at = now
            row.watermark = watermark
            row.edit_watermark = max(row.edit_watermark or 0, edit_watermark)
            row.pending_count = remaining
            row.dirty_at = now if remaining else None
            row.force = bool(remaining)  # keep catching up without waiting for the idle timer
            row.lease_id = None
            row.lease_until = None
            row.fail_count = 0
            row.next_attempt_at = None
            row.last_error = None
            row.updated_at = now

            if item:
                db.add(TrackerItem(id=str(uuid.uuid4()), channel_id=channel_id, created_at=now, **item))
            await db.commit()
            return True

    async def fail(self, channel_id: str, lease_id: str, error: str, db: Optional[AsyncSession] = None) -> None:
        now = time.time_ns()
        async with get_async_db_context(db) as db:
            row = await db.get(TrackerState, channel_id)
            if not row or row.lease_id != lease_id:
                return
            row.fail_count = (row.fail_count or 0) + 1
            backoff_s = min(30 * 2 ** (row.fail_count - 1), 3600)
            row.next_attempt_at = now + backoff_s * 1_000_000_000
            row.last_error = error[:2000]
            row.lease_id = None
            row.lease_until = None
            row.updated_at = now
            await db.commit()

    async def get_items(
        self, channel_id: str, start: int, end: int, limit: int = 50, db: Optional[AsyncSession] = None
    ) -> list[TrackerItemModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(TrackerItem)
                .where(
                    TrackerItem.channel_id == channel_id,
                    and_(TrackerItem.period_end >= start, TrackerItem.period_start <= end),
                )
                .order_by(TrackerItem.period_end)
                .limit(limit)
            )
            return [TrackerItemModel.model_validate(row) for row in result.scalars().all()]

    async def get_stats(self, now: int, db: Optional[AsyncSession] = None) -> dict:
        async with get_async_db_context(db) as db:

            async def count(*conditions) -> int:
                stmt = select(func.count()).select_from(TrackerState)
                if conditions:
                    stmt = stmt.where(*conditions)
                return (await db.execute(stmt)).scalar() or 0

            oldest_dirty = (await db.execute(select(func.min(TrackerState.dirty_at)))).scalar()
            return {
                'channels': await count(),
                'dirty': await count(TrackerState.dirty_at.is_not(None)),
                'processing': await count(TrackerState.lease_until > now),
                'failing': await count(TrackerState.fail_count > 0),
                'max_lag_seconds': round((now - oldest_dirty) / 1e9) if oldest_dirty else 0,
            }


Trackers = TrackerTable()
