"""Historique des ressources par serveur.

L'index composite (serveur, horodatage) n'est pas décoratif : toutes les lectures
filtrent sur ce couple, et la table grossit d'un point par serveur toutes les
trente secondes.

Revision ID: 7ad40f0c959a
Revises: 3d476356818d
Create Date: 2026-08-11 21:38:16.021688
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from msm.db.types import UtcDateTime

revision: str = "7ad40f0c959a"
down_revision: str | None = "3d476356818d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "metric_samples",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("ts", UtcDateTime(timezone=True), nullable=False),
        sa.Column("cpu_percent", sa.Float(), nullable=False),
        sa.Column("memory_mb", sa.Float(), nullable=False),
        sa.Column("players_online", sa.Integer(), nullable=False),
        sa.Column("online", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["servers.id"],
            name=op.f("fk_metric_samples_server_id_servers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metric_samples")),
    )
    with op.batch_alter_table("metric_samples", schema=None) as batch_op:
        batch_op.create_index("ix_metric_samples_server_ts", ["server_id", "ts"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("metric_samples", schema=None) as batch_op:
        batch_op.drop_index("ix_metric_samples_server_ts")

    op.drop_table("metric_samples")
