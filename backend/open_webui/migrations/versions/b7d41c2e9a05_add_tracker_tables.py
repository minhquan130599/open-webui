"""Add project tracker state and item tables.

Revision ID: b7d41c2e9a05
Revises: 9f6c2a8d1b43
"""

import sqlalchemy as sa
from alembic import op

revision = 'b7d41c2e9a05'
down_revision = '9f6c2a8d1b43'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'tracker_state',
        sa.Column('channel_id', sa.Text(), primary_key=True),
        sa.Column('state', sa.JSON(), nullable=True),
        sa.Column('schema_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('version', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('watermark', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('edit_watermark', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('dirty_at', sa.BigInteger(), nullable=True),
        sa.Column('last_activity_at', sa.BigInteger(), nullable=True),
        sa.Column('pending_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('force', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('lease_id', sa.Text(), nullable=True),
        sa.Column('lease_until', sa.BigInteger(), nullable=True),
        sa.Column('fail_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('next_attempt_at', sa.BigInteger(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('processed_at', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
    )
    op.create_index('ix_tracker_state_dirty', 'tracker_state', ['dirty_at'])
    op.create_index('ix_tracker_state_lease', 'tracker_state', ['lease_until'])

    op.create_table(
        'tracker_item',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('channel_id', sa.Text(), nullable=False),
        sa.Column('source_type', sa.Text(), nullable=False),
        sa.Column('source_ids', sa.JSON(), nullable=False),
        sa.Column('period_start', sa.BigInteger(), nullable=False),
        sa.Column('period_end', sa.BigInteger(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('data', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
    )
    op.create_index('ix_tracker_item_channel_period', 'tracker_item', ['channel_id', 'period_end'])
    # Tracker scans "messages of a channel after a watermark", including thread replies.
    op.create_index('ix_message_channel_created', 'message', ['channel_id', 'created_at'])


def downgrade():
    op.drop_index('ix_message_channel_created', table_name='message')
    op.drop_table('tracker_item')
    op.drop_table('tracker_state')
