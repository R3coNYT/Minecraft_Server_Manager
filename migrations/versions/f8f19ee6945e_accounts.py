"""Accounts: registration, profile, bans.

Ouverture au public, étape 2.

- Comptes : langue, avatar, bannissement ; e-mail unique (en minuscules).
- `username_history` : anciens pseudos, réservés à leur titulaire.
- `invitations` : liens d'inscription à usage unique.

Deux comptes qui partageaient la même adresse avant cette migration la gardent
sur le plus ancien ; l'autre la perd, et devra en saisir une dans son profil.

Revision ID: f8f19ee6945e
Revises: 54f42fc71f84
Create Date: 2026-09-24 17:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from msm.db.types import UtcDateTime

revision: str = "f8f19ee6945e"
down_revision: str | None = "54f42fc71f84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_users = sa.table("users", sa.column("id", sa.Integer()), sa.column("email", sa.String()))


def _normalise_emails() -> None:
    connection = op.get_bind()
    seen: set[str] = set()
    rows = connection.execute(
        sa.select(_users.c.id, _users.c.email).order_by(_users.c.id)
    ).all()
    for user_id, email in rows:
        clean = (email or "").strip().lower() or None
        if clean is not None and clean in seen:
            clean = None
        if clean is not None:
            seen.add(clean)
        if clean != email:
            connection.execute(_users.update().where(_users.c.id == user_id).values(email=clean))


def upgrade() -> None:
    _normalise_emails()
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("language", sa.String(length=8), nullable=True))
        batch_op.add_column(sa.Column("avatar_updated_at", UtcDateTime(), nullable=True))
        batch_op.add_column(sa.Column("banned_at", UtcDateTime(), nullable=True))
        batch_op.add_column(sa.Column("banned_by", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("ban_reason", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_users_banned_by_users"),
            "users",
            ["banned_by"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_unique_constraint(batch_op.f("uq_users_email"), ["email"])

    op.create_table(
        "username_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("changed_at", UtcDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_username_history_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_username_history")),
    )
    with op.batch_alter_table("username_history", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_username_history_user_id"), ["user_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_username_history_username"), ["username"], unique=False
        )

    op.create_table(
        "invitations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("note", sa.String(length=128), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("expires_at", UtcDateTime(), nullable=False),
        sa.Column("used_at", UtcDateTime(), nullable=True),
        sa.Column("used_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_invitations_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["used_by"],
            ["users.id"],
            name=op.f("fk_invitations_used_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invitations")),
    )
    with op.batch_alter_table("invitations", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_invitations_token_hash"), ["token_hash"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("invitations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_invitations_token_hash"))
    op.drop_table("invitations")

    with op.batch_alter_table("username_history", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_username_history_username"))
        batch_op.drop_index(batch_op.f("ix_username_history_user_id"))
    op.drop_table("username_history")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("uq_users_email"), type_="unique")
        batch_op.drop_constraint(batch_op.f("fk_users_banned_by_users"), type_="foreignkey")
        batch_op.drop_column("ban_reason")
        batch_op.drop_column("banned_by")
        batch_op.drop_column("banned_at")
        batch_op.drop_column("avatar_updated_at")
        batch_op.drop_column("language")
