"""Tâches programmées.

`next_run_at` est indexé : la boucle du planificateur interroge cette colonne
toutes les vingt secondes, sur toutes les tâches de toutes les machines.

Revision ID: bd4d4cce333f
Revises: 7ad40f0c959a
Create Date: 2026-08-11 23:39:46.558627
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from msm.db.types import UtcDateTime

revision: str = "bd4d4cce333f"
down_revision: str | None = "7ad40f0c959a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schedules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "action",
            sa.Enum(
                "BACKUP",
                "RESTART",
                "START",
                "STOP",
                "EVENT",
                "COMMAND",
                name="scheduleaction",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("rule", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", UtcDateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", UtcDateTime(timezone=True), nullable=True),
        sa.Column(
            "last_status",
            sa.Enum(
                "NEVER",
                "SUCCESS",
                "FAILED",
                "MISSED",
                "SKIPPED",
                name="schedulestatus",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            UtcDateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            UtcDateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_schedules_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["servers.id"],
            name=op.f("fk_schedules_server_id_servers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schedules")),
    )
    with op.batch_alter_table("schedules", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_schedules_next_run_at"), ["next_run_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_schedules_server_id"), ["server_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("schedules", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_schedules_server_id"))
        batch_op.drop_index(batch_op.f("ix_schedules_next_run_at"))

    op.drop_table("schedules")
