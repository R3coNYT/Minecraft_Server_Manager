"""Lecture des fichiers de référence des joueurs écrits par le serveur.

Un serveur Minecraft tient à jour quatre fichiers JSON à la racine de son
dossier. Ils sont la **seule source fiable** pour savoir qui est opérateur,
banni ou sur liste blanche : la console ne le dit pas, et rejouer l'historique
des commandes serait à la fois coûteux et faux (un fichier peut être édité à la
main serveur arrêté).

Ces fichiers sont lus, jamais écrits : accorder un statut passe toujours par une
commande console, pour que le serveur en tienne compte immédiatement et que
l'action soit auditée.

Aucune lecture ne lève : un fichier absent, vide ou corrompu renvoie une liste
vide. Un `ops.json` malformé ne doit pas empêcher d'afficher la liste des
joueurs connectés.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from msm.logging_conf import get_logger

logger = get_logger(__name__)

OPS_FILE = "ops.json"
BANNED_PLAYERS_FILE = "banned-players.json"
WHITELIST_FILE = "whitelist.json"
USERCACHE_FILE = "usercache.json"


@dataclass(frozen=True, slots=True)
class PlayerRecord:
    """Entrée d'un des fichiers de référence."""

    username: str
    uuid: str | None = None
    #: Niveau d'opérateur (1 à 4) — présent uniquement dans ``ops.json``.
    level: int | None = None
    #: Motif du bannissement, le cas échéant.
    reason: str | None = None
    #: Date d'expiration du bannissement (``forever`` pour un bannissement définitif).
    expires: str | None = None


def _load_array(directory: Path, filename: str) -> list[dict[str, Any]]:
    """Charge un fichier JSON contenant un tableau d'objets.

    La lecture utilise ``utf-8-sig`` : un administrateur qui édite ``ops.json``
    avec le Bloc-notes de Windows y ajoute une marque d'ordre d'octets, que
    l'analyseur JSON refuse. Le serveur Minecraft, lui, n'en écrit jamais — mais
    le fichier lui survit et doit rester lisible.
    """
    path = directory / filename
    if not path.is_file():
        return []
    try:
        content = json.loads(path.read_text(encoding="utf-8-sig", errors="replace") or "[]")
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("player_file_unreadable", file=str(path), error=str(exc))
        return []
    if not isinstance(content, list):
        return []
    return [entry for entry in content if isinstance(entry, dict)]


def _normalize_uuid(raw: Any) -> str | None:
    return str(raw).lower() if isinstance(raw, str) and raw else None


def read_ops(directory: Path) -> list[PlayerRecord]:
    """Opérateurs déclarés dans ``ops.json``."""
    return [
        PlayerRecord(
            username=str(entry.get("name", "")),
            uuid=_normalize_uuid(entry.get("uuid")),
            level=entry.get("level") if isinstance(entry.get("level"), int) else None,
        )
        for entry in _load_array(directory, OPS_FILE)
        if entry.get("name")
    ]


def read_banned(directory: Path) -> list[PlayerRecord]:
    """Joueurs bannis, avec motif et expiration."""
    return [
        PlayerRecord(
            username=str(entry.get("name", "")),
            uuid=_normalize_uuid(entry.get("uuid")),
            reason=entry.get("reason") if isinstance(entry.get("reason"), str) else None,
            expires=entry.get("expires") if isinstance(entry.get("expires"), str) else None,
        )
        for entry in _load_array(directory, BANNED_PLAYERS_FILE)
        if entry.get("name")
    ]


def read_whitelist(directory: Path) -> list[PlayerRecord]:
    """Joueurs autorisés dans ``whitelist.json``."""
    return [
        PlayerRecord(
            username=str(entry.get("name", "")),
            uuid=_normalize_uuid(entry.get("uuid")),
        )
        for entry in _load_array(directory, WHITELIST_FILE)
        if entry.get("name")
    ]


def read_usercache(directory: Path) -> dict[str, str]:
    """Correspondance pseudo → UUID de tous les joueurs déjà venus.

    C'est la seule façon de connaître l'UUID d'un joueur **hors ligne** : les
    logs ne l'annoncent qu'au moment de la connexion.
    """
    mapping: dict[str, str] = {}
    for entry in _load_array(directory, USERCACHE_FILE):
        name = entry.get("name")
        uuid = _normalize_uuid(entry.get("uuid"))
        if isinstance(name, str) and name and uuid:
            mapping[name.casefold()] = uuid
    return mapping


@dataclass(frozen=True, slots=True)
class PlayerFilesSnapshot:
    """Instantané des quatre fichiers, lu en une passe."""

    ops: dict[str, PlayerRecord]
    banned: dict[str, PlayerRecord]
    whitelisted: dict[str, PlayerRecord]
    uuids: dict[str, str]
    #: Le serveur applique-t-il une liste blanche (fichier présent et non vide) ?
    whitelist_active: bool = False

    def is_op(self, username: str) -> bool:
        return username.casefold() in self.ops

    def is_banned(self, username: str) -> bool:
        return username.casefold() in self.banned

    def is_whitelisted(self, username: str) -> bool:
        return username.casefold() in self.whitelisted

    def uuid_of(self, username: str) -> str | None:
        key = username.casefold()
        record = self.ops.get(key) or self.banned.get(key) or self.whitelisted.get(key)
        return (record.uuid if record else None) or self.uuids.get(key)


def read_all(directory: Path) -> PlayerFilesSnapshot:
    """Lit les quatre fichiers en une fois.

    Les lectures sont groupées volontairement : appeler quatre fois le disque à
    chaque rafraîchissement de la liste des joueurs serait inutilement coûteux.
    """
    ops = {record.username.casefold(): record for record in read_ops(directory)}
    banned = {record.username.casefold(): record for record in read_banned(directory)}
    whitelisted = {record.username.casefold(): record for record in read_whitelist(directory)}
    return PlayerFilesSnapshot(
        ops=ops,
        banned=banned,
        whitelisted=whitelisted,
        uuids=read_usercache(directory),
        whitelist_active=bool(whitelisted),
    )
