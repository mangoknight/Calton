"""``sessions`` — the store a refresh cookie redeems against.

Additive: a table the baseline did not create, added with the feature that needs
it (T14) rather than by editing 0001, so a database already stamped at 0001 does
not have to be rebuilt.

See ``calton.models.session`` for why this table exists despite not being one of
the 24 in the Phase 1 list.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import calton.db.types

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "sessions",
        sa.Column("id", calton.db.types.keyed_text(191), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", calton.db.types.keyed_text(191), nullable=False),
        sa.Column("device_info", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.Text(), nullable=True),
        sa.Column(
            "is_long_session",
            calton.db.types.CaltonBoolean(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("oidcid_token", sa.Text(), nullable=True),
        sa.Column("oidc_provider_key", sa.Text(), nullable=True),
        sa.Column("last_active", calton.db.types.CaltonDateTime(), nullable=False),
        sa.Column("created", calton.db.types.CaltonDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("PK_sessions")),
    )
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.create_index("IDX_sessions_user_id", ["user_id"], unique=False)
        batch_op.create_index("UQE_sessions_id", ["id"], unique=True)
        batch_op.create_index("UQE_sessions_token_hash", ["token_hash"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.drop_index("UQE_sessions_token_hash")
        batch_op.drop_index("UQE_sessions_id")
        batch_op.drop_index("IDX_sessions_user_id")

    op.drop_table("sessions")
