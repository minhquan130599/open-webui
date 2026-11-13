"""Index perf_event by message id (timeline of one answer).

Revision ID: f2c4d6e8a913
Revises: e7a3c5d81f42
"""

from alembic import op

revision = 'f2c4d6e8a913'
down_revision = 'e7a3c5d81f42'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index('ix_perf_event_message_id', 'perf_event', ['message_id'])


def downgrade():
    op.drop_index('ix_perf_event_message_id', table_name='perf_event')
