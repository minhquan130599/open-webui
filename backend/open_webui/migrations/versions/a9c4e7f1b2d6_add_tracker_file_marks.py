"""Add file_marks (files marked as reference material, and files to read again) to tracker_state.

Revision ID: a9c4e7f1b2d6
Revises: f4c6e8a0b2d3
"""

import sqlalchemy as sa
from alembic import op

revision = 'a9c4e7f1b2d6'
down_revision = 'f4c6e8a0b2d3'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tracker_state', sa.Column('file_marks', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('tracker_state') as batch_op:
        batch_op.drop_column('file_marks')
