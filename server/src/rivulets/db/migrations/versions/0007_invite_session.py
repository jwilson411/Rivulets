"""Add the invite_session table (#350)

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-15

Invite-redeemed sessions get a persistent re-entry credential so a page
refresh or sign-out no longer permanently locks an invited human out of a
spent single-use invite — see InviteSession's docstring in db/models.py.

Hand-written, same reasoning as 0002-0006: 0001's dynamic reconciliation
against today's `Base.metadata` already creates this table for a workspace
that runs the whole migration chain from scratch, so the CREATE here is
guarded — it only fires for a workspace already sitting at 0006 that needs
a real catch-up.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "invite_session"


def _table_exists() -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return _TABLE in inspector.get_table_names()


def upgrade() -> None:
    if _table_exists():
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("secret_hash", sa.String(), nullable=False),
        sa.Column("invite_id", sa.String(), nullable=False),
        sa.Column("human_id", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
    )


def downgrade() -> None:
    if _table_exists():
        op.drop_table(_TABLE)
