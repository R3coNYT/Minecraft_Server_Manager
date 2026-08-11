"""Accès aux serveurs, à leurs réglages et aux droits qui les concernent."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from msm.core.states import ServerState
from msm.db.models.server import (
    Server,
    ServerPermission,
    ServerRuntimeStateRow,
    ServerSettings,
)


class ServerRepository:
    """Requêtes sur les serveurs gérés."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _loaded(self) -> tuple[object, ...]:
        # Les réglages et l'état sont systématiquement nécessaires : les charger
        # d'emblée évite une requête par serveur à l'affichage du tableau de bord.
        return (
            selectinload(Server.settings),
            selectinload(Server.runtime_state),
        )

    async def get(self, server_id: int) -> Server | None:
        statement = (
            select(Server).where(Server.id == server_id).options(*self._loaded())  # type: ignore[arg-type]
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> Server | None:
        statement = select(Server).where(Server.slug == slug).options(*self._loaded())  # type: ignore[arg-type]
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_by_name(self, name: str) -> Server | None:
        statement = select(Server).where(Server.name.ilike(name.strip()))
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_by_directory(self, directory: str) -> Server | None:
        statement = select(Server).where(Server.directory == directory)
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_all(self, *, only_enabled: bool = False) -> list[Server]:
        statement = select(Server).options(*self._loaded())  # type: ignore[arg-type]
        if only_enabled:
            statement = statement.where(Server.enabled.is_(True))
        statement = statement.order_by(Server.sort_order, Server.name)
        return list((await self._session.execute(statement)).scalars())

    def add(self, server: Server) -> None:
        self._session.add(server)

    async def delete(self, server: Server) -> None:
        await self._session.delete(server)

    async def flush(self) -> None:
        await self._session.flush()

    # ------------------------------------------------------------------ #
    #  État persistant (réadoption après un redémarrage de MSM)
    # ------------------------------------------------------------------ #
    async def save_runtime_state(
        self,
        server_id: int,
        *,
        state: ServerState,
        pid: int | None = None,
        group_id: int | None = None,
        process_create_time: float | None = None,
        exit_code: int | None = None,
        consecutive_crashes: int = 0,
        last_error: dict[str, str] | None = None,
    ) -> ServerRuntimeStateRow:
        """Enregistre l'état courant d'un serveur.

        Le couple ``pid`` + ``process_create_time`` est ce qui permet, au prochain
        démarrage de MSM, de savoir si un processus encore vivant est bien le
        nôtre et non un PID recyclé par le système.
        """
        row = await self._session.get(ServerRuntimeStateRow, server_id)
        if row is None:
            row = ServerRuntimeStateRow(server_id=server_id)
            self._session.add(row)

        row.state = state
        row.pid = pid
        row.group_id = group_id
        row.process_create_time = process_create_time
        row.last_exit_code = exit_code
        row.consecutive_crashes = consecutive_crashes
        row.last_error = last_error

        now = datetime.now(UTC)
        if state is ServerState.STARTING:
            row.started_at = now
            row.stopped_at = None
        elif not state.is_running:
            row.stopped_at = now

        await self._session.flush()
        return row


class ServerPermissionRepository:
    """Surcharges de droits par serveur."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: int, server_id: int) -> ServerPermission | None:
        statement = select(ServerPermission).where(
            ServerPermission.user_id == user_id,
            ServerPermission.server_id == server_id,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_for_user(self, user_id: int) -> list[ServerPermission]:
        statement = select(ServerPermission).where(ServerPermission.user_id == user_id)
        return list((await self._session.execute(statement)).scalars())

    async def upsert(
        self,
        *,
        user_id: int,
        server_id: int,
        granted: list[str],
        revoked: list[str],
    ) -> ServerPermission:
        record = await self.get(user_id, server_id)
        if record is None:
            record = ServerPermission(user_id=user_id, server_id=server_id)
            self._session.add(record)
        record.granted = granted
        record.revoked = revoked
        await self._session.flush()
        return record


def build_settings(**overrides: object) -> ServerSettings:
    """Crée un jeu de réglages avec les valeurs par défaut du modèle.

    SQLAlchemy n'applique ses ``default`` qu'au moment de l'insertion ; sans cette
    initialisation explicite, les champs seraient ``None`` entre la création de
    l'objet et son enregistrement.
    """
    settings = ServerSettings(
        custom_argv=[],
        jvm_args=[],
        extra_args=[],
        env={},
        stop_command="stop",
        stop_timeout_s=60.0,
        kill_timeout_s=15.0,
        start_timeout_s=300.0,
        restart_delay_s=10.0,
        max_consecutive_crashes=3,
        autostart_on_boot=False,
        auto_accept_eula=False,
        log_history_lines=5000,
        use_pty=False,
        rcon_enabled=False,
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings
