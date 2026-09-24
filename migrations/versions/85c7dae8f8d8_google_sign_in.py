"""Google sign-in.

Ouverture au public, étape 5 : un compte peut être lié à un compte Google par son
identifiant stable (`users.google_sub`), unique.

Revision ID: 85c7dae8f8d8
Revises: a8146df1cd22
Create Date: 2026-09-24 23:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "85c7dae8f8d8"
down_revision: str | None = "a8146df1cd22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("google_sub", sa.String(length=255), nullable=True))
        batch_op.create_unique_constraint(batch_op.f("uq_users_google_sub"), ["google_sub"])


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("uq_users_google_sub"), type_="unique")
        batch_op.drop_column("google_sub")
