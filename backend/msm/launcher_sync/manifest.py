"""Lecture du manifest publié par le serveur de fichiers d'un launcher.

Le manifest est une **entrée non fiable** : il vient d'un autre serveur, et
chacun de ses chemins finira par désigner un fichier écrit sur le disque de la
machine. Il est donc validé entrée par entrée, et refusé en bloc à la moindre
anomalie — un manifest à moitié accepté produirait une synchronisation à moitié
juste, et une synchronisation à moitié juste supprime des mods.

Format (protocole 1) — compatible tel quel avec le launcher FrankuMC ::

    {
      "packVersion": "2026-09-23",
      "mcVersion": "1.21.1",
      "neoforgeVersion": "21.1.248",
      "files":         [{"path": "mods/jei.jar", "sha256": "…", "size": 1430522}],
      "disabledFiles": [{"path": "mods/opti.jar", "sha256": "…", "size": 2100}]
    }

``disabledFiles`` et le champ ``side`` d'une entrée sont des extensions
facultatives. Les fichiers désactivés **font toujours partie du modpack** :
c'est l'état que MSM a lui-même poussé, et les ignorer ferait supprimer du
serveur, au cycle suivant, les mods que l'on venait de désactiver.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from msm.exceptions import ValidationError
from msm.i18n import tr

#: Taille maximale d'un fichier annoncé : un JAR de mod dépasse rarement 100 Mo.
MAX_FILE_BYTES = 1024 * 1024 * 1024
#: Un modpack de plusieurs milliers de fichiers est plausible ; cent mille ne l'est pas.
MAX_ENTRIES = 100_000
MAX_PATH_LENGTH = 512

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")

#: Côtés reconnus. `*` est l'écriture de Fabric pour « les deux ».
SIDE_ALIASES: dict[str, str] = {
    "client": "client",
    "server": "server",
    "both": "both",
    "*": "both",
}


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """Un fichier du modpack."""

    path: str
    sha256: str
    size: int
    #: Côté déclaré par le serveur de fichiers, s'il le fait.
    side: str | None = None
    #: Listé dans `disabledFiles` : désactivé chez les joueurs.
    disabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
            "side": self.side,
            "disabled": self.disabled,
        }


@dataclass(frozen=True, slots=True)
class Manifest:
    """Manifest validé."""

    pack_version: str | None
    mc_version: str | None
    loader: str | None
    entries: tuple[ManifestEntry, ...]

    def under(self, prefixes: tuple[str, ...]) -> list[ManifestEntry]:
        """Entrées situées sous l'un des dossiers synchronisés."""
        return [entry for entry in self.entries if entry.path.startswith(prefixes)]


def _refuse(cause: str) -> ValidationError:
    return ValidationError(
        tr("Manifest refused."),
        cause=cause,
        remediation=tr(
            "Regenerate the manifest on the file server, then synchronise again. Nothing "
            "was changed on the Minecraft server."
        ),
    )


def validate_path(raw: Any) -> str:
    """Valide un chemin relatif venu du manifest.

    Refusé plutôt que corrigé : un chemin qui contient `..` ou un antislash n'est
    pas une coquille à réparer, c'est un manifest qu'on ne doit pas appliquer.
    """
    if not isinstance(raw, str) or not raw:
        raise _refuse(tr("An entry has no path."))
    if len(raw) > MAX_PATH_LENGTH:
        raise _refuse(tr("Path too long ({length} characters).", length=len(raw)))
    if "\x00" in raw or "\\" in raw:
        raise _refuse(tr("Forbidden character in the path “{path}”.", path=raw[:80]))
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise _refuse(tr("Absolute path refused: “{path}”.", path=raw[:80]))

    parts = raw.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise _refuse(tr("Non-normalised or climbing path: “{path}”.", path=raw[:80]))
    return raw


def _entry(raw: Any, *, disabled: bool) -> ManifestEntry:
    if not isinstance(raw, dict):
        raise _refuse(tr("An entry of `files` is not an object."))

    path = validate_path(raw.get("path"))

    sha = raw.get("sha256")
    if not isinstance(sha, str) or not _SHA256_RE.match(sha):
        raise _refuse(tr("Invalid SHA-256 checksum for “{path}”.", path=path))

    size = raw.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= MAX_FILE_BYTES:
        raise _refuse(tr("Invalid size for “{path}”.", path=path))

    side_raw = raw.get("side")
    side: str | None = None
    if side_raw is not None:
        side = SIDE_ALIASES.get(str(side_raw).lower())
        if side is None:
            raise _refuse(tr("Unknown side “{side}” for “{path}”.", side=side_raw, path=path))

    return ManifestEntry(path=path, sha256=sha.lower(), size=size, side=side, disabled=disabled)


def parse_manifest(raw: Any) -> Manifest:
    """Valide un manifest décodé. Lève :class:`ValidationError` au moindre écart."""
    if not isinstance(raw, dict):
        raise _refuse(tr("The manifest is not a JSON object."))

    files = raw.get("files")
    if not isinstance(files, list):
        raise _refuse(tr("The `files` field is missing or is not a list."))
    disabled = raw.get("disabledFiles", [])
    if not isinstance(disabled, list):
        raise _refuse(tr("The `disabledFiles` field is not a list."))
    if len(files) + len(disabled) > MAX_ENTRIES:
        raise _refuse(tr("More than {maximum} files listed.", maximum=MAX_ENTRIES))

    entries = [_entry(item, disabled=False) for item in files]
    entries += [_entry(item, disabled=True) for item in disabled]

    seen: set[str] = set()
    for entry in entries:
        # Comparaison insensible à la casse : sous Windows, `Mods/A.jar` et
        # `mods/a.jar` désignent le même fichier.
        key = entry.path.casefold()
        if key in seen:
            raise _refuse(tr("Path listed twice: “{path}”.", path=entry.path))
        seen.add(key)

    loader = None
    for field, label in (
        ("neoforgeVersion", "neoforge"),
        ("forgeVersion", "forge"),
        ("fabricVersion", "fabric"),
        ("quiltVersion", "quilt"),
    ):
        if raw.get(field):
            loader = f"{label} {raw[field]}"
            break

    return Manifest(
        pack_version=str(raw["packVersion"]) if raw.get("packVersion") else None,
        mc_version=str(raw["mcVersion"]) if raw.get("mcVersion") else None,
        loader=loader,
        entries=tuple(entries),
    )
