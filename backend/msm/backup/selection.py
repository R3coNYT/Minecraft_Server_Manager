"""Ce qu'une sauvegarde contient — et ce qu'elle décrit sans le contenir.

Une sauvegarde MSM emporte **les mondes et les configurations**, pas les mods ni
les JAR. Un dossier moddé pèse plusieurs gigaoctets dont l'essentiel est
re-téléchargeable ; les mondes, eux, sont irremplaçables. Sauvegarder le tout
rendrait l'opération assez lente et volumineuse pour qu'on cesse de la faire —
et une sauvegarde qu'on ne fait pas ne protège de rien.

Ce qui n'est pas emporté est **inventorié** : l'archive contient la liste des
mods et plugins installés, avec leur taille et leur état (actif ou désactivé).
En cas de perte du serveur, cette liste dit exactement quoi réinstaller.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from msm.minecraft.capabilities import world_directories

#: Nom du manifeste structuré, à la racine de l'archive.
MANIFEST_NAME = "msm-manifest.json"
#: Inventaire lisible sans outil, à la racine de l'archive.
INVENTORY_NAME = "mods-et-plugins.txt"

#: Fichiers de configuration à la racine du serveur, emportés s'ils existent.
ROOT_FILES: tuple[str, ...] = (
    "server.properties",
    "eula.txt",
    "ops.json",
    "banned-players.json",
    "banned-ips.json",
    "whitelist.json",
    "usercache.json",
    "server-icon.png",
    # Forks Bukkit : leurs réglages vivent à la racine, pas dans config/.
    "bukkit.yml",
    "spigot.yml",
    "paper.yml",
    "paper-global.yml",
    "paper-world-defaults.yml",
    "purpur.yml",
    "commands.yml",
    "permissions.yml",
    "help.yml",
)

#: Dossiers de configuration emportés intégralement.
CONFIG_DIRS: tuple[str, ...] = ("config", "defaultconfigs", "world_config")

#: Fichiers ignorés partout : verrous et journaux n'ont aucune valeur restaurée.
EXCLUDED_NAMES: frozenset[str] = frozenset({"session.lock", "level.dat_old", ".DS_Store"})
EXCLUDED_SUFFIXES: tuple[str, ...] = (".log", ".log.gz", ".tmp", ".part")

#: Extensions considérées comme des greffons dans mods/ et plugins/.
PLUGIN_SUFFIXES: tuple[str, ...] = (".jar", ".jar.disabled")


@dataclass(frozen=True, slots=True)
class ArchiveEntry:
    """Un fichier à écrire dans l'archive."""

    source: Path
    #: Chemin à l'intérieur de l'archive, toujours en séparateurs POSIX.
    arcname: str
    size: int


@dataclass(frozen=True, slots=True)
class InstalledFile:
    """Un mod ou un plugin présent sur le serveur, non emporté mais inventorié."""

    name: str
    size_bytes: int
    enabled: bool

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "size_bytes": self.size_bytes, "enabled": self.enabled}


@dataclass(frozen=True, slots=True)
class Selection:
    """Contenu retenu pour une sauvegarde."""

    entries: tuple[ArchiveEntry, ...]
    worlds: tuple[str, ...]
    mods: tuple[InstalledFile, ...]
    plugins: tuple[InstalledFile, ...]

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.entries)

    @property
    def file_count(self) -> int:
        return len(self.entries)


def _is_excluded(path: Path) -> bool:
    if path.name in EXCLUDED_NAMES:
        return True
    return path.name.endswith(EXCLUDED_SUFFIXES)


