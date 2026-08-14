"""Add UNIQUE constraint on workspace.singleton (#324)

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-14

There is exactly one workspace per install (product invariant), but
nothing in the schema enforced it -- two concurrent first logins on an
empty DB could both see no `Workspace` row and both insert one, leaving
every later `select(Workspace).scalar_one_or_none()` call (login, invite
accept) to raise an unhandled `MultipleResultsFound`. See Workspace's
docstring in db/models.py for the full reasoning.

Hand-written, same reasoning as 0002-0005: 0001's dynamic reconciliation
against today's `Base.metadata` already backfills `singleton` (and its
constraint) for a workspace that runs the whole migration chain from
scratch; a workspace already sitting at 0005 needs a real ALTER TABLE to
catch up.

If this workspace already has two-or-more `workspace` rows from #324's
race, the `create_unique_constraint` below fails loudly (batch mode
rebuilds the table by copying rows into the new, constrained one, which
violates the constraint) rather than silently picking a winner -- same
"raise rather than guess" treatment 0005 gives idx_tool_custom_name. An
operator hitting this needs to pick a surviving row by hand.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "workspace"
_COLUMN = "singleton"
_CONSTRAINT = "idx_workspace_singleton"


def _existing_columns() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {col["name"] for col in inspector.get_columns(_TABLE)}


def _has_constraint() -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    names = {uc["name"] for uc in inspector.get_unique_constraints(_TABLE)}
    names |= {idx["name"] for idx in inspector.get_indexes(_TABLE)}
    return _CONSTRAINT in names


def upgrade() -> None:
    existing = _existing_columns()
    has_constraint = _has_constraint()
    with op.batch_alter_table(_TABLE) as batch_op:
        if _COLUMN not in existing:
            batch_op.add_column(
                sa.Column(_COLUMN, sa.Integer(), nullable=False, server_default="1")
            )
        if not has_constraint:
            batch_op.create_unique_constraint(_CONSTRAINT, [_COLUMN])


def downgrade() -> None:
    existing = _existing_columns()
    has_constraint = _has_constraint()
    with op.batch_alter_table(_TABLE) as batch_op:
        if has_constraint:
            batch_op.drop_constraint(_CONSTRAINT, type_="unique")
        if _COLUMN in existing:
            batch_op.drop_column(_COLUMN)
