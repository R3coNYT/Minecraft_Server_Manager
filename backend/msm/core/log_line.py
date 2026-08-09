"""Représentation et analyse d'une ligne de log de serveur Minecraft.

Trois formats couvrent l'essentiel du parc :

* moderne (1.7+, log4j)   ``[12:30:04] [Server thread/INFO]: Done (1.234s)!``
* moderne avec catégorie  ``[12:30:04] [Server thread/INFO] [minecraft/DedicatedServer]: …``
* ancien (≤ 1.6)          ``[12:30:04 INFO]: Done``

Une ligne non reconnue n'est jamais perdue : elle est conservée telle quelle avec
un niveau ``RAW``. Le champ ``raw`` garde toujours le texte d'origine, ``text``
la version nettoyée (sans codes couleur) destinée à l'affichage et à la recherche.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

#: Codes couleur ANSI (certains serveurs colorisent leur sortie).
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
#: Codes de formatage Minecraft (§a, §l, …) — le caractère peut aussi être `&`.
_MC_COLOR_RE = re.compile(r"[§\xa7][0-9a-fk-orA-FK-OR]")
#: Caractères de contrôle indésirables (on garde la tabulation).
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]")

_MODERN_RE = re.compile(
    r"^\[(?P<time>\d{2}:\d{2}:\d{2})\]\s+"
    r"\[(?P<thread>[^/\]]+)/(?P<level>[A-Z]+)\]"
    r"(?:\s*\[(?P<category>[^\]]+)\])?"
    r":\s?(?P<message>.*)$"
)
_LEGACY_RE = re.compile(r"^\[(?P<time>\d{2}:\d{2}:\d{2})\s+(?P<level>[A-Z]+)\]:\s?(?P<message>.*)$")


class LogLevel(str, Enum):
    """Niveau d'une ligne, utilisé pour la colorisation et le filtrage."""

    TRACE = "TRACE"
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    FATAL = "FATAL"
    RAW = "RAW"

    @classmethod
    def parse(cls, value: str) -> LogLevel:
        normalized = value.upper()
        if normalized == "WARNING":
            return cls.WARN
        if normalized == "SEVERE":
            return cls.ERROR
        try:
            return cls(normalized)
        except ValueError:
            return cls.RAW


class LineSource(str, Enum):
    """Origine de la ligne — permet à l'UI de distinguer serveur et panel."""

    STDOUT = "stdout"
    STDERR = "stderr"
    #: Ligne injectée par MSM lui-même (« Démarrage demandé par flavien »).
    MSM = "msm"
    #: Écho d'une commande envoyée depuis le panel.
    COMMAND = "command"


def strip_formatting(text: str) -> str:
    """Retire codes ANSI, codes couleur Minecraft et caractères de contrôle."""
    cleaned = _ANSI_RE.sub("", text)
    cleaned = _MC_COLOR_RE.sub("", cleaned)
    return _CONTROL_RE.sub("", cleaned)


@dataclass(frozen=True, slots=True)
class LogLine:
    """Une ligne de console, immuable et sérialisable.

    ``seq`` est un compteur monotone propre à chaque serveur : il permet au client
    WebSocket de reprendre exactement là où il s'était arrêté après une coupure,
    sans trou ni doublon.
    """

    seq: int
    ts: datetime
    text: str
    raw: str
    level: LogLevel = LogLevel.RAW
    thread: str | None = None
    category: str | None = None
    source: LineSource = LineSource.STDOUT
    #: Horodatage extrait du log lui-même (« 12:30:04 »), utile à l'affichage.
    server_time: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts.isoformat(),
            "text": self.text,
            "level": self.level.value,
            "thread": self.thread,
            "category": self.category,
            "source": self.source.value,
            "server_time": self.server_time,
        }

    def matches(self, needle: str) -> bool:
        """Recherche insensible à la casse dans le texte nettoyé."""
        return needle.casefold() in self.text.casefold()


def parse_line(
    raw: str,
    *,
    seq: int,
    source: LineSource = LineSource.STDOUT,
    received_at: datetime | None = None,
) -> LogLine:
    """Analyse une ligne brute et produit un :class:`LogLine`.

    L'analyse ne peut pas échouer : une ligne inconnue devient une ligne ``RAW``.
    """
    ts = received_at or datetime.now(UTC)
    cleaned = strip_formatting(raw).rstrip("\r\n")

    for pattern in (_MODERN_RE, _LEGACY_RE):
        match = pattern.match(cleaned)
        if match is None:
            continue
        groups = match.groupdict()
        return LogLine(
            seq=seq,
            ts=ts,
            text=groups["message"],
            raw=raw,
            level=LogLevel.parse(groups["level"]),
            thread=groups.get("thread"),
            category=groups.get("category"),
            source=source,
            server_time=groups["time"],
        )

    # Ligne non structurée : traces Java, sortie de scripts, bannières de démarrage.
    level = LogLevel.ERROR if source is LineSource.STDERR else LogLevel.RAW
    return LogLine(seq=seq, ts=ts, text=cleaned, raw=raw, level=level, source=source)


def make_system_line(
    text: str,
    *,
    seq: int,
    source: LineSource = LineSource.MSM,
    level: LogLevel = LogLevel.INFO,
) -> LogLine:
    """Fabrique une ligne émise par MSM (annonce d'action, diagnostic d'erreur)."""
    return LogLine(
        seq=seq,
        ts=datetime.now(UTC),
        text=text,
        raw=text,
        level=level,
        source=source,
        thread="MSM",
    )