def _walk(root: Path, base: Path) -> list[ArchiveEntry]:
    """Liste récursivement les fichiers de ``root``, relatifs à ``base``.

    Les liens symboliques ne sont pas suivis : un lien vers ``/`` transformerait
    la sauvegarde d'un monde en copie du système de fichiers entier.
    """
    entries: list[ArchiveEntry] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            children = sorted(current.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_symlink() or _is_excluded(child):
                continue
            if child.is_dir():
                stack.append(child)
                continue
            if not child.is_file():
                continue
            try:
                size = child.stat().st_size
            except OSError:
                continue
            entries.append(
                ArchiveEntry(
                    source=child,
                    arcname=child.relative_to(base).as_posix(),
                    size=size,
                )
            )
    return entries


def _installed(directory: Path) -> tuple[InstalledFile, ...]:
    """Inventaire des greffons d'un dossier (mods/ ou plugins/)."""
    if not directory.is_dir():
        return ()

    found: list[InstalledFile] = []
    try:
        children = sorted(directory.iterdir())
    except OSError:
        return ()

    for child in children:
        if not child.is_file() or not child.name.endswith(PLUGIN_SUFFIXES):
            continue
        try:
            size = child.stat().st_size
        except OSError:
            size = 0
        enabled = not child.name.endswith(".disabled")
        name = child.name[: -len(".disabled")] if not enabled else child.name
        found.append(InstalledFile(name=name, size_bytes=size, enabled=enabled))
    return tuple(found)


def _plugin_data_dirs(directory: Path) -> list[Path]:
    """Sous-dossiers de ``plugins/`` : les réglages d'un plugin y vivent.

    Les JAR eux-mêmes restent dehors ; leurs configurations, non — les
    reconstituer à la main après une perte serait le vrai travail.
    """
    plugins = directory / "plugins"
    if not plugins.is_dir():
        return []
    try:
        return sorted(child for child in plugins.iterdir() if child.is_dir())
    except OSError:
        return []


def select_content(directory: Path) -> Selection:
    """Détermine ce qui part dans l'archive pour ce dossier de serveur."""
    entries: list[ArchiveEntry] = []
    worlds: list[str] = []

    for world in world_directories(directory):
        worlds.append(world.name)
        entries.extend(_walk(world, directory))

    for name in ROOT_FILES:
        candidate = directory / name
        if candidate.is_file() and not candidate.is_symlink():
            entries.append(
                ArchiveEntry(
                    source=candidate,
                    arcname=name,
                    size=candidate.stat().st_size,
                )
            )

    for name in CONFIG_DIRS:
        candidate = directory / name
        if candidate.is_dir() and not candidate.is_symlink():
            entries.extend(_walk(candidate, directory))

    for plugin_dir in _plugin_data_dirs(directory):
        entries.extend(_walk(plugin_dir, directory))

    # Tri par chemin : une archive reproductible se compare et se lit mieux.
    entries.sort(key=lambda entry: entry.arcname)

    return Selection(
        entries=tuple(entries),
        worlds=tuple(sorted(worlds)),
        mods=_installed(directory / "mods"),
        plugins=_installed(directory / "plugins"),
    )


def build_manifest(
    selection: Selection,
    *,
    server_name: str,
    server_type: str,
    minecraft_version: str | None,
    msm_version: str,
    created_at: datetime | None = None,
) -> dict[str, bytes]:
    """Construit les fichiers descriptifs ajoutés à la racine de l'archive.

    Deux formats pour deux usages : le JSON est relu par MSM avant une
    restauration, le texte est lisible par un humain qui a perdu son serveur et
    n'a plus que l'archive sous la main.
    """
    stamp = created_at or datetime.now(UTC)
    manifest = {
        "format": 1,
        "msm_version": msm_version,
        "created_at": stamp.isoformat(),
        "server": {
            "name": server_name,
            "type": server_type,
            "minecraft_version": minecraft_version,
        },
        "content": {
            "worlds": list(selection.worlds),
            "file_count": selection.file_count,
            "total_bytes": selection.total_bytes,
        },
        "mods": [item.to_dict() for item in selection.mods],
        "plugins": [item.to_dict() for item in selection.plugins],
    }

    lines = [
        f"Serveur : {server_name} ({server_type}"
        + (f", {minecraft_version}" if minecraft_version else "")
        + ")",
        f"Sauvegarde du {stamp.strftime('%d/%m/%Y à %H:%M UTC')}",
        "",
        "Cette archive contient les mondes et les configurations, pas les mods",
        "ni les plugins. Voici la liste de ceux qui étaient installés, à",
        "réinstaller manuellement en cas de reconstruction du serveur.",
        "",
    ]
    for title, items in (("MODS", selection.mods), ("PLUGINS", selection.plugins)):
        lines.append(f"--- {title} ({len(items)}) ---")
        if not items:
            lines.append("(aucun)")
        for item in items:
            suffix = "" if item.enabled else "   [désactivé]"
            lines.append(f"{item.name}   {item.size_bytes / 1_048_576:.1f} Mo{suffix}")
        lines.append("")

    return {
        MANIFEST_NAME: json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        INVENTORY_NAME: "\n".join(lines).encode("utf-8"),
    }
