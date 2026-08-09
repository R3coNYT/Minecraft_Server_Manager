"""Reconnaissance d'événements dans les logs Minecraft.

Ces motifs sont volontairement regroupés ici, hors du runtime : ils changent d'une
version de Minecraft à l'autre et d'un fork à l'autre. Les faire évoluer ne doit
jamais imposer de toucher au gestionnaire de processus.

Le principe est celui du **détecteur** : chaque fonction reçoit une ligne déjà
analysée et renvoie soit ``None``, soit un événement typé.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from msm.core.log_line import LogLine

# --------------------------------------------------------------------------- #
#  Démarrage terminé
# --------------------------------------------------------------------------- #
#: « Done (12.345s)! For help, type "help" » — commun à toutes les versions.
DONE_RE = re.compile(r"Done \([\d.,]+s\)!\s*For help, type", re.IGNORECASE)
#: Variante courte de certains forks.
DONE_FALLBACK_RE = re.compile(r"^Done \([\d.,]+s\)!", re.IGNORECASE)

# --------------------------------------------------------------------------- #
#  Arrêt
# --------------------------------------------------------------------------- #
STOPPING_RE = re.compile(r"^Stopping (the )?server", re.IGNORECASE)

# --------------------------------------------------------------------------- #
#  Joueurs
# --------------------------------------------------------------------------- #
#: Un pseudo Minecraft : 3 à 16 caractères alphanumériques ou `_`.
USERNAME = r"[A-Za-z0-9_]{1,16}"

JOIN_RE = re.compile(rf"^(?P<name>{USERNAME}) joined the game$")
LEAVE_RE = re.compile(rf"^(?P<name>{USERNAME}) left the game$")
#: « UUID of player Flavien is 069a79f4-… » — seule source fiable d'UUID hors fichiers.
UUID_RE = re.compile(
    rf"^UUID of player (?P<name>{USERNAME}) is "
    r"(?P<uuid>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)
#: « Flavien[/127.0.0.1:52344] logged in with entity id 42 at (…) »
LOGGED_IN_RE = re.compile(rf"^(?P<name>{USERNAME})\[/(?P<addr>[^\]]+)\] logged in")
#: Réponse à la commande `list`. Deux formulations coexistent selon les versions :
#: « There are 2 of a max of 20 players online: … » et « There are 2/20 players online: … ».
LIST_RE = re.compile(
    r"There are (?P<online>\d+)"
    r"(?:\s*/\s*|\s+of\s+a\s+max(?:\s+of)?\s+)"
    r"(?P<max>\d+)\s+players?\s+online:?\s*"
    r"(?P<names>.*)$"
)

# --------------------------------------------------------------------------- #
#  Erreurs fatales
# --------------------------------------------------------------------------- #
FATAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Failed to bind to port", re.IGNORECASE),
    re.compile(r"\*\*\*\* FAILED TO BIND TO PORT", re.IGNORECASE),
    re.compile(r"You need to agree to the EULA", re.IGNORECASE),
    re.compile(r"Encountered an unexpected exception", re.IGNORECASE),
    re.compile(r"A fatal error has been detected by the Java Runtime", re.IGNORECASE),
    re.compile(r"Could not reserve enough space for .* object heap", re.IGNORECASE),
    re.compile(r"Error: Unable to access jarfile", re.IGNORECASE),
    re.compile(r"Unsupported class file major version", re.IGNORECASE),
    re.compile(r"has been compiled by a more recent version of the Java Runtime", re.IGNORECASE),
)

#: Diagnostics enrichis : motif → (cause lisible, action corrective).
FATAL_DIAGNOSTICS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"FAILED TO BIND TO PORT|Failed to bind to port", re.IGNORECASE),
        "Le port du serveur est déjà utilisé par un autre processus.",
        "Changer `server-port` dans server.properties, ou arrêter le processus qui occupe ce port.",
    ),
    (
        re.compile(r"You need to agree to the EULA", re.IGNORECASE),
        "Le CLUF (EULA) de Minecraft n'a pas été accepté.",
        "Activer « Accepter l'EULA automatiquement » dans les réglages du serveur, "
        "ou passer `eula=true` dans eula.txt.",
    ),
    (
        re.compile(r"Could not reserve enough space for .* object heap", re.IGNORECASE),
        "La mémoire maximale demandée dépasse la RAM disponible sur la machine.",
        "Réduire la mémoire maximale dans les réglages du serveur.",
    ),
    (
        re.compile(r"Error: Unable to access jarfile", re.IGNORECASE),
        "Le fichier JAR indiqué est introuvable ou illisible.",
        "Vérifier le chemin du JAR dans les réglages du serveur.",
    ),
    (
        re.compile(
            r"Unsupported class file major version"
            r"|has been compiled by a more recent version of the Java Runtime",
            re.IGNORECASE,
        ),
        "La version de Java installée est incompatible avec ce serveur.",
        "Installer la version de Java requise et renseigner son chemin dans les réglages.",
    ),
)


class MinecraftEventKind(str, Enum):
    """Nature d'un événement détecté dans les logs."""

    SERVER_READY = "server_ready"
    SERVER_STOPPING = "server_stopping"
    PLAYER_JOIN = "player_join"
    PLAYER_LEAVE = "player_leave"
    PLAYER_UUID = "player_uuid"
    PLAYER_ADDRESS = "player_address"
    PLAYER_LIST = "player_list"
    FATAL = "fatal"


