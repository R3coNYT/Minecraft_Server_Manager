"""Per-server Discord notifications.

Chaque serveur a désormais son propre webhook (`server_notifications`). Le
webhook global est conservé, mais ne sert plus qu'aux événements de MSM : ses
événements cochés deviennent la création et la suppression de serveurs.

Revision ID: f414e4b69a05
Revises: 6913bd3193a1
Create Date: 2026-09-24 01:41:35.906475
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from msm.db.types import UtcDateTime

revision: str = "f414e4b69a05"
down_revision: str | None = "6913bd3193a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Clé du réglage global dans `app_settings`.
_GLOBAL_KEY = "notifications.discord"
_GLOBAL_EVENTS = ["server_created", "server_deleted"]
#: Événements cochés par défaut avant cette migration, remis en cas de retour arrière.
_PREVIOUS_DEFAULTS = ["server_crashed", "server_restarted", "backup_failed", "schedule_failed"]

_app_settings = sa.table(
    "app_settings",
    sa.column("key", sa.String()),
    sa.column("value", sa.JSON()),
)


def _set_global_events(events: list[str]) -> None:
    connection = op.get_bind()
    row = connection.execute(
        sa.select(_app_settings.c.value).where(_app_settings.c.key == _GLOBAL_KEY)
    ).first()
    if row is None or not isinstance(row.value, dict):
        return
    # L'URL chiffrée et l'activation restent telles quelles.
    connection.execute(
        _app_settings.update()
        .where(_app_settings.c.key == _GLOBAL_KEY)
        .values(value={**row.value, "events": events})
    )


def upgrade() -> None:
    op.create_table(
        "server_notifications",
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("webhook_url_enc", sa.String(length=1024), nullable=True),
        sa.Column("events", sa.JSON(), nullable=False),
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
            name=op.f("fk_server_notifications_server_id_servers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("server_id", name=op.f("pk_server_notifications")),
    )
    _set_global_events(_GLOBAL_EVENTS)


def downgrade() -> None:
    _set_global_events(_PREVIOUS_DEFAULTS)
    op.drop_table("server_notifications")
