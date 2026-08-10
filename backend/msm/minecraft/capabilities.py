"""Détection des capacités réelles d'un dossier de serveur.

Principe directeur : **rien n'est supposé**. Un serveur Vanilla dans lequel
quelqu'un a créé un dossier ``mods/`` verra l'onglet Mods ; un Forge sans
``config/`` ne verra pas l'onglet Configurations. L'interface reflète le contenu
du disque, pas une famille de serveur déclarée.
"""

from __future__ import annotations

from pathlib import Path

from msm.minecraft.types import BASE_CAPABILITIES, Capability

#: Dossier ou fichier à chercher pour chaque capacité conditionnelle.
_MARKERS: tuple[tuple[Capability, str, bool], ...] = (
    # (capacité, chemin relatif, est-ce un dossier ?)
    (Capability.MODS, "mods", True),
    (Capability.PLUGINS, "plugins", True),
    (Capability.CONFIGS, "config", True),
    (Capability.PROPERTIES, "server.properties", False),
)


def detect_capabilities(directory: Path) -> frozenset[Capability]:
    """Capacités disponibles pour ce dossier de serveur."""
    capabilities = set(BASE_CAPABILITIES)

    if not directory.is_dir():
        return frozenset(capabilities)

    for capability, relative, is_directory in _MARKERS:
        target = directory / relative
        if target.is_dir() if is_directory else target.is_file():
            capabilities.add(capability)

    if _find_worlds(directory):
        capabilities.add(Capability.WORLDS)
    if _find_datapacks(directory):
        capabilities.add(Capability.DATAPACKS)

    return frozenset(capabilities)


def _find_worlds(directory: Path) -> list[Path]:
    """Un monde est un dossier contenant ``level.dat``."""
    worlds: list[Path] = []
    try:
        for child in directory.iterdir():
            if child.is_dir() and (child / "level.dat").is_file():
                worlds.append(child)
    except OSError:
        return []
    return worlds


def _find_datapacks(directory: Path) -> list[Path]:
    """Les datapacks vivent dans ``<monde>/datapacks``."""
    return [
        world / "datapacks" for world in _find_worlds(directory) if (world / "datapacks").is_dir()
    ]


def world_directories(directory: Path) -> list[Path]:
    """Mondes présents dans le dossier du serveur."""
    return sorted(_find_worlds(directory))


def datapack_directories(directory: Path) -> list[Path]:
    """Dossiers de datapacks détectés."""
    return sorted(_find_datapacks(directory))
