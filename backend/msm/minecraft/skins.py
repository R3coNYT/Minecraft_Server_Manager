"""Récupération et mise en cache des skins Minecraft.

Deux choix de conception méritent explication :

**Le skin est relayé par MSM, pas chargé par le navigateur.** Si chaque panneau
allait chercher les images chez Mojang, l'UUID des joueurs serait communiqué à un
tiers depuis le poste de chaque administrateur, et l'interface deviendrait
inutilisable sur un réseau isolé. MSM télécharge une fois, met en cache sur
disque, et sert l'image lui-même.

**Un échec n'est pas une erreur.** Un serveur en mode hors ligne
(``online-mode=false``) attribue des UUID qui n'existent pas chez Mojang :
l'API répond « inconnu ». Ce cas est mémorisé avec une durée de validité courte
pour ne pas réinterroger l'API à chaque affichage, et l'interface se rabat sur un
avatar par défaut.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from msm.logging_conf import get_logger

logger = get_logger(__name__)

PROFILE_URL = "https://sessionserver.mojang.com/session/minecraft/profile/{uuid}"

#: Durée de validité d'un skin connu. Un joueur change rarement d'apparence.
CACHE_TTL = timedelta(days=1)
#: Durée avant de réessayer un UUID inconnu — court, mais suffisant pour ne pas
#: marteler l'API quand une liste de joueurs se rafraîchit en continu.
NOT_FOUND_TTL = timedelta(hours=6)

REQUEST_TIMEOUT_S = 5.0
#: Un skin Minecraft fait quelques kilo-octets ; au-delà, ce n'est pas un skin.
MAX_SKIN_BYTES = 512 * 1024


@dataclass(frozen=True, slots=True)
class SkinResult:
    """Résultat d'une résolution de skin."""

    uuid: str
    texture_url: str | None
    image: bytes | None
    not_found: bool = False

    @property
    def available(self) -> bool:
        return self.image is not None


def is_expired(fetched_at: datetime | None, *, not_found: bool) -> bool:
    """Le cache doit-il être rafraîchi ?"""
    if fetched_at is None:
        return True
    ttl = NOT_FOUND_TTL if not_found else CACHE_TTL
    return datetime.now(UTC) - fetched_at > ttl


class SkinClient:
    """Client de l'API de profils Mojang."""

    def __init__(self, cache_dir: Path, *, timeout_s: float = REQUEST_TIMEOUT_S) -> None:
        self._cache_dir = cache_dir
        self._timeout = timeout_s

    def skin_path(self, uuid: str) -> Path:
        return self._cache_dir / f"{uuid.replace('-', '')}.png"

    def cached_image(self, uuid: str) -> bytes | None:
        """Image déjà sur disque, ou ``None``."""
        path = self.skin_path(uuid)
        if not path.is_file():
            return None
        try:
            return path.read_bytes()
        except OSError:  # pragma: no cover - disque en erreur
            return None

    async def fetch(self, uuid: str) -> SkinResult:
        """Interroge Mojang et met le skin en cache. Ne lève jamais."""
        normalized = uuid.replace("-", "")
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(PROFILE_URL.format(uuid=normalized))

                # 204 (mode hors ligne) et 404 signifient « ce profil n'existe pas ».
                if response.status_code in (204, 404):
                    return SkinResult(uuid=uuid, texture_url=None, image=None, not_found=True)
                response.raise_for_status()

                texture_url = _extract_texture_url(response.json())
                if texture_url is None:
                    return SkinResult(uuid=uuid, texture_url=None, image=None, not_found=True)

                image_response = await client.get(texture_url)
                image_response.raise_for_status()
                image = image_response.content

        except (httpx.HTTPError, ValueError, KeyError) as exc:
            # Panne réseau ou réponse inattendue : on ne marque pas « inconnu »,
            # l'information sera peut-être disponible au prochain essai.
            logger.info("skin_fetch_failed", uuid=uuid, error=str(exc))
            return SkinResult(uuid=uuid, texture_url=None, image=None, not_found=False)

        if len(image) > MAX_SKIN_BYTES:
            logger.warning("skin_too_large", uuid=uuid, size=len(image))
            return SkinResult(uuid=uuid, texture_url=texture_url, image=None, not_found=False)

        self._store(uuid, image)
        return SkinResult(uuid=uuid, texture_url=texture_url, image=image)

    def _store(self, uuid: str, image: bytes) -> None:
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            self.skin_path(uuid).write_bytes(image)
        except OSError as exc:  # pragma: no cover - disque en erreur
            logger.warning("skin_cache_write_failed", uuid=uuid, error=str(exc))


def _extract_texture_url(profile: object) -> str | None:
    """Extrait l'URL du skin du profil Mojang.

    Le profil encapsule ses textures dans une propriété encodée en base64 ;
    l'API ne les expose pas directement.
    """
    if not isinstance(profile, dict):
        return None

    for prop in profile.get("properties", []):
        if not isinstance(prop, dict) or prop.get("name") != "textures":
            continue
        try:
            decoded = json.loads(base64.b64decode(str(prop.get("value", ""))))
        except (ValueError, TypeError):
            return None
        skin = decoded.get("textures", {}).get("SKIN", {}) if isinstance(decoded, dict) else {}
        url = skin.get("url") if isinstance(skin, dict) else None
        # Seules les URL officielles sont suivies : le profil est fourni par un
        # tiers, il ne doit pas pouvoir faire télécharger n'importe quoi au serveur.
        if isinstance(url, str) and url.startswith("https://textures.minecraft.net/"):
            return url
    return None
