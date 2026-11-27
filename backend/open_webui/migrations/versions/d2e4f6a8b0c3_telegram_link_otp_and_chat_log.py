"""Telegram: linking confirmed by a one-time code sent by email, and the bot's chat messages (deleted every day).

Revision ID: d2e4f6a8b0c3
Revises: c1d3e5f7a9b2
"""

import sqlalchemy as sa
from alembic import op

revision = 'd2e4f6a8b0c3'
down_revision = 'c1d3e5f7a9b2'
branch_labels = None
depends_on = None


def upgrade():
    # Batch mode: SQLite cannot alter tables in place (the other databases ignore it).
    with op.batch_alter_table('telegram_link_code') as batch:
        batch.add_column(sa.Column('pending_by', sa.Text(), nullable=True))
        batch.add_column(sa.Column('pending_data', sa.JSON(), nullable=True))
        batch.add_column(sa.Column('otp_hash', sa.Text(), nullable=True))
        batch.add_column(sa.Column('otp_expires_at', sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column('otp_attempts', sa.Integer(), nullable=False, server_default='0'))
    op.create_table(
        'telegram_chat_log',
        sa.Column('chat_id', sa.Text(), primary_key=True),
        sa.Column('tg_message_id', sa.BigInteger(), primary_key=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
    )
    op.create_index('ix_telegram_chat_log_created', 'telegram_chat_log', ['created_at'])


def downgrade():
    op.drop_index('ix_telegram_chat_log_created', table_name='telegram_chat_log')
    op.drop_table('telegram_chat_log')
    with op.batch_alter_table('telegram_link_code') as batch:
        batch.drop_column('otp_attempts')
        batch.drop_column('otp_expires_at')
        batch.drop_column('otp_hash')
        batch.drop_column('pending_data')
        batch.drop_column('pending_by')
