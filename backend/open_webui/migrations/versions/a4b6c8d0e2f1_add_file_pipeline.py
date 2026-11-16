"""File pipeline: processing queue per file and per-page OCR text, scores and vision repairs.

Revision ID: a4b6c8d0e2f1
Revises: f2c4d6e8a913
"""

import sqlalchemy as sa
from alembic import op

revision = 'a4b6c8d0e2f1'
down_revision = 'f2c4d6e8a913'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'file_job',
        sa.Column('file_id', sa.Text(), primary_key=True),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('stage', sa.Text(), nullable=True),
        sa.Column('progress', sa.JSON(), nullable=True),
        sa.Column('stats', sa.JSON(), nullable=True),
        sa.Column('options', sa.JSON(), nullable=True),
        sa.Column('lease_id', sa.Text(), nullable=True),
        sa.Column('lease_until', sa.BigInteger(), nullable=True),
        sa.Column('fail_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('next_attempt_at', sa.BigInteger(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('queued_at', sa.BigInteger(), nullable=False),
        sa.Column('started_at', sa.BigInteger(), nullable=True),
        sa.Column('finished_at', sa.BigInteger(), nullable=True),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
    )
    op.create_index('ix_file_job_status', 'file_job', ['status'])
    op.create_index('ix_file_job_queued_at', 'file_job', ['queued_at'])

    op.create_table(
        'file_page',
        sa.Column('file_id', sa.Text(), primary_key=True),
        sa.Column('page', sa.Integer(), primary_key=True),
        sa.Column('method', sa.Text(), nullable=True),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('score', sa.Float(), nullable=True),
        sa.Column('raw_score', sa.Float(), nullable=True),
        sa.Column('issues', sa.JSON(), nullable=True),
        sa.Column('raw_text', sa.Text(), nullable=True),
        sa.Column('text', sa.Text(), nullable=True),
        sa.Column('repair', sa.JSON(), nullable=True),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
    )
    op.create_index('ix_file_page_status', 'file_page', ['file_id', 'status'])


def downgrade():
    op.drop_index('ix_file_page_status', table_name='file_page')
    op.drop_table('file_page')
    op.drop_index('ix_file_job_queued_at', table_name='file_job')
    op.drop_index('ix_file_job_status', table_name='file_job')
    op.drop_table('file_job')
