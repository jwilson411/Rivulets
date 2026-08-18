"""Add integration OAuth app + account tables (#458)

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-18

Connected third-party accounts (Google first) and the owner's OAuth
client for that vendor. Tokens and client secrets live in the credential
store, not these tables — same never-in-rivulets.db rule as
ProviderConfig.api_key_ref. Neither table is synced.

Hand-written for the same reason 0007/0011 were: 0001's dynamic
reconciliation already creates these for a workspace migrating from
scratch, but one already sitting at 0011 needs a real CREATE TABLE.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _tables()
    if "integration_oauth_app" not in existing:
        op.create_table(
            "integration_oauth_app",
            sa.Column("provider", sa.String(), primary_key=True),
            sa.Column("client_id", sa.String(), nullable=False),
            sa.Column("client_secret_ref", sa.String(), nullable=True),
            sa.Column("updated_at", sa.String(), nullable=False),
        )
    if "integration_account" not in existing:
        op.create_table(
            "integration_account",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("provider", sa.String(), nullable=False),
            sa.Column("label", sa.String(), nullable=False),
            sa.Column("account_email", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("scopes_json", sa.String(), nullable=False),
            sa.Column("credential_ref", sa.String(), nullable=True),
            sa.Column("last_error", sa.String(), nullable=True),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.Column("updated_at", sa.String(), nullable=False),
        )


def downgrade() -> None:
    existing = _tables()
    if "integration_account" in existing:
        op.drop_table("integration_account")
    if "integration_oauth_app" in existing:
        op.drop_table("integration_oauth_app")
