"""Un mod va-t-il côté client, côté serveur, ou des deux ?

C'est la question dont dépend la survie du serveur : installer un mod
client-only — minimap, shaders, rendu — sur un serveur dédié le fait planter au
démarrage (`invalid dist DEDICATED_SERVER`). Le manifest ne le dit pas toujours,
alors on regarde dans le JAR.

La détection n'est **fiable que pour Fabric et Quilt**, qui déclarent leur
environnement. Forge et NeoForge n'ont pas de champ équivalent : on s'appuie sur
un indice répandu — la dépendance à Minecraft déclarée `side = "CLIENT"` — et,
faute d'indice, on répond « les deux ». Se tromper dans ce sens installe un mod
de trop, que l'administrateur corrige d'un clic ; se tromper dans l'autre
priverait le serveur d'un mod nécessaire, sans qu'aucun message ne le dise.
"""

from __future__ import annotations

import json
import tomllib
import zipfile
from pathlib import Path
from typing import Any

#: Aucun fichier de métadonnées ne pèse plus : au-delà, c'est un piège.
_MAX_METADATA_BYTES = 1024 * 1024

CLIENT = "client"
SERVER = "server"
BOTH = "both"


def _read(archive: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > _MAX_METADATA_BYTES:
        return None
    return archive.read(info)


def _from_fabric(data: bytes) -> str | None:
    try:
        meta = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    environment = str(meta.get("environment", "*")).lower() if isinstance(meta, dict) else "*"
    return {"client": CLIENT, "server": SERVER}.get(environment, BOTH)


def _from_quilt(data: bytes) -> str | None:
    try:
        meta = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    minecraft = meta.get("minecraft", {}) if isinstance(meta, dict) else {}
    environment = (
        str(minecraft.get("environment", "*")).lower() if isinstance(minecraft, dict) else "*"
    )
    return {"client": CLIENT, "dedicated_server": SERVER}.get(environment, BOTH)


def _from_forge(data: bytes) -> str | None:
    """Indice Forge / NeoForge : le côté déclaré pour la dépendance à Minecraft."""
    try:
        meta: dict[str, Any] = tomllib.loads(data.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return None

    # Champ explicite des versions récentes de Forge.
    if meta.get("clientSideOnly") is True:
        return CLIENT

    dependencies = meta.get("dependencies", {})
    if not isinstance(dependencies, dict):
        return BOTH

    sides: set[str] = set()
    for entries in dependencies.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("modId") in ("minecraft", "neoforge", "forge"):
                sides.add(str(entry.get("side", "BOTH")).upper())

    if sides == {"CLIENT"}:
        return CLIENT
    if sides == {"SERVER"}:
        return SERVER
    return BOTH


def detect_side(path: Path) -> str:
    """Côté d'un fichier de mod. « both » quand on ne peut pas savoir.

    Le nom du fichier n'est pas regardé : un fichier préparé porte son empreinte
    pour nom. C'est à l'appelant de ne soumettre que des `.jar`.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            for name, reader in (
                ("fabric.mod.json", _from_fabric),
                ("quilt.mod.json", _from_quilt),
                ("META-INF/neoforge.mods.toml", _from_forge),
                ("META-INF/mods.toml", _from_forge),
            ):
                data = _read(archive, name)
                if data is not None:
                    side = reader(data)
                    if side is not None:
                        return side
    except (zipfile.BadZipFile, OSError):
        return BOTH
    return BOTH
