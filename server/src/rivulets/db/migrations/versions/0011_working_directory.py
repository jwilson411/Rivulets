"""Add channel and rivulet working_directory (#project folder)

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-18

Node-local absolute paths for the folder agents read/write/build in.
Channel (river) holds the default; a rivulet may override it. Neither
column is synced — an absolute path is meaningless on another peer.

Hand-written for the same reason 0004/0009/0010 were: 0001's dynamic
reconciliation already backfills these columns for a workspace migrating
from scratch, but one already sitting at 0010 needs a real ALTER TABLE
to catch up.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMN = "working_directory"
_TABLES = ("channel", "rivulet")


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for table in _TABLES:
        if table not in tables:
            continue
        if _COLUMN not in _existing_columns(table):
            with op.batch_alter_table(table) as batch_op:
                batch_op.add_column(sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for table in _TABLES:
        if table not in tables:
            continue
        if _COLUMN in _existing_columns(table):
            with op.batch_alter_table(table) as batch_op:
                batch_op.drop_column(_COLUMN)
