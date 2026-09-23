"""Intégration avec le serveur de fichiers d'un launcher.

`launcher_integrations` porte les réglages et l'état de synchronisation d'un
serveur ; `launcher_files` retient les fichiers que MSM y a installés, pour ne
jamais supprimer un mod ajouté à la main.

Revision ID: 6913bd3193a1
Revises: bd4d4cce333f
Create Date: 2026-09-23 03:02:49.492922
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from msm.db.types import UtcDateTime

revision: str = "6913bd3193a1"
down_revision: str | None = "bd4d4cce333f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "launcher_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("mtime_ns", sa.BigInteger(), nullable=False),
        sa.Column("installed_at", UtcDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["servers.id"],
            name=op.f("fk_launcher_files_server_id_servers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_launcher_files")),
        sa.UniqueConstraint("server_id", "path", name=op.f("uq_launcher_files_server_id")),
    )
    with op.batch_alter_table("launcher_files", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_launcher_files_server_id"), ["server_id"], unique=False
        )

    op.create_table(
        "launcher_integrations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("file_server_url", sa.String(length=512), nullable=False),
        sa.Column("sync_paths", sa.JSON(), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("push_token_encrypted", sa.Text(), nullable=True),
        sa.Column("side_overrides", sa.JSON(), nullable=False),
        sa.Column("side_cache", sa.JSON(), nullable=False),
        sa.Column("manifest_etag", sa.String(length=256), nullable=True),
        sa.Column("pack_version", sa.String(length=64), nullable=True),
        sa.Column("manifest_entries", sa.JSON(), nullable=False),
        sa.Column("next_sync_at", UtcDateTime(timezone=True), nullable=True),
        sa.Column("last_sync_at", UtcDateTime(timezone=True), nullable=True),
        sa.Column(
            "last_sync_status",
            sa.Enum(
                "NEVER",
                "UP_TO_DATE",
                "APPLIED",
                "PENDING_RESTART",
                "BLOCKED",
                "FAILED",
                name="syncstatus",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("last_sync_error", sa.Text(), nullable=True),
        sa.Column("last_sync_summary", sa.JSON(), nullable=False),
        sa.Column("pending_plan", sa.JSON(), nullable=True),
        sa.Column("state_revision", sa.Integer(), nullable=False),
        sa.Column("pushed_revision", sa.Integer(), nullable=False),
        sa.Column("disabled_files", sa.JSON(), nullable=False),
        sa.Column("last_push_at", UtcDateTime(timezone=True), nullable=True),
        sa.Column("last_push_error", sa.Text(), nullable=True),
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
            ["server_id"],
            ["servers.id"],
            name=op.f("fk_launcher_integrations_server_id_servers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_launcher_integrations")),
        sa.UniqueConstraint("server_id", name=op.f("uq_launcher_integrations_server_id")),
    )


def downgrade() -> None:
    op.drop_table("launcher_integrations")
    with op.batch_alter_table("launcher_files", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_launcher_files_server_id"))

    op.drop_table("launcher_files")
