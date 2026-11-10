"""Add pending_deletions to tracker_state.

Revision ID: c3e8a1f05d72
Revises: b7d41c2e9a05
"""

import sqlalchemy as sa
from alembic import op

revision = 'c3e8a1f05d72'
down_revision = 'b7d41c2e9a05'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tracker_state', sa.Column('pending_deletions', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('tracker_state') as batch_op:
        batch_op.drop_column('pending_deletions')
