"""Règles des pseudos choisis par les utilisateurs.

Un pseudo s'affiche partout — barre du haut, membres d'un serveur, journal
d'audit — et sert à partager un serveur. D'où des règles strictes : court,
sans espace ni caractère ambigu, et quelques noms réservés, qu'un inscrit ne
doit pas pouvoir porter pour se faire passer pour l'équipe de MSM.

Les comptes créés par un admin échappent à ces règles (compatibilité avec les
comptes existants) ; l'inscription et le changement de pseudo les appliquent.
"""

from __future__ import annotations

import re

from msm.exceptions import ValidationError
from msm.i18n import tr

MIN_LENGTH = 3
MAX_LENGTH = 24
_PATTERN = re.compile(r"[A-Za-z0-9_-]+")

#: Noms qu'aucun inscrit ne peut prendre (comparés sans tenir compte de la casse).
RESERVED: frozenset[str] = frozenset(
    {
        "admin",
        "administrator",
        "administrateur",
        "api",
        "mod",
        "moderator",
        "moderateur",
        "minecraft",
        "mojang",
        "msm",
        "null",
        "owner",
        "root",
        "staff",
        "support",
        "system",
        "undefined",
    }
)


def validate_username(raw: str) -> str:
    """Pseudo nettoyé, ou :class:`ValidationError` qui dit quoi corriger."""
    username = raw.strip()
    if not MIN_LENGTH <= len(username) <= MAX_LENGTH:
        raise ValidationError(
            tr("Invalid username."),
            cause=tr(
                "A username has between {min} and {max} characters.",
                min=MIN_LENGTH,
                max=MAX_LENGTH,
            ),
            remediation=tr("Choose a username of the right length."),
        )
    if not _PATTERN.fullmatch(username):
        raise ValidationError(
            tr("Invalid username."),
            cause=tr("Only letters, digits, “_” and “-” are allowed, without spaces."),
            remediation=tr("Remove the other characters."),
        )
    if username.casefold() in RESERVED:
        raise ValidationError(
            tr("This username is reserved."),
            cause=tr("“{username}” could be mistaken for the MSM team.", username=username),
            remediation=tr("Choose another username."),
        )
    return username
