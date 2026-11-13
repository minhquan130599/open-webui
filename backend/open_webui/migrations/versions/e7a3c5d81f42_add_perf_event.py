"""Add perf_event (performance monitoring events).

Revision ID: e7a3c5d81f42
Revises: d5f2a7c91b34
"""

import sqlalchemy as sa
from alembic import op

revision = 'e7a3c5d81f42'
down_revision = 'd5f2a7c91b34'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'perf_event',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('ts', sa.BigInteger(), nullable=False),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('source', sa.Text(), nullable=True),
        sa.Column('user_id', sa.Text(), nullable=True),
        sa.Column('model_id', sa.Text(), nullable=True),
        sa.Column('chat_id', sa.Text(), nullable=True),
        sa.Column('message_id', sa.Text(), nullable=True),
        sa.Column('name', sa.Text(), nullable=True),
        sa.Column('status', sa.Text(), nullable=False, server_default='ok'),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('ttft_ms', sa.Integer(), nullable=True),
        sa.Column('input_tokens', sa.Integer(), nullable=True),
        sa.Column('output_tokens', sa.Integer(), nullable=True),
        sa.Column('count', sa.Integer(), nullable=True),
        sa.Column('meta', sa.JSON(), nullable=True),
    )
    op.create_index('ix_perf_event_kind_ts', 'perf_event', ['kind', 'ts'])
    op.create_index('ix_perf_event_ts', 'perf_event', ['ts'])


def downgrade():
    op.drop_index('ix_perf_event_ts', table_name='perf_event')
    op.drop_index('ix_perf_event_kind_ts', table_name='perf_event')
    op.drop_table('perf_event')
