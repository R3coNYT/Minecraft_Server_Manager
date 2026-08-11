"""Persistance des joueurs connus et du cache de skins."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from msm.db.models.misc import Player, SkinCache


class PlayerRepository:
    """Joueurs vus par un serveur, conservés même hors ligne."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, server_id: int, username: str) -> Player | None:
        """Recherche par pseudo, insensible à la casse.

        Minecraft conserve la casse choisie par le joueur mais ne la distingue
        pas : « Flavien » et « flavien » sont le même compte.
        """
        statement = select(Player).where(
            Player.server_id == server_id, Player.username.ilike(username)
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_for_server(self, server_id: int, *, limit: int = 500) -> list[Player]:
        statement = (
            select(Player)
            .where(Player.server_id == server_id)
            .order_by(Player.last_seen.desc().nullslast(), Player.username)
            .limit(limit)
        )
        return list((await self._session.execute(statement)).scalars())

    async def record_join(self, server_id: int, username: str, uuid: str | None = None) -> Player:
        """Enregistre une connexion : création au besoin, compteurs mis à jour."""
        now = datetime.now(UTC)
        player = await self.get(server_id, username)

        if player is None:
            player = Player(
                server_id=server_id,
                username=username,
                uuid=uuid,
                first_seen=now,
                last_seen=now,
                total_sessions=1,
            )
            self._session.add(player)
        else:
            player.last_seen = now
            player.total_sessions += 1
            # La casse peut avoir changé côté Mojang ; on garde la plus récente.
            player.username = username
            if uuid and not player.uuid:
                player.uuid = uuid

        await self._session.flush()
        return player

    async def record_leave(self, server_id: int, username: str) -> Player | None:
        player = await self.get(server_id, username)
        if player is not None:
            player.last_seen = datetime.now(UTC)
            await self._session.flush()
        return player

    async def sync_statuses(
        self,
        server_id: int,
        *,
        ops: set[str],
        banned: set[str],
        whitelisted: set[str],
    ) -> None:
        """Reporte en base les statuts lus dans les fichiers du serveur.

        Les fichiers font autorité : ils peuvent avoir été édités à la main,
        serveur arrêté. La base n'en est qu'un reflet, pratique pour filtrer et
        afficher sans relire le disque à chaque requête.
        """
        for player in await self.list_for_server(server_id, limit=5000):
            key = player.username.casefold()
            player.is_op = key in ops
            player.is_banned = key in banned
            player.is_whitelisted = key in whitelisted
        await self._session.flush()


class SkinRepository:
    """Cache des skins, indexé par UUID."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, uuid: str) -> SkinCache | None:
        return await self._session.get(SkinCache, uuid.lower())

    async def upsert(
        self,
        uuid: str,
        *,
        username: str | None = None,
        texture_url: str | None = None,
        avatar_path: str | None = None,
        not_found: bool = False,
    ) -> SkinCache:
        key = uuid.lower()
        record = await self._session.get(SkinCache, key)
        if record is None:
            record = SkinCache(uuid=key)
            self._session.add(record)

        record.username = username or record.username
        record.texture_url = texture_url
        record.avatar_path = avatar_path
        record.not_found = not_found
        record.fetched_at = datetime.now(UTC)

        await self._session.flush()
        return record
