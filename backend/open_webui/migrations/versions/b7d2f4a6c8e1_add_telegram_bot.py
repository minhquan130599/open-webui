"""Telegram bot: accounts linked to users, link codes, chat state, bot messages mapped to answers.

Revision ID: b7d2f4a6c8e1
Revises: a9c4e7f1b2d6
"""

import sqlalchemy as sa
from alembic import op

revision = 'b7d2f4a6c8e1'
down_revision = 'a9c4e7f1b2d6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'telegram_account',
        sa.Column('telegram_user_id', sa.Text(), primary_key=True),
        sa.Column('user_id', sa.Text(), nullable=False, unique=True),
        sa.Column('username', sa.Text(), nullable=True),
        sa.Column('display_name', sa.Text(), nullable=True),
        sa.Column('linked_at', sa.BigInteger(), nullable=False),
        sa.Column('last_seen_at', sa.BigInteger(), nullable=True),
    )
    op.create_table(
        'telegram_link_code',
        sa.Column('code_hash', sa.Text(), primary_key=True),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('used_by', sa.Text(), nullable=True),
        sa.Column('used_at', sa.BigInteger(), nullable=True),
    )
    op.create_index('ix_telegram_link_code_user', 'telegram_link_code', ['user_id'])
    op.create_table(
        'telegram_session',
        sa.Column('chat_id', sa.Text(), primary_key=True),
        sa.Column('telegram_user_id', sa.Text(), nullable=False),
        sa.Column('mode', sa.Text(), nullable=False, server_default='direct'),
        sa.Column('model_id', sa.Text(), nullable=True),
        sa.Column('chat_ref', sa.Text(), nullable=True),
        sa.Column('last_message_id', sa.Text(), nullable=True),
        sa.Column('channel_id', sa.Text(), nullable=True),
        sa.Column('data', sa.JSON(), nullable=True),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
    )
    op.create_table(
        'telegram_message_map',
        sa.Column('chat_id', sa.Text(), primary_key=True),
        sa.Column('tg_message_id', sa.BigInteger(), primary_key=True),
        sa.Column('target', sa.Text(), nullable=False),
        sa.Column('target_id', sa.Text(), nullable=False),
        sa.Column('message_id', sa.Text(), nullable=False),
        sa.Column('thread_id', sa.Text(), nullable=True),
        sa.Column('question', sa.Text(), nullable=True),
        sa.Column('data', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
    )
    op.create_index('ix_telegram_message_map_created', 'telegram_message_map', ['created_at'])


def downgrade():
    op.drop_index('ix_telegram_message_map_created', table_name='telegram_message_map')
    op.drop_table('telegram_message_map')
    op.drop_table('telegram_session')
    op.drop_index('ix_telegram_link_code_user', table_name='telegram_link_code')
    op.drop_table('telegram_link_code')
    op.drop_table('telegram_account')
