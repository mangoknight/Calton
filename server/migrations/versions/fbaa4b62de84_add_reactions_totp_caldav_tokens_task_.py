"""add reactions totp caldav_tokens task_unread_statuses

Revision ID: fbaa4b62de84
Revises: 0002
Create Date: 2026-08-06 13:12:44.052531

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import calton.db.types


revision: str = 'fbaa4b62de84'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('reactions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('kind', calton.db.types.keyed_text(64), nullable=False),
        sa.Column('entity_id', sa.Integer(), nullable=False),
        sa.Column('value', calton.db.types.keyed_text(64), nullable=False),
        sa.Column('created', calton.db.types.CaltonDateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('PK_reactions')),
        sa.UniqueConstraint('kind', 'entity_id', 'user_id', 'value', name='UQE_reactions'),
        sqlite_autoincrement=True,
    )
    with op.batch_alter_table('reactions', schema=None) as batch_op:
        batch_op.create_index('IDX_reactions_entity', ['kind', 'entity_id'], unique=False)
        batch_op.create_index('IDX_reactions_user_id', ['user_id'], unique=False)

    op.create_table('totp',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('secret', sa.Text(), nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default='0', nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('PK_totp')),
        sqlite_autoincrement=True,
    )
    with op.batch_alter_table('totp', schema=None) as batch_op:
        batch_op.create_index('UQE_totp_user_id', ['user_id'], unique=True)

    op.create_table('caldav_tokens',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('token', calton.db.types.keyed_text(191), nullable=False),
        sa.Column('created', calton.db.types.CaltonDateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('PK_caldav_tokens')),
        sqlite_autoincrement=True,
    )
    with op.batch_alter_table('caldav_tokens', schema=None) as batch_op:
        batch_op.create_index('UQE_caldav_tokens_id', ['id'], unique=True)
        batch_op.create_index('IDX_caldav_tokens_user_id', ['user_id'], unique=False)

    op.create_table('task_unread_statuses',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('read_at', calton.db.types.CaltonDateTime(), nullable=True),
        sa.Column('created', calton.db.types.CaltonDateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('PK_task_unread_statuses')),
        sqlite_autoincrement=True,
    )
    with op.batch_alter_table('task_unread_statuses', schema=None) as batch_op:
        batch_op.create_index('UQE_task_unread_statuses_id', ['id'], unique=True)
        batch_op.create_index('IDX_task_unread_user_id', ['user_id'], unique=False)
        batch_op.create_index('IDX_task_unread_task_id', ['task_id'], unique=False)


def downgrade() -> None:
    op.drop_table('task_unread_statuses')
    op.drop_table('caldav_tokens')
    op.drop_table('totp')
    op.drop_table('reactions')
