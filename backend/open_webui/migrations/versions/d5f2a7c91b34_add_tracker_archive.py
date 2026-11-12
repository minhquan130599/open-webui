"""Add archive (closed/cancelled work items) to tracker_state.

Revision ID: d5f2a7c91b34
Revises: c3e8a1f05d72
"""

import sqlalchemy as sa
from alembic import op

revision = 'd5f2a7c91b34'
down_revision = 'c3e8a1f05d72'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tracker_state', sa.Column('archive', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('tracker_state') as batch_op:
        batch_op.drop_column('archive')
