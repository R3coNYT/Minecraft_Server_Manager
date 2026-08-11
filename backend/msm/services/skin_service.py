"""Résolution des skins, avec cache en base et sur disque.

Le service ne renvoie jamais d'erreur au client : un skin indisponible est une
information, pas une panne. L'interface affiche alors un avatar par défaut.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from msm.config import Settings
from msm.db.repositories.player_repo import SkinRepository
from msm.logging_conf import get_logger
from msm.minecraft.skins import SkinClient, is_expired

logger = get_logger(__name__)


class SkinService:
    """Fournit l'image de skin d'un joueur, en limitant les appels externes."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._repository = SkinRepository(session)
        self._cache_dir = Path(settings.data_dir) / "cache" / "skins"
        self._client = SkinClient(self._cache_dir)

    async def get_skin(self, uuid: str) -> bytes | None:
        """Image PNG du skin, ou ``None`` si elle est indisponible.

        Le cache disque est consulté en premier ; l'API externe n'est sollicitée
        que si l'entrée est absente ou périmée.
        """
        normalized = uuid.lower()
        record = await self._repository.get(normalized)

        if record is not None and not is_expired(record.fetched_at, not_found=record.not_found):
            if record.not_found:
                return None
            cached = self._client.cached_image(normalized)
            if cached is not None:
                return cached
            # Entrée en base sans fichier : le cache disque a été vidé.

        result = await self._client.fetch(normalized)
        await self._repository.upsert(
            normalized,
            texture_url=result.texture_url,
            avatar_path=str(self._client.skin_path(normalized)) if result.available else None,
            not_found=result.not_found,
        )
        return result.image
