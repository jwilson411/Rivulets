"""Add workflow_run.visit_counts_json / total_steps (#249)

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-13

Persists the loop guard's per-run counters (workflows/engine.py's
MAX_NODE_VISITS_PER_RUN / MAX_TOTAL_STEPS_PER_RUN) so resume_workflow can
restore them across a pause/resume boundary instead of starting a fresh
`_RunContext` at zero every time -- see WorkflowRun's own docstring
(db/models.py). Hand-written for the same reason 0002 was: 0001's dynamic
reconciliation against today's `Base.metadata` already backfills these
columns for a workspace that runs the whole migration chain from scratch,
but a workspace already sitting at 0002 needs a real ALTER TABLE to catch
up.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "workflow_run"
_COLUMNS = ("visit_counts_json", "total_steps")


def _existing_columns() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {col["name"] for col in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    existing = _existing_columns()
    with op.batch_alter_table(_TABLE) as batch_op:
        if "visit_counts_json" not in existing:
            batch_op.add_column(
                sa.Column(
                    "visit_counts_json", sa.Text(), nullable=False, server_default="{}"
                )
            )
        if "total_steps" not in existing:
            batch_op.add_column(
                sa.Column("total_steps", sa.Integer(), nullable=False, server_default="0")
            )


def downgrade() -> None:
    existing = _existing_columns()
    with op.batch_alter_table(_TABLE) as batch_op:
        for column in _COLUMNS:
            if column in existing:
                batch_op.drop_column(column)
