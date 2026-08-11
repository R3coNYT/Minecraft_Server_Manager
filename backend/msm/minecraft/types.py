"""Types de serveurs Minecraft reconnus et capacités associées.

Rien n'est supposé du contenu d'un dossier : le type sert d'indication (icône,
valeurs par défaut à la création), pas de vérité. Ce sont les **capacités**
réellement détectées — présence de ``mods/``, ``plugins/``, ``config/`` — qui
déterminent les onglets affichés. Un serveur Vanilla avec un dossier ``mods/``
verra donc l'onglet Mods, et un Forge sans ``config/`` ne verra pas l'onglet
Configurations.
"""

from __future__ import annotations

from enum import Enum


class ServerType(str, Enum):
    """Famille de serveur, à titre indicatif."""

    VANILLA = "VANILLA"
    FORGE = "FORGE"
    NEOFORGE = "NEOFORGE"
    FABRIC = "FABRIC"
    QUILT = "QUILT"
    MOHIST = "MOHIST"
    PAPER = "PAPER"
    SPIGOT = "SPIGOT"
    BUKKIT = "BUKKIT"
    PURPUR = "PURPUR"
    #: Fork non identifié ou configuration entièrement manuelle.
    CUSTOM = "CUSTOM"
    UNKNOWN = "UNKNOWN"

    @property
    def label(self) -> str:
        return _LABELS.get(self, self.value.capitalize())


_LABELS: dict[ServerType, str] = {
    ServerType.VANILLA: "Vanilla",
    ServerType.FORGE: "Forge",
    ServerType.NEOFORGE: "NeoForge",
    ServerType.FABRIC: "Fabric",
    ServerType.QUILT: "Quilt",
    ServerType.MOHIST: "Mohist",
    ServerType.PAPER: "Paper",
    ServerType.SPIGOT: "Spigot",
    ServerType.BUKKIT: "Bukkit",
    ServerType.PURPUR: "Purpur",
    ServerType.CUSTOM: "Personnalisé",
    ServerType.UNKNOWN: "Inconnu",
}


class Capability(str, Enum):
    """Fonctionnalité disponible pour un serveur, déduite de son contenu réel."""

    CONSOLE = "console"
    PLAYERS = "players"
    MODS = "mods"
    PLUGINS = "plugins"
    DATAPACKS = "datapacks"
    CONFIGS = "configs"
    PROPERTIES = "properties"
    EVENTS = "events"
    WORLDS = "worlds"


#: Capacités toujours présentes, quel que soit le contenu du dossier.
BASE_CAPABILITIES: frozenset[Capability] = frozenset(
    {Capability.CONSOLE, Capability.PLAYERS, Capability.EVENTS}
)
