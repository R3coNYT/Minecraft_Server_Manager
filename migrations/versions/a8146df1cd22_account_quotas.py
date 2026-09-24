"""Account quotas.

Ouverture au public, étape 3 : surcharge des quotas d'hébergement par compte
(`users.quota`). Les quotas par défaut, la plage de ports et le dossier des
comptes vivent dans `app_settings` (clé `hosting`) et n'ont pas besoin de schéma.

Revision ID: a8146df1cd22
Revises: f8f19ee6945e
Create Date: 2026-09-24 18:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8146df1cd22"
down_revision: str | None = "f8f19ee6945e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("quota", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("quota")
