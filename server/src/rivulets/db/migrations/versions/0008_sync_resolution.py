"""Add the sync_resolution table (#348)

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-16

Last-writer-wins register for conflict resolutions: two nodes resolving
the same sync conflict independently publish vector clocks that are
concurrent again, so the clocks alone can never pick a winner — the
(resolved_at, node_id) stamp stored here is the deterministic tie-break
both sides agree on. See SyncResolution's docstring in db/models.py.

Hand-written, same reasoning as 0002-0007: 0001's dynamic reconciliation
against today's `Base.metadata` already creates this table for a workspace
that runs the whole migration chain from scratch, so the CREATE here is
guarded — it only fires for a workspace already sitting at 0007 that needs
a real catch-up.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "sync_resolution"


def _table_exists() -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return _TABLE in inspector.get_table_names()


def upgrade() -> None:
    if _table_exists():
        return
    op.create_table(
        _TABLE,
        sa.Column("entity_type", sa.String(), primary_key=True),
        sa.Column("entity_id", sa.String(), primary_key=True),
        sa.Column("resolved_at", sa.String(), nullable=False),
        sa.Column("node_id", sa.String(), nullable=False),
    )


def downgrade() -> None:
    if _table_exists():
        op.drop_table(_TABLE)
