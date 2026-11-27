"""Add tracker_state.rebuild: a channel's summary being rebuilt next to the one in use, swapped in when complete.

Revision ID: c1d3e5f7a9b2
Revises: b7d2f4a6c8e1
"""

import sqlalchemy as sa
from alembic import op

revision = 'c1d3e5f7a9b2'
down_revision = 'b7d2f4a6c8e1'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tracker_state', sa.Column('rebuild', sa.JSON(none_as_null=True), nullable=True))


def downgrade():
    with op.batch_alter_table('tracker_state') as batch_op:
        batch_op.drop_column('rebuild')
