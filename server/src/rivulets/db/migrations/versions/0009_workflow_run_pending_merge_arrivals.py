"""Add workflow_run.pending_merge_arrivals_json (#359)

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-16

Persists the merge arrivals a mid-fan-out pause would otherwise strand:
sibling branches that reached a merge node before another sibling paused
on a 'human_input' node are banked here (a JSON list of
[merge_node_id, input] pairs) so resume_workflow can fold them back into
merge resolution instead of firing the merge with only the resumed
branch's input -- see WorkflowRun's own docstring (db/models.py).
Hand-written for the same reason 0004 was: 0001's dynamic reconciliation
already backfills this column for a workspace migrating from scratch, but
one already sitting at 0008 needs a real ALTER TABLE to catch up.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "workflow_run"
_COLUMN = "pending_merge_arrivals_json"


def _existing_columns() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {col["name"] for col in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    if _COLUMN not in _existing_columns():
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.add_column(sa.Column(_COLUMN, sa.Text(), nullable=False, server_default="[]"))


def downgrade() -> None:
    if _COLUMN in _existing_columns():
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_column(_COLUMN)