@dataclass(frozen=True, slots=True)
class MinecraftEvent:
    """Événement extrait d'une ligne de log."""

    kind: MinecraftEventKind
    line: LogLine
    username: str | None = None
    uuid: str | None = None
    address: str | None = None
    players: tuple[str, ...] = ()
    online: int | None = None
    max_players: int | None = None
    cause: str | None = None
    remediation: str | None = None


def detect_events(line: LogLine) -> list[MinecraftEvent]:
    """Extrait tous les événements portés par une ligne.

    Une même ligne peut en produire plusieurs (rare mais possible) ; la liste vide
    est le cas normal et doit rester peu coûteuse.
    """
    text = line.text.strip()
    if not text:
        return []

    events: list[MinecraftEvent] = []

    if DONE_RE.search(text) or DONE_FALLBACK_RE.match(text):
        events.append(MinecraftEvent(MinecraftEventKind.SERVER_READY, line))
    elif STOPPING_RE.match(text):
        events.append(MinecraftEvent(MinecraftEventKind.SERVER_STOPPING, line))

    if (match := JOIN_RE.match(text)) is not None:
        events.append(MinecraftEvent(MinecraftEventKind.PLAYER_JOIN, line, username=match["name"]))
    elif (match := LEAVE_RE.match(text)) is not None:
        events.append(MinecraftEvent(MinecraftEventKind.PLAYER_LEAVE, line, username=match["name"]))
    elif (match := UUID_RE.match(text)) is not None:
        events.append(
            MinecraftEvent(
                MinecraftEventKind.PLAYER_UUID,
                line,
                username=match["name"],
                uuid=match["uuid"].lower(),
            )
        )
    elif (match := LOGGED_IN_RE.match(text)) is not None:
        events.append(
            MinecraftEvent(
                MinecraftEventKind.PLAYER_ADDRESS,
                line,
                username=match["name"],
                address=match["addr"],
            )
        )
    elif (match := LIST_RE.search(text)) is not None:
        raw_names = match["names"].strip()
        names = tuple(n.strip() for n in raw_names.split(",") if n.strip()) if raw_names else ()
        events.append(
            MinecraftEvent(
                MinecraftEventKind.PLAYER_LIST,
                line,
                players=names,
                online=int(match["online"]),
                max_players=int(match["max"]),
            )
        )

    if (diagnostic := diagnose_fatal(text)) is not None:
        cause, remediation = diagnostic
        events.append(
            MinecraftEvent(MinecraftEventKind.FATAL, line, cause=cause, remediation=remediation)
        )

    return events


def diagnose_fatal(text: str) -> tuple[str, str] | None:
    """Renvoie ``(cause, action)`` si la ligne dénote une erreur fatale connue."""
    for pattern, cause, remediation in FATAL_DIAGNOSTICS:
        if pattern.search(text):
            return cause, remediation
    for pattern in FATAL_PATTERNS:
        if pattern.search(text):
            return (
                "Le serveur a signalé une erreur fatale.",
                "Consulter les dernières lignes de la console pour le détail.",
            )
    return None
