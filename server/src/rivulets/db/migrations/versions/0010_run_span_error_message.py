"""Add run_span.error_message (#405)

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-17

Stores a sanitized failure reason on an error span so the Runs page can
show why a step failed (e.g. "not registered with AgentOS") instead of
just "Writer · Failed · 2.0s". The rivulet thread keeps a separate
plain-language system_alert and does not read this column.

Hand-written for the same reason 0004/0009 were: 0001's dynamic
reconciliation already backfills this column for a workspace migrating
from scratch, but one already sitting at 0009 needs a real ALTER TABLE
to catch up.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "run_span"
_COLUMN = "error_message"


def _existing_columns() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    if _TABLE not in sa.inspect(op.get_bind()).get_table_names():
        return
    if _COLUMN not in _existing_columns():
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.add_column(sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    if _TABLE not in sa.inspect(op.get_bind()).get_table_names():
        return
    if _COLUMN in _existing_columns():
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_column(_COLUMN)
