"""Scheduled reports: the queue of sends, with the AI comments written ahead of the send time.

Revision ID: f4c6e8a0b2d3
Revises: e3b5d7f9a1c2
"""

import sqlalchemy as sa
from alembic import op

revision = 'f4c6e8a0b2d3'
down_revision = 'e3b5d7f9a1c2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'report_job',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('schedule_id', sa.Text(), nullable=False),
        sa.Column('run_at', sa.BigInteger(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('lease_id', sa.Text(), nullable=True),
        sa.Column('lease_until', sa.BigInteger(), nullable=True),
        sa.Column('retry_at', sa.BigInteger(), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('comment_digest', sa.Text(), nullable=True),
        sa.Column('written_at', sa.BigInteger(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
        sa.UniqueConstraint('schedule_id', 'run_at', name='uq_report_job_schedule_run'),
    )
    op.create_index('ix_report_job_status', 'report_job', ['status', 'run_at'])


def downgrade():
    op.drop_index('ix_report_job_status', table_name='report_job')
    op.drop_table('report_job')
