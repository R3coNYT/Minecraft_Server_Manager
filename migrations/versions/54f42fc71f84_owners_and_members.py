"""Server owners and members.

Ouverture au public, étape 1. Chaque serveur a désormais un propriétaire
(`servers.owner_id`) et se partage via `server_members`. L'ancien réglage fin des
droits par serveur (`server_permissions`) disparaît.

- Les serveurs existants reviennent au premier admin ; leurs dossiers ne bougent pas.
- Le nom d'un serveur devient unique par propriétaire, et non plus sur tout MSM.
- Chaque compte reçoit un identifiant de stockage immuable (nom de son dossier).
- Le rôle Viewer disparaît au profit de User ; Admin et Moderator restent.

Revision ID: 54f42fc71f84
Revises: f414e4b69a05
Create Date: 2026-09-24 15:40:00.000000
"""

from __future__ import annotations

import secrets
import string
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from msm.db.types import UtcDateTime

revision: str = "54f42fc71f84"
down_revision: str | None = "f414e4b69a05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALPHABET = string.ascii_lowercase + string.digits

_users = sa.table(
    "users",
    sa.column("id", sa.Integer()),
    sa.column("role", sa.String()),
    sa.column("storage_id", sa.String()),
)
_servers = sa.table("servers", sa.column("id", sa.Integer()), sa.column("owner_id", sa.Integer()))


def _new_storage_id() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(10))


def _first_owner(connection: sa.Connection) -> int | None:
    """Le premier admin, à défaut le plus ancien compte."""
    admin = connection.execute(
        sa.select(sa.func.min(_users.c.id)).where(_users.c.role == "ADMIN")
    ).scalar()
    if admin is not None:
        return int(admin)
    oldest = connection.execute(sa.select(sa.func.min(_users.c.id))).scalar()
    return int(oldest) if oldest is not None else None


def upgrade() -> None:
    connection = op.get_bind()

    # --- Comptes : identifiant de stockage, fin du rôle Viewer ------------------
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("storage_id", sa.String(length=16), nullable=True))

    taken: set[str] = set()
    for (user_id,) in connection.execute(sa.select(_users.c.id)).all():
        storage_id = _new_storage_id()
        while storage_id in taken:
            storage_id = _new_storage_id()
        taken.add(storage_id)
        connection.execute(
            _users.update().where(_users.c.id == user_id).values(storage_id=storage_id)
        )
    connection.execute(_users.update().where(_users.c.role == "VIEWER").values(role="USER"))

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("storage_id", existing_type=sa.String(length=16), nullable=False)
        batch_op.create_unique_constraint(batch_op.f("uq_users_storage_id"), ["storage_id"])

    # --- Serveurs : un propriétaire, un nom unique chez lui ---------------------
    with op.batch_alter_table("servers", schema=None) as batch_op:
        batch_op.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))

    has_servers = connection.execute(sa.select(sa.func.count(_servers.c.id))).scalar()
    if has_servers:
        owner = _first_owner(connection)
        if owner is None:
            raise RuntimeError(
                "Servers exist but no account does: create an admin account "
                "(msm createadmin) before upgrading."
            )
        connection.execute(_servers.update().values(owner_id=owner))

    with op.batch_alter_table("servers", schema=None) as batch_op:
        batch_op.alter_column("owner_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_index(batch_op.f("ix_servers_owner_id"), ["owner_id"], unique=False)
        batch_op.create_foreign_key(
            batch_op.f("fk_servers_owner_id_users"),
            "users",
            ["owner_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.drop_constraint("uq_servers_name", type_="unique")
        batch_op.create_unique_constraint(batch_op.f("uq_servers_owner_id"), ["owner_id", "name"])

    # --- Membres ----------------------------------------------------------------
    op.create_table(
        "server_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("OWNER", "ADMIN", "VIEWER", name="serverrole", native_enum=False, length=16),
            nullable=False,
        ),
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
            name=op.f("fk_server_members_server_id_servers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_server_members_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_server_members")),
        sa.UniqueConstraint("server_id", "user_id", name=op.f("uq_server_members_server_id")),
    )
    with op.batch_alter_table("server_members", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_server_members_server_id"), ["server_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_server_members_user_id"), ["user_id"], unique=False)

    # --- Fin des surcharges de droits par serveur -------------------------------
    with op.batch_alter_table("server_permissions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_server_permissions_user_id"))
        batch_op.drop_index(batch_op.f("ix_server_permissions_server_id"))
    op.drop_table("server_permissions")


def downgrade() -> None:
    op.create_table(
        "server_permissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("granted", sa.JSON(), nullable=False),
        sa.Column("revoked", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["servers.id"],
            name=op.f("fk_server_permissions_server_id_servers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_server_permissions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_server_permissions")),
        sa.UniqueConstraint("user_id", "server_id", name=op.f("uq_server_permissions_user_id")),
    )
    with op.batch_alter_table("server_permissions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_server_permissions_server_id"), ["server_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_server_permissions_user_id"), ["user_id"], unique=False
        )

    with op.batch_alter_table("server_members", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_server_members_user_id"))
        batch_op.drop_index(batch_op.f("ix_server_members_server_id"))
    op.drop_table("server_members")

    # Deux serveurs de même nom chez deux propriétaires empêcheraient de revenir
    # à un nom unique sur tout MSM : le retour arrière échoue alors, clairement.
    with op.batch_alter_table("servers", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("uq_servers_owner_id"), type_="unique")
        batch_op.create_unique_constraint("uq_servers_name", ["name"])
        batch_op.drop_constraint(batch_op.f("fk_servers_owner_id_users"), type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_servers_owner_id"))
        batch_op.drop_column("owner_id")

    op.get_bind().execute(_users.update().where(_users.c.role == "USER").values(role="VIEWER"))
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("uq_users_storage_id"), type_="unique")
        batch_op.drop_column("storage_id")
