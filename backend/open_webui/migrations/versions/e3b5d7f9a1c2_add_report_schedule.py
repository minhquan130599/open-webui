"""Scheduled reports: schedules, Telegram chats linked to users, and a named lease.

Revision ID: e3b5d7f9a1c2
Revises: a4b6c8d0e2f1
"""

import sqlalchemy as sa
from alembic import op

revision = 'e3b5d7f9a1c2'
down_revision = 'a4b6c8d0e2f1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'report_schedule',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('config', sa.JSON(), nullable=True),
        sa.Column('next_run_at', sa.BigInteger(), nullable=True),
        sa.Column('last_run_at', sa.BigInteger(), nullable=True),
        sa.Column('last_status', sa.Text(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('last_digest', sa.Text(), nullable=True),
        sa.Column('lease_id', sa.Text(), nullable=True),
        sa.Column('lease_until', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
    )
    op.create_index('ix_report_schedule_user', 'report_schedule', ['user_id'])
    op.create_index('ix_report_schedule_next_run', 'report_schedule', ['next_run_at'])

    op.create_table(
        'report_telegram_link',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('code', sa.Text(), nullable=True),
        sa.Column('chat_id', sa.Text(), nullable=True),
        sa.Column('chat_title', sa.Text(), nullable=True),
        sa.Column('chat_type', sa.Text(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('linked_at', sa.BigInteger(), nullable=True),
    )
    op.create_index('ix_report_telegram_link_user', 'report_telegram_link', ['user_id'])
    op.create_index('ix_report_telegram_link_code', 'report_telegram_link', ['code'])

    op.create_table(
        'report_lock',
        sa.Column('name', sa.Text(), primary_key=True),
        sa.Column('lease_id', sa.Text(), nullable=True),
        sa.Column('lease_until', sa.BigInteger(), nullable=True),
        sa.Column('data', sa.JSON(), nullable=True),
    )


def downgrade():
    op.drop_table('report_lock')
    op.drop_index('ix_report_telegram_link_code', table_name='report_telegram_link')
    op.drop_index('ix_report_telegram_link_user', table_name='report_telegram_link')
    op.drop_table('report_telegram_link')
    op.drop_index('ix_report_schedule_next_run', table_name='report_schedule')
    op.drop_index('ix_report_schedule_user', table_name='report_schedule')
    op.drop_table('report_schedule')
